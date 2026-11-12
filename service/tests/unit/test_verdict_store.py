"""Spec 022 T019: the verified tier's verdicts are kept, and exported for review."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ai_engine.adapters.persistence.sql import SCHEMA_VERSION, open_connection
from ai_engine.adapters.persistence.verdict_store import InMemoryVerdictStore, SqlVerdictStore
from ai_engine.api.app import create_app
from ai_engine.application.run_page_task import PageTaskExecutor, PageTaskSpec
from ai_engine.application.verdicts import VerdictRecorder, review_export
from ai_engine.domain.enums import QualityTier
from ai_engine.domain.evidence import Evidence
from ai_engine.observability.metrics import Metrics
from tests.e2e.test_job_a_end_to_end import make_settings
from tests.unit.test_verified_tier_routing import CorrectingVerifier, FakeOcr, FakeRenderer, job

JOB = "6f1c3d1e-4b1a-4c9e-9a51-0d6c1c1f0a11"


def ev(token_id: str, status: str = "REVIEW", **kw) -> Evidence:
    return Evidence(
        token_id=token_id,
        original="الفضل",
        candidate="الفصل",
        applied=status == "ACCEPT",
        status=status,  # type: ignore[arg-type]
        detector=kw.pop("detector", "verifier"),
        decided_by="verifier",
        rule=kw.pop("rule", "decide()"),
        **kw,
    )


@pytest.fixture(params=["memory", "sql"])
async def store(request, tmp_path: Path):
    if request.param == "memory":
        yield InMemoryVerdictStore()
        return
    sql = SqlVerdictStore(tmp_path / "jobs.db")
    yield sql
    await sql.close()


async def test_a_page_is_stored_and_read_back_with_its_full_evidence(store) -> None:
    rich = ev("p002w004", "ACCEPT", scores={"final": 0.93}, reads={"lighton": "الفصل"})
    await store.replace_page(job_id=JOB, page=2, evidence=[rich, ev("p002w009")])
    await store.replace_page(job_id=JOB, page=1, evidence=[ev("p001w000")])
    await store.replace_page(job_id="other", page=1, evidence=[ev("p001w001")])
    rows = await store.for_job(JOB)
    assert [(p, e.token_id) for p, e in rows] == [(1, "p001w000"), (2, "p002w004"), (2, "p002w009")]
    assert rows[1][1] == rich
    assert [e.token_id for _p, e in await store.for_job(JOB, page=2)] == ["p002w004", "p002w009"]


async def test_a_retried_page_replaces_its_verdicts(store) -> None:
    await store.replace_page(job_id=JOB, page=1, evidence=[ev("p001w000"), ev("p001w001")])
    await store.replace_page(job_id=JOB, page=1, evidence=[ev("p001w005", "ACCEPT")])
    assert [(e.token_id, e.status) for _p, e in await store.for_job(JOB)] == [
        ("p001w005", "ACCEPT")
    ]


@pytest.mark.parametrize("old", [3, 4])
async def test_an_older_database_upgrades_in_place(tmp_path: Path, old: int) -> None:
    path = str(tmp_path / "jobs.db")
    connection = open_connection(path)
    connection.execute("DROP TABLE page_verdicts")
    if old == 3:
        connection.execute("DROP TABLE provider_usage")
    connection.execute("UPDATE schema_meta SET version = ?", (old,))
    connection.close()

    sql = SqlVerdictStore(path)
    await sql.replace_page(job_id=JOB, page=1, evidence=[ev("p001w000")])
    assert len(await sql.for_job(JOB)) == 1
    await sql.close()
    connection = open_connection(path)
    assert connection.execute("SELECT version FROM schema_meta").fetchone()[0] == SCHEMA_VERSION
    connection.close()


class BrokenStore:
    def __init__(self, error: BaseException) -> None:
        self.error = error

    async def replace_page(self, **_kw):
        raise self.error

    async def for_job(self, *_a, **_kw):
        return []


async def test_saving_never_fails_the_page() -> None:
    await VerdictRecorder(BrokenStore(OSError("disk full"))).save(  # type: ignore[arg-type]
        job_id=JOB, page=1, evidence=[ev("p001w000")]
    )


async def test_a_programming_error_while_saving_is_not_swallowed() -> None:
    with pytest.raises(TypeError):
        await VerdictRecorder(BrokenStore(TypeError("bug"))).save(  # type: ignore[arg-type]
            job_id=JOB, page=1, evidence=[ev("p001w000")]
        )


async def test_the_page_task_saves_the_verifiers_verdicts_before_uploading(tmp_path: Path) -> None:
    store = InMemoryVerdictStore()
    order: list[str] = []
    executor = PageTaskExecutor(
        store=None,  # type: ignore[arg-type]  # the stages below never touch the job store
        renderer=FakeRenderer(),
        ocr=FakeOcr(),
        uploader=None,  # type: ignore[arg-type]
        metrics=Metrics(),
        verifier=CorrectingVerifier(),
        verdicts=VerdictRecorder(store),
    )

    async def upload(spec, descriptors):
        order.append(f"upload after {len(await store.for_job(str(spec.job.job_id)))} verdicts")
        return {}

    async def report(job_id, page, file_ids):
        return "reported"

    executor._upload_in_order = upload  # type: ignore[method-assign]
    executor._report_completed = report  # type: ignore[method-assign]
    the_job = job(QualityTier.VERIFIED)
    spec = PageTaskSpec(job=the_job, page_number=1, source_path="book.pdf", scratch_dir=tmp_path)
    assert await executor._run_stages(spec) == "reported"
    rows = await store.for_job(str(the_job.job_id))
    assert [(p, e.token_id, e.status, e.applied) for p, e in rows] == [
        (1, "p001w001", "ACCEPT", True)
    ]
    assert order == ["upload after 1 verdicts"]


async def test_a_fast_page_saves_nothing(tmp_path: Path) -> None:
    store = InMemoryVerdictStore()
    executor = PageTaskExecutor(
        store=None,  # type: ignore[arg-type]
        renderer=FakeRenderer(),
        ocr=FakeOcr(),
        uploader=None,  # type: ignore[arg-type]
        metrics=Metrics(),
        verifier=CorrectingVerifier(),
        verdicts=VerdictRecorder(store),
    )

    async def nothing(*_a):
        return {}

    executor._upload_in_order = nothing  # type: ignore[method-assign]
    executor._report_completed = nothing  # type: ignore[method-assign]
    the_job = job(QualityTier.FAST)
    spec = PageTaskSpec(job=the_job, page_number=1, source_path="book.pdf", scratch_dir=tmp_path)
    await executor._run_stages(spec)
    assert await store.for_job(str(the_job.job_id)) == []


# ── export and the API ──────────────────────────────────────────────────────


def test_the_review_export_has_the_cli_shape_and_only_review_verdicts() -> None:
    rows = [
        (1, ev("p001w000", "ACCEPT")),
        (1, ev("p001w003", scores={"suspicion": 0.81}, models={"gemini": "gemini-3.8-flash"})),
        (2, ev("p002w001", detector="reviewer", rule="final-review:suggest")),
        (2, ev("p002w002", "REJECT")),
    ]
    out = review_export(JOB, rows)
    assert out["job_id"] == JOB and list(out["pages"]) == ["1", "2"]
    first = out["pages"]["1"][0]
    assert set(first) >= {"id", "ocr_word", "proposed", "suspicion", "model", "source"}
    assert (first["id"], first["suspicion"], first["model"]) == (
        "p001w003",
        0.81,
        "gemini-3.8-flash",
    )
    assert out["pages"]["2"][0]["source"] == "service-reviewer:final-review:suggest"
    assert out["pages"]["2"][0]["suspicion"] == 1.0, "unscored: a person looks first"


async def test_ops_verdicts_reports_evidence_and_review_format(tmp_path: Path) -> None:
    store = InMemoryVerdictStore()
    await store.replace_page(
        job_id=JOB, page=1, evidence=[ev("p001w000", "ACCEPT"), ev("p001w001")]
    )
    await store.replace_page(job_id=JOB, page=2, evidence=[ev("p002w000", "REJECT")])
    app = create_app(settings=make_settings(tmp_path), store=_store(), verdicts=store)
    with TestClient(app) as client:
        body = client.get("/ops/verdicts", params={"jobId": JOB}).json()
        assert body["jobId"] == JOB
        assert body["counts"] == {"ACCEPT": 1, "REVIEW": 1, "REJECT": 1}
        assert body["verdicts"][0]["page"] == 1 and body["verdicts"][0]["token_id"] == "p001w000"

        one = client.get("/ops/verdicts", params={"jobId": JOB, "page": 2}).json()
        assert [v["token_id"] for v in one["verdicts"]] == ["p002w000"]
        review = client.get("/ops/verdicts", params={"jobId": JOB, "status": "REVIEW"}).json()
        assert review["counts"] == {"REVIEW": 1}

        exported = client.get("/ops/verdicts", params={"jobId": JOB, "format": "review"}).json()
        assert [e["id"] for e in exported["pages"]["1"]] == ["p001w001"]
        assert client.get("/ops/verdicts").status_code == 422, "jobId is required"


def _store():
    from ai_engine.adapters.persistence.memory import InMemoryJobStore

    return InMemoryJobStore()
