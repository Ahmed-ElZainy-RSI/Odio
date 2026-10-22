"""The paid calls the visual verifier makes (spec 022): a text reviewer, a blind crop
reader, and a crop re-reader. Each is a small class over an injected sync ``httpx.Client``
so the verifier can be tested with fakes and these can be tested with a mock transport.

The prompts and the handling of the model's Arabic output follow the walidapro prototype
(``prompts.py``); the failure rules, which differ on purpose by caller:

  - **the reviewer** gets up to 3 attempts, but only a *temporary* failure (network, 408,
    5xx, an empty or unparseable reply) is retried, after a short capped backoff; a permanent
    4xx is not, because another paid attempt cannot fix it. A failed review is a failed
    page, never "no suspects": that would silently pass every OCR error through.
  - **the blind reader** gets one attempt; a bad answer is UNCLEAR, which is zero evidence
    -- and an unclear read can never accept a letter change, so failing safe is free.
  - **quota / outage** (HTTP 429, 503) is :class:`ModelUnavailableError` everywhere, so the
    page is retried later by the job's own retry policy instead of being judged on missing
    evidence.
  - every call has an output-token ceiling, so one reply can't run up the bill.
"""

from __future__ import annotations

import base64
import json
import random
import re
import time
from collections.abc import Callable
from typing import Any

import httpx

from ai_engine.domain.cache_key import digest, fingerprint
from ai_engine.domain.errors import AiInferenceError, ModelUnavailableError
from ai_engine.observability import usage
from ai_engine.observability.logging import get_logger
from ai_engine.observability.metrics import PROVIDER_CACHE, Metrics
from ai_engine.ports.cache import ProviderCache

from . import prompts
from .crops import upscale_for_reading

log = get_logger(__name__)

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com"
_UNAVAILABLE = frozenset({429, 503})
#: output-token ceilings per call, the prototype's values
REVIEW_MAX_TOKENS = 6000
SCORE_MAX_TOKENS = 1000
BLIND_MAX_TOKENS = 120


class ProviderCallError(AiInferenceError):
    """A verifier call failed. ``transient`` says whether another attempt could succeed."""

    def __init__(self, message: str, *, transient: bool) -> None:
        super().__init__(message)
        self.transient = transient


def to_bool(value: Any, *, default: bool) -> bool:
    """A model's boolean, read strictly: ``"false"`` is False, and a missing value is
    ``default`` -- for ``unclear`` that is True, so an omitted field is never evidence."""
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}
    return bool(value)


def to_score(value: Any) -> float:
    """A model's score clamped to 0..1; anything non-numeric is 0.0 (no support)."""
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


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
        self,
        *,
        system: str,
        user: str,
        image_png: bytes | None = None,
        operation: str,
        max_output_tokens: int | None = None,
    ) -> dict[str, Any]:
        key = fingerprint(
            provider=self.provider,
            model=self.model_id,
            operation=operation,
            params={
                "system": digest(system),
                "user": digest(user),
                "image": digest(image_png) if image_png is not None else None,
                "max_output_tokens": max_output_tokens,
            },
        )
        hit = self._cache.get(key)
        if isinstance(hit, dict):
            usage.record(self.provider, self.model_id, operation, cached=True)
            return hit
        reply = self._call(
            system=system,
            user=user,
            image_png=image_png,
            operation=operation,
            max_output_tokens=max_output_tokens,
        )
        self._cache.put(key, reply)
        return reply

    def _call(
        self,
        *,
        system: str,
        user: str,
        image_png: bytes | None,
        operation: str,
        max_output_tokens: int | None,
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
        config: dict[str, Any] = {"temperature": 0.0, "responseMimeType": "application/json"}
        if max_output_tokens is not None:
            config["maxOutputTokens"] = max_output_tokens
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": config,
        }
        try:
            response = self._client.post(
                self._url, headers={"x-goog-api-key": self._api_key}, json=body
            )
        except httpx.HTTPError as exc:
            raise ProviderCallError(
                f"verifier call {operation} failed: {type(exc).__name__}", transient=True
            ) from exc
        self.calls += 1
        status = response.status_code
        if status in _UNAVAILABLE:
            raise ModelUnavailableError(f"gemini returned HTTP {status} for {operation}")
        if status >= 400:
            # a timeout or a server fault may pass; a bad request or bad key will not
            raise ProviderCallError(
                f"gemini returned HTTP {status} for {operation}",
                transient=status == 408 or status >= 500,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderCallError(
                f"gemini returned a non-JSON body for {operation}", transient=True
            ) from exc
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
            raise ProviderCallError(
                f"gemini returned no content for {operation}", transient=True
            ) from exc
        try:
            return parse_json_object("".join(texts))
        except AiInferenceError as exc:
            raise ProviderCallError(f"{operation}: {exc}", transient=True) from exc


# ── the text reviewer ───────────────────────────────────────────────────────────


def _backoff(attempt: int) -> float:
    """Short and capped: a page should not wait 16-32 s on one reviewer call."""
    return min(1.5 * 2 ** (attempt - 1) + random.uniform(0.2, 0.8), 5.0)


class GeminiReviewer:
    """Finds suspect words in a page's text and scores a proposed replacement."""

    def __init__(
        self,
        gemini: GeminiJson,
        *,
        attempts: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._gemini = gemini
        self._attempts = max(1, min(attempts, 3))  # cost cap: never more than 3
        self._sleep = sleep

    def _ask(self, system: str, user: str, operation: str, max_tokens: int) -> dict[str, Any]:
        last: ProviderCallError | None = None
        for attempt in range(1, self._attempts + 1):
            try:
                return self._gemini.ask(
                    system=system, user=user, operation=operation, max_output_tokens=max_tokens
                )
            except ProviderCallError as exc:
                last = exc
                if not exc.transient:
                    raise
                if attempt < self._attempts:
                    wait = _backoff(attempt)
                    log.warning(
                        "verify.retry", operation=operation, attempt=attempt, wait_s=round(wait, 1)
                    )
                    self._sleep(wait)
        raise AiInferenceError(
            f"{operation} failed after {self._attempts} attempts: {last}"
        ) from last

    def find_suspects(
        self, page_text: str, *, page: int = 0, threshold: float = 0.75
    ) -> list[dict[str, Any]]:
        user = prompts.REVIEW_USER.format(page=page, threshold=threshold, text=page_text)
        reply = self._ask(prompts.REVIEW_SYSTEM, user, "verify.review", REVIEW_MAX_TOKENS)
        items = reply.get("items")
        if not isinstance(items, list):
            # a reply without the list is a failed review, not an empty one
            raise AiInferenceError("verify.review reply has no items list")
        return [item for item in items if isinstance(item, dict)]

    def context_score(
        self, sentence: str, original: str, candidate: str, visual_reading: str
    ) -> float:
        """How likely ``candidate`` is the printed word, given the sentence and what LightOn
        read in the crop."""
        user = prompts.CONTEXT_USER.format(
            sentence=sentence, original=original, candidate=candidate, visual_reading=visual_reading
        )
        reply = self._ask(prompts.CONTEXT_SYSTEM, user, "verify.context", SCORE_MAX_TOKENS)
        return to_score(reply.get("score"))

    def validation_score(
        self, old_sentence: str, new_sentence: str, original: str, candidate: str
    ) -> float:
        """The last, deliberately conservative check: the sentence before and after."""
        user = prompts.VALIDATE_USER.format(
            old_sentence=old_sentence,
            new_sentence=new_sentence,
            original=original,
            candidate=candidate,
        )
        reply = self._ask(prompts.VALIDATE_SYSTEM, user, "verify.validate", SCORE_MAX_TOKENS)
        return to_score(reply.get("score"))


# ── the blind reader ────────────────────────────────────────────────────────────


class GeminiBlindReader:
    """Reads the crop -- and only the crop: it is never told the OCR text, the candidate or
    the sentence, so its reading is independent evidence rather than an echo."""

    def __init__(self, gemini: GeminiJson) -> None:
        self._gemini = gemini

    def read(self, crop_png: bytes) -> tuple[str, float, bool]:
        try:
            reply = self._gemini.ask(
                system=prompts.BLIND_SYSTEM,
                user=prompts.BLIND_USER,
                image_png=upscale_for_reading(crop_png),
                operation="verify.blind_read",
                max_output_tokens=BLIND_MAX_TOKENS,
            )
        except AiInferenceError:
            return "", 0.0, True  # one attempt; no reading is UNCLEAR, never a guess
        return (
            str(reply.get("reading") or "").strip(),
            to_score(reply.get("confidence")),
            to_bool(reply.get("unclear"), default=True),
        )


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
        for mark in ("#", "**", "__", "`"):
            text = text.replace(mark, " ")
        return " ".join(text.split())
