"""Entry models and fill resolution (SPEC 15).

Five models arm an order from one confirmed MSS, and every one of them can be flattered
by a careless backtest in a different way. Most of this module is the machinery that
stops that happening.

**Model A's fill price is the single easiest way to invent returns.** SPEC 15.3 is blunt
about it: filling at ``C_b`` — the close that triggered the signal — is a lookahead of one
full bar and *"typically adds 10-30% to headline returns on H4"*. Model A fills at the
**open of bar b+1**, or at the first M1 price after ``close_time(b) + exec.latency_ms``
when M1 exists. The close of ``b`` is not an obtainable price and is never used.

**A limit order price merely touches is not reliably filled.** SPEC 15.4 requires
``L_bar <= p - backtest.limit_fill_buffer_pips`` for a buy limit, because the queue may
never reach you. Assuming touch-fills is *"one of the largest silent optimisms in retail
backtesting, and it disproportionately flatters exactly the retracement models B-E"* —
that is, it flatters the four models being compared against the one baseline that cannot
benefit from it.

**Within one bar, the order of entry and stop is determined by continuity, not
ambiguous.** A bullish limit sits at ``p`` with its stop at ``s < p`` and price approaches
from above, so any continuous path that reaches ``s`` must pass ``p`` first: the entry
fills, always. The first version of this module treated a bar touching both as a coin
flip and resolved it "pessimistically" by cancelling. That was wrong twice over — the
physics says fill, and cancelling is not even the pessimistic *outcome*, since a fill that
then stops out loses 1R while a cancel loses nothing. It produced 15 false cancels on the
Phase 12 fixture. See D-013 §1.

So ``cancel_if`` clause 1 means what SPEC 15.1 actually says it means: *"a limit order can
fill on the way back up from a level that already invalidated the idea."* That needs price
to reach the stop **without having filled on the way**, which under continuity requires a
**gap** past both. Gap bars are the only ones where the bar-level rule has to guess, and
they are where ``backtest.intrabar_mode`` earns its keep.

``ohlc_heuristic`` is prohibited by SPEC 17.5 and is deliberately not offered as a config
value: an option that must never be selected should not be selectable.

**Scope.** Phase 12 arms orders and resolves fills. It does not size them (SPEC 18,
Phase 13), manage them (SPEC 17), or evaluate shadow trades (SPEC 15.6, which needs
``exit.max_bars_in_trade``). Only stop model S1 is implemented, because ``cancel_if``
clause 1 needs a planned stop before an order can be armed at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Sequence

import numpy as np

from bot.config.schema import AppConfig
from bot.core.bars import BarSeries, from_epoch_s
from bot.core.displacement import Direction
from bot.core.fvg import Fvg, FvgDirection, select_fvg
from bot.core.indicators import atr_ref
from bot.core.order_blocks import OrderBlock


class EntryModel(str, Enum):
    """SPEC 15.2's five models, run as five pre-registered variants."""

    A_MARKET = "A"
    B_RETRACEMENT = "B"
    C_FVG = "C"
    D_ORDER_BLOCK = "D"
    E_LEG_MIDPOINT = "E"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class ArmReject(str, Enum):
    """Why a model could not arm.  SPEC 15.7: the default is to invalidate, not fall back."""

    NO_FVG_AVAILABLE = "NO_FVG_AVAILABLE"
    NO_OB_AVAILABLE = "NO_OB_AVAILABLE"
    PRICE_THROUGH_STOP = "PRICE_THROUGH_STOP"
    NO_ATR = "NO_ATR"
    DEGENERATE = "DEGENERATE"


class FillState(str, Enum):
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    PENDING = "PENDING"


class CancelReason(str, Enum):
    """SPEC 15.1's four hard cancels."""

    SL_BEFORE_ENTRY = "SL_BEFORE_ENTRY"
    OPPOSING_SWEEP = "OPPOSING_SWEEP"
    BIAS_FLIP = "BIAS_FLIP"
    ENTRY_EXPIRED = "ENTRY_EXPIRED"


@dataclass(frozen=True)
class EntryPlan:
    """SPEC 15.1's common contract."""

    model: EntryModel
    order_type: OrderType
    direction: Direction
    price: float
    stop: float
    valid_from: datetime
    valid_from_bar: int  # the MSS bar; no order may exist before it
    expires_at: datetime
    expires_at_bar: int
    sl_model: str
    reference_id: str | None = None  # the FVG or OB the price came from

    @property
    def risk_distance(self) -> float:
        return abs(self.price - self.stop)


@dataclass(frozen=True)
class ArmResult:
    model: EntryModel
    plan: EntryPlan | None
    reason: ArmReject | None = None

    @property
    def ok(self) -> bool:
        return self.plan is not None


@dataclass(frozen=True)
class Fill:
    """The resolution of one armed order."""

    plan: EntryPlan
    state: FillState
    bar: int | None = None
    at: datetime | None = None
    price: float | None = None
    cancel_reason: CancelReason | None = None
    #: True when the deciding bar contained both the entry and the stop.  Continuity
    #: resolves it (entry first), so this is a diagnostic rather than an ambiguity.
    touched_both: bool = False
    #: True when the bar OPENED beyond the stop -- a real gap, and the only case where
    #: the bar-level rule genuinely cannot see what happened.
    gap_ambiguous: bool = False

    @property
    def filled(self) -> bool:
        return self.state is FillState.FILLED


# ----------------------------------------------------------------------- the stop


def planned_stop(
    series: BarSeries,
    cfg: AppConfig,
    *,
    direction: Direction,
    sweep_extreme: float,
    atr_value: float,
) -> float:
    """SPEC 16.1 model S1, which is all Phase 12 needs.

    The sweep extreme is the price at which the setup's premise is falsified: below it,
    the "sweep" was a breakout. The buffer is SPEC 16.2's ATR term only -- its spread and
    stops-level terms need a broker and real spread data (Q1/Q2), and inventing them
    would make every stop a property of the invention.
    """
    buffer = cfg.sl.buffer_atr * atr_value
    return (
        sweep_extreme - buffer
        if direction is Direction.BULLISH
        else sweep_extreme + buffer
    )


# ------------------------------------------------------------------------ arming


def _zone_price(low: float, high: float, direction: Direction, point: str) -> float:
    """A zone's proximal / ce / distal price, oriented by approach direction.

    Proximal is the edge price reaches first: the HIGH of a zone below a bullish setup.
    SPEC 12.1 labels this backwards for FVGs and D-011 §1 corrects it; the same
    orientation applies to order blocks.
    """
    if point == "ce":
        return (low + high) / 2.0
    proximal = high if direction is Direction.BULLISH else low
    distal = low if direction is Direction.BULLISH else high
    return proximal if point == "proximal" else distal


def arm(
    series: BarSeries,
    cfg: AppConfig,
    *,
    direction: Direction,
    mss_bar: int,
    leg_start: int,
    sweep_extreme: float,
    break_price: float,
    model: EntryModel | str | None = None,
    fvgs: Sequence[Fvg] = (),
    order_block: OrderBlock | None = None,
    atr: np.ndarray | None = None,
) -> ArmResult:
    """Arm one model's order on a confirmed MSS, or say why it could not.

    ``valid_from`` is ``close_time(mss_bar)``: SPEC 15.1 forbids an order existing before
    the MSS is confirmed, and the state machine treats that as an invariant rather than a
    guideline (SPEC 14.2 step 3).
    """
    m = EntryModel(model or cfg.entry.model)
    if atr is None:
        atr = atr_ref(series, cfg.atr.period)
    if mss_bar < 0 or mss_bar >= series.n or leg_start < 0 or leg_start > mss_bar:
        return ArmResult(m, None, ArmReject.DEGENERATE)

    a = atr[mss_bar]
    if not np.isfinite(a) or a <= 0:
        return ArmResult(m, None, ArmReject.NO_ATR)
    a = float(a)

    stop = planned_stop(
        series, cfg, direction=direction, sweep_extreme=sweep_extreme, atr_value=a
    )
    bullish = direction is Direction.BULLISH
    leg_low = float(series.low[leg_start : mss_bar + 1].min())
    leg_high = float(series.high[leg_start : mss_bar + 1].max())

    ref_id: str | None = None
    if m is EntryModel.A_MARKET:
        order_type = OrderType.MARKET
        # Placeholder: the real price is resolved at fill time from bar b+1's open, and
        # is deliberately NOT the close of b (SPEC 15.3).
        price = float(series.close[mss_bar])
    elif m is EntryModel.B_RETRACEMENT:
        order_type = OrderType.LIMIT
        span = break_price - leg_low if bullish else leg_high - break_price
        price = (
            leg_low + cfg.entry.retrace_pct * span
            if bullish
            else leg_high - cfg.entry.retrace_pct * span
        )
    elif m is EntryModel.C_FVG:
        order_type = OrderType.LIMIT
        gap = select_fvg(
            fvgs,
            leg_start,
            mss_bar,
            FvgDirection.BULLISH if bullish else FvgDirection.BEARISH,
            cfg,
            at_bar=mss_bar,
            price=float(series.close[mss_bar]),
        )
        if gap is None:
            return ArmResult(m, None, ArmReject.NO_FVG_AVAILABLE)
        price = _zone_price(gap.zone_low, gap.zone_high, direction, cfg.entry.fvg_entry_point)
        ref_id = gap.id
    elif m is EntryModel.D_ORDER_BLOCK:
        order_type = OrderType.LIMIT
        if order_block is None or not order_block.is_available_at(mss_bar):
            return ArmResult(m, None, ArmReject.NO_OB_AVAILABLE)
        price = _zone_price(
            order_block.zone_low, order_block.zone_high, direction, cfg.entry.ob_entry_point
        )
        ref_id = order_block.id
    elif m is EntryModel.E_LEG_MIDPOINT:
        order_type = OrderType.LIMIT
        # SPEC 15.2: E measures leg low to leg HIGH, B measures leg low to the BREAK
        # price. They coincide when the break bar makes the leg high -- common, but not
        # universal, and collapsing them would hide which measurement matters.
        price = (leg_low + leg_high) / 2.0
    else:  # pragma: no cover - the enum is exhaustive
        return ArmResult(m, None, ArmReject.DEGENERATE)

    # A limit already at or beyond its own stop is not an order, it is a loss waiting to
    # be booked. Rejected at arm time rather than cancelled on the first bar, so the
    # rejection log distinguishes "never armable" from "armed and then invalidated".
    if order_type is OrderType.LIMIT:
        through = price <= stop if bullish else price >= stop
        if through:
            return ArmResult(m, None, ArmReject.PRICE_THROUGH_STOP)

    expiry_bar = mss_bar + cfg.entry.pending_expiry_bars
    return ArmResult(
        m,
        EntryPlan(
            model=m,
            order_type=order_type,
            direction=direction,
            price=float(price),
            stop=float(stop),
            valid_from=from_epoch_s(series.close_time[mss_bar]),
            valid_from_bar=mss_bar,
            expires_at=from_epoch_s(
                series.close_time[min(expiry_bar, series.n - 1)]
            ),
            expires_at_bar=expiry_bar,
            sl_model=cfg.sl.model,
            reference_id=ref_id,
        ),
    )


# ------------------------------------------------------------- fill resolution


def _pip_size(symbol: str) -> float:
    return 0.01 if symbol.upper().endswith("JPY") else 0.0001


def _limit_touched(plan: EntryPlan, low: float, high: float, buffer: float) -> bool:
    """SPEC 15.4, including the buffer that makes a touch insufficient."""
    return (
        low <= plan.price - buffer
        if plan.direction is Direction.BULLISH
        else high >= plan.price + buffer
    )


def _stop_touched(plan: EntryPlan, low: float, high: float) -> bool:
    return low <= plan.stop if plan.direction is Direction.BULLISH else high >= plan.stop


def resolve_fill(
    series: BarSeries,
    cfg: AppConfig,
    plan: EntryPlan,
    *,
    opposing_sweep_bars: Sequence[int] = (),
    bias_flip_bars: Sequence[int] = (),
    m1: BarSeries | None = None,
) -> Fill:
    """Walk forward from the MSS bar and resolve the order (SPEC 15.1 / 15.4).

    Cancels are checked in SPEC 15.1's own order and the first to occur wins. A market
    order is resolved immediately at bar ``b+1``'s open, so only limits can be cancelled.

    **The within-bar ordering is not guessed.** A limit at ``p`` with its stop beyond it
    cannot have the stop reached first on a continuous path, so a bar touching both fills.
    The exception is a bar that *opens* beyond the stop: price gapped past the order
    without ever offering it, the idea is already invalidated, and a later touch would be
    exactly the *"fill on the way back up"* SPEC 15.1 clause 1 exists to prevent. That is
    the only place ``m1`` changes an answer, and it is consulted there.
    """
    buffer = cfg.backtest.limit_fill_buffer_pips * _pip_size(series.symbol)
    opposing = set(opposing_sweep_bars)
    flips = set(bias_flip_bars)
    bullish = plan.direction is Direction.BULLISH

    if plan.order_type is OrderType.MARKET:
        return _market_fill(series, cfg, plan, m1)

    last = min(plan.expires_at_bar, series.n - 1)
    for i in range(plan.valid_from_bar + 1, last + 1):
        at = from_epoch_s(series.close_time[i])

        if i in opposing:
            return Fill(plan, FillState.CANCELLED, i, at, None, CancelReason.OPPOSING_SWEEP)
        if cfg.entry.cancel_on_bias_flip and i in flips:
            return Fill(plan, FillState.CANCELLED, i, at, None, CancelReason.BIAS_FLIP)

        o = float(series.open[i])
        low, high = float(series.low[i]), float(series.high[i])
        hit_entry = _limit_touched(plan, low, high, buffer)
        hit_stop = _stop_touched(plan, low, high)
        opened_beyond_stop = o <= plan.stop if bullish else o >= plan.stop

        if opened_beyond_stop:
            # A true gap past the order. M1 is consulted because a series with finer
            # resolution may show the level was in fact offered before the stop.
            if m1 is not None and _entry_first_m1(series, m1, i, plan, buffer):
                return Fill(plan, FillState.FILLED, i, at, plan.price, None, hit_stop, True)
            return Fill(
                plan, FillState.CANCELLED, i, at, None,
                CancelReason.SL_BEFORE_ENTRY, hit_stop, True,
            )

        if hit_entry:
            # Continuity: the entry is at or above the stop on the approach path, so it
            # was reached first whether or not this bar also tagged the stop.
            return Fill(plan, FillState.FILLED, i, at, plan.price, None, hit_stop, False)

        if hit_stop:
            # Only reachable when the risk distance is under the fill buffer -- a
            # sub-pip stop. Economically degenerate, but it is a real branch and the
            # order is cancelled rather than silently left pending.
            return Fill(
                plan, FillState.CANCELLED, i, at, None, CancelReason.SL_BEFORE_ENTRY
            )

    at = from_epoch_s(series.close_time[last])
    if plan.expires_at_bar > series.n - 1:
        # The series ended before the order could expire on its own terms: censored, not
        # expired, and counting it as an expiry would understate every fill rate.
        return Fill(plan, FillState.PENDING, last, at)
    return Fill(plan, FillState.EXPIRED, last, at, None, CancelReason.ENTRY_EXPIRED)


def _market_fill(
    series: BarSeries, cfg: AppConfig, plan: EntryPlan, m1: BarSeries | None
) -> Fill:
    """SPEC 15.3.  Never ``C_b``.

    With M1, the fill is the first M1 price at or after ``close_time(b) + latency``. The
    two paths can differ by a pip or two and the report measures it, because the whole
    point of SPEC 15.3 is that this particular price is worth being careful about.
    """
    nxt = plan.valid_from_bar + 1
    if nxt >= series.n:
        return Fill(plan, FillState.PENDING, None, None)
    at = from_epoch_s(series.close_time[nxt])

    if m1 is not None:
        t = int(series.close_time[plan.valid_from_bar]) + cfg.exec.latency_ms // 1000
        idx = np.searchsorted(m1.open_time, t, side="left")
        if idx < m1.n:
            return Fill(plan, FillState.FILLED, nxt, at, float(m1.open[idx]))
    return Fill(plan, FillState.FILLED, nxt, at, float(series.open[nxt]))


def _entry_first_m1(
    series: BarSeries, m1: BarSeries, bar: int, plan: EntryPlan, buffer: float
) -> bool:
    """Whether the M1 path inside ``bar`` offered the entry before reaching the stop.

    Only consulted for a bar that opened beyond the stop, where the H4 bar alone cannot
    distinguish "price gapped past the order" from "price traded down through it and
    kept going". An unresolvable bar takes the cancelling reading rather than the
    convenient one.
    """
    lo = int(series.open_time[bar])
    hi = int(series.close_time[bar])
    start = int(np.searchsorted(m1.open_time, lo, side="left"))
    end = int(np.searchsorted(m1.open_time, hi, side="left"))
    for k in range(start, end):
        low, high = float(m1.low[k]), float(m1.high[k])
        if _stop_touched(plan, low, high):
            return False
        if _limit_touched(plan, low, high, buffer):
            return True
    return False
