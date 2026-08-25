"""Session engine (SPEC 3.4 / 3.5 / 3.6)."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime

import numpy as np
import pytest

from bot.core.sessions import SessionStatus, build_sessions
from bot.data.calendar import UTC, is_dst_desync
from bot.data.resample import resample
from bot.data.synthetic import generate


def test_h4_bars_are_refused_as_a_session_source(cfg, m15_quarter):
    """SPEC 3.6.  One H4 bar straddles a session boundary, so its high may belong to
    either side.  D-002 moved *confirmation* to H4; it did not move measurement."""
    h4 = resample(m15_quarter, "H4", cfg)
    with pytest.raises(ValueError, match="M15"):
        build_sessions(h4, cfg)


def test_extremes_match_the_underlying_bars(cfg, m15_quarter):
    sessions = build_sessions(m15_quarter, cfg)
    assert sessions
    for s in sessions[:80]:
        seg = m15_quarter.slice_between(
            int(s.start_utc.timestamp()), int(s.end_utc.timestamp())
        )
        assert seg.n == s.bar_count
        assert s.high == seg.high.max()
        assert s.low == seg.low.min()
        assert s.open == seg.open[0]
        assert s.close == seg.close[-1]
        assert s.low <= s.close <= s.high
        assert s.range >= 0


def test_extreme_timestamps_point_at_the_extreme_bar(cfg, m15_quarter):
    sessions = build_sessions(m15_quarter, cfg)
    for s in sessions[:40]:
        seg = m15_quarter.slice_between(
            int(s.start_utc.timestamp()), int(s.end_utc.timestamp())
        )
        hi = seg.open_time[int(np.argmax(seg.high))]
        lo = seg.open_time[int(np.argmin(seg.low))]
        assert int(s.high_ts.timestamp()) == hi
        assert int(s.low_ts.timestamp()) == lo
        assert s.start_utc <= s.high_ts < s.end_utc
        assert s.start_utc <= s.low_ts < s.end_utc


def test_forming_sessions_are_not_liquidity(cfg, m15_quarter):
    """SPEC 3.5.  A running extreme cannot be swept by the price action creating it."""
    sessions = build_sessions(m15_quarter, cfg)
    forming = [s for s in sessions if s.status is SessionStatus.FORMING]
    assert all(not s.is_liquidity_source for s in forming)
    assert all(s.end_utc > datetime.fromtimestamp(int(m15_quarter.close_time[-1]), UTC) for s in forming)


def test_a_thin_session_is_incomplete_and_not_liquidity(cfg):
    """A half-holiday is detected from bar coverage, not a hard-coded calendar."""
    src = generate(
        "EURUSD",
        datetime(2026, 2, 1, tzinfo=UTC),
        datetime(2026, 2, 28, tzinfo=UTC),
        cfg,
        timeframe="M15",
        # London closes early: drop 11:00-16:30 UTC on one day.
        drop_ranges=[(datetime(2026, 2, 11, 11, 0, tzinfo=UTC), datetime(2026, 2, 11, 16, 30, tzinfo=UTC))],
    )
    sessions = build_sessions(src, cfg)
    ldn = next(
        s for s in sessions if s.session_name == "LONDON" and s.trading_date == date(2026, 2, 11)
    )
    assert ldn.coverage < cfg.session.min_bar_coverage
    assert ldn.status is SessionStatus.INCOMPLETE
    assert not ldn.is_liquidity_source


def test_a_full_holiday_produces_no_instance_at_all(cfg):
    """An instance with no prices would be a lie, so none is built.

    The absence is counted by ``quality.session_coverage`` instead -- see
    ``test_quality.py`` -- which is what keeps a silently-dropped holiday visible.
    """
    src = generate(
        "EURUSD",
        datetime(2026, 2, 1, tzinfo=UTC),
        datetime(2026, 2, 28, tzinfo=UTC),
        cfg,
        timeframe="M15",
        holidays=[date(2026, 2, 12)],
    )
    sessions = build_sessions(src, cfg)
    assert not [
        s for s in sessions if s.session_name == "LONDON" and s.trading_date == date(2026, 2, 12)
    ]


def test_overlap_is_derived_and_tagged_for_desync(cfg, m15_year):
    sessions = build_sessions(m15_year, cfg)
    ov = [s for s in sessions if s.session_name == "OVERLAP"]
    assert ov
    durations = Counter(round((s.end_utc - s.start_utc).total_seconds() / 3600, 2) for s in ov)
    assert set(durations) == {3.5, 4.5}
    for s in ov:
        widened = round((s.end_utc - s.start_utc).total_seconds() / 3600, 2) == 4.5
        assert widened == s.dst_desync == is_dst_desync(s.trading_date)


def test_disabled_sessions_are_excluded_by_default(cfg, m15_quarter):
    names = {s.session_name for s in build_sessions(m15_quarter, cfg)}
    assert "LONDON_KZ" not in names
    with_disabled = {s.session_name for s in build_sessions(m15_quarter, cfg, include_disabled=True)}
    assert "LONDON_KZ" in with_disabled


def test_sessions_are_returned_in_close_order(cfg, m15_quarter):
    sessions = build_sessions(m15_quarter, cfg)
    ends = [s.end_utc for s in sessions]
    assert ends == sorted(ends)


def test_asia_range_precedes_the_london_session_it_feeds(cfg, m15_quarter):
    """The flagship setup requires the Asian range to be closed before London opens."""
    sessions = build_sessions(m15_quarter, cfg)
    ar = {s.trading_date: s for s in sessions if s.session_name == "ASIA_RANGE"}
    ldn = {s.trading_date: s for s in sessions if s.session_name == "LONDON"}
    shared = set(ar) & set(ldn)
    assert len(shared) > 30
    for d in shared:
        assert ar[d].end_utc <= ldn[d].start_utc
