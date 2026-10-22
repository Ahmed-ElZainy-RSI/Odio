"""The paid calls the visual verifier makes (spec 022): a text reviewer, a blind crop
reader, and a crop re-reader. Each is a small class over an injected sync ``httpx.Client``
so the verifier can be tested with fakes and these can be tested with a mock transport.

Failure rules, which differ on purpose:

  - **the reviewer** raises when it can't produce a parseable answer. A failed review is
    a failed page, never "no suspects": that would silently pass every OCR error through.
  - **the blind reader** gets one attempt; a bad answer is UNCLEAR, which is zero evidence
    -- and an unclear read can never accept a letter change, so failing safe is free.
  - **quota / outage** (HTTP 429, 503) is :class:`ModelUnavailableError` everywhere, so the
    page is retried later instead of being judged on missing evidence.
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any

import httpx

from ai_engine.domain.cache_key import digest, fingerprint
from ai_engine.domain.errors import AiInferenceError, ModelUnavailableError
from ai_engine.observability import usage
from ai_engine.observability.logging import get_logger
from ai_engine.observability.metrics import PROVIDER_CACHE, Metrics
from ai_engine.ports.cache import ProviderCache

from .crops import upscale_for_reading

log = get_logger(__name__)

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com"
_UNAVAILABLE = frozenset({429, 503})
_TRAILING_COMMA = re.compile(r",\s*([}\]])")


def parse_json_object(text: str) -> dict[str, Any]:
    """The JSON object in a model reply: code fences, prose around it and trailing commas
    are tolerated. Anything else is :class:`AiInferenceError`."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else ""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise AiInferenceError("model reply has no JSON object")
    body = text[start : end + 1]
    for candidate in (body, _TRAILING_COMMA.sub(r"\1", body)):
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    raise AiInferenceError("model reply is not a parseable JSON object")


class _CallCache:
    """The paid-call cache (spec 021) for one provider's verifier calls.

    Only a reply that already parsed is stored, so a hit never needs re-validating beyond
    decoding; an undecodable entry is a miss. A hit is not a paid call: ``calls`` is not
    incremented, and usage is recorded as cached.
    """

    def __init__(self, cache: ProviderCache | None, metrics: Metrics | None) -> None:
        self._cache = cache
        self._metrics = metrics

    def _count(self, outcome: str) -> None:
        if self._metrics is not None:
            self._metrics.inc(PROVIDER_CACHE, {"operation": "verify", "outcome": outcome})

    def get(self, key: str) -> Any:
        if self._cache is None:
            return None
        raw = self._cache.get(key)
        if raw is None:
            return None
        try:
            value = json.loads(raw.decode("utf-8"))["v"]
        except (ValueError, KeyError, TypeError) as exc:
            log.warning("cache.decode_failed", kind="verify", error=type(exc).__name__)
            return None
        self._count("hit")
        return value

    def put(self, key: str, value: Any) -> None:
        if self._cache is None:
            return
        self._cache.put(key, json.dumps({"v": value}, ensure_ascii=False).encode("utf-8"))
        self._count("miss")


class GeminiJson:
    """One Gemini ``generateContent`` call that must come back as a JSON object."""

    provider = "gemini"

    def __init__(
        self,
        *,
        client: httpx.Client,
        api_key: str,
        model_id: str,
        base_url: str = GEMINI_BASE_URL,
        cache: ProviderCache | None = None,
        metrics: Metrics | None = None,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self.model_id = model_id
        self._url = f"{base_url.rstrip('/')}/v1beta/models/{model_id}:generateContent"
        self._cache = _CallCache(cache, metrics)
        self.calls = 0

    def ask(
        self, *, system: str, user: str, image_png: bytes | None = None, operation: str
    ) -> dict[str, Any]:
        key = fingerprint(
            provider=self.provider,
            model=self.model_id,
            operation=operation,
            params={
                "system": digest(system),
                "user": digest(user),
                "image": digest(image_png) if image_png is not None else None,
            },
        )
        hit = self._cache.get(key)
        if isinstance(hit, dict):
            usage.record(self.provider, self.model_id, operation, cached=True)
            return hit
        reply = self._call(system=system, user=user, image_png=image_png, operation=operation)
        self._cache.put(key, reply)
        return reply

    def _call(
        self, *, system: str, user: str, image_png: bytes | None, operation: str
    ) -> dict[str, Any]:
        parts: list[dict[str, Any]] = []
        if image_png is not None:
            parts.append(
                {
                    "inline_data": {
                        "mime_type": "image/png",
                        "data": base64.b64encode(image_png).decode(),
                    }
                }
            )
        parts.append({"text": user})
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0.0, "responseMimeType": "application/json"},
        }
        try:
            response = self._client.post(
                self._url, headers={"x-goog-api-key": self._api_key}, json=body
            )
        except httpx.HTTPError as exc:
            raise AiInferenceError(
                f"verifier call {operation} failed: {type(exc).__name__}"
            ) from exc
        self.calls += 1
        if response.status_code in _UNAVAILABLE:
            raise ModelUnavailableError(
                f"gemini returned HTTP {response.status_code} for {operation}"
            )
        if response.status_code >= 400:
            raise AiInferenceError(f"gemini returned HTTP {response.status_code} for {operation}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise AiInferenceError(f"gemini returned a non-JSON body for {operation}") from exc
        meta = payload.get("usageMetadata") or {}
        # recorded before parsing: an unparseable answer was still billed (spec 025)
        usage.record(
            self.provider,
            self.model_id,
            operation,
            images=1 if image_png is not None else 0,
            input_tokens=int(meta.get("promptTokenCount") or 0),
            output_tokens=int(meta.get("candidatesTokenCount") or 0)
            + int(meta.get("thoughtsTokenCount") or 0),
        )
        try:
            texts = [p.get("text", "") for p in payload["candidates"][0]["content"]["parts"]]
        except (KeyError, IndexError, TypeError) as exc:
            raise AiInferenceError(f"gemini returned no content for {operation}") from exc
        return parse_json_object("".join(texts))


# ── the text reviewer ───────────────────────────────────────────────────────────

_REVIEW_SYSTEM = (
    "أنت مدقق لغوي لنصوص عربية خرجت من التعرف الضوئي على الحروف (OCR). "
    "مهمتك العثور على الكلمات التي يُرجَّح أنها قُرئت خطأً بسبب تشابه شكل الحروف "
    "(مثل: ص/ض، ح/خ/ج، ب/ت/ث/ن/ي، ر/ز، د/ذ، ع/غ، ف/ق، ة/ه). "
    "لا تُصحح الأسلوب ولا النحو ولا التشكيل، ولا تقترح كلمة بعدد حروف مختلف كثيرًا. "
    "انسخ الكلمة والجملة حرفيًا كما وردتا في النص. "
    'أجب بكائن JSON فقط: {"suspects": [{"word": "...", "sentence": "...", '
    '"score": 0.0, "reason": "...", "candidate": "..." أو null}]}. '
    "score بين 0 و1 هو مدى ثقتك بأن الكلمة خاطئة. "
    "إن لم تكن متأكدًا من البديل فاجعل candidate قيمته null."
)
_CONTEXT_SYSTEM = (
    "أنت تقيّم استبدال كلمة واحدة في جملة عربية. قيّم فقط ملاءمة الكلمة البديلة للسياق "
    "مقارنةً بالكلمة الأصلية. أجب بكائن JSON فقط: "
    '{"score": 0.0, "reason": "..."} حيث score بين 0 و1، و1 تعني أن البديل هو الصحيح بلا شك.'
)
_VALIDATE_SYSTEM = (
    "أنت تتحقق من أن استبدال كلمة في نص عربي آمن: أن البديل كلمة عربية صحيحة إملائيًا، "
    "وأن الاستبدال لا يغيّر المعنى إلا بتصحيح خطأ قراءة، وأن الفرق بين الكلمتين فرق شكلي في الحروف. "
    'أجب بكائن JSON فقط: {"score": 0.0, "reason": "..."} حيث score بين 0 و1.'
)


class GeminiReviewer:
    """Finds suspect words in a page's text and scores a proposed replacement."""

    def __init__(self, gemini: GeminiJson, *, attempts: int = 3) -> None:
        self._gemini = gemini
        self._attempts = max(1, attempts)

    def _ask(self, system: str, user: str, operation: str) -> dict[str, Any]:
        last: AiInferenceError | None = None
        for _ in range(self._attempts):
            try:
                return self._gemini.ask(system=system, user=user, operation=operation)
            except AiInferenceError as exc:  # unparseable or a 5xx: worth another try
                last = exc
        raise AiInferenceError(
            f"{operation} failed after {self._attempts} attempts: {last}"
        ) from last

    def find_suspects(self, page_text: str) -> list[dict[str, Any]]:
        reply = self._ask(_REVIEW_SYSTEM, f"النص:\n{page_text}", "verify.review")
        suspects = reply.get("suspects")
        if not isinstance(suspects, list):
            raise AiInferenceError("verify.review reply has no suspects list")
        return [s for s in suspects if isinstance(s, dict)]

    def _score(
        self, system: str, sentence: str, original: str, candidate: str, operation: str
    ) -> float:
        user = f"الجملة: {sentence}\nالكلمة الأصلية: {original}\nالكلمة البديلة: {candidate}"
        try:
            return float(self._ask(system, user, operation).get("score", 0.0))
        except (TypeError, ValueError) as exc:
            raise AiInferenceError(f"{operation} reply has no numeric score") from exc

    def context_score(self, sentence: str, original: str, candidate: str) -> float:
        return self._score(_CONTEXT_SYSTEM, sentence, original, candidate, "verify.context")

    def validation_score(self, sentence: str, original: str, candidate: str) -> float:
        return self._score(_VALIDATE_SYSTEM, sentence, original, candidate, "verify.validate")


# ── the blind reader ────────────────────────────────────────────────────────────

_BLIND_SYSTEM = (
    "أنت قارئ بصري للخط العربي. ستُعرض عليك صورة مقتطعة لكلمة عربية واحدة من صفحة كتاب. "
    "اقرأ الكلمة كما تراها في الصورة حرفًا حرفًا، دون تخمين من المعنى ودون تصحيح. "
    "إن لم تستطع قراءة الكلمة بوضوح فاجعل unclear قيمتها true. "
    'أجب بكائن JSON فقط: {"reading": "...", "confidence": 0.0, "unclear": false}.'
)


class GeminiBlindReader:
    """Reads the crop -- and only the crop: it is never told the OCR text or the candidate,
    so its reading is independent evidence rather than an echo."""

    def __init__(self, gemini: GeminiJson) -> None:
        self._gemini = gemini

    def read(self, crop_png: bytes) -> tuple[str, float, bool]:
        try:
            reply = self._gemini.ask(
                system=_BLIND_SYSTEM,
                user="ما الكلمة المكتوبة في هذه الصورة؟",
                image_png=upscale_for_reading(crop_png),
                operation="verify.blind_read",
            )
            return (
                str(reply.get("reading") or ""),
                float(reply.get("confidence") or 0.0),
                bool(reply.get("unclear")),
            )
        except (AiInferenceError, TypeError, ValueError):
            return "", 0.0, True  # one attempt; anything wrong is UNCLEAR


# ── the crop re-reader ──────────────────────────────────────────────────────────


class LightOnCropReader:
    """Re-reads one crop with LightOn's parse endpoint; returns the page markdown as text."""

    provider = "lighton"

    def __init__(
        self,
        *,
        client: httpx.Client,
        api_key: str,
        base_url: str,
        model_id: str = "lighton/parse-v3",
        cache: ProviderCache | None = None,
        metrics: Metrics | None = None,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._url = f"{base_url.rstrip('/')}/api/v3/parse"
        self.model_id = model_id
        self._cache = _CallCache(cache, metrics)
        self.calls = 0

    def read(self, crop_png: bytes) -> str:
        key = fingerprint(
            provider=self.provider,
            model=self.model_id,
            operation="verify.crop_read",
            params={"crop": digest(crop_png)},
        )
        hit = self._cache.get(key)
        if isinstance(hit, str):
            usage.record(self.provider, self.model_id, "verify.crop_read", cached=True)
            return hit
        text = self._call(crop_png)
        self._cache.put(key, text)
        return text

    def _call(self, crop_png: bytes) -> str:
        try:
            response = self._client.post(
                self._url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                files={"file": ("crop.png", crop_png, "image/png")},
            )
        except httpx.HTTPError as exc:
            raise AiInferenceError(f"lighton crop read failed: {type(exc).__name__}") from exc
        self.calls += 1
        if response.status_code in _UNAVAILABLE:
            raise ModelUnavailableError(f"lighton returned HTTP {response.status_code}")
        if response.status_code >= 400:
            raise AiInferenceError(f"lighton returned HTTP {response.status_code}")
        usage.record(self.provider, self.model_id, "verify.crop_read", pages=1)
        try:
            payload = response.json()
        except ValueError as exc:
            raise AiInferenceError("lighton returned a non-JSON body") from exc
        if not isinstance(payload, dict) or payload.get("status", "completed") != "completed":
            raise AiInferenceError("lighton parse did not complete")
        pages = (payload.get("result") or {}).get("pages") or []
        text = " ".join(
            str(p.get("markdown") or p.get("text") or "") for p in pages if isinstance(p, dict)
        )
        for mark in ("#", "**", "`"):
            text = text.replace(mark, " ")
        return " ".join(text.split())
