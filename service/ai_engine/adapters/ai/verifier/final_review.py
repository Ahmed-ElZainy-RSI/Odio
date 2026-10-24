"""The final context review (spec 024): one text-only reading of the verified page that
*suggests* residual OCR fixes for a person to check. It never changes a token.

The prompts are the walidapro prototype's, word for word (``prompts.FINAL_REVIEW_*``); the
safety checks are ``domain.final_review_rules``. Cost is bounded at two completions per
page: one attempt plus one retry, and only a temporary failure is retried -- an UNSAFE
reply is final, since asking again only buys a different guess.

The stage is optional, so it never fails the page: an unsafe reply, an outage or an
exhausted retry all mean "no suggestions", logged with the reason.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from ai_engine.domain import final_review_rules as rules
from ai_engine.domain.errors import ModelUnavailableError
from ai_engine.observability.logging import get_logger

from . import prompts
from .providers import GeminiJson, ProviderCallError

log = get_logger(__name__)

MAX_ATTEMPTS = 2  # SC-002: at most two completions per page


@dataclass(frozen=True, slots=True)
class FinalReview:
    #: (token index, proposed text), one-for-one word changes only
    suggestions: Sequence[tuple[int, str]] = ()
    #: why there are no suggestions, when the reviewer's reply could not be used
    rejected: str | None = None
    #: changed words that had no single token to attach to (splits, merges, insertions)
    dropped: int = 0
    stats: rules.ReviewStats | None = field(default=None)


class GeminiFinalReviewer:
    def __init__(
        self,
        gemini: GeminiJson,
        *,
        attempts: int = MAX_ATTEMPTS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._gemini = gemini
        self._attempts = max(1, min(attempts, MAX_ATTEMPTS))
        self._sleep = sleep
        self.model_id = gemini.model_id

    def review(self, token_texts: Sequence[str], *, page_number: int) -> FinalReview:
        original = " ".join(token_texts)
        if not rules.arabic_letter_count(original):
            return FinalReview(rejected="no Arabic text on the page")
        reply = self._ask(original, page_number)
        if isinstance(reply, FinalReview):
            return reply
        corrected = rules.clean_reply(reply)
        try:
            stats = rules.check_reply(original, corrected)
        except rules.UnsafeReview as exc:
            log.warning("final_review.unsafe", page_number=page_number, reason=str(exc))
            return FinalReview(rejected=f"unsafe: {exc}")
        found = rules.suggestions(token_texts, corrected)
        dropped = rules.dropped_changes(token_texts, corrected)
        log.info(
            "final_review.done",
            page_number=page_number,
            suggestions=len(found),
            dropped=dropped,
            changed_ratio=round(stats.changed_ratio, 3),
        )
        return FinalReview(suggestions=found, dropped=dropped, stats=stats)

    def _ask(self, original: str, page_number: int) -> str | FinalReview:
        user = prompts.FINAL_REVIEW_USER.format(text=original)
        last: Exception | None = None
        for attempt in range(1, self._attempts + 1):
            try:
                return self._gemini.ask_text(
                    system=prompts.FINAL_REVIEW_SYSTEM, user=user, operation="verify.final_review"
                )
            except ModelUnavailableError as exc:
                log.warning("final_review.unavailable", page_number=page_number, error=str(exc))
                return FinalReview(rejected=f"unavailable: {exc}")
            except ProviderCallError as exc:
                last = exc
                if not exc.transient:
                    break
                if attempt < self._attempts:
                    self._sleep(min(2**attempt, 6))
        log.warning("final_review.failed", page_number=page_number, error=str(last))
        return FinalReview(rejected=f"failed: {last}")
