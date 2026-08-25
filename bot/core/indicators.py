"""Shared numeric primitives.

Only ATR for now (SPEC 1.6).  Every threshold in the strategy that could have been
written in pips is written in ATR multiples instead, so one parameter set is
meaningful across symbols and across volatility regimes.
"""

from __future__ import annotations

import numpy as np

from bot.core.bars import BarSeries


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """Wilder's true range.  ``tr[0]`` falls back to the bar's own range."""
    n = len(high)
    tr = np.empty(n, dtype=np.float64)
    if n == 0:
        return tr
    tr[0] = high[0] - low[0]
    if n > 1:
        prev = close[:-1]
        tr[1:] = np.maximum(
            high[1:] - low[1:], np.maximum(np.abs(high[1:] - prev), np.abs(low[1:] - prev))
        )
    return tr


def wilder_atr(series: BarSeries, period: int) -> np.ndarray:
    """Wilder-smoothed ATR, seeded with the mean of the first ``period`` true ranges.

    Values before the seed are ``nan``: there is no ATR yet, and returning a
    part-formed number would let a rule fire during warm-up on a threshold computed
    from three bars.  SPEC 1.6 requires the warm-up to be respected explicitly.
    """
    tr = true_range(series.high, series.low, series.close)
    n = len(tr)
    atr = np.full(n, np.nan, dtype=np.float64)
    if n < period or period < 2:
        return atr
    atr[period - 1] = tr[:period].mean()
    for i in range(period, n):
        atr[i] = ((period - 1) * atr[i - 1] + tr[i]) / period
    return atr


def atr_ref(series: BarSeries, period: int) -> np.ndarray:
    """ATR as of the **previous** closed bar, aligned to each bar index.

    THE reference-value rule (SPEC 1.6): a test applied to bar ``i`` uses ``ATR(i-1)``.
    A displacement bar must not be allowed to raise the threshold it is being tested
    against -- using ``ATR(i)`` makes large bars self-normalising and quietly destroys
    the displacement filter.  Everything downstream reads this function, never
    ``wilder_atr`` directly.
    """
    atr = wilder_atr(series, period)
    out = np.full_like(atr, np.nan)
    out[1:] = atr[:-1]
    return out


def warmup_bars(period: int, fractal_n: int = 0) -> int:
    """Bars required before any signal may be emitted (SPEC 1.6)."""
    return period + fractal_n + 1
