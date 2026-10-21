"""Spec 016: Job A text is diacritised when the page needs it, and never corrupted."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

from ai_engine.adapters.ai.diacritizer_local import LocalDiacritizer, SyntheticDiacritizer
from ai_engine.adapters.persistence.memory import InMemoryJobStore
from ai_engine.application.run_page_task import PageTaskExecutor, PageTaskSpec
from ai_engine.artifacts.text_builder import build_text
from ai_engine.artifacts.tokenizer import tokenize
from ai_engine.config.settings import Settings
from ai_engine.domain.enums import ArtifactType, QualityTier
from ai_engine.domain.errors import ModelUnavailableError
from ai_engine.domain.preservation import diacritic_ratio
from ai_engine.domain.tokens import PageProvenance, Token
from ai_engine.observability.metrics import DIACRITIZATION_TOTAL, Metrics
from ai_engine.worker import build_worker
from tests.e2e.test_job_a_end_to_end import BACKEND, make_settings
from tests.unit.test_verified_tier_routing import CorrectingVerifier, FakeRenderer, job

PLAIN = [Token(id="p001w000", text="ذهب"), Token(id="p001w001", text="الفضل")]
PRINTED = [Token(id="p001w000", text="ذَهَبَ"), Token(id="p001w001", text="الْفَصْلِ")]


class FakeModel:
    def __init__(self, output: str | Exception) -> None:
        self.output, self.calls = output, 0

    def diacritize(self, text: str) -> str:
        self.calls += 1
        if isinstance(self.output, Exception):
            raise self.output
        return self.output


def local(output: str | Exception) -> tuple[LocalDiacritizer, FakeModel]:
    model = FakeModel(output)
    return LocalDiacritizer(loader=lambda: model), model


# ── the adapter ─────────────────────────────────────────────────────────────────


def test_marks_only_output_is_applied_by_position() -> None:
    adapter, _ = local("ذَهَبَ الْفَضْلِ")
    shaped, review = adapter.diacritize(PLAIN, page_image=None, page_number=1)
    assert [t.text for t in shaped] == ["ذَهَبَ", "الْفَضْلِ"]
    assert [t.id for t in shaped] == ["p001w000", "p001w001"]
    assert review == 0.0


@pytest.mark.parametrize(
    "output",
    ["ذَهَبَ", "ذَهَبَ الْفَصْلِ"],  # a dropped word; a changed letter (ض -> ص)
    ids=["word-count", "letter-changed"],
)
def test_a_page_the_model_damaged_is_left_untouched(output: str) -> None:
    adapter, _ = local(output)
    shaped, review = adapter.diacritize(PLAIN, page_image=None, page_number=1)
    assert shaped == PLAIN
    assert review == 1.0


def test_a_model_crash_leaves_the_page_untouched() -> None:
    adapter, _ = local(RuntimeError("model OOM"))
    assert adapter.diacritize(PLAIN, page_image=None, page_number=1) == (PLAIN, 1.0)


def test_a_programming_error_is_not_swallowed() -> None:
    adapter, _ = local(TypeError("bug"))
    with pytest.raises(TypeError):
        adapter.diacritize(PLAIN, page_image=None, page_number=1)


def test_the_model_loads_once_per_instance() -> None:
    loads = []
    adapter = LocalDiacritizer(loader=lambda: loads.append(1) or FakeModel("ذَهَبَ الْفَضْلِ"))
    adapter.diacritize(PLAIN, page_image=None, page_number=1)
    adapter.diacritize(PLAIN, page_image=None, page_number=2)
    assert loads == [1]


def test_missing_package_is_model_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "diacritize", None)  # makes the import fail
    with pytest.raises(ModelUnavailableError, match="arabic-diacritizer"):
        LocalDiacritizer().load()


def test_synthetic_diacritizer_only_adds_marks() -> None:
    shaped, _ = SyntheticDiacritizer().diacritize(PLAIN, page_image=None, page_number=1)
    assert diacritic_ratio(" ".join(t.text for t in shaped)) >= 0.9
    assert [t.id for t in shaped] == [t.id for t in PLAIN]


# ── the page task ───────────────────────────────────────────────────────────────


class Ocr:
    name, provider, model_id = "lighton", "local", "fake"

    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens

    def extract_page(self, *, page_image, pdf_path, page_number):
        return list(self.tokens), PageProvenance(ocr_engine=self.name)


def produce(tmp_path: Path, tokens, *, diacritizer, verifier=None, tier=QualityTier.FAST):
    metrics = Metrics()
    executor = PageTaskExecutor(
        store=InMemoryJobStore(),
        renderer=FakeRenderer(),
        ocr=Ocr(tokens),
        uploader=None,  # type: ignore[arg-type]
        metrics=metrics,
        verifier=verifier,
        diacritizer=diacritizer,
    )
    spec = PageTaskSpec(job=job(tier), page_number=1, source_path="b.pdf", scratch_dir=tmp_path)
    payload = executor._produce(spec)[ArtifactType.TEXT].payload.decode("utf-8")
    text = payload.removesuffix("\n")  # the Text artifact always ends in one newline
    return text, metrics


def outcome(metrics: Metrics, name: str) -> float:
    return metrics._fallback.counter_value(DIACRITIZATION_TOTAL, {"outcome": name})


def test_an_undiacritised_page_is_diacritised(tmp_path: Path) -> None:
    text, metrics = produce(tmp_path, PLAIN, diacritizer=local("ذَهَبَ الْفَضْلِ")[0])
    assert text == "ذَهَبَ الْفَضْلِ"
    assert outcome(metrics, "applied") == 1


def test_a_page_printed_with_tashkeel_is_kept(tmp_path: Path) -> None:
    adapter, model = local("unused")
    text, metrics = produce(tmp_path, PRINTED, diacritizer=adapter)
    assert text == "ذَهَبَ الْفَصْلِ"
    assert model.calls == 0, "printed marks must not be overwritten"
    assert outcome(metrics, "kept") == 1


def test_a_diacritizer_that_breaks_the_invariant_is_ignored(tmp_path: Path) -> None:
    class Lying:
        def diacritize(self, tokens, *, page_image, page_number):
            return [tokens[0], Token(id="p001w001", text="الفصل")], 0.0  # letter changed

    text, metrics = produce(tmp_path, PLAIN, diacritizer=Lying())
    assert text == "ذهب الفضل"
    assert outcome(metrics, "rejected") == 1


def test_no_diacritizer_means_text_as_extracted(tmp_path: Path) -> None:
    text, _ = produce(tmp_path, PLAIN, diacritizer=None)
    assert text == "ذهب الفضل"


def test_verification_runs_before_diacritisation(tmp_path: Path) -> None:
    """Correct the letters first, then add marks (constitution I): the diacritizer must see
    الفصل, not الفضل."""
    seen = []

    class Recording(SyntheticDiacritizer):
        def diacritize(self, tokens, *, page_image, page_number):
            seen.extend(t.text for t in tokens)
            return super().diacritize(tokens, page_image=page_image, page_number=page_number)

    produce(
        tmp_path,
        PLAIN,
        diacritizer=Recording(),
        verifier=CorrectingVerifier(),
        tier=QualityTier.VERIFIED,
    )
    assert seen == ["ذهب", "الفصل"]


def test_job_b_still_reproduces_job_a_ids_from_diacritised_text() -> None:
    shaped, _ = SyntheticDiacritizer().diacritize(PLAIN, page_image=None, page_number=1)
    again = tokenize(build_text(shaped).decode("utf-8"), 1)
    assert [t.id for t in again] == [t.id for t in PLAIN]
    assert [t.text for t in again] == [t.text for t in shaped]


# ── configuration and wiring ────────────────────────────────────────────────────


def test_unknown_diacritizer_setting_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="diacritizer"):
        Settings(**(make_settings(tmp_path).model_dump() | {"diacritizer": "gpt"}))


async def test_worker_wires_nothing_by_default(tmp_path: Path) -> None:
    async with httpx.AsyncClient(base_url=BACKEND) as client:
        worker = build_worker(
            settings=make_settings(tmp_path),
            store=InMemoryJobStore(),
            metrics=Metrics(),
            http_client=client,
        )
    assert worker.extraction._pages._diacritizer is None


async def test_worker_fails_fast_when_local_model_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "diacritize", None)
    settings = make_settings(tmp_path).model_copy(update={"diacritizer": "local"})
    async with httpx.AsyncClient(base_url=BACKEND) as client:
        with pytest.raises(ModelUnavailableError):
            build_worker(
                settings=settings, store=InMemoryJobStore(), metrics=Metrics(), http_client=client
            )
