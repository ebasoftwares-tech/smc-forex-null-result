"""Synthetic bar generation for fixtures and pipeline smoke tests.

This is **test scaffolding, not a market model**.  It exists so that Phase 1 can be
verified before any broker or archive data is available, and so the DST fixture year
that the Phase 1 gate requires can be built deterministically.

It is deliberately *not* used to produce any strategy result.  A random walk has no
liquidity, no sessions and no structure, so a backtest over it would measure nothing.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable, Sequence

import numpy as np

from bot.config.schema import AppConfig
from bot.core.bars import BarSeries, TIMEFRAMES, build_series
from bot.data.calendar import UTC, week_start_utc


def trading_minutes(
    start: datetime,
    end: datetime,
    cfg: AppConfig,
    step_s: int,
    holidays: Iterable[date] = (),
) -> np.ndarray:
    """Bar open times on the FX trading calendar: Sunday 21:00 UTC to Friday 21:00 UTC.

    Weekends are *absent bars*, not zero-volume bars (SPEC 1.5).  ``holidays`` removes
    whole UTC dates, which is how the fixture reproduces a broker holiday for the
    coverage and DATA_SUSPECT paths.
    """
    holiday_set = {h for h in holidays}
    out: list[int] = []
    ws = week_start_utc(start, cfg.week)
    while ws <= end:
        close = ws + timedelta(days=(cfg.week.close_dow - ws.weekday()) % 7 or 7)
        close = close.replace(
            hour=cfg.week.close_time.hour, minute=cfg.week.close_time.minute, second=0, microsecond=0
        )
        t = ws
        while t < close:
            if t >= start and t <= end and t.date() not in holiday_set:
                out.append(int(t.timestamp()))
            t += timedelta(seconds=step_s)
        ws += timedelta(days=7)
    return np.asarray(sorted(set(out)), dtype=np.int64)


def generate(
    symbol: str,
    start: datetime,
    end: datetime,
    cfg: AppConfig,
    *,
    timeframe: str = "M1",
    seed: int = 7,
    start_price: float = 1.1000,
    pip: float = 0.0001,
    sigma_pips: float = 0.7,
    holidays: Sequence[date] = (),
    drop_ranges: Sequence[tuple[datetime, datetime]] = (),
) -> BarSeries:
    """A deterministic random-walk series on the FX trading calendar.

    ``drop_ranges`` removes bars inside the given UTC intervals, which is how the
    fixture produces an intraday data gap for ``quality.py`` to find.
    """
    step_s = int(TIMEFRAMES[timeframe].total_seconds())
    t = trading_minutes(start, end, cfg, step_s, holidays)
    for lo, hi in drop_ranges:
        lo_s, hi_s = int(lo.timestamp()), int(hi.timestamp())
        t = t[(t < lo_s) | (t >= hi_s)]
    n = len(t)
    if n == 0:
        return build_series(symbol, timeframe, *(np.zeros(0) for _ in range(7)))

    rng = np.random.default_rng(seed)
    sigma = sigma_pips * pip
    steps = rng.normal(0.0, sigma, size=n)
    closes = start_price + np.cumsum(steps)
    opens = np.empty(n, dtype=np.float64)
    opens[0] = start_price
    opens[1:] = closes[:-1]

    wick = np.abs(rng.normal(0.0, sigma * 0.6, size=(2, n)))
    highs = np.maximum(opens, closes) + wick[0]
    lows = np.minimum(opens, closes) - wick[1]
    vol = rng.integers(20, 400, size=n).astype(np.float64)

    return build_series(
        symbol,
        timeframe,
        t,
        t + step_s,
        opens,
        highs,
        lows,
        closes,
        vol,
    )


def fixture_year(cfg: AppConfig, *, year: int = 2026, timeframe: str = "M15", seed: int = 7) -> BarSeries:
    """The DST fixture year the Phase 1 gate requires.

    Spans both hemispheres' daylight-saving transitions, so session boundaries, the
    London/New York desynchronisation weeks and the day-length edge cases are all
    exercised by one dataset.
    """
    return generate(
        "EURUSD",
        datetime(year, 1, 1, tzinfo=UTC),
        datetime(year, 12, 31, 23, 59, tzinfo=UTC),
        cfg,
        timeframe=timeframe,
        seed=seed,
    )
