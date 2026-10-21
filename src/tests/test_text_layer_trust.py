"""Spec 012: a text layer is spot-checked against OCR before it is trusted."""
from pathlib import Path

import pytest

pytest.importorskip("pymupdf")  # the module reads PDFs; system python may lack it

from pipeline import text_layer_trust as tlt

PAGE_A = "ذهب الولد إلى الفصل صباحا مع أصدقائه".split()  # noqa: SIM905 -- a sentence reads better than a list
PAGE_B = "رأت فرح قوس قزح في السماء بعد المطر".split()  # noqa: SIM905 -- a sentence reads better than a list
SAMPLES = Path(__file__).resolve().parents[2] / "assets" / "PDF Samples"


class TestSimilarity:
    def test_same_words_score_one(self):
        assert tlt.similarity(PAGE_A, PAGE_A) == 1.0

    def test_text_layer_and_ocr_spelling_differences_are_folded(self):
        ocr = ["ذَهَبَ", "الولد", "الى", "الفصل،", "صباحاً", "مع", "اصدقائه"]
        assert tlt.similarity(PAGE_A, ocr) == 1.0

    def test_text_from_another_page_scores_low(self):
        assert tlt.similarity(PAGE_A, PAGE_B) < 0.2

    def test_scrambled_order_scores_lower_than_the_right_order(self):
        assert tlt.similarity(PAGE_A, PAGE_A[::-1]) < tlt.similarity(PAGE_A, PAGE_A)


@pytest.fixture
def fake_pdf(tmp_path, monkeypatch):
    """A 'PDF' whose text layer is PAGE_A on every page; no real file needed."""
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.7 fake")
    monkeypatch.setattr(tlt, "sample_pages", lambda path, k=2: [2, 7][:k])
    monkeypatch.setattr(tlt, "text_layer_words", lambda path, page: PAGE_A)
    return pdf


class Ocr:
    def __init__(self, by_page):
        self.by_page, self.calls = by_page, []

    def __call__(self, path, page):
        self.calls.append(page)
        return self.by_page[page]


class TestAssess:
    def test_matching_text_layer_is_trusted(self, fake_pdf):
        result = tlt.assess(fake_pdf, ocr_words=Ocr({2: PAGE_A, 7: PAGE_A}), engine="gemini")
        assert result.trusted and result.similarity == 1.0
        assert "matches OCR" in result.reason

    def test_one_ghost_page_is_enough_to_distrust_the_book(self, fake_pdf):
        result = tlt.assess(fake_pdf, ocr_words=Ocr({2: PAGE_B, 7: PAGE_A}), engine="gemini")
        assert not result.trusted
        assert "text-layer-mismatch" in result.reason and "p2=" in result.reason

    def test_scores_are_cached_by_content_and_engine(self, fake_pdf, tmp_path):
        ocr = Ocr({2: PAGE_A, 7: PAGE_A})
        tlt.assess(fake_pdf, ocr_words=ocr, engine="gemini", cache_dir=tmp_path / "trust")
        again = tlt.assess(fake_pdf, ocr_words=ocr, engine="gemini", cache_dir=tmp_path / "trust",
                           threshold=0.99)
        assert ocr.calls == [2, 7], "the second run (even with a new threshold) costs no OCR"
        assert again.trusted
        tlt.assess(fake_pdf, ocr_words=ocr, engine="lighton", cache_dir=tmp_path / "trust")
        assert ocr.calls == [2, 7, 2, 7], "a different engine is a different check"

    def test_no_page_with_enough_words(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tlt, "sample_pages", lambda path, k=2: [])
        pdf = tmp_path / "b.pdf"
        pdf.write_bytes(b"x")
        result = tlt.assess(pdf, ocr_words=Ocr({}), engine="gemini")
        assert not result.trusted and "enough" in result.reason


class TestMainWiring:
    @pytest.fixture
    def main(self, monkeypatch, tmp_path):
        pytest.importorskip("pymupdf")
        from pipeline import main as m
        calls = []
        monkeypatch.setattr(m, "has_text_layer", lambda p: True)
        monkeypatch.setattr(m.run_extract, "run", lambda *a, **k: calls.append("text-layer"))
        monkeypatch.setattr(m.run_ocr_gemini, "run", lambda *a, **k: calls.append("ocr"))
        for name in ("run_tashkel", "run_speak_gemini"):
            monkeypatch.setattr(getattr(m, name), "run", lambda *a, **k: None)
        monkeypatch.setattr(m.build_reader, "build", lambda *a, **k: None)
        pdf = tmp_path / "book.pdf"
        pdf.write_bytes(b"%PDF fake")
        return m, calls, pdf, tmp_path / "work" / "book"

    def test_ghost_layer_goes_down_the_ocr_path(self, main, monkeypatch, capsys):
        m, calls, pdf, work = main
        monkeypatch.setattr(m, "text_layer_is_trusted", lambda p, w, o: (False, "text-layer-mismatch"))
        m.run_pipeline(pdf, work_dir=work)
        assert calls == ["ocr"]
        assert "text-layer-mismatch" in capsys.readouterr().out

    def test_trust_flag_skips_the_check(self, main, monkeypatch):
        m, calls, pdf, work = main

        def must_not_run(*a):
            raise AssertionError("the check must not run with --trust-text-layer")

        monkeypatch.setattr(m, "text_layer_is_trusted", must_not_run)
        m.run_pipeline(pdf, work_dir=work, trust_text_layer=True)
        assert calls == ["text-layer"]

    def test_an_unavailable_check_falls_back_to_the_text_layer_loudly(self, main, monkeypatch):
        m, _calls, pdf, work = main
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setattr(m.text_layer_trust, "sample_pages", lambda path, k=2: [1])
        monkeypatch.setattr(m, "_ocr_sampler", lambda ocr: (_ for _ in ()).throw(KeyError("GEMINI_API_KEY")))
        trusted, reason = m.text_layer_is_trusted(pdf, work, "gemini")
        assert trusted and "UNCHECKED" in reason

    def test_a_bug_in_the_check_is_not_swallowed(self, main, monkeypatch):
        m, _, pdf, work = main
        monkeypatch.setattr(m, "_ocr_sampler", lambda ocr: (_ for _ in ()).throw(TypeError("bug")))
        with pytest.raises(TypeError):
            m.text_layer_is_trusted(pdf, work, "gemini")


@pytest.mark.skipif(not SAMPLES.exists(), reason="sample corpus PDFs are not available")
def test_real_ghost_page_scores_below_the_threshold():
    """شكرا يامى page 2: the real case. Its text layer is about another page. `shown` is
    what LightOn actually read from the rendered page (the page record in git history,
    lighton/parse-v3), so no paid call is needed."""
    pdf = next(p for p in SAMPLES.glob("*.pdf") if p.stem.strip() == "شكرا يامى")
    shown = ("مَرْحَبًا بِكُمْ مِنْ جَدِيدٍ يَا أَصْدِقَائِي؛ فَأَنَا وَإِلَدَةُ فَرَحَ، وَسَأَخْكِي لَكُمْ قِصَّةً عَنْ فَرَحِ أَحِبٌ أَنْ يَعْرِفَهَا كُلُّ الأَوْلَادِ وَالْبَنَاتِ، الأَبَاءِ وَالْأُمَّهَاتِ. كَانَتْ فَرَحُ طِفْلَةً نَشِيطَةً، ثُمَارِسُ الْهَوَايَاتِ، تَرْسُمُ وَتَلْعَبُ، وَتَبْتَكِرُ بِالْمُكَّعَبَاتِ،").split()  # noqa: SIM905 -- a sentence reads better than a list
    score = tlt.similarity(tlt.text_layer_words(pdf, 2), shown)
    assert score == pytest.approx(0.596, abs=0.01)  # measured 2026-10-21
    assert score < tlt.DEFAULT_THRESHOLD
