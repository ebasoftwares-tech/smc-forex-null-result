"""The backtest engine: a setup stream in, trades and rejections out.

**The engine is two passes, and the split is the design decision worth understanding.**

*Pass one is geometry and is portfolio-free.* For each setup it runs the SPEC 16.3 stop
caps, the 17.2 RR gate, arming (SPEC 15), fill resolution (15.4) and the exit path (17.3–
17.5). Everything it produces — entry bar, exit bar, **R-multiple** — depends only on the
price series and the configuration. Nothing about equity, open positions or the drawdown
ladder can reach it.

*Pass two is the portfolio.* It walks the candidates in fill order, applies SPEC 18.4's
limits against a live ledger, sizes each admitted trade at the running equity, and books
the PnL.

That split is not an optimisation. **R-expectancy is BACKTEST_PROTOCOL section 4.1's
primary metric precisely because it is a property of the strategy rather than of the
equity path**, and computing it in a pass that cannot see equity is how that claim is made
structurally true rather than merely asserted. Position size scales PnL and cannot move R.

**Entries are processed before exits within one bar.** A market entry fills at the bar's
open while an exit happens somewhere inside the bar, so opening first is the ordering the
prices actually support. It is also the choice that does not invent capacity: processing
exits first would free a position slot using a close whose time within the bar is unknown,
and let a trade in that the limits should have refused.

**Every setup that does not become a trade produces a rejection record with its forward
return** (SPEC 19's closing rule, 21.3). That is what turns "were our filters right?" into
a query over one run rather than another backtest — and it is the single cheapest analysis
in the project, because it costs nothing against the out-of-sample budget.

**An unfilled armed order additionally produces a shadow trade** (SPEC 15.6): the
would-have-been outcome over the planned stop and target. Shadow trades never touch
equity. They exist so "did we miss the good ones?" is answerable per entry model, which
matters because the five models have coverage from 33% to 100% (D-013 section 5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence

import numpy as np

from bot.config.schema import AppConfig
from bot.core.bars import BarSeries, from_epoch_s
from bot.core.costs import (
    TradeCosts,
    commission,
    entry_slippage,
    fill_price_with_costs,
    nights_held,
    spread_at,
    swap,
)
from bot.core.displacement import Direction, leg_origin
from bot.core.entries import (
    CancelReason,
    EntryModel,
    EntryPlan,
    OrderType,
    arm,
    resolve_fill,
)
from bot.core.exits import ExitOutcome, ExitReason, resolve_exit
from bot.core.fvg import Fvg, detect_fvgs
from bot.core.indicators import atr_ref
from bot.core.liquidity import Side
from bot.core.mss import MssResult, SetupCandidate, analyse_mss
from bot.core.order_blocks import ObDefinition, propose
from bot.core.risk import OpenPosition, RiskLedger
from bot.core.sessions import build_sessions
from bot.core.stops import StopModel, symbol_spec
from bot.core.structure import StructureResult, analyse_structure
from bot.core.sweeps import analyse_sweeps
from bot.core.swings import detect_swings
from bot.core.targets import TargetModel
from bot.core.trade import Stage, evaluate
from bot.data.resample import resample

#: Pass one evaluates the stop caps and the RR gate and **does not size**. SPEC 18.2's
#: rejections (SIZE_BELOW_MIN and friends) are functions of equity, so they belong with
#: the portfolio in pass two -- Phase 13's account sweep measured exactly how much of the
#: stream they remove, and it varies by a factor of six between a USD 500 and a USD 2,000
#: account. An earlier version sized pass one at a nominal equity, which meant the
#: geometry population silently depended on a constant chosen for convenience.
_SIZING_STAGES = (Stage.SIZING,)


# --------------------------------------------------------------------- the market


@dataclass(frozen=True)
class Market:
    """One symbol's fully-derived state.  Built once and shared by every variant.

    Sharing it is what makes SPEC 15.8's paired comparison possible: *"all five models run
    as separate pre-registered variants over identical setups, from one shared setup
    stream, so the comparison is paired."* Rebuilding the stream per variant would compare
    two populations and call it a model difference.
    """

    symbol: str
    h4: BarSeries
    m1: BarSeries | None
    atr: np.ndarray
    structure: StructureResult
    fvgs: list[Fvg]
    mss: MssResult
    opposing_sweep_bars: dict[Side, set[int]]
    sessions_by_bar: dict[int, str]
    levels_created: int
    sweeps_confirmed: int
    #: BACKTEST_PROTOCOL section 6.4's controls, and nothing else, set this.  A control
    #: is a *different setup stream over the same prices*, so it replaces the stream
    #: rather than the market -- which is what keeps every control comparable to the
    #: baseline and to each other.  ``None`` is the strategy, and the default.
    setup_override: tuple[SetupCandidate, ...] | None = None

    @property
    def setups(self) -> list[SetupCandidate]:
        """SPEC 14.2 step 5: displaced CHoCH events, which is what an MSS is.

        A section 6.4 control substitutes its own stream here.  It is deliberately an
        override rather than a flag the filter reads: ``sweep_only`` triggers on a bar
        where no CHoCH exists at all, so there is no predicate over ``mss.candidates``
        that could express it, and a control that had to fabricate MSS records to be
        run would be indistinguishable in the output from the thing it is a control for.
        """
        if self.setup_override is not None:
            return list(self.setup_override)
        return [c for c in self.mss.candidates if c.is_choch and c.displacement.confirmed]


def build_market(
    cfg: AppConfig,
    m1: BarSeries,
    *,
    keep_m1: bool = True,
    level_transform=None,
) -> Market:
    """Run the whole pipeline once, in STATE_MACHINE section 4's order.

    Steps 1–8 of that ordering are the engines below; step 9 onward is ``run``. The two
    orderings the state machine calls load-bearing are preserved by construction here:
    structure before liquidity (a swing must be confirmed before it can become a level),
    and existing setups before new ones (``run`` walks candidates in MSS-bar order).
    """
    h4 = resample(m1, "H4", cfg)
    d1 = resample(m1, "D1", cfg)
    m15 = resample(m1, "M15", cfg)
    structure = analyse_structure(h4, cfg)
    sessions = build_sessions(m15, cfg)
    book, sweeps = analyse_sweeps(
        cfg=cfg, h4=h4, d1=d1, w1=resample(m1, "W1", cfg), mn1=resample(m1, "MN1", cfg),
        sessions=sessions, h4_structure=structure, d1_swings=detect_swings(d1, cfg),
        level_transform=level_transform,
    )
    fvgs = detect_fvgs(h4, cfg)
    confirmed = sweeps.confirmed()
    mss = analyse_mss(h4, cfg, confirmed, swings=structure.swings, fvgs=fvgs)

    opposing: dict[Side, set[int]] = {}
    for e in confirmed:
        opposing.setdefault(e.side, set()).add(e.confirm_bar)

    # Session label per H4 bar, for the cost model and the section 4.2.1 matrix.
    by_bar: dict[int, str] = {}
    for s in sessions:
        lo, hi = s.start_utc.timestamp(), s.end_utc.timestamp()
        idx = np.where((h4.close_time > lo) & (h4.close_time <= hi))[0]
        for i in idx:
            by_bar[int(i)] = s.session_name

    return Market(
        symbol=h4.symbol,
        h4=h4,
        m1=m1 if keep_m1 else None,
        atr=atr_ref(h4, cfg.atr.period),
        structure=structure,
        fvgs=fvgs,
        mss=mss,
        opposing_sweep_bars=opposing,
        sessions_by_bar=by_bar,
        levels_created=len(book.levels),
        sweeps_confirmed=len(confirmed),
    )


# --------------------------------------------------------------------- the records


@dataclass(frozen=True)
class Trade:
    """SPEC 21.2's trade record, in the columns that exist at this phase.

    The context group (`monthly_bias`, `alignment_label`, `gate_mode`) waits on the bias
    engine (Phases 2–4) and is deliberately absent rather than present and null: a column
    of nulls in a breakdown table reads as a measurement of nothing, which is worse than
    a column that is not there.
    """

    trade_id: str
    setup_id: str
    symbol: str
    direction: Direction
    entry_model: EntryModel
    sl_model: str
    tp_model: str
    order_type: OrderType

    sweep_at: datetime
    mss_at: datetime
    entry_at: datetime
    exit_at: datetime
    entry_bar: int
    exit_bar: int
    duration_bars: int

    sweep_session: str
    mss_session: str
    entry_session: str

    liq_source: str
    liq_side: str
    liq_tier: int
    penetration_atr: float
    bars_sweep_to_mss: int
    displacement_net_atr: float

    planned_price: float
    fill_price: float
    sl_price: float
    sl_distance_pips: float
    sl_distance_atr: float
    tp_price: float | None
    planned_rr: float | None

    equity_at_entry: float
    risk_pct: float
    lots: float
    risk_amount: float

    exit_reason: ExitReason
    exit_price: float
    r_multiple: float
    r_net: float
    pnl_gross: float
    pnl_net: float
    costs: TradeCosts
    mae_r: float
    mfe_r: float
    bars_to_mae: int
    bars_to_mfe: int

    atr_at_entry: float
    intrabar_ambiguous: bool
    gapped: bool
    data_suspect: bool
    censored: bool

    @property
    def won(self) -> bool:
        return self.r_net > 0


@dataclass(frozen=True)
class Rejection:
    """SPEC 21.3's counterfactual record.

    ``forward_return_r`` is the planned trade simulated over the next
    ``analysis.forward_bars`` bars — what the setup *would* have paid had the gate not
    fired. A gate whose rejected population has positive expectancy is destroying edge.
    """

    setup_id: str
    symbol: str
    direction: Direction
    at: datetime
    bar: int
    stage: Stage
    reason: str
    detail: str
    entry_model: EntryModel
    planned_price: float | None
    planned_stop: float | None
    #: Forward move in the setup's direction, in ATR, from the MSS close.  See
    #: ``_forward_return_atr`` for why it is not in R from the planned entry.
    forward_return_atr: float | None


@dataclass(frozen=True)
class ShadowTrade:
    """SPEC 15.6.  A trade that would have happened, priced on the same path.

    Never touches equity. Its only job is to answer "did we miss the good ones?" per entry
    model, which cannot be read off the filled population by construction.
    """

    setup_id: str
    symbol: str
    direction: Direction
    entry_model: EntryModel
    reason: CancelReason | None
    r_multiple: float
    exit_reason: ExitReason


@dataclass
class BacktestResult:
    config_hash: str
    entry_model: EntryModel
    sl_model: str
    tp_model: str
    cost_multiplier: float
    trades: list[Trade] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    shadows: list[ShadowTrade] = field(default_factory=list)
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)
    funnel: dict[str, int] = field(default_factory=dict)

    @property
    def n_trades(self) -> int:
        return len(self.trades)

    @property
    def qualified_setups(self) -> int:
        """Setups that armed an order — the denominator for SPEC 15.5's fill rate."""
        return self.funnel.get("orders_armed", 0)

    @property
    def orders_filled(self) -> int:
        return self.funnel.get("orders_filled", 0)

    @property
    def fill_rate(self) -> float:
        """SPEC 15.5's coverage: **orders filled over orders armed**.

        Measured on pass one, before the portfolio limits, and that distinction is not
        pedantic. Counting admitted *trades* instead would fold SPEC 18.4's position cap
        into the model comparison — and the cap bites hardest on whichever model fills
        most, so the model that covers the most setups would be penalised for covering
        them. Model A read 58% that way against the 100% Phase 12 measured.
        """
        q = self.qualified_setups
        return self.orders_filled / q if q else float("nan")


# ------------------------------------------------------------------ pass one


@dataclass(frozen=True)
class _Candidate:
    """Pass one's output: everything about a trade that equity cannot influence."""

    setup: SetupCandidate
    plan: EntryPlan
    target: float | None
    rr: float | None
    fill_bar: int
    fill_at: datetime
    fill_price: float
    exit: ExitOutcome
    atr_at_entry: float


def _forward_return_atr(
    market: Market,
    cfg: AppConfig,
    *,
    direction: Direction,
    bar: int,
) -> float | None:
    """SPEC 19 / 21.3's counterfactual, **measured in ATR from the MSS close**.

    SPEC 21.3 says "simulating the planned trade", i.e. from the planned entry price in R.
    Both of those turn out to distort the answer, and the first version did both:

    * **From the planned entry price.** A bullish limit sits *below* the market, so
      measuring forward from it starts at a price the trade never paid. On the Phase 14
      fixture that produced a median forward return of **+1.7R with a 92% win rate on a
      random walk** — the same free-discount artefact the shadow trades had, in a
      different place.
    * **In R against the planned risk.** Several rejection reasons are *precisely that
      the risk was wrong*: ``SL_TOO_TIGHT`` rejects a 0.37-pip stop, and dividing by it
      reported **+7.0R**. The denominator, not the market, is what that number measures.

    So the reference is the **MSS bar's close** — the price at the moment the gate fired,
    which every gate shares — and the normaliser is **ATR**, which is this project's
    normaliser everywhere else (SPEC 1.6) and is defined for every setup regardless of
    what the gate objected to. The resulting number answers the question SPEC 21.3 is
    actually asking: *of the setups this gate rejected, did the market go the setup's way?*
    See D-015 section 7.
    """
    j = bar + cfg.analysis.forward_bars
    a = float(market.atr[bar]) if bar < len(market.atr) else float("nan")
    if j >= market.h4.n or not np.isfinite(a) or a <= 0:
        return None
    ref = float(market.h4.close[bar])
    c = float(market.h4.close[j])
    d = c - ref if direction is Direction.BULLISH else ref - c
    return d / a


def _session_of(market: Market, bar: int) -> str:
    return market.sessions_by_bar.get(bar, "OTHER")


def _pass_one(
    cfg: AppConfig,
    market: Market,
    *,
    entry_model: EntryModel,
    sl_model: StopModel,
    tp_model: TargetModel,
    entry_bar_offset: int = 0,
) -> tuple[list[_Candidate], list[Rejection], list[ShadowTrade], dict[str, int]]:
    spec = symbol_spec(cfg, market.symbol)
    candidates: list[_Candidate] = []
    rejections: list[Rejection] = []
    shadows: list[ShadowTrade] = []
    counts = dict.fromkeys(
        ("setups", "orders_armed", "orders_filled", "gate_rejected", "arm_rejected"), 0
    )

    for i, c in enumerate(market.setups):
        counts["setups"] += 1
        b = c.choch_bar
        a_bar = leg_origin(c.sweep_extreme_bar, b, cfg)
        atr_v = float(market.atr[b])
        if not np.isfinite(atr_v) or atr_v <= 0:
            continue

        ob = propose(
            market.h4, cfg, direction=c.direction, sweep_extreme_bar=c.sweep_extreme_bar,
            leg_start=a_bar, break_bar=b, reference_price=c.reference_price,
            displacement_confirmed=True, definition=ObDefinition.A_LAST_OPPOSING,
            swings=market.structure.swings.swings, atr=market.atr, seq=i,
        )
        armed = arm(
            market.h4, cfg, direction=c.direction, mss_bar=b, leg_start=a_bar,
            sweep_extreme=c.sweep.sweep_extreme,
            break_price=float(market.h4.close[b]), model=entry_model,
            fvgs=market.fvgs, order_block=ob.ob, atr=market.atr,
            sl_model=sl_model, setup_start_bar=c.sweep_extreme_bar,
        )
        if not armed.ok:
            counts["arm_rejected"] += 1
            rejections.append(Rejection(
                c.sweep.id, market.symbol, c.direction, from_epoch_s(market.h4.close_time[b]),
                b, Stage.ARM, armed.reason.value, "", entry_model, None, None, None,
            ))
            continue

        decision = evaluate(
            cfg, armed.plan, symbol=market.symbol, atr_value=atr_v,
            equity=cfg.account.starting_equity, apply_limits=False, skip_sizing=True,
        )
        if not decision.ok:
            counts["gate_rejected"] += 1
            rejections.append(Rejection(
                c.sweep.id, market.symbol, c.direction, decision.at, b, decision.stage,
                decision.reason or "", decision.detail, entry_model,
                armed.plan.price, armed.plan.stop,
                _forward_return_atr(market, cfg, direction=c.direction, bar=b),
            ))
            continue

        counts["orders_armed"] += 1
        fill = resolve_fill(
            market.h4, cfg, armed.plan,
            opposing_sweep_bars=sorted(
                market.opposing_sweep_bars.get(
                    Side.BUY_SIDE if c.direction is Direction.BEARISH else Side.SELL_SIDE,
                    set(),
                )
            ),
            m1=market.m1,
        )
        target = decision.plan.target.price
        if not fill.filled or fill.bar is None:
            # SPEC 15.6, and the trap in it. A shadow trade is counterfactual on the
            # **cancel**, never on the fill: the question is "would this trade have been
            # good had the gate not stopped it", not "would it have been good had price
            # gone somewhere it never went".
            #
            # The first version simulated an entry at the limit price from the MSS bar
            # regardless of whether price ever reached it -- and a bullish limit sits
            # BELOW the market, so every shadow got a free discount. 38 of 54 shadows
            # took profit against 2 stops, for a mean of +1.57R against a filled
            # population near zero. See D-015 section 3.
            #
            # So: re-resolve the same order with the cancels removed. If it would have
            # filled, simulate from THAT fill. If it would still never have filled, there
            # is no shadow, because there was no trade to miss.
            would = resolve_fill(market.h4, cfg, armed.plan, m1=market.m1)
            if would.filled and would.bar is not None:
                shadow = resolve_exit(
                    market.h4, cfg, direction=c.direction, entry_bar=would.bar,
                    entry_price=float(would.price), planned_stop=armed.plan.stop,
                    target=target, atr=market.atr, spec=spec, m1=market.m1,
                    apply_slippage=False,
                    entry_at_bar_open=armed.plan.order_type is OrderType.MARKET,
                )
                shadows.append(ShadowTrade(
                    c.sweep.id, market.symbol, c.direction, entry_model,
                    fill.cancel_reason, shadow.r_multiple, shadow.reason,
                ))
            rejections.append(Rejection(
                c.sweep.id, market.symbol, c.direction,
                fill.at or decision.at, fill.bar if fill.bar is not None else b,
                Stage.FILL, (fill.cancel_reason.value if fill.cancel_reason
                             else fill.state.value),
                "", entry_model, armed.plan.price, armed.plan.stop,
                _forward_return_atr(market, cfg, direction=c.direction, bar=b),
            ))
            continue

        counts["orders_filled"] += 1
        fill_bar, fill_price, fill_at = fill.bar, float(fill.price), fill.at
        if entry_bar_offset:
            # BACKTEST_PROTOCOL section 9's entry-timing test: enter a bar late or early
            # and see whether the result survives. A strategy that only works on exactly
            # the right bar is fitting the bar grid rather than the market. The shifted
            # entry takes that bar's OPEN, because that is the only price a trade
            # arriving at a different bar could actually have got.
            shifted = fill_bar + entry_bar_offset
            if shifted <= c.choch_bar or shifted >= market.h4.n:
                continue
            fill_bar = shifted
            fill_price = float(market.h4.open[shifted])
            fill_at = from_epoch_s(market.h4.close_time[shifted])
        out = resolve_exit(
            market.h4, cfg, direction=c.direction, entry_bar=fill_bar,
            entry_price=fill_price, planned_stop=armed.plan.stop,
            target=target, atr=market.atr, spec=spec, m1=market.m1,
            # A market order fills at the bar's open, so the whole bar is after the fill;
            # a limit fills inside it and only part of it is.  See resolve_exit.
            entry_at_bar_open=(
                armed.plan.order_type is OrderType.MARKET or bool(entry_bar_offset)
            ),
        )
        candidates.append(_Candidate(
            c, armed.plan, target, decision.plan.target.rr, fill_bar, fill_at,
            fill_price, out, atr_v,
        ))

    return candidates, rejections, shadows, counts


# ------------------------------------------------------------------ pass two


def run(
    cfg: AppConfig,
    market: Market,
    *,
    config_hash: str = "",
    entry_model: EntryModel | str | None = None,
    sl_model: StopModel | str | None = None,
    tp_model: TargetModel | str | None = None,
    equity: float | None = None,
    apply_limits: bool = True,
    entry_bar_offset: int = 0,
) -> BacktestResult:
    """One variant over one market.  Deterministic: no clock, no RNG, no I/O."""
    em = EntryModel(entry_model or cfg.entry.model)
    sm = StopModel(sl_model or cfg.sl.model)
    tm = TargetModel(tp_model or cfg.tp.model)
    spec = symbol_spec(cfg, market.symbol)
    start_equity = cfg.account.starting_equity if equity is None else equity

    cands, rejections, shadows, counts = _pass_one(
        cfg, market, entry_model=em, sl_model=sm, tp_model=tm,
        entry_bar_offset=entry_bar_offset,
    )

    result = BacktestResult(config_hash, em, sm.value, tm.value, cfg.cost.multiplier)
    result.rejections = rejections
    result.shadows = shadows

    ledger = RiskLedger(cfg, equity=start_equity)
    eq = start_equity
    result.equity_curve.append(
        (from_epoch_s(market.h4.close_time[0]), eq)
    )

    # Events on one timeline.  Entries first at a shared bar -- see the module docstring.
    opens = sorted(cands, key=lambda c: (c.fill_bar, c.setup.choch_bar))
    live: dict[str, tuple[_Candidate, Trade]] = {}
    pending_close: dict[int, list[str]] = {}
    by_open: dict[int, list[_Candidate]] = {}
    for c in opens:
        by_open.setdefault(c.fill_bar, []).append(c)

    for bar in range(market.h4.n):
        # --- open ---------------------------------------------------------
        for c in by_open.get(bar, ()):
            at = c.fill_at or from_epoch_s(market.h4.close_time[bar])
            ledger.mark_equity(eq)
            decision = evaluate(
                cfg, c.plan, symbol=market.symbol, atr_value=c.atr_at_entry,
                equity=eq, ledger=ledger if apply_limits else None,
                at=at, apply_limits=apply_limits,
            )
            if not decision.ok:
                rejections.append(Rejection(
                    c.setup.sweep.id, market.symbol, c.setup.direction, at, bar,
                    decision.stage, decision.reason or "", decision.detail, em,
                    c.plan.price, c.plan.stop,
                    _forward_return_atr(market, cfg, direction=c.setup.direction,
                                        bar=c.setup.choch_bar),
                ))
                continue

            trade = _make_trade(
                cfg, market, c, decision, spec=spec, entry_model=em, sl_model=sm.value,
                tp_model=tm.value, equity_at_entry=eq,
            )
            live[trade.trade_id] = (c, trade)
            ledger.open(OpenPosition(
                trade.trade_id, market.symbol, c.setup.direction,
                decision.plan.risk_pct, at,
            ))
            pending_close.setdefault(c.exit.bar, []).append(trade.trade_id)

        # --- close --------------------------------------------------------
        for tid in pending_close.pop(bar, ()):
            c, trade = live.pop(tid)
            eq += trade.pnl_net
            ledger.close(tid)
            ledger.mark_equity(eq)
            ledger.record_close(_closed(trade))
            result.trades.append(trade)
            result.equity_curve.append((trade.exit_at, eq))

    counts["trades_closed"] = len(result.trades)
    counts["levels_created"] = market.levels_created
    counts["sweeps_confirmed"] = market.sweeps_confirmed
    result.funnel = counts
    return result


def _closed(trade: Trade):
    from bot.core.risk import ClosedTrade

    return ClosedTrade(trade.symbol, trade.exit_at, trade.pnl_net)


def _make_trade(
    cfg: AppConfig,
    market: Market,
    c: _Candidate,
    decision,
    *,
    spec,
    entry_model: EntryModel,
    sl_model: str,
    tp_model: str,
    equity_at_entry: float,
) -> Trade:
    """Assemble the SPEC 21.2 record, applying SPEC 26's costs once and in one place."""
    direction = c.setup.direction
    lots = decision.plan.lots
    entry_session = _session_of(market, c.fill_bar)
    sp = spread_at(cfg, symbol=market.symbol, spec=spec, session=entry_session)

    # A limit fills at its price; only a market order is slipped on entry (SPEC 26).
    slip_in = (
        entry_slippage(cfg, spec=spec, atr_value=c.atr_at_entry)
        if c.plan.order_type is OrderType.MARKET
        else 0.0
    )
    fill = fill_price_with_costs(
        c.fill_price, direction=direction, spread=sp, slippage=slip_in, is_entry=True
    )

    value = spec.contract_size  # EURUSD on a USD account; SPEC 18.2's identity case
    risk = abs(fill - c.plan.stop)
    gross = (
        (c.exit.price - fill) if direction is Direction.BULLISH else (fill - c.exit.price)
    ) * lots * value
    comm = commission(cfg, lots=lots)
    sw = swap(
        cfg, symbol=market.symbol, lots=lots,
        nights=nights_held(c.fill_at, c.exit.at), direction=direction,
    )
    slip_cost = abs(fill - c.fill_price) * lots * value
    costs = TradeCosts(spread=0.0, commission=comm, swap=sw, slippage=slip_cost)
    net = gross - comm - sw

    risk_amount = decision.plan.sizing.realised_risk
    r_net = net / risk_amount if risk_amount > 0 else 0.0

    return Trade(
        trade_id=f"{c.setup.sweep.id}:{entry_model.value}:{sl_model}:{tp_model}",
        setup_id=c.setup.sweep.id,
        symbol=market.symbol,
        direction=direction,
        entry_model=entry_model,
        sl_model=sl_model,
        tp_model=tp_model,
        order_type=c.plan.order_type,
        sweep_at=c.setup.sweep.at,
        mss_at=from_epoch_s(market.h4.close_time[c.setup.choch_bar]),
        entry_at=c.fill_at,
        exit_at=c.exit.at,
        entry_bar=c.fill_bar,
        exit_bar=c.exit.bar,
        duration_bars=c.exit.bars_held,
        sweep_session=_session_of(market, c.setup.sweep.confirm_bar),
        mss_session=_session_of(market, c.setup.choch_bar),
        entry_session=entry_session,
        liq_source=c.setup.sweep.level_source.value,
        liq_side=c.setup.sweep.side.value,
        liq_tier=c.setup.sweep.level_tier,
        penetration_atr=c.setup.sweep.penetration_atr,
        bars_sweep_to_mss=c.setup.choch_bar - c.setup.sweep.confirm_bar,
        displacement_net_atr=c.setup.displacement.net_atr,
        planned_price=c.plan.price,
        fill_price=fill,
        sl_price=c.plan.stop,
        sl_distance_pips=risk / spec.pip_size,
        sl_distance_atr=risk / c.atr_at_entry if c.atr_at_entry > 0 else float("nan"),
        tp_price=c.target,
        planned_rr=c.rr,
        equity_at_entry=equity_at_entry,
        risk_pct=decision.plan.risk_pct,
        lots=lots,
        risk_amount=risk_amount,
        exit_reason=c.exit.reason,
        exit_price=c.exit.price,
        r_multiple=c.exit.r_multiple,
        r_net=r_net,
        pnl_gross=gross,
        pnl_net=net,
        costs=costs,
        mae_r=c.exit.mae_r,
        mfe_r=c.exit.mfe_r,
        bars_to_mae=c.exit.bars_to_mae,
        bars_to_mfe=c.exit.bars_to_mfe,
        atr_at_entry=c.atr_at_entry,
        intrabar_ambiguous=c.exit.intrabar_ambiguous,
        gapped=c.exit.gapped,
        data_suspect=bool(market.h4.flag("data_suspect")[c.fill_bar]),
        censored=c.exit.censored,
    )


def run_variants(
    cfg: AppConfig,
    market: Market,
    *,
    config_hash: str = "",
    entry_models: Sequence[EntryModel] = tuple(EntryModel),
    **kw,
) -> dict[EntryModel, BacktestResult]:
    """SPEC 15.8's paired bake-off: every model over the **same** ``Market``.

    The pairing is the whole point — it makes "model C beats model A" a statement about
    the same setups rather than about two populations, which is worth a large multiple in
    power over independent runs.
    """
    return {m: run(cfg, market, config_hash=config_hash, entry_model=m, **kw)
            for m in entry_models}
