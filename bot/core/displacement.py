"""Displacement detection (SPEC 10).

Displacement is evaluated over a **leg**, not a single bar, because a two-bar drive and
a one-bar drive of the same magnitude are the same event.

The leg is not searched for.  Given a break bar ``b`` and the sweep extreme bar ``s``:

    a = max(s, b - disp.max_leg_bars + 1)

and that is the only leg considered.  SPEC 10.5 is explicit about why: *"No search over
origins -- searching for the window that passes is how a filter becomes a formality."*
An engine that tried several origins and took the best would report displacement on
almost everything, and the rejection rate -- the thing Phase 8's gate measures -- would
be meaningless.

Every component is returned, not just the verdict, so the distribution of each can be
plotted against its threshold (SPEC 10.6).  A threshold that never rejects anything is
not a filter.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np

from bot.config.schema import AppConfig
from bot.core.bars import BarSeries
from bot.core.fvg import Fvg, FvgDirection, fvgs_in_leg
from bot.core.indicators import atr_ref


class Direction(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"

    @property
    def fvg(self) -> FvgDirection:
        return FvgDirection.BULLISH if self is Direction.BULLISH else FvgDirection.BEARISH


class DisplacementReason(str, Enum):
    NET_TOO_SMALL = "NET_TOO_SMALL"
    BODY_RATIO = "BODY_RATIO"
    DIRECTIONAL_BARS = "DIRECTIONAL_BARS"
    NO_FVG = "NO_FVG"
    LEG_TOO_LONG = "LEG_TOO_LONG"
    NO_ATR = "NO_ATR"
    DEGENERATE = "DEGENERATE"


@dataclass(frozen=True)
class DisplacementResult:
    """Every component of the SPEC 10.1 test, whether or not it passed."""

    confirmed: bool
    direction: Direction
    leg_start: int
    leg_end: int
    leg_bars: int
    net: float
    net_atr: float
    gross: float
    bodies: float
    body_ratio: float
    dir_bars: int
    atr: float
    fvg_id: str | None
    fvg_count: int
    spans_gap: bool
    reasons: tuple[DisplacementReason, ...] = ()
    mode: str = "leg"

    @property
    def failed_on(self) -> DisplacementReason | None:
        return self.reasons[0] if self.reasons else None


def _empty(direction: Direction, a: int, b: int, reason: DisplacementReason) -> DisplacementResult:
    return DisplacementResult(
        confirmed=False,
        direction=direction,
        leg_start=a,
        leg_end=b,
        leg_bars=max(0, b - a + 1),
        net=0.0,
        net_atr=0.0,
        gross=0.0,
        bodies=0.0,
        body_ratio=0.0,
        dir_bars=0,
        atr=0.0,
        fvg_id=None,
        fvg_count=0,
        spans_gap=False,
        reasons=(reason,),
    )


def leg_origin(sweep_extreme_bar: int, break_bar: int, cfg: AppConfig) -> int:
    """SPEC 10.1.  Clamped to the sweep extreme so the leg is never measured from before
    the sweep that defines the setup."""
    return max(sweep_extreme_bar, break_bar - cfg.disp.max_leg_bars + 1)


def evaluate_leg(
    series: BarSeries,
    a: int,
    b: int,
    direction: Direction,
    cfg: AppConfig,
    fvgs: list[Fvg] | None = None,
    atr: np.ndarray | None = None,
) -> DisplacementResult:
    """Test SPEC 10.1's five conditions over the leg ``[a..b]``.

    All five are evaluated even once one has failed, so the report can say *which*
    condition rejects how often rather than only that something did.
    """
    if b < a or a < 0 or b >= series.n:
        return _empty(direction, a, b, DisplacementReason.DEGENERATE)

    if atr is None:
        atr = atr_ref(series, cfg.atr.period)
    A = atr[b]
    if not np.isfinite(A) or A <= 0:
        return _empty(direction, a, b, DisplacementReason.NO_ATR)
    A = float(A)

    o = series.open[a : b + 1]
    h = series.high[a : b + 1]
    l = series.low[a : b + 1]
    c = series.close[a : b + 1]
    bullish = direction is Direction.BULLISH

    net = float(c[-1] - l.min()) if bullish else float(h.max() - c[-1])
    gross = float((h - l).sum())
    up = c > o
    keep = up if bullish else ~up
    bodies = float(np.abs(c - o)[keep].sum())
    dir_bars = int(keep.sum())
    leg_bars = b - a + 1

    # SPEC 10.5: all-doji legs make bodies/gross undefined.  Guarded explicitly rather
    # than left to produce a NaN that silently compares False against every threshold.
    body_ratio = bodies / gross if gross > 0 else 0.0

    matching = fvgs_in_leg(fvgs or [], a, b, direction.fvg)
    spans_gap = bool(
        np.any(series.flag("spans_gap")[a : b + 1])
        or np.any(series.open_time[a + 1 : b + 1] > series.close_time[a:b])
    )

    reasons: list[DisplacementReason] = []
    if net < cfg.disp.min_leg_atr * A:
        reasons.append(DisplacementReason.NET_TOO_SMALL)
    if body_ratio < cfg.disp.min_body_ratio:
        reasons.append(DisplacementReason.BODY_RATIO)
    if dir_bars < cfg.disp.min_directional_bars:
        reasons.append(DisplacementReason.DIRECTIONAL_BARS)
    if cfg.disp.require_fvg and not matching:
        reasons.append(DisplacementReason.NO_FVG)
    if leg_bars > cfg.disp.max_leg_bars:
        reasons.append(DisplacementReason.LEG_TOO_LONG)

    return DisplacementResult(
        confirmed=not reasons,
        direction=direction,
        leg_start=a,
        leg_end=b,
        leg_bars=leg_bars,
        net=net,
        net_atr=net / A,
        gross=gross,
        bodies=bodies,
        body_ratio=body_ratio,
        dir_bars=dir_bars,
        atr=A,
        fvg_id=matching[0].id if matching else None,
        fvg_count=len(matching),
        spans_gap=spans_gap,
        reasons=tuple(reasons),
        mode="leg",
    )


def evaluate_bar(
    series: BarSeries,
    i: int,
    direction: Direction,
    cfg: AppConfig,
    atr: np.ndarray | None = None,
) -> DisplacementResult:
    """SPEC 10.3, the classic single-bar formulation.  Retained for ablation."""
    if i < 0 or i >= series.n:
        return _empty(direction, i, i, DisplacementReason.DEGENERATE)
    if atr is None:
        atr = atr_ref(series, cfg.atr.period)
    A = atr[i]
    if not np.isfinite(A) or A <= 0:
        return _empty(direction, i, i, DisplacementReason.NO_ATR)
    A = float(A)

    o, h, l, c = (
        float(series.open[i]),
        float(series.high[i]),
        float(series.low[i]),
        float(series.close[i]),
    )
    rng = h - l
    body = abs(c - o)
    ratio = body / rng if rng > 0 else 0.0
    right_way = (c > o) if direction is Direction.BULLISH else (c < o)

    reasons: list[DisplacementReason] = []
    if rng < cfg.disp.min_range_atr * A:
        reasons.append(DisplacementReason.NET_TOO_SMALL)
    if ratio < cfg.disp.min_body_ratio:
        reasons.append(DisplacementReason.BODY_RATIO)
    if not right_way:
        reasons.append(DisplacementReason.DIRECTIONAL_BARS)

    return DisplacementResult(
        confirmed=not reasons,
        direction=direction,
        leg_start=i,
        leg_end=i,
        leg_bars=1,
        net=rng,
        net_atr=rng / A,
        gross=rng,
        bodies=body,
        body_ratio=ratio,
        dir_bars=1 if right_way else 0,
        atr=A,
        fvg_id=None,
        fvg_count=0,
        spans_gap=bool(series.flag("spans_gap")[i]),
        reasons=tuple(reasons),
        mode="bar",
    )


def evaluate(
    series: BarSeries,
    sweep_extreme_bar: int,
    break_bar: int,
    direction: Direction,
    cfg: AppConfig,
    fvgs: list[Fvg] | None = None,
    atr: np.ndarray | None = None,
) -> DisplacementResult:
    """The entry point Phase 9 calls: resolve the leg origin, then apply ``disp.mode``."""
    a = leg_origin(sweep_extreme_bar, break_bar, cfg)
    if cfg.disp.mode == "bar":
        return evaluate_bar(series, break_bar, direction, cfg, atr)
    leg = evaluate_leg(series, a, break_bar, direction, cfg, fvgs, atr)
    if cfg.disp.mode == "leg" or leg.confirmed:
        return leg
    return evaluate_bar(series, break_bar, direction, cfg, atr)
