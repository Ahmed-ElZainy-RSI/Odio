"""The diacritization letter-preservation guard (specs/006-letter-preservation-guard).

A diacritizer may add or remove marks. It may never change a letter, a punctuation mark or
the word sequence -- a changed letter produces fluent, confidently wrong audio. The
properties below must hold for every input, not just the examples we thought of.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from ai_engine.domain.preservation import check_letters_preserved, strip_marks

LETTERS = [chr(c) for c in range(0x0621, 0x063B)] + [chr(c) for c in range(0x0641, 0x064B)]
MARKS = [chr(c) for c in range(0x064B, 0x0653)]  # tanween, harakat, shadda, sukun

arabic_word = st.text(alphabet=LETTERS, min_size=1, max_size=8)
arabic_words = st.lists(arabic_word, min_size=1, max_size=12)


@st.composite
def with_marks(draw, word: str) -> str:
    """The same word with zero to two marks after each letter."""
    return "".join(ch + "".join(draw(st.lists(st.sampled_from(MARKS), max_size=2))) for ch in word)


@given(arabic_words, st.data())
def test_adding_marks_always_preserves(words: list[str], data: st.DataObject) -> None:
    after = [data.draw(with_marks(w)) for w in words]
    assert check_letters_preserved(words, after).preserved


@given(arabic_words, st.data())
def test_removing_marks_always_preserves(words: list[str], data: st.DataObject) -> None:
    before = [data.draw(with_marks(w)) for w in words]
    assert check_letters_preserved(before, words).preserved


@given(arabic_words, st.data())
def test_any_letter_substitution_is_caught(words: list[str], data: st.DataObject) -> None:
    i = data.draw(st.integers(0, len(words) - 1))
    j = data.draw(st.integers(0, len(words[i]) - 1))
    old = words[i][j]
    new = data.draw(st.sampled_from([c for c in LETTERS if c != old]))
    after = list(words)
    after[i] = words[i][:j] + new + words[i][j + 1 :]

    result = check_letters_preserved(words, after)

    assert not result.preserved
    assert result.first_difference is not None
    assert result.first_difference.index == i
    assert result.first_difference.reason == "letters_changed"


@given(arabic_words, st.data())
def test_any_letter_insertion_or_deletion_is_caught(words: list[str], data: st.DataObject) -> None:
    i = data.draw(st.integers(0, len(words) - 1))
    after = list(words)
    if data.draw(st.booleans()) or len(words[i]) == 1:
        after[i] = words[i] + data.draw(st.sampled_from(LETTERS))
    else:
        after[i] = words[i][1:]
    assert not check_letters_preserved(words, after).preserved


def test_the_readme_example_valid_word_wrong_letter() -> None:
    # الفصل ("the classroom") -> الْفَضْلِ ("the merit"): a real word, so only a letter
    # comparison catches it.
    result = check_letters_preserved(["الفصل"], ["الْفَضْلِ"])
    assert not result.preserved
    assert result.first_difference is not None
    assert result.first_difference.before == "الفصل"
    assert result.first_difference.after == "الْفَضْلِ"


def test_word_count_change_is_reported_as_such() -> None:
    result = check_letters_preserved(["قوس", "قزح"], ["قَوْسُ"])
    assert not result.preserved
    assert result.first_difference is not None
    assert result.first_difference.reason == "word_count"


def test_punctuation_change_is_caught() -> None:
    assert not check_letters_preserved(["نعم،"], ["نَعَمْ"]).preserved
    assert not check_letters_preserved(["لماذا"], ["لِمَاذَا؟"]).preserved


def test_added_hamza_is_a_letter_change_not_a_mark() -> None:
    # مريي -> مرئي: the lost-hamza error class. Combining hamza above (U+0654) after ي
    # composes to ئ under NFC, so it must not be stripped as a "mark".
    assert not check_letters_preserved(["مريي"], ["مرئي"]).preserved
    assert not check_letters_preserved(["مريي"], ["مرئي"]).preserved
    # ...and on a letter where it does not compose, it is still not a removable mark.
    assert not check_letters_preserved(["كتب"], ["كتٔب"]).preserved


def test_decomposed_and_precomposed_forms_are_the_same_letters() -> None:
    assert check_letters_preserved(["أحمد"], ["أحْمَد"]).preserved


def test_tatweel_and_dagger_alef_are_marks() -> None:
    assert check_letters_preserved(["هذا"], ["هٰذَا"]).preserved
    assert check_letters_preserved(["جميل"], ["جمـيل"]).preserved


def test_strip_marks_keeps_letters_and_punctuation() -> None:
    assert strip_marks("الْفَصْلِ، يَا!") == "الفصل، يا!"


def test_empty_pages_are_preserved() -> None:
    assert check_letters_preserved([], []).preserved


def test_to_dict_is_json_ready() -> None:
    result = check_letters_preserved(["الفصل"], ["الفضل"])
    assert result.to_dict() == {
        "preserved": False,
        "first_difference": {
            "index": 0,
            "before": "الفصل",
            "after": "الفضل",
            "reason": "letters_changed",
        },
    }
