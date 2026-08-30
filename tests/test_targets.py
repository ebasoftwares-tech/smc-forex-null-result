"""First-target placement and the minimum-RR gate (SPEC 17.1, 17.2).

The gate is a pre-trade rejection (SPEC 19 item 16), which is why this half of SPEC 17
sits in Phase 13 with the stop caps and the sizing rejections rather than in Phase 14
with the exit policy.

Two of these tests exist because of what implementing the gate turned up, and both are
statements about the *specification* rather than about the market:

* ``test_T3_cannot_pass_its_own_gate_at_the_default_min_rr`` — T3's first ladder rung is
  1R and ``min_rr`` is 1.5, so T3 is rejected on every setup it will ever see.
* ``test_T4_is_exempt_from_the_gate_which_makes_the_ablation_unpaired`` — T4 has no fixed
  target, so the gate cannot be applied to it, so it accepts setups T1–T3 reject.

Neither is fixed here. ``min_rr`` is an ABLATION parameter and BACKTEST_PROTOCOL section
10.2 forbids moving one to make a result appear; both need an explicit decision (D-014).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bot.config.loader import load_config
from bot.core.displacement import Direction
from bot.core.liquidity import LevelSource, LevelStatus, LiquidityLevel, Side
from bot.core.targets import (
    TargetModel,
    TargetReject,
    first_target,
    gate_is_reachable,
    select_target_level,
)

UTC = timezone.utc
ENTRY = 1.08365
STOP = 1.08105          # 26 pips — SPEC 16.4's own worked example
SL = ENTRY - STOP
ATR = 0.00450


def level(price: float, side: Side, ident: str = "L1", active: bool = True):
    """A liquidity level for the target tests.

    **The sides here were all inverted until D-019**, along with `_opposing_side` itself:
    a bullish setup's target was built as SELL_SIDE, which `liquidity.Side` defines as the
    pool sitting *below* price. The give-away was in this very helper -- every level is
    built with `source=PREV_DAY_HIGH`, and `period_levels` assigns a previous-day high to
    **BUY_SIDE**. The fixtures disagreed with the source they claimed to come from.
    """
    at = datetime(2026, 3, 2, 12, tzinfo=UTC)
    return LiquidityLevel(
        id=ident, symbol="EURUSD", side=side, source=LevelSource.PREV_DAY_HIGH,
        timeframe="D1", tier=1, price=price, formed_at=at, confirmed_at=at,
        status=LevelStatus.ACTIVE if active else LevelStatus.SWEPT,
    )


def ask(cfg, model, **kw):
    return first_target(
        cfg, direction=kw.pop("direction", Direction.BULLISH),
        entry_price=kw.pop("entry_price", ENTRY), stop=kw.pop("stop", STOP),
        atr_value=kw.pop("atr_value", ATR), model=model, **kw,
    )


# ------------------------------------------------------------------ SPEC 17.1


def test_T1_is_a_fixed_multiple_of_the_stop_distance(cfg):
    t = ask(cfg, TargetModel.T1_FIXED_R)
    assert t.ok
    assert t.price == pytest.approx(ENTRY + cfg.tp.r_multiple * SL)
    assert t.rr == pytest.approx(cfg.tp.r_multiple)


def test_T1_mirrors_for_a_sell(cfg):
    t = ask(cfg, TargetModel.T1_FIXED_R, direction=Direction.BEARISH,
            entry_price=STOP, stop=ENTRY)
    assert t.ok and t.price < STOP


def test_T2_sits_in_front_of_the_level_never_at_it(cfg):
    """SPEC 17.1: the level is where the resting orders are; an order at it is behind them."""
    lvl = level(1.09240, Side.BUY_SIDE)
    t = ask(cfg, TargetModel.T2_OPPOSING_LIQUIDITY, levels=[lvl],
            ranks={lvl.id: cfg.tp.min_target_rank})
    assert t.ok
    assert t.price == pytest.approx(1.09240 - cfg.tp.target_buffer_atr * ATR)
    assert t.price < lvl.price
    assert t.reference_id == lvl.id


def test_T2_takes_the_nearest_qualifying_level_not_the_best_ranked(cfg):
    """Rank filters; distance orders. The target is the first pool price has to reach."""
    near = level(1.09000, Side.BUY_SIDE, "near")
    far = level(1.09800, Side.BUY_SIDE, "far")
    ranks = {"near": cfg.tp.min_target_rank, "far": cfg.tp.min_target_rank + 5}
    t = ask(cfg, TargetModel.T2_OPPOSING_LIQUIDITY, levels=[far, near], ranks=ranks)
    assert t.reference_id == "near"


def test_T2_ignores_levels_below_min_target_rank(cfg):
    weak = level(1.09000, Side.BUY_SIDE, "weak")
    strong = level(1.09800, Side.BUY_SIDE, "strong")
    ranks = {"weak": cfg.tp.min_target_rank - 0.5, "strong": cfg.tp.min_target_rank}
    t = ask(cfg, TargetModel.T2_OPPOSING_LIQUIDITY, levels=[weak, strong], ranks=ranks)
    assert t.reference_id == "strong"


def test_T2_ignores_inactive_levels_and_the_wrong_side(cfg):
    dead = level(1.09000, Side.BUY_SIDE, "dead", active=False)
    wrong = level(1.09100, Side.SELL_SIDE, "wrong")
    ranks = {"dead": 9.0, "wrong": 9.0}
    assert select_target_level(
        [dead, wrong], cfg, direction=Direction.BULLISH, entry_price=ENTRY, ranks=ranks
    ) is None


def test_T2_with_no_qualifying_level_rejects_rather_than_falling_back_to_T1(cfg):
    """SPEC 17.2: ``below_min_rr_action = skip``. A fallback contaminates T2 with T1."""
    t = ask(cfg, TargetModel.T2_OPPOSING_LIQUIDITY, levels=[], ranks={})
    assert not t.ok and t.reason is TargetReject.NO_TARGET_AVAILABLE
    assert t.price is None


def test_T2_rejects_when_the_buffer_pulls_the_target_back_through_the_entry(cfg):
    """A level closer than the buffer is wide is not a target."""
    lvl = level(ENTRY + 0.00010, Side.BUY_SIDE)
    t = ask(cfg, TargetModel.T2_OPPOSING_LIQUIDITY, levels=[lvl],
            ranks={lvl.id: cfg.tp.min_target_rank})
    assert not t.ok and t.reason is TargetReject.NO_TARGET_AVAILABLE


# ------------------------------------------------------------------ SPEC 17.2


def test_a_target_inside_min_rr_is_rejected(cfg):
    lvl = level(ENTRY + 1.2 * SL, Side.BUY_SIDE)
    t = ask(cfg, TargetModel.T2_OPPOSING_LIQUIDITY, levels=[lvl],
            ranks={lvl.id: cfg.tp.min_target_rank})
    assert not t.ok and t.reason is TargetReject.RR_BELOW_MIN
    assert t.rr is not None and t.rr < cfg.tp.min_rr
    # The price is still reported, so the rejection log carries what was measured.
    assert t.price is not None


def test_T3_cannot_pass_its_own_gate_at_the_default_min_rr(cfg):
    """D-014 section 1 — a contradiction between two specification defaults.

    SPEC 17.2 measures ``tp_1`` and SPEC 17.1 puts T3's first rung at 1R, so ``rr`` is
    1.0 by construction against a 1.5 floor. Not a market fact and not a tuning
    opportunity: it holds on every setup, and ``min_rr`` is ABLATION.
    """
    t = ask(cfg, TargetModel.T3_PARTIAL_LADDER)
    assert not t.ok and t.reason is TargetReject.RR_BELOW_MIN
    assert t.rr == pytest.approx(cfg.tp.ladder_first_r)
    assert not gate_is_reachable(cfg, TargetModel.T3_PARTIAL_LADDER)

    # It passes at exactly one of the three declared ablation values for min_rr.
    reachable = [
        m for m in (1.0, 1.5, 2.0)
        if gate_is_reachable(
            load_config(overrides={"tp": {"min_rr": m}})[0],
            TargetModel.T3_PARTIAL_LADDER,
        )
    ]
    assert reachable == [1.0]


def test_T1_by_contrast_passes_the_gate_at_every_ablation_value(cfg):
    """The positive control: the gate is not simply rejecting everything."""
    for m in (1.0, 1.5, 2.0):
        c, _ = load_config(overrides={"tp": {"min_rr": m}})
        assert gate_is_reachable(c, TargetModel.T1_FIXED_R)
        assert ask(c, TargetModel.T1_FIXED_R).ok


def test_T4_is_exempt_from_the_gate_which_makes_the_ablation_unpaired(cfg):
    """D-014 section 6.

    SPEC 17.7 asks for 'paired T1–T4 variants on a shared setup stream'. T4 has no
    ``tp_1``, so there is nothing to divide by ``sl_distance``, so the gate cannot apply
    — and T4 therefore accepts a setup that T1–T3 reject. The streams are not shared,
    and a comparison that does not say so is wrong.
    """
    tight = level(ENTRY + 1.2 * SL, Side.BUY_SIDE)
    ranks = {tight.id: cfg.tp.min_target_rank}
    t2 = ask(cfg, TargetModel.T2_OPPOSING_LIQUIDITY, levels=[tight], ranks=ranks)
    t4 = ask(cfg, TargetModel.T4_STRUCTURE_TRAIL, levels=[tight], ranks=ranks)

    assert not t2.ok and t2.reason is TargetReject.RR_BELOW_MIN
    assert t4.ok                      # the same setup, accepted
    assert t4.price is None and t4.rr is None
    assert not t4.gate_applies and t2.gate_applies


def test_a_degenerate_stop_distance_is_rejected_not_divided_by(cfg):
    t = ask(cfg, TargetModel.T1_FIXED_R, stop=ENTRY)
    assert not t.ok and t.reason is TargetReject.NO_TARGET_AVAILABLE


def test_below_min_rr_action_cannot_be_set_to_fixed_fallback(cfg):
    """SPEC 17.2 names ``fixed_fallback`` and rejects it in the same paragraph."""
    with pytest.raises(Exception):
        load_config(overrides={"tp": {"below_min_rr_action": "fixed_fallback"}})


# ------------------------------------------------- the convention itself (D-019)


def test_a_long_targets_buy_side_liquidity_and_a_short_targets_sell_side(cfg):
    """SPEC 17.1's worked example, as a test.

    Its setup is a BUY LIMIT off a swept `sweep_low`, and the target it names is
    *"nearest opposing liquidity **PDH** 1.17240"*. A previous-day HIGH is a BUY_SIDE
    level (`period_levels` assigns it), so a long targets BUY_SIDE. "Opposing" means
    opposing to the side the setup **swept** -- a bullish setup sweeps SELL_SIDE below and
    runs at the BUY_SIDE pool above.

    This was inverted for the life of the project, and both the code's docstring and these
    tests asserted the inversion, which is why nothing caught it.
    """
    above = level(ENTRY + 3 * SL, Side.BUY_SIDE, "above")
    below_side_above_price = level(ENTRY + 3 * SL, Side.SELL_SIDE, "wrong_side")
    ranks = {"above": 9.0, "wrong_side": 9.0}

    picked = select_target_level(
        [below_side_above_price, above], cfg,
        direction=Direction.BULLISH, entry_price=ENTRY, ranks=ranks,
    )
    assert picked is not None and picked.id == "above"

    # And the mirror: a short targets the sell-side pool below.
    lower = level(ENTRY - 3 * SL, Side.SELL_SIDE, "below")
    upper_side_below = level(ENTRY - 3 * SL, Side.BUY_SIDE, "wrong_side_2")
    picked = select_target_level(
        [upper_side_below, lower], cfg,
        direction=Direction.BEARISH, entry_price=ENTRY, ranks={"below": 9.0, "wrong_side_2": 9.0},
    )
    assert picked is not None and picked.id == "below"


def test_a_level_snapshot_can_stand_in_for_a_level_in_the_selector(cfg):
    """The engine hands the gate `LevelSnapshot`s, not `LiquidityLevel`s, because the
    finished book cannot be read causally (D-019). The selector must accept both."""
    from bot.core.liquidity import LevelSnapshot

    snap = LevelSnapshot(
        id="S1", side=Side.BUY_SIDE, price=ENTRY + 3 * SL, rank=9.0, tier=1, strength=2,
    )
    picked = select_target_level(
        [snap], cfg, direction=Direction.BULLISH, entry_price=ENTRY, ranks={"S1": 9.0},
    )
    assert picked is snap
    assert snap.is_active
