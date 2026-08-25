"""Timeframe construction (SPEC 2).

Broker MN1/W1/D1/H4 candles are cut at the broker's server midnight, vary between
brokers, and change shape twice a year.  This module builds every higher timeframe
from the ingest series instead, against an explicit, configurable anchor.

Two rules drive the implementation:

* **DECISION D-001** -- the day boundary is UTC 00:00, so the H4 grid is fixed at
  00/04/08/12/16/20 UTC year-round.  The general (tz-anchored) path is used anyway so
  the New-York ablation exercises this code rather than a second implementation.
* **DECISION D-001a / SPEC 2.6.1** -- the Sunday 21:00-24:00 UTC stub is merged into
  Monday's D1 bar.  Without it, a three-hour opening range becomes Monday's PDH/PDL,
  every week.

A bucket with no source bars produces no bar at all -- there is no forward fill
anywhere in this system (SPEC 1.5).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Callable

import numpy as np

from bot.config.schema import AppConfig
from bot.core.bars import BarSeries, TIMEFRAMES, build_series, empty_flags, from_epoch_s
from bot.data.calendar import UTC, DayBoundary, week_close_utc, week_start_utc

_HOUR = 3600
_DAY_S = 86400

DERIVED_TIMEFRAMES = ("M15", "H1", "H4", "D1", "W1", "MN1")


class ResampleError(ValueError):
    pass


# --------------------------------------------------------------------- bucket keys


def _day_grid(boundary: DayBoundary, first: datetime, last: datetime) -> tuple[np.ndarray, list[date]]:
    """Day-boundary instants (epoch seconds) spanning the data, plus their trading dates.

    Padded one day either side so that ``searchsorted`` can never fall off an end.
    """
    d0 = boundary.trading_date(first) - timedelta(days=2)
    d1 = boundary.trading_date(last) + timedelta(days=2)
    dates: list[date] = []
    stamps: list[int] = []
    d = d0
    while d <= d1:
        dates.append(d)
        stamps.append(int(boundary.boundary_utc(d).timestamp()))
        d += timedelta(days=1)
    arr = np.asarray(stamps, dtype=np.int64)
    if not np.all(np.diff(arr) > 0):
        raise ResampleError("day boundaries are not strictly increasing")
    return arr, dates


def _week_grid(cfg: AppConfig, first: datetime, last: datetime) -> np.ndarray:
    """Trading-week open instants (epoch seconds) spanning the data, padded either side."""
    start = week_start_utc(first, cfg.week) - timedelta(days=7)
    stamps: list[int] = []
    t = start
    end = last + timedelta(days=14)
    while t <= end:
        stamps.append(int(t.timestamp()))
        t += timedelta(days=7)
    return np.asarray(stamps, dtype=np.int64)


def _bucket_bounds(
    t: np.ndarray,
    timeframe: str,
    boundary: DayBoundary,
    cfg: AppConfig,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return ``(bucket_start, bucket_end, day_length_s)`` per source bar, epoch seconds.

    ``day_length_s`` is 86400 under a UTC anchor and 82800 / 90000 on a DST transition
    day under a tz anchor; it is what ``irregular_day`` is derived from.
    """
    first, last = from_epoch_s(t[0]), from_epoch_s(t[-1])

    if timeframe in ("M15", "H1"):
        # SPEC 2.3: intraday sub-H4 grids are aligned to the UTC hour and need no anchor.
        step = int(TIMEFRAMES[timeframe].total_seconds())
        start = (t // step) * step
        return start, start + step, np.full(t.shape, _DAY_S, dtype=np.int64)

    days, _dates = _day_grid(boundary, first, last)
    di = np.searchsorted(days, t, side="right") - 1
    if np.any(di < 0):
        raise ResampleError("a bar precedes the day grid")
    day_start = days[di]
    day_end = days[di + 1]
    day_len = day_end - day_start

    if timeframe == "D1":
        return day_start, day_end, day_len

    if timeframe == "H4":
        step = 4 * _HOUR
        k = (t - day_start) // step
        start = day_start + k * step
        end = np.minimum(start + step, day_end)
        return start, end, day_len

    if timeframe == "W1":
        weeks = _week_grid(cfg, first, last)
        wi = np.searchsorted(weeks, t, side="right") - 1
        if np.any(wi < 0):
            raise ResampleError("a bar precedes the week grid")
        # A weekly bar is complete once the week's trading has ended, so it closes at
        # the first day boundary AFTER the configured week close (Saturday 00:00 UTC
        # under D-001) -- not at the next week's open on Sunday night.  Closing it on
        # Sunday would delay Monday's Weekly analysis by a day and, worse, would make
        # the W1 close disagree with the close of the final D1 bar it is built from.
        start = weeks[wi]
        end = np.empty_like(start)
        for w_open in np.unique(start):
            w_close = week_close_utc(from_epoch_s(int(w_open)), cfg.week)
            # Resolve the following day boundary directly rather than indexing the day
            # grid: when the source is truncated mid-week the week's close lies days
            # beyond the last bar, past the end of the (necessarily finite) grid.
            nxt = boundary.boundary_utc(boundary.trading_date(w_close) + timedelta(days=1))
            end[start == w_open] = int(nxt.timestamp())
        return start, end, day_len

    if timeframe == "MN1":
        # Month membership follows the local calendar date of the day boundary, so it
        # never depends on a UTC offset (SPEC 2.3).
        local_dates = np.array(
            [boundary.trading_date(from_epoch_s(x)) for x in day_start], dtype=object
        )
        starts = np.empty_like(t)
        ends = np.empty_like(t)
        cache: dict[tuple[int, int], tuple[int, int]] = {}
        for i, ld in enumerate(local_dates):
            key = (ld.year, ld.month)
            if key not in cache:
                m_first = date(ld.year, ld.month, 1)
                nxt = date(ld.year + (ld.month == 12), (ld.month % 12) + 1, 1)
                cache[key] = (
                    int(boundary.boundary_utc(m_first).timestamp()),
                    int(boundary.boundary_utc(nxt).timestamp()),
                )
            starts[i], ends[i] = cache[key]
        return starts, ends, day_len

    raise ResampleError(f"unsupported timeframe {timeframe!r}")


# ------------------------------------------------------------------- Sunday stub


def _merge_week_stub(
    t: np.ndarray,
    bucket_start: np.ndarray,
    bucket_end: np.ndarray,
    cfg: AppConfig,
    source_step: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Merge the trading week's first D1 bucket forward when it is a stub.

    SPEC 2.6.1.  Returns ``(bucket_start, bucket_end, merged_mask)`` where the mask
    marks the source bars that were reassigned.

    The condition is exactly: the bucket is the first of its trading week **and** its
    coverage is below ``tf.stub_merge_threshold``.  A short Monday caused by a holiday
    is therefore *not* merged -- it is a real, if thin, trading day, and the coverage
    machinery already reports it.
    """
    merged = np.zeros(t.shape, dtype=bool)
    if cfg.tf.sunday_handling != "merge_into_monday":
        return bucket_start, bucket_end, merged

    weeks = _week_grid(cfg, from_epoch_s(t[0]), from_epoch_s(t[-1]))
    wi = np.searchsorted(weeks, t, side="right") - 1

    new_start = bucket_start.copy()
    new_end = bucket_end.copy()

    for w in np.unique(wi):
        sel = wi == w
        b_in_week = np.unique(bucket_start[sel])
        if len(b_in_week) < 2:
            continue  # nothing to merge into
        first_b = b_in_week[0]
        target_b = b_in_week[1]
        is_first = sel & (bucket_start == first_b)
        span = int(bucket_end[is_first][0] - first_b)
        expected = max(1, span // source_step)
        coverage = int(is_first.sum()) / expected
        if coverage >= cfg.tf.stub_merge_threshold:
            continue
        target_end = bucket_end[sel & (bucket_start == target_b)][0]
        new_start[is_first] = target_b
        new_end[is_first] = target_end
        merged[is_first] = True
        merged[sel & (bucket_start == target_b)] = True

    return new_start, new_end, merged


# ------------------------------------------------------------------- aggregation


def _group_starts(keys: np.ndarray) -> np.ndarray:
    if len(keys) == 0:
        return np.zeros(0, dtype=np.int64)
    return np.concatenate(([0], np.flatnonzero(np.diff(keys)) + 1)).astype(np.int64)


def _internal_gap_mask(
    t: np.ndarray, starts: np.ndarray, counts: np.ndarray, source_step: int, cfg: AppConfig
) -> np.ndarray:
    """True for buckets containing a gap wider than ``data.max_gap_bars`` source bars.

    Gaps that contain a trading-week open are excluded: the weekend is not a data
    defect, and flagging it would make the flag meaningless on W1 and MN1, which
    contain a weekend by construction (SPEC 1.5).
    """
    if len(starts) == 0:
        return np.zeros(0, dtype=bool)
    limit = cfg.data.max_gap_bars * source_step
    weeks = _week_grid(cfg, from_epoch_s(t[0]), from_epoch_s(t[-1]))
    out = np.zeros(len(starts), dtype=bool)
    for i, (s, c) in enumerate(zip(starts, counts)):
        if c < 2:
            continue
        seg = t[s : s + c]
        d = np.diff(seg)
        big = np.flatnonzero(d > limit)
        for j in big:
            lo, hi = seg[j], seg[j + 1]
            if np.any((weeks > lo) & (weeks <= hi)):
                continue  # weekend
            out[i] = True
            break
    return out


def resample(
    source: BarSeries,
    timeframe: str,
    cfg: AppConfig,
    *,
    require_closed: bool = True,
) -> BarSeries:
    """Aggregate ``source`` (the ingest timeframe) up to ``timeframe``.

    ``require_closed`` implements SPEC 2.4: a bucket is emitted only once its end
    instant has passed according to observed data, so the final still-forming bucket is
    never emitted.

    The test is ``bucket_end <= last observed close``, not "a source bar exists after
    bucket_end".  The two agree whenever the source grid divides the bucket grid, which
    it always does here, but only the first makes resampling **prefix-stable**: bars
    already emitted never change, and truncating the source can only remove bars from
    the end.  That is the no-repaint property for a resampler, and
    ``tests/test_causality.py`` asserts it directly.  Keying on "a later bar exists"
    would instead make emission depend on data that arrives *after* the bar closed,
    which is the shape of a lookahead even when the values happen to match.
    """
    if timeframe not in DERIVED_TIMEFRAMES:
        raise ResampleError(f"{timeframe!r} is not a derived timeframe")
    if source.n == 0:
        return build_series(source.symbol, timeframe, *(np.zeros(0) for _ in range(7)))

    boundary = DayBoundary(cfg.tf.day_boundary_tz, cfg.tf.day_boundary_time)
    source_step = int(TIMEFRAMES[source.timeframe].total_seconds())
    t = source.open_time

    b_start, b_end, day_len = _bucket_bounds(t, timeframe, boundary, cfg)

    merged_mask = np.zeros(t.shape, dtype=bool)
    if timeframe == "D1":
        b_start, b_end, merged_mask = _merge_week_stub(t, b_start, b_end, cfg, source_step)

    if not np.all(np.diff(b_start) >= 0):
        raise ResampleError(f"{timeframe}: bucket keys are not monotonic after assignment")

    starts = _group_starts(b_start)
    counts = np.diff(np.append(starts, len(t))).astype(np.int64)

    bucket_start = b_start[starts]
    bucket_end = b_end[starts]
    first_bar_t = t[starts]
    # A merged bucket genuinely begins at its first constituent bar, which is earlier
    # than its nominal grid start.  Taking the minimum keeps buckets ordered and makes
    # the coverage denominator honest for the merged Monday bar.
    open_time = np.minimum(bucket_start, first_bar_t)

    o = source.open[starts]
    c = source.close[starts + counts - 1]
    h = np.maximum.reduceat(source.high, starts)
    lo = np.minimum.reduceat(source.low, starts)
    v = np.add.reduceat(source.volume, starts)

    expected = np.maximum(1, (bucket_end - open_time) // source_step).astype(np.int64)
    coverage = counts / expected

    flags = empty_flags(len(starts))
    flags["partial_bar"] = coverage < 0.999
    flags["low_coverage"] = coverage < cfg.tf.min_bar_coverage_warn
    flags["merged_stub"] = merged_mask[starts]
    flags["spans_gap"] = _internal_gap_mask(t, starts, counts, source_step, cfg)
    flags["irregular_day"] = day_len[starts] != _DAY_S
    flags["data_suspect"] = np.zeros(len(starts), dtype=bool)  # set by quality.py

    keep = np.ones(len(starts), dtype=bool)
    if require_closed:
        # SPEC 2.4 -- a bucket is knowable only once its end instant has passed.
        keep = bucket_end <= int(source.close_time[-1])

    out = build_series(
        source.symbol,
        timeframe,
        open_time[keep],
        bucket_end[keep],
        o[keep],
        h[keep],
        lo[keep],
        c[keep],
        v[keep],
        bar_count=counts[keep],
        expected_bars=expected[keep],
        flags={k: arr[keep] for k, arr in flags.items()},
    )
    return out


def resample_all(
    source: BarSeries, cfg: AppConfig, timeframes: tuple[str, ...] = DERIVED_TIMEFRAMES
) -> dict[str, BarSeries]:
    return {tf: resample(source, tf, cfg) for tf in timeframes}
