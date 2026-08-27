"""The pre-trade chain: armed order -> sized trade, or a named rejection.

This is where SPEC 16.3's stop caps, SPEC 17.2's RR gate and SPEC 18's sizing and limits
meet. All three fire in the same state (CHOCH_CONFIRMED / WAITING_FOR_ENTRY, SPEC 19
items 16-20), and running them in one place is what lets the rejection log say *which*
constraint a setup died on rather than only that it died.

**The order is the specification's order, and it is load-bearing for the log rather than
for the outcome.** A setup that fails three checks fails all three whatever sequence they
run in; what changes is the reason recorded, and therefore what a rejection table means.
The chain runs cheapest-and-most-structural first -- geometry, then the stop caps, then
the RR gate, then sizing, then the portfolio limits -- so a rejection names the property
of *this setup* that was wrong before it names a property of the book it happened to
arrive into. A setup rejected for ``RISK_LIMIT_POSITIONS`` would have been fine tomorrow;
one rejected for ``SL_TOO_WIDE`` never would.

**Nothing here adjusts anything.** SPEC 16.3 forbids moving a stop to fit a cap and
SPEC 18.1 forbids raising risk, and both prohibitions are structural rather than
policed: there is no code path in this module that writes a price or raises a percentage.

**Every rejection carries the forward return** the setup would have had (SPEC 19's closing
rule), because that is what turns the rejection log into the counterfactual dataset that
answers "were our filters right?" without a second backtest run. Phase 13 records the
hook and the bar index; the return itself is filled in by the caller, which is the only
layer that knows the analysis horizon.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from bot.config.schema import AppConfig
from bot.core.displacement import Direction
from bot.core.entries import ArmReject, EntryPlan, OrderType
from bot.core.liquidity import LiquidityLevel
from bot.core.risk import (
    MissingConversionRate,
    RiskLedger,
    RiskReject,
    SizingResult,
    size_for_setup,
)
from bot.core.stops import StopCheck, StopReject, check_stop, symbol_spec
from bot.core.targets import TargetPlan, TargetReject, first_target


#: A placeholder for the geometry pass, which does not size.  Zero lots and zero risk
#: make it obvious in any record that escapes: a trade that reaches a report with these
#: numbers was never sized, rather than sized to nothing.
_UNSIZED_LOTS = 0.0


class Stage(str, Enum):
    """Which layer rejected, so a table can be read by layer as well as by reason."""

    ARM = "ARM"
    STOP = "STOP"
    TARGET = "TARGET"
    SIZING = "SIZING"
    LIMITS = "LIMITS"
    FILL = "FILL"
    ACCEPTED = "ACCEPTED"


@dataclass(frozen=True)
class TradePlan:
    """A setup that cleared every pre-trade check.  Still not a trade: it must fill."""

    entry: EntryPlan
    target: TargetPlan
    sizing: SizingResult
    stop_check: StopCheck
    risk_pct: float
    equity: float

    @property
    def lots(self) -> float:
        return self.sizing.lots

    @property
    def sl_distance(self) -> float:
        return self.stop_check.sl_distance


@dataclass(frozen=True)
class Decision:
    """Accepted or rejected, with the stage and the reason.

    ``reason`` is deliberately typed as a plain string holding an enum *value*: the four
    rejection enums live in four modules and unifying them into one would make each
    module depend on the others' vocabularies. What matters downstream is that the value
    is one of SPEC 19's catalogue names, which ``REASONS`` pins.
    """

    stage: Stage
    reason: str | None
    plan: TradePlan | None = None
    at: datetime | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.plan is not None


_UNSIZED = SizingResult(_UNSIZED_LOTS, 0.0, 0.0, 0.0, 0.0, None)


#: Every reason this chain can produce, as SPEC 19 names them.  A test asserts that the
#: set matches the enums, so a new rejection cannot be introduced without appearing here.
REASONS: frozenset[str] = frozenset(
    [r.value for r in ArmReject]
    + [r.value for r in StopReject]
    + [r.value for r in TargetReject]
    + [r.value for r in RiskReject]
)


def evaluate(
    cfg: AppConfig,
    plan: EntryPlan,
    *,
    symbol: str,
    atr_value: float,
    equity: float,
    ledger: RiskLedger | None = None,
    levels: tuple[LiquidityLevel, ...] = (),
    ranks: dict[str, float] | None = None,
    spread: float | None = None,
    quote_to_account: float | None = None,
    at: datetime | None = None,
    apply_limits: bool = True,
    skip_sizing: bool = False,
) -> Decision:
    """Run SPEC 16.3 -> 17.2 -> 18.2 -> 18.4 over one armed order.

    ``skip_sizing`` exists for the backtest engine's geometry pass. SPEC 18.2's rejections
    are functions of **equity**, so a pass that is meant to be portfolio-free must not run
    them -- otherwise the set of setups it produces depends on an account size, which is
    exactly what that pass exists not to depend on.

    ``apply_limits`` exists for SPEC 18.9's requirement that results be reported *both*
    with limits on and off: *"a strategy that is only profitable with a daily loss limit
    engaged is a strategy with a fragility the limit is hiding."* It switches off the
    portfolio limits only -- the per-setup rejections (stop caps, RR, sizing) are part of
    the strategy rather than part of the risk overlay, and turning those off would be
    reporting a different strategy, not the same one unprotected.
    """
    at = at or plan.valid_from
    entry_price = plan.price
    spec = symbol_spec(cfg, symbol)

    # --- SPEC 16.3 -----------------------------------------------------------
    sc = check_stop(
        cfg,
        symbol=symbol,
        direction=plan.direction,
        entry_price=entry_price,
        stop=plan.stop,
        atr_value=atr_value,
        spec=spec,
        spread=spread,
    )
    if not sc.ok:
        assert sc.reason is not None
        return Decision(Stage.STOP, sc.reason.value, None, at, sc.binding)

    # --- SPEC 17.2 -----------------------------------------------------------
    tgt = first_target(
        cfg,
        direction=plan.direction,
        entry_price=entry_price,
        stop=plan.stop,
        atr_value=atr_value,
        levels=levels,
        ranks=ranks,
    )
    if not tgt.ok:
        assert tgt.reason is not None
        return Decision(Stage.TARGET, tgt.reason.value, None, at, tgt.model.value)

    # --- SPEC 18.5 then 18.2 -------------------------------------------------
    # The ladder is applied BEFORE sizing, never after: SPEC 18.1 permits the risk layer
    # to reduce risk_pct, and reducing it after the lots are computed would be adjusting
    # a position rather than sizing one.
    risk_pct = (
        ledger.effective_risk_pct() if ledger is not None else cfg.risk.pct_per_trade
    )
    if skip_sizing:
        return Decision(
            Stage.ACCEPTED, None,
            TradePlan(plan, tgt, _UNSIZED, sc, risk_pct, equity), at,
        )
    try:
        sizing = size_for_setup(
            cfg,
            symbol=symbol,
            equity=equity,
            risk_pct=risk_pct,
            sl_distance=sc.sl_distance,
            quote_to_account=quote_to_account,
        )
    except MissingConversionRate as exc:
        # SPEC 18.2: the absence of a conversion series blocks the symbol.  Surfaced as a
        # rejection rather than a crash, and never as a default rate.
        return Decision(Stage.SIZING, RiskReject.SIZE_BELOW_MIN.value, None, at, str(exc))
    if not sizing.ok:
        assert sizing.reason is not None
        return Decision(Stage.SIZING, sizing.reason.value, None, at,
                        f"raw_lots={sizing.raw_lots:.4f}")

    # --- SPEC 18.4 -----------------------------------------------------------
    if apply_limits and ledger is not None:
        rej = ledger.check(
            at,
            symbol=symbol,
            direction=plan.direction,
            risk_pct=risk_pct,
            spread=spread,
            sl_distance=sc.sl_distance,
        )
        if rej is not None:
            return Decision(Stage.LIMITS, rej.value, None, at)

    return Decision(
        Stage.ACCEPTED,
        None,
        TradePlan(plan, tgt, sizing, sc, risk_pct, equity),
        at,
    )


def revalidate_at_fill(
    cfg: AppConfig,
    trade: TradePlan,
    *,
    symbol: str,
    fill_price: float,
    atr_value: float,
    spread: float | None = None,
) -> Decision:
    """SPEC 16.5's second check, which the specification says is not optional.

    *"Stop inside the spread at fill time -- rejected at fill (``SPREAD_EXCEEDS_STOP``)
    even if it passed at arm time. Both checks are required."*

    Two things move between arming and filling and only this function sees either.

    **The spread moves**, which is the case the specification names, and which is inert
    until Q2 delivers a spread series.

    **The stop itself moves under S4**, which the specification does not name because it
    treats the stop as fixed once planned. Under S1-S3 the anchor is structural and the
    fill price is irrelevant to it. Under S4 the stop is ``entry_price -/+ atr_multiple x
    ATR``, and for a MARKET order the planned entry price is a *placeholder* for a price
    that SPEC 15.3 forbids using -- the fill is next bar's open. So the planned stop under
    S4+market is anchored to an unobtainable price and must be re-derived here, which also
    means the SPEC 16.3 caps can genuinely produce a different answer at fill than at arm.
    See D-014 section 4.
    """
    from bot.core.stops import StopModel, planned_stop  # local: avoids a cycle at import

    stop = trade.entry.stop
    if StopModel(trade.entry.sl_model) is StopModel.S4_ATR:
        stop = planned_stop(
            cfg,
            direction=trade.entry.direction,
            atr_value=atr_value,
            sweep_extreme=float("nan"),  # unused by S4
            model=StopModel.S4_ATR,
            entry_price=fill_price,
            spec=symbol_spec(cfg, symbol),
        )

    sc = check_stop(
        cfg,
        symbol=symbol,
        direction=trade.entry.direction,
        entry_price=fill_price,
        stop=stop,
        atr_value=atr_value,
        spec=symbol_spec(cfg, symbol),
        spread=spread,
    )
    if not sc.ok:
        assert sc.reason is not None
        return Decision(Stage.FILL, sc.reason.value, None, None, sc.binding)
    return Decision(
        Stage.ACCEPTED,
        None,
        TradePlan(trade.entry, trade.target, trade.sizing, sc, trade.risk_pct, trade.equity),
    )


def stop_moved_at_fill(
    cfg: AppConfig, plan: EntryPlan, *, fill_price: float, atr_value: float, symbol: str
) -> float:
    """How far the S4 stop moves between arming and filling, in price units.

    Zero for S1-S3 by construction, and zero for any limit order (a limit fills at its own
    price or not at all). Non-zero only for S4 with a MARKET order, and then exactly the
    distance between ``C_b`` and the fill -- which is the gap SPEC 15.3 is about. On a
    perfectly continuous fixture that distance is 0.0000 and the whole effect is
    invisible, the same way SPEC 15.3's own lookahead is (D-013 section 3).
    """
    from bot.core.stops import StopModel, planned_stop

    if StopModel(plan.sl_model) is not StopModel.S4_ATR:
        return 0.0
    if plan.order_type is not OrderType.MARKET:
        return 0.0
    refreshed = planned_stop(
        cfg,
        direction=plan.direction,
        atr_value=atr_value,
        sweep_extreme=float("nan"),
        model=StopModel.S4_ATR,
        entry_price=fill_price,
        spec=symbol_spec(cfg, symbol),
    )
    return abs(refreshed - plan.stop)


def direction_of(plan: EntryPlan) -> Direction:  # pragma: no cover - trivial accessor
    return plan.direction
