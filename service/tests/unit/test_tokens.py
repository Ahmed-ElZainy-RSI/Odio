"""Token and timing value objects."""

from __future__ import annotations

import pytest

from ai_engine.domain.enums import TimingSource
from ai_engine.domain.tokens import (
    BindReport,
    TimedToken,
    Token,
    is_monotonic,
    token_id,
)


def test_token_id_format_matches_existing_artifacts() -> None:
    """Ids must match the `p003w000` convention already on disk in 186 page records."""
    assert token_id(3, 0) == "p003w000"
    assert token_id(12, 44) == "p012w044"


def test_token_id_is_a_pure_function_of_page_and_index() -> None:
    """This purity is what lets Job B reproduce Job A's ids from the text file alone."""
    assert token_id(7, 5) == token_id(7, 5)


@pytest.mark.parametrize(("page", "index"), [(0, 0), (-1, 0), (1, -1)])
def test_token_id_rejects_out_of_range(page: int, index: int) -> None:
    with pytest.raises(ValueError):
        token_id(page, index)


def test_token_rejects_whitespace() -> None:
    """A token containing whitespace would make the Text artifact non-invertible.

    Since the artifact is a space-join, whitespace inside a token would break Job B's
    re-tokenization and silently shift every subsequent token id.
    """
    with pytest.raises(ValueError, match="whitespace"):
        Token(id="p001w000", text="two words")


def test_token_accepts_diacritized_arabic() -> None:
    token = Token(id="p003w001", text="الْعُصْفُورُ")
    assert token.text == "الْعُصْفُورُ"


def test_timed_token_requires_a_source() -> None:
    with pytest.raises(ValueError, match="source"):
        TimedToken(id="p001w000", text="x", start_ms=0, end_ms=10)


def test_timed_token_rejects_inverted_window() -> None:
    with pytest.raises(ValueError, match="precedes"):
        TimedToken(id="p001w000", text="x", start_ms=500, end_ms=100, source=TimingSource.ANCHOR)


def test_timed_token_rejects_half_open_window() -> None:
    with pytest.raises(ValueError, match="half-open"):
        TimedToken(id="p001w000", text="x", start_ms=0, source=TimingSource.ANCHOR)


def test_untimed_token_has_no_source() -> None:
    """An untimed token is a legitimate state, not an error.

    7 of 3,873 real tokens on disk are in it, and a reader shows such a word
    unhighlighted rather than guessing.
    """
    token = TimedToken.untimed("p001w000", "x")
    assert not token.is_timed
    assert token.source is None


def test_untimed_token_must_not_declare_a_source() -> None:
    with pytest.raises(ValueError, match="untimed"):
        TimedToken(id="p001w000", text="x", source=TimingSource.ANCHOR)


def test_monotonic_detection() -> None:
    ok = [
        TimedToken("p1w0", "a", 0, 100, TimingSource.ANCHOR),
        TimedToken("p1w1", "b", 100, 200, TimingSource.INTERPOLATED),
    ]
    overlapping = [
        TimedToken("p1w0", "a", 0, 200, TimingSource.ANCHOR),
        TimedToken("p1w1", "b", 100, 300, TimingSource.ANCHOR),
    ]
    assert is_monotonic(ok)
    assert not is_monotonic(overlapping)


def test_untimed_tokens_do_not_break_monotonicity() -> None:
    tokens = [
        TimedToken("p1w0", "a", 0, 100, TimingSource.ANCHOR),
        TimedToken.untimed("p1w1", "b"),
        TimedToken("p1w2", "c", 100, 200, TimingSource.ANCHOR),
    ]
    assert is_monotonic(tokens)


def test_bind_report_anchor_ratio_matches_real_page() -> None:
    """Real values from work/فلفل المغرور-lighton/p003.json: 45 tokens, 40 anchored."""
    report = BindReport(tokens=45, anchored=40, interpolated=5, untimed=0, monotonic=True)
    assert report.anchor_ratio == pytest.approx(0.8889, abs=1e-4)


def test_bind_report_must_add_up() -> None:
    with pytest.raises(ValueError, match="does not add up"):
        BindReport(tokens=10, anchored=5, interpolated=2, untimed=1, monotonic=True)


def test_bind_report_from_tokens() -> None:
    tokens = [
        TimedToken("p1w0", "a", 0, 100, TimingSource.ANCHOR),
        TimedToken("p1w1", "b", 100, 200, TimingSource.INTERPOLATED),
        TimedToken.untimed("p1w2", "c"),
    ]
    report = BindReport.from_tokens(tokens)
    assert (report.tokens, report.anchored, report.interpolated, report.untimed) == (3, 1, 1, 1)
    assert report.monotonic


def test_bind_report_of_empty_page_is_zero_not_a_crash() -> None:
    report = BindReport.from_tokens([])
    assert report.anchor_ratio == 0.0
