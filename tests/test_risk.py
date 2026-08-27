"""Position sizing and the risk limits (SPEC 18) — Phase 13's gate.

*"Every limit exercised by scenario; sizing purity test passes."*

**The purity half is asserted three ways, because one is not enough.** SPEC 18.1 makes
martingale, loss-recovery sizing and averaging down unimplementable by withholding the
information they need — *"the only reliable way to prevent them"*. A behavioural test can
only show the function did not use history on the inputs it happened to get, so:

* by **introspection**, that the signature admits no history-shaped parameter;
* by **construction**, that a ledger carrying any history produces the same lots;
* by **invariant**, that the drawdown ladder is monotone non-increasing and clamped at
  1.0, which no configuration can express a violation of and no arithmetic can produce.

**The scenario half lives in ``bot.research.risk_study.run_scenarios``** and is asserted
here as a battery. Each scenario carries a *near miss* as well as a trigger, because a
limit that fires on its trigger has been shown to fire, while a limit that also declines
to fire one step below it has been shown to fire *because of* the trigger. Without the
second half a check that rejected everything would pass.

Two limits cannot be reached by any legal configuration and are reported as such rather
than quietly passing — ``max_total_open_risk_pct`` and ``min_realised_fraction``. See
D-014 sections 2 and 3.
"""

from __future__ import annotations

import inspect
import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from bot.config.loader import load_config
from bot.config.schema import SymbolSpec
from bot.core.displacement import Direction
from bot.core.risk import (
    ClosedTrade,
    MissingConversionRate,
    OpenPosition,
    RiskLedger,
    RiskReject,
    correlation_clusters,
    kill_switch_breached,
    position_size,
    realised_risk_distribution,
    size_for_setup,
    value_per_price_unit_per_lot,
)
from bot.research.risk_study import (
    account_sweep,
    ladder_profile,
    minimum_viable_equity,
    run_scenarios,
)

UTC = timezone.utc
MON = datetime(2026, 3, 2, 12, tzinfo=UTC)
EUR = SymbolSpec()


def pos(symbol="EURUSD", i=0, risk_pct=0.35, bullish=True):
    return OpenPosition(f"s{i}", symbol,
                        Direction.BULLISH if bullish else Direction.BEARISH,
                        risk_pct, MON)


# ------------------------------------------------------ the sizing purity test


def test_sizing_is_a_pure_function_of_its_declared_inputs():
    """SPEC 18.1's invariant, asserted on the signature rather than on behaviour.

    The forbidden strategies all need one thing: knowledge of what happened before. This
    asserts the function cannot have it. A behavioural test would only show that a
    particular call did not use history.
    """
    params = set(inspect.signature(position_size).parameters)
    assert params == {
        "equity", "risk_pct", "sl_distance",
        "spec", "value_per_unit", "min_realised_fraction",
    }
    forbidden = {
        "history", "trades", "pnl", "streak", "consecutive", "losses", "ledger",
        "state", "equity_curve", "drawdown", "last_result", "wins",
    }
    assert not (params & forbidden)

    # And the module-level function closes over nothing mutable.
    assert not position_size.__closure__


def test_the_same_inputs_size_identically_whatever_the_ledger_has_seen():
    """The behavioural half: history cannot reach the arithmetic even when it exists."""
    cfg, _ = load_config()
    baseline = size_for_setup(cfg, symbol="EURUSD", equity=10_000.0, risk_pct=0.35,
                              sl_distance=0.0026)
    led = RiskLedger(cfg, equity=10_000.0)
    for i in range(50):
        led.record_close(ClosedTrade("EURUSD", MON + timedelta(hours=i), -100.0))
        led.open(pos(i=i))
        led.close(f"s{i}")
    after = size_for_setup(cfg, symbol="EURUSD", equity=10_000.0, risk_pct=0.35,
                           sl_distance=0.0026)
    assert after == baseline


def test_the_risk_layer_can_only_reduce_never_raise():
    """SPEC 18.1: ``risk_pct`` may only be REDUCED by the risk layer, never increased."""
    cfg, _ = load_config()
    led = RiskLedger(cfg, equity=100_000.0)
    assert led.risk_multiplier() == 1.0
    for dd in np.linspace(0.0, 9.9, 200):
        led.mark_equity(100_000.0 * (1 - dd / 100.0))
        assert led.risk_multiplier() <= 1.0
        assert led.effective_risk_pct() <= cfg.risk.pct_per_trade
    # A profit past the old peak resets the peak; it never buys a multiplier above 1.
    led.mark_equity(500_000.0)
    assert led.risk_multiplier() == 1.0


def test_the_ladder_is_monotone_non_increasing_in_drawdown():
    """SPEC 18.5's anti-martingale invariant at portfolio level."""
    cfg, _ = load_config()
    prof = ladder_profile(cfg)
    mults = [m for _, m in prof]
    assert mults == sorted(mults, reverse=True)
    assert max(mults) <= 1.0
    assert dict(prof)[0.0] == 1.0
    assert dict(prof)[5.0] == 0.75
    assert dict(prof)[8.0] == 0.50
    assert dict(prof)[4.99] == 1.0        # the boundary is inclusive upward
    assert dict(prof)[7.99] == 0.75


def test_the_ladder_is_monotone_in_drawdown_but_not_in_time():
    """SPEC 18.5 explicitly restores the multiplier as drawdown falls.

    A test asserting monotonicity in *time* would be asserting the opposite of what the
    specification says, so this pins the recovery instead.
    """
    cfg, _ = load_config()
    led = RiskLedger(cfg, equity=100_000.0)
    led.mark_equity(93_000.0)                     # 7% down
    assert led.risk_multiplier() == 0.75
    led.mark_equity(98_000.0)                     # recovered to 2%
    assert led.risk_multiplier() == 1.0
    assert led.state.peak_equity == 100_000.0


def test_no_configuration_can_express_an_increasing_ladder():
    """The validator, not the clamp: a violation must be unwritable, not just unused."""
    for bad in ([(5.0, 1.25)], [(5.0, 0.75), (8.0, 0.90)], [(8.0, 0.5), (5.0, 0.75)]):
        with pytest.raises(Exception):
            load_config(overrides={"risk": {"dd_ladder": bad}})


def test_the_clamp_holds_even_if_the_validator_is_bypassed():
    """The other half of the same guarantee, and it needs its own test.

    The validator says no *configuration* can express an increase; the clamp in
    ``drawdown_multiplier`` says no *arithmetic* can produce one. Because the validator
    fires first, nothing reachable through ``load_config`` can exercise the clamp — a
    mutation deleting it survived the whole suite until this test existed. ``model_copy``
    bypasses validation, which is the only way to ask the second question.
    """
    from bot.core.risk import drawdown_multiplier

    cfg, _ = load_config()
    rogue = cfg.model_copy(
        update={"risk": cfg.risk.model_copy(update={"dd_ladder": [(5.0, 1.5)]})}
    )
    assert rogue.risk.dd_ladder == [(5.0, 1.5)]      # the validator really was bypassed
    assert drawdown_multiplier(rogue, peak_equity=100_000.0, equity=90_000.0) == 1.0
    led = RiskLedger(rogue, equity=100_000.0)
    led.mark_equity(90_000.0)
    assert led.risk_multiplier() == 1.0
    assert led.effective_risk_pct() <= rogue.risk.pct_per_trade


def test_risk_pct_cannot_be_set_outside_the_briefs_band():
    for bad in (0.05, 0.75):
        with pytest.raises(Exception):
            load_config(overrides={"risk": {"pct_per_trade": bad}})


# --------------------------------------------------------------- SPEC 18.2 sizing


def test_the_worked_example_from_the_specification_reproduces():
    """SPEC 23.1: EUR10,000 at 0.35%, EURUSD at 1.1663, 24.8-pip stop -> 0.16 lots."""
    cfg, _ = load_config(overrides={"account": {"currency": "EUR"}})
    spec = cfg.symbol_specs["EURUSD"]
    v = value_per_price_unit_per_lot(spec, "EUR", quote_to_account=1 / 1.1663)
    assert v == pytest.approx(85_741, rel=1e-4)
    r = position_size(10_000.0, 0.35, 0.00248, spec=spec, value_per_unit=v,
                      min_realised_fraction=cfg.risk.min_realised_fraction)
    assert r.ok
    assert r.lots == pytest.approx(0.16)
    assert r.realised_risk == pytest.approx(34.02, rel=1e-3)


def test_lots_are_floored_never_rounded():
    """Rounding up takes more risk than was asked for — the one forbidden direction."""
    r = position_size(10_000.0, 0.35, 0.0026, spec=EUR, value_per_unit=100_000.0,
                      min_realised_fraction=0.5)
    assert r.raw_lots == pytest.approx(0.1346, abs=1e-4)
    assert r.lots == pytest.approx(0.13)
    assert r.realised_risk <= r.intended_risk


def test_an_exact_multiple_of_the_lot_step_does_not_quantise_one_step_low():
    """0.03 / 0.01 is 2.9999999999999996 in IEEE 754; the epsilon guard is not cosmetic."""
    v = 100_000.0
    intended = 10_000.0 * 0.35 / 100
    sl = intended / (0.03 * v)            # raw_lots is exactly 0.03
    r = position_size(10_000.0, 0.35, sl, spec=EUR, value_per_unit=v,
                      min_realised_fraction=0.5)
    assert r.lots == pytest.approx(0.03)
    assert r.realised_fraction == pytest.approx(1.0)


def test_realised_risk_never_exceeds_intended():
    """SPEC 18.9: 'any mass above the nominal value is a sizing bug'."""
    rng = np.random.default_rng(3)
    out = [
        position_size(float(rng.uniform(500, 500_000)), float(rng.uniform(0.10, 0.50)),
                      float(rng.uniform(0.0008, 0.0060)), spec=EUR,
                      value_per_unit=100_000.0, min_realised_fraction=0.5)
        for _ in range(20_000)
    ]
    dist = realised_risk_distribution(out)
    assert dist["above_nominal"] == 0.0
    assert dist["max_fraction"] <= 1.0


def test_min_realised_fraction_is_unreachable_at_its_default():
    """D-014 section 3 — a defaulted safety check that is provably dead.

    ``lots = k x step`` with ``raw < (k+1) x step`` gives a realised fraction above
    ``k/(k+1) >= 1/2`` for every lot grid, so ``realised < 0.5 x intended`` never holds.
    Proved by construction below and confirmed by 20,000 randomised sizings.
    """
    cfg, _ = load_config()
    assert cfg.risk.min_realised_fraction == 0.5

    rng = np.random.default_rng(11)
    fired = 0
    worst = 1.0
    for _ in range(20_000):
        r = position_size(float(rng.uniform(200, 200_000)), float(rng.uniform(0.10, 0.50)),
                          float(rng.uniform(0.0005, 0.0060)), spec=EUR,
                          value_per_unit=100_000.0, min_realised_fraction=0.5)
        fired += r.reason is RiskReject.SIZE_UNDER_RISK
        if r.ok:
            worst = min(worst, r.realised_fraction)
    assert fired == 0
    assert worst >= 0.5

    # The analytic worst case, on three different lot grids.
    for step in (0.01, 0.1, 1.0):
        spec = SymbolSpec(lot_step=step, min_lot=step)
        intended = 10_000.0 * 0.35 / 100
        sl = intended / ((2 * step - 1e-9) * 100_000.0)
        r = position_size(10_000.0, 0.35, sl, spec=spec, value_per_unit=100_000.0,
                          min_realised_fraction=0.5)
        assert r.ok
        assert r.realised_fraction >= 0.5


def test_the_specifications_own_justification_for_that_check_does_not_trip_it():
    """SPEC 18.2 describes a EUR2,000 account taking 'half' the risk. It takes 0.52."""
    r = position_size(2_000.0, 0.25, 26 * 0.0001, spec=EUR, value_per_unit=100_000.0,
                      min_realised_fraction=0.5)
    assert r.ok
    assert r.realised_fraction == pytest.approx(0.52, abs=0.005)
    # The positive control: raising the threshold does catch it, so the check works.
    strict = position_size(2_000.0, 0.25, 26 * 0.0001, spec=EUR,
                           value_per_unit=100_000.0, min_realised_fraction=0.75)
    assert strict.reason is RiskReject.SIZE_UNDER_RISK


def test_a_missing_conversion_rate_blocks_the_symbol_rather_than_defaulting():
    """SPEC 18.2: 'its absence blocks the inclusion of any symbol whose quote currency is
    not the account currency'. A silent 1.0 is wrong by up to 40% on JPY pairs."""
    cfg, _ = load_config()
    usdjpy = cfg.symbol_specs["USDJPY"]
    with pytest.raises(MissingConversionRate):
        value_per_price_unit_per_lot(usdjpy, "USD")
    assert value_per_price_unit_per_lot(usdjpy, "USD", quote_to_account=1 / 156.0) == (
        pytest.approx(100_000 / 156.0)
    )
    # EURUSD on a USD account needs no series at all: the rate is 1 by identity.
    assert value_per_price_unit_per_lot(cfg.symbol_specs["EURUSD"], "USD") == 100_000.0


def test_size_rejections_name_themselves():
    cfg, _ = load_config()
    tiny = size_for_setup(cfg, symbol="EURUSD", equity=200.0, risk_pct=0.10,
                          sl_distance=0.0060)
    assert tiny.reason is RiskReject.SIZE_BELOW_MIN
    huge = position_size(1e9, 0.50, 0.0026, spec=EUR, value_per_unit=100_000.0,
                         min_realised_fraction=0.5)
    assert huge.reason is RiskReject.SIZE_ABOVE_MAX


# ---------------------------------------------------- SPEC 18.4, every limit


def test_every_limit_is_exercised_by_a_scenario(cfg):
    """The gate, as a battery. Each row needs its trigger AND its near miss."""
    scenarios = run_scenarios(cfg)
    failed = [s for s in scenarios if not s.ok]
    assert not failed, [
        (s.limit, s.expected, s.got, s.near_miss_expected, s.near_miss_got)
        for s in failed
    ]
    assert len(scenarios) >= 18


def test_the_scenario_battery_covers_every_limit_the_config_declares(cfg):
    """A limit added to the config without a scenario should fail this, not pass silently."""
    covered = {s.limit for s in run_scenarios(cfg)}
    required = {
        "max_daily_loss_pct", "max_weekly_loss_pct", "max_monthly_loss_pct",
        "max_consecutive_losses", "max_open_positions", "max_positions_per_symbol",
        "max_correlated_positions", "max_total_open_risk_pct", "max_spread_pips",
        "max_spread_pct_of_sl", "equity_dd_kill_pct", "min_realised_fraction",
    }
    assert required <= covered


def test_a_loss_limit_halts_and_the_next_period_clears_it(cfg):
    """SPEC 18.4: 'halt new entries until the next day boundary'."""
    led = RiskLedger(cfg, equity=100_000.0)
    led.record_close(ClosedTrade("EURUSD", MON, -2_000.0))
    assert led.check(MON + timedelta(hours=1), symbol="EURUSD",
                     direction=Direction.BULLISH, risk_pct=0.35) is (
        RiskReject.RISK_LIMIT_DAILY
    )
    assert led.check(MON + timedelta(days=1), symbol="EURUSD",
                     direction=Direction.BULLISH, risk_pct=0.35) is None


def test_the_monthly_halt_persists_until_manually_cleared(cfg):
    """SPEC 18.4 singles the monthly limit out: it 'requires manual re-enable'."""
    led = RiskLedger(cfg, equity=100_000.0)
    for w in range(3):
        for d in range(3):
            led.record_close(
                ClosedTrade("EURUSD", MON + timedelta(days=7 * w + d), -1_000.0)
            )
    at = MON + timedelta(days=16)
    assert led.check(at, symbol="EURUSD", direction=Direction.BULLISH,
                     risk_pct=0.35) is RiskReject.RISK_LIMIT_MONTHLY
    # A new month does not lift it on its own, unlike the daily and weekly halts.
    assert led.check(MON + timedelta(days=60), symbol="EURUSD",
                     direction=Direction.BULLISH,
                     risk_pct=0.35) is RiskReject.RISK_LIMIT_MONTHLY
    led.state.monthly_halt = False
    assert led.check(MON + timedelta(days=60), symbol="EURUSD",
                     direction=Direction.BULLISH, risk_pct=0.35) is None


def test_a_win_resets_the_consecutive_loss_counter(cfg):
    led = RiskLedger(cfg, equity=100_000.0)
    for i in range(cfg.risk.max_consecutive_losses - 1):
        led.record_close(ClosedTrade("EURUSD", MON + timedelta(hours=i), -10.0))
    led.record_close(ClosedTrade("EURUSD", MON + timedelta(hours=9), +10.0))
    assert led.state.consecutive_losses == 0
    for i in range(cfg.risk.max_consecutive_losses - 1):
        led.record_close(ClosedTrade("EURUSD", MON + timedelta(hours=10 + i), -10.0))
    assert led.check(MON + timedelta(hours=20), symbol="EURUSD",
                     direction=Direction.BULLISH, risk_pct=0.35) is None


def test_max_total_open_risk_pct_is_unreachable_within_the_tunable_band(cfg):
    """D-014 section 2 — a limit that no legal configuration can breach.

    ``max_open_positions`` is 3 and ``pct_per_trade`` tops out at 0.50%, so open risk
    reaches exactly the 1.5% cap and never exceeds it. The position count always binds
    first. Implemented and scenario-tested; the scenario has to step outside the band.
    """
    n, hi, cap = cfg.risk.max_open_positions, 0.50, cfg.risk.max_total_open_risk_pct
    assert n * hi <= cap                       # cannot breach at the top of the band
    assert n * cfg.risk.pct_per_trade < cap    # nor at the default

    led = RiskLedger(cfg, equity=100_000.0)
    led.open(pos("GBPUSD", 0, risk_pct=hi))
    led.open(pos("AUDUSD", 1, risk_pct=hi))
    assert led.check(MON, symbol="EURUSD", direction=Direction.BULLISH,
                     risk_pct=hi) is None
    # The positive control: the check does fire, just not on anything legal.
    assert led.check(MON, symbol="EURUSD", direction=Direction.BULLISH,
                     risk_pct=0.60) is RiskReject.RISK_LIMIT_EXPOSURE


def test_the_position_cap_binds_before_the_exposure_cap(cfg):
    """Which is the substantive half of the previous test."""
    led = RiskLedger(cfg, equity=100_000.0)
    for i in range(cfg.risk.max_open_positions):
        led.open(pos(f"SYM{i}USD", i, risk_pct=0.50))
    assert led.check(MON, symbol="EURUSD", direction=Direction.BULLISH,
                     risk_pct=0.50) is RiskReject.RISK_LIMIT_POSITIONS


# ------------------------------------------------------- SPEC 18.7 correlation


def test_directionally_equivalent_exposure_counts_toward_the_cluster(cfg):
    """SPEC 18.7: 'long EURUSD and short USDCHF are the same position'."""
    clusters = {"EURUSD": "C+", "USDCHF": "C-", "GBPUSD": "C+"}
    led = RiskLedger(cfg, equity=100_000.0, clusters=clusters)
    led.open(pos("GBPUSD", 0, bullish=True))
    led.open(pos("USDCHF", 1, bullish=False))     # same net exposure as long EURUSD
    assert led.check(MON, symbol="EURUSD", direction=Direction.BULLISH,
                     risk_pct=0.35) is RiskReject.RISK_LIMIT_CORRELATION
    # The other side of the same cluster is a different exposure and is allowed.
    other = RiskLedger(cfg, equity=100_000.0, clusters=clusters)
    other.open(pos("GBPUSD", 0, bullish=True))
    other.open(pos("USDCHF", 1, bullish=True))
    assert other.check(MON, symbol="EURUSD", direction=Direction.BULLISH,
                       risk_pct=0.35) is None


def test_clusters_are_built_from_correlation_with_a_sign(cfg):
    n = 200
    rng = np.random.default_rng(5)
    base = rng.normal(size=n)
    returns = {
        "EURUSD": base + rng.normal(scale=0.05, size=n),
        "GBPUSD": base + rng.normal(scale=0.05, size=n),
        "USDCHF": -base + rng.normal(scale=0.05, size=n),
        "USDJPY": rng.normal(size=n),               # independent
    }
    clusters = correlation_clusters(returns, cfg)
    assert clusters["EURUSD"][-1] == clusters["GBPUSD"][-1]
    assert clusters["EURUSD"][-1] != clusters["USDCHF"][-1]
    assert clusters["EURUSD"][:-1] == clusters["USDCHF"][:-1]   # one cluster
    assert "USDJPY" not in clusters


def test_an_uncorrelated_panel_produces_no_clusters(cfg):
    """The negative control: the cap must not group things that are not correlated."""
    rng = np.random.default_rng(6)
    returns = {s: rng.normal(size=400) for s in ("EURUSD", "GBPUSD", "USDJPY")}
    assert correlation_clusters(returns, cfg) == {}


# ------------------------------------------------------ SPEC 18.6 kill switch


def test_the_kill_switch_fires_on_floating_equity_not_closed_pnl(cfg):
    """SPEC 18.4 marks this limit '(includes floating)' and the loss limits 'closed'."""
    led = RiskLedger(cfg, equity=100_000.0)
    led.mark_equity(90_000.0)               # floating, nothing closed
    assert led.state.closed == []
    assert led.kill_switch()
    assert kill_switch_breached(cfg, peak_equity=100_000.0, equity=90_000.0)
    assert not kill_switch_breached(cfg, peak_equity=100_000.0, equity=91_000.0)


def test_two_loss_streaks_in_one_week_is_a_halt_not_a_pause(cfg):
    led = RiskLedger(cfg, equity=100_000.0)
    for wk in range(2):
        for i in range(cfg.risk.max_consecutive_losses):
            led.record_close(
                ClosedTrade("EURUSD", MON + timedelta(days=wk, hours=i), -10.0)
            )
    assert led.kill_switch()
    # A single streak is a timeout that lifts on its own.
    one = RiskLedger(cfg, equity=100_000.0)
    for i in range(cfg.risk.max_consecutive_losses):
        one.record_close(ClosedTrade("EURUSD", MON + timedelta(hours=i), -10.0))
    assert not one.kill_switch()


def test_the_manual_trigger_exists(cfg):
    """SPEC 18.6: 'a kill switch that can only fire automatically cannot be used by the
    person watching the screen'."""
    led = RiskLedger(cfg, equity=100_000.0)
    assert not led.kill_switch()
    led.trip()
    assert led.kill_switch()
    assert led.check(MON, symbol="EURUSD", direction=Direction.BULLISH,
                     risk_pct=0.35) is RiskReject.KILL_SWITCH


# ------------------------------------------------- the minimum viable account


def test_small_accounts_are_rejected_rather_than_silently_under_risked(cfg):
    """The rejections are a property of equity, not of the setup — hence the sweep."""
    distances = [d * 0.0001 for d in range(8, 61)]
    rows = account_sweep(cfg, distances, [500, 1_000, 2_000, 5_000, 10_000, 50_000])
    acceptance = {r.equity: r.acceptance for r in rows}
    assert acceptance[500] < acceptance[10_000] <= 1.0
    assert acceptance[50_000] == 1.0
    # Every rejection at every equity is a lot-granularity one, never an under-risk one.
    assert all(r.under_risk == 0 for r in rows)
    assert math.isfinite(minimum_viable_equity(rows))


def test_risk_day_follows_the_configured_day_boundary(cfg):
    """SPEC 18.4: a 'daily' limit and a 'daily' bias must mean the same day."""
    from bot.core.risk import risk_day

    ny, _ = load_config(overrides={"tf": {"day_boundary_tz": "America/New_York"}})
    at = datetime(2026, 3, 3, 2, 0, tzinfo=UTC)   # 21:00 the previous day in New York
    assert risk_day(cfg, at).day == 3
    assert risk_day(ny, at).day == 2
