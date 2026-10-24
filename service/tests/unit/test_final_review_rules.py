"""Spec 024: the final context review's safety checks and token mapping."""

from __future__ import annotations

import pytest

from ai_engine.domain.final_review_rules import (
    UnsafeReview,
    arabic_words,
    check_reply,
    clean_ocr_noise,
    clean_reply,
    dropped_changes,
    speaker_labels,
    suggestions,
)

# 20 words, so one changed word is 5% and four are 20%
PAGE = (
    "ذهب الولد إلى السوق في الصباح الباكر واشترى خبزا وجبنا وعاد إلى البيت "
    "فوجد أمه تنتظره عند الباب وهي مسرورة"
)


def test_page_has_twenty_words() -> None:
    assert len(arabic_words(PAGE)) == 20


def test_noise_is_removed_and_words_are_not() -> None:
    raw = "<!-- page 3 -->\n<b>ذهب</b> الولد\n\n\n12\nNote: scanned\nإلى السوق"
    assert clean_ocr_noise(raw) == "ذهب الولد\n\nإلى السوق"


@pytest.mark.parametrize(
    "reply",
    ["```text\nذهب الولد\n```", "النص المصحح: ذهب الولد", "```\nالتصحيح:\nذهب الولد\n```"],
)
def test_reply_wrappers_are_stripped(reply: str) -> None:
    assert clean_reply(reply) == "ذهب الولد"


def test_a_small_fix_passes_the_checks() -> None:
    stats = check_reply(PAGE, PAGE.replace("السوق", "السوك"))
    assert stats.changed_words == 1
    assert stats.changed_ratio == pytest.approx(0.05)


def test_marks_do_not_split_words() -> None:
    """The prototype counted letters on marked text, so a haraka split a word in two."""
    assert arabic_words("ذَهَبَ الوَلَدُ") == ["ذهب", "الولد"]
    stats = check_reply("ذَهَبَ الوَلَدُ إلى السوق", "ذهب الولد إلى السوق")
    assert stats.changed_words == 0


@pytest.mark.parametrize(
    ("corrected", "reason"),
    [
        ("", "no text"),
        ("123 456", "no Arabic"),
        (PAGE + " ثم نام الولد طويلا", "length"),  # +4 words of insertion
        (" ".join(PAGE.split()[:17]), "length"),  # paragraph tail deleted
        (
            PAGE.replace("ذهب", "راح")
            .replace("السوق", "المتجر")
            .replace("خبزا", "عيشا")
            .replace("أمه", "والدته"),
            "words changed",
        ),
    ],
)
def test_unsafe_replies_are_rejected(corrected: str, reason: str) -> None:
    with pytest.raises(UnsafeReview, match=reason):
        check_reply(PAGE, corrected)


def test_a_renamed_speaker_is_unsafe() -> None:
    page = "سارة: ذهبت إلى السوق " + " ".join(["وعادت"] * 15)
    with pytest.raises(UnsafeReview, match="speaker"):
        check_reply(page, page.replace("سارة:", "سلمى:"))
    assert speaker_labels(["سارة:", "قالت", "المعلمة:", "نعم"]) == ["سارة", "المعلمة"]


def test_a_page_without_arabic_is_not_reviewable() -> None:
    with pytest.raises(UnsafeReview, match="no Arabic text to review"):
        check_reply("123", "123")


def test_one_for_one_changes_map_to_token_indices() -> None:
    tokens = ["ذهب", "الفضل", "إلى", "السوق"]
    assert suggestions(tokens, "ذهب الفصل إلى السوق") == [(1, "الفصل")]


def test_marks_or_punctuation_only_are_not_suggestions() -> None:
    tokens = ["ذهب", "الفضل", "إلى", "السوق،"]
    assert suggestions(tokens, "ذَهَبَ الفضل إلى السوق.") == []


def test_splits_merges_and_insertions_have_no_token_and_are_counted() -> None:
    tokens = ["ذهب", "الفضل", "إلى", "السوق"]
    merged = "ذهب الفضلإلى السوق"
    assert suggestions(tokens, merged) == []
    assert dropped_changes(tokens, merged) == 2
    assert dropped_changes(tokens, "ذهب الفصل إلى السوق") == 0
