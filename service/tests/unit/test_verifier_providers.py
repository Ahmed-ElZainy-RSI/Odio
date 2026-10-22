"""Spec 022: the visual verifier's paid calls, against a mock transport."""

from __future__ import annotations

import json

import httpx
import pytest

from ai_engine.adapters.ai.verifier import prompts
from ai_engine.adapters.ai.verifier.providers import (
    BLIND_MAX_TOKENS,
    REVIEW_MAX_TOKENS,
    SCORE_MAX_TOKENS,
    GeminiBlindReader,
    GeminiJson,
    GeminiReviewer,
    LightOnCropReader,
    parse_json_object,
    to_bool,
)
from ai_engine.domain.errors import AiInferenceError, ModelUnavailableError
from ai_engine.observability import usage

PNG = b"\x89PNG-not-really"


def gemini_reply(obj, status: int = 200) -> httpx.Response:
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return httpx.Response(
        status,
        json={
            "candidates": [{"content": {"parts": [{"text": text}]}}],
            "usageMetadata": {
                "promptTokenCount": 100,
                "candidatesTokenCount": 20,
                "thoughtsTokenCount": 5,
            },
        },
    )


def gemini(*responses: httpx.Response, seen: list | None = None) -> GeminiJson:
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return queue.pop(0)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return GeminiJson(client=client, api_key="k", model_id="gemini-test")


@pytest.mark.parametrize(
    "text",
    ['{"a": 1}', '```json\n{"a": 1}\n```', 'Here: {"a": 1} done', '{"a": 1,}'],
)
def test_json_replies_are_repaired_where_safe(text: str) -> None:
    assert parse_json_object(text) == {"a": 1}


@pytest.mark.parametrize("text", ["", "no json", "[1, 2]", "{broken"])
def test_unrepairable_replies_are_errors(text: str) -> None:
    with pytest.raises(AiInferenceError):
        parse_json_object(text)


def test_a_call_is_authenticated_metered_and_recorded() -> None:
    seen: list[httpx.Request] = []
    g = gemini(gemini_reply({"ok": True}), seen=seen)
    with usage.collecting(page=3, stage="verify") as records:
        assert g.ask(system="s", user="u", image_png=PNG, operation="verify.blind_read") == {
            "ok": True
        }
    (request,) = seen
    assert request.headers["x-goog-api-key"] == "k"
    assert request.url.path.endswith("/models/gemini-test:generateContent")
    body = json.loads(request.content)
    assert body["contents"][0]["parts"][0]["inline_data"]["mime_type"] == "image/png"
    assert g.calls == 1
    (r,) = records
    assert (r.provider, r.operation, r.input_tokens, r.output_tokens, r.page) == (
        "gemini",
        "verify.blind_read",
        100,
        25,
        3,
    )


@pytest.mark.parametrize(
    ("status", "error"),
    [(429, ModelUnavailableError), (503, ModelUnavailableError), (400, AiInferenceError)],
)
def test_http_failures_are_typed(status: int, error: type) -> None:
    with pytest.raises(error):
        gemini(httpx.Response(status)).ask(system="s", user="u", operation="x")


def reviewer_of(*responses: httpx.Response, seen: list | None = None):
    waits: list[float] = []
    return GeminiReviewer(gemini(*responses, seen=seen), sleep=waits.append), waits


SUSPECT = {"word": "الفضل", "sentence": "s", "score": 0.9, "candidate": "الفصل"}


def test_the_reviewer_sends_the_prototype_prompts_with_page_threshold_and_a_token_ceiling() -> None:
    seen: list[httpx.Request] = []
    reviewer, _ = reviewer_of(
        gemini_reply({"total_words": 1, "items": [SUSPECT, "junk"]}), seen=seen
    )
    assert reviewer.find_suspects("نص الصفحة", page=7, threshold=0.75) == [SUSPECT]
    body = json.loads(seen[0].content)
    assert body["systemInstruction"]["parts"][0]["text"] == prompts.REVIEW_SYSTEM
    user = body["contents"][0]["parts"][-1]["text"]
    assert "نص الصفحة" in user and "7" in user and "0.75" in user
    assert body["generationConfig"]["maxOutputTokens"] == REVIEW_MAX_TOKENS


def test_the_reviewer_retries_temporary_failures_with_a_short_capped_backoff() -> None:
    ok = gemini_reply({"items": [SUSPECT]})
    reviewer, waits = reviewer_of(gemini_reply("nonsense"), httpx.Response(500), ok)
    assert reviewer.find_suspects("نص") == [SUSPECT]
    assert len(waits) == 2 and all(0 < w <= 5.0 for w in waits)

    reviewer, waits = reviewer_of(*[gemini_reply("nonsense")] * 3)
    with pytest.raises(AiInferenceError, match="3 attempts"):
        reviewer.find_suspects("نص")
    assert len(waits) == 2, "no wait after the last attempt"


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_a_permanent_client_error_is_not_retried(status: int) -> None:
    seen: list[httpx.Request] = []
    reviewer, waits = reviewer_of(httpx.Response(status), seen=seen)
    with pytest.raises(AiInferenceError):
        reviewer.find_suspects("نص")
    assert len(seen) == 1 and waits == []


def test_the_reviewer_does_not_retry_a_quota_error() -> None:
    reviewer, waits = reviewer_of(httpx.Response(429))
    with pytest.raises(ModelUnavailableError):
        reviewer.find_suspects("نص")
    assert waits == []


def test_attempts_are_capped_at_three_whatever_is_configured() -> None:
    seen: list[httpx.Request] = []
    reviewer = GeminiReviewer(
        gemini(*[httpx.Response(500)] * 10, seen=seen), attempts=10, sleep=lambda _: None
    )
    with pytest.raises(AiInferenceError):
        reviewer.find_suspects("نص")
    assert len(seen) == 3


def test_a_reply_without_the_items_list_is_a_failure_not_zero_suspects() -> None:
    reviewer, _ = reviewer_of(gemini_reply({"total_words": 0}))
    with pytest.raises(AiInferenceError, match="items"):
        reviewer.find_suspects("نص")
    reviewer, _ = reviewer_of(gemini_reply({"total_words": 0, "items": []}))
    assert reviewer.find_suspects("نص") == []


def test_the_context_judge_sees_the_visual_reading() -> None:
    seen: list[httpx.Request] = []
    reviewer, _ = reviewer_of(gemini_reply({"score": 0.8}), seen=seen)
    assert reviewer.context_score("الجملة هنا", "الفضل", "الفصل", "قراءة_لايتون") == 0.8
    body = json.loads(seen[0].content)
    assert body["systemInstruction"]["parts"][0]["text"] == prompts.CONTEXT_SYSTEM
    assert "قراءة_لايتون" in body["contents"][0]["parts"][-1]["text"]
    assert body["generationConfig"]["maxOutputTokens"] == SCORE_MAX_TOKENS


def test_the_validator_compares_the_sentence_before_and_after() -> None:
    seen: list[httpx.Request] = []
    reviewer, _ = reviewer_of(gemini_reply({"score": "0.6"}), seen=seen)
    assert reviewer.validation_score("ذهب الفضل", "ذهب الفصل", "الفضل", "الفصل") == 0.6
    body = json.loads(seen[0].content)
    assert body["systemInstruction"]["parts"][0]["text"] == prompts.VALIDATE_SYSTEM
    user = body["contents"][0]["parts"][-1]["text"]
    assert "ذهب الفضل" in user and "ذهب الفصل" in user


@pytest.mark.parametrize(("raw", "score"), [("high", 0.0), (None, 0.0), (7, 1.0), (-1, 0.0)])
def test_a_non_numeric_or_out_of_range_score_is_clamped_not_a_failure(raw, score) -> None:
    reviewer, _ = reviewer_of(gemini_reply({"score": raw}))
    assert reviewer.context_score("s", "a", "b", "v") == score


def blind_png() -> bytes:
    pytest.importorskip("PIL")
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (120, 40), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_the_blind_reader_sends_only_the_crop_with_the_prototype_prompts() -> None:
    seen: list[httpx.Request] = []
    reader = GeminiBlindReader(
        gemini(gemini_reply({"reading": " الفصل ", "confidence": 0.9, "unclear": False}), seen=seen)
    )
    assert reader.read(blind_png()) == ("الفصل", 0.9, False)
    body = json.loads(seen[0].content)
    assert body["systemInstruction"]["parts"][0]["text"] == prompts.BLIND_SYSTEM
    assert body["contents"][0]["parts"][-1]["text"] == prompts.BLIND_USER
    assert body["generationConfig"]["maxOutputTokens"] == BLIND_MAX_TOKENS
    assert "الفضل" not in json.dumps(body, ensure_ascii=False)


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ({"reading": "الفصل", "confidence": 0.9}, ("الفصل", 0.9, True)),  # no `unclear`: unclear
        ({"reading": "الفصل", "confidence": 0.9, "unclear": "false"}, ("الفصل", 0.9, False)),
        ({"reading": "الفصل", "confidence": 0.9, "unclear": "yes"}, ("الفصل", 0.9, True)),
        ({"reading": "الفصل", "confidence": "high", "unclear": False}, ("الفصل", 0.0, False)),
        ({"reading": "الفصل", "confidence": 3, "unclear": False}, ("الفصل", 1.0, False)),
        ("garbage", ("", 0.0, True)),
    ],
)
def test_the_blind_readers_output_is_read_strictly(reply, expected) -> None:
    assert GeminiBlindReader(gemini(gemini_reply(reply))).read(blind_png()) == expected


def test_a_failed_blind_read_is_unclear_after_one_attempt() -> None:
    seen: list[httpx.Request] = []
    reader = GeminiBlindReader(gemini(httpx.Response(500), seen=seen))
    assert reader.read(blind_png()) == ("", 0.0, True)
    assert len(seen) == 1


@pytest.mark.parametrize(
    ("value", "default", "expected"),
    [
        (None, True, True),
        (None, False, False),
        ("TRUE", False, True),
        ("no", True, False),
        (1, False, True),
    ],
)
def test_to_bool(value, default, expected) -> None:
    assert to_bool(value, default=default) is expected


def lighton(response: httpx.Response) -> LightOnCropReader:
    client = httpx.Client(transport=httpx.MockTransport(lambda r: response))
    return LightOnCropReader(client=client, api_key="k", base_url="https://lighton.test")


def test_lighton_returns_the_cleaned_markdown() -> None:
    reader = lighton(
        httpx.Response(
            200, json={"status": "completed", "result": {"pages": [{"markdown": "## **الفصل**"}]}}
        )
    )
    assert reader.read(PNG) == "الفصل"
    assert reader.calls == 1


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(429), ModelUnavailableError),
        (httpx.Response(500), AiInferenceError),
        (httpx.Response(200, json={"status": "failed"}), AiInferenceError),
        (httpx.Response(200, text="not json"), AiInferenceError),
    ],
)
def test_lighton_failures_are_typed(response: httpx.Response, error: type) -> None:
    with pytest.raises(error):
        lighton(response).read(PNG)


# ── the paid-call cache (spec 021) ──────────────────────────────────────────


class DictCache:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    def get(self, key: str) -> bytes | None:
        return self.data.get(key)

    def put(self, key: str, value: bytes) -> None:
        self.data[key] = value


def counting_client(*responses: httpx.Response) -> tuple[httpx.Client, list]:
    seen: list[httpx.Request] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return queue.pop(0)

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_a_repeated_gemini_call_is_served_from_the_cache_and_not_billed() -> None:
    cache = DictCache()
    client, seen = counting_client(gemini_reply({"score": 0.8}))
    g = GeminiJson(client=client, api_key="k", model_id="m", cache=cache)
    with usage.collecting(page=1, stage="verify") as records:
        first = g.ask(system="s", user="u", image_png=PNG, operation="verify.context")
        second = g.ask(system="s", user="u", image_png=PNG, operation="verify.context")
    assert first == second == {"score": 0.8}
    assert len(seen) == 1 and g.calls == 1
    assert [r.cached for r in records] == [False, True]


def test_any_input_change_misses() -> None:
    cache = DictCache()
    client, seen = counting_client(*[gemini_reply({"score": 0.5})] * 4)
    g = GeminiJson(client=client, api_key="k", model_id="m", cache=cache)
    g.ask(system="s", user="u", image_png=PNG, operation="verify.context")
    g.ask(system="s", user="u2", image_png=PNG, operation="verify.context")
    g.ask(system="s", user="u", image_png=PNG + b"x", operation="verify.context")
    g.ask(system="s", user="u", image_png=PNG, operation="verify.validate")
    assert len(seen) == 4


def test_a_failed_or_unparseable_reply_is_never_cached() -> None:
    cache = DictCache()
    client, _ = counting_client(httpx.Response(500), gemini_reply("nonsense"))
    g = GeminiJson(client=client, api_key="k", model_id="m", cache=cache)
    for _ in range(2):
        with pytest.raises(AiInferenceError):
            g.ask(system="s", user="u", operation="verify.review")
    assert cache.data == {}


def test_an_unreadable_entry_is_a_miss() -> None:
    cache = DictCache()
    client, seen = counting_client(gemini_reply({"ok": 1}), gemini_reply({"ok": 2}))
    g = GeminiJson(client=client, api_key="k", model_id="m", cache=cache)
    g.ask(system="s", user="u", operation="x")
    for key in cache.data:
        cache.data[key] = b"\xff garbage"
    assert g.ask(system="s", user="u", operation="x") == {"ok": 2}
    assert len(seen) == 2


def test_lighton_crop_reads_are_cached_including_an_empty_reading() -> None:
    cache = DictCache()
    empty = httpx.Response(200, json={"status": "completed", "result": {"pages": []}})
    client, seen = counting_client(empty)
    reader = LightOnCropReader(client=client, api_key="k", base_url="https://l.test", cache=cache)
    assert reader.read(PNG) == "" == reader.read(PNG)
    assert len(seen) == 1 and reader.calls == 1
