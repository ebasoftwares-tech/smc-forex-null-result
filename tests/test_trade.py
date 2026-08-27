"""The pre-trade chain: armed order -> sized trade, or a named rejection.

SPEC 16.3's stop caps, SPEC 17.2's RR gate and SPEC 18's sizing and limits all fire in
the same state, and this is where they meet. What the tests here are mostly about is the
*reason* recorded rather than the outcome: a setup that fails three checks fails all
three whatever order they run in, so the order decides what a rejection table means.

The one place the chain is genuinely path-dependent is at fill. SPEC 16.5 requires the
stop checks to run twice — *"Both checks are required"* — and under stop model S4 the
stop itself moves between the two, because S4 anchors on the entry price and a market
order's planned price is a placeholder for one SPEC 15.3 forbids using. See D-014
section 4.
"""

from __future__ import annotations

from datetime import timezone

import numpy as np
import pytest

from bot.config.loader import load_config
from bot.core.bars import build_series
from bot.core.displacement import Direction
from bot.core.entries import EntryModel, OrderType, arm
from bot.core.fvg import detect_fvgs
from bot.core.indicators import atr_ref
from bot.core.order_blocks import ObDefinition, propose
from bot.core.risk import ClosedTrade, RiskLedger, RiskReject
from bot.core.stops import StopModel, StopReject
from bot.core.targets import TargetReject
from bot.core.trade import (
    REASONS,
    Stage,
    evaluate,
    revalidate_at_fill,
    stop_moved_at_fill,
)

UTC = timezone.utc
H4 = 14400
WARM = 20
MID = 1.07860
HALF = 0.00225

#: Deliberately tighter than the ``test_entries`` fixture. That one spreads the models
#: far apart to make each price distinguishable, which puts model E's stop 74 pips from
#: its entry -- past SPEC 16.3's 60-pip cap, so every setup on it is SL_TOO_WIDE and
#: nothing downstream is ever reached. The distances here are close to SPEC 16.4's own
#: worked example instead.
SETUP = [
    (1.07950, 1.08000, 1.07800, 1.07940),  # 20  sweep low, and a down bar (OB origin)
    (1.07940, 1.08150, 1.07930, 1.08130),  # 21
    (1.08130, 1.08350, 1.08120, 1.08330),  # 22  break bar
]
S_BAR, A_BAR, B_BAR = WARM, WARM + 1, WARM + 2
SWEEP_EXTREME = 1.07800
BREAK_PRICE = 1.08330
REFERENCE = 1.08300


def make():
    rows = SETUP
    n = WARM + len(rows)
    t = np.arange(n, dtype=np.int64) * H4
    o = [MID] * WARM + [r[0] for r in rows]
    h = [MID + HALF] * WARM + [r[1] for r in rows]
    lo = [MID - HALF] * WARM + [r[2] for r in rows]
    c = [MID] * WARM + [r[3] for r in rows]
    return build_series(
        "EURUSD", "H4", t, t + H4,
        np.array(o), np.array(h), np.array(lo), np.array(c), np.ones(n),
    )


@pytest.fixture(scope="module")
def fx(cfg):
    s = make()
    atr = atr_ref(s, cfg.atr.period)
    ob = propose(
        s, cfg, direction=Direction.BULLISH, sweep_extreme_bar=S_BAR, leg_start=A_BAR,
        break_bar=B_BAR, reference_price=REFERENCE, displacement_confirmed=True,
        definition=ObDefinition.A_LAST_OPPOSING, atr=atr, seq=1,
    )
    assert ob.ok, ob.reason
    return s, atr, float(atr[B_BAR]), detect_fvgs(s, cfg), ob.ob


def armed(cfg, fx, model=EntryModel.E_LEG_MIDPOINT, **kw):
    s, atr, a, fvgs, ob = fx
    r = arm(
        s, cfg, direction=Direction.BULLISH, mss_bar=B_BAR, leg_start=S_BAR,
        sweep_extreme=kw.pop("sweep_extreme", SWEEP_EXTREME),
        break_price=BREAK_PRICE, model=model, fvgs=fvgs, atr=atr,
        order_block=kw.pop("order_block", ob), **kw,
    )
    assert r.ok, r.reason
    return r.plan


def run(cfg, fx, plan, **kw):
    a = fx[2]
    return evaluate(
        cfg, plan, symbol="EURUSD", atr_value=kw.pop("atr_value", a),
        equity=kw.pop("equity", 100_000.0), **kw,
    )


# ------------------------------------------------------------- the happy path


def test_a_clean_setup_produces_a_sized_trade(cfg, fx):
    """The positive control. A chain that only ever rejects would pass every other test."""
    d = run(cfg, fx, armed(cfg, fx))
    assert d.ok and d.stage is Stage.ACCEPTED and d.reason is None
    t = d.plan
    assert t.lots > 0
    assert t.sl_distance == pytest.approx(abs(t.entry.price - t.entry.stop))
    assert t.target.rr == pytest.approx(cfg.tp.r_multiple)
    assert t.risk_pct == cfg.risk.pct_per_trade
    assert t.sizing.realised_risk <= t.sizing.intended_risk


def test_every_reason_the_chain_can_produce_is_in_the_catalogue():
    """SPEC 19: 'every reason is an enum value ... nothing exits a setup silently'."""
    assert len(REASONS) == 24
    for r in ("SL_TOO_WIDE", "SL_TOO_TIGHT", "RR_BELOW_MIN", "SIZE_BELOW_MIN",
              "SIZE_ABOVE_MAX", "SIZE_UNDER_RISK", "SPREAD_TOO_WIDE", "KILL_SWITCH"):
        assert r in REASONS


# ------------------------------------------------------------- rejection order


def test_the_stop_caps_are_evaluated_before_the_rr_gate(cfg, fx):
    """A setup that fails both is reported on the property of *itself*, not of its target."""
    plan = armed(cfg, fx)
    # A tiny ATR makes the stop distance enormous in ATR terms; the RR gate would also
    # have something to say, since T1's target scales with the same distance.
    d = run(cfg, fx, plan, atr_value=0.00020)
    assert d.stage is Stage.STOP
    assert d.reason == StopReject.SL_TOO_WIDE.value


def test_sizing_is_evaluated_before_the_portfolio_limits(cfg, fx):
    """A setup too small to size is rejected for that, not for the book it arrived into.

    The distinction matters for reading a rejection log: ``RISK_LIMIT_POSITIONS`` would
    have been fine tomorrow, ``SIZE_BELOW_MIN`` never would.
    """
    plan = armed(cfg, fx)
    led = RiskLedger(cfg, equity=300.0)
    for i in range(cfg.risk.max_open_positions):
        led.open(
            __import__("bot.core.risk", fromlist=["OpenPosition"]).OpenPosition(
                f"s{i}", f"SYM{i}USD", Direction.BULLISH, 0.35, plan.valid_from
            )
        )
    d = run(cfg, fx, plan, equity=300.0, ledger=led)
    assert d.stage is Stage.SIZING
    assert d.reason == RiskReject.SIZE_BELOW_MIN.value


def test_the_portfolio_limits_reject_a_setup_that_is_otherwise_fine(cfg, fx):
    plan = armed(cfg, fx)
    led = RiskLedger(cfg, equity=100_000.0)
    led.record_close(ClosedTrade("EURUSD", plan.valid_from, -2_500.0))
    d = run(cfg, fx, plan, ledger=led)
    assert d.stage is Stage.LIMITS
    assert d.reason == RiskReject.RISK_LIMIT_DAILY.value


def test_limits_off_reports_the_same_strategy_unprotected_not_a_different_one(cfg, fx):
    """SPEC 18.9 asks for both. Turning the limits off must not turn the *rules* off.

    The per-setup rejections — stop caps, RR gate, sizing — are part of the strategy;
    only the portfolio overlay is switched.
    """
    plan = armed(cfg, fx)
    led = RiskLedger(cfg, equity=100_000.0)
    led.record_close(ClosedTrade("EURUSD", plan.valid_from, -2_500.0))
    assert run(cfg, fx, plan, ledger=led).stage is Stage.LIMITS
    assert run(cfg, fx, plan, ledger=led, apply_limits=False).ok

    # ... but a stop-cap failure survives apply_limits=False, because it is the strategy.
    d = run(cfg, fx, plan, ledger=led, apply_limits=False, atr_value=0.00020)
    assert d.stage is Stage.STOP


def test_the_drawdown_ladder_reduces_the_size_before_it_is_computed(cfg, fx):
    """SPEC 18.1 permits reducing risk_pct; reducing it after sizing would be adjusting."""
    plan = armed(cfg, fx)
    flat = RiskLedger(cfg, equity=100_000.0)
    down = RiskLedger(cfg, equity=100_000.0)
    down.mark_equity(93_000.0)              # 7% drawdown -> x0.75

    a = run(cfg, fx, plan, ledger=flat)
    b = run(cfg, fx, plan, equity=93_000.0, ledger=down)
    assert a.ok and b.ok
    assert b.plan.risk_pct == pytest.approx(cfg.risk.pct_per_trade * 0.75)
    assert b.plan.lots < a.plan.lots


def test_a_missing_conversion_rate_is_a_rejection_not_a_crash(cfg, fx):
    """SPEC 18.2's blocking rule, surfaced through the chain."""
    plan = armed(cfg, fx)
    a = fx[2]
    d = evaluate(cfg, plan, symbol="USDJPY", atr_value=a, equity=100_000.0)
    assert not d.ok
    assert d.stage in (Stage.STOP, Stage.SIZING)


# ------------------------------------------------- SPEC 16.5, the fill-time check


def test_the_stop_checks_run_again_at_fill(cfg, fx):
    """SPEC 16.5: a stop that cleared the caps at arm time can sit inside the spread later."""
    plan = armed(cfg, fx)
    d = run(cfg, fx, plan)
    assert d.ok
    a = fx[2]
    ok = revalidate_at_fill(cfg, d.plan, symbol="EURUSD", fill_price=plan.price,
                            atr_value=a)
    assert ok.ok
    widened = revalidate_at_fill(
        cfg, d.plan, symbol="EURUSD", fill_price=plan.price, atr_value=a,
        spread=abs(plan.price - plan.stop) * 1.1,
    )
    assert not widened.ok
    assert widened.stage is Stage.FILL
    assert widened.reason == StopReject.SPREAD_EXCEEDS_STOP.value


def test_only_S4_moves_its_stop_between_arming_and_filling(cfg, fx):
    """D-014 section 4.

    S1–S3 anchor on structure, so the fill price cannot move the stop. S4 anchors on the
    entry price, and for a MARKET order the planned price is ``C_b`` — a placeholder for
    a price SPEC 15.3 forbids using, since the fill is next bar's open. A limit fills at
    its own price or not at all, so only S4 + MARKET is affected.
    """
    a = fx[2]
    fill = 1.08700          # a gap away from the planned market price

    for sl_model in StopModel:
        market = armed(cfg, fx, EntryModel.A_MARKET, sl_model=sl_model)
        moved = stop_moved_at_fill(cfg, market, fill_price=fill, atr_value=a,
                                   symbol="EURUSD")
        if sl_model is StopModel.S4_ATR:
            assert moved > 0
            assert moved == pytest.approx(abs(fill - market.price))
        else:
            assert moved == 0.0

    limit = armed(cfg, fx, EntryModel.E_LEG_MIDPOINT, sl_model=StopModel.S4_ATR)
    assert limit.order_type is OrderType.LIMIT
    assert stop_moved_at_fill(cfg, limit, fill_price=fill, atr_value=a,
                              symbol="EURUSD") == 0.0


def test_the_S4_stop_is_re_derived_from_the_fill_not_carried_over(cfg, fx):
    """The consequence: the re-derived stop keeps S4's ATR distance, whatever the fill.

    Run at a 30-pip ATR, because at this fixture's own 45-pip ATR an S4 stop is 67 pips
    and SPEC 16.3's 60-pip cap rejects it before any of this is reached -- which is a
    finding in its own right, pinned by
    ``test_S4_has_a_hard_atr_ceiling_imposed_by_the_pip_cap`` below.
    """
    s, atr_arr, _, fvgs, ob = fx
    small = np.full_like(atr_arr, 0.00300)
    plan = arm(
        s, cfg, direction=Direction.BULLISH, mss_bar=B_BAR, leg_start=S_BAR,
        sweep_extreme=SWEEP_EXTREME, break_price=BREAK_PRICE,
        model=EntryModel.A_MARKET, fvgs=fvgs, order_block=ob, atr=small,
        sl_model=StopModel.S4_ATR,
    ).plan
    d = evaluate(cfg, plan, symbol="EURUSD", atr_value=0.00300, equity=100_000.0)
    assert d.ok, (d.stage, d.reason, d.detail)

    fill = plan.price + 0.00040
    again = revalidate_at_fill(cfg, d.plan, symbol="EURUSD", fill_price=fill,
                               atr_value=0.00300)
    assert again.ok
    # The distance is preserved; the anchor is not.  That is what S4 means.
    assert again.plan.sl_distance == pytest.approx(cfg.sl.atr_multiple * 0.00300)
    assert d.plan.sl_distance == pytest.approx(cfg.sl.atr_multiple * 0.00300)
    assert again.plan.stop_check.sl_distance == pytest.approx(d.plan.sl_distance)
    assert stop_moved_at_fill(
        cfg, plan, fill_price=fill, atr_value=0.00300, symbol="EURUSD"
    ) == pytest.approx(0.00040)


def test_an_S1_stop_is_unchanged_by_the_fill_price(cfg, fx):
    """The control for the previous two: the structural anchor does not move."""
    a = fx[2]
    plan = armed(cfg, fx, EntryModel.A_MARKET, sl_model=StopModel.S1_SWEEP_EXTREME)
    d = run(cfg, fx, plan)
    assert d.ok, (d.stage, d.reason)
    again = revalidate_at_fill(cfg, d.plan, symbol="EURUSD",
                               fill_price=plan.price + 0.00020, atr_value=a)
    assert again.ok
    assert again.plan.entry.stop == plan.stop
    # ... and the risk distance therefore moves with the fill, which is the other half of
    # the same fact: under S1 the fill changes R, under S4 it changes the stop.
    assert again.plan.sl_distance > d.plan.sl_distance


def test_S4_has_a_hard_atr_ceiling_imposed_by_the_pip_cap(cfg, fx):
    """Two FROZEN defaults that interact: ``atr_multiple`` 1.5 and ``max_sl_pips`` 60.

    S4's stop is 1.5 ATR by construction, so it exceeds the 60-pip cap for any ATR above
    **40 pips** and the 90-pip JPY cap above 60 pips. H4 majors spend much of their life
    above 40, which makes S4 unavailable rather than merely wide -- measured on the
    fixture in the Phase 13 report.

    The mirror of it: ``max_sl_atr`` is 2.5 and S4 is 1.5, so under S4 the ATR cap can
    never fire. A third vacuous check, after S4's ``PRICE_THROUGH_STOP`` and
    ``min_realised_fraction``.
    """
    s, atr_arr, a, fvgs, ob = fx
    assert a > 0.0040, "this fixture is meant to sit above the S4 ceiling"

    def at(atr_value):
        arr = np.full_like(atr_arr, atr_value)
        plan = arm(
            s, cfg, direction=Direction.BULLISH, mss_bar=B_BAR, leg_start=S_BAR,
            sweep_extreme=SWEEP_EXTREME, break_price=BREAK_PRICE,
            model=EntryModel.A_MARKET, fvgs=fvgs, order_block=ob, atr=arr,
            sl_model=StopModel.S4_ATR,
        ).plan
        return evaluate(cfg, plan, symbol="EURUSD", atr_value=atr_value,
                        equity=100_000.0)

    ceiling = cfg.risk.max_sl_pips["default"] / cfg.sl.atr_multiple  # 40 pips
    assert ceiling == pytest.approx(40.0)
    assert at(0.0001 * (ceiling - 1)).ok
    over = at(0.0001 * (ceiling + 1))
    assert over.stage is Stage.STOP
    assert over.reason == StopReject.SL_TOO_WIDE.value
    assert over.detail == "max_sl_pips"          # never max_sl_atr, under S4


# ---------------------------------------------------------------- T3 and T4 again


def test_T3_is_rejected_by_the_chain_on_every_setup(cfg, fx):
    """D-014 section 1, reaching the assembly: T3 never produces a trade."""
    c, _ = load_config(overrides={"tp": {"model": "partial_ladder"}})
    d = run(c, fx, armed(c, fx))
    assert d.stage is Stage.TARGET
    assert d.reason == TargetReject.RR_BELOW_MIN.value


def test_T4_takes_a_setup_the_other_models_reject(cfg, fx):
    """D-014 section 6: the T1–T4 ablation runs on four different setup streams."""
    tight, _ = load_config(overrides={"tp": {"model": "fixed_r", "r_multiple": 1.2}})
    trail, _ = load_config(overrides={"tp": {"model": "structure_trail"}})
    plan = armed(cfg, fx)
    assert run(tight, fx, plan).stage is Stage.TARGET
    assert run(trail, fx, plan).ok
