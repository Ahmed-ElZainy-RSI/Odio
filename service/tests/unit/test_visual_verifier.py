"""Spec 022: the visual verifier, with fake providers and a real (synthetic) page image."""

from __future__ import annotations

import io

import pytest

from ai_engine.adapters.ai.verifier import VisualVerifier
from ai_engine.domain.errors import AiInferenceError, ModelUnavailableError
from ai_engine.domain.tokens import Token
from ai_engine.domain.verification import contract_violations
from ai_engine.ports.ai import PageVerifier

PIL = pytest.importorskip("PIL")

ORIG, CAND = "الفضل", "الفصل"
BOX = (40.0, 10.0, 20.0, 5.0)
TOKENS = [
    Token(id="p001w000", text="ذهب", box=(70.0, 10.0, 20.0, 5.0)),
    Token(id="p001w001", text=ORIG, box=BOX),
    Token(id="p001w002", text="مسرورا"),  # no box
]
SENTENCE = "ذهب الفضل مسرورا"


def page_image() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (800, 1100), "white").save(buf, format="JPEG")
    return buf.getvalue()


class Counter:
    calls = 0


class FakeReviewer:
    def __init__(self, suspects, context=0.95, validation=0.95) -> None:
        self.suspects, self.context, self.validation = suspects, context, validation
        self.counter = Counter()

    def find_suspects(self, page_text, *, page, threshold):
        self.counter.calls += 1
        self.page, self.threshold = page, threshold
        if isinstance(self.suspects, Exception):
            raise self.suspects
        return self.suspects

    def context_score(self, sentence, original, candidate, visual_reading):
        self.context_args = (sentence, original, candidate, visual_reading)
        if isinstance(self.context, Exception):
            raise self.context
        return self.context

    def validation_score(self, old_sentence, new_sentence, original, candidate):
        self.validation_args = (old_sentence, new_sentence, original, candidate)
        return self.validation


class FakeBlind:
    def __init__(self, reading=CAND, confidence=0.95, unclear=False) -> None:
        self.reply = (reading, confidence, unclear)
        self.seen: list[bytes] = []

    def read(self, crop_png):
        self.seen.append(crop_png)
        return self.reply


class FakeLightOn:
    def __init__(self, *readings: str) -> None:
        self.readings = list(readings) or [CAND]
        self.calls = 0

    def read(self, crop_png):
        assert crop_png.startswith(b"\x89PNG")
        reading = self.readings[min(self.calls, len(self.readings) - 1)]
        self.calls += 1
        return reading


def suspect(word=ORIG, candidate=CAND, score=0.9, sentence=SENTENCE):
    return {
        "word": word,
        "sentence": sentence,
        "score": score,
        "reason": "ض/ص",
        "candidate": candidate,
    }


def verifier(reviewer, blind=None, lighton=None, **kw) -> VisualVerifier:
    lighton = lighton or FakeLightOn()
    return VisualVerifier(
        reviewer=reviewer,
        blind_reader=blind or FakeBlind(),
        crop_reader=lighton,
        call_counters={"gemini": reviewer.counter, "lighton": lighton},
        **kw,
    )


def run(v: VisualVerifier):
    result = v.verify(page_image=page_image(), tokens=TOKENS, page_number=1)
    assert contract_violations(TOKENS, result) == []
    return result


def test_it_implements_the_port() -> None:
    assert isinstance(verifier(FakeReviewer([])), PageVerifier)


def test_a_visually_confirmed_correction_is_applied_with_its_evidence() -> None:
    lighton = FakeLightOn(CAND, CAND)
    result = run(verifier(FakeReviewer([suspect()]), lighton=lighton))
    assert [t.text for t in result.tokens] == ["ذهب", CAND, "مسرورا"]
    (v,) = result.verdicts
    assert v.status == "ACCEPT" and v.token_id == "p001w001"
    ev = v.evidence
    assert ev.applied and ev.detector == "verifier" and ev.rule == "decide()"
    assert ev.reads["lighton"] == CAND and ev.reads["blind"] == CAND
    assert ev.scores["final"] >= 0.88
    assert ev.bbox == BOX
    assert lighton.calls == 2, "two agreeing reads stop the variant loop"
    assert result.calls_made == {"gemini": 1, "lighton": 2}


def test_the_blind_reader_gets_only_the_crop() -> None:
    blind = FakeBlind()
    run(verifier(FakeReviewer([suspect()]), blind=blind))
    (crop,) = blind.seen
    assert crop.startswith(b"\x89PNG") and crop != page_image()


def test_readers_seeing_the_original_keep_the_original() -> None:
    result = run(
        verifier(
            FakeReviewer([suspect()], context=1.0), blind=FakeBlind(ORIG), lighton=FakeLightOn(ORIG)
        )
    )
    assert result.tokens[1].text == ORIG
    assert result.verdicts[0].status == "REVIEW"
    assert not result.verdicts[0].evidence.applied


def test_an_unclear_blind_read_never_changes_letters() -> None:
    result = run(verifier(FakeReviewer([suspect()]), blind=FakeBlind(unclear=True)))
    assert result.tokens[1].text == ORIG
    assert result.verdicts[0].status != "ACCEPT"


@pytest.mark.parametrize(
    ("raw", "status", "rule"),
    [
        (suspect(candidate=None), "REVIEW", "no-candidate"),
        (suspect(candidate="الفصل الأول"), "REVIEW", "not-one-word"),
        (suspect(candidate="الْفَضْلُ"), "REJECT", "diacritics-only"),
        (suspect(word="مسرورا", candidate="مسرورة"), "REVIEW", "not-located"),
    ],
)
def test_suspects_that_cannot_be_judged_visually_are_never_applied(raw, status, rule) -> None:
    lighton = FakeLightOn()
    result = run(verifier(FakeReviewer([raw]), lighton=lighton))
    (v,) = result.verdicts
    assert (v.status, v.evidence.rule) == (status, rule)
    assert [t.text for t in result.tokens] == [t.text for t in TOKENS]
    assert lighton.calls == 0


def test_unresolvable_and_duplicate_suspects_get_no_verdict() -> None:
    result = run(
        verifier(FakeReviewer([suspect(), suspect(score=0.8), suspect(word="ذه", sentence="ذه")]))
    )
    assert [v.token_id for v in result.verdicts] == ["p001w001"]


def test_suspects_are_capped_per_page_strongest_first() -> None:
    raw = [suspect(word="ذهب", candidate="دهب", score=0.8), suspect(score=0.95)]
    result = run(verifier(FakeReviewer(raw), max_suspects=1))
    assert [v.token_id for v in result.verdicts] == ["p001w001"]


def test_a_failed_review_fails_the_page_rather_than_passing_it_clean() -> None:
    with pytest.raises(AiInferenceError):
        verifier(FakeReviewer(AiInferenceError("unparseable"))).verify(
            page_image=page_image(), tokens=TOKENS, page_number=1
        )


def test_an_empty_page_costs_nothing() -> None:
    reviewer = FakeReviewer([suspect()])
    result = verifier(reviewer).verify(page_image=page_image(), tokens=[], page_number=1)
    assert result.verdicts == [] and reviewer.counter.calls == 0


def test_the_reviewer_gets_the_page_and_threshold_and_the_judges_get_the_evidence() -> None:
    reviewer = FakeReviewer([suspect()])
    run(verifier(reviewer, lighton=FakeLightOn(CAND, CAND), suspicion_threshold=0.8))
    assert (reviewer.page, reviewer.threshold) == (1, 0.8)
    assert reviewer.context_args == (SENTENCE, ORIG, CAND, CAND)
    assert reviewer.validation_args == (SENTENCE, "ذهب الفصل مسرورا", ORIG, CAND)


def test_a_word_repeated_in_its_sentence_goes_to_review_without_scoring() -> None:
    """The page has the word once (so it resolves to one token), but the sentence the
    reviewer quoted has it twice: there is no single "after" sentence to validate."""
    reviewer = FakeReviewer([suspect(sentence="الفضل الفضل")])
    result = run(verifier(reviewer))
    assert [(v.status, v.evidence.rule) for v in result.verdicts] == [
        ("REVIEW", "ambiguous-in-sentence")
    ]
    assert not hasattr(reviewer, "context_args")


def test_one_words_scoring_failure_reviews_that_word_and_the_page_goes_on() -> None:
    reviewer = FakeReviewer([suspect()], context=AiInferenceError("judge said nonsense"))
    result = run(verifier(reviewer))
    (v,) = result.verdicts
    assert (v.status, v.evidence.rule) == ("REVIEW", "context-validation-failed")
    assert result.tokens[1].text == ORIG


def test_a_quota_error_while_scoring_still_fails_the_page_for_a_later_retry() -> None:
    reviewer = FakeReviewer([suspect()], context=ModelUnavailableError("429"))
    with pytest.raises(ModelUnavailableError):
        verifier(reviewer).verify(page_image=page_image(), tokens=TOKENS, page_number=1)
