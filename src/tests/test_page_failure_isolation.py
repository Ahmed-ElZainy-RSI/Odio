"""Spec 008: one bad page must not stop the book.

A page-specific failure is recorded in work/<book>/failures.json and the run
moves on; a quota error still stops the run (more calls would only burn more
quota); a programming error still crashes loudly (spec 007). A failure never
touches the page's status, so the failed stage simply retries it next run.
"""
import asyncio
import json
import sys
import types

import pytest
from conftest import make_token, read_page, write_page

from pipeline import failures
from pipeline.failures import classify_failure


class QuotaError(Exception):
    def __init__(self):
        super().__init__("429 RESOURCE_EXHAUSTED. Quota exceeded for metric generate_content")
        self.code = 429


def read_failures(work_dir):
    path = work_dir / "failures.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


class TestClassifyFailure:
    def test_quota_by_status_code(self):
        assert classify_failure(QuotaError()) == "quota"

    def test_quota_by_response_status_code(self):
        exc = Exception("too many")
        exc.response = types.SimpleNamespace(status_code=429)
        assert classify_failure(exc) == "quota"

    @pytest.mark.parametrize("message", [
        "RESOURCE_EXHAUSTED", "You exceeded your current quota", "Rate limit reached",
        "insufficient credits remaining",
    ])
    def test_quota_by_message(self, message):
        assert classify_failure(RuntimeError(message)) == "quota"

    @pytest.mark.parametrize("exc", [NameError("x"), AttributeError("x"), TypeError("x")])
    def test_programming_errors_are_bugs(self, exc):
        assert classify_failure(exc) == "bug"

    @pytest.mark.parametrize("exc", [
        ValueError("bad json"), KeyError("lines"), RuntimeError("500 INTERNAL"),
        ConnectionError("reset"),
    ])
    def test_everything_else_is_the_page(self, exc):
        assert classify_failure(exc) == "page"


class TestFailureLedger:
    def test_record_then_clear(self, tmp_path):
        failures.record(tmp_path, 3, "tts", ValueError("boom"))
        entry = read_failures(tmp_path)["3"]["tts"]
        assert entry["type"] == "ValueError"
        assert entry["message"] == "boom"
        assert "at" in entry

        failures.clear(tmp_path, 3, "tts")
        assert read_failures(tmp_path) == {}

    def test_clear_without_ledger_is_a_no_op(self, tmp_path):
        failures.clear(tmp_path, 1, "ocr")
        assert not (tmp_path / "failures.json").exists()

    def test_stages_are_independent(self, tmp_path):
        failures.record(tmp_path, 3, "ocr", ValueError("a"))
        failures.record(tmp_path, 3, "tts", ValueError("b"))
        failures.clear(tmp_path, 3, "ocr")
        assert list(read_failures(tmp_path)["3"]) == ["tts"]


# ---------------------------------------------------------------- run_speak_gemini

@pytest.fixture
def speak_gemini(monkeypatch):
    pytest.importorskip("google.genai")
    pytest.importorskip("faster_whisper")
    from pipeline import run_speak_gemini
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    monkeypatch.setattr(run_speak_gemini.genai, "Client", lambda **k: object())
    return run_speak_gemini


def three_pages(book):
    for n in (1, 2, 3):
        write_page(book, n, [make_token(f"p{n:03d}w000", "كلمة")], status="diacritised")


class TestSpeakGemini:
    def test_page_failure_is_recorded_and_the_book_continues(self, speak_gemini, tmp_path,
                                                             monkeypatch):
        book = tmp_path / "book"
        three_pages(book)
        done = []

        def fake_do_page(client, path, audio_dir, voice):
            rec = json.loads(path.read_text(encoding="utf-8"))
            if rec["page"] == 2:
                raise ValueError("model returned no audio")
            done.append(rec["page"])

        monkeypatch.setattr(speak_gemini, "do_page", fake_do_page)
        summary = speak_gemini.run(book)

        assert done == [1, 3]
        assert summary == {"done": 2, "failed": 1, "stopped_on_quota": False}
        assert read_failures(book)["2"]["tts"]["message"] == "model returned no audio"
        assert read_page(book, 2)["status"] == "diacritised", "status must not change on failure"

    def test_quota_stops_the_run_and_records_nothing(self, speak_gemini, tmp_path, monkeypatch):
        book = tmp_path / "book"
        three_pages(book)
        done = []

        def fake_do_page(client, path, audio_dir, voice):
            rec = json.loads(path.read_text(encoding="utf-8"))
            if rec["page"] == 2:
                raise QuotaError()
            done.append(rec["page"])

        monkeypatch.setattr(speak_gemini, "do_page", fake_do_page)
        summary = speak_gemini.run(book)

        assert done == [1]
        assert summary["stopped_on_quota"] is True
        assert read_failures(book) == {}

    def test_programming_error_propagates(self, speak_gemini, tmp_path, monkeypatch):
        book = tmp_path / "book"
        three_pages(book)

        def fake_do_page(*a):
            raise TypeError("unsupported operand")

        monkeypatch.setattr(speak_gemini, "do_page", fake_do_page)
        with pytest.raises(TypeError):
            speak_gemini.run(book)

    def test_success_clears_an_earlier_failure(self, speak_gemini, tmp_path, monkeypatch):
        book = tmp_path / "book"
        three_pages(book)
        failures.record(book, 2, "tts", ValueError("old"))
        monkeypatch.setattr(speak_gemini, "do_page", lambda *a: None)

        speak_gemini.run(book)

        assert read_failures(book) == {}


# ---------------------------------------------------------------- run_speak_hume

class TestSpeakHume:
    @pytest.fixture
    def speak_hume(self, monkeypatch):
        if "hume" not in sys.modules:
            try:
                import hume  # noqa: F401
            except ImportError:
                stub = types.ModuleType("hume")
                stub.AsyncHumeClient = object
                monkeypatch.setitem(sys.modules, "hume", stub)
        monkeypatch.delitem(sys.modules, "pipeline.tts_hume", raising=False)
        tts_stub = types.ModuleType("pipeline.tts_hume")
        tts_stub.synthesize, tts_stub.VOICE = None, "v"
        monkeypatch.setitem(sys.modules, "pipeline.tts_hume", tts_stub)
        monkeypatch.delitem(sys.modules, "pipeline.run_speak_hume", raising=False)
        import importlib
        mod = importlib.import_module("pipeline.run_speak_hume")
        monkeypatch.setenv("HUME_API_KEY", "dummy")
        monkeypatch.setattr(mod, "AsyncHumeClient", lambda **k: object())
        return mod

    def test_page_failure_is_recorded_and_the_book_continues(self, speak_hume, tmp_path,
                                                             monkeypatch):
        book = tmp_path / "book"
        three_pages(book)
        done = []

        async def fake_do_page(client, path, audio_dir, voice):
            rec = json.loads(path.read_text(encoding="utf-8"))
            if rec["page"] == 2:
                raise ValueError("no timestamps")
            done.append(rec["page"])

        monkeypatch.setattr(speak_hume, "do_page", fake_do_page)
        summary = speak_hume.run(book)

        assert done == [1, 3]
        assert summary == {"done": 2, "failed": 1, "stopped_on_quota": False}
        assert read_failures(book)["2"]["tts"]["type"] == "ValueError"


# ---------------------------------------------------------------- OCR drivers

@pytest.fixture
def three_page_pdf(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    for _ in range(3):
        doc.new_page()
    path = tmp_path / "book.pdf"
    doc.save(str(path))
    doc.close()
    return path


class TestOcrGemini:
    @pytest.fixture
    def ocr_gemini(self, monkeypatch):
        pytest.importorskip("google.genai")
        from pipeline import run_ocr_gemini
        monkeypatch.setenv("GEMINI_API_KEY", "dummy")
        monkeypatch.setattr(run_ocr_gemini.genai, "Client", lambda **k: object())
        monkeypatch.setattr(run_ocr_gemini, "render_page", lambda page: (b"jpeg", (10, 10)))
        return run_ocr_gemini

    def test_page_failure_is_recorded_and_the_book_continues(self, ocr_gemini, three_page_pdf,
                                                             tmp_path, monkeypatch):
        calls = []

        def fake_ocr(client, jpeg):
            calls.append(len(calls) + 1)
            if len(calls) == 2:
                raise ValueError("response did not match schema")
            return [{"text": "كلمة", "box_2d": [0, 0, 10, 10]}]

        monkeypatch.setattr(ocr_gemini, "ocr_page", fake_ocr)
        out = tmp_path / "out"
        summary = ocr_gemini.run(three_page_pdf, out)

        assert summary == {"done": 2, "failed": 1, "stopped_on_quota": False}
        assert sorted(p.name for p in out.glob("p*.json")) == ["p001.json", "p003.json"]
        assert read_failures(out)["2"]["ocr"]["message"] == "response did not match schema"

    def test_render_failure_is_a_page_failure_too(self, ocr_gemini, three_page_pdf, tmp_path,
                                                  monkeypatch):
        def bad_render(page):
            raise RuntimeError("cannot render page")

        monkeypatch.setattr(ocr_gemini, "render_page", bad_render)
        summary = ocr_gemini.run(three_page_pdf, tmp_path / "out")
        assert summary["failed"] == 3

    def test_quota_stops_the_run(self, ocr_gemini, three_page_pdf, tmp_path, monkeypatch):
        def quota(client, jpeg):
            raise QuotaError()

        monkeypatch.setattr(ocr_gemini, "ocr_page", quota)
        summary = ocr_gemini.run(three_page_pdf, tmp_path / "out")
        assert summary == {"done": 0, "failed": 0, "stopped_on_quota": True}


class TestOcrLighton:
    @pytest.fixture
    def ocr_lighton(self, monkeypatch):
        pytest.importorskip("pypdf")
        from pipeline import run_ocr_lighton
        monkeypatch.setenv("LIGHTON_API_KEY", "dummy")
        monkeypatch.setattr(run_ocr_lighton, "slice_page", lambda reader, i: b"%PDF")
        return run_ocr_lighton

    def test_page_failure_is_recorded_and_the_book_continues(self, ocr_lighton, three_page_pdf,
                                                             tmp_path, monkeypatch):
        def fake_parse(pdf_bytes, key, name):
            if name == "p002.pdf":
                raise ValueError("job status failed")
            return "كلمة"

        monkeypatch.setattr(ocr_lighton, "parse_page", fake_parse)
        out = tmp_path / "out"
        summary = ocr_lighton.run(three_page_pdf, out)

        assert summary == {"done": 2, "failed": 1, "stopped_on_quota": False}
        assert "2" in read_failures(out)


# ---------------------------------------------------------------- run_tashkel

class TestTashkel:
    def test_model_crash_on_one_page_does_not_stop_the_book(self, tmp_path, monkeypatch):
        stub = types.ModuleType("diacritize")
        stub.Diacritizer = types.SimpleNamespace(from_pretrained=object)
        monkeypatch.setitem(sys.modules, "diacritize", stub)
        monkeypatch.delitem(sys.modules, "pipeline.tashkel", raising=False)
        monkeypatch.delitem(sys.modules, "pipeline.run_tashkel", raising=False)
        import importlib
        run_tashkel = importlib.import_module("pipeline.run_tashkel")

        def fake_diacritize(tokens):
            if tokens[0]["id"].startswith("p002"):
                raise RuntimeError("model OOM")
            return tokens, {"diacritized": len(tokens), "mismatch": False, "preserved": True}

        monkeypatch.setattr(run_tashkel, "diacritize_tokens", fake_diacritize)
        book = tmp_path / "book"
        for n in (1, 2, 3):
            write_page(book, n, [make_token(f"p{n:03d}w000", "كلمة")],
                       status="extracted", path="diacritise")

        run_tashkel.run(book)

        assert [read_page(book, n)["status"] for n in (1, 2, 3)] == \
            ["diacritised", "extracted", "diacritised"]
        assert read_failures(book)["2"]["diacritise"]["message"] == "model OOM"


def test_asyncio_is_left_clean():
    """The hume driver runs its own event loop via asyncio.run; nothing above
    should leave one running."""
    with pytest.raises(RuntimeError):
        asyncio.get_running_loop()
