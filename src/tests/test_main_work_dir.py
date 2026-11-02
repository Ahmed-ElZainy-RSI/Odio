"""main.py puts each OCR engine's run in its own work directory."""
import pytest

pytest.importorskip("pymupdf")
pytest.importorskip("google.genai")

from pipeline import main


@pytest.mark.parametrize("ocr, variant", [("gemini", ""), ("lighton", "lighton")])
def test_the_ocr_engine_picks_the_work_dir(tmp_path, monkeypatch, ocr, variant):
    seen = {}

    def resolve(pdf_path, *, variant=""):
        seen["variant"] = variant
        return tmp_path / "work"

    class Stop(Exception):
        pass

    def stop(*a, **k):
        raise Stop
    monkeypatch.setattr(main.books, "resolve_work_dir", resolve)
    monkeypatch.setattr(main, "has_text_layer", lambda p: False)
    monkeypatch.setattr(main.run_ocr_gemini, "run", stop)
    monkeypatch.setattr(main.run_ocr_lighton, "run", stop)

    with pytest.raises(Stop):
        main.run_pipeline(tmp_path / "book.pdf", ocr=ocr, force_ocr=True)
    assert seen["variant"] == variant
