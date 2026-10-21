"""Spec 019: the accuracy harness, on synthetic pages where the right answer is known."""
import json

import pytest
import score_corrections as sc
from conftest import make_token, write_page


def tok(id_, txt, ocr_txt=None):
    t = make_token(id_, txt)
    if ocr_txt is not None:
        t["ocr_txt"] = ocr_txt
    return t


def score(ref, tokens):
    return sc.score_page(1, ref, {"tokens": tokens})


def test_perfect_page():
    s = score("ذهب إلى الفصل", [tok("p001w000", "ذهب"), tok("p001w001", "إلى"),
                                 tok("p001w002", "الفصل")])
    assert (s.wer_before, s.wer_after, s.cer_after) == (0, 0, 0)
    assert (s.caught, s.false_edits, s.missed) == ([], [], [])


def test_a_caught_error():
    """الفضل -> الفصل: the README's dangerous class, fixed by correction."""
    s = score("ذهب إلى الفصل", [tok("p001w000", "ذهب"), tok("p001w001", "إلى"),
                                 tok("p001w002", "الفصل", ocr_txt="الفضل")])
    assert s.caught == ["p001w002"]
    assert s.wer_before == pytest.approx(1 / 3, abs=1e-3) and s.wer_after == 0


def test_a_false_edit():
    s = score("قوس قزح", [tok("p001w000", "قوس"), tok("p001w001", "فزح", ocr_txt="قزح")])
    assert s.false_edits == ["p001w001"]
    assert s.wer_before == 0 and s.wer_after == pytest.approx(0.5)


def test_a_missed_error():
    s = score("قوس قزح", [tok("p001w000", "قوس"), tok("p001w001", "فزح")])
    assert s.missed == ["p001w001"]


def test_diacritics_and_punctuation_do_not_count_as_letter_errors():
    s = score("قَوْسُ قُزَحَ.", [tok("p001w000", "قوس"), tok("p001w001", "قزح")])
    assert s.wer_after == 0


def test_alef_forms_are_real_errors_not_folded():
    s = score("إلى", [tok("p001w000", "الى")])
    assert s.wer_after == 1.0 and s.missed == ["p001w000"]


def test_insertions_and_deletions_count_in_wer():
    s = score("ذهب إلى البيت", [tok("p001w000", "ذهب"), tok("p001w001", "البيت")])
    assert s.wer_after == pytest.approx(1 / 3, abs=1e-3)
    s = score("ذهب", [tok("p001w000", "ذهب"), tok("p001w001", "زائد")])
    assert s.wer_after == pytest.approx(1.0)


def test_der_on_a_diacritised_reference():
    # same letters; the last letter's case ending differs: 1 wrong of 5 letters
    s = score("الْفَصْلِ", [tok("p001w000", "الْفَصْلُ")])
    assert s.der_after == pytest.approx(0.2)
    assert s.der_letters == 5


def test_no_der_on_an_undiacritised_reference():
    assert score("الفصل", [tok("p001w000", "الْفَصْلِ")]).der_after is None


def test_run_reports_a_book(tmp_path, capsys):
    work, refs = tmp_path / "book-lighton", tmp_path / "refs"
    write_page(work, 1, [tok("p001w000", "قوس"), tok("p001w001", "قزح", ocr_txt="فزح")])
    write_page(work, 2, [tok("p002w000", "مرئي")])
    refs.mkdir()
    (refs / "p001.txt").write_text("قوس قزح", encoding="utf-8")
    (refs / "p002.txt").write_text("مرئي", encoding="utf-8")
    (refs / "p009.txt").write_text("لا صفحة", encoding="utf-8")  # no record: skipped
    (refs / "notes.md").write_text("ignored", encoding="utf-8")

    report = sc.run(work, refs)

    assert (report["pages"], report["ref_words"]) == (2, 3)
    assert report["caught"] == 1 and report["false_edits"] == 0
    assert report["wer_before"] == pytest.approx(1 / 3, abs=1e-3) and report["wer_after"] == 0
    out = capsys.readouterr()
    assert "caught 1" in out.out and "p009" in out.err


def test_json_output_round_trips(tmp_path, capsys):
    work, refs = tmp_path / "b", tmp_path / "r"
    write_page(work, 1, [tok("p001w000", "قوس")])
    refs.mkdir()
    (refs / "p001.txt").write_text("قوس", encoding="utf-8")
    sc.run(work, refs, as_json=True)
    assert json.loads(capsys.readouterr().out)["wer_after"] == 0
