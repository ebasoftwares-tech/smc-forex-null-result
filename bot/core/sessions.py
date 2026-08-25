"""Session engine (SPEC 3).

Builds :class:`SessionInstance` objects from the session source series (M15 by
default).  Two rules from the specification are load-bearing here:

* **H4 bars cannot produce session levels** (SPEC 3.6).  One H4 bar straddles a
  session boundary, so its high may belong to either side.  The source timeframe is
  M15, and this remains true under DECISION D-002 -- D-002 moved *confirmation* to H4,
  not the measurement of session extremes.
* **A session's levels become liquidity only at its close** (SPEC 3.5).  A running
  extreme cannot be swept by the price action that is still creating it, and code that
  allows it reports a "sweep" on most bars.  ``SessionInstance`` therefore carries an
  explicit status and the liquidity layer will only ever read ``CLOSED`` ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

import numpy as np

from bot.config.schema import AppConfig
from bot.core.bars import BarSeries, from_epoch_s
from bot.data.calendar import (
    DayBoundary,
    SessionWindow,
    is_dst_desync,
    overlap_windows,
    session_windows,
)


class SessionStatus(str, Enum):
    FORMING = "FORMING"
    CLOSED = "CLOSED"
    INCOMPLETE = "INCOMPLETE"


@dataclass(frozen=True)
class SessionInstance:
    """One completed (or forming) occurrence of a session for one symbol.  SPEC 3.4."""

    symbol: str
    session_name: str
    trading_date: date
    start_utc: datetime
    end_utc: datetime
    open: float
    high: float
    low: float
    close: float
    high_ts: datetime
    low_ts: datetime
    bar_count: int
    expected_bars: int
    status: SessionStatus
    dst_desync: bool
    source_tf: str

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def coverage(self) -> float:
        return self.bar_count / self.expected_bars if self.expected_bars else 0.0

    def range_atr(self, atr_d1: float | None) -> float | None:
        """Session range in daily-ATR units.  ``None`` while ATR is still warming up."""
        if atr_d1 is None or not np.isfinite(atr_d1) or atr_d1 <= 0:
            return None
        return self.range / atr_d1

    @property
    def is_liquidity_source(self) -> bool:
        """Whether this session may contribute liquidity levels.

        SPEC 3.4/3.5: only a CLOSED session with sufficient coverage.  An INCOMPLETE
        session -- a half-holiday, a data gap, a broker outage -- is detected from bar
        coverage rather than a hard-coded holiday calendar, which makes it correct for
        every broker and every year with no maintenance.
        """
        return self.status is SessionStatus.CLOSED


def _build_one(
    series: BarSeries,
    window: SessionWindow,
    cfg: AppConfig,
    source_step_s: int,
    data_end_s: int,
) -> SessionInstance | None:
    s = int(window.start_utc.timestamp())
    e = int(window.end_utc.timestamp())
    lo = int(np.searchsorted(series.open_time, s, side="left"))
    hi = int(np.searchsorted(series.open_time, e, side="left"))
    if hi <= lo:
        return None  # no data at all in the window: the session did not happen here

    o = series.open[lo]
    c = series.close[hi - 1]
    h_idx = lo + int(np.argmax(series.high[lo:hi]))
    l_idx = lo + int(np.argmin(series.low[lo:hi]))
    count = hi - lo
    expected = max(1, (e - s) // source_step_s)

    if e > data_end_s:
        status = SessionStatus.FORMING
    elif count / expected < cfg.session.min_bar_coverage:
        status = SessionStatus.INCOMPLETE
    else:
        status = SessionStatus.CLOSED

    return SessionInstance(
        symbol=series.symbol,
        session_name=window.name,
        trading_date=window.trading_date,
        start_utc=window.start_utc,
        end_utc=window.end_utc,
        open=float(o),
        high=float(series.high[h_idx]),
        low=float(series.low[l_idx]),
        close=float(c),
        high_ts=from_epoch_s(series.open_time[h_idx]),
        low_ts=from_epoch_s(series.open_time[l_idx]),
        bar_count=int(count),
        expected_bars=int(expected),
        status=status,
        dst_desync=is_dst_desync(window.trading_date),
        source_tf=series.timeframe,
    )


def build_sessions(
    series: BarSeries,
    cfg: AppConfig,
    *,
    include_overlap: bool = True,
    include_disabled: bool = False,
) -> list[SessionInstance]:
    """Every session occurrence covered by ``series``, ascending by end time.

    ``series`` must be on ``cfg.session.source_tf``.  Passing H4 is refused rather
    than silently degraded -- see SPEC 3.6.
    """
    if series.timeframe != cfg.session.source_tf:
        raise ValueError(
            f"session engine requires {cfg.session.source_tf} bars, got {series.timeframe}; "
            "H4 bars straddle session boundaries and cannot produce session levels (SPEC 3.6)"
        )
    if series.n == 0:
        return []

    from bot.core.bars import TIMEFRAMES

    step = int(TIMEFRAMES[series.timeframe].total_seconds())
    boundary = DayBoundary(cfg.tf.day_boundary_tz, cfg.tf.day_boundary_time)
    first = from_epoch_s(series.open_time[0]).date()
    last = from_epoch_s(series.open_time[-1]).date()
    data_end_s = int(series.close_time[-1])

    windows: list[SessionWindow] = []
    by_name: dict[str, list[SessionWindow]] = {}
    for spec in cfg.session.windows:
        if not spec.enabled and not include_disabled:
            continue
        w = session_windows(spec, boundary, first, last)
        by_name[spec.name] = w
        windows.extend(w)

    if include_overlap and "LONDON" in by_name and "NEW_YORK" in by_name:
        windows.extend(overlap_windows(by_name["LONDON"], by_name["NEW_YORK"]))

    out: list[SessionInstance] = []
    for w in windows:
        inst = _build_one(series, w, cfg, step, data_end_s)
        if inst is not None:
            out.append(inst)
    out.sort(key=lambda s: (s.end_utc, s.session_name))
    return out
