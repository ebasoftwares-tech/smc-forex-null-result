"""Causality and non-repainting (SPEC 1.2 / 25).

This is the Phase 1 instance of the replay test that SPEC 25.2 requires of every
engine.  Its shape: **resampling is prefix-stable.**  Truncating the source can only
remove bars from the end, and a bar that has been emitted never changes afterwards.

Code review does not reliably catch a lookahead bug, and a suspiciously good equity
curve is a very late signal.  This does catch it, cheaply, on every commit.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from bot.core.bars import build_series, from_epoch_s, to_epoch_s
from bot.data.calendar import UTC
from bot.data.resample import DERIVED_TIMEFRAMES, resample

_COMPARED = ("open_time", "close_time", "open", "high", "low", "close", "volume", "bar_count", "expected_bars")


def test_as_of_returns_only_closed_bars(cfg, m15_quarter):
    rng = np.random.default_rng(11)
    for t in rng.choice(m15_quarter.open_time, size=50, replace=False):
        view = m15_quarter.as_of(int(t))
        assert np.all(view.close_time <= int(t))
        if view.n < m15_quarter.n:
            assert m15_quarter.close_time[view.n] > int(t)


def test_as_of_never_reveals_the_forming_bar(cfg, m15_quarter):
    """A bar is invisible until it closes, even one microsecond before."""
    i = 500
    just_before = int(m15_quarter.close_time[i]) - 1
    assert m15_quarter.as_of(just_before).n == i
    assert m15_quarter.as_of(int(m15_quarter.close_time[i])).n == i + 1


@pytest.mark.parametrize("tf", DERIVED_TIMEFRAMES)
def test_resampling_is_prefix_stable(cfg, m15_quarter, tf):
    """SPEC 25.2 applied to the resampler.

    For 60 random cut points, resampling the truncated source must reproduce the
    leading bars of the full-history resample exactly.  Any use of a future bar makes
    these diverge, because the truncated engine cannot see what leaked.
    """
    full = resample(m15_quarter, tf, cfg)
    rng = np.random.default_rng(5)
    cuts = rng.choice(m15_quarter.close_time, size=60, replace=False)
    for t in cuts:
        trunc = resample(m15_quarter.as_of(int(t)), tf, cfg)
        assert trunc.n <= full.n, (tf, from_epoch_s(int(t)))
        head = full.head(trunc.n)
        for name in _COMPARED:
            assert np.array_equal(getattr(head, name), getattr(trunc, name)), (
                tf,
                name,
                from_epoch_s(int(t)),
            )
        for flag in trunc.flags:
            assert np.array_equal(head.flag(flag), trunc.flag(flag)), (tf, flag)


def test_prefix_stability_holds_across_the_dst_transitions(cfg, m15_year):
    """Cut points concentrated on the transition weekends, where bucket maths shifts."""
    interesting = [
        datetime(2026, 3, 8, h, tzinfo=UTC) for h in range(0, 24, 3)
    ] + [datetime(2026, 3, 29, h, tzinfo=UTC) for h in range(0, 24, 3)] + [
        datetime(2026, 10, 25, h, tzinfo=UTC) for h in range(0, 24, 3)
    ] + [datetime(2026, 11, 1, h, tzinfo=UTC) for h in range(0, 24, 3)]

    for tf in ("H4", "D1", "W1"):
        full = resample(m15_year, tf, cfg)
        for ts in interesting:
            trunc = resample(m15_year.as_of(ts), tf, cfg)
            head = full.head(trunc.n)
            for name in _COMPARED:
                assert np.array_equal(getattr(head, name), getattr(trunc, name)), (tf, name, ts)


def test_shifted_data_changes_results_only_marginally(cfg, m15_quarter):
    """SPEC 25.3.  An off-by-one in bucket assignment survives the prefix test but
    not this one: dropping the first source bar must not move the bar grid."""
    shifted = m15_quarter.head(m15_quarter.n).slice_between(
        int(m15_quarter.open_time[1]), int(m15_quarter.close_time[-1]) + 1
    )
    a = resample(m15_quarter, "H4", cfg)
    b = resample(shifted, "H4", cfg)
    # The grids must coincide on every shared bucket.
    common = np.intersect1d(a.open_time, b.open_time)
    assert len(common) > 100
    ia = np.searchsorted(a.open_time, common)
    ib = np.searchsorted(b.open_time, common)
    assert np.array_equal(a.close_time[ia], b.close_time[ib])
    assert np.array_equal(a.high[ia], b.high[ib])
    # Only the very first bucket may differ, and only because it lost one bar.
    assert np.count_nonzero(a.low[ia] != b.low[ib]) <= 1


def test_bar_series_rejects_overlapping_or_unordered_bars():
    t = np.array([0, 60, 120], dtype=np.int64)
    ones = np.ones(3)
    with pytest.raises(ValueError):
        build_series("X", "M1", t[::-1], t[::-1] + 60, ones, ones, ones, ones, ones)
    with pytest.raises(ValueError):
        build_series("X", "M1", t, t + 120, ones, ones, ones, ones, ones)  # overlaps
    with pytest.raises(ValueError):
        build_series("X", "M1", t, t, ones, ones, ones, ones, ones)  # zero length


def test_naive_timestamps_are_refused():
    with pytest.raises(ValueError):
        to_epoch_s(datetime(2026, 1, 1))
