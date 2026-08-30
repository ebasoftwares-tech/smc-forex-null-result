"""First-target placement and the minimum-RR gate (SPEC 17.1, 17.2).

**Scope, and why this half of SPEC 17 is here rather than in Phase 14.** ``RR_BELOW_MIN``
fires in state CHOCH_CONFIRMED (SPEC 19 item 16) -- the same state as SPEC 16.3's stop
caps and SPEC 18.2's sizing rejections. Those three are the pre-trade rejections Phase
13's gate exists to exercise, and implementing two of the three would leave the gate half
met. What is *not* here is management: 17.3's break-even and trailing, 17.4's time and
calendar exits, and the execution of T3's ladder and T4's trail all need an open trade.

Two findings fell out of implementing the gate, both recorded in D-014.

**T3 cannot pass its own gate under the defaults (D-014 section 1).** SPEC 17.2 measures
``rr = |tp_1 - entry| / sl_distance`` and rejects below ``tp.min_rr`` (default 1.5). T3's
``tp_1`` is the first rung of its ladder, which SPEC 17.1 fixes at **1R**. So ``rr`` is
1.0 by construction, 1.0 < 1.5, and T3 is rejected on every setup it will ever see. That
is not a tuning opportunity -- ``min_rr`` is an ABLATION parameter and BACKTEST_PROTOCOL
section 10.2 forbids moving one to make a result appear -- it is a contradiction between
two specification defaults, and it needs an explicit decision rather than a quiet edit.

**T4 is exempt from the gate, which makes the T1-T4 ablation unpaired (D-014 section 6).**
T4 has no fixed target at all: it exits on the first opposing CHoCH. There is no ``tp_1``
to divide by ``sl_distance``, so the gate cannot be applied, so T4 accepts a population of
setups that T1-T3 reject. SPEC 17.7 calls for "paired T1-T4 variants on a shared setup
stream"; the streams are not shared, and any comparison has to say so or be wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from bot.config.schema import AppConfig
from bot.core.displacement import Direction
from bot.core.liquidity import LiquidityLevel, Side


class TargetModel(str, Enum):
    """SPEC 17.1."""

    T1_FIXED_R = "fixed_r"
    T2_OPPOSING_LIQUIDITY = "opposing_liquidity"
    T3_PARTIAL_LADDER = "partial_ladder"
    T4_STRUCTURE_TRAIL = "structure_trail"


class TargetReject(str, Enum):
    RR_BELOW_MIN = "RR_BELOW_MIN"
    NO_TARGET_AVAILABLE = "NO_TARGET_AVAILABLE"


@dataclass(frozen=True)
class TargetPlan:
    """The first target and the RR the SPEC 17.2 gate measured on it.

    ``price`` is ``None`` only for T4, which has no fixed target by design. ``rr`` is
    ``None`` in the same case, and the distinction between "no target" and "a target with
    an RR of zero" is load-bearing: the first is exempt from the gate and the second fails
    it.
    """

    model: TargetModel
    price: float | None
    rr: float | None
    reason: TargetReject | None = None
    reference_id: str | None = None

    @property
    def ok(self) -> bool:
        return self.reason is None

    @property
    def gate_applies(self) -> bool:
        """False only for T4 -- see the module docstring and D-014 section 6."""
        return self.model is not TargetModel.T4_STRUCTURE_TRAIL


def _opposing_side(direction: Direction) -> Side:
    """A long targets **buy-side** liquidity above; a short targets sell-side below.

    Inverted until D-019, with a docstring that contradicted the project's own vocabulary:
    ``liquidity.Side`` defines BUY_SIDE as the pool sitting *above* price, so "sell-side
    liquidity above" names something that cannot exist.

    "Opposing" is opposing to the side the setup **swept**. A bullish setup sweeps
    SELL_SIDE liquidity below and runs toward the BUY_SIDE pool above -- which is what
    SPEC 17.1's worked example does when it takes "nearest opposing liquidity PDH" as the
    target for a BUY LIMIT: a previous-day HIGH is a BUY_SIDE level.
    """
    return Side.BUY_SIDE if direction is Direction.BULLISH else Side.SELL_SIDE


def select_target_level(
    levels: Sequence[LiquidityLevel],
    cfg: AppConfig,
    *,
    direction: Direction,
    entry_price: float,
    ranks: dict[str, float] | None = None,
) -> LiquidityLevel | None:
    """SPEC 17.1's T2: the **nearest** ACTIVE opposing level clearing ``min_target_rank``.

    Nearest rather than best-ranked, deliberately and per the specification: the target is
    the first pool of liquidity price has to reach, and a higher-ranked level further away
    does not make the nearer one stop existing. Rank is a *filter* here, not the ordering.

    ``ranks`` is injected rather than recomputed, because ``LiquidityEngine.rank`` depends
    on the bar index and this function must not be able to see a bar the caller has not
    reached.
    """
    ranks = ranks or {}
    side = _opposing_side(direction)
    bullish = direction is Direction.BULLISH
    best: LiquidityLevel | None = None
    best_gap = float("inf")
    for lvl in levels:
        if not lvl.is_active or lvl.side is not side:
            continue
        beyond = lvl.price > entry_price if bullish else lvl.price < entry_price
        if not beyond:
            continue
        if ranks.get(lvl.id, 0.0) < cfg.tp.min_target_rank:
            continue
        gap = abs(lvl.price - entry_price)
        if gap < best_gap:
            best, best_gap = lvl, gap
    return best


def first_target(
    cfg: AppConfig,
    *,
    direction: Direction,
    entry_price: float,
    stop: float,
    atr_value: float,
    model: TargetModel | str | None = None,
    levels: Sequence[LiquidityLevel] = (),
    ranks: dict[str, float] | None = None,
) -> TargetPlan:
    """Place ``tp_1`` and apply the SPEC 17.2 gate.

    ``below_min_rr_action`` is ``skip`` and is not configurable: SPEC 17.2 names
    ``fixed_fallback`` as the alternative and rejects it in the same paragraph -- falling
    back to a fixed-R target when the structural one is too close means taking the trade
    without the reason for taking it, and it contaminates the T2 population with T1
    trades. An option that must never be selected is not offered.
    """
    m = TargetModel(model or cfg.tp.model)
    sl_distance = abs(entry_price - stop)
    bullish = direction is Direction.BULLISH
    sign = 1.0 if bullish else -1.0
    ref: str | None = None

    if sl_distance <= 0:
        return TargetPlan(m, None, None, TargetReject.NO_TARGET_AVAILABLE)

    if m is TargetModel.T4_STRUCTURE_TRAIL:
        # No fixed target exists, so SPEC 17.2 has nothing to measure.  Exempt, and the
        # exemption is the finding -- see D-014 section 6.
        return TargetPlan(m, None, None, None)

    if m is TargetModel.T1_FIXED_R:
        price = entry_price + sign * cfg.tp.r_multiple * sl_distance
    elif m is TargetModel.T3_PARTIAL_LADDER:
        # tp_1 is the ladder's FIRST rung, not its last: SPEC 17.2 gates on the first
        # target, and 17.1 puts T3's first partial at 1R.  This is what makes T3
        # unreachable under the default min_rr of 1.5.
        price = entry_price + sign * cfg.tp.ladder_first_r * sl_distance
    elif m is TargetModel.T2_OPPOSING_LIQUIDITY:
        lvl = select_target_level(
            levels, cfg, direction=direction, entry_price=entry_price, ranks=ranks
        )
        if lvl is None:
            return TargetPlan(m, None, None, TargetReject.NO_TARGET_AVAILABLE)
        # In FRONT of the level, never at it (SPEC 17.1): the level is where the resting
        # orders are, and an order at the same price is behind all of them.
        price = lvl.price - sign * cfg.tp.target_buffer_atr * atr_value
        ref = lvl.id
        beyond = price > entry_price if bullish else price < entry_price
        if not beyond:
            # The buffer pulled the target back through the entry -- the level was closer
            # than the buffer is wide.  Not a target.
            return TargetPlan(m, None, None, TargetReject.NO_TARGET_AVAILABLE, ref)
    else:  # pragma: no cover - the enum is exhaustive
        return TargetPlan(m, None, None, TargetReject.NO_TARGET_AVAILABLE)

    rr = abs(price - entry_price) / sl_distance
    if rr < cfg.tp.min_rr:
        return TargetPlan(m, float(price), rr, TargetReject.RR_BELOW_MIN, ref)
    return TargetPlan(m, float(price), rr, None, ref)


def gate_is_reachable(cfg: AppConfig, model: TargetModel | str) -> bool:
    """Whether a model can pass SPEC 17.2's gate on *any* setup, given the config.

    A property of the configuration alone, computed rather than measured, because for T1
    and T3 the RR does not depend on the setup at all: both place ``tp_1`` at a fixed
    multiple of ``sl_distance``, so the ratio is that multiple and nothing about the
    market can change it. T3 fails at every default and T1 passes at every one, which is
    exactly the kind of statement that should be derived once and asserted rather than
    discovered in a rejection table.
    """
    m = TargetModel(model)
    if m is TargetModel.T1_FIXED_R:
        return cfg.tp.r_multiple >= cfg.tp.min_rr
    if m is TargetModel.T3_PARTIAL_LADDER:
        return cfg.tp.ladder_first_r >= cfg.tp.min_rr
    # T2 depends on where the liquidity is; T4 is exempt.
    return True
