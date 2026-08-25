"""DECISION D-001a / SPEC 2.6.1 -- the Sunday stub D1 bar.

The defect this guards against: the market opens Sunday 21:00 UTC and the UTC day
boundary is Monday 00:00, so a naive resampler emits a three-hour "Sunday" D1 bar
whose high and low become Monday's PDH/PDL -- one of the most heavily used liquidity
sources in the model, wrong every single week.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from bot.config.loader import load_config
from bot.data.calendar import UTC
from bot.data.resample import resample


@pytest.fixture(scope="module")
def standalone_cfg():
    c, _ = load_config(overrides={"tf": {"sunday_handling": "standalone_incomplete"}})
    return c


def test_merged_monday_bar_starts_sunday_evening(cfg, m15_quarter):
    d1 = resample(m15_quarter, "D1", cfg)
    merged = [i for i in range(d1.n) if d1.flag("merged_stub")[i]]
    assert merged, "no bar was merged"
    for i in merged:
        assert d1.open_dt(i).weekday() == 6  # opens Sunday
        assert d1.open_dt(i).hour == 21
        assert d1.close_dt(i).weekday() == 1  # closes at Tuesday's boundary
        assert d1.close_dt(i).hour == 0


def test_merged_bar_is_fully_covered(cfg, m15_quarter):
    """Coverage is measured over the bar's real span, so a merged bar reads 1.00.

    Using the nominal 24-hour bucket as the denominator would report 112% and make
    every downstream coverage threshold meaningless.
    """
    d1 = resample(m15_quarter, "D1", cfg)
    for i in range(d1.n):
        if d1.flag("merged_stub")[i]:
            assert d1.expected_bars[i] == 108  # 27 hours of M15
            assert d1.coverage[i] == pytest.approx(1.0)


def test_no_three_hour_sunday_bar_survives(cfg, m15_quarter):
    """The defect itself: no D1 bar may be a Sunday stub."""
    d1 = resample(m15_quarter, "D1", cfg)
    for i in range(d1.n):
        span_h = (int(d1.close_time[i]) - int(d1.open_time[i])) / 3600
        assert span_h >= 20, (d1.open_dt(i), span_h)


def test_pdh_pdl_for_tuesday_comes_from_a_full_monday(cfg, m15_quarter):
    """The consequence that matters: Tuesday's previous-day levels are real.

    Compares the merged Monday bar against the raw Sunday-evening range it absorbed.
    A Sunday stub would give Tuesday a previous-day range measured over three hours.
    """
    d1 = resample(m15_quarter, "D1", cfg)
    i = next(i for i in range(d1.n) if d1.flag("merged_stub")[i])

    sunday_only = m15_quarter.slice_between(
        int(d1.open_time[i]), int(d1.open_time[i]) + 3 * 3600
    )
    assert sunday_only.n == 12
    stub_range = sunday_only.high.max() - sunday_only.low.min()
    full_range = d1.high[i] - d1.low[i]
    assert full_range > stub_range
    assert d1.high[i] >= sunday_only.high.max()
    assert d1.low[i] <= sunday_only.low.min()


def test_standalone_mode_emits_the_stub_and_flags_it(standalone_cfg, m15_quarter):
    """The ablation alternative: keep the stub, let coverage expose it."""
    d1 = resample(m15_quarter, "D1", standalone_cfg)
    sundays = [i for i in range(d1.n) if d1.open_dt(i).weekday() == 6]
    assert sundays, "standalone mode must emit the Sunday bucket"
    for i in sundays:
        assert d1.coverage[i] == pytest.approx(12 / 96)
        assert d1.flag("low_coverage")[i]
        assert not d1.flag("merged_stub")[i]


def test_merging_does_not_lose_or_duplicate_any_source_bar(cfg, m15_quarter):
    """Every M15 bar must land in exactly one D1 bar."""
    d1 = resample(m15_quarter, "D1", cfg)
    total = int(d1.bar_count.sum())
    # Bars after the final emitted D1 close are still forming, not lost.
    tail = int((m15_quarter.open_time >= d1.close_time[-1]).sum())
    assert total + tail == m15_quarter.n


def test_a_short_holiday_monday_is_not_merged(cfg):
    """Only the week's *first* bucket merges.  A thin Monday is a real trading day.

    Merging on coverage alone would silently fuse a holiday Monday into Tuesday and
    destroy Tuesday's previous-day levels in exactly the weeks a clinic-grade dataset
    is already least reliable.
    """
    from datetime import date

    from bot.data.synthetic import generate

    src = generate(
        "EURUSD",
        datetime(2026, 2, 1, tzinfo=UTC),
        datetime(2026, 2, 28, tzinfo=UTC),
        cfg,
        timeframe="M15",
        drop_ranges=[
            (datetime(2026, 2, 10, 0, 0, tzinfo=UTC), datetime(2026, 2, 10, 21, 0, tzinfo=UTC))
        ],
    )
    d1 = resample(src, "D1", cfg)
    tue = next(i for i in range(d1.n) if d1.open_dt(i).date() == date(2026, 2, 10))
    assert d1.coverage[tue] < 0.25  # would qualify on coverage alone
    assert not d1.flag("merged_stub")[tue]  # but it is not the week's first bucket
    assert d1.flag("low_coverage")[tue]  # it is reported instead
