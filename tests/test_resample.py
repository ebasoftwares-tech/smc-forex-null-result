"""Timeframe construction (SPEC 2), including the D-001 UTC grid."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from bot.core.bars import from_epoch_s
from bot.data.calendar import UTC, DayBoundary
from bot.data.resample import DERIVED_TIMEFRAMES, ResampleError, resample


def test_h4_grid_is_fixed_at_utc_multiples_of_four(cfg, m15_quarter):
    """DECISION D-001: 00/04/08/12/16/20 UTC, year-round, no DST drift."""
    h4 = resample(m15_quarter, "H4", cfg)
    hours = {h4.open_dt(i).hour for i in range(h4.n)}
    assert hours <= {0, 4, 8, 12, 16, 20}
    assert all(h4.open_dt(i).minute == 0 for i in range(h4.n))


def test_h4_grid_does_not_move_across_the_dst_transitions(cfg, m15_year):
    h4 = resample(m15_year, "H4", cfg)
    for month in (1, 4, 7, 10):
        sel = [i for i in range(h4.n) if h4.open_dt(i).month == month]
        assert {h4.open_dt(i).hour for i in sel} <= {0, 4, 8, 12, 16, 20}


def test_ny_anchor_irregular_days_are_the_dst_sundays(cfg, ny_cfg, m15_year):
    """Under the ablation anchor the 23h/25h days are flagged -- and they are Sundays.

    Established while writing this test: daylight-saving transitions always fall on a
    Sunday, so under a New-York anchor the irregular trading days are always the
    (almost entirely non-trading) Sunday.  A 25-hour day therefore never produces its
    theoretical seventh H4 bucket on real FX data -- there is no price action in the
    hours that would fill it.  The practical impact of the NY anchor's irregular days
    is confined to the Sunday-open stub, which SPEC 2.6.1 already handles.
    """
    h4 = resample(m15_year, "H4", ny_cfg)
    b = DayBoundary(ny_cfg.tf.day_boundary_tz, ny_cfg.tf.day_boundary_time)
    irregular = h4.flag("irregular_day")
    assert irregular.any(), "the NY anchor must produce irregular days"

    days = {b.trading_date(h4.open_dt(i)) for i in range(h4.n) if irregular[i]}
    assert days, "no irregular day survived into the H4 series"
    assert all(d.weekday() == 6 for d in days), days
    assert {(d.month, d.day) for d in days} <= {(3, 8), (11, 1)}

    # And the UTC anchor -- the D-001 default -- never produces one at all.
    assert not resample(m15_year, "H4", cfg).flag("irregular_day").any()


@pytest.mark.parametrize("tf", DERIVED_TIMEFRAMES)
def test_ohlc_aggregation_is_exact(cfg, m15_quarter, tf):
    """Every derived bar must equal the aggregate of its own constituents."""
    out = resample(m15_quarter, tf, cfg)
    assert out.n > 0
    rng = np.random.default_rng(3)
    for i in rng.choice(out.n, size=min(25, out.n), replace=False):
        seg = m15_quarter.slice_between(int(out.open_time[i]), int(out.close_time[i]))
        assert seg.n == out.bar_count[i]
        assert out.open[i] == seg.open[0]
        assert out.close[i] == seg.close[-1]
        assert out.high[i] == seg.high.max()
        assert out.low[i] == seg.low.min()
        assert out.volume[i] == pytest.approx(seg.volume.sum())


def test_bars_never_overlap_and_are_ascending(cfg, m15_year):
    for tf in DERIVED_TIMEFRAMES:
        s = resample(m15_year, tf, cfg)
        assert np.all(np.diff(s.open_time) > 0), tf
        assert np.all(s.close_time > s.open_time), tf
        assert np.all(s.close_time[:-1] <= s.open_time[1:]), tf


def test_empty_buckets_produce_no_bar(cfg, m15_quarter):
    """SPEC 1.5: no forward fill.  A weekend simply has no D1 bars."""
    d1 = resample(m15_quarter, "D1", cfg)
    weekdays = {d1.close_dt(i).weekday() for i in range(d1.n)}
    # close_time is the following day boundary, so Fri closes on Sat (5) and the
    # merged Monday bar closes on Tue (1).  Sunday closes (weekday 6) would mean a
    # Saturday trading day existed.
    assert 6 not in weekdays


def test_weekly_bar_closes_on_saturday_not_sunday(cfg, m15_quarter):
    """A weekly bar is complete when the week's trading ends, not when the next opens."""
    w1 = resample(m15_quarter, "W1", cfg)
    assert w1.n > 0
    for i in range(w1.n):
        assert w1.close_dt(i).weekday() == 5  # Saturday
        assert w1.close_dt(i).hour == 0


def test_weekly_equals_the_aggregate_of_its_daily_bars(cfg, m15_quarter):
    """SPEC 2.3 defines W1 as the D1 bars of one trading week; prove the identity."""
    d1 = resample(m15_quarter, "D1", cfg)
    w1 = resample(m15_quarter, "W1", cfg)
    for i in range(1, w1.n):  # skip the dataset-edge partial week
        sub = d1.slice_between(int(w1.open_time[i]), int(w1.close_time[i]))
        if sub.n == 0:
            continue
        assert w1.high[i] == sub.high.max()
        assert w1.low[i] == sub.low.min()
        assert w1.open[i] == sub.open[0]
        assert w1.close[i] == sub.close[-1]


def test_monthly_buckets_follow_calendar_months(cfg, m15_year):
    mn1 = resample(m15_year, "MN1", cfg)
    # The fixture year's final M15 bar closes exactly at 2027-01-01T00:00, which is
    # December's bucket end, so December is closed and all twelve months are emitted.
    assert mn1.n == 12
    for i in range(mn1.n):
        assert mn1.open_dt(i).day == 1
        assert mn1.close_dt(i).day == 1
        assert mn1.close_dt(i) > mn1.open_dt(i)


def test_forming_bucket_is_never_emitted(cfg, m15_quarter):
    """SPEC 2.4.  The final bucket has not closed, so it must not appear."""
    for tf in DERIVED_TIMEFRAMES:
        s = resample(m15_quarter, tf, cfg)
        assert int(s.close_time[-1]) <= int(m15_quarter.close_time[-1]), tf


def test_coverage_and_flags_are_consistent(cfg, m15_year):
    for tf in DERIVED_TIMEFRAMES:
        s = resample(m15_year, tf, cfg)
        cov = s.coverage
        assert np.all(cov > 0), tf
        assert np.array_equal(s.flag("partial_bar"), cov < 0.999), tf
        assert np.array_equal(s.flag("low_coverage"), cov < cfg.tf.min_bar_coverage_warn), tf


def test_week_edge_h4_bars_have_the_coverage_the_spec_predicts(cfg, m15_year):
    """SPEC 2.6.2: Sunday's opening H4 bucket is 75% covered, Friday's closing one 25%."""
    h4 = resample(m15_year, "H4", cfg)
    sunday_open = [i for i in range(h4.n) if h4.open_dt(i).weekday() == 6 and h4.open_dt(i).hour == 20]
    friday_close = [i for i in range(h4.n) if h4.open_dt(i).weekday() == 4 and h4.open_dt(i).hour == 20]
    assert sunday_open and friday_close
    assert all(abs(h4.coverage[i] - 0.75) < 1e-9 for i in sunday_open)
    assert all(abs(h4.coverage[i] - 0.25) < 1e-9 for i in friday_close)
    # Friday's stub is below the warn threshold and must be tagged; Sunday's is not.
    assert all(h4.flag("low_coverage")[i] for i in friday_close)
    assert not any(h4.flag("low_coverage")[i] for i in sunday_open)


def test_resampling_from_m1_matches_resampling_via_m15(cfg, m1_month):
    """Aggregation must be associative: M1 -> H4 equals M1 -> M15 -> H4."""
    direct = resample(m1_month, "H4", cfg)
    via = resample(resample(m1_month, "M15", cfg), "H4", cfg)
    n = min(direct.n, via.n)
    assert n > 50
    for name in ("open_time", "close_time", "open", "high", "low", "close"):
        assert np.array_equal(getattr(direct, name)[:n], getattr(via, name)[:n]), name


def test_unknown_timeframe_is_refused(cfg, m15_quarter):
    with pytest.raises(ResampleError):
        resample(m15_quarter, "H2", cfg)
