"""The `JSON` timing artifact (REQ-ART-04).

The second mandated artifact that does not exist as a file today. The data exists: the
pipeline writes ``tokens[].t`` and ``tokens[].t_src`` into ``work/<book>/pNNN.json``,
mixed in with OCR provenance, a ``status`` state machine and the raw ``whisper_words``
array (21% of the record by bytes). This module carves out a clean projection.

The V1 PDF says only "timing json" and specifies no schema, so the shape below is a
PROPOSAL pending Q-10. Four principles drove it:

1. **Self-contained** -- it carries `txt` per token, so a reader never has to correlate
   back to Job A's text file. Costs ~1.5 KB/page and removes a class of coupling bugs.
2. **Versioned** -- `schema_version` from day one. Adding it later would be breaking.
3. **A projection, not a dump** -- no `whisper_words`, no `pdf_sha256_16`, no `status`.
4. **Honest about quality** -- `provenance.anchor_ratio` ships inside the artifact, so a
   consumer can tell a 94%-anchored page from a 51%-anchored one. That distinction is real:
   README.md:473,491 records exactly that spread across two real books.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from ai_engine.domain.enums import ArtifactType
from ai_engine.domain.job import ArtifactDescriptor
from ai_engine.domain.tokens import AudioFormat, BindReport, PageProvenance, TimedToken

#: Bump the MINOR for additive optional fields, the MAJOR for anything a consumer could
#: break on. Consumers must ignore unknown fields.
SCHEMA_VERSION = "1.0"


class TimingArtifactError(ValueError):
    """The timing data violates an invariant a consumer is allowed to rely on."""


def _validate(tokens: Sequence[TimedToken], duration_ms: int) -> None:
    """Enforce the guarantees documented for consumers.

    Deliberately strict: a reader that trusts monotonicity and then meets an overlapping
    window will mis-highlight, and the bug will look like a reader bug.
    """
    if duration_ms < 0:
        raise TimingArtifactError(f"duration_ms must be >= 0, got {duration_ms}")

    previous_end: int | None = None
    for token in tokens:
        if not token.is_timed:
            continue
        start, end = token.start_ms, token.end_ms
        assert start is not None and end is not None
        if end > duration_ms:
            raise TimingArtifactError(
                f"{token.id} ends at {end}ms, beyond duration {duration_ms}ms"
            )
        if previous_end is not None and start < previous_end:
            raise TimingArtifactError(
                f"{token.id} starts at {start}ms, overlapping previous end {previous_end}ms"
            )
        previous_end = end


def build_timing_document(
    *,
    page_number: int,
    tokens: Sequence[TimedToken],
    duration_ms: int,
    audio_format: AudioFormat,
    bind_report: BindReport,
    provenance: PageProvenance,
) -> dict:
    """Build the timing artifact as a plain dict, validated."""
    _validate(tokens, duration_ms)

    token_entries: list[dict] = []
    for token in tokens:
        entry: dict = {"id": token.id, "txt": token.text}
        if token.is_timed:
            entry["t"] = [token.start_ms, token.end_ms]
            entry["t_src"] = token.source.value  # type: ignore[union-attr]
        # An untimed token carries neither `t` nor `t_src`. Absence means "no timing
        # available" -- the reader shows the word unhighlighted rather than guessing.
        token_entries.append(entry)

    return {
        "schema_version": SCHEMA_VERSION,
        "page": page_number,
        "duration_ms": duration_ms,
        "audio": {
            "codec": audio_format.codec,
            "container": audio_format.container,
            "sample_rate_hz": audio_format.sample_rate_hz,
            "channels": audio_format.channels,
            "bit_depth": audio_format.bit_depth,
        },
        "provenance": {
            "tts_model": provenance.tts_model,
            "voice": provenance.voice,
            "align_method": provenance.align_method,
            "anchor_ratio": round(bind_report.anchor_ratio, 4),
            "tokens": bind_report.tokens,
            "anchored": bind_report.anchored,
            "interpolated": bind_report.interpolated,
            "untimed": bind_report.untimed,
        },
        "tokens": token_entries,
    }


def build_timing_artifact(
    *,
    page_number: int,
    tokens: Sequence[TimedToken],
    duration_ms: int,
    audio_format: AudioFormat,
    bind_report: BindReport,
    provenance: PageProvenance,
) -> ArtifactDescriptor:
    """The timing artifact, ready for the upload cycle."""
    document = build_timing_document(
        page_number=page_number,
        tokens=tokens,
        duration_ms=duration_ms,
        audio_format=audio_format,
        bind_report=bind_report,
        provenance=provenance,
    )
    payload = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return ArtifactDescriptor(
        artifact_type=ArtifactType.JSON,
        payload=payload,
        file_name=f"p{page_number:03d}.json",
    )
