"""Entry models and fill resolution (SPEC 15) — Phase 12's gate.

*"All five models arm correctly on a fixture; fill logic verified against M1."*

The arming half is pinned by hand-built bars where every model's price is reachable by
arithmetic. The fill half is mostly about the ways a backtest flatters itself: filling
model A at the close that triggered it, treating a touched limit as filled, and guessing
the within-bar order of entry and stop.

Two branches the synthetic fixture cannot produce — a price gap past the stop, and the
"fill on the way back up" that SPEC 15.1 clause 1 exists to prevent — are constructed
here or nowhere.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from bot.config.loader import load_config
from bot.core.bars import build_series
from bot.core.displacement import Direction, leg_origin
from bot.core.entries import (
    ArmReject,
    CancelReason,
    EntryModel,
    FillState,
    OrderType,
    arm,
    planned_stop,
    resolve_fill,
)
from bot.core.fvg import detect_fvgs
from bot.core.indicators import atr_ref
from bot.core.mss import analyse_mss
from bot.core.order_blocks import ObDefinition, propose
from bot.core.sessions import build_sessions
from bot.core.structure import analyse_structure
from bot.core.sweeps import analyse_sweeps
from bot.core.swings import detect_swings
from bot.data.resample import resample
from bot.data.synthetic import generate

UTC = timezone.utc
H4 = 14400
WARM = 20
MID = 1.07860
HALF = 0.00225


def make(tail):
    n = WARM + len(tail)
    t = np.arange(n, dtype=np.int64) * H4
    o = [MID] * WARM + [x[0] for x in tail]
    h = [MID + HALF] * WARM + [x[1] for x in tail]
    lo = [MID - HALF] * WARM + [x[2] for x in tail]
    c = [MID] * WARM + [x[3] for x in tail]
    return build_series(
        "EURUSD", "H4", t, t + H4,
        np.array(o), np.array(h), np.array(lo), np.array(c), np.ones(n),
    )


#: A bullish setup laid out so every model has a distinguishable price.
SETUP = [
    (1.07800, 1.07850, 1.07300, 1.07780),  # 20  s: sweep low, and a down bar (OB origin)
    (1.07780, 1.08100, 1.07770, 1.08080),  # 21  a
    (1.08080, 1.08700, 1.08060, 1.08660),  # 22  b: break bar
]
S_BAR, A_BAR, B_BAR = WARM, WARM + 1, WARM + 2
SWEEP_EXTREME = 1.07300
BREAK_PRICE = 1.08660
LEG_LOW, LEG_HIGH = 1.07300, 1.08700
REFERENCE = 1.08600


def build(cfg, tail=()):
    s = make(SETUP + list(tail))
    return s, atr_ref(s, cfg.atr.period), detect_fvgs(s, cfg)


def order_block(cfg, s, atr):
    """OB-A on this fixture picks bar 20 — the last down bar before the leg."""
    p = propose(
        s, cfg, direction=Direction.BULLISH, sweep_extreme_bar=S_BAR, leg_start=A_BAR,
        break_bar=B_BAR, reference_price=REFERENCE, displacement_confirmed=True,
        definition=ObDefinition.A_LAST_OPPOSING, atr=atr, seq=1,
    )
    assert p.ok and p.ob.origin_index == S_BAR
    return p.ob


def ask(cfg, model, tail=(), **kw):
    s, atr, fvgs = build(cfg, tail)
    return s, atr, arm(
        s, cfg,
        direction=kw.pop("direction", Direction.BULLISH),
        mss_bar=B_BAR, leg_start=kw.pop("leg_start", S_BAR),
        sweep_extreme=kw.pop("sweep_extreme", SWEEP_EXTREME),
        break_price=kw.pop("break_price", BREAK_PRICE),
        model=model, fvgs=fvgs,
        order_block=kw.pop("order_block", order_block(cfg, s, atr)),
        atr=atr, **kw,
    )


def hold(price):
    return (price, price + 0.0002, price - 0.0002, price)


# ------------------------------------------- the gate: all five models arm correctly


def test_model_A_arms_a_market_order(cfg):
    _, _, r = ask(cfg, EntryModel.A_MARKET)
    assert r.ok
    assert r.plan.order_type is OrderType.MARKET


def test_model_B_measures_from_the_leg_low_to_the_BREAK_price(cfg):
    _, _, r = ask(cfg, EntryModel.B_RETRACEMENT)
    assert r.ok
    assert r.plan.order_type is OrderType.LIMIT
    assert r.plan.price == pytest.approx(LEG_LOW + 0.5 * (BREAK_PRICE - LEG_LOW))


def test_model_E_measures_from_the_leg_low_to_the_leg_HIGH(cfg):
    _, _, r = ask(cfg, EntryModel.E_LEG_MIDPOINT)
    assert r.ok
    assert r.plan.price == pytest.approx((LEG_LOW + LEG_HIGH) / 2)


def test_B_and_E_are_genuinely_different_models(cfg):
    """SPEC 15.2 keeps them apart deliberately: they coincide when the break bar makes
    the leg high, which is common but not universal, and collapsing them would hide which
    measurement matters."""
    _, _, b = ask(cfg, EntryModel.B_RETRACEMENT)
    _, _, e = ask(cfg, EntryModel.E_LEG_MIDPOINT)
    assert b.plan.price != pytest.approx(e.plan.price)


def test_model_C_takes_the_selected_FVG_at_the_configured_point(cfg):
    s, atr, fvgs = build(cfg)
    gap = [f for f in fvgs if f.confirmed_index == B_BAR][0]
    assert gap.zone_low == pytest.approx(1.07850) and gap.zone_high == pytest.approx(1.08060)
    for point, want in (
        ("ce", gap.ce),
        ("proximal", gap.zone_high),  # reached first, coming down (D-011 §1)
        ("distal", gap.zone_low),
    ):
        c, _ = load_config(overrides={"entry": {"fvg_entry_point": point}})
        _, _, r = ask(c, EntryModel.C_FVG)
        assert r.ok and r.plan.price == pytest.approx(want), point
        assert r.plan.reference_id == gap.id


def test_model_D_takes_the_order_block_at_the_configured_point(cfg):
    s, atr, _ = build(cfg)
    ob = order_block(cfg, s, atr)
    assert ob.zone_low == pytest.approx(1.07300) and ob.zone_high == pytest.approx(1.07850)
    for point, want in (
        ("proximal", ob.zone_high),
        ("ce", ob.ce),
        ("distal", ob.zone_low),
    ):
        c, _ = load_config(overrides={"entry": {"ob_entry_point": point}})
        _, _, r = ask(c, EntryModel.D_ORDER_BLOCK, order_block=ob)
        assert r.ok and r.plan.price == pytest.approx(want), point


def test_all_five_models_arm_on_the_same_setup(cfg):
    """The gate, stated directly."""
    prices = {}
    for m in EntryModel:
        _, _, r = ask(cfg, m)
        assert r.ok, m
        prices[m] = r.plan.price
    assert len({round(p, 6) for p in prices.values()}) >= 4


def test_no_order_may_exist_before_the_MSS_is_confirmed(cfg):
    """SPEC 15.1 / 14.2 step 3, which the state machine treats as an invariant."""
    s, _, r = ask(cfg, EntryModel.C_FVG)
    assert r.plan.valid_from_bar == B_BAR
    assert r.plan.valid_from.timestamp() == s.close_time[B_BAR]
    assert r.plan.expires_at_bar == B_BAR + cfg.entry.pending_expiry_bars


def test_the_bearish_mirror_arms(cfg):
    tail = [
        (1.07900, 1.08400, 1.07850, 1.07920),  # s: sweep high, up bar
        (1.07920, 1.07930, 1.07600, 1.07620),  # a
        (1.07620, 1.07640, 1.07000, 1.07040),  # b
    ]
    s = make(tail)
    atr = atr_ref(s, cfg.atr.period)
    r = arm(
        s, cfg, direction=Direction.BEARISH, mss_bar=B_BAR, leg_start=S_BAR,
        sweep_extreme=1.08400, break_price=1.07040,
        model=EntryModel.E_LEG_MIDPOINT, atr=atr,
    )
    assert r.ok
    assert r.plan.stop > r.plan.price  # a sell's stop sits above its entry
    assert r.plan.price == pytest.approx((1.07000 + 1.08400) / 2)


def test_a_limit_already_beyond_its_own_stop_is_rejected_at_arm_time(cfg):
    """Not an order -- a loss waiting to be booked. Rejected up front so the log
    distinguishes "never armable" from "armed and then invalidated"."""
    _, _, r = ask(cfg, EntryModel.E_LEG_MIDPOINT, sweep_extreme=1.08500)
    assert not r.ok
    assert r.reason is ArmReject.PRICE_THROUGH_STOP


def test_a_model_that_cannot_arm_says_which(cfg):
    """SPEC 15.7: the default is to invalidate, not to fall back -- a fallback chain
    silently mixes populations and makes per-model statistics uninterpretable."""
    assert cfg.entry.fallback_model == "none"
    s, atr, _ = build(cfg)
    r = arm(
        s, cfg, direction=Direction.BULLISH, mss_bar=B_BAR, leg_start=S_BAR,
        sweep_extreme=SWEEP_EXTREME, break_price=BREAK_PRICE,
        model=EntryModel.C_FVG, fvgs=[], atr=atr,
    )
    assert not r.ok and r.reason is ArmReject.NO_FVG_AVAILABLE
    r2 = arm(
        s, cfg, direction=Direction.BULLISH, mss_bar=B_BAR, leg_start=S_BAR,
        sweep_extreme=SWEEP_EXTREME, break_price=BREAK_PRICE,
        model=EntryModel.D_ORDER_BLOCK, order_block=None, atr=atr,
    )
    assert not r2.ok and r2.reason is ArmReject.NO_OB_AVAILABLE


def test_the_stop_is_the_sweep_extreme_less_a_buffer(cfg):
    s, atr, _ = build(cfg)
    a = float(atr[B_BAR])
    want = planned_stop(
        s, cfg, direction=Direction.BULLISH, sweep_extreme=SWEEP_EXTREME, atr_value=a
    )
    assert want == pytest.approx(SWEEP_EXTREME - cfg.sl.buffer_atr * a)
    _, _, r = ask(cfg, EntryModel.E_LEG_MIDPOINT)
    assert r.plan.stop == pytest.approx(want)


# ------------------------------------------------ SPEC 15.3: model A's fill price


def test_model_A_never_fills_at_the_close_that_triggered_it(cfg):
    """The single easiest way to invent returns: SPEC 15.3 puts it at 10-30% of headline
    return on H4. The close of `b` is not an obtainable price."""
    s, _, r = ask(cfg, EntryModel.A_MARKET, tail=[(1.08700, 1.08900, 1.08650, 1.08800)])
    f = resolve_fill(s, cfg, r.plan)
    assert f.filled
    assert f.price == pytest.approx(float(s.open[B_BAR + 1]))
    assert f.price != pytest.approx(float(s.close[B_BAR]))
    assert f.bar == B_BAR + 1


def test_model_A_uses_the_first_M1_price_after_latency_when_M1_exists(cfg):
    s, _, r = ask(cfg, EntryModel.A_MARKET, tail=[(1.08700, 1.08900, 1.08650, 1.08800)])
    n = s.n * 240
    t = np.arange(n, dtype=np.int64) * 60
    px = np.full(n, 1.09999)
    m1 = build_series(
        "EURUSD", "M1", t, t + 60, px, px + 0.0001, px - 0.0001, px, np.ones(n)
    )
    f = resolve_fill(s, cfg, r.plan, m1=m1)
    assert f.filled
    assert f.price == pytest.approx(1.09999)  # from the M1 path, not the H4 open


def test_a_market_order_at_the_end_of_the_series_is_pending_not_filled(cfg):
    s, _, r = ask(cfg, EntryModel.A_MARKET)
    f = resolve_fill(s, cfg, r.plan)
    assert f.state is FillState.PENDING


# ---------------------------------------------- SPEC 15.4: the limit fill buffer


def test_a_limit_merely_touched_is_not_filled(cfg):
    """SPEC 15.4's buffer. Assuming touch-fills is one of the largest silent optimisms in
    retail backtesting, and it flatters models B-E against the one baseline that cannot
    benefit from it."""
    _, _, r = ask(cfg, EntryModel.E_LEG_MIDPOINT)
    p = r.plan.price
    touch = (p + 0.0010, p + 0.0012, p, p + 0.0008)  # low exactly at the limit
    s, _, r2 = ask(cfg, EntryModel.E_LEG_MIDPOINT, tail=[touch, hold(p + 0.0008)])
    assert resolve_fill(s, cfg, r2.plan).state is not FillState.FILLED

    clear = (p + 0.0010, p + 0.0012, p - 0.0005, p + 0.0008)  # well through it
    s2, _, r3 = ask(cfg, EntryModel.E_LEG_MIDPOINT, tail=[clear, hold(p + 0.0008)])
    f = resolve_fill(s2, cfg, r3.plan)
    assert f.filled and f.price == pytest.approx(p)


# ------------------------------- SPEC 15.1 clause 1, and the continuity argument


def test_a_bar_touching_both_entry_and_stop_FILLS(cfg):
    """The regression test for D-013 §1.

    A bullish limit sits above its stop and price approaches from above, so any
    continuous path reaching the stop passed the entry first. The first version of this
    module treated such a bar as ambiguous and cancelled it "pessimistically" -- which is
    wrong physically, and is not even the pessimistic outcome, since a fill that then
    stops out loses 1R while a cancel loses nothing.
    """
    _, _, r = ask(cfg, EntryModel.E_LEG_MIDPOINT)
    p, stop = r.plan.price, r.plan.stop
    through = (p + 0.0010, p + 0.0012, stop - 0.0001, stop + 0.0002)
    s, _, r2 = ask(cfg, EntryModel.E_LEG_MIDPOINT, tail=[through, hold(stop + 0.0002)])
    f = resolve_fill(s, cfg, r2.plan)
    assert f.filled
    assert f.touched_both
    assert not f.gap_ambiguous


def test_a_gap_past_the_stop_cancels(cfg):
    """SPEC 15.1 clause 1's actual scenario, and a branch the synthetic fixture cannot
    produce: its bars always open exactly at the previous close."""
    _, _, r = ask(cfg, EntryModel.E_LEG_MIDPOINT)
    stop = r.plan.stop
    gapped = (stop - 0.0005, stop - 0.0003, stop - 0.0010, stop - 0.0007)
    s, _, r2 = ask(cfg, EntryModel.E_LEG_MIDPOINT, tail=[gapped, hold(stop - 0.0007)])
    f = resolve_fill(s, cfg, r2.plan)
    assert f.state is FillState.CANCELLED
    assert f.cancel_reason is CancelReason.SL_BEFORE_ENTRY
    assert f.gap_ambiguous


def test_no_fill_on_the_way_back_up_after_the_level_was_invalidated(cfg):
    """SPEC 15.1's own words for why clause 1 exists: *"a limit order can fill on the way
    back up from a level that already invalidated the idea."*"""
    _, _, r = ask(cfg, EntryModel.E_LEG_MIDPOINT)
    p, stop = r.plan.price, r.plan.stop
    gapped = (stop - 0.0005, stop - 0.0003, stop - 0.0010, stop - 0.0007)
    back = (stop - 0.0007, p + 0.0020, stop - 0.0006, p + 0.0015)  # returns through p
    s, _, r2 = ask(cfg, EntryModel.E_LEG_MIDPOINT, tail=[gapped, back, hold(p + 0.0015)])
    f = resolve_fill(s, cfg, r2.plan)
    assert f.state is FillState.CANCELLED
    assert f.bar == B_BAR + 1  # cancelled at the gap, never revisited


def test_m1_can_rescue_a_gap_bar_that_did_offer_the_level(cfg):
    """The only place `backtest.intrabar_mode` changes an answer."""
    _, _, r = ask(cfg, EntryModel.E_LEG_MIDPOINT)
    p, stop = r.plan.price, r.plan.stop
    gapped = (stop - 0.0005, stop - 0.0003, stop - 0.0010, stop - 0.0007)
    s, _, r2 = ask(cfg, EntryModel.E_LEG_MIDPOINT, tail=[gapped, hold(stop - 0.0007)])

    bar = B_BAR + 1
    lo, hi = int(s.open_time[bar]), int(s.close_time[bar])
    t = np.arange(lo, hi, 60, dtype=np.int64)
    px = np.full(len(t), p - 0.0003)  # the M1 path offers the limit before the stop
    px[len(t) // 2 :] = stop - 0.0008
    m1 = build_series(
        "EURUSD", "M1", t, t + 60, px, px + 0.00005, px - 0.00005, px, np.ones(len(t))
    )
    assert resolve_fill(s, cfg, r2.plan, m1=None).state is FillState.CANCELLED
    assert resolve_fill(s, cfg, r2.plan, m1=m1).state is FillState.FILLED


# ------------------------------------------------ SPEC 15.1: the other three cancels


def test_an_opposing_sweep_cancels(cfg):
    _, _, r = ask(cfg, EntryModel.E_LEG_MIDPOINT)
    p = r.plan.price
    s, _, r2 = ask(cfg, EntryModel.E_LEG_MIDPOINT, tail=[hold(p + 0.0010)] * 3)
    f = resolve_fill(s, cfg, r2.plan, opposing_sweep_bars=[B_BAR + 2])
    assert f.state is FillState.CANCELLED
    assert f.cancel_reason is CancelReason.OPPOSING_SWEEP


def test_a_bias_flip_cancels_only_when_configured(cfg):
    _, _, r = ask(cfg, EntryModel.E_LEG_MIDPOINT)
    p = r.plan.price
    s, _, r2 = ask(cfg, EntryModel.E_LEG_MIDPOINT, tail=[hold(p + 0.0010)] * 3)
    f = resolve_fill(s, cfg, r2.plan, bias_flip_bars=[B_BAR + 2])
    assert f.cancel_reason is CancelReason.BIAS_FLIP

    off, _ = load_config(overrides={"entry": {"cancel_on_bias_flip": False}})
    _, _, r3 = ask(off, EntryModel.E_LEG_MIDPOINT, tail=[hold(p + 0.0010)] * 3)
    f2 = resolve_fill(s, off, r3.plan, bias_flip_bars=[B_BAR + 2])
    assert f2.cancel_reason is not CancelReason.BIAS_FLIP


def test_an_unfilled_order_expires_at_the_configured_bar(cfg):
    _, _, r = ask(cfg, EntryModel.E_LEG_MIDPOINT)
    p = r.plan.price
    quiet = [hold(p + 0.0010)] * (cfg.entry.pending_expiry_bars + 2)
    s, _, r2 = ask(cfg, EntryModel.E_LEG_MIDPOINT, tail=quiet)
    f = resolve_fill(s, cfg, r2.plan)
    assert f.state is FillState.EXPIRED
    assert f.cancel_reason is CancelReason.ENTRY_EXPIRED
    assert f.bar == r2.plan.expires_at_bar


def test_a_window_the_series_outlives_is_pending_not_expired(cfg):
    """Counting a censored order as an expiry would understate every fill rate."""
    _, _, r = ask(cfg, EntryModel.E_LEG_MIDPOINT)
    p = r.plan.price
    s, _, r2 = ask(cfg, EntryModel.E_LEG_MIDPOINT, tail=[hold(p + 0.0010)])
    f = resolve_fill(s, cfg, r2.plan)
    assert f.state is FillState.PENDING


def test_cancels_are_checked_in_spec_order(cfg):
    """SPEC 15.1 lists them 1-4 and the first to occur wins; an opposing sweep on an
    earlier bar beats an expiry on a later one."""
    _, _, r = ask(cfg, EntryModel.E_LEG_MIDPOINT)
    p = r.plan.price
    quiet = [hold(p + 0.0010)] * (cfg.entry.pending_expiry_bars + 2)
    s, _, r2 = ask(cfg, EntryModel.E_LEG_MIDPOINT, tail=quiet)
    f = resolve_fill(s, cfg, r2.plan, opposing_sweep_bars=[B_BAR + 1])
    assert f.cancel_reason is CancelReason.OPPOSING_SWEEP
    assert f.bar == B_BAR + 1


# ----------------------------------- the gate's second half: verified against M1


@pytest.fixture(scope="module")
def m1_run():
    """One synthetic year generated at M1 and resampled up, so the H4 bars and the M1
    path describe the same underlying series."""
    cfg, _ = load_config()
    m1 = generate(
        "EURUSD", datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 6, 30, 23, 59, tzinfo=UTC), cfg, timeframe="M1", seed=41,
    )
    h4 = resample(m1, "H4", cfg)
    d1 = resample(m1, "D1", cfg)
    m15 = resample(m1, "M15", cfg)
    st = analyse_structure(h4, cfg)
    _, sw = analyse_sweeps(
        cfg=cfg, h4=h4, d1=d1, w1=resample(m1, "W1", cfg), mn1=resample(m1, "MN1", cfg),
        sessions=build_sessions(m15, cfg), h4_structure=st, d1_swings=detect_swings(d1, cfg),
    )
    fvgs = detect_fvgs(h4, cfg)
    res = analyse_mss(h4, cfg, sw.confirmed(), swings=st.swings, fvgs=fvgs)
    pop = [c for c in res.candidates if c.is_choch and c.displacement.confirmed]
    return cfg, m1, h4, st, fvgs, pop, atr_ref(h4, cfg.atr.period)


def _plans(cfg, h4, st, fvgs, pop, atr, model):
    out = []
    for i, c in enumerate(pop):
        a = leg_origin(c.sweep_extreme_bar, c.choch_bar, cfg)
        ob = propose(
            h4, cfg, direction=c.direction, sweep_extreme_bar=c.sweep_extreme_bar,
            leg_start=a, break_bar=c.choch_bar, reference_price=c.reference_price,
            displacement_confirmed=True, definition=ObDefinition.A_LAST_OPPOSING,
            swings=st.swings.swings, atr=atr, seq=i,
        )
        r = arm(
            h4, cfg, direction=c.direction, mss_bar=c.choch_bar, leg_start=a,
            sweep_extreme=c.sweep.sweep_extreme, break_price=float(h4.close[c.choch_bar]),
            model=model, fvgs=fvgs, order_block=ob.ob, atr=atr,
        )
        if r.ok:
            out.append(r.plan)
    return out


def test_the_fixture_produces_setups_for_every_model(m1_run):
    cfg, _, h4, st, fvgs, pop, atr = m1_run
    assert len(pop) > 20
    for m in EntryModel:
        assert _plans(cfg, h4, st, fvgs, pop, atr, m), m


def test_bar_level_and_m1_fill_resolution_agree(m1_run):
    """The gate's second half.

    The bar-level rule and the M1 replay must reach the same verdict wherever the bar
    determines the answer. They now do on every case; before D-013 §1 they disagreed on
    15, and the M1 path was right every time.
    """
    cfg, m1, h4, st, fvgs, pop, atr = m1_run
    disagreements = 0
    total = 0
    for m in EntryModel:
        for plan in _plans(cfg, h4, st, fvgs, pop, atr, m):
            total += 1
            a = resolve_fill(h4, cfg, plan, m1=None)
            b = resolve_fill(h4, cfg, plan, m1=m1)
            if a.state is not b.state:
                disagreements += 1
    assert total > 100
    assert disagreements == 0, f"{disagreements} of {total} disagreed"


def test_bars_touching_both_are_reached_and_resolved_as_fills(m1_run):
    """The continuity branch has to be exercised by the fixture or the regression test
    above is hypothetical."""
    cfg, m1, h4, st, fvgs, pop, atr = m1_run
    both = 0
    for m in EntryModel:
        for plan in _plans(cfg, h4, st, fvgs, pop, atr, m):
            f = resolve_fill(h4, cfg, plan, m1=m1)
            if f.touched_both:
                both += 1
                assert f.filled
    assert both > 0


def test_model_A_fill_rate_is_one_and_the_others_are_not(m1_run):
    """SPEC 15.5: fill rate is not a nuisance statistic. Model A is the only 100% model,
    which is what makes it the baseline and what makes per-SETUP expectancy the only
    valid comparison."""
    cfg, m1, h4, st, fvgs, pop, atr = m1_run
    rates = {}
    for m in EntryModel:
        plans = _plans(cfg, h4, st, fvgs, pop, atr, m)
        fills = [resolve_fill(h4, cfg, p, m1=m1) for p in plans]
        decided = [f for f in fills if f.state is not FillState.PENDING]
        rates[m] = sum(1 for f in decided if f.filled) / len(decided)
    assert rates[EntryModel.A_MARKET] == pytest.approx(1.0)
    for m in (EntryModel.B_RETRACEMENT, EntryModel.C_FVG, EntryModel.E_LEG_MIDPOINT):
        assert 0.0 < rates[m] < 0.9, m


def test_resolution_is_deterministic(m1_run):
    cfg, m1, h4, st, fvgs, pop, atr = m1_run
    plans = _plans(cfg, h4, st, fvgs, pop, atr, EntryModel.C_FVG)
    a = [resolve_fill(h4, cfg, p, m1=m1).state for p in plans]
    b = [resolve_fill(h4, cfg, p, m1=m1).state for p in plans]
    assert a == b
