"""Spec 022: the verified tier's decision rules."""

from __future__ import annotations

import pytest

from ai_engine.domain.verify_rules import (
    ACCEPT,
    REJECT,
    REVIEW,
    BlindRead,
    LocatedWord,
    best_box,
    decide,
    enough_votes,
    filter_suspects,
    gate_blind_read,
    normalize,
    replace_in_sentence,
    resolve_token,
    same_letters,
    similarity,
    visual_consensus,
)

ORIG, CAND = "الفضل", "الفصل"


def clear(reading: str = CAND, confidence: float = 0.95) -> BlindRead:
    return gate_blind_read(reading, confidence, False, original=ORIG, candidate=CAND)


UNCLEAR = BlindRead("", 0.0, 0.0, 0.0, True)


# ── normalisation ───────────────────────────────────────────────────────────


def test_normalize_folds_marks_tatweel_and_letter_forms() -> None:
    assert normalize("أَحْمَـــدُ") == "احمد"
    assert normalize("إلى") == "الي"
    assert same_letters("الْفَصْلِ", "الفصل")
    assert not same_letters(ORIG, CAND)


def test_similarity_is_one_for_the_same_letters_and_zero_for_nothing() -> None:
    assert similarity("الْفَصْلِ", "الفصل") == 1.0
    assert similarity("", CAND) == 0.0
    assert 0.7 < similarity(ORIG, CAND) < 1.0


# ── the blind reader's gates ────────────────────────────────────────────────


def test_a_clear_confident_single_word_read_is_evidence() -> None:
    blind = clear()
    assert not blind.unclear
    assert blind.supports_candidate == 1.0
    assert blind.supports_original < 1.0


@pytest.mark.parametrize(
    ("reading", "confidence", "unclear"),
    [
        (CAND, 0.71, False),  # below the confidence gate
        (CAND, 0.95, True),  # the reader said so (confidence capped at 0.49)
        ("الفصل الأول", 0.95, False),  # not one word
        ("abc", 0.95, False),  # not Arabic
        ("", 0.95, False),  # nothing
        ("الفضصل", 0.95, False),  # between the two: no side favoured by 0.12
    ],
)
def test_anything_short_of_clear_is_zero_evidence(
    reading: str, confidence: float, unclear: bool
) -> None:
    blind = gate_blind_read(reading, confidence, unclear, original=ORIG, candidate=CAND)
    assert blind.unclear
    assert blind.supports_original == blind.supports_candidate == 0.0


# ── LightOn re-reads ────────────────────────────────────────────────────────


def test_early_stop_needs_two_reads_and_two_agreeing() -> None:
    assert not enough_votes([CAND])
    assert not enough_votes([CAND, ORIG])
    assert enough_votes([CAND, "# الْفَصْل"])


def test_consensus_prefers_the_reading_most_variants_agree_on() -> None:
    word, score, agreeing = visual_consensus(
        {"original": ORIG, "upscaled": CAND, "contrast": CAND}, candidate=CAND, original=ORIG
    )
    assert normalize(word) == normalize(CAND)
    assert score == 1.0
    assert agreeing == ["upscaled", "contrast"]


def test_consensus_of_nothing_is_empty() -> None:
    assert visual_consensus({"original": ""}, candidate=CAND, original=ORIG) == ("", 0.0, [])


# ── the decision ────────────────────────────────────────────────────────────


def run(**kw):
    args = dict(
        original=ORIG,
        candidate=CAND,
        context_score=0.95,
        validation_score=0.95,
        lighton_reading=CAND,
        lighton_score=1.0,
        blind=clear(),
    )
    args.update(kw)
    return decide(**args)


def test_everything_agreeing_on_the_candidate_accepts() -> None:
    score, status = run()
    assert status == ACCEPT
    assert score >= 0.88


def test_both_readers_seeing_the_original_vetoes_however_strong_the_context() -> None:
    score, status = run(lighton_reading=ORIG, blind=clear(ORIG), context_score=1.0)
    assert status == REVIEW
    assert score <= 0.75


def test_context_alone_never_changes_letters() -> None:
    """Perfect context and validation, no visual support for either side."""
    _, status = run(
        lighton_reading="",
        lighton_score=0.0,
        blind=UNCLEAR,
        context_score=1.0,
        validation_score=1.0,
    )
    assert status != ACCEPT


def test_an_unclear_blind_read_can_never_accept_a_letter_change() -> None:
    score, status = run(blind=UNCLEAR)
    assert status == REVIEW  # LightOn and context are strong: a human looks
    assert score < 0.88


def test_an_unclear_blind_read_and_weak_lighton_rejects() -> None:
    _, status = run(blind=UNCLEAR, lighton_reading=ORIG, context_score=0.5)
    assert status in {REVIEW, REJECT}
    _, status = run(
        blind=UNCLEAR,
        lighton_reading="",
        lighton_score=0.0,
        context_score=0.5,
        validation_score=0.5,
    )
    assert status == REJECT


def test_weak_context_rejects_even_with_visual_support() -> None:
    _, status = run(
        context_score=0.1, validation_score=0.1, lighton_score=0.2, blind=clear(confidence=0.75)
    )
    assert status in {REVIEW, REJECT}
    assert status != ACCEPT


def test_scores_are_clamped() -> None:
    score, _ = run(context_score=7.0, validation_score=-3.0)
    assert 0.0 <= score <= 1.0


# ── suspects and tokens ─────────────────────────────────────────────────────

PAGE = "ذهب الفضل إلى السوق ثم عاد الفضل مسرورا"


def test_suspects_must_appear_literally_and_clear_the_threshold() -> None:
    raw = [
        {"word": "الفضل", "sentence": "ذهب الفضل إلى السوق", "score": 0.9, "candidate": "الفصل"},
        {"word": "غائب", "sentence": "ذهب غائب", "score": 0.9, "candidate": "x"},  # not on page
        {"word": "السوق", "sentence": "ثم عاد", "score": 0.9, "candidate": "x"},  # not in sentence
        {"word": "عاد", "sentence": "ثم عاد الفضل", "score": 0.5, "candidate": "x"},  # weak
        {"word": "مسرورا", "sentence": "عاد الفضل مسرورا", "score": 0.8, "candidate": "null"},
        {"word": "ثم", "sentence": "ثم", "score": "high"},  # unparseable score
        "not a dict",
    ]
    suspects = filter_suspects(raw, PAGE)  # type: ignore[arg-type]
    assert [s.word for s in suspects] == ["الفضل", "مسرورا"]
    assert suspects[1].candidate is None


def test_a_word_that_occurs_once_resolves_to_its_token() -> None:
    assert resolve_token("السوق", "", PAGE.split()) == 3


def test_a_repeated_word_is_pinned_by_its_sentence() -> None:
    tokens = PAGE.split()
    assert resolve_token("الفضل", "ذهب الفضل إلى السوق", tokens) == 1
    assert resolve_token("الفضل", "ثم عاد الفضل", tokens) == 6


def test_an_ambiguous_word_is_left_alone() -> None:
    tokens = PAGE.split()
    assert resolve_token("الفضل", "", tokens) is None
    assert resolve_token("الفضل", "سطر لا يوجد", tokens) is None
    assert resolve_token("الفضل", "الفضل الفضل", ["الفضل", "الفضل", "الفضل", "الفضل"]) is None


def test_a_word_never_matches_inside_a_longer_token() -> None:
    assert resolve_token("فضل", "", PAGE.split()) is None


def test_best_box_prefers_an_exact_match() -> None:
    located = [
        LocatedWord("الفصول", 0.99, (0, 0, 1, 1)),
        LocatedWord("الْفَضْل", 0.10, (5, 5, 6, 6)),
    ]
    item, score = best_box(ORIG, located)
    assert item is located[1] and score == 1.0
    assert best_box("", located) == (None, 0.0)


def test_the_sentence_after_replacement_swaps_exactly_one_whole_word() -> None:
    assert replace_in_sentence("ذهب الفضل، ثم عاد", "الفضل", "الفصل") == "ذهب الفصل، ثم عاد"
    assert replace_in_sentence("الفضل والفضل", "الفضل", "الفصل") == "الفصل والفضل"
    assert replace_in_sentence("الفضل ثم الفضل", "الفضل", "الفصل") is None  # twice
    assert replace_in_sentence("الفضلاء", "الفضل", "الفصل") is None  # only inside a longer word
    assert replace_in_sentence("", "الفضل", "الفصل") is None
