"""Paid OCR adapters, for pages with no usable text layer.

Two engines, both wrapping the legacy pipeline's implementations rather than reimplementing
them. What the adapters add is the discipline the legacy code lacks: an explicit timeout, a
model id from configuration instead of a module constant, typed domain errors instead of
`RuntimeError`, retries owned by exactly one layer, and structured logging with a call
count so the cost is visible.

The two engines differ in a way that matters downstream and is worth stating plainly:

* **Gemini** returns per-*line* boxes, which the legacy `lines_to_tokens` subdivides into
  word boxes proportionally by letter count. The line box is the only real measurement, so
  word geometry is approximate -- `box_level` is `"line"`.
* **LightOn** returns markdown with **no geometry at all**. `box` is omitted rather than
  fabricated, and `box_level` is `"none"`.

So switching OCR engine silently changes whether the reader can highlight on the page
image. That is a real consequence of a config change, not an implementation detail.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx

from ai_engine.adapters.ai.legacy import LegacyPipelineUnavailable, ensure_legacy_on_path
from ai_engine.adapters.http.retry import RetryExhausted, RetryPolicy, send_with_retry
from ai_engine.domain.errors import (
    AiInferenceError,
    ErrorCode,
    ModelUnavailableError,
    SourceInvalidError,
)
from ai_engine.domain.tokens import PageProvenance, Token
from ai_engine.observability.logging import get_logger
from ai_engine.observability.metrics import (
    INFERENCE_CALLS,
    INFERENCE_FAILURES,
    Metrics,
)

log = get_logger(__name__)


def _load_legacy(repo_root: Path | None, module: str):
    try:
        ensure_legacy_on_path(repo_root)
        import importlib

        return importlib.import_module(f"pipeline.{module}")
    except (LegacyPipelineUnavailable, ImportError) as exc:
        raise ModelUnavailableError(
            f"the legacy OCR module pipeline.{module} could not be loaded: {exc}"
        ) from exc


def _records_to_tokens(records: Sequence[dict]) -> list[Token]:
    """Translate legacy token dicts into domain tokens.

    Tokens whose cleaned text is empty or contains whitespace are dropped: the `Text`
    artifact is a space-join, so a whitespace-bearing token would make it non-invertible
    and silently shift every following token id in Job B.
    """
    tokens: list[Token] = []
    for record in records:
        text = (record.get("txt") or "").strip()
        if not text or any(character.isspace() for character in text):
            continue
        box = record.get("box")
        tokens.append(
            Token(
                id=record["id"],
                text=text,
                raw=record.get("raw"),
                box=tuple(box) if box else None,
                fixes=tuple(record.get("fixes") or ()),
            )
        )
    return tokens


class GeminiVisionOcr:
    """OCR via Gemini vision. Per-line boxes, subdivided into words."""

    name = "gemini"
    provider = "gemini"

    def __init__(
        self,
        *,
        model_id: str = "gemini-3.6-flash",
        api_key: str | None = None,
        client: Any | None = None,
        legacy_repo_root: Path | None = None,
        metrics: Metrics | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.model_id = model_id
        self._api_key = api_key
        self._client = client
        self._legacy_repo_root = legacy_repo_root
        self._metrics = metrics
        self._timeout = timeout_seconds

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self._api_key:
            raise ModelUnavailableError("GEMINI_API_KEY is not configured")
        try:
            from google import genai  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - dependency-group failure
            raise ModelUnavailableError(f"google-genai is not installed: {exc}") from exc
        self._client = genai.Client(api_key=self._api_key)
        return self._client

    def extract_page(
        self, *, page_image: bytes | None, pdf_path: str | None, page_number: int
    ) -> tuple[Sequence[Token], PageProvenance]:
        if page_image is None:
            raise SourceInvalidError("the Gemini OCR path needs a rendered page image")

        legacy = _load_legacy(self._legacy_repo_root, "ocr_gemini")
        extract = _load_legacy(self._legacy_repo_root, "extract")
        client = self._ensure_client()

        if self._metrics is not None:
            self._metrics.inc(
                INFERENCE_CALLS,
                {"provider": self.name, "model": self.model_id, "stage": "ocr"},
            )
        try:
            lines = self._request(client, legacy, page_image)
        except (AiInferenceError, ModelUnavailableError) as exc:
            self._count_failure(exc)
            raise
        except Exception as exc:
            # Retries are owned by one layer. This adapter reports and does not loop.
            failure = AiInferenceError(f"OCR provider failed: {type(exc).__name__}")
            self._count_failure(failure)
            raise failure from exc

        records = legacy.lines_to_tokens(lines, page_number, extract.clean_word)
        tokens = _records_to_tokens(records)
        log.info(
            "ocr.extracted",
            stage="ocr",
            page_number=page_number,
            engine=self.name,
            model=self.model_id,
            lines=len(lines),
            tokens=len(tokens),
        )
        # An illustration-only page legitimately yields zero tokens; that is a completable
        # page, not an error.
        return tokens, PageProvenance(
            ocr_engine=f"{self.name}:{self.model_id}",
            box_level="line",
            extra={"source": "ocr", "lines": str(len(lines))},
        )

    def _request(self, client: Any, legacy: Any, page_image: bytes) -> list[dict]:
        """One structured-output call, returning the model's line records."""
        from google.genai import types  # type: ignore[import-not-found]

        response = client.models.generate_content(
            model=self.model_id,
            contents=[
                types.Part.from_bytes(data=page_image, mime_type="image/jpeg"),
                legacy.PROMPT,
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=legacy.SCHEMA,
                temperature=0.0,
                http_options=types.HttpOptions(timeout=int(self._timeout * 1000)),
            ),
        )
        parsed = getattr(response, "parsed", None)
        if parsed is None:
            import json

            text = getattr(response, "text", None)
            if not text:
                raise AiInferenceError("OCR provider returned no content")
            try:
                parsed = json.loads(text)
            except ValueError as exc:
                raise AiInferenceError("OCR provider returned unparseable JSON") from exc
        if not isinstance(parsed, list):
            raise AiInferenceError("OCR provider returned an unexpected shape")
        return [dict(line) for line in parsed]

    def _count_failure(self, exc: Exception) -> None:
        if self._metrics is None:
            return
        code = getattr(getattr(exc, "reason", None), "code", ErrorCode.AI_INFERENCE_ERROR)
        self._metrics.inc(
            INFERENCE_FAILURES,
            {"provider": self.name, "model": self.model_id, "error_code": code.value},
        )


class LightOnOcr:
    """OCR via LightOn Parse. Markdown only -- no geometry is available."""

    name = "lighton"
    provider = "lighton"

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str = "https://api.lighton.ai",
        model_id: str = "lighton/parse-v3",
        api_key: str | None = None,
        legacy_repo_root: Path | None = None,
        metrics: Metrics | None = None,
        policy: RetryPolicy | None = None,
    ) -> None:
        self._client = client
        # NOTE: the legacy config default is `http://localhost:8000/v1`, which combined
        # with the client's `/api/v3/parse` suffix builds a nonsense URL and is set in no
        # .env in the repo. The hosted host is the correct default.
        self._base_url = base_url.rstrip("/")
        self.model_id = model_id
        self._api_key = api_key
        self._legacy_repo_root = legacy_repo_root
        self._metrics = metrics
        self._policy = policy or RetryPolicy(max_attempts=3)

    @property
    def endpoint(self) -> str:
        return f"{self._base_url}/api/v3/parse"

    async def extract_page_async(
        self, *, pdf_path: str, page_number: int
    ) -> tuple[Sequence[Token], PageProvenance]:
        """Async because the provider call is network-bound.

        The synchronous :meth:`extract_page` exists so this satisfies the `OcrEngine` port
        from a worker thread; prefer this method when already in async code.
        """
        if not self._api_key:
            raise ModelUnavailableError("LIGHTON_API_KEY is not configured")

        legacy = _load_legacy(self._legacy_repo_root, "ocr_lighton")
        extract = _load_legacy(self._legacy_repo_root, "extract")
        page_pdf = self._slice_page(legacy, pdf_path, page_number)

        if self._metrics is not None:
            self._metrics.inc(
                INFERENCE_CALLS,
                {"provider": self.name, "model": self.model_id, "stage": "ocr"},
            )

        async def send() -> httpx.Response:
            return await self._client.post(
                self.endpoint,
                headers={"Authorization": f"Bearer {self._api_key}"},
                files={"file": ("page.pdf", page_pdf, "application/pdf")},
            )

        try:
            response = await send_with_retry(
                send,
                policy=self._policy,
                operation="lighton.parse",
                code=ErrorCode.AI_INFERENCE_ERROR,
            )
        except RetryExhausted as exc:
            self._count_failure(ErrorCode.AI_INFERENCE_ERROR)
            raise AiInferenceError(
                f"OCR provider failed after {exc.attempts} attempts ({exc.last_error})"
            ) from exc

        if response.status_code >= 400:
            self._count_failure(ErrorCode.AI_INFERENCE_ERROR)
            raise AiInferenceError(f"OCR provider returned HTTP {response.status_code}")

        markdown = self._first_page_markdown(response)
        text = legacy.clean_markdown(markdown)
        records = legacy.text_to_tokens(text, page_number, extract.clean_word)
        tokens = _records_to_tokens(records)
        log.info(
            "ocr.extracted",
            stage="ocr",
            page_number=page_number,
            engine=self.name,
            model=self.model_id,
            tokens=len(tokens),
            box_level="none",
        )
        return tokens, PageProvenance(
            ocr_engine=self.model_id,
            # No geometry at all. Omitted rather than fabricated -- a reader that trusted
            # invented boxes would highlight the wrong words.
            box_level="none",
            extra={"source": "ocr"},
        )

    def extract_page(
        self, *, page_image: bytes | None, pdf_path: str | None, page_number: int
    ) -> tuple[Sequence[Token], PageProvenance]:
        """Synchronous `OcrEngine` entry point, for use from a worker thread."""
        import asyncio

        if pdf_path is None:
            raise SourceInvalidError("the LightOn OCR path needs the source PDF")
        return asyncio.run(self.extract_page_async(pdf_path=pdf_path, page_number=page_number))

    @staticmethod
    def _slice_page(legacy: Any, pdf_path: str, page_number: int) -> bytes:
        """One-page PDF, so each request maps to exactly one page record."""
        from pypdf import PdfReader

        reader = PdfReader(pdf_path)
        if not 1 <= page_number <= len(reader.pages):
            raise SourceInvalidError(
                f"page {page_number} is outside the document's 1..{len(reader.pages)}"
            )
        return legacy.slice_page(reader, page_number - 1)

    @staticmethod
    def _first_page_markdown(response: httpx.Response) -> str:
        try:
            pages = response.json()["result"]["pages"]
        except (ValueError, KeyError, TypeError) as exc:
            raise AiInferenceError("OCR provider response did not contain result.pages") from exc
        if not pages:
            # A blank or illustration-only page. Empty is a legitimate result.
            return ""
        return pages[0].get("markdown") or ""

    def _count_failure(self, code: ErrorCode) -> None:
        if self._metrics is not None:
            self._metrics.inc(
                INFERENCE_FAILURES,
                {"provider": self.name, "model": self.model_id, "error_code": code.value},
            )


def build_ocr_engine(
    *,
    settings: Any,
    renderer: Any,
    http_client: httpx.AsyncClient,
    metrics: Metrics,
    pdf_path: str | None = None,
) -> Any:
    """Pick an OCR engine.

    `auto` prefers the embedded text layer when the document has one: it is exact, free,
    and 25 of the 36 sample books qualify. The default should never spend money it does not
    have to.
    """
    from ai_engine.adapters.ai.ocr_textlayer import TextLayerOcr

    choice = getattr(settings, "ocr_engine", "auto")
    legacy_root = settings.legacy_repo_root

    if choice == "auto":
        if pdf_path is not None and renderer.has_text_layer(pdf_path):
            return TextLayerOcr(legacy_repo_root=legacy_root)
        choice = "gemini"

    if choice == "textlayer":
        return TextLayerOcr(legacy_repo_root=legacy_root)
    if choice == "gemini":
        key = settings.gemini_api_key.get_secret_value() or None
        _require_key(key, "GEMINI_API_KEY", "gemini")
        return GeminiVisionOcr(
            model_id=settings.ocr_gemini_model,
            api_key=key,
            legacy_repo_root=legacy_root,
            metrics=metrics,
        )
    if choice == "lighton":
        key = settings.lighton_api_key.get_secret_value() or None
        _require_key(key, "LIGHTON_API_KEY", "lighton")
        return LightOnOcr(
            client=http_client,
            base_url=settings.lighton_base_url,
            api_key=key,
            legacy_repo_root=legacy_root,
            metrics=metrics,
        )
    raise ModelUnavailableError(f"unknown ocr_engine {choice!r}")


def _require_key(key: str | None, env_var: str, engine: str) -> None:
    """Refuse a paid engine with no credentials *before* any page is rendered.

    A missing key is a deployment mistake affecting the whole book, so it should surface
    once as a job failure -- not as N identical page failures after N renders.
    """
    if not key:
        raise ModelUnavailableError(
            f"{env_var} is not configured but OCR_ENGINE resolved to {engine!r}"
        )
