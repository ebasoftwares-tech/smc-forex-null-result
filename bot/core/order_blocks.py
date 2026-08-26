"""Order Blocks (SPEC 13) — four definitions, and the machinery to compare them.

SPEC 13.1 opens by admitting the problem rather than papering over it:

> *"'The last opposing candle before a move that breaks structure' is the standard
> formulation and it is under-specified in three places: **which** opposing candle when
> several are adjacent, whether the zone is the body or the full range, and whether
> 'before the move' means before the first displacement bar or before the leg origin.
> Different choices produce zones tens of pips apart, which for a stop-based strategy is
> the difference between a win and a loss."*

So this module implements **four pre-registered variants**, not one rule with options,
and Phase 11's gate is the bake-off between them rather than any one variant's
performance. `bot/research/ob_study.py` builds the agreement matrix SPEC 13.8 requires.

Four things worth knowing before changing anything here.

**The validity constraints are what stop OB-A being trivial.**  SPEC 13.4 constraint 1 --
the leg starting at the OB must itself displace -- is the difference between "the last
opposing candle" and "the last red candle", and the second one always exists within a few
bars on any chart. A proposal that skipped the constraints would report a near-100% hit
rate for every definition and measure nothing.

**Agreement is the deliverable, not a diagnostic.**  SPEC 13.6 says it outright about
OB-A and OB-C: *"if OB-A and OB-C select the same bar 80% of the time, they are not two
hypotheses."*  The count feeds the multiple-testing correction directly.

**OB-D is under-specified in a way A/B/C are not**, and it is flagged rather than quietly
resolved. A, B and C all key off the displacement leg of the setup in hand. D describes a
*different* structural event -- a failed move whose swing high was later traded through --
in one line, without saying which swing, how far back, or what "broken downward" means for
a high. The reading implemented here is documented at `_ob_d` and its
`NO_OB_AVAILABLE` rate is reported, because SPEC 13.7 makes that rate a quality signal
for a definition rather than a defect.

**The zone is the candle, but the lifecycle is FVG's.**  SPEC 13.5 says "identical in
shape to §12.2", so `status_at(bar)` is the accessor and the stored `status` is the
end-of-run value -- the same lookahead trap D-011 §3 records for gaps.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Sequence

import numpy as np

from bot.config.schema import AppConfig
from bot.core.bars import BarSeries, from_epoch_s
from bot.core.displacement import Direction
from bot.core.indicators import atr_ref
from bot.core.swings import Swing, SwingKind


class ObDefinition(str, Enum):
    """SPEC 13.2's four candidates."""

    A_LAST_OPPOSING = "last_opposing"
    B_LAST_DOWN_CLOSE_BEFORE_BREAK = "last_down_close_before_break"
    C_EXTREME_ORIGIN = "extreme_origin"
    D_BREAKER = "breaker"


class ObStatus(str, Enum):
    """SPEC 13.5 -- identical in shape to the FVG lifecycle (SPEC 12.2)."""

    UNMITIGATED = "UNMITIGATED"
    PARTIAL = "PARTIAL"
    MITIGATED = "MITIGATED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"

    @property
    def is_terminal(self) -> bool:
        return self in (ObStatus.MITIGATED, ObStatus.INVALIDATED, ObStatus.EXPIRED)


class ObReject(str, Enum):
    """Why a definition produced nothing.

    SPEC 13.7: *"the frequency of this is a quality signal for the definition"* -- so the
    reasons are enumerated and counted rather than collapsed into a None.
    """

    NO_OPPOSING_BAR = "NO_OPPOSING_BAR"
    NO_FAILED_MOVE = "NO_FAILED_MOVE"  # OB-D only
    NO_DISPLACEMENT = "NO_DISPLACEMENT"  # constraint 1
    TOO_FAR = "TOO_FAR"  # constraint 4
    TOO_OLD = "TOO_OLD"  # constraint 5
    ABOVE_REFERENCE = "OB_ABOVE_REFERENCE"  # SPEC 13.7
    DEGENERATE = "DEGENERATE"


@dataclass
class OrderBlock:
    """One proposed zone, plus when each lifecycle transition happened to it."""

    id: str
    symbol: str
    timeframe: str
    definition: ObDefinition
    direction: Direction
    origin_index: int
    zone_low: float
    zone_high: float
    zone_mode: str
    formed_at: datetime
    proposed_index: int  # the bar the setup confirmed on; the OB is knowable from here
    size_atr: float
    status: ObStatus = ObStatus.UNMITIGATED

    first_touch_index: int | None = None
    mitigated_index: int | None = None
    invalidated_index: int | None = None
    expired_index: int | None = None
    first_touch_at: datetime | None = None
    mitigated_at: datetime | None = None
    _closes_beyond: int = 0

    @property
    def ce(self) -> float:
        return (self.zone_low + self.zone_high) / 2.0

    @property
    def proximal(self) -> float:
        """SPEC 13.3: the edge nearest current price.

        The high for a bullish OB, which price approaches from above -- the same
        orientation as an FVG's proximal edge, and the same trap SPEC 12.1 fell into
        (D-011 §1).
        """
        return self.zone_high if self.direction is Direction.BULLISH else self.zone_low

    @property
    def distal(self) -> float:
        return self.zone_low if self.direction is Direction.BULLISH else self.zone_high

    @property
    def terminal_index(self) -> int | None:
        for i in (self.mitigated_index, self.invalidated_index, self.expired_index):
            if i is not None:
                return i
        return None

    def age_at(self, bar: int) -> int:
        return bar - self.origin_index

    def status_at(self, bar: int) -> ObStatus:
        """Status as of the close of ``bar``.

        Use this, never the stored ``status``, to decide availability at a past bar --
        the field holds the end-of-run value. Same rule, and same reason, as
        ``Fvg.status_at`` (D-011 §3).
        """
        t = self.terminal_index
        if t is not None and bar >= t:
            if self.mitigated_index == t:
                return ObStatus.MITIGATED
            if self.invalidated_index == t:
                return ObStatus.INVALIDATED
            return ObStatus.EXPIRED
        if self.first_touch_index is not None and bar >= self.first_touch_index:
            return ObStatus.PARTIAL
        return ObStatus.UNMITIGATED

    def is_available_at(self, bar: int) -> bool:
        return self.status_at(bar) is ObStatus.UNMITIGATED

    def touched_by(self, high: float, low: float) -> bool:
        """Range intersection, for the reason D-011 §2 records for gaps: a one-sided
        test counts a bar that jumped past the zone as having entered it."""
        return low <= self.zone_high and high >= self.zone_low


@dataclass(frozen=True)
class ObProposal:
    """What one definition produced for one setup -- a block, or a named reason."""

    definition: ObDefinition
    ob: OrderBlock | None
    reason: ObReject | None = None

    @property
    def ok(self) -> bool:
        return self.ob is not None


# ------------------------------------------------------------------- the zone


def zone_for(
    series: BarSeries, index: int, direction: Direction, mode: str
) -> tuple[float, float]:
    """SPEC 13.3.  ``(zone_low, zone_high)`` for the bar at ``index``."""
    o = float(series.open[index])
    h = float(series.high[index])
    l = float(series.low[index])
    c = float(series.close[index])
    if mode == "full_range":
        return l, h
    if mode == "body":
        return min(o, c), max(o, c)
    if mode == "wick_to_open":
        # Bullish: [L, O]. Mirrored for bearish, where the wick runs the other way.
        return (l, o) if direction is Direction.BULLISH else (o, h)
    raise ValueError(f"unknown ob.zone_mode {mode!r}")


# ------------------------------------------------------- the four origin searches


def _opposing(series: BarSeries, i: int, direction: Direction) -> bool:
    """SPEC 13.7: a doji is **not** opposing.  Strict ``C < O`` for the bullish case."""
    o, c = float(series.open[i]), float(series.close[i])
    return c < o if direction is Direction.BULLISH else c > o


def _ob_a(series, cfg, direction, s, a_disp, b) -> int | None:
    """OB-A ``last_opposing``: the last opposing bar strictly before the leg origin."""
    floor = max(0, a_disp - cfg.ob.max_lookback_bars)
    for i in range(a_disp - 1, floor - 1, -1):
        if _opposing(series, i, direction):
            return i
    return None


def _ob_b(series, cfg, direction, s, a_disp, b) -> int | None:
    """OB-B ``last_down_close_before_break``: the last opposing bar before the break bar.

    Differs from OB-A only when the displacement leg itself contains an opposing bar --
    a two-bar drive with a red middle bar, say. How often that happens is exactly what
    the agreement matrix measures.
    """
    floor = max(0, b - cfg.ob.max_lookback_bars)
    for i in range(b - 1, floor - 1, -1):
        if _opposing(series, i, direction):
            return i
    return None


def _ob_c(series, cfg, direction, s, a_disp, b) -> int | None:
    """OB-C ``extreme_origin``: the extreme bar of ``[s, a_disp]``.

    SPEC 13.6 notes this is *"the sweep-extreme bar itself in most cases"*, and that the
    overlap with OB-A is worth measuring rather than assuming.
    """
    lo_i, hi_i = min(s, a_disp), max(s, a_disp)
    if hi_i < lo_i or lo_i < 0 or hi_i >= series.n:
        return None
    window = (
        series.low[lo_i : hi_i + 1]
        if direction is Direction.BULLISH
        else series.high[lo_i : hi_i + 1]
    )
    off = int(np.argmin(window) if direction is Direction.BULLISH else np.argmax(window))
    return lo_i + off


def _ob_d(series, cfg, direction, s, a_disp, b, swings: Sequence[Swing]) -> int | None:
    """OB-D ``breaker``, on the reading documented here.

    **SPEC 13.2 describes this one in a single line and leaves more open than it closes.**
    A, B and C all key off the displacement leg of the setup in hand; D points at a
    *different* structural event -- *"the last opposing bar of the failed move: the up-bar
    before a swing high that was subsequently broken downward, now used as
    resistance-turned-support"* -- without saying which swing high, how far back to look,
    or what "broken downward" means for a level that is broken upward by definition.

    The reading taken, for a bullish setup: find the most recent confirmed swing **low**
    before the sweep whose price the sweep traded *below* -- the failed move, whose
    support gave way -- and return the last bar before that swing formed which closed in
    the direction of the failed move (an up-bar, for a low that failed). That bar's zone
    is the breaker: a level that stopped working as support, which the reversal is now
    expected to reclaim.

    Two other readings are defensible, and this is a **flagged ambiguity rather than a
    resolved one** (see D-012). Its ``NO_OB_AVAILABLE`` rate is reported rather than
    hidden, because SPEC 13.7 makes that rate a quality signal for a definition.
    """
    kind = SwingKind.LOW if direction is Direction.BULLISH else SwingKind.HIGH
    extreme = float(series.low[s]) if direction is Direction.BULLISH else float(series.high[s])
    floor = max(0, s - cfg.ob.max_lookback_bars * 3)

    candidates = [
        sw
        for sw in swings
        if sw.kind is kind
        and sw.confirmed_index <= s
        and floor <= sw.formed_index < s
        and (extreme < sw.price if direction is Direction.BULLISH else extreme > sw.price)
    ]
    if not candidates:
        return None
    failed = max(candidates, key=lambda sw: sw.formed_index)

    # The last bar before the failed swing formed that closed WITH the move that failed.
    for i in range(failed.formed_index - 1, max(0, failed.formed_index - cfg.ob.max_lookback_bars) - 1, -1):
        if not _opposing(series, i, direction):
            return i
    return None


#: The search functions, keyed by definition.  OB-D takes an extra argument, which is why
#: dispatch is explicit rather than a dict of uniform callables.
_SEARCHES = {
    ObDefinition.A_LAST_OPPOSING: _ob_a,
    ObDefinition.B_LAST_DOWN_CLOSE_BEFORE_BREAK: _ob_b,
    ObDefinition.C_EXTREME_ORIGIN: _ob_c,
}


# --------------------------------------------------------------------- proposal


def propose(
    series: BarSeries,
    cfg: AppConfig,
    *,
    direction: Direction,
    sweep_extreme_bar: int,
    leg_start: int,
    break_bar: int,
    reference_price: float,
    displacement_confirmed: bool,
    definition: ObDefinition | str | None = None,
    swings: Sequence[Swing] = (),
    atr: np.ndarray | None = None,
    seq: int = 0,
) -> ObProposal:
    """Propose an order block for one confirmed setup, or say why not.

    The five SPEC 13.4 constraints are applied in a fixed order, and each failure is
    named. Constraint 1 (the leg must displace) is checked first and is the one that
    stops OB-A degenerating into "the last red candle".
    """
    d = ObDefinition(definition or cfg.ob.definition)
    if atr is None:
        atr = atr_ref(series, cfg.atr.period)

    if not displacement_confirmed:
        return ObProposal(d, None, ObReject.NO_DISPLACEMENT)
    if break_bar >= series.n or leg_start < 0 or sweep_extreme_bar < 0:
        return ObProposal(d, None, ObReject.DEGENERATE)

    if d is ObDefinition.D_BREAKER:
        origin = _ob_d(series, cfg, direction, sweep_extreme_bar, leg_start, break_bar, swings)
        missing = ObReject.NO_FAILED_MOVE
    else:
        origin = _SEARCHES[d](series, cfg, direction, sweep_extreme_bar, leg_start, break_bar)
        missing = ObReject.NO_OPPOSING_BAR
    if origin is None:
        return ObProposal(d, None, missing)

    a = atr[break_bar]
    if not np.isfinite(a) or a <= 0:
        return ObProposal(d, None, ObReject.DEGENERATE)
    a = float(a)

    if break_bar - origin > cfg.ob.max_age_bars:
        return ObProposal(d, None, ObReject.TOO_OLD)

    zl, zh = zone_for(series, origin, direction, cfg.ob.zone_mode)
    if zh <= zl:
        # A zone with no height cannot be touched or invalidated; it is not an object.
        return ObProposal(d, None, ObReject.DEGENERATE)
    proximal = zh if direction is Direction.BULLISH else zl

    if abs(proximal - reference_price) > cfg.ob.max_distance_atr * a:
        return ObProposal(d, None, ObReject.TOO_FAR)

    # SPEC 13.7: entering beyond the level whose break defined the setup means the
    # "retracement" entry is not a retracement.
    beyond = (
        proximal > reference_price
        if direction is Direction.BULLISH
        else proximal < reference_price
    )
    if beyond:
        return ObProposal(d, None, ObReject.ABOVE_REFERENCE)

    return ObProposal(
        d,
        OrderBlock(
            id=f"{series.symbol}:{series.timeframe}:OB:{d.value[:4]}:{seq:05d}",
            symbol=series.symbol,
            timeframe=series.timeframe,
            definition=d,
            direction=direction,
            origin_index=origin,
            zone_low=zl,
            zone_high=zh,
            zone_mode=cfg.ob.zone_mode,
            formed_at=from_epoch_s(series.open_time[origin]),
            proposed_index=break_bar,
            size_atr=(zh - zl) / a,
        ),
    )


# ------------------------------------------------------------------- lifecycle


@dataclass(frozen=True)
class ObTransition:
    ob_id: str
    bar_index: int
    at: datetime
    frm: ObStatus
    to: ObStatus
    price: float


@dataclass
class ObBook:
    symbol: str
    timeframe: str
    blocks: list[OrderBlock] = field(default_factory=list)
    transitions: list[ObTransition] = field(default_factory=list)

    def by_status(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for b in self.blocks:
            out[b.status.value] = out.get(b.status.value, 0) + 1
        return dict(sorted(out.items()))

    def touched(self) -> list[OrderBlock]:
        return [b for b in self.blocks if b.first_touch_index is not None]

    def fill_curve(self, horizons: Sequence[int], n_bars: int) -> dict[int, float]:
        """Proportion mitigated within k bars of proposal.

        Blocks whose k-bar window runs past the end of the series are excluded from that
        horizon rather than counted unfilled -- the same censoring rule as
        ``FvgBook.fill_curve``, and for the same reason.
        """
        out: dict[int, float] = {}
        for k in horizons:
            eligible = [b for b in self.blocks if b.proposed_index + k < n_bars]
            if not eligible:
                out[k] = float("nan")
                continue
            filled = sum(
                1
                for b in eligible
                if b.mitigated_index is not None
                and b.mitigated_index - b.proposed_index <= k
            )
            out[k] = filled / len(eligible)
        return out

    def bars_to_mitigation(self) -> list[int]:
        return [
            b.mitigated_index - b.proposed_index
            for b in self.blocks
            if b.mitigated_index is not None
        ]


def track_order_blocks(
    series: BarSeries,
    cfg: AppConfig,
    blocks: Sequence[OrderBlock],
    atr: np.ndarray | None = None,
) -> ObBook:
    """SPEC 13.5's lifecycle, one bar at a time.

    Works on copies, so a caller can track the same proposals under several
    configurations without the first run contaminating the second.

    Tracking starts the bar **after** the block is proposed. The proposal bar is the
    break bar of the setup, and letting the move that confirmed the setup also mitigate
    its own order block would fill every one of them instantly.
    """
    if atr is None:
        atr = atr_ref(series, cfg.atr.period)
    book = ObBook(series.symbol, series.timeframe)
    book.blocks = [replace(b) for b in blocks]

    by_start: dict[int, list[OrderBlock]] = {}
    for b in book.blocks:
        by_start.setdefault(b.proposed_index, []).append(b)

    live: list[OrderBlock] = []
    for i in range(series.n):
        at = from_epoch_s(series.close_time[i])
        hi, lo, close = float(series.high[i]), float(series.low[i]), float(series.close[i])

        still: list[OrderBlock] = []
        for b in live:
            frm = b.status

            if b.age_at(i) > cfg.ob.max_age_bars:
                b.status = ObStatus.EXPIRED
                b.expired_index = i
                book.transitions.append(ObTransition(b.id, i, at, frm, b.status, close))
                continue

            if b.first_touch_index is None and b.touched_by(hi, lo):
                b.first_touch_index = i
                b.first_touch_at = at
                b.status = ObStatus.PARTIAL
                book.transitions.append(ObTransition(b.id, i, at, frm, b.status, close))
                frm = ObStatus.PARTIAL

            # Mitigation at the midpoint, matching the FVG default (`ce`): the OB
            # lifecycle is "identical in shape" to SPEC 12.2 and there is no separate
            # ob.mitigation_mode in the registry.
            if b.touched_by(hi, lo) and (
                lo <= b.ce if b.direction is Direction.BULLISH else hi >= b.ce
            ):
                b.status = ObStatus.MITIGATED
                b.mitigated_index = i
                b.mitigated_at = at
                book.transitions.append(ObTransition(b.id, i, at, frm, b.status, b.ce))
                continue

            beyond = (
                close < b.distal if b.direction is Direction.BULLISH else close > b.distal
            )
            if beyond:
                b._closes_beyond += 1
                if b._closes_beyond >= cfg.ob.invalidate_closes:
                    b.status = ObStatus.INVALIDATED
                    b.invalidated_index = i
                    book.transitions.append(
                        ObTransition(b.id, i, at, frm, b.status, close)
                    )
                    continue
            else:
                # SPEC 13.5 counts closes beyond the distal edge; a bar back inside the
                # zone means the level held, so the count restarts rather than
                # accumulating across unrelated excursions.
                b._closes_beyond = 0

            still.append(b)

        live = still + by_start.get(i, [])

    return book
