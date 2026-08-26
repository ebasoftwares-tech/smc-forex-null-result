"""Fair Value Gap detection and lifecycle (SPEC 12).

Detection landed in Phase 8, because displacement requires an FVG inside the leg by
default (SPEC 10.2) and shipping that phase with the filter switched off would have made
its rejection rates describe a different filter than the one that runs.  Phase 10 adds
the rest: the SPEC 12.2 lifecycle and the SPEC 12.3 selection rule.

Three things about the lifecycle carry weight.

**Proximal is the edge price reaches FIRST, and SPEC 12.1 labels it backwards.**  A
bullish gap forms with price above it, so a return meets ``zone_high`` (= L_n) first.
12.1's table says the proximal edge is ``H_(n-2)`` = ``zone_low``; 12.2's touch rule
(``bullish: L <= zone_high``) and 12.4's worked example (*"buy limit at 1.08420
(proximal edge)"*, where 1.08420 is L_n) both say the opposite, and they are right.
Entry model C places its limit at the proximal edge, so the label decides whether a
model-C entry waits for a shallow pullback or a deep one.  See D-011.

**Status is a function of time, not a field.**  ``status_at(bar)`` is what callers use;
the stored indices say *when* each transition happened.  Reading a single mutable
``status`` after a whole run and using it to decide whether a gap was available at some
earlier bar is lookahead, and it is the natural mistake here because the object looks
like it holds one.

**A gap that was traded through counts as used, wherever the bar closed.**  Mitigation is
tested before invalidation within a bar, so a bar that reaches the mitigation threshold
and then closes beyond the zone is MITIGATED, not INVALIDATED.  The gap-over case of SPEC
12.5 -- closing beyond without ever touching -- is what invalidation is for.

The detection rule is a three-bar arithmetic condition and nothing more:

    BULLISH   L_n > H_(n-2)      zone = [H_(n-2), L_n]
    BEARISH   H_n < L_(n-2)      zone = [H_n, L_(n-2)]

**Bar `n-1` is not tested at all.**  A bullish FVG does not require the middle bar to be
bullish; the gap is between the outer two bars by definition.  An implementation that
also tests the middle bar is computing a different object (SPEC 12.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Sequence

import numpy as np

from bot.config.schema import AppConfig
from bot.core.bars import BarSeries, from_epoch_s
from bot.core.indicators import atr_ref


class FvgDirection(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


class FvgStatus(str, Enum):
    """SPEC 12.2.

    ``PARTIAL`` is the only non-terminal one: a gap that has been touched but has not
    yet reached its mitigation threshold can still go on to be mitigated, invalidated or
    expired.
    """

    UNMITIGATED = "UNMITIGATED"
    PARTIAL = "PARTIAL"
    MITIGATED = "MITIGATED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"

    @property
    def is_terminal(self) -> bool:
        return self in (FvgStatus.MITIGATED, FvgStatus.INVALIDATED, FvgStatus.EXPIRED)


@dataclass
class Fvg:
    """SPEC 12.2.  One gap, plus when each lifecycle transition happened to it."""

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

    #: Bar indices of each transition.  At most one terminal index is ever set, because
    #: tracking stops at the first one.
    first_touch_index: int | None = None
    mitigated_index: int | None = None
    invalidated_index: int | None = None
    expired_index: int | None = None
    first_touch_at: datetime | None = None
    mitigated_at: datetime | None = None

    @property
    def ce(self) -> float:
        """Consequent encroachment: the zone midpoint.  Used by entry model C."""
        return (self.zone_low + self.zone_high) / 2.0

    @property
    def proximal(self) -> float:
        """The edge price reaches first when returning to the zone.

        ``zone_high`` for a bullish gap: price sits above it and comes back down.  SPEC
        12.1's table labels this the other way round and contradicts both 12.2's touch
        rule and 12.4's worked example -- see the module docstring and D-011.
        """
        return self.zone_high if self.direction is FvgDirection.BULLISH else self.zone_low

    @property
    def distal(self) -> float:
        """The far edge -- the one a full traverse of the zone reaches."""
        return self.zone_low if self.direction is FvgDirection.BULLISH else self.zone_high

    @property
    def terminal_index(self) -> int | None:
        for i in (self.mitigated_index, self.invalidated_index, self.expired_index):
            if i is not None:
                return i
        return None

    def age_at(self, bar: int) -> int:
        """Bars since the gap became knowable (SPEC 12.2 ``age_bars``)."""
        return bar - self.confirmed_index

    def status_at(self, bar: int) -> FvgStatus:
        """Status as of the close of ``bar``.

        **Use this, never the stored ``status``, to decide what was available at a past
        bar.**  ``status`` holds the end-of-run value, so reading it at bar ``i`` would
        let a gap mitigated at ``i + 5`` look unavailable at ``i`` -- lookahead, and
        invisible, because the object looks like it simply has a status.
        """
        t = self.terminal_index
        if t is not None and bar >= t:
            if self.mitigated_index == t:
                return FvgStatus.MITIGATED
            if self.invalidated_index == t:
                return FvgStatus.INVALIDATED
            return FvgStatus.EXPIRED
        if self.first_touch_index is not None and bar >= self.first_touch_index:
            return FvgStatus.PARTIAL
        return FvgStatus.UNMITIGATED

    def is_available_at(self, bar: int) -> bool:
        """Whether an entry model could still use this gap at ``bar`` (SPEC 12.3)."""
        return self.status_at(bar) is FvgStatus.UNMITIGATED

    def touched_by(self, high: float, low: float) -> bool:
        """Whether a bar's range actually **intersects** the zone.

        SPEC 12.2 writes this one-sided -- bullish ``L <= zone_high``, bearish
        ``H >= zone_low`` -- which is correct for the case it was written for, price
        returning to the gap from its own side.  It is wrong for the case SPEC 12.5
        describes: *"Price gaps over the entire zone without touching -- zone is **not**
        mitigated (never touched) but is INVALIDATED if the close is beyond it."*  A bar
        that opens below a bullish zone satisfies ``L <= zone_high`` while never having
        traded inside it.

        Intersection agrees with 12.2 everywhere 12.2 is right (a bar arriving from above
        has ``H >= zone_low`` trivially) and differs only in 12.5's case, so it
        generalises the rule rather than replacing it.

        **This is load-bearing, not tidiness.**  Under the one-sided rule, mitigation
        always fires before invalidation -- a bullish close below ``zone_low`` implies a
        low below ``zone_low``, which is at or past every mitigation target -- so
        ``INVALIDATED`` is unreachable and every gap-over is miscounted as a fill.  That
        inflates the fill-rate curve SPEC 12.6 asks for, which is Phase 10's own
        deliverable.  See D-011.
        """
        return low <= self.zone_high and high >= self.zone_low

    def mitigation_price(self, mode: str) -> float:
        """The price that consumes the gap under ``fvg.mitigation_mode``.

        ``touch`` is the strictest (any tag consumes it), ``full`` the loosest.  The
        choice changes how many gaps remain available to entry model C and is an
        ablation dimension (SPEC 12.2).
        """
        if mode == "touch":
            return self.proximal
        if mode == "ce":
            return self.ce
        if mode == "full":
            return self.distal
        raise ValueError(f"unknown fvg.mitigation_mode {mode!r}")

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


# ------------------------------------------------------------------- lifecycle


@dataclass(frozen=True)
class FvgTransition:
    """One lifecycle change, for the report and for the replay test."""

    fvg_id: str
    bar_index: int
    at: datetime
    frm: FvgStatus
    to: FvgStatus
    price: float


@dataclass
class FvgBook:
    """Every gap in a series, with its lifecycle resolved."""

    symbol: str
    timeframe: str
    fvgs: list[Fvg] = field(default_factory=list)
    transitions: list[FvgTransition] = field(default_factory=list)

    def by_status(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.fvgs:
            out[f.status.value] = out.get(f.status.value, 0) + 1
        return dict(sorted(out.items()))

    def available_at(
        self, bar: int, direction: FvgDirection | None = None
    ) -> list[Fvg]:
        """Gaps that were confirmed by ``bar`` and still UNMITIGATED at it."""
        return [
            f
            for f in self.fvgs
            if f.confirmed_index <= bar
            and (direction is None or f.direction is direction)
            and f.is_available_at(bar)
        ]

    def touched(self) -> list[Fvg]:
        return [f for f in self.fvgs if f.first_touch_index is not None]

    def fill_curve(self, horizons: Sequence[int]) -> dict[int, float]:
        """SPEC 12.6: proportion mitigated within k bars of confirmation.

        Gaps whose k-bar window runs past the end of the series are excluded from that
        horizon rather than counted as unfilled -- right-censoring them would make the
        curve sag at the long end purely as an artefact of where the data stops.
        """
        out: dict[int, float] = {}
        if not self.fvgs:
            return {k: float("nan") for k in horizons}
        last = max(f.confirmed_index for f in self.fvgs)
        for k in horizons:
            eligible = [f for f in self.fvgs if f.confirmed_index + k <= last]
            if not eligible:
                out[k] = float("nan")
                continue
            filled = sum(
                1
                for f in eligible
                if f.mitigated_index is not None and f.mitigated_index - f.confirmed_index <= k
            )
            out[k] = filled / len(eligible)
        return out

    def bars_to_mitigation(self) -> list[int]:
        return [
            f.mitigated_index - f.confirmed_index
            for f in self.fvgs
            if f.mitigated_index is not None
        ]


def track_fvgs(
    series: BarSeries,
    cfg: AppConfig,
    fvgs: Sequence[Fvg] | None = None,
    atr: np.ndarray | None = None,
) -> FvgBook:
    """Run the SPEC 12.2 lifecycle over ``series``, one bar at a time.

    Works on **copies**, so the list handed in keeps whatever state it had.  Detection
    output is shared with the displacement engine (SPEC 10.2), and a tracker that
    mutated it in place would make the displacement filter's behaviour depend on whether
    anyone had run the tracker first.

    Ordering inside a bar is fixed and matters:

    1. **expiry**, checked before anything else, so a gap that ages out on the same bar
       price finally reaches it is EXPIRED -- it was already unusable when the bar
       opened;
    2. **touch**, which sets ``first_touch_index`` and PARTIAL;
    3. **mitigation**, which is why a bar that trades through the zone and closes beyond
       it is MITIGATED rather than INVALIDATED;
    4. **invalidation**, the SPEC 12.5 gap-over case -- price left the zone behind
       without using it.
    """
    if fvgs is None:
        fvgs = detect_fvgs(series, cfg)
    if atr is None:
        atr = atr_ref(series, cfg.atr.period)

    book = FvgBook(series.symbol, series.timeframe)
    book.fvgs = [replace(f) for f in fvgs]
    live: list[Fvg] = []
    by_confirm: dict[int, list[Fvg]] = {}
    for f in book.fvgs:
        by_confirm.setdefault(f.confirmed_index, []).append(f)

    mode = cfg.fvg.mitigation_mode
    for i in range(series.n):
        at = from_epoch_s(series.close_time[i])
        hi, lo, close = float(series.high[i]), float(series.low[i]), float(series.close[i])
        a = atr[i]
        buffer = (
            cfg.fvg.invalidate_buffer_atr * float(a)
            if np.isfinite(a) and a > 0
            else 0.0
        )

        still: list[Fvg] = []
        for f in live:
            frm = f.status

            if f.age_at(i) > cfg.fvg.max_age_bars:
                f.status = FvgStatus.EXPIRED
                f.expired_index = i
                book.transitions.append(
                    FvgTransition(f.id, i, at, frm, f.status, close)
                )
                continue

            if f.first_touch_index is None and f.touched_by(hi, lo):
                f.first_touch_index = i
                f.first_touch_at = at
                f.status = FvgStatus.PARTIAL
                book.transitions.append(
                    FvgTransition(f.id, i, at, frm, f.status, close)
                )
                frm = FvgStatus.PARTIAL

            # Mitigation requires the bar to have actually been inside the zone, not
            # merely to have passed its level on the way through (SPEC 12.5).
            target = f.mitigation_price(mode)
            reached = f.touched_by(hi, lo) and (
                lo <= target if f.direction is FvgDirection.BULLISH else hi >= target
            )
            if reached:
                f.status = FvgStatus.MITIGATED
                f.mitigated_index = i
                f.mitigated_at = at
                book.transitions.append(
                    FvgTransition(f.id, i, at, frm, f.status, target)
                )
                continue

            gone = (
                close < f.zone_low - buffer
                if f.direction is FvgDirection.BULLISH
                else close > f.zone_high + buffer
            )
            if gone:
                f.status = FvgStatus.INVALIDATED
                f.invalidated_index = i
                book.transitions.append(
                    FvgTransition(f.id, i, at, frm, f.status, close)
                )
                continue

            still.append(f)

        # Gaps confirmed by THIS bar join only now: bar n is the bar that creates the
        # gap, and testing it against its own third bar would mitigate a gap with the
        # move that made it.
        live = still + by_confirm.get(i, [])

    return book


# ------------------------------------------------------------------- selection


def select_fvg(
    fvgs: Sequence[Fvg],
    leg_start: int,
    leg_end: int,
    direction: FvgDirection,
    cfg: AppConfig,
    *,
    at_bar: int | None = None,
    price: float | None = None,
) -> Fvg | None:
    """SPEC 12.3.  The gap entry model C would use, or ``None``.

    Qualifying = direction matches the setup, confirmed inside the displacement leg
    ``[a..b]`` (SPEC 10.1), and **UNMITIGATED as of ``at_bar``** -- which defaults to the
    leg end, the bar the setup confirms on.  Availability is read through
    ``status_at``: a gap mitigated later must still count as available now, and the
    stored end-of-run ``status`` would say otherwise.

    ``nearest`` needs the price to measure from and falls back to ``first`` without one,
    rather than silently ranking by an edge it cannot compare.
    """
    bar = leg_end if at_bar is None else at_bar
    qualifying = [
        f
        for f in fvgs_in_leg(list(fvgs), leg_start, leg_end, direction)
        if f.is_available_at(bar)
    ]
    if not qualifying:
        return None

    mode = cfg.fvg.selection
    if mode == "first":
        return min(qualifying, key=lambda f: (f.confirmed_index, f.id))
    if mode == "largest":
        return max(qualifying, key=lambda f: (f.size_atr, -f.confirmed_index))
    if mode == "nearest":
        if price is None:
            return min(qualifying, key=lambda f: (f.confirmed_index, f.id))
        return min(qualifying, key=lambda f: (abs(f.proximal - price), f.confirmed_index))
    raise ValueError(f"unknown fvg.selection {mode!r}")
