"""A cast reads a page: every word gets its speaker, and timings run on across the speakers."""
import json

import pytest

from pipeline import run_speak_cast as cast_run


def record(words):
    return {"book": "b", "source_pdf": "b.pdf", "pdf_sha256_16": "0" * 16, "page": 5, "source": "ocr",
            "box_level": "word", "diac_ratio": 1.0, "path": "keep", "status": "diacritised",
            "tokens": [{"id": f"p005w{i:03d}", "raw": w, "txt": w, "fixes": []} for i, w in enumerate(words)]}


CAST = {"roles": {"narrator": {"voice": "Puck", "style": "tell: "}, "mother": {"voice": "Sulafat"}},
        "lines": {"5": [[2, 4, "mother"]]}}


@pytest.fixture
def page(tmp_path, monkeypatch):
    sent = []

    def fake_synthesize(client, text, voice, style=""):
        sent.append((voice, style, text))
        return b"\0\0" * 24000, 1000.0  # every stretch lasts a second

    monkeypatch.setattr(cast_run, "synthesize", fake_synthesize)
    monkeypatch.setattr(cast_run, "align_words", lambda wav: [])
    path = tmp_path / "p005.json"
    path.write_text(json.dumps(record(["قال", "الراوي", "نعم", "يا", "ولد"]), ensure_ascii=False), encoding="utf-8")
    return path, sent


def test_the_narrator_reads_what_no_character_says():
    assert cast_run.stretches(10, [[2, 4, "mother"], [6, 8, "boy"]]) == [
        (0, 2, "narrator"), (2, 4, "mother"), (4, 6, "narrator"), (6, 8, "boy"), (8, 10, "narrator")]


def test_a_page_with_no_lines_is_all_narrator():
    assert cast_run.stretches(3, []) == [(0, 3, "narrator")]


@pytest.mark.parametrize("lines", [[[0, 9, "boy"]], [[3, 3, "boy"]], [[0, 2, "boy"], [1, 3, "mother"]]])
def test_lines_that_do_not_fit_stop_before_paying(lines):
    with pytest.raises(ValueError):
        cast_run.stretches(5, lines)


def test_each_speaker_is_asked_for_their_own_words(page):
    path, sent = page

    cast_run.do_page(None, path, path.parent / "audio", CAST)

    assert sent == [("Puck", "tell: ", "قال الراوي"), ("Sulafat", "", "نعم يا"), ("Puck", "tell: ", "ولد")]


def test_timings_run_on_across_speakers_and_every_word_has_one(page):
    path, _ = page

    cast_run.do_page(None, path, path.parent / "audio", CAST)

    rec = json.loads(path.read_text(encoding="utf-8"))
    assert all("t" in t for t in rec["tokens"])
    assert rec["tokens"][2]["t"][0] == 1000 + cast_run.PAUSE_MS  # the mother starts after the pause
    assert rec["tokens"][4]["t"][0] == 2000 + 2 * cast_run.PAUSE_MS
    assert rec["duration_ms"] == 3000 + 2 * cast_run.PAUSE_MS
    assert rec["bind_report"]["monotonic"] and rec["status"] == "spoken"
    assert [c[2:] for c in rec["cast"]] == [["narrator", "Puck"], ["mother", "Sulafat"], ["narrator", "Puck"]]


def test_a_word_is_timed_where_its_speaker_said_it(page, monkeypatch):
    path, _ = page
    monkeypatch.setattr(cast_run, "align_words", lambda wav: [{"text": "نعم", "b": 100, "e": 300}])

    cast_run.do_page(None, path, path.parent / "audio", CAST)

    tokens = json.loads(path.read_text(encoding="utf-8"))["tokens"]
    assert tokens[2]["t"] == [1000 + cast_run.PAUSE_MS + 100, 1000 + cast_run.PAUSE_MS + 300]
    assert tokens[2]["t_src"] == "anchor"


def test_the_audio_is_as_long_as_the_record_says(page):
    import wave
    path, _ = page

    cast_run.do_page(None, path, path.parent / "audio", CAST)

    with wave.open(str(path.parent / "audio" / "p005.wav")) as w:
        assert round(w.getnframes() / w.getframerate() * 1000) == json.loads(path.read_text(encoding="utf-8"))["duration_ms"]


def test_instructions_read_aloud_are_tried_again_then_fail_the_page_alone(page, monkeypatch, tmp_path):
    path, sent = page
    monkeypatch.setattr(cast_run, "align_words", lambda wav: [{"text": "x", "b": 0, "e": 1}] * 50)

    with pytest.raises(cast_run.NoAudioReturned):
        cast_run.do_page(None, path, path.parent / "audio", CAST)

    assert len(sent) == cast_run.MAX_ATTEMPTS
    assert not (path.parent / "audio" / "p005.wav").exists()
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "diacritised"
