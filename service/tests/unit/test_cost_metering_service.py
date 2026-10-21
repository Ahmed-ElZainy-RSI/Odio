"""Spec 025, service side: paid calls are collected where they happen, saved per job, and
priced on /ops/cost."""

from __future__ import annotations

import asyncio
import sqlite3
import types
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from ai_engine.adapters.ai.cached import CachedTts
from ai_engine.adapters.ai.tts import GeminiTts
from ai_engine.adapters.persistence.cache_fs import FileProviderCache
from ai_engine.adapters.persistence.memory import InMemoryJobStore
from ai_engine.adapters.persistence.sql import SCHEMA_VERSION, open_connection
from ai_engine.adapters.persistence.usage_store import InMemoryUsageSink, SqlUsageSink
from ai_engine.api.app import create_app
from ai_engine.application.usage_meter import UsageMeter
from ai_engine.domain.enums import QualityTier
from ai_engine.domain.usage import PriceTable, UsageRecord
from ai_engine.observability import usage
from ai_engine.observability.metrics import COST_USD, Metrics
from ai_engine.worker import build_worker
from tests.e2e.test_job_a_end_to_end import BACKEND, REPO_ROOT, make_settings
from tests.e2e.test_job_b_end_to_end import (
    ARABIC_TEXT,
    make_audio_job,
    perfect_transcriber,
)
from tests.unit.test_verified_tier_routing import job

PRICES = PriceTable.from_toml((REPO_ROOT / "config/prices.toml").read_text(encoding="utf-8"))


def rec(**kw) -> UsageRecord:
    return UsageRecord(
        **(
            {
                "provider": "gemini",
                "model": "gemini-3.6-flash",
                "operation": "ocr",
                "at": "2026-10-21T10:00:00+00:00",
            }
            | kw
        )
    )


# ── the collector ───────────────────────────────────────────────────────────────


def test_record_outside_a_collector_is_a_no_op() -> None:
    usage.record("gemini", "m", "ocr", input_tokens=1)  # must not raise


async def test_records_from_a_worker_thread_reach_the_collector() -> None:
    with usage.collecting(page=3, stage="extraction") as used:
        await asyncio.to_thread(usage.record, "gemini", "m", "ocr", input_tokens=5)
    assert [(r.page, r.stage, r.input_tokens) for r in used] == [(3, "extraction", 5)]


# ── adapters record what they were billed for ──────────────────────────────────


class Client:
    def __init__(self, response) -> None:
        self.models = self
        self._response = response

    def generate_content(self, **kwargs):
        return self._response


def tts_response(seconds: float, prompt=40, out=900):
    pcm = b"\x00\x00" * int(24_000 * seconds)
    part = types.SimpleNamespace(inline_data=types.SimpleNamespace(data=pcm))
    return types.SimpleNamespace(
        candidates=[types.SimpleNamespace(content=types.SimpleNamespace(parts=[part]))],
        usage_metadata=types.SimpleNamespace(
            prompt_token_count=prompt, candidates_token_count=out, thoughts_token_count=None
        ),
    )


def test_gemini_tts_records_billed_tokens() -> None:
    pytest.importorskip("google.genai")
    tts = GeminiTts(client=Client(tts_response(len(ARABIC_TEXT) * 0.06)))
    with usage.collecting() as used:
        tts.synthesize(ARABIC_TEXT)
    [r] = used
    assert (r.provider, r.model, r.operation) == ("gemini", tts.model_id, "tts")
    assert (r.input_tokens, r.output_tokens, r.chars) == (40, 900, len(ARABIC_TEXT))


def test_a_cache_hit_is_recorded_as_cached(tmp_path: Path) -> None:
    pytest.importorskip("google.genai")
    tts = CachedTts(
        GeminiTts(client=Client(tts_response(len(ARABIC_TEXT) * 0.06))),
        FileProviderCache(tmp_path),
        Metrics(),
    )
    tts.synthesize(ARABIC_TEXT)
    with usage.collecting() as used:
        tts.synthesize(ARABIC_TEXT)
    assert [r.cached for r in used] == [True]


# ── sinks ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["memory", "sql"])
async def test_sink_round_trip_and_per_job_filter(tmp_path: Path, kind: str) -> None:
    sink = InMemoryUsageSink() if kind == "memory" else SqlUsageSink(tmp_path / "jobs.db")
    await sink.write(
        [rec(page=1, input_tokens=10), rec(page=2, cached=True)], job_id="a", tier="fast"
    )
    await sink.write([rec(operation="tts", chars=7)], job_id="b", tier="verified")
    assert [r.page for r in await sink.records(job_id="a")] == [1, 2]
    assert (await sink.records(job_id="a"))[1].cached is True
    assert len(await sink.records()) == 3
    if kind == "sql":
        await sink.close()


def test_a_v3_database_is_upgraded_in_place_not_refused(tmp_path: Path) -> None:
    path = str(tmp_path / "jobs.db")
    open_connection(path).execute("UPDATE schema_meta SET version = 3").connection.close()
    connection = open_connection(path)  # must not raise "schema version mismatch"
    assert connection.execute("SELECT version FROM schema_meta").fetchone()[0] == SCHEMA_VERSION
    connection.execute("SELECT count(*) FROM provider_usage")
    connection.close()


def test_a_version_without_an_additive_upgrade_is_still_refused(tmp_path: Path) -> None:
    path = str(tmp_path / "jobs.db")
    open_connection(path).execute("UPDATE schema_meta SET version = 2").connection.close()
    with pytest.raises(RuntimeError, match="schema version mismatch"):
        open_connection(path)


# ── the meter ───────────────────────────────────────────────────────────────────


async def test_meter_counts_priced_spend_by_tier() -> None:
    metrics, sink = Metrics(), InMemoryUsageSink()
    meter = UsageMeter(sink=sink, prices=PRICES, metrics=metrics)
    await meter.save(
        job(QualityTier.FAST),
        [rec(input_tokens=1_000_000), rec(provider="lighton", model="lighton/parse-v3", pages=1)],
    )
    spent = metrics._fallback.counter_value(
        COST_USD, {"provider": "gemini", "model": "gemini-3.6-flash", "tier": "fast"}
    )
    assert spent == pytest.approx(0.75)
    assert len(await sink.records()) == 2


async def test_a_sink_failure_never_fails_the_page() -> None:
    class Broken(InMemoryUsageSink):
        async def write(self, records, *, job_id, tier):
            raise sqlite3.OperationalError("database is locked")

    meter = UsageMeter(sink=Broken(), prices=PRICES, metrics=Metrics())
    await meter.save(job(QualityTier.FAST), [rec()])  # logged, not raised


# ── end to end: Job B records its TTS call ─────────────────────────────────────


async def test_job_b_saves_its_tts_usage(tmp_path: Path) -> None:
    pytest.importorskip("google.genai")
    from tests.fakes.backend import FakeBackend

    fake = FakeBackend(source_bytes=ARABIC_TEXT.encode("utf-8"))
    store, sink = InMemoryJobStore(), InMemoryUsageSink()
    audio_job = make_audio_job()
    await store.accept(audio_job)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake.app), base_url=BACKEND
    ) as client:
        worker = build_worker(
            settings=make_settings(tmp_path),
            store=store,
            metrics=Metrics(),
            http_client=client,
            tts=GeminiTts(client=Client(tts_response(len(ARABIC_TEXT) * 0.06))),
            transcriber=perfect_transcriber(ARABIC_TEXT),
            usage_sink=sink,
        )
        await worker.run_job(audio_job)
    [r] = await sink.records(job_id=str(audio_job.job_id))
    assert (r.operation, r.stage, r.page) == ("tts", "audio", audio_job.page_number)


# ── /ops/cost ───────────────────────────────────────────────────────────────────


def test_ops_cost_prices_one_job(tmp_path: Path) -> None:
    sink = InMemoryUsageSink()
    asyncio.run(
        sink.write(
            [
                rec(input_tokens=1_000_000),
                rec(provider="lighton", model="lighton/parse-v3", pages=1),
            ],
            job_id="job-1",
            tier="fast",
        )
    )
    asyncio.run(sink.write([rec(input_tokens=5)], job_id="job-2", tier="fast"))
    app = create_app(
        settings=make_settings(tmp_path), store=InMemoryJobStore(), metrics=Metrics(), usage=sink
    )

    body = TestClient(app).get("/ops/cost", params={"jobId": "job-1"}).json()

    assert body["jobId"] == "job-1" and body["calls"] == 2
    assert body["usd"] == pytest.approx(0.75)
    assert body["unpricedCalls"] == 1
    assert body["pricesAsOf"] == PRICES.as_of
    assert {(line["provider"], line["operation"]) for line in body["lines"]} == {
        ("gemini", "ocr"),
        ("lighton", "ocr"),
    }
