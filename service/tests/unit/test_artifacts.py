"""The two artifacts that do not exist on disk today.

REQ-ART-02 (`Text`) and REQ-ART-04 (`JSON` timing) are the only two mandated artifacts the
existing pipeline does not write as files -- the data is computed and discarded. These
tests pin down the contract for both.
"""

from __future__ import annotations

import json

import pytest

from ai_engine.artifacts.text_builder import build_text, build_text_artifact
from ai_engine.artifacts.timing_builder import (
    SCHEMA_VERSION,
    TimingArtifactError,
    build_timing_artifact,
    build_timing_document,
)
from ai_engine.artifacts.tokenizer import detokenize, tokenize
from ai_engine.domain.enums import ArtifactType, TimingSource
from ai_engine.domain.tokens import AudioFormat, BindReport, PageProvenance, TimedToken

# A real page from work/فلفل المغرور-lighton/p003.json.
REAL_PAGE = 3
REAL_TOKENS = ["طَارُ", "الْعُصْفُورُ", "صَوْصَوْ"]
REAL_DURATION_MS = 27_520


# ── Tokenizer ───────────────────────────────────────────────────────────────────


def test_tokenize_produces_stable_page_prefixed_ids() -> None:
    tokens = tokenize(" ".join(REAL_TOKENS), REAL_PAGE)
    assert [t.id for t in tokens] == ["p003w000", "p003w001", "p003w002"]


def test_tokenize_is_deterministic() -> None:
    """The Job A -> Job B hinge: same text and page always yields the same ids."""
    text = " ".join(REAL_TOKENS)
    assert tokenize(text, REAL_PAGE) == tokenize(text, REAL_PAGE)


def test_tokenize_detokenize_round_trips() -> None:
    text = " ".join(REAL_TOKENS)
    assert detokenize(tokenize(text, REAL_PAGE)) == text


def test_tokenize_collapses_irregular_whitespace() -> None:
    """Newlines and runs of spaces must not create empty tokens or shift ids."""
    tokens = tokenize("  طَارُ \n\n الْعُصْفُورُ \t صَوْصَوْ  ", REAL_PAGE)
    assert [t.text for t in tokens] == REAL_TOKENS
    assert [t.id for t in tokens] == ["p003w000", "p003w001", "p003w002"]


def test_tokenize_empty_text_yields_no_tokens() -> None:
    assert tokenize("", REAL_PAGE) == []
    assert tokenize("   \n  ", REAL_PAGE) == []


# ── Text artifact (REQ-ART-02) ──────────────────────────────────────────────────


def test_text_artifact_is_utf8_without_bom() -> None:
    payload = build_text(tokenize(" ".join(REAL_TOKENS), REAL_PAGE))
    assert not payload.startswith(b"\xef\xbb\xbf")
    assert payload.decode("utf-8")


def test_text_artifact_has_exactly_one_trailing_newline() -> None:
    payload = build_text(tokenize(" ".join(REAL_TOKENS), REAL_PAGE))
    assert payload.endswith(b"\n")
    assert not payload.endswith(b"\n\n")


def test_text_artifact_is_byte_stable_across_runs() -> None:
    """Job B re-tokenizes this file, so drifting bytes mean drifting token ids."""
    tokens = tokenize(" ".join(REAL_TOKENS), REAL_PAGE)
    assert build_text(tokens) == build_text(tokens)


def test_empty_page_yields_newline_only_not_an_error() -> None:
    """An illustration-only page is a completable page.

    The Backend waits for every page in 1..N, so a picture page must map to a legitimate
    artifact rather than a job failure. The existing diacritization code raises
    RuntimeError("No Arabic word boxes were detected.") on such a page; that must not
    propagate here.
    """
    assert build_text([]) == b"\n"


def test_text_artifact_round_trips_through_the_tokenizer() -> None:
    """The property that makes the whole Job A -> Job B handoff work."""
    original = tokenize(" ".join(REAL_TOKENS), REAL_PAGE)
    text = build_text(original).decode("utf-8")
    assert tokenize(text, REAL_PAGE) == original


def test_text_artifact_descriptor_metadata() -> None:
    descriptor = build_text_artifact(tokenize("أ ب", REAL_PAGE), REAL_PAGE)
    assert descriptor.artifact_type is ArtifactType.TEXT
    assert descriptor.content_type == "text/plain"
    assert descriptor.file_name == "p003.txt"
    assert descriptor.size_in_bytes == len(descriptor.payload)


# ── Timing artifact (REQ-ART-04) ────────────────────────────────────────────────


def real_timed_tokens() -> list[TimedToken]:
    """Real windows from work/فلفل المغرور-lighton/p003.json."""
    return [
        TimedToken("p003w000", "طَارُ", 0, 560, TimingSource.ANCHOR),
        TimedToken("p003w001", "الْعُصْفُورُ", 560, 1240, TimingSource.ANCHOR),
        TimedToken("p003w002", "صَوْصَوْ", 1240, 1820, TimingSource.INTERPOLATED),
    ]


def provenance() -> PageProvenance:
    return PageProvenance(
        tts_model="gemini-3.1-flash-tts-preview",
        voice="Kore",
        align_method="forced-whisper-small",
    )


def build_real_document() -> dict:
    tokens = real_timed_tokens()
    return build_timing_document(
        page_number=REAL_PAGE,
        tokens=tokens,
        duration_ms=REAL_DURATION_MS,
        audio_format=AudioFormat(),
        bind_report=BindReport.from_tokens(tokens),
        provenance=provenance(),
    )


def test_timing_document_shape() -> None:
    doc = build_real_document()
    assert doc["schema_version"] == SCHEMA_VERSION
    assert doc["page"] == REAL_PAGE
    assert doc["duration_ms"] == REAL_DURATION_MS
    assert doc["audio"] == {
        "codec": "pcm_s16le",
        "container": "wav",
        "sample_rate_hz": 24_000,
        "channels": 1,
        "bit_depth": 16,
    }


def test_timing_document_is_self_contained() -> None:
    """Carrying `txt` per token means the reader never correlates back to Job A."""
    doc = build_real_document()
    assert [t["txt"] for t in doc["tokens"]] == REAL_TOKENS


def test_timing_document_reports_anchor_ratio() -> None:
    """Quality ships inside the artifact, so a consumer can tell a good page from a bad one.

    Corpus baseline is 0.78 across 95 real pages; per book the observed range is
    0.51 (Gemini + forced alignment) to 0.94 (Hume native timestamps).
    """
    prov = build_real_document()["provenance"]
    assert prov["anchor_ratio"] == pytest.approx(2 / 3, abs=1e-4)
    assert (prov["tokens"], prov["anchored"], prov["interpolated"], prov["untimed"]) == (
        3,
        2,
        1,
        0,
    )
    assert prov["align_method"] == "forced-whisper-small"


def test_timing_document_excludes_internal_fields() -> None:
    """A projection, not a dump of the internal page record."""
    doc = build_real_document()
    for leaked in ("whisper_words", "pdf_sha256_16", "status", "ocr_txt", "box_level"):
        assert leaked not in doc
        assert all(leaked not in token for token in doc["tokens"])


def test_untimed_token_omits_both_t_and_t_src() -> None:
    """Absence means 'no timing available'. There is no third state to interpret."""
    tokens = [
        TimedToken("p003w000", "طَارُ", 0, 560, TimingSource.ANCHOR),
        TimedToken.untimed("p003w001", "الْعُصْفُورُ"),
    ]
    doc = build_timing_document(
        page_number=REAL_PAGE,
        tokens=tokens,
        duration_ms=1000,
        audio_format=AudioFormat(),
        bind_report=BindReport.from_tokens(tokens),
        provenance=provenance(),
    )
    assert "t" not in doc["tokens"][1]
    assert "t_src" not in doc["tokens"][1]
    assert doc["tokens"][0]["t"] == [0, 560]
    assert doc["tokens"][0]["t_src"] == "anchor"


def test_timing_rejects_overlapping_windows() -> None:
    tokens = [
        TimedToken("p003w000", "a", 0, 900, TimingSource.ANCHOR),
        TimedToken("p003w001", "b", 500, 1200, TimingSource.ANCHOR),
    ]
    with pytest.raises(TimingArtifactError, match="overlapping"):
        build_timing_document(
            page_number=REAL_PAGE,
            tokens=tokens,
            duration_ms=2000,
            audio_format=AudioFormat(),
            bind_report=BindReport.from_tokens(tokens),
            provenance=provenance(),
        )


def test_timing_rejects_window_beyond_duration() -> None:
    tokens = [TimedToken("p003w000", "a", 0, 5000, TimingSource.ANCHOR)]
    with pytest.raises(TimingArtifactError, match="beyond duration"):
        build_timing_document(
            page_number=REAL_PAGE,
            tokens=tokens,
            duration_ms=1000,
            audio_format=AudioFormat(),
            bind_report=BindReport.from_tokens(tokens),
            provenance=provenance(),
        )


def test_zero_anchor_page_still_produces_a_valid_artifact() -> None:
    """Zero anchors is a DEGRADED result, not a failure. The page still completes."""
    tokens = [TimedToken.untimed(f"p003w{i:03d}", "x") for i in range(3)]
    doc = build_timing_document(
        page_number=REAL_PAGE,
        tokens=tokens,
        duration_ms=1000,
        audio_format=AudioFormat(),
        bind_report=BindReport.from_tokens(tokens),
        provenance=provenance(),
    )
    assert doc["provenance"]["anchor_ratio"] == 0.0
    assert doc["provenance"]["untimed"] == 3
    assert all("t" not in token for token in doc["tokens"])


def test_timing_artifact_descriptor_is_valid_json() -> None:
    tokens = real_timed_tokens()
    descriptor = build_timing_artifact(
        page_number=REAL_PAGE,
        tokens=tokens,
        duration_ms=REAL_DURATION_MS,
        audio_format=AudioFormat(),
        bind_report=BindReport.from_tokens(tokens),
        provenance=provenance(),
    )
    assert descriptor.artifact_type is ArtifactType.JSON
    assert descriptor.content_type == "application/json"
    assert descriptor.file_name == "p003.json"
    reparsed = json.loads(descriptor.payload)
    assert reparsed["page"] == REAL_PAGE
    # Arabic must survive as real characters, not \u escapes.
    assert "طَارُ" in descriptor.payload.decode("utf-8")


def test_timing_artifact_is_a_realistic_size() -> None:
    """Sanity check against the ~3.3 KB per-page projection measured from real records."""
    tokens = [
        TimedToken(f"p003w{i:03d}", "الْعُصْفُورُ", i * 100, (i + 1) * 100, TimingSource.ANCHOR)
        for i in range(45)
    ]
    descriptor = build_timing_artifact(
        page_number=REAL_PAGE,
        tokens=tokens,
        duration_ms=4500,
        audio_format=AudioFormat(),
        bind_report=BindReport.from_tokens(tokens),
        provenance=provenance(),
    )
    assert 1_000 < descriptor.size_in_bytes < 8_000
