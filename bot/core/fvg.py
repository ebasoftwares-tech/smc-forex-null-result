"""Fair Value Gap detection (SPEC 12.1).

**Phase 8 scope: detection only.**  Displacement requires an FVG inside the leg by
default (SPEC 10.2), so shipping Phase 8 with that filter switched off would make its
rejection-rate measurements describe a different filter than the one that actually runs.
The lifecycle in SPEC 12.2 -- touch, PARTIAL, MITIGATED, INVALIDATED, EXPIRED, and the
selection rule of 12.3 -- is Phase 10, whose gate is the standalone edge test.

The rule is a three-bar arithmetic condition and nothing more:

    BULLISH   L_n > H_(n-2)      zone = [H_(n-2), L_n]
    BEARISH   H_n < L_(n-2)      zone = [H_n, L_(n-2)]

**Bar `n-1` is not tested at all.**  A bullish FVG does not require the middle bar to be
bullish; the gap is between the outer two bars by definition.  An implementation that
also tests the middle bar is computing a different object (SPEC 12.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

import numpy as np

from bot.config.schema import AppConfig
from bot.core.bars import BarSeries, from_epoch_s
from bot.core.indicators import atr_ref


class FvgDirection(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


class FvgStatus(str, Enum):
    """SPEC 12.2.  Only ``UNMITIGATED`` is reachable in Phase 8."""

    UNMITIGATED = "UNMITIGATED"
    PARTIAL = "PARTIAL"  # [Phase 10]
    MITIGATED = "MITIGATED"  # [Phase 10]
    INVALIDATED = "INVALIDATED"  # [Phase 10]
    EXPIRED = "EXPIRED"  # [Phase 10]


@dataclass
class Fvg:
    """SPEC 12.2.  Lifecycle fields are present but only set from Phase 10."""

    id: str
    symbol: str
    timeframe: str
    direction: FvgDirection
    zone_low: float
    zone_high: float
    size: float
    size_atr: float
    formed_index: int  # bar n-1, the middle bar the gap is centred on
    confirmed_index: int  # bar n; the gap is not knowable before this closes
    formed_at: datetime
    confirmed_at: datetime
    spans_gap: bool = False
    status: FvgStatus = FvgStatus.UNMITIGATED

    @property
    def ce(self) -> float:
        """Consequent encroachment: the zone midpoint.  Used by entry model C."""
        return (self.zone_low + self.zone_high) / 2.0

    @property
    def proximal(self) -> float:
        """The edge price reaches first when returning to the zone."""
        return self.zone_low if self.direction is FvgDirection.BULLISH else self.zone_high

    @property
    def distal(self) -> float:
        return self.zone_high if self.direction is FvgDirection.BULLISH else self.zone_low

    def __repr__(self) -> str:  # pragma: no cover - diagnostics
        return (
            f"<FVG {self.direction.value} [{self.zone_low:.5f}, {self.zone_high:.5f}] "
            f"{self.size_atr:.2f}ATR @{self.confirmed_index}>"
        )


def _pip_size(symbol: str) -> float:
    """SPEC 1.4.  0.01 for JPY quotes, 0.0001 otherwise."""
    return 0.01 if symbol.upper().endswith("JPY") else 0.0001


def detect_fvgs(series: BarSeries, cfg: AppConfig) -> list[Fvg]:
    """Every FVG in ``series``, in confirmation order.

    Causality: an FVG's ``confirmed_index`` is bar ``n``, so it is invisible until that
    bar closes even though the zone it describes sits two bars back.
    """
    out: list[Fvg] = []
    if series.n < 3:
        return out
    atr = atr_ref(series, cfg.atr.period)
    pip = _pip_size(series.symbol)
    min_pips = cfg.fvg.min_size_pips * pip

    for n in range(2, series.n):
        a = atr[n]
        if not np.isfinite(a) or a <= 0:
            continue
        prev2_high = float(series.high[n - 2])
        prev2_low = float(series.low[n - 2])
        h = float(series.high[n])
        l = float(series.low[n])

        if l > prev2_high:
            direction, lo, hi = FvgDirection.BULLISH, prev2_high, l
        elif h < prev2_low:
            direction, lo, hi = FvgDirection.BEARISH, h, prev2_low
        else:
            continue

        size = hi - lo
        if size < cfg.fvg.min_size_atr * float(a) or size < min_pips:
            continue

        # A discontinuity anywhere across the three bars means the "gap" is a period the
        # market was closed for, not an imbalance left behind by participants.
        spans_gap = bool(
            series.open_time[n] > series.close_time[n - 1]
            or series.open_time[n - 1] > series.close_time[n - 2]
            or series.flag("spans_gap")[n]
            or series.flag("spans_gap")[n - 1]
        )
        if spans_gap and cfg.fvg.exclude_weekend_gaps:
            continue

        out.append(
            Fvg(
                id=f"FV{len(out):06d}",
                symbol=series.symbol,
                timeframe=series.timeframe,
                direction=direction,
                zone_low=lo,
                zone_high=hi,
                size=size,
                size_atr=size / float(a),
                formed_index=n - 1,
                confirmed_index=n,
                formed_at=from_epoch_s(series.open_time[n - 1]),
                confirmed_at=from_epoch_s(series.close_time[n]),
                spans_gap=spans_gap,
            )
        )
    return out


def fvgs_in_leg(
    fvgs: list[Fvg], a: int, b: int, direction: FvgDirection
) -> list[Fvg]:
    """FVGs usable by a displacement leg ``[a..b]`` (SPEC 10.1).

    Membership is by **confirmation bar**: ``a <= n <= b``.  The gap's own first bar may
    precede the leg, which SPEC 10.4 relies on -- its two-bar example finds a bullish FVG
    via ``L_b > H`` of the bar *before* the leg.  Requiring all three bars inside the leg
    would make an FVG impossible on any leg shorter than three bars.
    """
    return [f for f in fvgs if a <= f.confirmed_index <= b and f.direction is direction]
