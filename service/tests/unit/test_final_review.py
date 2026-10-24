"""Spec 024: the final context review adapter, and that it never changes a token."""

from __future__ import annotations

import json

import httpx
import pytest

from ai_engine.adapters.ai.verifier import prompts
from ai_engine.adapters.ai.verifier.final_review import FinalReview, GeminiFinalReviewer
from ai_engine.adapters.ai.verifier.providers import GeminiJson
from ai_engine.domain.tokens import Token
from ai_engine.domain.verification import contract_violations
from tests.unit.test_visual_verifier import (
    TOKENS,
    FakeBlind,
    FakeLightOn,
    FakeReviewer,
    page_image,
    suspect,
    verifier,
)

WORDS = [
    "ذهب",
    "الولد",
    "إلى",
    "السوق",
    "في",
    "الصباح",
    "الباكر",
    "واشترى",
    "خبزا",
    "وجبنا",
    "وعاد",
    "إلى",
    "البيت",
    "فوجد",
    "أمه",
    "تنتظره",
    "عند",
    "الباب",
    "وهي",
    "مسرورة",
]


def text_reply(text: str) -> httpx.Response:
    return httpx.Response(
        200, json={"candidates": [{"content": {"parts": [{"text": text}]}}], "usageMetadata": {}}
    )


def reviewer_with(*responses: httpx.Response):
    seen: list[httpx.Request] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return queue.pop(0)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    gemini = GeminiJson(client=client, api_key="k", model_id="gemini-pro-test")
    return GeminiFinalReviewer(gemini, sleep=lambda _: None), seen


def fixed(old: str, new: str) -> str:
    return " ".join(new if w == old else w for w in WORDS)


def test_a_safe_fix_becomes_a_suggestion_with_the_prototype_prompts() -> None:
    reviewer, seen = reviewer_with(text_reply("النص المصحح: " + fixed("السوق", "السوك")))
    review = reviewer.review(WORDS, page_number=4)
    assert review.suggestions == [(3, "السوك")] and review.rejected is None
    body = json.loads(seen[0].content)
    assert body["systemInstruction"]["parts"][0]["text"] == prompts.FINAL_REVIEW_SYSTEM
    assert body["contents"][0]["parts"][0]["text"] == prompts.FINAL_REVIEW_USER.format(
        text=" ".join(WORDS)
    )
    assert "responseMimeType" not in body["generationConfig"], "plain text, not JSON"


def test_an_unsafe_reply_is_not_retried_and_yields_nothing() -> None:
    rewrite = " ".join(["كلام"] * 20)
    reviewer, seen = reviewer_with(text_reply(rewrite), text_reply(fixed("السوق", "السوك")))
    review = reviewer.review(WORDS, page_number=1)
    assert not review.suggestions and review.rejected.startswith("unsafe")
    assert len(seen) == 1


def test_at_most_two_completions_per_page() -> None:
    reviewer, seen = reviewer_with(*[httpx.Response(500)] * 5)
    review = reviewer.review(WORDS, page_number=1)
    assert review.rejected.startswith("failed") and len(seen) == 2

    reviewer, seen = reviewer_with(text_reply("  "), text_reply(fixed("خبزا", "خبزًا")))
    assert reviewer.review(WORDS, page_number=1).rejected is None
    assert len(seen) == 2, "an empty reply is a temporary failure: one retry"


def test_a_permanent_error_or_quota_skips_the_stage_without_failing_the_page() -> None:
    reviewer, seen = reviewer_with(httpx.Response(400))
    assert reviewer.review(WORDS, page_number=1).rejected.startswith("failed")
    assert len(seen) == 1
    reviewer, _ = reviewer_with(httpx.Response(429))
    assert reviewer.review(WORDS, page_number=1).rejected.startswith("unavailable")


def test_a_page_without_arabic_costs_nothing() -> None:
    reviewer, seen = reviewer_with()
    assert reviewer.review(["123", "456"], page_number=1).rejected
    assert seen == []


# ── inside the verifier ─────────────────────────────────────────────────────


class FakeFinal:
    model_id = "pro-test"

    def __init__(self, review: FinalReview) -> None:
        self.result = review
        self.seen: list[list[str]] = []

    def review(self, token_texts, *, page_number):
        self.seen.append(list(token_texts))
        return self.result


def test_suggestions_are_review_verdicts_and_never_change_a_token() -> None:
    final = FakeFinal(FinalReview(suggestions=[(0, "دهب"), (2, "مسرورة")]))
    v = verifier(FakeReviewer([]), final_reviewer=final)
    result = v.verify(page_image=page_image(), tokens=TOKENS, page_number=1)
    assert contract_violations(TOKENS, result) == []
    assert [t.text for t in result.tokens] == [t.text for t in TOKENS], "SC-001: 0 changes"
    assert [(v.token_id, v.status, v.evidence.rule) for v in result.verdicts] == [
        ("p001w000", "REVIEW", "final-review:suggest"),
        ("p001w002", "REVIEW", "final-review:suggest"),
    ]
    ev = result.verdicts[0].evidence
    assert not ev.applied and ev.detector == "reviewer" and ev.reads == {"llm": "دهب"}


def test_the_final_review_reads_the_verified_page_and_skips_judged_tokens() -> None:
    final = FakeFinal(FinalReview(suggestions=[(1, "الفصول")]))
    v = verifier(
        FakeReviewer([suspect()]),
        blind=FakeBlind(),
        lighton=FakeLightOn("الفصل", "الفصل"),
        final_reviewer=final,
    )
    result = v.verify(page_image=page_image(), tokens=TOKENS, page_number=1)
    assert final.seen == [["ذهب", "الفصل", "مسرورا"]], "after the visual correction"
    assert [v.evidence.rule for v in result.verdicts] == ["decide()"]


def test_off_means_no_final_review_call() -> None:
    result = verifier(FakeReviewer([])).verify(
        page_image=page_image(), tokens=[Token(id="p001w000", text="ذهب")], page_number=1
    )
    assert result.verdicts == []


@pytest.mark.parametrize("mode", ["apply", "on"])
def test_there_is_no_apply_mode(mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import ValidationError

    from ai_engine.config.settings import Settings

    monkeypatch.setenv("FINAL_REVIEW", mode)
    with pytest.raises(ValidationError):
        Settings()
