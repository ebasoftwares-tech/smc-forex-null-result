"""Monte Carlo robustness (BACKTEST_PROTOCOL section 9).

Each test here is a **positive control**: a constructed sequence with a known defect, and
the assertion that the matching test catches it. A resampling suite that only ever says
"passed" on real data is indistinguishable from one that always says "passed", which is
the failure mode STATE.md section 7 keeps recording in other forms.

The interesting result is that one of them does *not* catch what the protocol says it
uniquely catches, and the gap is narrow and specific — see
``test_the_skip_test_alone_misses_a_result_carried_by_three_of_sixty``.
"""

from __future__ import annotations

import numpy as np
import pytest

from bot.backtest import montecarlo as MC

RISK = 0.35


def diffuse(n=300, mean=0.15, seed=0):
    return list(np.random.default_rng(seed).normal(mean, 1.0, n))


# ------------------------------------------------------------- determinism


def test_every_test_is_reproducible_from_its_seed():
    """SPEC 25.5 permits seeded randomness here and nowhere else.

    A Monte Carlo result that moved between runs could not be distinguished from one that
    moved because the strategy changed.
    """
    r = diffuse()
    a = MC.bootstrap_expectancy(r, n=500, seed=7)
    b = MC.bootstrap_expectancy(r, n=500, seed=7)
    c = MC.bootstrap_expectancy(r, n=500, seed=8)
    assert a.value == b.value
    assert a.value != c.value


# ------------------------------------------------------- the positive controls


def test_the_bootstrap_rejects_a_zero_edge_and_accepts_a_real_one():
    good = MC.bootstrap_expectancy(diffuse(n=400, mean=0.30), n=3_000)
    null = MC.bootstrap_expectancy(diffuse(n=400, mean=0.00, seed=3), n=3_000)
    assert good.passed is True
    assert null.passed is False


def test_the_shuffle_reports_a_drawdown_distribution_not_the_realised_one():
    """The realised ordering is one draw; quoting it alone reports luck as a property."""
    r = diffuse(n=200)
    out = MC.trade_order_shuffle(r, risk_pct=RISK, n=400)
    assert len(out) == 2
    dd, ruin = out
    assert dd.value > 0
    assert 0.0 <= ruin.value <= 1.0


def test_ruin_is_measured_on_a_compounding_path():
    """An additive path can never reach zero, so it would report ruin = 0 for everything."""
    ruinous = [-1.0] * 400
    out = MC.trade_order_shuffle(ruinous, risk_pct=50.0, n=200)
    ruin = next(o for o in out if "ruin" in o.statistic)
    assert ruin.value == 1.0 and ruin.passed is False


def test_the_skip_test_catches_a_result_carried_by_a_single_trade():
    """The protocol's stated purpose for it, and here it works."""
    carried = [-0.1] * 39 + [8.0]
    assert MC.skip_ten_percent(carried, risk_pct=RISK, n=800).passed is False
    assert MC.skip_ten_percent(diffuse(n=300), risk_pct=RISK, n=800).passed is True


def test_the_skip_test_alone_misses_a_result_carried_by_three_of_sixty():
    """D-015 section 4 — the gap in the protocol's most-emphasised test.

    Its acceptance is *"5th-percentile net return > 0"*, a **sign** test, while
    concentration shows up as a **drop**. Dropping 10% of 60 trades removes 6, so it
    rarely removes all three that carry the result and the sign survives.

    The companion statistic catches it, which is why it exists.
    """
    carried = [-0.1] * 57 + [5.0] * 3
    assert MC.skip_ten_percent(carried, risk_pct=RISK, n=2_000).passed is True

    share, degradation = MC.concentration(carried, risk_pct=RISK)
    assert share.value > 1.0
    assert share.passed is False, "the companion must catch what the sign test misses"
    # A share above 1.0 means the rest of the book loses money, which is the whole claim.
    assert sum(x for x in carried if x < 0) < 0


def test_concentration_passes_a_diffuse_edge():
    """The negative control: the companion must not flag an ordinary result."""
    share, degradation = MC.concentration(diffuse(n=300), risk_pct=RISK)
    assert share.passed is True
    assert 0.0 < share.value < 1.0


def test_concentration_gives_no_verdict_on_a_losing_book():
    """A share of a negative total is a negative number that sails past "< 1.0".

    The Phase 14 fixture reported -5.97 and "passed". Concentration is a question about
    how a *profit* is distributed, so on a losing book the honest output is no verdict.
    """
    losing = [-0.5] * 40 + [1.0] * 5
    assert sum(losing) < 0
    out = MC.concentration(losing, risk_pct=RISK)
    assert len(out) == 1
    assert out[0].passed is None
    assert not np.isfinite(out[0].value)


def test_randomised_costs_take_the_median_over_the_multiplier_grid():
    calls: list[float] = []

    def rerun(m: float) -> float:
        calls.append(m)
        return 0.30 - 0.10 * m

    out = MC.randomised_costs(rerun, n=100)
    assert out.passed is True
    # Each distinct multiplier is re-run once, not once per draw.
    assert len(set(calls)) == len(calls) <= 5

    hopeless = MC.randomised_costs(lambda m: 0.05 - 0.10 * m, n=100)
    assert hopeless.passed is False


def test_entry_timing_uses_the_worst_shift_not_the_mean():
    """Averaging a good shift against a bad one hides the fragility being tested."""
    ok = MC.entry_timing_shift(0.20, [0.18, 0.15])
    assert ok.passed is True
    fragile = MC.entry_timing_shift(0.20, [0.19, 0.02])
    assert fragile.passed is False
    assert fragile.value == pytest.approx((0.20 - 0.02) / 0.20)


def test_thin_samples_return_none_rather_than_a_verdict():
    """Three trades cannot support a robustness claim in either direction."""
    tiny = [0.5, -1.0, 2.0]
    assert MC.trade_order_shuffle(tiny, risk_pct=RISK) == []
    assert MC.concentration(tiny, risk_pct=RISK) == []
    assert MC.skip_ten_percent(tiny, risk_pct=RISK).passed is None
