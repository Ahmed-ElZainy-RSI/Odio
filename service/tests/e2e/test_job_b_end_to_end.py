"""Job B end to end: PAGE_AUDIO_AND_TEXT_TIMING_GENERATION.

Zero paid calls. TTS is the synthetic engine, which produces genuine 24 kHz mono 16-bit WAV
audio rather than a placeholder, and transcription is injected -- but the **real**
`speak.bind` binding logic runs in every test, because that is where the product value and
the subtle bugs live.

The centrepiece is `test_job_a_text_artifact_feeds_job_b_with_identical_ids`: it runs Job A,
takes the text artifact that actually reached storage, feeds it to Job B, and asserts the
timing JSON's token ids match Job A's exactly. That round trip is the whole Job A -> Job B
contract, and nothing else verifies it.
"""

from __future__ import annotations

import io
import json
import wave
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from ai_engine.adapters.ai.tts import SyntheticTts
from ai_engine.adapters.persistence.memory import InMemoryJobStore
from ai_engine.artifacts.timing_builder import SCHEMA_VERSION
from ai_engine.domain.enums import ArtifactType, JobState, JobType, PageState
from ai_engine.domain.errors import ErrorCode
from ai_engine.domain.job import Job, canonical_payload_hash
from ai_engine.observability.metrics import ALIGNMENT_ANCHOR_RATIO, PAGES_TOTAL, Metrics
from ai_engine.worker import build_worker
from tests.e2e.test_job_a_end_to_end import BACKEND, REPO_ROOT, make_settings
from tests.fakes.backend import FakeBackend

PAGE = 7
ARABIC_TEXT = "طَارُ الْعُصْفُورُ صَوْصَوْ وَرَاءَ فُلْفُل وَهُوَ يُغَنِّي بِفَرَحٍ"


# ── Transcribers: injected so no model loads, while `bind` stays real ────────────


def perfect_transcriber(text: str):
    """Returns a stamp per word that matches it exactly, so every token anchors."""

    def transcribe(audio_path: str) -> list[dict]:
        words = text.split()
        step = 400
        return [
            {"text": word, "b": index * step, "e": (index + 1) * step}
            for index, word in enumerate(words)
        ]

    return transcribe


def silent_transcriber(audio_path: str) -> list[dict]:
    """Returns nothing, as a failed or silent transcription would."""
    return []


def partial_transcriber(text: str, *, keep: int):
    """Stamps only the first `keep` words -- the realistic case."""

    def transcribe(audio_path: str) -> list[dict]:
        words = text.split()[:keep]
        return [
            {"text": word, "b": index * 400, "e": (index + 1) * 400}
            for index, word in enumerate(words)
        ]

    return transcribe


def garbage_transcriber(audio_path: str) -> list[dict]:
    """Inverted and out-of-order intervals, as a vendor sometimes emits."""
    return [
        {"text": "خطأ", "b": 900, "e": 100},
        {"text": "ثانٍ", "b": 50, "e": 25},
    ]


def make_audio_job(*, file_id: str = "page-text", page: int = PAGE) -> Job:
    return Job(
        job_id=uuid4(),
        job_type=JobType.PAGE_AUDIO_AND_TEXT_TIMING_GENERATION,
        file_id=file_id,
        folder_id="books/reading-first/grade-1",
        path_prefix="s3-prefix/2026/09",
        source_download_url=f"{BACKEND}/_source/{file_id}",
        page_number=page,
        payload_hash=canonical_payload_hash(
            job_type=JobType.PAGE_AUDIO_AND_TEXT_TIMING_GENERATION,
            file_id=file_id,
            folder_id="books/reading-first/grade-1",
            path_prefix="s3-prefix/2026/09",
            page_number=page,
        ),
    )


async def run_audio(
    tmp_path: Path,
    fake: FakeBackend,
    *,
    transcriber=None,
    tts=None,
    job: Job | None = None,
    store: InMemoryJobStore | None = None,
    budget=None,
):
    settings = make_settings(tmp_path)
    store = store or InMemoryJobStore()
    metrics = Metrics()
    job = job or make_audio_job()
    if await store.get(job.job_id) is None:
        await store.accept(job)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake.app), base_url=BACKEND
    ) as client:
        worker = build_worker(
            settings=settings,
            store=store,
            metrics=metrics,
            http_client=client,
            tts=tts or SyntheticTts(),
            transcriber=transcriber or perfect_transcriber(ARABIC_TEXT),
            budget=budget,
        )
        outcome = await worker.run_job(job)
        await worker.drain_until_quiet()
    return outcome, store, metrics, job, fake


def timing_document(fake: FakeBackend) -> dict:
    payload = next(
        u["received"] for u in fake.uploads.values() if u["content_type"] == "application/json"
    )
    return json.loads(payload)


def audio_payload(fake: FakeBackend) -> bytes:
    return next(u["received"] for u in fake.uploads.values() if u["content_type"] == "audio/wav")


@pytest.fixture
def text_fake() -> FakeBackend:
    return FakeBackend(sources={"page-text": ARABIC_TEXT.encode("utf-8")})


# ── The happy path ──────────────────────────────────────────────────────────────


async def test_job_b_completes_and_reports_the_page(tmp_path: Path, text_fake: FakeBackend) -> None:
    outcome, store, _metrics, job, fake = await run_audio(tmp_path, text_fake)

    assert outcome.state is JobState.COMPLETED
    assert outcome.page_number == PAGE
    assert fake.terminal_reports() == {PAGE: "completed"}

    ledger = await store.pages(job.job_id)
    assert [p.state for p in ledger] == [PageState.REPORTED_COMPLETED]


async def test_job_b_sends_no_metadata_callback(tmp_path: Path, text_fake: FakeBackend) -> None:
    """Job B covers one supplied page, so there is no page count to report."""
    _outcome, _store, _metrics, _job, fake = await run_audio(tmp_path, text_fake)
    assert "metadata" not in fake.ops()


async def test_upload_order_is_audio_then_json(tmp_path: Path, text_fake: FakeBackend) -> None:
    """REQ-UPL-05 for Job B."""
    _outcome, _store, _metrics, _job, fake = await run_audio(tmp_path, text_fake)
    inits = [c.body["contentType"] for c in fake.calls if c.op == "initialize_upload"]
    assert inits == ["audio/wav", "application/json"]


async def test_artifact_types_are_audio_then_json(tmp_path: Path, text_fake: FakeBackend) -> None:
    """REQ-CB-03: exact, capitalised wire values."""
    _outcome, _store, _metrics, _job, fake = await run_audio(tmp_path, text_fake)
    assert [t for t, _ in fake.artifacts_for(PAGE)] == ["Audio", "JSON"]


async def test_the_uploaded_audio_is_a_real_24khz_mono_wav(
    tmp_path: Path, text_fake: FakeBackend
) -> None:
    """Matches what the legacy pipeline writes, so artifacts stay comparable."""
    _outcome, _store, _metrics, _job, fake = await run_audio(tmp_path, text_fake)
    payload = audio_payload(fake)
    assert payload.startswith(b"RIFF")
    with wave.open(io.BytesIO(payload), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 24_000
        assert handle.getnframes() > 0


async def test_timing_json_matches_the_published_schema(
    tmp_path: Path, text_fake: FakeBackend
) -> None:
    _outcome, _store, _metrics, _job, fake = await run_audio(tmp_path, text_fake)
    doc = timing_document(fake)

    assert doc["schema_version"] == SCHEMA_VERSION
    assert doc["page"] == PAGE
    assert doc["duration_ms"] > 0
    assert doc["audio"]["sample_rate_hz"] == 24_000
    assert doc["provenance"]["align_method"] == "forced-whisper-small"
    assert doc["provenance"]["tokens"] == len(ARABIC_TEXT.split())
    assert [t["txt"] for t in doc["tokens"]] == ARABIC_TEXT.split()


async def test_timings_are_monotonic_and_within_the_duration(
    tmp_path: Path, text_fake: FakeBackend
) -> None:
    """The two invariants a reader is allowed to rely on."""
    _outcome, _store, _metrics, _job, fake = await run_audio(tmp_path, text_fake)
    doc = timing_document(fake)

    previous_end = 0
    for token in doc["tokens"]:
        if "t" not in token:
            continue
        start, end = token["t"]
        assert start >= previous_end, f"{token['id']} overlaps the previous token"
        assert start <= end
        assert end <= doc["duration_ms"]
        previous_end = end


async def test_duration_is_measured_from_the_audio_not_estimated(
    tmp_path: Path, text_fake: FakeBackend
) -> None:
    """A duration estimated from character count silently corrupts every timing."""
    _outcome, _store, _metrics, _job, fake = await run_audio(tmp_path, text_fake)
    doc = timing_document(fake)

    payload = audio_payload(fake)
    with wave.open(io.BytesIO(payload), "rb") as handle:
        true_ms = round(1000 * handle.getnframes() / handle.getframerate())
    assert doc["duration_ms"] == true_ms


async def test_anchor_ratio_is_reported_in_the_artifact_and_the_metric(
    tmp_path: Path, text_fake: FakeBackend
) -> None:
    """Quality ships inside the artifact so a consumer can see it per page."""
    _outcome, _store, metrics, _job, fake = await run_audio(tmp_path, text_fake)
    doc = timing_document(fake)

    assert doc["provenance"]["anchor_ratio"] == 1.0
    observations = metrics.observations(
        ALIGNMENT_ANCHOR_RATIO,
        {"align_method": "forced-whisper-small", "tts_model": "synthetic-v1"},
    )
    assert observations == [1.0]


# ── The Job A -> Job B round trip ────────────────────────────────────────────────


@pytest.mark.slow
async def test_job_a_text_artifact_feeds_job_b_with_identical_ids(
    tmp_path: Path,
) -> None:
    """The whole Job A -> Job B contract, verified against a real book.

    Job A's `Text` artifact is the only thing Job B receives, so the ids in the timing JSON
    must be reproducible from those bytes alone -- no shared state, no correlation table.
    Nothing else in the suite checks that the two jobs actually agree.
    """
    sample = REPO_ROOT / "assets" / "PDF Samples" / "النجوم.pdf"
    if not sample.exists():
        pytest.skip("sample corpus PDF is not available")

    from tests.e2e.test_job_a_end_to_end import make_job, run_job

    # 1. Run Job A and capture the text artifact that actually reached storage.
    fake_a = FakeBackend(source_bytes=sample.read_bytes())
    _outcome, _store, _metrics, _job = await run_job(tmp_path / "a", fake_a, make_job())
    texts = [u["received"] for u in fake_a.uploads.values() if u["content_type"] == "text/plain"]
    assert len(texts) == 16
    page_three_text = texts[2]  # uploads are ordered per page

    # 2. Feed exactly those bytes to Job B for page 3.
    fake_b = FakeBackend(sources={"page-3-text": page_three_text})
    decoded = page_three_text.decode("utf-8")
    job = make_audio_job(file_id="page-3-text", page=3)
    _outcome_b, _store_b, _metrics_b, _job_b, fake_b = await run_audio(
        tmp_path / "b",
        fake_b,
        transcriber=perfect_transcriber(decoded),
        job=job,
    )

    doc = timing_document(fake_b)
    expected_words = decoded.split()
    assert doc["page"] == 3
    assert [t["id"] for t in doc["tokens"]] == [
        f"p003w{index:03d}" for index in range(len(expected_words))
    ]
    assert [t["txt"] for t in doc["tokens"]] == expected_words


# ── Degradation: alignment quality ──────────────────────────────────────────────


async def test_a_silent_transcription_still_produces_a_valid_artifact(
    tmp_path: Path, text_fake: FakeBackend
) -> None:
    """Zero anchors is a DEGRADED result, not a failure. The page still completes."""
    outcome, _store, _metrics, _job, fake = await run_audio(
        tmp_path, text_fake, transcriber=silent_transcriber
    )
    assert outcome.state is JobState.COMPLETED
    assert fake.terminal_reports() == {PAGE: "completed"}

    doc = timing_document(fake)
    assert doc["provenance"]["anchored"] == 0
    # Every token still gets a window, interpolated across the page.
    assert all(t.get("t_src") in {"interpolated", None} for t in doc["tokens"])


async def test_a_partial_transcription_mixes_anchored_and_interpolated(
    tmp_path: Path, text_fake: FakeBackend
) -> None:
    """The realistic case: measured across 95 real pages, 78% anchor and 21.8% do not."""
    words = ARABIC_TEXT.split()
    _outcome, _store, _metrics, _job, fake = await run_audio(
        tmp_path, text_fake, transcriber=partial_transcriber(ARABIC_TEXT, keep=3)
    )
    doc = timing_document(fake)
    provenance = doc["provenance"]

    assert provenance["anchored"] == 3
    assert provenance["interpolated"] == len(words) - 3
    assert 0.0 < provenance["anchor_ratio"] < 1.0
    sources = {t["t_src"] for t in doc["tokens"] if "t_src" in t}
    assert sources == {"anchor", "interpolated"}


async def test_garbage_vendor_timestamps_are_dropped_not_trusted(
    tmp_path: Path, text_fake: FakeBackend
) -> None:
    """`bind` drops inverted intervals. Trusting them would produce a nonsense artifact."""
    outcome, _store, _metrics, _job, fake = await run_audio(
        tmp_path, text_fake, transcriber=garbage_transcriber
    )
    assert outcome.state is JobState.COMPLETED
    doc = timing_document(fake)

    previous_end = 0
    for token in doc["tokens"]:
        if "t" not in token:
            continue
        start, end = token["t"]
        assert start >= previous_end
        assert start <= end
        previous_end = end


# ── Failure paths ───────────────────────────────────────────────────────────────


async def test_a_non_utf8_source_fails_the_page_with_a_safe_reason(
    tmp_path: Path,
) -> None:
    fake = FakeBackend(sources={"page-text": b"\xff\xfe\x00 not utf-8 \xff"})
    outcome, _store, _metrics, _job, fake = await run_audio(tmp_path, fake)

    assert outcome.error_code is ErrorCode.SOURCE_INVALID
    assert fake.terminal_reports() == {PAGE: "failed"}
    reason = next(b["reason"] for _j, _p, k, b in fake.page_reports if k == "failed")
    assert reason.startswith("SOURCE_INVALID: ")


async def test_an_empty_source_fails_the_page(tmp_path: Path) -> None:
    fake = FakeBackend(sources={"page-text": b"   \n  \n "})
    outcome, _store, _metrics, _job, fake = await run_audio(tmp_path, fake)
    assert outcome.error_code is ErrorCode.SOURCE_INVALID
    assert fake.terminal_reports() == {PAGE: "failed"}


async def test_a_tts_failure_fails_the_page_and_still_reports_it(
    tmp_path: Path, text_fake: FakeBackend
) -> None:
    """INVARIANT-1 for Job B: the page reports even when the AI fails."""

    class BrokenTts:
        name = "broken"
        model_id = "broken-v1"

        def synthesize(self, text: str, *, voice: str | None = None):
            from ai_engine.domain.errors import AiInferenceError

            raise AiInferenceError("TTS provider returned HTTP 500")

    outcome, _store, _metrics, _job, fake = await run_audio(tmp_path, text_fake, tts=BrokenTts())
    assert outcome.error_code is ErrorCode.AI_INFERENCE_ERROR
    assert fake.terminal_reports() == {PAGE: "failed"}
    reason = next(b["reason"] for _j, _p, k, b in fake.page_reports if k == "failed")
    assert reason.startswith("AI_INFERENCE_ERROR: ")


async def test_an_alignment_failure_fails_the_page(tmp_path: Path, text_fake: FakeBackend) -> None:
    def exploding_transcriber(audio_path: str) -> list[dict]:
        raise RuntimeError("ctranslate2 blew up")

    outcome, _store, _metrics, _job, fake = await run_audio(
        tmp_path, text_fake, transcriber=exploding_transcriber
    )
    assert fake.terminal_reports() == {PAGE: "failed"}
    assert outcome.error_code is not None


async def test_degenerate_tts_output_is_rejected_by_the_sanity_gate(
    tmp_path: Path, text_fake: FakeBackend
) -> None:
    """A real observed failure: ~224 s of audio for ~50 words.

    Shipping that would give a four-minute audio file and a timing artifact that is
    nonsense for every token.
    """
    from ai_engine.adapters.ai.tts import GeminiTts, wrap_pcm_as_wav

    class DegenerateClient:
        def __init__(self) -> None:
            self.models = self

        def generate_content(self, **kwargs):
            raise AssertionError("not reached: _request is overridden")

    class DegenerateTts(GeminiTts):
        def _request(self, client, text, voice):
            # 60 s of audio for a handful of words.
            return b"\x00\x00" * (24_000 * 60)

    tts = DegenerateTts(client=DegenerateClient())
    outcome, _store, _metrics, _job, fake = await run_audio(tmp_path, text_fake, tts=tts)
    assert outcome.error_code is ErrorCode.AI_INFERENCE_ERROR
    reason = next(b["reason"] for _j, _p, k, b in fake.page_reports if k == "failed")
    assert "duration sanity check" in reason
    assert wrap_pcm_as_wav(b"\x00\x00").startswith(b"RIFF")


async def test_a_retried_job_b_page_does_not_reupload_confirmed_artifacts(
    tmp_path: Path, text_fake: FakeBackend
) -> None:
    """Audio and timing JSON confirmed, the report never sent, then the process died."""
    from tests.support import simulate_upload_then_crash

    store = InMemoryJobStore(callback_backoff_base=1.0)
    job = make_audio_job()
    await store.accept(job)
    await simulate_upload_then_crash(
        store, job, PAGE, artifacts=(ArtifactType.AUDIO, ArtifactType.JSON), fake=text_fake
    )

    await run_audio(tmp_path, text_fake, job=await store.get(job.job_id), store=store)

    assert [c for c in text_fake.calls if c.op == "initialize_upload"] == [], (
        "an already-confirmed artifact was uploaded again"
    )
    assert text_fake.terminal_reports() == {PAGE: "completed"}


async def test_metrics_count_the_page(tmp_path: Path, text_fake: FakeBackend) -> None:
    _outcome, _store, metrics, _job, _fake = await run_audio(tmp_path, text_fake)
    label = {"job_type": JobType.PAGE_AUDIO_AND_TEXT_TIMING_GENERATION.value}
    assert metrics.counter_value(PAGES_TOTAL, {**label, "outcome": "completed"}) == 1


async def test_ledger_records_both_artifacts(tmp_path: Path, text_fake: FakeBackend) -> None:
    _outcome, store, _metrics, job, _fake = await run_audio(tmp_path, text_fake)
    artifacts = await store.artifacts_for(job.job_id, PAGE)
    assert set(artifacts) == {ArtifactType.AUDIO, ArtifactType.JSON}


async def test_a_programming_error_is_reported_as_internal_not_as_the_provider(
    tmp_path: Path, text_fake: FakeBackend
) -> None:
    """Spec 007: a TypeError in our own code still fails the page exactly once (INVARIANT-1),
    but as INTERNAL_ERROR -- never as "TTS provider failed", which would read as an outage."""
    from ai_engine.adapters.ai.tts import GeminiTts

    class BuggyTts(GeminiTts):
        def _request(self, client, text, voice):
            raise TypeError("unsupported operand type(s) for +: 'int' and 'str'")

    outcome, _store, _metrics, _job, fake = await run_audio(
        tmp_path, text_fake, tts=BuggyTts(client=object())
    )

    assert fake.terminal_reports() == {PAGE: "failed"}
    assert outcome.error_code is ErrorCode.INTERNAL_ERROR
    reason = next(b["reason"] for _j, _p, k, b in fake.page_reports if k == "failed")
    assert "internal error" in reason
    assert "provider" not in reason
