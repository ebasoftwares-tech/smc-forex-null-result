"""Stop placement and the SPEC 16.3 constraints — half of Phase 13's gate.

The constraints are the interesting half. SPEC 16.3 says a setup is **rejected, never
adjusted**, and the reason it gives is a bias argument rather than a purity one:
tightening a stop to fit a cap creates a low-quality trade with a structurally wrong stop
*precisely on the widest, most volatile setups*. So the tests here are as much about what
does not happen — no price is ever moved — as about which reason fires.

Each new rule carries a positive control and, where the rule could be silently deleted, a
mutation check: a test that fails when the rule is removed rather than one that merely
passes while it is present (STATE.md section 7).
"""

from __future__ import annotations

import numpy as np
import pytest

from bot.config.loader import load_config
from bot.config.schema import SymbolSpec
from bot.core.bars import build_series
from bot.core.displacement import Direction
from bot.core.entries import ArmReject, EntryModel, OrderType, arm
from bot.core.fvg import detect_fvgs
from bot.core.indicators import atr_ref
from bot.core.order_blocks import ObDefinition, propose
from bot.core.stops import (
    StopModel,
    StopReject,
    check_stop,
    dominant_upper_cap,
    planned_stop,
    stop_buffer,
    symbol_spec,
)

H4 = 14400
WARM = 20
MID = 1.07860
HALF = 0.00225

SETUP = [
    (1.07800, 1.07850, 1.07300, 1.07780),  # 20  sweep low + the OB origin bar
    (1.07780, 1.08100, 1.07770, 1.08080),  # 21
    (1.08080, 1.08700, 1.08060, 1.08660),  # 22  break bar
]
S_BAR, A_BAR, B_BAR = WARM, WARM + 1, WARM + 2
SWEEP_EXTREME = 1.07300
BREAK_PRICE = 1.08660
REFERENCE = 1.08600


def make(tail=()):
    rows = SETUP + list(tail)
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
    return s, atr, float(atr[B_BAR]), detect_fvgs(s, cfg)


def ob_for(cfg, s, atr):
    p = propose(
        s, cfg, direction=Direction.BULLISH, sweep_extreme_bar=S_BAR, leg_start=A_BAR,
        break_bar=B_BAR, reference_price=REFERENCE, displacement_confirmed=True,
        definition=ObDefinition.A_LAST_OPPOSING, atr=atr, seq=1,
    )
    assert p.ok
    return p.ob


# ------------------------------------------------------------- SPEC 16.2 buffer


def test_buffer_is_the_max_of_its_three_terms_not_the_atr_one(cfg):
    """SPEC 16.2 is a max, and the spread term is 'not optional' (SPEC 1.3)."""
    spec = SymbolSpec()
    atr = 0.00450
    assert stop_buffer(cfg, atr_value=atr, spec=spec) == pytest.approx(
        cfg.sl.buffer_atr * atr
    )
    # A spread wide enough to dominate: two spreads above 0.10 ATR is SPEC 1.3's own
    # example of the case the term exists for.
    wide = 0.00300
    assert stop_buffer(cfg, atr_value=atr, spec=spec, spread=wide) == pytest.approx(
        cfg.sl.buffer_spread_mult * wide
    )


def test_the_spread_term_is_omitted_not_zeroed_when_there_is_no_spread_data(cfg):
    """``spread=None`` and ``spread=0.0`` must be the same number but different calls.

    They agree here only because ``max`` ignores a zero. The distinction is kept at the
    call site so that "we have no spread data" never reads as "the spread was zero".
    """
    spec = SymbolSpec()
    a = stop_buffer(cfg, atr_value=0.00450, spec=spec, spread=None)
    b = stop_buffer(cfg, atr_value=0.00450, spec=spec, spread=0.0)
    assert a == b


def test_a_broker_stops_level_raises_the_buffer_floor(cfg):
    """SPEC 16.2's third term, inert at the declared default of 0 points (Q1)."""
    tight = SymbolSpec(stops_level_points=0)
    broker = SymbolSpec(stops_level_points=300)  # 30 pips on a 5-digit major
    small_atr = 0.00050
    assert stop_buffer(cfg, atr_value=small_atr, spec=tight) == pytest.approx(
        cfg.sl.buffer_atr * small_atr
    )
    assert stop_buffer(cfg, atr_value=small_atr, spec=broker) > cfg.sl.buffer_atr * small_atr


# -------------------------------------------------------- SPEC 16.1 four models


def test_S1_is_the_sweep_extreme_less_the_buffer(cfg, fx):
    s, atr, a, _ = fx
    got = planned_stop(
        cfg, direction=Direction.BULLISH, atr_value=a, sweep_extreme=SWEEP_EXTREME,
        model=StopModel.S1_SWEEP_EXTREME,
    )
    assert got == pytest.approx(SWEEP_EXTREME - cfg.sl.buffer_atr * a)


def test_S2_uses_the_lowest_low_of_the_whole_setup_window(cfg, fx):
    """SPEC 16.1: ``min(L over [s..b])``, not the sweep extreme.

    The two coincide unless a bar inside the window dipped lower, which is exactly why
    S1 and S2 are near-duplicates on most setups — measured in the Phase 13 report.
    """
    s, atr, a, _ = fx
    # A window whose lowest low IS the sweep extreme: S1 and S2 agree.
    same = planned_stop(
        cfg, direction=Direction.BULLISH, atr_value=a, sweep_extreme=SWEEP_EXTREME,
        model=StopModel.S2_STRUCTURAL_SWING, series=s,
        setup_start_bar=S_BAR, break_bar=B_BAR,
    )
    assert same == pytest.approx(SWEEP_EXTREME - cfg.sl.buffer_atr * a)

    # A window containing a lower low: S2 drops and S1 does not.
    deeper = make([(1.08660, 1.08700, 1.07100, 1.08600)])
    d_atr = float(atr_ref(deeper, cfg.atr.period)[B_BAR + 1])
    s2 = planned_stop(
        cfg, direction=Direction.BULLISH, atr_value=d_atr, sweep_extreme=SWEEP_EXTREME,
        model=StopModel.S2_STRUCTURAL_SWING, series=deeper,
        setup_start_bar=S_BAR, break_bar=B_BAR + 1,
    )
    s1 = planned_stop(
        cfg, direction=Direction.BULLISH, atr_value=d_atr, sweep_extreme=SWEEP_EXTREME,
        model=StopModel.S1_SWEEP_EXTREME,
    )
    assert s2 < s1
    assert s2 == pytest.approx(1.07100 - cfg.sl.buffer_atr * d_atr)


def test_S3_uses_the_order_blocks_distal_edge(cfg, fx):
    s, atr, a, _ = fx
    ob = ob_for(cfg, s, atr)
    got = planned_stop(
        cfg, direction=Direction.BULLISH, atr_value=a, sweep_extreme=SWEEP_EXTREME,
        model=StopModel.S3_ORDER_BLOCK, order_block=ob,
    )
    assert got == pytest.approx(ob.distal - cfg.sl.buffer_atr * a)


def test_S4_is_measured_from_the_entry_price_and_ignores_the_buffer(cfg, fx):
    """SPEC 16.1's table gives S4 no buffer term, so ``sl.buffer_atr`` is inert under it.

    Implemented as written. Worth pinning rather than leaving implicit: a buffer ablation
    across {0.05, 0.10, 0.20} covers three of the four stop models and this test is where
    that is recorded.
    """
    _, _, a, _ = fx
    entry = 1.08400
    got = planned_stop(
        cfg, direction=Direction.BULLISH, atr_value=a, sweep_extreme=SWEEP_EXTREME,
        model=StopModel.S4_ATR, entry_price=entry,
    )
    assert got == pytest.approx(entry - cfg.sl.atr_multiple * a)

    wider, _ = load_config(overrides={"sl": {"buffer_atr": 0.20}})
    assert planned_stop(
        wider, direction=Direction.BULLISH, atr_value=a, sweep_extreme=SWEEP_EXTREME,
        model=StopModel.S4_ATR, entry_price=entry,
    ) == pytest.approx(got)


def test_a_model_missing_its_own_input_raises_rather_than_falling_back(cfg, fx):
    """SPEC 15.7's rule applied to stops: a silent degrade to S1 makes S1 a mixture."""
    _, _, a, _ = fx
    for model, kw in (
        (StopModel.S2_STRUCTURAL_SWING, {}),
        (StopModel.S3_ORDER_BLOCK, {}),
        (StopModel.S4_ATR, {}),
    ):
        with pytest.raises(ValueError):
            planned_stop(
                cfg, direction=Direction.BULLISH, atr_value=a,
                sweep_extreme=SWEEP_EXTREME, model=model, **kw,
            )


def test_every_model_mirrors_for_a_sell(cfg, fx):
    s, atr, a, _ = fx
    for model, kw in (
        (StopModel.S1_SWEEP_EXTREME, {}),
        (StopModel.S2_STRUCTURAL_SWING,
         {"series": s, "setup_start_bar": S_BAR, "break_bar": B_BAR}),
        (StopModel.S4_ATR, {"entry_price": 1.07500}),
    ):
        up = planned_stop(cfg, direction=Direction.BULLISH, atr_value=a,
                          sweep_extreme=SWEEP_EXTREME, model=model, **kw)
        down = planned_stop(cfg, direction=Direction.BEARISH, atr_value=a,
                            sweep_extreme=SWEEP_EXTREME, model=model, **kw)
        assert down > up, model


# ---------------------------------------------------- SPEC 16.3 reject, never adjust


def test_a_stop_wider_than_max_sl_atr_is_rejected(cfg):
    atr = 0.00100
    c = check_stop(
        cfg, symbol="EURUSD", direction=Direction.BULLISH,
        entry_price=1.08000, stop=1.08000 - 3.0 * atr, atr_value=atr,
    )
    assert not c.ok and c.reason is StopReject.SL_TOO_WIDE
    assert c.binding == "max_sl_atr"


def test_a_stop_wider_than_max_sl_pips_is_rejected_even_within_the_atr_cap(cfg):
    """The two upper caps are independent, and on H4 majors the pip cap usually binds."""
    atr = 0.00400  # 40 pips: 2.5 ATR is 100 pips, well past the 60-pip cap
    c = check_stop(
        cfg, symbol="EURUSD", direction=Direction.BULLISH,
        entry_price=1.08000, stop=1.08000 - 0.00700, atr_value=atr,
    )
    assert not c.ok and c.reason is StopReject.SL_TOO_WIDE
    assert c.binding == "max_sl_pips"


def test_a_stop_tighter_than_min_sl_pips_is_rejected(cfg):
    c = check_stop(
        cfg, symbol="EURUSD", direction=Direction.BULLISH,
        entry_price=1.08000, stop=1.08000 - 0.00050, atr_value=0.00400,
    )
    assert not c.ok and c.reason is StopReject.SL_TOO_TIGHT


def test_the_jpy_family_gets_its_own_caps(cfg):
    """8 pips on a JPY cross is a third of the intended minimum — the family fallback."""
    spec = symbol_spec(cfg, "USDJPY")
    ten_pips = 10 * spec.pip_size
    assert check_stop(
        cfg, symbol="EURUSD", direction=Direction.BULLISH, entry_price=150.000,
        stop=150.000 - 10 * 0.0001, atr_value=0.00400,
    ).ok
    c = check_stop(
        cfg, symbol="USDJPY", direction=Direction.BULLISH, entry_price=150.000,
        stop=150.000 - ten_pips, atr_value=0.400, spec=spec,
    )
    assert not c.ok and c.reason is StopReject.SL_TOO_TIGHT  # 10 < 12


def test_a_broker_stops_level_rejects_rather_than_widening(cfg):
    """SPEC 1.4: 'a setup whose stop is inside it MUST be rejected, not silently widened'."""
    spec = SymbolSpec(stops_level_points=300)  # 30 pips
    c = check_stop(
        cfg, symbol="EURUSD", direction=Direction.BULLISH, entry_price=1.08000,
        stop=1.08000 - 0.00200, atr_value=0.00400, spec=spec,
    )
    assert not c.ok and c.binding == "stops_level"


def test_a_stop_inside_the_spread_is_rejected_at_fill_time(cfg):
    """SPEC 16.5: checked at arm AND at fill, because the spread moves in between."""
    c = check_stop(
        cfg, symbol="EURUSD", direction=Direction.BULLISH, entry_price=1.08000,
        stop=1.08000 - 0.00150, atr_value=0.00400, spread=0.00200,
    )
    assert not c.ok and c.reason is StopReject.SPREAD_EXCEEDS_STOP


def test_a_stop_on_the_wrong_side_of_the_entry_is_invalid_geometry(cfg):
    """SPEC 16.5: 'impossible by construction; asserted ... never silently corrected'."""
    c = check_stop(
        cfg, symbol="EURUSD", direction=Direction.BULLISH, entry_price=1.08000,
        stop=1.08100, atr_value=0.00400,
    )
    assert not c.ok and c.reason is StopReject.INVALID_GEOMETRY


def test_check_stop_never_returns_a_moved_price(cfg):
    """The mutation check for SPEC 16.3's central rule.

    ``StopCheck`` carries no price field at all, so there is no channel through which an
    adjusted stop could be returned. If a future change adds one, this fails.
    """
    c = check_stop(
        cfg, symbol="EURUSD", direction=Direction.BULLISH, entry_price=1.08000,
        stop=1.08000 - 0.00900, atr_value=0.00400,
    )
    assert not c.ok
    fields = set(vars(c))
    assert "stop" not in fields and "adjusted" not in fields and "price" not in fields


# --------------------------------------------- which upper cap actually does the work


def test_the_upper_caps_cross_at_a_computable_atr(cfg):
    """``max_sl_atr`` and ``max_sl_pips`` are both FROZEN and only one can ever reject.

    They cross at ``max_sl_pips / max_sl_atr`` = 24 pips of ATR on a major: below that
    ATR the ATR cap binds and the pip cap is decoration, above it the reverse. Which one
    is decoration therefore depends on the ATR distribution and is a measurement, not an
    assumption -- the Phase 13 gate reports it (100% ATR-cap on a fixture whose median
    H4 ATR is 17.4 pips), and it is one of the numbers most likely to flip on real bars.
    """
    pip = 0.0001
    assert dominant_upper_cap(cfg, "EURUSD", 20 * pip) == "max_sl_atr"
    assert dominant_upper_cap(cfg, "EURUSD", 40 * pip) == "max_sl_pips"
    crossover = cfg.risk.max_sl_pips["default"] / cfg.risk.max_sl_atr
    assert crossover == pytest.approx(24.0)


# ------------------------------------------------- the four models through ``arm``


def test_arm_supports_every_stop_model(cfg, fx):
    """Phase 12's gate said five entry models arm; this is the other axis."""
    s, atr, a, fvgs = fx
    ob = ob_for(cfg, s, atr)
    for sl_model in StopModel:
        r = arm(
            s, cfg, direction=Direction.BULLISH, mss_bar=B_BAR, leg_start=S_BAR,
            sweep_extreme=SWEEP_EXTREME, break_price=BREAK_PRICE,
            model=EntryModel.E_LEG_MIDPOINT, fvgs=fvgs, order_block=ob, atr=atr,
            sl_model=sl_model,
        )
        assert r.ok, (sl_model, r.reason)
        assert r.plan.sl_model == sl_model.value


def test_S3_without_an_order_block_is_a_rejection_not_an_exception(cfg, fx):
    """No OB proposed is an ordinary business outcome, unlike a malformed call."""
    s, atr, a, fvgs = fx
    r = arm(
        s, cfg, direction=Direction.BULLISH, mss_bar=B_BAR, leg_start=S_BAR,
        sweep_extreme=SWEEP_EXTREME, break_price=BREAK_PRICE,
        model=EntryModel.E_LEG_MIDPOINT, fvgs=fvgs, order_block=None, atr=atr,
        sl_model=StopModel.S3_ORDER_BLOCK,
    )
    assert not r.ok and r.reason is ArmReject.NO_OB_AVAILABLE


def test_S4_makes_PRICE_THROUGH_STOP_structurally_unreachable(cfg, fx):
    """D-014 section 4: a guard that is vacuous for one model in four.

    Under S1-S3 the stop is anchored to structure and a limit price can land beyond it.
    Under S4 the stop is placed a fixed ATR distance from the price, so the two can never
    cross — the check is correct and cannot fire. Pinned so a zero in a rejection table
    is read as "impossible", not as "never happened to occur".
    """
    s, atr, a, fvgs = fx
    ob = ob_for(cfg, s, atr)
    for model in EntryModel:
        r = arm(
            s, cfg, direction=Direction.BULLISH, mss_bar=B_BAR, leg_start=S_BAR,
            # A sweep extreme far above the leg would put an S1 stop through every limit.
            sweep_extreme=1.09000, break_price=BREAK_PRICE,
            model=model, fvgs=fvgs, order_block=ob, atr=atr,
            sl_model=StopModel.S4_ATR,
        )
        assert r.reason is not ArmReject.PRICE_THROUGH_STOP
        if r.ok and r.plan.order_type is OrderType.LIMIT:
            assert r.plan.stop < r.plan.price


def test_S1_still_rejects_a_limit_that_sits_through_its_stop(cfg, fx):
    """The positive control for the previous test: the guard does fire for S1."""
    s, atr, a, fvgs = fx
    ob = ob_for(cfg, s, atr)
    r = arm(
        s, cfg, direction=Direction.BULLISH, mss_bar=B_BAR, leg_start=S_BAR,
        sweep_extreme=1.09000, break_price=BREAK_PRICE,
        model=EntryModel.E_LEG_MIDPOINT, fvgs=fvgs, order_block=ob, atr=atr,
        sl_model=StopModel.S1_SWEEP_EXTREME,
    )
    assert not r.ok and r.reason is ArmReject.PRICE_THROUGH_STOP


def test_the_setup_window_defaults_to_the_leg_but_can_start_at_the_sweep(cfg, fx):
    """SPEC 10's leg origin is clamped to the sweep extreme bar and may sit after it.

    S2's window is ``[s..b]``, so passing the leg start where the sweep bar belongs
    silently shortens it. The parameter defaults to the leg start for compatibility and
    the Phase 13 report passes the sweep bar.
    """
    s, atr, a, fvgs = fx
    late = arm(
        s, cfg, direction=Direction.BULLISH, mss_bar=B_BAR, leg_start=A_BAR,
        sweep_extreme=SWEEP_EXTREME, break_price=BREAK_PRICE,
        model=EntryModel.E_LEG_MIDPOINT, fvgs=fvgs, atr=atr,
        sl_model=StopModel.S2_STRUCTURAL_SWING,
    )
    early = arm(
        s, cfg, direction=Direction.BULLISH, mss_bar=B_BAR, leg_start=A_BAR,
        sweep_extreme=SWEEP_EXTREME, break_price=BREAK_PRICE,
        model=EntryModel.E_LEG_MIDPOINT, fvgs=fvgs, atr=atr,
        sl_model=StopModel.S2_STRUCTURAL_SWING, setup_start_bar=S_BAR,
    )
    assert late.ok and early.ok
    assert early.plan.stop < late.plan.stop
