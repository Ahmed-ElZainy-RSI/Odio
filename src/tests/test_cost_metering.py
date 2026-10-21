"""Spec 025: paid calls are recorded where they happen, and priced by tools/cost_report.py.

Provider clients are faked; the assertions are on what lands in usage.jsonl.
"""
import asyncio
import json
import sys
import types
from pathlib import Path

import pytest

from pipeline import usage

REPO_ROOT = Path(__file__).resolve().parents[2]


def log(work_dir):
    path = work_dir / "usage.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] \
        if path.exists() else []


def gemini_response(text="[]", prompt=1200, candidates=80, thoughts=20, audio=None):
    meta = types.SimpleNamespace(prompt_token_count=prompt, candidates_token_count=candidates,
                                 thoughts_token_count=thoughts)
    parts = [types.SimpleNamespace(inline_data=types.SimpleNamespace(data=audio))] if audio else []
    content = types.SimpleNamespace(parts=parts)
    return types.SimpleNamespace(text=text, usage_metadata=meta,
                                 candidates=[types.SimpleNamespace(content=content)])


class FakeGemini:
    def __init__(self, *responses):
        self.models = self
        self._responses = list(responses)

    def generate_content(self, **kwargs):
        r = self._responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class TestRecordingScope:
    def test_nothing_is_written_outside_a_scope(self, tmp_path):
        assert usage.record("gemini", "m", "ocr", input_tokens=1) is None
        assert list(tmp_path.iterdir()) == []

    def test_scope_tags_stage_and_page(self, tmp_path):
        with usage.recording(tmp_path, "ocr-gemini"), usage.page(7):
            usage.record("gemini", "m", "ocr", input_tokens=10)
        [line] = log(tmp_path)
        assert (line["stage"], line["page"], line["input_tokens"]) == ("ocr-gemini", 7, 10)
        assert "at" in line

    def test_scope_is_restored_after_an_exception(self, tmp_path):
        with pytest.raises(RuntimeError), usage.recording(tmp_path, "x"):
            raise RuntimeError
        assert usage.record("gemini", "m", "ocr") is None


class TestProviders:
    def test_gemini_ocr_records_every_billed_response_even_unparseable(self, tmp_path, monkeypatch):
        pytest.importorskip("google.genai")
        from pipeline import ocr_gemini
        monkeypatch.setattr(ocr_gemini.time, "sleep", lambda s: None)
        client = FakeGemini(gemini_response(text="not json"), gemini_response(text="[]"))

        with usage.recording(tmp_path, "ocr-gemini"), usage.page(3):
            assert ocr_gemini.ocr_page(client, b"jpeg") == []

        lines = log(tmp_path)
        assert len(lines) == 2, "the unparseable first response was billed too"
        assert lines[0]["input_tokens"] == 1200
        assert lines[0]["output_tokens"] == 100, "thinking tokens bill as output"
        assert lines[0]["images"] == 1 and lines[0]["model"] == ocr_gemini.MODEL

    def test_a_refused_call_is_not_recorded(self, tmp_path, monkeypatch):
        pytest.importorskip("google.genai")
        from pipeline import ocr_gemini
        monkeypatch.setattr(ocr_gemini.time, "sleep", lambda s: None)
        client = FakeGemini(RuntimeError("429 RESOURCE_EXHAUSTED"), gemini_response())
        with usage.recording(tmp_path, "ocr-gemini"):
            ocr_gemini.ocr_page(client, b"jpeg")
        assert len(log(tmp_path)) == 1

    def test_gemini_tts_records_chars_tokens_and_audio(self, tmp_path):
        pytest.importorskip("google.genai")
        from pipeline import tts_gemini
        pcm = b"\x00\x00" * 24_000  # 1 s at 24 kHz mono 16-bit
        with usage.recording(tmp_path, "tts-gemini"):
            tts_gemini.synthesize(FakeGemini(gemini_response(audio=pcm)), "قوس قزح", "Kore")
        [line] = log(tmp_path)
        assert (line["operation"], line["chars"], line["audio_ms"]) == ("tts", 7, 1000)
        assert line["model"] == tts_gemini.MODEL

    def test_lighton_records_one_page_per_successful_parse(self, tmp_path, monkeypatch):
        pytest.importorskip("pypdf")
        from pipeline import ocr_lighton

        class Response:
            status_code = 200
            def json(self):
                return {"result": {"pages": [{"markdown": "كلمة"}]}}

        monkeypatch.setattr(ocr_lighton.requests, "post", lambda *a, **k: Response())
        with usage.recording(tmp_path, "ocr-lighton"):
            ocr_lighton.parse_page(b"%PDF", "key")
        [line] = log(tmp_path)
        assert (line["provider"], line["model"], line["pages"]) == ("lighton", "lighton/parse-v3", 1)

    def test_hume_records_chars_and_audio(self, tmp_path, monkeypatch):
        if "hume" not in sys.modules:
            try:
                import hume.tts  # noqa: F401
            except ImportError:
                pytest.skip("hume SDK not installed")
        from pipeline import tts_hume
        gen = types.SimpleNamespace(audio="", duration=2.5, snippets=[])

        class Client:
            class tts:
                @staticmethod
                async def synthesize_json(**kwargs):
                    return types.SimpleNamespace(generations=[gen])

        with usage.recording(tmp_path, "tts-hume"):
            asyncio.run(tts_hume.synthesize(Client(), "نص", "v"))
        [line] = log(tmp_path)
        assert (line["provider"], line["chars"], line["audio_ms"]) == ("hume", 2, 2500)

    def test_openrouter_units_take_the_reported_cost(self):
        units = usage.openrouter_units(
            {"usage": {"prompt_tokens": 900, "completion_tokens": 50, "cost": 0.0042}})
        assert units == {"input_tokens": 900, "output_tokens": 50, "reported_cost_usd": 0.0042}


class TestCostReport:
    def write(self, work_dir, *records):
        with usage.recording(work_dir, "ocr-gemini"):
            for page, kw in records:
                with usage.page(page):
                    usage.record(**kw)

    def test_prices_with_the_shipped_table(self, tmp_path, capsys):
        import cost_report
        self.write(
            tmp_path,
            (1, {"provider": "gemini", "model": "gemini-3.6-flash", "operation": "ocr",
                 "input_tokens": 1_000_000, "output_tokens": 0}),
            (2, {"provider": "lighton", "model": "lighton/parse-v3", "operation": "ocr",
                 "pages": 1}),
        )
        report = cost_report.run([tmp_path], by=("page",))

        assert report["calls"] == 2
        assert report["unpriced_calls"] == 1
        assert report["usd"] > 0
        assert "lower bound" in capsys.readouterr().out

    def test_empty_book(self, tmp_path, capsys):
        import cost_report
        assert cost_report.run([tmp_path])["calls"] == 0
        assert "no usage recorded" in capsys.readouterr().out


def test_pipeline_copy_matches_the_service_domain_module():
    service = REPO_ROOT / "service/ai_engine/domain/usage.py"
    assert (REPO_ROOT / "src/pipeline/usage_model.py").read_bytes() == service.read_bytes()
