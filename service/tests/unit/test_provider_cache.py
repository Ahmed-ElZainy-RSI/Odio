"""Spec 021: a retried page never pays twice for a call that already succeeded."""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx
import pytest

from ai_engine.adapters.ai.cached import CachedOcrEngine, CachedTts
from ai_engine.adapters.ai.tts import SyntheticTts
from ai_engine.adapters.persistence.cache_fs import FileProviderCache
from ai_engine.adapters.persistence.memory import InMemoryJobStore
from ai_engine.domain.cache_key import CACHE_FORMAT, digest, fingerprint
from ai_engine.domain.errors import AiInferenceError
from ai_engine.domain.tokens import AudioFormat, PageProvenance, Token
from ai_engine.observability.metrics import PROVIDER_CACHE, Metrics
from ai_engine.worker import build_worker
from tests.e2e.test_job_a_end_to_end import BACKEND, make_settings

# ── fingerprint ─────────────────────────────────────────────────────────────────

BASE = {"provider": "gemini", "model": "m1", "operation": "ocr", "params": {"a": "x", "n": 1}}


def test_fingerprint_is_stable_and_order_independent() -> None:
    reordered = dict(BASE, params={"n": 1, "a": "x"})
    assert fingerprint(**BASE) == fingerprint(**reordered)
    assert len(fingerprint(**BASE)) == 64


@pytest.mark.parametrize(
    "change",
    [
        {"provider": "lighton"},
        {"model": "m2"},
        {"operation": "tts"},
        {"params": {"a": "y", "n": 1}},
        {"params": {"a": "x", "n": 2}},
        {"params": {"a": "x"}},
    ],
)
def test_any_change_is_a_different_key(change: dict) -> None:
    assert fingerprint(**(BASE | change)) != fingerprint(**BASE)


def test_format_version_is_part_of_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    import ai_engine.domain.cache_key as ck

    before = fingerprint(**BASE)
    monkeypatch.setattr(ck, "CACHE_FORMAT", CACHE_FORMAT + 1)
    assert ck.fingerprint(**BASE) != before


def test_missing_identity_is_refused() -> None:
    with pytest.raises(ValueError):
        fingerprint(**(BASE | {"model": ""}))


# ── the filesystem store ────────────────────────────────────────────────────────

KEY = fingerprint(**BASE)


def test_put_then_get(tmp_path: Path) -> None:
    cache = FileProviderCache(tmp_path)
    assert cache.get(KEY) is None
    cache.put(KEY, b"value")
    assert cache.get(KEY) == b"value"


def test_a_failed_write_leaves_the_old_entry_and_no_debris(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = FileProviderCache(tmp_path)
    cache.put(KEY, b"old")

    def crash(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", crash)
    with pytest.raises(OSError):
        cache.put(KEY, b"new")
    monkeypatch.undo()

    assert cache.get(KEY) == b"old"
    assert [p.name for p in (tmp_path / KEY[:2]).iterdir()] == [KEY]


def test_old_entries_expire(tmp_path: Path) -> None:
    cache = FileProviderCache(tmp_path, max_age_seconds=60)
    cache.put(KEY, b"value")
    old = time.time() - 3600
    os.utime(tmp_path / KEY[:2] / KEY, (old, old))
    assert cache.get(KEY) is None


def test_an_unreadable_entry_is_a_miss(tmp_path: Path) -> None:
    (tmp_path / KEY[:2] / KEY).mkdir(parents=True)  # a directory where a file should be
    assert FileProviderCache(tmp_path).get(KEY) is None


def test_keys_cannot_escape_the_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        FileProviderCache(tmp_path).put("../../etc/passwd", b"x")


# ── the OCR wrapper ─────────────────────────────────────────────────────────────

TOKENS = [Token(id="p003w000", text="قوس", box=(1.0, 2.0, 3.0, 4.0), fixes=("kashida",))]
PROVENANCE = PageProvenance(ocr_engine="gemini:m1", box_level="line", extra={"lines": "1"})


class PaidOcr:
    name, provider, model_id = "gemini", "gemini", "m1"

    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def extract_page(self, *, page_image, pdf_path, page_number):
        self.calls += 1
        if self.fail:
            raise AiInferenceError("provider exploded")
        return list(TOKENS), PROVENANCE


def ocr(tmp_path: Path, inner=None):
    metrics = Metrics()
    inner = inner or PaidOcr()
    return CachedOcrEngine(inner, FileProviderCache(tmp_path), metrics), inner, metrics


def lookups(metrics: Metrics, operation: str, outcome: str) -> float:
    return metrics._fallback.counter_value(
        PROVIDER_CACHE, {"operation": operation, "outcome": outcome}
    )


def test_second_call_is_served_from_the_cache(tmp_path: Path) -> None:
    engine, inner, metrics = ocr(tmp_path)
    first = engine.extract_page(page_image=b"img", pdf_path="b.pdf", page_number=3)
    second = engine.extract_page(page_image=b"img", pdf_path="b.pdf", page_number=3)
    assert inner.calls == 1
    assert second == first
    assert (lookups(metrics, "ocr", "miss"), lookups(metrics, "ocr", "hit")) == (1, 1)


@pytest.mark.parametrize("change", [{"page_image": b"other"}, {"page_number": 4}])
def test_different_input_is_a_miss(tmp_path: Path, change: dict) -> None:
    engine, inner, _ = ocr(tmp_path)
    engine.extract_page(page_image=b"img", pdf_path="b.pdf", page_number=3)
    engine.extract_page(**({"page_image": b"img", "pdf_path": "b.pdf", "page_number": 3} | change))
    assert inner.calls == 2


def test_a_failure_is_never_cached(tmp_path: Path) -> None:
    engine, inner, _ = ocr(tmp_path, PaidOcr(fail=True))
    for _ in range(2):
        with pytest.raises(AiInferenceError):
            engine.extract_page(page_image=b"img", pdf_path="b.pdf", page_number=3)
    assert inner.calls == 2


def test_a_corrupt_entry_falls_back_to_the_provider(tmp_path: Path) -> None:
    engine, inner, _ = ocr(tmp_path)
    FileProviderCache(tmp_path).put(engine.key(b"img", 3), b"{not json")
    tokens, _ = engine.extract_page(page_image=b"img", pdf_path="b.pdf", page_number=3)
    assert inner.calls == 1
    assert tokens == TOKENS


def test_no_page_image_means_no_caching(tmp_path: Path) -> None:
    engine, inner, _ = ocr(tmp_path)
    engine.extract_page(page_image=None, pdf_path="b.pdf", page_number=3)
    engine.extract_page(page_image=None, pdf_path="b.pdf", page_number=3)
    assert inner.calls == 2


def test_the_wrapper_keeps_the_engine_identity(tmp_path: Path) -> None:
    engine, _, _ = ocr(tmp_path)
    assert (engine.name, engine.provider, engine.model_id) == ("gemini", "gemini", "m1")


# ── the TTS wrapper ─────────────────────────────────────────────────────────────


class PaidTts:
    name, provider, model_id = "gemini", "gemini", "tts-1"
    _voice = "Kore"

    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def synthesize(self, text, *, voice=None):
        self.calls += 1
        if self.fail:
            raise AiInferenceError("duration sanity check failed")
        return b"RIFF....audio", AudioFormat(sample_rate_hz=24_000), 1234


def test_tts_hit_returns_identical_audio(tmp_path: Path) -> None:
    inner = PaidTts()
    tts = CachedTts(inner, FileProviderCache(tmp_path), Metrics())
    first = tts.synthesize("نص", voice="Kore")
    assert tts.synthesize("نص", voice="Kore") == first
    assert inner.calls == 1


@pytest.mark.parametrize("call", [("نص آخر", "Kore"), ("نص", "Puck")])
def test_tts_different_text_or_voice_is_a_miss(tmp_path: Path, call: tuple) -> None:
    inner = PaidTts()
    tts = CachedTts(inner, FileProviderCache(tmp_path), Metrics())
    tts.synthesize("نص", voice="Kore")
    tts.synthesize(call[0], voice=call[1])
    assert inner.calls == 2


def test_tts_default_voice_is_part_of_the_key(tmp_path: Path) -> None:
    tts = CachedTts(PaidTts(), FileProviderCache(tmp_path), Metrics())
    assert tts.key("نص", None) == tts.key("نص", "Kore")
    assert tts.key("نص", None) != tts.key("نص", "Puck")


def test_audio_that_failed_validation_is_not_cached(tmp_path: Path) -> None:
    inner = PaidTts(fail=True)
    tts = CachedTts(inner, FileProviderCache(tmp_path), Metrics())
    for _ in range(2):
        with pytest.raises(AiInferenceError):
            tts.synthesize("نص")
    assert inner.calls == 2


def test_text_key_uses_a_digest_not_the_text() -> None:
    """Page text never lands in a filename or a log line via the key."""
    assert digest("نص") != "نص" and len(digest("نص")) == 64


# ── wiring ──────────────────────────────────────────────────────────────────────


async def build(tmp_path: Path, *, cache_dir: Path | None, tts: object):
    settings = make_settings(tmp_path).model_copy(update={"provider_cache_dir": cache_dir})
    async with httpx.AsyncClient(base_url=BACKEND) as client:
        return build_worker(
            settings=settings,
            store=InMemoryJobStore(),
            metrics=Metrics(),
            http_client=client,
            tts=tts,
        )


async def test_paid_tts_is_wrapped_when_a_cache_dir_is_set(tmp_path: Path) -> None:
    worker = await build(tmp_path, cache_dir=tmp_path / "cache", tts=PaidTts())
    assert isinstance(worker.audio._tts, CachedTts)


async def test_free_tts_is_not_wrapped(tmp_path: Path) -> None:
    worker = await build(tmp_path, cache_dir=tmp_path / "cache", tts=SyntheticTts())
    assert isinstance(worker.audio._tts, SyntheticTts)


async def test_no_cache_dir_means_no_cache(tmp_path: Path) -> None:
    worker = await build(tmp_path, cache_dir=None, tts=PaidTts())
    assert isinstance(worker.audio._tts, PaidTts)
