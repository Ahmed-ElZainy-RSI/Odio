"""Spec 015: parallel books, and a paid-call limiter all processes share."""
import multiprocessing
import types
from pathlib import Path

import pytest

from pipeline import ratelimit
from pipeline.failures import classify_failure
from pipeline.ratelimit import QuotaPaused, RateLimiter, parse_limits

# ── limiter ─────────────────────────────────────────────────────────────────────

def test_parse_limits():
    assert parse_limits("gemini:m=10/86400, lighton=100/3600") == {
        "gemini:m": (10, 86400), "lighton": (100, 3600)}
    with pytest.raises(ValueError):
        parse_limits("gemini=ten/day")


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_window_is_enforced_then_resets(tmp_path):
    clock = Clock()
    limiter = RateLimiter(tmp_path / "rl.sqlite", {"gemini": (3, 3600)}, clock=clock)
    for _ in range(3):
        limiter.acquire("gemini", "m")
    with pytest.raises(QuotaPaused) as info:
        limiter.acquire("gemini", "m")
    assert 0 < info.value.retry_after <= 3600
    clock.t += 3600
    limiter.acquire("gemini", "m")  # new window


def test_the_most_specific_key_wins_and_others_are_unlimited(tmp_path):
    limiter = RateLimiter(tmp_path / "rl.sqlite",
                          {"gemini": (100, 60), "gemini:tts": (1, 60)}, clock=Clock())
    limiter.acquire("gemini", "tts")
    with pytest.raises(QuotaPaused):
        limiter.acquire("gemini", "tts")
    for _ in range(5):
        limiter.acquire("gemini", "flash")   # falls back to the provider-wide limit
        limiter.acquire("lighton", "parse")  # not configured: unlimited


def test_exhausted_limit_is_classified_as_quota():
    assert classify_failure(QuotaPaused("gemini", 10, 10, 60.0)) == "quota"


def _grab(db: str, n: int, results) -> None:
    limiter = RateLimiter(Path(db), {"gemini": (10, 3600)})
    granted = 0
    for _ in range(n):
        try:
            limiter.acquire("gemini", "m")
            granted += 1
        except QuotaPaused:
            pass
    results.put(granted)


def test_processes_racing_for_the_last_calls_cannot_overshoot(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    results = ctx.Queue()
    procs = [ctx.Process(target=_grab, args=(str(tmp_path / "rl.sqlite"), 5, results))
             for _ in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
    assert sum(results.get(timeout=5) for _ in procs) == 10


def test_module_acquire_is_a_no_op_until_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("PIPELINE_RATE_LIMITS", raising=False)
    for _ in range(50):
        ratelimit.acquire("gemini", "m")
    monkeypatch.setenv("PIPELINE_RATE_LIMITS", "gemini=1/3600")
    monkeypatch.setenv("PIPELINE_RATE_DB", str(tmp_path / "rl.sqlite"))
    ratelimit.acquire("gemini", "m")
    with pytest.raises(QuotaPaused):
        ratelimit.acquire("gemini", "m")


def test_an_exhausted_window_stops_ocr_before_any_paid_request(tmp_path, monkeypatch):
    pytest.importorskip("google.genai")
    pytest.importorskip("pymupdf")
    from pipeline import ocr_gemini
    monkeypatch.setenv("PIPELINE_RATE_LIMITS", f"gemini:{ocr_gemini.MODEL}=0/3600")
    monkeypatch.setenv("PIPELINE_RATE_DB", str(tmp_path / "rl.sqlite"))
    calls = []
    client = types.SimpleNamespace(models=types.SimpleNamespace(
        generate_content=lambda **k: calls.append(k)))
    with pytest.raises(QuotaPaused):
        ocr_gemini.ocr_page(client, b"jpeg")
    assert calls == [], "no request is sent, and the limit is not retried"


# ── batch ───────────────────────────────────────────────────────────────────────

def fake_runner(pdf, **options):
    """Top level, so the process pool can pickle it."""
    if pdf.stem == "broken":
        raise RuntimeError("bad PDF")
    return Path("work") / pdf.stem / "reader.html"


@pytest.fixture
def batch(monkeypatch):
    pytest.importorskip("pymupdf")  # batch -> main.py reads PDFs
    from pipeline import batch as module
    monkeypatch.setattr(module.pipeline_main, "has_text_layer", lambda p: "text" in p.stem)
    return module


@pytest.fixture
def folder(tmp_path, batch):
    for name in ("b-scanned", "a-text", "broken", "c-text"):
        (tmp_path / f"{name}.pdf").write_bytes(b"%PDF fake")
    return tmp_path


def test_text_layer_books_come_first(batch, folder):
    assert [p.stem for p in batch.discover(folder)] == ["a-text", "c-text", "b-scanned", "broken"]


def test_one_failing_book_does_not_stop_the_batch(batch, folder, capsys):
    results = batch.run(folder, workers=1, runner=fake_runner, pages="5")
    assert [(r["book"], r["ok"]) for r in results] == [
        ("a-text.pdf", True), ("c-text.pdf", True), ("b-scanned.pdf", True), ("broken.pdf", False)]
    assert "bad PDF" in results[-1]["error"]
    assert "3/4 book(s) finished" in capsys.readouterr().out


def test_a_process_pool_gives_the_same_results(batch, folder):
    serial = batch.run(folder, workers=1, runner=fake_runner)
    parallel = batch.run(folder, workers=3, runner=fake_runner)
    def strip(rs):
        return [(r["book"], r["ok"]) for r in rs]
    assert strip(parallel) == strip(serial)
