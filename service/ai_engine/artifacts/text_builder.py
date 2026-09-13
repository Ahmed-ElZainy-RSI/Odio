"""The `Text` artifact (REQ-ART-02).

One of the two mandated artifacts that does not exist on disk today. The exact bytes are
already computed by the existing pipeline and thrown away -- see
``src/pipeline/run_ocr_gemini.py:65``, where

    text = " ".join(t["txt"] for t in tokens)

is a local variable that is never written. This module is that line, given a contract.

The V1 spec calls it "only the free text no highlighting no timing".

ASSUMPTION (Q-04): this is the FINAL reading text -- corrected and diacritized -- because
it is what the reader UI displays and what Job B feeds to TTS, and undiacritized Arabic is
mispronounced. If the Backend means raw OCR text instead, both job scopes change.
"""

from __future__ import annotations

from collections.abc import Sequence

from ai_engine.domain.enums import ArtifactType
from ai_engine.domain.job import ArtifactDescriptor
from ai_engine.domain.tokens import Token

#: UTF-8 with no BOM. A BOM shows up as a stray character in naive consumers, and this
#: file is re-read by our own Job B.
ENCODING = "utf-8"


def build_text(tokens: Sequence[Token]) -> bytes:
    """Render page tokens as the Text artifact's exact bytes.

    Guarantees:
      - UTF-8, no BOM.
      - Exactly one trailing newline, so the file is diff-able and cat-able.
      - An empty page (illustration only) yields a newline-only file, NOT an error -- the
        Backend still waits for that page, so it must be a completable page.
      - Byte-stable: the same tokens always produce the same bytes. Job B re-tokenizes
        this file, so drifting bytes would mean drifting token ids.
    """
    body = " ".join(token.text for token in tokens)
    return f"{body}\n".encode(ENCODING)


def build_text_artifact(tokens: Sequence[Token], page_number: int) -> ArtifactDescriptor:
    """The Text artifact, ready for the upload cycle."""
    return ArtifactDescriptor(
        artifact_type=ArtifactType.TEXT,
        payload=build_text(tokens),
        file_name=f"p{page_number:03d}.txt",
    )
