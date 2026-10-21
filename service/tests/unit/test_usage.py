"""Spec 025: usage records, the price table, and turning one into the other."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_engine.domain.usage import PriceTable, UsageRecord, cost_of, summarize

REPO_ROOT = Path(__file__).resolve().parents[3]
PRICES = PriceTable.from_toml((REPO_ROOT / "config/prices.toml").read_text(encoding="utf-8"))

TABLE = PriceTable.from_toml(
    """
as_of = "2026-10-21"
[[price]]
provider = "gemini"
model = "flash"
input_per_1m_tokens = 1.0
output_per_1m_tokens = 4.0
valid_until = 2026-12-31
[[price]]
provider = "gemini"
model = "flash"
input_per_1m_tokens = 2.0
output_per_1m_tokens = 8.0
valid_from = 2027-01-01
[[price]]
provider = "lighton"
model = "parse"
per_page = 0.01
"""
)


def rec(**kw) -> UsageRecord:
    base = {
        "provider": "gemini",
        "model": "flash",
        "operation": "ocr",
        "at": "2026-10-21T10:00:00+00:00",
    }
    return UsageRecord(**(base | kw))


def test_token_pricing() -> None:
    assert cost_of(rec(input_tokens=2_000_000, output_tokens=500_000), TABLE) == pytest.approx(4.0)


def test_effective_dates_pick_the_right_rate() -> None:
    before = rec(input_tokens=1_000_000, at="2026-12-31T23:59:00+00:00")
    after = rec(input_tokens=1_000_000, at="2027-01-01T00:00:00+00:00")
    assert cost_of(before, TABLE) == pytest.approx(1.0)
    assert cost_of(after, TABLE) == pytest.approx(2.0)


def test_per_page_pricing() -> None:
    assert cost_of(rec(provider="lighton", model="parse", pages=3), TABLE) == pytest.approx(0.03)


def test_unknown_model_is_unpriced_not_free() -> None:
    assert cost_of(rec(model="mystery", input_tokens=10), TABLE) is None


def test_reported_cost_wins() -> None:
    assert cost_of(rec(model="mystery", reported_cost_usd=0.0123), TABLE) == 0.0123


def test_cache_hit_costs_nothing() -> None:
    assert cost_of(rec(input_tokens=1_000_000, cached=True), TABLE) == 0.0


def test_summary_counts_unpriced_calls_separately() -> None:
    lines = summarize(
        [
            rec(input_tokens=1_000_000, page=1),
            rec(model="mystery", page=1),
            rec(input_tokens=1_000_000, cached=True, page=2),
        ],
        TABLE,
        by=("page",),
    )
    assert lines[(1,)].calls == 2
    assert lines[(1,)].unpriced == 1
    assert lines[(1,)].usd == pytest.approx(1.0)
    assert lines[(2,)].cached == 1
    assert lines[(2,)].usd == 0.0


def test_record_round_trips_and_omits_empty_units() -> None:
    record = rec(page=4, input_tokens=120, output_tokens=30)
    data = record.to_dict()
    assert "audio_ms" not in data and "reported_cost_usd" not in data
    assert UsageRecord.from_dict(data) == record


def test_unknown_price_field_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown price fields"):
        PriceTable.from_toml('[[price]]\nprovider="g"\nmodel="m"\nper_token=1.0\n')


# ── the real config/prices.toml ─────────────────────────────────────────────────


def test_the_shipped_price_table_parses_and_prices_the_models_we_use() -> None:
    ocr = UsageRecord(
        provider="gemini",
        model="gemini-3.6-flash",
        operation="ocr",
        at="2026-10-21T10:00:00+00:00",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    assert cost_of(ocr, PRICES) == pytest.approx(0.75 + 3.75)
    next_year = UsageRecord.from_dict(ocr.to_dict() | {"at": "2027-02-01T00:00:00+00:00"})
    assert cost_of(next_year, PRICES) == pytest.approx(1.50 + 7.50)
    tts = UsageRecord(
        provider="gemini",
        model="gemini-3.1-flash-tts-preview",
        operation="tts",
        at="2026-10-21T10:00:00+00:00",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    assert cost_of(tts, PRICES) == pytest.approx(21.0)


def test_plan_dependent_providers_are_reported_unpriced() -> None:
    lighton = UsageRecord(provider="lighton", model="lighton/parse-v3", operation="ocr", pages=1)
    assert cost_of(lighton, PRICES) is None
