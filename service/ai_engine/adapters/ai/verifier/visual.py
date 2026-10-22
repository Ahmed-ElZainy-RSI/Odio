"""The visual verifier: the verified tier's real implementation (spec 022).

Per page:

  1. a text reviewer lists suspect words, each with the sentence it sits in and maybe a
     candidate replacement (``domain.verify_rules.filter_suspects`` keeps only what it can
     back up);
  2. each suspect is pinned to exactly one stable token id, or left alone;
  3. the word is cut out of the page image and re-read twice, independently: LightOn on up
     to five image variants (stopping once two agree), and a blind vision read that is
     never shown the OCR text or the candidate;
  4. context and validation scores for the replacement;
  5. ``domain.verify_rules.decide`` fuses it all into ACCEPT / REVIEW / REJECT, and only an
     ACCEPT changes the token's text.

Every collaborator is injected, so this class is tested with fakes; the paid ones live in
``providers.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any, Protocol

from ai_engine.domain import verify_rules as rules
from ai_engine.domain.errors import AiInferenceError, ModelUnavailableError
from ai_engine.domain.evidence import Evidence
from ai_engine.domain.tokens import Token
from ai_engine.domain.verification import Verdict, VerificationResult
from ai_engine.observability.logging import get_logger

from .crops import word_variants

log = get_logger(__name__)


class Reviewer(Protocol):
    def find_suspects(
        self, page_text: str, *, page: int, threshold: float
    ) -> list[dict[str, Any]]: ...
    def context_score(
        self, sentence: str, original: str, candidate: str, visual_reading: str
    ) -> float: ...
    def validation_score(
        self, old_sentence: str, new_sentence: str, original: str, candidate: str
    ) -> float: ...


class BlindReader(Protocol):
    def read(self, crop_png: bytes) -> tuple[str, float, bool]: ...


class CropReader(Protocol):
    def read(self, crop_png: bytes) -> str: ...


#: (page image, token) -> percent box (x, y, w, h), or None when the word can't be found
Locator = Callable[[bytes, Token], Sequence[float] | None]


def token_box(page_image: bytes, token: Token) -> Sequence[float] | None:
    """The default locator: the box the OCR engine gave the token, if any."""
    return token.box


def _calls(obj: object) -> int:
    return int(getattr(obj, "calls", 0) or 0)


class VisualVerifier:
    """Implements :class:`~ai_engine.ports.ai.PageVerifier`."""

    name = "visual"

    def __init__(
        self,
        *,
        reviewer: Reviewer,
        blind_reader: BlindReader,
        crop_reader: CropReader,
        locator: Locator = token_box,
        crop: Callable[
            [bytes, Sequence[float]], tuple[dict[str, bytes], tuple[int, int, int, int]]
        ] = word_variants,
        suspicion_threshold: float = 0.75,
        accept_threshold: float = 0.88,
        review_threshold: float = 0.70,
        max_suspects: int = 20,
        models: dict[str, str] | None = None,
        call_counters: dict[str, object] | None = None,
    ) -> None:
        self._reviewer = reviewer
        self._blind = blind_reader
        self._crop_reader = crop_reader
        self._locate = locator
        self._crop = crop
        self._suspicion = suspicion_threshold
        self._accept = accept_threshold
        self._review = review_threshold
        self._max_suspects = max_suspects
        self._models = dict(models or {})
        # provider -> the object whose `.calls` counts that provider's paid calls
        self._counters = dict(call_counters or {})

    def verify(
        self, *, page_image: bytes, tokens: Sequence[Token], page_number: int
    ) -> VerificationResult:
        tokens = list(tokens)
        before = {p: _calls(c) for p, c in self._counters.items()}
        page_text = " ".join(t.text for t in tokens)
        verdicts: list[Verdict] = []
        if page_text:
            suspects = rules.filter_suspects(
                self._reviewer.find_suspects(
                    page_text, page=page_number, threshold=self._suspicion
                ),
                page_text,
                threshold=self._suspicion,
            )
            suspects.sort(key=lambda s: s.score, reverse=True)
            texts = [t.text for t in tokens]
            seen: set[int] = set()
            for suspect in suspects[: self._max_suspects]:
                index = rules.resolve_token(suspect.word, suspect.sentence, texts)
                if index is None or index in seen:
                    continue  # ambiguous or already judged: never guess which token it meant
                seen.add(index)
                evidence = self._judge(page_image, tokens[index], suspect)
                verdicts.append(Verdict(evidence))
                if evidence.applied:
                    tokens[index] = replace(tokens[index], text=evidence.candidate)
        calls = {p: _calls(c) - before[p] for p, c in self._counters.items()}
        log.info(
            "verify.page",
            page_number=page_number,
            suspects=len(verdicts),
            accepted=sum(v.status == "ACCEPT" for v in verdicts),
            review=sum(v.status == "REVIEW" for v in verdicts),
            calls=calls,
        )
        return VerificationResult(tokens=tokens, verdicts=verdicts, calls_made=calls)

    # ── one suspect ──────────────────────────────────────────────────────────

    def _evidence(
        self, token: Token, candidate: str, status: str, rule: str, **extra: Any
    ) -> Evidence:
        return Evidence(
            token_id=token.id,
            original=token.text,
            candidate=candidate,
            applied=status == rules.ACCEPT,
            status=status,  # type: ignore[arg-type]
            detector="verifier",
            decided_by="verifier",
            rule=rule,
            models=self._models,
            **extra,
        )

    def _judge(self, page_image: bytes, token: Token, suspect: rules.Suspect) -> Evidence:
        original, candidate = token.text, suspect.candidate
        if candidate is None:
            return self._evidence(token, original, rules.REVIEW, "no-candidate")
        if not candidate or any(c.isspace() for c in candidate):
            # a token is one word; a split or merge is not a letter correction
            return self._evidence(
                token, candidate.strip() or original, rules.REVIEW, "not-one-word"
            )
        if rules.same_letters(original, candidate):
            # marks only: that is the diacritiser's job, not a letter correction
            return self._evidence(token, candidate, rules.REJECT, "diacritics-only")

        box = self._locate(page_image, token)
        if not box:
            return self._evidence(token, candidate, rules.REVIEW, "not-located")
        try:
            variants, _ = self._crop(page_image, box)
        except ValueError:
            return self._evidence(token, candidate, rules.REVIEW, "not-located", bbox=tuple(box))

        readings: dict[str, str] = {}
        for name in rules.VARIANT_ORDER:
            if name not in variants:
                continue
            readings[name] = self._crop_reader.read(variants[name])
            if rules.enough_votes(list(readings.values())):
                break
        lighton_word, lighton_score, agreeing = rules.visual_consensus(
            readings, candidate=candidate, original=original
        )
        blind = rules.gate_blind_read(
            *self._blind.read(variants.get("original") or next(iter(variants.values()))),
            original=original,
            candidate=candidate,
        )
        reads = {"lighton": lighton_word, "blind": blind.reading, "variants": agreeing}
        new_sentence = rules.replace_in_sentence(suspect.sentence, suspect.word, candidate)
        if new_sentence is None:
            # the validator compares the sentence before and after; with the word absent or
            # repeated in its sentence there is no single "after" to show it
            return self._evidence(
                token,
                candidate,
                rules.REVIEW,
                "ambiguous-in-sentence",
                reads=reads,
                bbox=tuple(box),
            )
        try:
            context = self._reviewer.context_score(
                suspect.sentence, original, candidate, lighton_word
            )
            validation = self._reviewer.validation_score(
                suspect.sentence, new_sentence, original, candidate
            )
        except ModelUnavailableError:
            raise  # quota or outage: retry the page later, don't judge it on missing evidence
        except AiInferenceError as exc:
            # one word's scoring failed: that word goes to a person, the page goes on
            log.warning("verify.scoring_failed", token_id=token.id, error=str(exc))
            return self._evidence(
                token,
                candidate,
                rules.REVIEW,
                "context-validation-failed",
                reads=reads,
                bbox=tuple(box),
            )
        final, status = rules.decide(
            original=original,
            candidate=candidate,
            context_score=context,
            validation_score=validation,
            lighton_reading=lighton_word,
            lighton_score=lighton_score,
            blind=blind,
            accept_threshold=self._accept,
            review_threshold=self._review,
        )
        return self._evidence(
            token,
            candidate,
            status,
            "decide()",
            reads=reads,
            scores={
                "suspicion": suspect.score,
                "context": round(float(context), 4),
                "validation": round(float(validation), 4),
                "lighton": round(lighton_score, 4),
                "blind_confidence": round(blind.confidence, 4),
                "final": round(final, 4),
            },
            bbox=tuple(box),
        )
