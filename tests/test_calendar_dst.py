"""The DST fixture year -- the Phase 1 acceptance gate.

SPEC 3.1 / 3.3.  These tests exist because a fixed UTC offset for a session is wrong
for half the year *and still produces plausible-looking results*, which is the failure
mode no eyeball review catches.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta, timezone

import pytest

from bot.data.calendar import (
    UTC,
    BoundaryResolutionError,
    DayBoundary,
    is_dst_desync,
    london_ny_offset_hours,
    overlap_windows,
    session_windows,
    week_close_utc,
    week_start_utc,
)

YEAR = 2026
# Published transition dates for the fixture year.
US_DST_START = date(2026, 3, 8)  # second Sunday in March
US_DST_END = date(2026, 11, 1)  # first Sunday in November
EU_DST_START = date(2026, 3, 29)  # last Sunday in March
EU_DST_END = date(2026, 10, 25)  # last Sunday in October


def _all_days(year: int):
    d = date(year, 1, 1)
    while d <= date(year, 12, 31):
        yield d
        d += timedelta(days=1)


# ------------------------------------------------------------------ day boundary


def test_utc_day_boundary_is_always_24h(cfg):
    """DECISION D-001: with a UTC anchor the trading day never changes length."""
    b = DayBoundary(cfg.tf.day_boundary_tz, cfg.tf.day_boundary_time)
    lengths = {b.day_length_hours(d) for d in _all_days(YEAR)}
    assert lengths == {24.0}


def test_ny_anchor_has_23h_and_25h_days(ny_cfg):
    """The ablation anchor does change length -- the code must handle both."""
    b = DayBoundary(ny_cfg.tf.day_boundary_tz, ny_cfg.tf.day_boundary_time)
    lengths = Counter(b.day_length_hours(d) for d in _all_days(YEAR))
    assert lengths[23.0] == 1
    assert lengths[25.0] == 1
    assert lengths[24.0] == 363


def test_boundary_round_trips_every_day_of_the_year(cfg):
    b = DayBoundary(cfg.tf.day_boundary_tz, cfg.tf.day_boundary_time)
    for d in _all_days(YEAR):
        assert b.trading_date(b.boundary_utc(d)) == d
        assert b.trading_date(b.boundary_utc(d) + timedelta(hours=23, minutes=59)) == d


def test_nonexistent_local_boundary_is_an_error_not_a_silent_shift():
    """A 02:30 boundary does not exist on the US spring-forward date.

    Python would quietly move it.  A boundary that silently moves produces two
    different backtests from the same data, so it must fail loudly instead.
    """
    b = DayBoundary("America/New_York", time(2, 30))
    with pytest.raises(BoundaryResolutionError):
        b.boundary_utc(US_DST_START)


def test_ambiguous_local_boundary_is_an_error():
    """01:30 occurs twice on the US fall-back date."""
    b = DayBoundary("America/New_York", time(1, 30))
    with pytest.raises(BoundaryResolutionError):
        b.boundary_utc(US_DST_END)


# ------------------------------------------------------------------------- DST


def test_desync_windows_match_the_published_transition_dates():
    runs: list[tuple[date, date]] = []
    cur: list[date] | None = None
    for d in _all_days(YEAR):
        if is_dst_desync(d):
            cur = [d, d] if cur is None else [cur[0], d]
        elif cur:
            runs.append((cur[0], cur[1]))
            cur = None
    if cur:
        runs.append((cur[0], cur[1]))

    assert len(runs) == 2, runs
    # US moves first in spring: desync from the US date until the EU catches up.
    assert runs[0] == (US_DST_START + timedelta(days=1), EU_DST_START)
    # EU moves first in autumn: desync from the EU date until the US catches up.
    assert runs[1] == (EU_DST_END + timedelta(days=1), US_DST_END)


def test_london_ny_offset_is_five_hours_except_in_desync():
    for d in _all_days(YEAR):
        off = london_ny_offset_hours(d)
        assert off in (4.0, 5.0)
        assert (off == 4.0) == is_dst_desync(d)


# -------------------------------------------------------------------- sessions


def test_session_windows_track_dst_automatically(cfg):
    """London is 08:00-16:30 UTC in winter and 07:00-15:30 UTC in summer."""
    b = DayBoundary(cfg.tf.day_boundary_tz, cfg.tf.day_boundary_time)
    ldn = session_windows(cfg.session.window("LONDON"), b, date(YEAR, 1, 1), date(YEAR, 12, 31))
    by_date = {w.trading_date: w for w in ldn}
    assert by_date[date(YEAR, 1, 14)].start_utc.hour == 8
    assert by_date[date(YEAR, 7, 15)].start_utc.hour == 7
    # Every occurrence is well formed.
    assert all(w.start_utc < w.end_utc for w in ldn)
    assert all(abs(w.duration_hours - 8.5) < 1e-9 for w in ldn)


def test_overlap_is_3h30_normally_and_4h30_in_desync(cfg):
    """SPEC 3.7, corrected during Phase 1.

    v1.0 asserted {3h, 4h, 5h}.  With London 08:00-16:30 and New York 08:00-17:00 the
    intersection is 13:00-16:30 UTC in winter and 12:00-15:30 in summer -- 3.5h either
    way -- widening to 12:00-16:30 = 4.5h only while the US is on DST and the EU is not.
    """
    b = DayBoundary(cfg.tf.day_boundary_tz, cfg.tf.day_boundary_time)
    ldn = session_windows(cfg.session.window("LONDON"), b, date(YEAR, 1, 1), date(YEAR, 12, 31))
    nyk = session_windows(cfg.session.window("NEW_YORK"), b, date(YEAR, 1, 1), date(YEAR, 12, 31))
    ov = overlap_windows(ldn, nyk)

    durations = Counter(round(w.duration_hours, 2) for w in ov)
    assert set(durations) == {3.5, 4.5}
    assert all(w.duration_hours > 0 for w in ov)
    # Every widened overlap is a desync date, and every desync weekday is widened.
    for w in ov:
        assert (round(w.duration_hours, 2) == 4.5) == is_dst_desync(w.trading_date)


def test_asia_range_spans_midnight_and_is_attributed_to_the_day_it_ends_in(cfg):
    """SPEC 3.2.  Tuesday's Asian range must be available to Tuesday's London session."""
    b = DayBoundary(cfg.tf.day_boundary_tz, cfg.tf.day_boundary_time)
    ar = session_windows(cfg.session.window("ASIA_RANGE"), b, date(YEAR, 2, 1), date(YEAR, 2, 28))
    ldn = session_windows(cfg.session.window("LONDON"), b, date(YEAR, 2, 1), date(YEAR, 2, 28))
    ldn_by_date = {w.trading_date: w for w in ldn}

    assert ar, "no Asian ranges built"
    for w in ar:
        assert w.start_utc < w.end_utc
        assert w.trading_date == w.end_utc.date() or w.trading_date == (w.end_utc - timedelta(microseconds=1)).date()
        london = ldn_by_date.get(w.trading_date)
        if london is not None:
            # The range must be complete before the London session it feeds opens.
            assert w.end_utc <= london.start_utc


def test_asia_range_ends_inside_the_utc_day_under_d001(cfg):
    """Under D-001 the Asian range no longer closes on the day boundary (SPEC 3.2 note)."""
    b = DayBoundary(cfg.tf.day_boundary_tz, cfg.tf.day_boundary_time)
    w = session_windows(cfg.session.window("ASIA_RANGE"), b, date(YEAR, 2, 2), date(YEAR, 2, 2))[0]
    assert w.end_utc.hour == 5  # 00:00 New York in winter
    assert w.end_utc.time() != cfg.tf.day_boundary_time


def test_every_session_of_the_year_is_well_formed(cfg):
    b = DayBoundary(cfg.tf.day_boundary_tz, cfg.tf.day_boundary_time)
    for spec in cfg.session.windows:
        ws = session_windows(spec, b, date(YEAR, 1, 1), date(YEAR, 12, 31))
        assert ws, spec.name
        for w in ws:
            assert w.start_utc < w.end_utc, (spec.name, w)
            assert 0 < w.duration_hours <= 24, (spec.name, w)


# ----------------------------------------------------------------------- weeks


def test_sunday_evening_belongs_to_the_following_week(cfg):
    """The stub-merge rule (SPEC 2.6.1) depends entirely on this grouping."""
    before = datetime(2026, 2, 8, 20, 59, tzinfo=UTC)
    at = datetime(2026, 2, 8, 21, 0, tzinfo=UTC)
    after = datetime(2026, 2, 9, 0, 0, tzinfo=UTC)

    assert week_start_utc(before, cfg.week) == datetime(2026, 2, 1, 21, 0, tzinfo=UTC)
    assert week_start_utc(at, cfg.week) == datetime(2026, 2, 8, 21, 0, tzinfo=UTC)
    assert week_start_utc(after, cfg.week) == week_start_utc(at, cfg.week)


def test_week_close_follows_its_open(cfg):
    for d in _all_days(YEAR):
        ts = datetime.combine(d, time(12, 0), tzinfo=UTC)
        ws = week_start_utc(ts, cfg.week)
        wc = week_close_utc(ws, cfg.week)
        assert ws < wc
        assert (wc - ws) == timedelta(days=4, hours=24)  # Sun 21:00 -> Fri 21:00
        assert wc.weekday() == cfg.week.close_dow


def test_naive_datetimes_are_refused(cfg):
    with pytest.raises(ValueError):
        week_start_utc(datetime(2026, 2, 9, 0, 0), cfg.week)
