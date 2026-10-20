"""tashkel.py must refuse a diacritizer output that changed any letter (spec 006).

The real model (arabic-diacritizer) is replaced with a fake so these run anywhere: the
`diacritize` package is stubbed in sys.modules before pipeline.tashkel is imported.
"""
import importlib
import sys
import types
from pathlib import Path

import pytest
from conftest import make_token, read_page, write_page


class FakeModel:
    """Returns whatever `output` is set to, ignoring its input."""

    output = ""

    def diacritize(self, text):
        return self.output


@pytest.fixture
def tashkel(monkeypatch):
    stub = types.ModuleType("diacritize")
    stub.Diacritizer = types.SimpleNamespace(from_pretrained=FakeModel)
    monkeypatch.setitem(sys.modules, "diacritize", stub)
    monkeypatch.delitem(sys.modules, "pipeline.tashkel", raising=False)
    monkeypatch.delitem(sys.modules, "pipeline.run_tashkel", raising=False)
    mod = importlib.import_module("pipeline.tashkel")
    mod._model = FakeModel()
    return mod


@pytest.fixture
def run_tashkel(tashkel):
    # Imported through sys.modules (fresh, bound to this test's tashkel), never as a
    # `pipeline` package attribute, which would still hold a previous test's module.
    return importlib.import_module("pipeline.run_tashkel")


def tokens(*words):
    return [make_token(f"p001w{i:03d}", w) for i, w in enumerate(words)]


def test_marks_only_output_is_applied(tashkel):
    tashkel._model.output = "قَوْسُ قُزَحَ"
    toks, report = tashkel.diacritize_tokens(tokens("قوس", "قزح"))
    assert report["diacritized"] == 2
    assert report["preserved"] is True
    assert [t["txt"] for t in toks] == ["قَوْسُ", "قُزَحَ"]


def test_changed_letter_leaves_page_untouched_and_names_the_token(tashkel):
    tashkel._model.output = "ذَهَبَ إِلَى الْفَضْلِ"
    toks, report = tashkel.diacritize_tokens(tokens("ذهب", "إلى", "الفصل"))

    assert report["diacritized"] == 0
    assert report["preserved"] is False
    assert report["first_diff"]["id"] == "p001w002"
    assert report["first_diff"]["before"] == "الفصل"
    assert report["first_diff"]["after"] == "الْفَضْلِ"
    # all-or-nothing: not even the two good words were applied
    assert [t["txt"] for t in toks] == ["ذهب", "إلى", "الفصل"]


def test_word_count_mismatch_still_reported_as_mismatch(tashkel):
    tashkel._model.output = "قَوْسُ"
    _, report = tashkel.diacritize_tokens(tokens("قوس", "قزح"))
    assert report["mismatch"] is True
    assert report["diacritized"] == 0


def test_run_tashkel_records_rejection_and_keeps_going(tashkel, run_tashkel, tmp_path: Path):
    book = tmp_path / "book"
    write_page(book, 1, tokens("الفصل"), status="extracted", path="diacritise")
    write_page(book, 2, [make_token("p002w000", "قوس")], status="extracted", path="diacritise")

    outputs = iter(["الْفَضْلِ", "قَوْسُ"])  # page 1 changes a letter, page 2 is fine
    tashkel._model.diacritize = lambda text: next(outputs)

    done, skipped, mismatched, rejected = run_tashkel.run(book)

    assert (done, skipped, mismatched, rejected) == (1, 0, 0, 1)
    p1 = read_page(book, 1)
    assert p1["status"] == "extracted"
    assert p1["tokens"][0]["txt"] == "الفصل"
    assert p1["diac_error"]["id"] == "p001w000"
    assert p1["diac_error"]["reason"] == "letters_changed"
    p2 = read_page(book, 2)
    assert p2["status"] == "diacritised"
    assert "diac_error" not in p2


def test_successful_rerun_clears_a_previous_rejection(tashkel, run_tashkel, tmp_path: Path):
    book = tmp_path / "book"
    write_page(book, 1, tokens("قوس"), status="extracted", path="diacritise",
               diac_error={"id": "p001w000", "reason": "letters_changed"})
    tashkel._model.output = "قَوْسُ"

    run_tashkel.run(book)

    p1 = read_page(book, 1)
    assert p1["status"] == "diacritised"
    assert "diac_error" not in p1
