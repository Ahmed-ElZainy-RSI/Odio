"""timing.py: per-step wall-clock rows in timings.jsonl, and the run summary."""
import pytest

from pipeline import timing


def test_step_outside_a_run_measures_but_writes_nothing(tmp_path):
    with timing.step("ocr") as t:
        pass
    assert t.seconds >= 0
    assert not (tmp_path / timing.LOG_NAME).exists()


def test_steps_are_logged_with_run_stage_and_page(tmp_path):
    with timing.run(tmp_path, "r1"), timing.stage("ocr-gemini"):
        for pg in (1, 2):
            with timing.page(pg):
                with timing.step("ocr"):
                    pass
                with timing.step("page", log=False):  # wrapper: printed, not logged
                    pass
    rows = timing.load(tmp_path, "r1")
    assert [(r["stage"], r["page"], r["step"], r["ok"]) for r in rows] == [
        ("ocr-gemini", 1, "ocr", True), ("ocr-gemini", 2, "ocr", True)]


def test_a_failed_step_is_still_logged(tmp_path):
    with timing.run(tmp_path, "r1"):
        with pytest.raises(RuntimeError):
            with timing.step("tts"):
                raise RuntimeError("boom")
    (row,) = timing.load(tmp_path, "r1")
    assert row["step"] == "tts" and row["ok"] is False


def test_summary_covers_only_its_own_run(tmp_path):
    for run_id in ("old", "new"):
        with timing.run(tmp_path, run_id), timing.stage("tts-gemini"), timing.page(3):
            with timing.step("tts"):
                pass
    text = timing.summary(tmp_path, "new", total_seconds=125)
    assert "1 pages timed" in text
    assert "book total (wall clock): 2m05s" in text


@pytest.mark.parametrize("secs,want", [(4.21, "4.2s"), (187, "3m07s"), (3720, "1h02m")])
def test_fmt(secs, want):
    assert timing.fmt(secs) == want


def test_latest_by_page_keeps_each_page_from_the_run_that_last_made_it(tmp_path):
    # run 1 times pages 1 and 2; run 2 (a resume) redoes only page 2's TTS
    with timing.run(tmp_path, "r1"), timing.page(1):
        timing._write("ocr", 5.0, True)
    with timing.run(tmp_path, "r1"), timing.page(2):
        timing._write("ocr", 6.0, True)
        timing._write("tts", 9.0, True)
    with timing.run(tmp_path, "r2"), timing.page(2):
        timing._write("tts", 4.0, True)
        timing._write("tts", 1.0, False)  # a failed attempt is not the page's time
    assert timing.latest_by_page(tmp_path) == {1: {"ocr": 5.0}, 2: {"ocr": 6.0, "tts": 4.0}}


def test_book_total_is_the_latest_and_kept_out_of_the_step_summary(tmp_path):
    for run_id, secs in (("r1", 100.0), ("r2", 250.0)):
        with timing.run(tmp_path, run_id):
            timing.record_book_total(secs)
    assert timing.latest_book_total(tmp_path) == 250.0
    assert timing.BOOK_TOTAL_STEP not in timing.summary(tmp_path, "r2")
