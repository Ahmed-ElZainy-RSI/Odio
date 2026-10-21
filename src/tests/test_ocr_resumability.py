"""Regression tests for the most consequential bug fixed this session:
run_ocr_gemini.py and run_ocr_lighton.py originally only recognized
status == "extracted" as "already done". tools/apply_corrections.py and
tools/replace_page.py deliberately reset a corrected page's status back to
"extracted" so run_tashkel.py/run_speak_gemini.py resume it -- but with the
original bug, re-running the base OCR driver on that same book would see
"extracted" doesn't match... no, actually the ORIGINAL bug was the reverse
direction: a page already advanced to "diacritised" or "spoken" was NOT
recognized as done (only the literal string "extracted" was), so the OCR
driver would silently re-OCR it, discarding every correction and all
downstream work. See README.md's Open Items and TESTING.md section 5.2.

No real API calls happen here: the network-touching functions are
monkeypatched to raise if called at all, so "the test completed without
raising" is itself the proof that the skip fired before any network work
was attempted.
"""
import json

import pytest
from conftest import make_token, read_page, write_page

pymupdf = pytest.importorskip("pymupdf")
pytest.importorskip("google.genai")

from pipeline import run_ocr_gemini, run_ocr_lighton


@pytest.fixture
def tiny_pdf(tmp_path):
    doc = pymupdf.open()
    for _ in range(3):
        doc.new_page()
    path = tmp_path / "test.pdf"
    doc.save(str(path))
    doc.close()
    return path


ALREADY_DONE_STATUSES = ["extracted", "diacritised", "spoken"]


class TestGeminiOcrResumability:
    @pytest.mark.parametrize("status", ALREADY_DONE_STATUSES)
    def test_page_past_extraction_is_skipped_not_reocrd(self, tiny_pdf, tmp_path, monkeypatch, status):
        out_dir = tmp_path / "out"
        write_page(out_dir, 1, [make_token("p001w000", "original")], status=status)
        before = read_page(out_dir, 1)

        monkeypatch.setenv("GEMINI_API_KEY", "dummy-key-never-used")

        def _must_not_be_called(*a, **k):
            raise AssertionError(f"render_page was called for a page already at status={status!r}")
        monkeypatch.setattr(run_ocr_gemini, "render_page", _must_not_be_called)

        run_ocr_gemini.run(tiny_pdf, out_dir, pages="1")

        after = read_page(out_dir, 1)
        assert after == before, "the page record must be completely untouched"

    def test_a_genuinely_new_page_does_proceed_to_render(self, tiny_pdf, tmp_path, monkeypatch):
        """Sanity check the mock above is actually wired: a page with NO
        existing record must reach render_page (proven by it raising).

        Since spec 008 a render failure is a page failure like any other --
        recorded in failures.json, not propagated -- so the proof that the
        skip branch was NOT taken is the recorded failure."""
        out_dir = tmp_path / "out"
        monkeypatch.setenv("GEMINI_API_KEY", "dummy-key-never-used")

        def _raises(*a, **k):
            raise RuntimeError("reached render_page as expected")
        monkeypatch.setattr(run_ocr_gemini, "render_page", _raises)

        run_ocr_gemini.run(tiny_pdf, out_dir, pages="1")

        recorded = json.loads((out_dir / "failures.json").read_text(encoding="utf-8"))
        assert recorded["1"]["ocr"]["message"] == "reached render_page as expected"


class TestLightonOcrResumability:
    @pytest.mark.parametrize("status", ALREADY_DONE_STATUSES)
    def test_page_past_extraction_is_skipped_not_reocrd(self, tiny_pdf, tmp_path, monkeypatch, status):
        out_dir = tmp_path / "out"
        write_page(out_dir, 1, [make_token("p001w000", "original")], status=status, box_level="none")
        before = read_page(out_dir, 1)

        monkeypatch.setenv("LIGHTON_API_KEY", "dummy-key-never-used")

        def _must_not_be_called(*a, **k):
            raise AssertionError(f"slice_page was called for a page already at status={status!r}")
        monkeypatch.setattr(run_ocr_lighton, "slice_page", _must_not_be_called)

        run_ocr_lighton.run(tiny_pdf, out_dir, pages="1")

        after = read_page(out_dir, 1)
        assert after == before

    def test_a_genuinely_new_page_does_proceed_to_slice(self, tiny_pdf, tmp_path, monkeypatch, capsys):
        """run_ocr_lighton.py wraps slice_page()/parse_page() in its own
        try/except (unlike run_ocr_gemini.py, where render_page() is outside
        it) -- that's intentional resumability design (a page-level failure
        shouldn't crash the whole run), so the raised marker is caught and
        printed rather than propagating. Checking stdout is therefore the
        correct way to prove slice_page was actually reached here, not a
        workaround for a bug."""
        out_dir = tmp_path / "out"
        monkeypatch.setenv("LIGHTON_API_KEY", "dummy-key-never-used")

        def _raises(*a, **k):
            raise RuntimeError("reached slice_page as expected")
        monkeypatch.setattr(run_ocr_lighton, "slice_page", _raises)

        run_ocr_lighton.run(tiny_pdf, out_dir, pages="1")  # must not crash the test

        assert "reached slice_page as expected" in capsys.readouterr().out


class TestCrossToolScenario:
    """The exact real-world sequence that exposed this bug: a correction
    tool resets status to 'extracted' expecting the NEXT stage
    (run_tashkel/run_speak) to pick it up -- not for the OCR driver itself
    to be re-run. But 'extracted' is also in ALREADY_DONE_STATUSES, so even
    that reset-to-extracted state must still be skipped by a repeat OCR run,
    exactly like the other two states."""

    def test_status_reset_by_apply_corrections_still_skips_ocr(self, tiny_pdf, tmp_path, monkeypatch):
        out_dir = tmp_path / "out"
        # simulate: page was 'spoken', then apply_corrections.py reset it to
        # 'extracted' after writing a real correction
        write_page(out_dir, 1, [make_token("p001w000", "corrected-word", fixes=["ensemble-vlm"])],
                   status="extracted")
        before = read_page(out_dir, 1)

        monkeypatch.setenv("GEMINI_API_KEY", "dummy-key-never-used")
        monkeypatch.setattr(run_ocr_gemini, "render_page",
                             lambda *a, **k: (_ for _ in ()).throw(
                                 AssertionError("a corrected page must never be re-OCR'd")))

        run_ocr_gemini.run(tiny_pdf, out_dir, pages="1")

        after = read_page(out_dir, 1)
        assert after == before
        assert after["tokens"][0]["txt"] == "corrected-word", "the real correction must survive"
