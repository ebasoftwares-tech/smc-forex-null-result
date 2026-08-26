"""Shared statistical primitives (`bot/research/stats.py`).

These are exercised indirectly by every study, but the ones tested here are the ones
whose *arithmetic* is easy to get subtly wrong in a way no study would surface: an
interval that quietly extends below zero, a power figure that does not scale, a
correction that is really the identity.
"""

from __future__ import annotations

import numpy as np
import pytest

from bot.research import stats as S


# ------------------------------------------------------------ Wilson interval


def test_the_calibration_interval_contains_the_rate_it_describes():
    for rate, trials in ((0.05, 400), (0.02, 200), (0.12, 1000)):
        lo, hi = S.calibration_interval(rate, trials)
        assert lo <= rate <= hi


def test_the_calibration_interval_never_goes_below_zero():
    """Wilson rather than the normal approximation, and this is why.

    At rates near alpha with a few hundred trials the normal interval reaches below zero,
    which is not a probability and reads as a broken calibration rather than a small one.
    """
    lo, hi = S.calibration_interval(0.005, 200)
    normal_lo = 0.005 - S._Z_ALPHA * np.sqrt(0.005 * 0.995 / 200)
    assert normal_lo < 0.0, "the case this guards against must actually arise"
    assert lo >= 0.0
    assert hi <= 1.0


def test_the_calibration_interval_narrows_as_trials_grow():
    width = lambda n: (lambda t: t[1] - t[0])(S.calibration_interval(0.05, n))
    assert width(2000) < width(400) < width(100)


def test_a_few_hundred_trials_cannot_settle_calibration():
    """The observation that motivated both the interval and a 3,000-trial default.

    Three draws of the same Phase 11 calibration read 4.8%, 8.0% and 5.5%. The two
    extremes are mutually compatible -- their intervals overlap -- yet they disagree
    about whether alpha is inside, which is precisely the question the calibration
    exists to answer. A few hundred shuffles cannot settle it, and quoting the point
    estimate alone hides that. See D-012 §4.
    """
    lo_a, hi_a = S.calibration_interval(0.080, 300)
    lo_b, hi_b = S.calibration_interval(0.048, 400)
    assert lo_a <= hi_b and lo_b <= hi_a, "the two draws must be mutually compatible"
    assert not (lo_a <= S.ALPHA <= hi_a), "the 8.0% draw excludes alpha"
    assert lo_b <= S.ALPHA <= hi_b, "the 4.8% draw contains it"

    # At 3,000 the interval is tight enough for the answer to mean something.
    lo, hi = S.calibration_interval(0.05, 3000)
    assert (hi - lo) < 0.02


def test_degenerate_calibration_inputs_are_nan_not_an_exception():
    assert all(np.isnan(v) for v in S.calibration_interval(float("nan"), 100))
    assert all(np.isnan(v) for v in S.calibration_interval(0.05, 0))
    assert np.isnan(S.calibration_sigma(0.05, 0))


def test_calibration_sigma_is_zero_at_alpha_and_grows_away_from_it():
    assert S.calibration_sigma(S.ALPHA, 400) == pytest.approx(0.0)
    assert S.calibration_sigma(0.08, 400) > S.calibration_sigma(0.06, 400) > 0


# ---------------------------------------------------------------- power maths


def test_required_n_scales_as_the_inverse_square_of_the_margin():
    rng = np.random.default_rng(1)
    a, b = rng.normal(size=60), rng.normal(size=400)
    assert S.required_n(a, b, 0.25) == pytest.approx(4 * S.required_n(a, b, 0.5), rel=1e-9)


def test_mde_and_required_n_agree_with_each_other():
    """Both come from the same standard error, so a sample at ``required_n`` should have
    an MDE equal to the margin it was computed for. If they disagreed, one of them is
    wrong and every planning figure built on them is fiction."""
    rng = np.random.default_rng(2)
    a, b = rng.normal(size=100), rng.normal(size=100)
    margin = 0.4
    n = S.required_n(a, b, margin)
    scaled_a = rng.normal(size=int(round(n)))
    scaled_b = rng.normal(size=int(round(n)))
    assert S.minimum_detectable_effect(scaled_a, scaled_b) == pytest.approx(margin, rel=0.15)


# -------------------------------------------------------------- the verdict


def test_the_three_way_verdict_covers_every_case():
    assert S.verdict_for(0.1, 0.7, 0.25, 50, 50) is S.Verdict.DIFFERENT
    assert S.verdict_for(-0.7, -0.1, 0.25, 50, 50) is S.Verdict.DIFFERENT
    assert S.verdict_for(-0.2, 0.2, 0.25, 50, 50) is S.Verdict.EQUIVALENT
    assert S.verdict_for(-0.9, 1.0, 0.25, 50, 50) is S.Verdict.UNDERPOWERED
    assert S.verdict_for(float("nan"), float("nan"), 0.25, 50, 50) is S.Verdict.NO_DATA
    assert S.verdict_for(-0.2, 0.2, 0.25, 1, 50) is S.Verdict.NO_DATA


def test_a_wider_margin_can_turn_underpowered_into_equivalent():
    """Which is exactly why the margin is declared before results are read (§10.2)."""
    assert S.verdict_for(-0.4, 0.4, 0.25, 50, 50) is S.Verdict.UNDERPOWERED
    assert S.verdict_for(-0.4, 0.4, 0.50, 50, 50) is S.Verdict.EQUIVALENT
