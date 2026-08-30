"""The backtest engine — Phase 14's gate.

*"Full protocol (`BACKTEST_PROTOCOL.md`); replay + shifted-data tests green; cost
sensitivity run."*

The two named tests are the load-bearing ones and they test different things:

* **Replay (SPEC 25.2)** catches lookahead. Running on a truncated series must reproduce
  the trades the full series produced up to that point, because a truncated engine cannot
  see the future bars a leaking component used. *"This is the primary defence — code
  review does not reliably catch a lookahead bug, and a suspiciously good equity curve is
  a very late signal."*
* **Shifted data (SPEC 25.3)** catches the one lookahead replay misses: *"an off-by-one in
  a shift() ... survives the replay test if the leak is exactly one bar and consistent."*
  Dropping the first bar shifts every index by one, so a consistent one-bar leak moves
  with it and shows up as a large change in the result.

The rest of the file pins the pieces the two rest on: costs applied once and in the right
direction, R independent of position size, and the two-pass split that makes that true.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from bot.backtest import metrics as M
from bot.backtest.engine import build_market, run, run_variants
from bot.config.loader import load_config
from bot.core.entries import EntryModel, OrderType
from bot.core.exits import ExitReason
from bot.data.synthetic import generate

UTC = timezone.utc


@pytest.fixture(scope="module")
def m1_half_year(cfg):
    return generate(
        "EURUSD", datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 6, 30, 23, 59, tzinfo=UTC), cfg, timeframe="M1", seed=41,
    )


@pytest.fixture(scope="module")
def market(cfg, m1_half_year):
    return build_market(cfg, m1_half_year)


@pytest.fixture(scope="module")
def result(cfg, market):
    return run(cfg, market, apply_limits=False)


# ------------------------------------------------------------ the gate, first half


def test_the_engine_is_deterministic(cfg, market):
    """SPEC 25.1: the same data and config produce byte-identical output.

    No wall clock, no unseeded randomness, no dict-ordering dependence. This is the
    cheapest of the three guarantees to check and the one everything else assumes.
    """
    a = run(cfg, market, apply_limits=False)
    b = run(cfg, market, apply_limits=False)
    assert [t.trade_id for t in a.trades] == [t.trade_id for t in b.trades]
    assert [t.r_net for t in a.trades] == [t.r_net for t in b.trades]
    assert a.funnel == b.funnel


def test_replay_on_truncated_data_reproduces_the_prefix(cfg, m1_half_year):
    """SPEC 25.2, the load-bearing test, at engine level.

    A trade that closed before the truncation point must be identical whether or not the
    engine was given the bars after it. Anything that reads ahead — a target from a future
    swing, an ATR computed over the whole series, a stop placed from a bar not yet closed
    — makes the two differ.
    """
    full = build_market(cfg, m1_half_year)
    full_res = run(cfg, full, apply_limits=False)
    assert full_res.n_trades >= 3, "a vacuous replay test would pass on zero trades"

    for frac in (0.5, 0.75):
        cut_bar = int(full.h4.n * frac)
        cut_time = int(full.h4.close_time[cut_bar])
        part = build_market(cfg, m1_half_year.slice_between(
            int(m1_half_year.open_time[0]), cut_time
        ))
        part_res = run(cfg, part, apply_limits=False)

        early_full = {
            t.trade_id: (round(t.r_multiple, 9), t.entry_bar, t.exit_bar)
            for t in full_res.trades if t.exit_bar < cut_bar - 1
        }
        early_part = {
            t.trade_id: (round(t.r_multiple, 9), t.entry_bar, t.exit_bar)
            for t in part_res.trades if not t.censored
        }
        shared = set(early_full) & set(early_part)
        assert shared, f"no overlap to compare at {frac}"
        for tid in shared:
            assert early_full[tid] == early_part[tid], (frac, tid)


def test_shifted_data_changes_the_result_only_marginally(cfg, m1_half_year):
    """SPEC 25.3, and the specific bug it exists for.

    *"An off-by-one in a shift() is the most common form of accidental lookahead and it
    survives the replay test if the leak is exactly one bar and consistent."* Dropping the
    first H4 bar's worth of M1 re-indexes everything; a consistent one-bar leak would move
    with the index and change the outcome sharply.
    """
    base = run(cfg, build_market(cfg, m1_half_year), apply_limits=False)
    shift_from = int(m1_half_year.open_time[0]) + 4 * 3600
    shifted = run(
        cfg,
        build_market(cfg, m1_half_year.slice_between(
            shift_from, int(m1_half_year.close_time[-1]) + 60
        )),
        apply_limits=False,
    )
    assert base.n_trades >= 3 and shifted.n_trades >= 3

    # Counts move a little; the distribution of outcomes must not move a lot.
    assert abs(base.n_trades - shifted.n_trades) <= max(3, 0.35 * base.n_trades)
    a = float(np.mean([t.r_net for t in base.trades]))
    b = float(np.mean([t.r_net for t in shifted.trades]))
    assert abs(a - b) < 1.0, (a, b)


# ------------------------------------------------------------ the two-pass split


def test_r_multiple_does_not_depend_on_equity(cfg, market):
    """The claim that makes R the primary metric, asserted rather than assumed.

    BACKTEST_PROTOCOL 4.1: *"net return conflates edge with position sizing and with the
    compounding path; R-expectancy is the property of the strategy itself."* Pass one
    cannot see equity, so changing the account size may change lots and PnL and must not
    move a single R.
    """
    small = run(cfg, market, equity=5_000.0, apply_limits=False)
    large = run(cfg, market, equity=500_000.0, apply_limits=False)
    assert small.n_trades == large.n_trades
    assert [round(t.r_multiple, 12) for t in small.trades] == [
        round(t.r_multiple, 12) for t in large.trades
    ]
    assert [t.lots for t in small.trades] != [t.lots for t in large.trades]


def test_pass_one_does_not_size_and_its_population_ignores_equity(cfg, market):
    """The geometry pass must not depend on an account size, not even a nominal one.

    SPEC 18.2's rejections are functions of equity, so running them in the pass that is
    meant to be portfolio-free makes the set of setups it produces depend on whichever
    number that pass was handed. An earlier version sized pass one at a constant, and
    changing that constant silently changed the funnel. See D-015 section 9.
    """
    # 1,000 against 3,000: Phase 13's account sweep put the lot-granularity boundary
    # between them, so one side loses trades to SIZE_BELOW_MIN and the other does not.
    # A far larger account is not the right comparison -- it starts losing trades to
    # SIZE_ABOVE_MAX instead, which would confound the two rejections.
    tiny, _ = load_config(overrides={"account": {"starting_equity": 1_000.0}})
    huge, _ = load_config(overrides={"account": {"starting_equity": 3_000.0}})
    a = run(tiny, market, apply_limits=False)
    b = run(huge, market, apply_limits=False)

    # Pass one is identical: same orders armed, same orders filled.
    assert a.funnel["orders_armed"] == b.funnel["orders_armed"]
    assert a.funnel["orders_filled"] == b.funnel["orders_filled"]

    # Pass two is not, and that is the point: a USD 500 account cannot size most of them,
    # so SIZE_BELOW_MIN removes trades there and nowhere else.
    assert len(a.trades) < len(b.trades)
    assert any(r.reason == "SIZE_BELOW_MIN" for r in a.rejections)
    assert not any(r.reason == "SIZE_BELOW_MIN" for r in b.rejections)

    # Whatever survives has identical geometry.
    shared = {t.trade_id: t for t in a.trades}
    common = [t for t in b.trades if t.trade_id in shared]
    assert common
    for t in common:
        assert round(t.r_multiple, 12) == round(shared[t.trade_id].r_multiple, 12)
        assert t.lots != shared[t.trade_id].lots


def test_the_portfolio_limits_remove_trades_and_nothing_else(cfg, market):
    """SPEC 18.9's on/off pair. Limits change which trades happen, never their geometry."""
    off = run(cfg, market, apply_limits=False)
    on = run(cfg, market, apply_limits=True)
    assert on.n_trades <= off.n_trades
    by_id = {t.trade_id: t for t in off.trades}
    for t in on.trades:
        assert t.trade_id in by_id
        assert round(t.r_multiple, 12) == round(by_id[t.trade_id].r_multiple, 12)


def test_fill_rate_is_an_order_property_not_a_portfolio_one(cfg, market):
    """Model A fills every order (Phase 12). Folding the position cap in hid that.

    The first version counted admitted *trades* over armed orders, which charged each
    model for SPEC 18.4's position cap — and the cap bites hardest on whichever model
    fills most. Model A read 58% instead of 100%. See D-015 section 3.
    """
    res = run(cfg, market, entry_model=EntryModel.A_MARKET, apply_limits=True)
    assert res.fill_rate == pytest.approx(1.0)
    assert res.orders_filled >= res.n_trades


# ---------------------------------------------------------------- shadow trades


def test_a_shadow_trade_is_counterfactual_on_the_cancel_not_on_the_fill(cfg, market):
    """SPEC 15.6, and the trap in it (D-015 section 3).

    A shadow must not assume a fill at a price price never reached. A bullish limit sits
    below the market, so entering there unconditionally is a free discount: the first
    version produced 38 take-profits against 2 stops for a mean of +1.57R while the filled
    population sat near zero.
    """
    res = run(cfg, market, entry_model=EntryModel.C_FVG, apply_limits=False)
    # Every shadow must correspond to an order that WOULD have filled without the cancel.
    assert len(res.shadows) <= res.qualified_setups - res.orders_filled
    if len(res.shadows) >= 5:
        outcomes = {s.exit_reason for s in res.shadows}
        assert ExitReason.STOP_LOSS in outcomes or ExitReason.TIME_STOP in outcomes, (
            "a shadow population with no losing exit is entering at prices never reached"
        )


def test_shadow_trades_never_touch_equity(cfg, market):
    res = run(cfg, market, apply_limits=False)
    booked = sum(t.pnl_net for t in res.trades)
    moved = res.equity_curve[-1][1] - res.equity_curve[0][1]
    assert moved == pytest.approx(booked, abs=1e-6)


# ---------------------------------------------------------------------- costs


def test_costs_are_adverse_in_both_directions(cfg, market):
    """SPEC 26: slippage is always adverse, and a long and a short must both pay it."""
    res = run(cfg, market, entry_model=EntryModel.A_MARKET, apply_limits=False)
    assert res.trades
    for t in res.trades:
        if t.order_type is not OrderType.MARKET:
            continue
        if t.direction.value == "BULLISH":
            assert t.fill_price > t.planned_price
        else:
            assert t.fill_price < t.planned_price


def test_a_limit_order_is_never_slipped(cfg, market):
    """A limit fills at its price or better -- slipping it models an impossibility.

    Compared against the **spread exactly**, not against a loose tolerance: the spread
    (0.8 pips) is larger than the entry slippage (~0.5), so a tolerance wide enough to
    admit both cannot tell whether slippage was applied.
    """
    from bot.core.costs import spread_at
    from bot.core.stops import symbol_spec

    spec = symbol_spec(cfg, market.symbol)
    res = run(cfg, market, entry_model=EntryModel.C_FVG, apply_limits=False)
    checked = 0
    for t in res.trades:
        if t.order_type is not OrderType.LIMIT:
            continue
        checked += 1
        sp = spread_at(cfg, symbol=market.symbol, spec=spec, session=t.entry_session)
        expected = t.planned_price + sp if t.direction.value == "BULLISH" else (
            t.planned_price - sp
        )
        assert t.fill_price == pytest.approx(expected, abs=1e-12), t.trade_id
    assert checked >= 3


def test_higher_costs_never_improve_a_trade(cfg, market):
    """BACKTEST_PROTOCOL 3.3's sensitivity run, as a monotonicity property.

    Doubling costs must not make any individual trade better. A cost applied with the
    wrong sign somewhere would show up here and nowhere else.
    """
    cheap, _ = load_config(overrides={"cost": {"multiplier": 1.0}})
    dear, _ = load_config(overrides={"cost": {"multiplier": 2.0}})
    a = run(cheap, market, entry_model=EntryModel.A_MARKET, apply_limits=False)
    b = run(dear, market, entry_model=EntryModel.A_MARKET, apply_limits=False)
    by_id = {t.trade_id: t for t in b.trades}
    compared = 0
    for t in a.trades:
        other = by_id.get(t.trade_id)
        if other is None:
            continue
        compared += 1
        assert other.r_net <= t.r_net + 1e-12, t.trade_id
    assert compared >= 3


# --------------------------------------------------------------------- metrics


def test_the_paired_bake_off_runs_every_model_over_one_setup_stream(cfg, market):
    """SPEC 15.8: the comparison is paired or it is two populations."""
    res = run_variants(cfg, market, apply_limits=False)
    assert set(res) == set(EntryModel)
    comps = M.compare_models(res)
    assert len(comps) == 5
    a = next(c for c in comps if c.model == "A")
    assert a.fill_rate == pytest.approx(1.0)
    for c in comps:
        if c.model != "A":
            assert c.fill_rate < 1.0


def test_small_breakdown_cells_are_labelled_not_reported(cfg, result):
    """Protocol 4.2: under 30 is not reportable, 30-99 is suggestive."""
    cells = M.breakdown(result.trades, lambda t: t.exit_reason.value)
    assert cells
    for c in cells:
        if c.n < 30:
            assert c.label == "not reportable" and not c.reportable
        elif c.n < 100:
            assert c.label == "suggestive" and not c.reportable
        else:
            assert c.reportable


def test_metrics_do_not_claim_a_headline_on_a_thin_sample(cfg, result):
    """Protocol 5.1: a headline strategy result needs 200 trades."""
    m = M.compute(result, n_boot=500, total_bars=result.funnel.get("levels_created"))
    assert m.n == result.n_trades
    assert m.reportable == (m.n >= 200)
    assert np.isfinite(m.expectancy_r)
    lo, hi = m.expectancy_r_ci
    assert lo <= m.expectancy_r <= hi


def test_the_session_matrix_exists_and_reports_its_diagonal(cfg, result):
    """Protocol 4.2.1, added by D-002 and required rather than optional."""
    matrix = M.session_matrix(result.trades)
    assert matrix
    share = M.diagonal_share(matrix)
    assert 0.0 <= share <= 1.0


def test_every_rejection_carries_a_named_reason(cfg, result):
    """SPEC 19: nothing exits a setup silently."""
    assert result.rejections
    for r in result.rejections:
        assert r.reason
        assert r.stage is not None


# ------------------------------------------- the liquidity book reaches the gate (D-019)
#
# SPEC 17.2's target gate reads the liquidity book for T2. Until D-019 the engine passed
# it nothing, so `select_target_level` iterated an empty sequence and T2 rejected every
# setup with NO_TARGET_AVAILABLE -- at any value of `tp.min_target_rank`, including 0.


def test_the_market_carries_a_per_bar_view_of_the_liquidity_book(market):
    assert market.level_snapshots, "no snapshots captured"
    assert set(market.level_snapshots) <= set(range(market.h4.n))
    # Capped by SPEC 8.9's prune cap, so this is tens of objects per bar, not a book copy.
    assert max(len(v) for v in market.level_snapshots.values()) <= 40


def test_the_snapshot_is_causal_and_cannot_be_reconstructed_from_the_finished_book(
    cfg, m1_half_year, market
):
    """The third instance of a trap this project has recorded twice (D-009 §4 for swings,
    D-011 §3 for FVGs).

    SPEC 8.8's merge mutates a surviving level's `price`, `tier` and `strength` **in
    place**, and termination rewrites `status`. So asking a finished level what it looked
    like at an early bar returns what it became by the last one. The snapshot is captured
    at the bar instead; this asserts it actually differs from the finished book, because
    a test that passed either way would prove nothing.
    """
    from bot.core.liquidity import build_candidates, LiquidityEngine
    from bot.data.resample import resample
    from bot.core.swings import detect_swings
    from bot.core.structure import analyse_structure
    from bot.core.sessions import build_sessions

    h4 = market.h4
    d1 = resample(m1_half_year, "D1", cfg)
    candidates = build_candidates(
        cfg=cfg, h4=h4, d1=d1, w1=resample(m1_half_year, "W1", cfg),
        mn1=resample(m1_half_year, "MN1", cfg),
        sessions=build_sessions(resample(m1_half_year, "M15", cfg), cfg),
        h4_structure=analyse_structure(h4, cfg), d1_swings=detect_swings(d1, cfg),
    )
    book = LiquidityEngine(h4, cfg, candidates, d1_close_times=d1.close_time).run()
    final = {l.id: l for l in book.levels}

    # No snapshot may contain a level that was not yet confirmed, or was already dead.
    for bar, snap in book.snapshots.items():
        for view in snap:
            lvl = final[view.id]
            assert 0 <= lvl.confirmed_bar <= bar, (bar, view.id)
            assert lvl.terminal_bar < 0 or lvl.terminal_bar >= bar, (bar, view.id)

    # And the finished book genuinely disagrees with the snapshots, which is the whole
    # reason the snapshots exist.
    moved = 0
    for bar, snap in book.snapshots.items():
        for view in snap:
            lvl = final[view.id]
            if lvl.price != view.price or lvl.strength != view.strength:
                moved += 1
    assert moved > 0, (
        "no level's price or strength ever changed after being snapshotted -- either the "
        "merge stopped mutating in place, or this test is no longer testing anything"
    )


def test_t2_can_arm_now_that_the_gate_can_see_the_book(cfg, market):
    """The wiring bug, pinned by its symptom.

    Before D-019 this was `NO_TARGET_AVAILABLE` on **every** setup, and lowering
    `tp.min_target_rank` to 0 changed nothing -- the filter was never reached because the
    sequence being filtered was empty.
    """
    from collections import Counter
    from bot.config.loader import load_config

    c, _ = load_config(overrides={"tp": {"model": "opposing_liquidity"}})
    res = run(c, market, apply_limits=False)
    reasons = Counter(r.reason for r in res.rejections)

    setups = len(market.setups)
    assert reasons["NO_TARGET_AVAILABLE"] < setups, (
        "T2 still finds no target on any setup -- the book is not reaching the gate"
    )


def test_both_passes_gate_against_the_same_bars_book(cfg, market):
    """Pass two re-runs the target gate over the plan pass one formed. If it read the fill
    bar's book instead of the arming bar's, the two could disagree about whether the same
    setup had a target -- a disagreement about a price nobody has paid."""
    from bot.config.loader import load_config

    c, _ = load_config(overrides={"tp": {"model": "opposing_liquidity"}})
    geometry = run(c, market, apply_limits=False)
    portfolio = run(c, market, apply_limits=True)

    # Every trade the portfolio pass books must have survived pass one's gate too.
    assert {t.setup_id for t in portfolio.trades} <= {t.setup_id for t in geometry.trades}

    # `result.rejections` carries BOTH passes, so a target rejection cannot be attributed
    # by inspecting one run. Pass one is identical in both, so any *extra* target
    # rejection when the portfolio pass runs is pass two's -- and there must be none.
    def n_no_target(res):
        return sum(1 for r in res.rejections if r.reason == "NO_TARGET_AVAILABLE")

    assert n_no_target(portfolio) == n_no_target(geometry), (
        "the portfolio pass added target rejections, so it is reading a different bar's "
        "book from the one that formed the plan"
    )
