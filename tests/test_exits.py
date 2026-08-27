"""Trade management and exit resolution (SPEC 17.3–17.5) and costs (SPEC 26).

The centre of this file is SPEC 17.5's ambiguity, and the point worth holding onto is that
it is **not** the same problem D-013 solved. Phase 12 found that a bar touching both a
limit entry and its stop is decided by continuity — price approaches from one side, so the
entry filled. An open trade sits *between* its stop and its target, so continuity rules out
neither, and ``backtest.intrabar_mode`` genuinely decides. This is the real version of the
problem Phase 12 had a fake version of, and the specification calls it *"the single largest
backtest bias"*.

Every bar here is hand-built so each branch is reachable by arithmetic. The synthetic
fixture cannot produce gaps at all (D-013 section 3), so the gap branches are constructed
here or nowhere.
"""

from __future__ import annotations

import numpy as np
import pytest

from bot.config.loader import load_config
from bot.config.schema import SymbolSpec
from bot.core.bars import build_series
from bot.core.costs import (
    commission,
    entry_slippage,
    fill_price_with_costs,
    spread_at,
    stop_slippage,
    swap,
)
from bot.core.displacement import Direction
from bot.core.exits import ExitReason, manage_stop, resolve_exit

H4 = 14400
ENTRY = 1.08000
STOP = 1.07800          # 20 pips
TARGET = 1.08400        # 2R
ATR = 0.00400
SPEC = SymbolSpec()


def series(rows, start=0):
    """``rows`` are (open, high, low, close); bar 0 is the entry bar."""
    n = len(rows)
    t = np.arange(start, start + n, dtype=np.int64) * H4
    return build_series(
        "EURUSD", "H4", t, t + H4,
        np.array([r[0] for r in rows]), np.array([r[1] for r in rows]),
        np.array([r[2] for r in rows]), np.array([r[3] for r in rows]), np.ones(n),
    )


def m1_path(prices, t0, t1):
    """An M1 series walking through ``prices`` inside one H4 bar."""
    n = len(prices)
    step = max(1, (t1 - t0) // max(n, 1))
    t = np.array([t0 + i * step for i in range(n)], dtype=np.int64)
    p = np.array(prices, dtype=np.float64)
    return build_series("EURUSD", "M1", t, t + step, p, p, p, p, np.ones(n))


def go(cfg, rows, **kw):
    s = kw.pop("series_", None) or series(rows)
    atr = np.full(s.n, ATR)
    return resolve_exit(
        s, cfg, direction=kw.pop("direction", Direction.BULLISH), entry_bar=0,
        entry_price=kw.pop("entry_price", ENTRY),
        planned_stop=kw.pop("planned_stop", STOP),
        target=kw.pop("target", TARGET), atr=atr, spec=SPEC,
        apply_slippage=kw.pop("apply_slippage", False), **kw,
    )


# ------------------------------------------------------------- the plain paths


def test_a_target_hit_closes_at_the_target(cfg):
    out = go(cfg, [(ENTRY, ENTRY, ENTRY, ENTRY), (ENTRY, TARGET + 0.0002, ENTRY - 0.0005, TARGET)])
    assert out.reason is ExitReason.TAKE_PROFIT
    assert out.price == pytest.approx(TARGET)
    assert out.r_multiple == pytest.approx(2.0)


def test_a_stop_hit_closes_at_the_stop(cfg):
    out = go(cfg, [(ENTRY,) * 4, (ENTRY, ENTRY + 0.0005, STOP - 0.0002, STOP)])
    assert out.reason is ExitReason.STOP_LOSS
    assert out.r_multiple == pytest.approx(-1.0)


def test_a_short_mirrors_exactly(cfg):
    out = go(
        cfg, [(ENTRY,) * 4, (ENTRY, ENTRY + 0.0005, 1.07600 - 0.0002, 1.07600)],
        direction=Direction.BEARISH, planned_stop=1.08200, target=1.07600,
    )
    assert out.reason is ExitReason.TAKE_PROFIT
    assert out.r_multiple == pytest.approx(2.0)


# ------------------------------------------- SPEC 17.5, the real ambiguity


def test_a_bar_containing_both_is_genuinely_ambiguous_unlike_the_entry_case(cfg):
    """The contrast with D-013 section 1, stated as a test.

    From the entry price both the stop and the target are reachable first, so continuity
    settles nothing. ``pessimistic`` takes the stop, which is what SPEC 17.5 mandates when
    M1 is absent.
    """
    rows = [(ENTRY,) * 4, (ENTRY, TARGET + 0.0002, STOP - 0.0002, ENTRY)]
    pess, _ = load_config(overrides={"backtest": {"intrabar_mode": "pessimistic"}})
    out = go(pess, rows)
    assert out.reason is ExitReason.STOP_LOSS
    assert out.intrabar_ambiguous


def test_the_m1_path_resolves_it_in_true_order(cfg):
    """SPEC 17.5: ``m1_path`` is 'the only correct option'."""
    rows = [(ENTRY,) * 4, (ENTRY, TARGET + 0.0002, STOP - 0.0002, ENTRY)]
    s = series(rows)
    t0, t1 = int(s.open_time[1]), int(s.close_time[1])

    up_first = m1_path([ENTRY, TARGET + 0.0001, ENTRY, STOP - 0.0001], t0, t1)
    out = go(cfg, rows, series_=s, m1=up_first)
    assert out.reason is ExitReason.TAKE_PROFIT
    assert out.intrabar_ambiguous

    down_first = m1_path([ENTRY, STOP - 0.0001, ENTRY, TARGET + 0.0001], t0, t1)
    out = go(cfg, rows, series_=s, m1=down_first)
    assert out.reason is ExitReason.STOP_LOSS


def test_an_m1_series_that_does_not_cover_the_bar_falls_back_to_pessimistic(cfg):
    """Never to a guess, and never to the convenient answer."""
    rows = [(ENTRY,) * 4, (ENTRY, TARGET + 0.0002, STOP - 0.0002, ENTRY)]
    s = series(rows)
    elsewhere = m1_path([ENTRY] * 4, 10**9, 10**9 + 240)
    out = go(cfg, rows, series_=s, m1=elsewhere)
    assert out.reason is ExitReason.STOP_LOSS


def test_stop_and_target_inside_one_m1_bar_take_the_pessimistic_reading(cfg):
    """One minute is the finest resolution the dataset has; below it there is no path."""
    rows = [(ENTRY,) * 4, (ENTRY, TARGET + 0.0002, STOP - 0.0002, ENTRY)]
    s = series(rows)
    t0, t1 = int(s.open_time[1]), int(s.close_time[1])
    n = 4
    step = (t1 - t0) // n
    t = np.array([t0 + i * step for i in range(n)], dtype=np.int64)
    o = np.full(n, ENTRY)
    hi = np.full(n, ENTRY)
    lo = np.full(n, ENTRY)
    hi[1], lo[1] = TARGET + 0.0001, STOP - 0.0001      # both in one minute
    both = build_series("EURUSD", "M1", t, t + step, o, hi, lo, o, np.ones(n))
    assert go(cfg, rows, series_=s, m1=both).reason is ExitReason.STOP_LOSS


# ------------------------------------------------------- the entry bar itself


def test_a_limit_that_fills_and_stops_out_on_one_bar_exits_on_that_bar(cfg):
    """D-015 section 5 — the entry bar is part of the trade.

    The first version started the walk at ``entry_bar + 1``, so a same-bar stop-out was
    carried to the next bar's open. That delayed every such loss and missed some outright:
    **6% of trades on the Phase 14 fixture close on their entry bar**, and including them
    moved measured expectancy by up to 0.04R — 40% of the protocol's own minimum
    acceptable edge.

    For a buy limit the ordering is not a guess. The fill is the first touch of ``p``, so
    the bar's low, being below ``p``, came at or after it. The stop was reached. That is
    D-013 section 1's continuity argument applied to the other end of the trade.
    """
    rows = [(ENTRY + 0.0020, ENTRY + 0.0025, STOP - 0.0002, STOP)]
    out = go(cfg, rows)
    assert out.bar == 0 and out.bars_held == 0
    assert out.reason is ExitReason.STOP_LOSS
    assert out.r_multiple == pytest.approx(-1.0)


def test_a_market_fill_sees_the_whole_of_its_entry_bar(cfg):
    """A market order fills at the open, so the entire bar happens after the fill."""
    rows = [(ENTRY, ENTRY + 0.0005, STOP - 0.0002, STOP)]
    out = go(cfg, rows, entry_at_bar_open=True)
    assert out.bar == 0
    assert out.reason is ExitReason.STOP_LOSS


def test_a_limits_entry_bar_target_is_not_credited_without_m1(cfg):
    """The asymmetry, and it is the honest reading rather than a cautious one.

    A buy limit fills on the way DOWN, so the bar's high may have printed before the fill.
    The low proves the stop was reachable after the fill; the high proves nothing about
    the target. One direction is settled by continuity and the other is not.
    """
    rows = [(ENTRY + 0.0020, TARGET + 0.0002, ENTRY - 0.0001, ENTRY),
            (ENTRY, ENTRY + 0.0002, ENTRY - 0.0002, ENTRY)]
    out = go(cfg, rows)
    assert out.bar != 0 or out.reason is not ExitReason.TAKE_PROFIT

    # A market fill on the same bar DOES see it, because the whole bar is after the fill.
    assert go(cfg, rows, entry_at_bar_open=True).reason is ExitReason.TAKE_PROFIT


def test_m1_restores_the_entry_bar_target_when_the_path_shows_it(cfg):
    """The asymmetry exists because OHLC cannot resolve it, not because it is unresolvable."""
    rows = [(ENTRY + 0.0020, TARGET + 0.0002, ENTRY - 0.0001, ENTRY),
            (ENTRY, ENTRY + 0.0002, ENTRY - 0.0002, ENTRY)]
    s = series(rows)
    t0, t1 = int(s.open_time[0]), int(s.close_time[0])
    down_then_up = m1_path([ENTRY + 0.0020, ENTRY - 0.0001, TARGET + 0.0001], t0, t1)
    out = go(cfg, rows, series_=s, m1=down_then_up)
    assert out.bar == 0
    assert out.reason is ExitReason.TAKE_PROFIT


def test_mfe_on_a_limits_entry_bar_is_clamped_to_the_entry(cfg):
    """An MFE the trade could not have had is not an MFE."""
    rows = [(ENTRY + 0.0020, ENTRY + 0.0060, ENTRY - 0.0001, ENTRY),
            (ENTRY, ENTRY + 0.0002, STOP - 0.0002, STOP)]
    out = go(cfg, rows)
    assert out.mfe_r < 0.5, out.mfe_r
    # A market fill at that bar's open would legitimately have caught the high.
    assert go(cfg, rows, entry_at_bar_open=True).mfe_r > 1.0


# ------------------------------------------------------------------ gaps


def test_a_gap_through_the_stop_fills_at_the_open_and_may_lose_more_than_1R(cfg):
    """SPEC 16.5: 'the R-multiple recorded is the realised one, which may be -2.4R'."""
    gap_open = STOP - 0.0060
    out = go(cfg, [(ENTRY,) * 4, (gap_open, gap_open + 0.0005, gap_open - 0.0005, gap_open)])
    assert out.reason is ExitReason.STOP_LOSS
    assert out.gapped and not out.intrabar_ambiguous
    assert out.price == pytest.approx(gap_open)
    assert out.r_multiple < -1.0


def test_a_gap_beyond_the_target_fills_better_than_planned(cfg):
    """The symmetric half, and it is what makes the gap model honest.

    Recording a favourable gap at the target would be a systematic pessimism, which is as
    wrong as the systematic optimism SPEC 26 warns about — just less flattering.
    """
    gap_open = TARGET + 0.0030
    out = go(cfg, [(ENTRY,) * 4, (gap_open, gap_open + 0.0005, gap_open - 0.0005, gap_open)])
    assert out.reason is ExitReason.TAKE_PROFIT
    assert out.gapped
    assert out.r_multiple > 2.0


def test_a_gap_is_resolved_before_the_ambiguity_is_asked_about(cfg):
    """Order matters: a gapped bar whose range also spans both is not ambiguous."""
    gap_open = STOP - 0.0010
    out = go(cfg, [(ENTRY,) * 4, (gap_open, TARGET + 0.0002, gap_open - 0.0002, ENTRY)])
    assert out.gapped and not out.intrabar_ambiguous
    assert out.reason is ExitReason.STOP_LOSS


# ------------------------------------------------------ SPEC 17.3 management


def test_break_even_is_off_by_default(cfg):
    """SPEC 17.3: on by default would flatter the statistic that matters least."""
    assert cfg.manage.be_trigger_r == 0.0
    stop = manage_stop(
        cfg, direction=Direction.BULLISH, entry_price=ENTRY, planned_stop=STOP,
        current_stop=STOP, best_r=5.0, extreme_price=ENTRY + 0.01, atr_value=ATR,
    )
    assert stop == STOP


def test_break_even_moves_the_stop_above_entry_by_the_offset(cfg):
    be, _ = load_config(overrides={"manage": {"be_trigger_r": 1.0}})
    stop = manage_stop(
        be, direction=Direction.BULLISH, entry_price=ENTRY, planned_stop=STOP,
        current_stop=STOP, best_r=1.2, extreme_price=ENTRY + 0.002, atr_value=ATR,
    )
    assert stop == pytest.approx(ENTRY + be.manage.be_offset_atr * ATR)
    # Below the trigger it must not move.
    assert manage_stop(
        be, direction=Direction.BULLISH, entry_price=ENTRY, planned_stop=STOP,
        current_stop=STOP, best_r=0.9, extreme_price=ENTRY + 0.001, atr_value=ATR,
    ) == STOP


def test_a_stop_can_only_move_toward_price(cfg):
    """A stop that can widen turns a -1R loss into a -2R one after the fact.

    It also makes R's denominator a moving target, which would quietly invalidate every
    R-based statistic in the project.
    """
    trail, _ = load_config(
        overrides={"manage": {"trail_mode": "atr", "trail_start_r": 0.5}}
    )
    tightened = manage_stop(
        trail, direction=Direction.BULLISH, entry_price=ENTRY, planned_stop=STOP,
        current_stop=ENTRY, best_r=3.0, extreme_price=ENTRY + 0.0005, atr_value=ATR,
    )
    assert tightened >= ENTRY
    for direction, worse in ((Direction.BULLISH, STOP - 0.01), (Direction.BEARISH, STOP + 0.01)):
        got = manage_stop(
            trail, direction=direction, entry_price=ENTRY, planned_stop=STOP,
            current_stop=ENTRY, best_r=0.0, extreme_price=worse, atr_value=ATR,
        )
        assert got == ENTRY

    # The clamp specifically: a trail far behind price proposes a LOOSER stop than the one
    # already in force, and must be ignored. This is the case an earlier version could not
    # reach, because each branch clamped internally and the final clamp was dead code --
    # D-014 section 8's lesson recurring one phase later.
    loose = manage_stop(
        trail, direction=Direction.BULLISH, entry_price=ENTRY, planned_stop=STOP,
        current_stop=ENTRY + 0.0050, best_r=5.0, extreme_price=ENTRY + 0.0010,
        atr_value=ATR,
    )
    assert loose == ENTRY + 0.0050


def test_management_runs_after_the_bar_it_is_on_is_resolved(cfg):
    """Otherwise break-even could trigger and stop out on the same bar.

    That is a one-bar lookahead inside the trade: the stop would be moved using the very
    high that the bar had not finished making when the move would have been placed.
    """
    be, _ = load_config(overrides={"manage": {"be_trigger_r": 1.0}})
    # One bar that reaches +1R and comes back to entry.  BE must not fire on it.
    rows = [(ENTRY,) * 4, (ENTRY, ENTRY + 0.0021, ENTRY - 0.0001, ENTRY),
            (ENTRY, ENTRY + 0.0002, STOP - 0.0002, STOP)]
    out = go(be, rows)
    assert out.bar == 2
    assert out.reason is ExitReason.BREAK_EVEN
    assert out.r_multiple > -1.0     # the BE stop, not the original one

    # The mutation this pins: one bar that reaches +1R AND returns to the stop. If
    # management ran before the bar was resolved, the BE stop would already be in force
    # and the trade would exit near breakeven instead of at -1R.
    one_bar = [(ENTRY,) * 4, (ENTRY, ENTRY + 0.0021, STOP - 0.0002, STOP)]
    got = go(be, one_bar)
    assert got.bar == 1
    assert got.reason is ExitReason.STOP_LOSS
    assert got.r_multiple == pytest.approx(-1.0)


# ------------------------------------------------------- SPEC 17.4 exits


def test_the_time_stop_closes_at_market(cfg):
    short, _ = load_config(overrides={"exit": {"max_bars_in_trade": 3}})
    flat = [(ENTRY, ENTRY + 0.0002, ENTRY - 0.0002, ENTRY)] * 10
    out = go(short, flat)
    assert out.reason is ExitReason.TIME_STOP
    assert out.bars_held == 3


def test_running_out_of_data_is_censored_not_time_stopped(cfg):
    """A TIME_STOP is a decision the strategy made; this one nobody took."""
    out = go(cfg, [(ENTRY, ENTRY + 0.0002, ENTRY - 0.0002, ENTRY)] * 4)
    assert out.reason is ExitReason.END_OF_DATA
    assert out.censored


def test_the_weekend_close_fires_on_the_configured_bar(cfg):
    """SPEC 17.4: Friday 19:00 UTC, to avoid the gap and the triple swap."""
    # Bar closing Fri 2026-01-02 20:00 UTC.
    from datetime import datetime, timezone

    friday_close = int(datetime(2026, 1, 2, 20, 0, tzinfo=timezone.utc).timestamp())
    t = np.array([friday_close - 2 * H4, friday_close - H4, friday_close], dtype=np.int64)
    flat = np.full(3, ENTRY)
    s = build_series("EURUSD", "H4", t - H4, t, flat, flat + 0.0002, flat - 0.0002,
                     flat, np.ones(3))
    out = resolve_exit(
        s, cfg, direction=Direction.BULLISH, entry_bar=0, entry_price=ENTRY,
        planned_stop=STOP, target=TARGET, atr=np.full(3, ATR), spec=SPEC,
        apply_slippage=False,
    )
    assert out.reason is ExitReason.WEEKEND_CLOSE

    off, _ = load_config(overrides={"exit": {"close_before_weekend": False}})
    assert resolve_exit(
        s, off, direction=Direction.BULLISH, entry_bar=0, entry_price=ENTRY,
        planned_stop=STOP, target=TARGET, atr=np.full(3, ATR), spec=SPEC,
        apply_slippage=False,
    ).reason is not ExitReason.WEEKEND_CLOSE


# ---------------------------------------------------------------- excursions


def test_mae_and_mfe_are_recorded_with_their_bars(cfg):
    """SPEC 17.7: computed once and reused, never re-optimised per target model."""
    rows = [
        (ENTRY,) * 4,
        (ENTRY, ENTRY + 0.0010, ENTRY - 0.0010, ENTRY),   # +-0.5R
        (ENTRY, ENTRY + 0.0030, ENTRY - 0.0002, ENTRY),   # +1.5R
        (ENTRY, ENTRY + 0.0002, STOP - 0.0002, STOP),
    ]
    out = go(cfg, rows)
    assert out.mfe_r == pytest.approx(1.5)
    assert out.bars_to_mfe == 2
    # The final bar's low is 0.0002 BELOW the stop, so the excursion is -1.1R even though
    # the exit fills at -1.0R.  MAE is measured on the bar, not on the fill -- which is
    # the whole point of recording it separately.
    assert out.mae_r == pytest.approx(-1.1)
    assert out.bars_to_mae == 3
    assert out.r_multiple == pytest.approx(-1.0)


# --------------------------------------------------------------- SPEC 26 costs


def test_stops_slip_more_than_entries(cfg):
    """'Modelling them symmetrically is a systematic optimism.'"""
    e = entry_slippage(cfg, spec=SPEC, atr_value=ATR)
    s = stop_slippage(cfg, spec=SPEC, atr_value=ATR)
    assert s > e > 0


def test_the_spread_is_wider_outside_the_active_sessions(cfg):
    active = spread_at(cfg, symbol="EURUSD", spec=SPEC, session="LONDON")
    quiet = spread_at(cfg, symbol="EURUSD", spec=SPEC, session="ASIA")
    assert quiet > active > 0
    assert spread_at(cfg, symbol="EURUSD", spec=SPEC, session=None) == quiet


def test_the_cost_multiplier_scales_every_cost(cfg):
    """BACKTEST_PROTOCOL 3.3's sensitivity dimension."""
    dear, _ = load_config(overrides={"cost": {"multiplier": 2.0}})
    assert spread_at(dear, symbol="EURUSD", spec=SPEC, session="LONDON") == pytest.approx(
        2 * spread_at(cfg, symbol="EURUSD", spec=SPEC, session="LONDON")
    )
    assert commission(dear, lots=1.0) == pytest.approx(2 * commission(cfg, lots=1.0))
    assert stop_slippage(dear, spec=SPEC, atr_value=ATR) == pytest.approx(
        2 * stop_slippage(cfg, spec=SPEC, atr_value=ATR)
    )


def test_swap_is_zero_and_says_so(cfg):
    """SPEC 26 takes it from the broker table (Q1). Zero beats an invented number."""
    assert cfg.cost.swap_pips_per_day == {}
    assert swap(cfg, symbol="EURUSD", lots=1.0, nights=5,
                direction=Direction.BULLISH) == 0.0


def test_a_long_buys_the_ask_and_a_short_sells_the_bid(cfg):
    sp = 0.0001
    long_in = fill_price_with_costs(ENTRY, direction=Direction.BULLISH, spread=sp,
                                    is_entry=True)
    short_in = fill_price_with_costs(ENTRY, direction=Direction.BEARISH, spread=sp,
                                     is_entry=True)
    assert long_in == pytest.approx(ENTRY + sp)
    assert short_in == pytest.approx(ENTRY - sp)


def test_exit_slippage_is_adverse_for_both_directions(cfg):
    slip = 0.0002
    assert fill_price_with_costs(STOP, direction=Direction.BULLISH, spread=0.0,
                                 slippage=slip, is_entry=False) < STOP
    assert fill_price_with_costs(STOP, direction=Direction.BEARISH, spread=0.0,
                                 slippage=slip, is_entry=False) > STOP


def test_entry_slippage_is_adverse_with_the_spread_held_at_zero(cfg):
    """The spread must not be able to mask a sign flip in the slippage term.

    The first version of this test compared the whole fill against the planned price with
    the spread included, and the spread (0.8 pips) is larger than the slippage (~0.5), so
    a mutation making slippage *favourable* still produced a worse fill and passed.
    """
    slip = 0.0002
    assert fill_price_with_costs(ENTRY, direction=Direction.BULLISH, spread=0.0,
                                 slippage=slip, is_entry=True) == pytest.approx(
        ENTRY + slip
    )
    assert fill_price_with_costs(ENTRY, direction=Direction.BEARISH, spread=0.0,
                                 slippage=slip, is_entry=True) == pytest.approx(
        ENTRY - slip
    )
