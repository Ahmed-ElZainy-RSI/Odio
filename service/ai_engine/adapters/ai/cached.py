"""Cache wrappers for the paid OCR and TTS engines (spec 021).

A retried or re-run page should not pay twice for a call that already succeeded with the
same inputs. These wrap any engine implementing the port, keep its identity attributes
(``name``/``provider``/``model_id`` -- the budget and the verified tier read them), and:

  - key on everything that determines the result (``domain.cache_key``);
  - store only what the inner engine returned *successfully* -- its own validation (e.g.
    the TTS duration sanity gate) has already passed; an exception is never cached;
  - treat an unreadable entry as a miss.
"""

from __future__ import annotations

import json
import struct
from collections.abc import Sequence
from typing import Any

from ai_engine.domain.cache_key import digest, fingerprint
from ai_engine.domain.tokens import AudioFormat, PageProvenance, Token
from ai_engine.observability.logging import get_logger
from ai_engine.observability.metrics import PROVIDER_CACHE, Metrics
from ai_engine.ports.cache import ProviderCache

log = get_logger(__name__)


class _Wrapper:
    def __init__(self, inner: Any, cache: ProviderCache, metrics: Metrics | None) -> None:
        self._inner = inner
        self._cache = cache
        self._metrics = metrics
        self.name = inner.name
        self.provider = getattr(inner, "provider", "unknown")
        self.model_id = getattr(inner, "model_id", "unknown")

    def _count(self, operation: str, outcome: str) -> None:
        if self._metrics is not None:
            self._metrics.inc(PROVIDER_CACHE, {"operation": operation, "outcome": outcome})

    def __getattr__(self, attr: str) -> Any:  # anything else: the inner engine's
        return getattr(self._inner, attr)


class CachedOcrEngine(_Wrapper):
    """Wraps an :class:`~ai_engine.ports.ai.OcrEngine`."""

    def key(self, page_image: bytes | None, page_number: int) -> str | None:
        if page_image is None:
            return None  # nothing to fingerprint the input by: don't cache
        return fingerprint(
            provider=self.provider,
            model=self.model_id,
            operation=f"ocr:{self.name}",
            # the page number is part of the result: token ids embed it
            params={"page_image": digest(page_image), "page_number": page_number},
        )

    def extract_page(
        self, *, page_image: bytes | None, pdf_path: str | None, page_number: int
    ) -> tuple[Sequence[Token], PageProvenance]:
        key = self.key(page_image, page_number)
        if key is not None:
            hit = self._cache.get(key)
            if hit is not None:
                decoded = _decode_ocr(hit)
                if decoded is not None:
                    self._count("ocr", "hit")
                    return decoded
        tokens, provenance = self._inner.extract_page(
            page_image=page_image, pdf_path=pdf_path, page_number=page_number
        )
        if key is not None:
            self._cache.put(key, _encode_ocr(tokens, provenance))
            self._count("ocr", "miss")
        return tokens, provenance


class CachedTts(_Wrapper):
    """Wraps a :class:`~ai_engine.ports.ai.TtsEngine`."""

    def key(self, text: str, voice: str | None) -> str:
        return fingerprint(
            provider=self.provider,
            model=self.model_id,
            operation="tts",
            params={"text": digest(text), "voice": voice or getattr(self._inner, "_voice", "")},
        )

    def synthesize(self, text: str, *, voice: str | None = None) -> tuple[bytes, AudioFormat, int]:
        key = self.key(text, voice)
        hit = self._cache.get(key)
        if hit is not None:
            decoded = _decode_audio(hit)
            if decoded is not None:
                self._count("tts", "hit")
                return decoded
        payload, fmt, duration_ms = self._inner.synthesize(text, voice=voice)
        self._cache.put(key, _encode_audio(payload, fmt, duration_ms))
        self._count("tts", "miss")
        return payload, fmt, duration_ms


# ── encodings: a small JSON header, then raw bytes for audio ────────────────────


def _encode_ocr(tokens: Sequence[Token], provenance: PageProvenance) -> bytes:
    body = {
        "tokens": [
            {
                "id": t.id,
                "text": t.text,
                "raw": t.raw,
                "box": list(t.box) if t.box is not None else None,
                "fixes": list(t.fixes),
            }
            for t in tokens
        ],
        "provenance": {
            "ocr_engine": provenance.ocr_engine,
            "box_level": provenance.box_level,
            "extra": dict(provenance.extra),
        },
    }
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def _decode_ocr(raw: bytes) -> tuple[list[Token], PageProvenance] | None:
    try:
        body = json.loads(raw.decode("utf-8"))
        tokens = [
            Token(
                id=t["id"],
                text=t["text"],
                raw=t.get("raw"),
                box=tuple(t["box"]) if t.get("box") is not None else None,
                fixes=tuple(t.get("fixes") or ()),
            )
            for t in body["tokens"]
        ]
        p = body["provenance"]
        return tokens, PageProvenance(
            ocr_engine=p.get("ocr_engine"),
            box_level=p.get("box_level", "none"),
            extra=dict(p.get("extra") or {}),
        )
    except (ValueError, KeyError, TypeError) as exc:
        log.warning("cache.decode_failed", kind="ocr", error=type(exc).__name__)
        return None


def _encode_audio(payload: bytes, fmt: AudioFormat, duration_ms: int) -> bytes:
    header = json.dumps(
        {
            "duration_ms": duration_ms,
            "codec": fmt.codec,
            "container": fmt.container,
            "sample_rate_hz": fmt.sample_rate_hz,
            "channels": fmt.channels,
            "bit_depth": fmt.bit_depth,
        }
    ).encode("utf-8")
    return struct.pack(">I", len(header)) + header + payload


def _decode_audio(raw: bytes) -> tuple[bytes, AudioFormat, int] | None:
    try:
        (n,) = struct.unpack(">I", raw[:4])
        header = json.loads(raw[4 : 4 + n].decode("utf-8"))
        payload = raw[4 + n :]
        if not payload:
            raise ValueError("empty audio")
        fmt = AudioFormat(
            codec=header["codec"],
            container=header["container"],
            sample_rate_hz=header["sample_rate_hz"],
            channels=header["channels"],
            bit_depth=header["bit_depth"],
        )
        return payload, fmt, int(header["duration_ms"])
    except (ValueError, KeyError, TypeError, struct.error) as exc:
        log.warning("cache.decode_failed", kind="tts", error=type(exc).__name__)
        return None
