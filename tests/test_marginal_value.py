"""MSS vs CHoCH-not-MSS — the H5 falsification study (SPEC 6.9, BACKTEST_PROTOCOL §6.2).

H5 is falsified by a **negative** result, which makes this the one study in the project
whose main risk is a false negative: a wide confidence interval being written up as
"displacement is decoration".  Most of what is tested here is the machinery that stops
that happening — the three-way verdict, the power arithmetic, and the two controls.

The controls are the load-bearing part. A study that could not detect an effect it was
handed would pass every other test in this file, and a study whose intervals were too
narrow would manufacture one.  ``test_positive_control_*`` and
``test_null_calibration_*`` close both directions.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from bot.config.loader import load_config
from bot.core.bars import build_series
from bot.core.displacement import Direction
from bot.core.fvg import detect_fvgs
from bot.core.indicators import atr_ref
from bot.core.mss import analyse_mss
from bot.core.sessions import build_sessions
from bot.core.structure import analyse_structure
from bot.core.sweeps import analyse_sweeps
from bot.core.swings import detect_swings
from bot.data.resample import resample
from bot.data.synthetic import generate
from bot.research import marginal_value as MV

UTC = timezone.utc


@pytest.fixture(scope="module")
def run():
    """One synthetic year, taken all the way to CHoCH candidates."""
    cfg, _ = load_config()
    src = generate(
        "EURUSD",
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 12, 31, 23, 59, tzinfo=UTC),
        cfg,
        timeframe="M15",
        seed=41,
    )
    h4 = resample(src, "H4", cfg)
    d1 = resample(src, "D1", cfg)
    st = analyse_structure(h4, cfg)
    _, sw = analyse_sweeps(
        cfg=cfg,
        h4=h4,
        d1=d1,
        w1=resample(src, "W1", cfg),
        mn1=resample(src, "MN1", cfg),
        sessions=build_sessions(src, cfg),
        h4_structure=st,
        d1_swings=detect_swings(d1, cfg),
    )
    mss = analyse_mss(h4, cfg, sw.confirmed(), swings=st.swings, fvgs=detect_fvgs(h4, cfg))
    return cfg, h4, mss.candidates


@pytest.fixture(scope="module")
def study(run):
    cfg, h4, cands = run
    return MV.run_study(h4, cands, cfg, permutations=800, bootstrap=2000)


@pytest.fixture(scope="module")
def h1_groups(study):
    c = study.of("all")[0]
    return c.mss.returns, c.not_mss.returns


# ------------------------------------------------------------------ the controls


def test_positive_control_a_known_effect_is_detected(run):
    """A study that can only ever say "no difference" would pass every null test here.

    The effect is injected into the MSS group *through the full study path*, not into
    the comparison function, so the sample builders and the verdict logic are exercised
    too.
    """
    cfg, h4, cands = run
    shifted = MV.run_study(h4, cands, cfg, permutations=800, bootstrap=2000, mss_shift=0.8)
    assert shifted.verdict() is MV.Verdict.DIFFERENT
    assert "H5 SURVIVES" in shifted.headline()
    first = shifted.of("all")[0]
    assert first.verdict is MV.Verdict.DIFFERENT
    assert first.diff > 0.5
    assert first.ci_low > 0


def test_positive_control_agrees_with_the_power_arithmetic(h1_groups):
    """The MDE has to mean something operationally, not just be a number in a column.

    An effect comfortably above it must be found and one comfortably below it must not;
    if those disagreed, either the interval or the arithmetic would be wrong and the
    `required_mss_n` planning figures would be worthless.
    """
    mss, other = h1_groups
    mde = MV.minimum_detectable_effect(mss, other)
    assert 0.1 < mde < 1.0, "an implausible MDE makes the rest of this test vacuous"
    assert MV.detects_effect(mss, other, mde * 2.0)
    assert not MV.detects_effect(mss, other, mde * 0.3)


def test_null_calibration_false_positive_rate_lands_near_alpha(h1_groups):
    """Shuffling the MSS label makes the true effect exactly zero, so every DIFFERENT
    verdict is a false positive by construction.

    This is Phase 7's lesson turned into a calibration: an interval method that is too
    narrow shows up here and nowhere else. The bound is deliberately loose — 400 trials
    put roughly a 1.1-point standard error on the rate — but a materially broken CI
    would land far outside it.
    """
    mss, other = h1_groups
    fpr = MV.null_calibration(mss, other, trials=400, bootstrap=800, seed=7)
    assert 0.0 < fpr < 0.12, f"false-positive rate {fpr:.1%} against alpha {MV.ALPHA:.0%}"


def test_null_calibration_also_catches_an_interval_that_never_fires(h1_groups):
    """The other direction: a CI so wide it can never reach DIFFERENT would sail through
    the bound above, and would silently turn every result into UNDERPOWERED.

    Deliberately *not* asserted as "the bootstrap is anti-conservative here". The
    measured rate sits a point or so above alpha, which at 400 trials is well under two
    standard errors — reading a direction into it would be the same mistake as Phase 7's
    significance tests and Phase 8's "natural break" detector.
    """
    mss, other = h1_groups
    fpr = MV.null_calibration(mss, other, trials=400, bootstrap=800, seed=7)
    assert fpr > 0.0, "an interval that never excludes zero cannot detect anything either"


# -------------------------------------------------------------- the three verdicts


def _cmp(diff: float, lo: float, hi: float, n_mss: int = 40, margin: float = 0.25):
    rng = np.random.default_rng(0)
    a = rng.normal(size=n_mss)
    b = rng.normal(size=200)
    return MV.Comparison(
        horizon=1,
        sample="all",
        mss=MV.Group("MSS", a),
        not_mss=MV.Group("CHoCH-not-MSS", b),
        all_choch=MV.Group("all", np.concatenate([a, b])),
        diff=diff,
        ci_low=lo,
        ci_high=hi,
        p_value=0.5,
        mde=0.3,
        required_mss_n=100.0,
        margin=margin,
    )


def test_a_ci_excluding_zero_is_DIFFERENT():
    assert _cmp(0.4, 0.1, 0.7).verdict is MV.Verdict.DIFFERENT
    assert _cmp(-0.4, -0.7, -0.1).verdict is MV.Verdict.DIFFERENT


def test_a_ci_inside_the_margin_is_EQUIVALENT():
    """The only verdict that licenses "the requirement is decoration"."""
    c = _cmp(0.01, -0.2, 0.2)
    assert c.verdict is MV.Verdict.EQUIVALENT
    assert "H5 falsified" in c.describe()


def test_a_wide_ci_spanning_zero_is_UNDERPOWERED_not_EQUIVALENT():
    """The failure mode this whole module exists to prevent.

    A two-way verdict would call this "no difference" and hand back a null result on the
    methodology's central claim, when what actually happened is that the sample could not
    resolve anything.
    """
    c = _cmp(0.05, -0.9, 1.0)
    assert c.verdict is MV.Verdict.UNDERPOWERED
    assert c.verdict is not MV.Verdict.EQUIVALENT
    assert "cannot resolve" in c.describe()


def test_the_headline_refuses_to_call_an_undecided_study_a_null_result():
    s = MV.MarginalValueStudy(horizons=(1,))
    s.comparisons = [_cmp(0.05, -0.9, 1.0)]
    assert s.verdict() is MV.Verdict.UNDERPOWERED
    assert "NOT a null result" in s.headline()
    assert "FALSIFIED" not in s.headline()


def test_equivalence_needs_every_horizon_not_just_one():
    """Falsifying H5 on one horizon while the others could not resolve anything is not a
    finding, so EQUIVALENT is only reported when every horizon agrees."""
    s = MV.MarginalValueStudy(horizons=(1, 4))
    s.comparisons = [_cmp(0.0, -0.1, 0.1), _cmp(0.05, -0.9, 1.0)]
    assert s.verdict() is MV.Verdict.UNDERPOWERED

    s.comparisons = [_cmp(0.0, -0.1, 0.1), _cmp(0.02, -0.15, 0.2)]
    assert s.verdict() is MV.Verdict.EQUIVALENT
    assert "FALSIFIED" in s.headline()


def test_one_different_horizon_keeps_h5_alive():
    s = MV.MarginalValueStudy(horizons=(1, 4))
    s.comparisons = [_cmp(0.4, 0.1, 0.7), _cmp(0.05, -0.9, 1.0)]
    assert s.verdict() is MV.Verdict.DIFFERENT


# ------------------------------------------------------------- power arithmetic


def test_required_n_scales_as_the_inverse_square_of_the_margin(h1_groups):
    mss, other = h1_groups
    a = MV.required_n(mss, other, 0.25)
    b = MV.required_n(mss, other, 0.50)
    assert a == pytest.approx(4 * b, rel=1e-9)


def test_mde_shrinks_as_the_sample_grows():
    rng = np.random.default_rng(3)
    other = rng.normal(size=500)
    small = MV.minimum_detectable_effect(rng.normal(size=25), other)
    large = MV.minimum_detectable_effect(rng.normal(size=400), other)
    assert large < small


def test_degenerate_inputs_return_nan_rather_than_raising():
    empty = np.asarray([])
    one = np.asarray([0.5])
    assert np.isnan(MV.minimum_detectable_effect(one, empty))
    assert np.isnan(MV.required_n(one, empty))
    assert np.isnan(MV._permutation_p(one, empty, 10, np.random.default_rng(0)))
    assert np.isnan(MV.null_calibration(one, empty))


# ------------------------------------------------------------ forward returns


def _flat(n: int, closes: list[float]):
    t = np.arange(n, dtype=np.int64) * 14400
    c = np.asarray(closes, dtype=np.float64)
    return build_series(
        "X", "H4", t, t + 14400, c.copy(), c + 0.002, c - 0.002, c, np.ones(n)
    )


def test_forward_return_is_signed_by_setup_direction():
    """A bearish setup that falls has a *positive* signed return; without the sign the
    two directions cancel and every mean lands near zero whatever happens."""
    closes = [1.10] * 20 + [1.10, 1.12]
    s = _flat(22, closes)
    atr = np.full(s.n, 0.01)
    up = MV.signed_forward_return(s, 20, Direction.BULLISH, 1, atr)
    down = MV.signed_forward_return(s, 20, Direction.BEARISH, 1, atr)
    assert up == pytest.approx(2.0)
    assert down == pytest.approx(-2.0)


def test_forward_return_is_censored_at_the_end_of_the_series():
    s = _flat(22, [1.10] * 22)
    atr = np.full(s.n, 0.01)
    assert MV.signed_forward_return(s, 21, Direction.BULLISH, 1, atr) is None
    assert MV.signed_forward_return(s, 10, Direction.BULLISH, 12, atr) is None
    assert MV.signed_forward_return(s, 10, Direction.BULLISH, 5, atr) is not None


def test_forward_return_skips_atr_warmup():
    s = _flat(22, [1.10] * 22)
    atr = np.full(s.n, np.nan)
    assert MV.signed_forward_return(s, 5, Direction.BULLISH, 1, atr) is None


# ------------------------------------------------------------- sample builders


def test_only_choch_candidates_enter_either_population(run):
    """SPEC 6.9 compares CHoCH against CHoCH. A candidate that never broke its reference
    belongs to neither group, and folding the funnel's rejections into the comparison
    would be measuring a different question."""
    cfg, h4, cands = run
    atr = atr_ref(h4, cfg.atr.period)
    events = MV.events_from(h4, cands, atr)
    bars = {c.choch_bar for c in cands if c.is_choch}
    assert {e.bar for e in events} <= bars
    assert events


def test_candidates_sharing_a_bar_and_direction_are_one_observation(run):
    """The forward return is a function of (bar, direction) and nothing else, so two
    such candidates contribute the identical number twice.

    On this fixture that halves the sample: leaving it in makes every interval about
    sqrt(2) too narrow and understates every required-sample figure by two.
    """
    cfg, h4, cands = run
    events = MV.events_from(h4, cands, atr_ref(h4, cfg.atr.period))
    keys = [(e.bar, e.direction) for e in events]
    assert len(keys) == len(set(keys))
    raw = sum(1 for c in cands if c.is_choch)
    assert len(events) < raw, "no duplicates on this fixture would make the rule untestable"


def test_a_bar_carrying_both_labels_resolves_to_MSS(run):
    """The dedup must not average the two labels away.

    A bar where an MSS and a CHoCH-not-MSS candidate break together would otherwise put
    one identical return into *both* groups, dragging their means together and biasing
    the study toward EQUIVALENT -- the verdict that declares the methodology decoration.
    A bug in the other direction would have been tolerable; this one is not.
    """
    cfg, h4, cands = run
    events = MV.events_from(h4, cands, atr_ref(h4, cfg.atr.period))
    by_key: dict[tuple[int, object], list[bool]] = {}
    for c in cands:
        if c.is_choch:
            by_key.setdefault((c.choch_bar, c.direction), []).append(c.is_mss)
    mixed = [k for k, v in by_key.items() if any(v) and not all(v)]
    assert mixed, "no mixed bar on this fixture would make the rule untestable"
    labelled = {(e.bar, e.direction): e.is_mss for e in events}
    for k in mixed:
        assert labelled[k] is True


def test_non_overlapping_leaves_no_two_events_inside_one_window(run):
    cfg, h4, cands = run
    events = MV.events_from(h4, cands, atr_ref(h4, cfg.atr.period))
    for horizon in (1, 4, 12):
        kept = MV.non_overlapping(events, horizon)
        bars = [e.bar for e in kept]
        assert bars == sorted(bars)
        assert all(b - a >= horizon for a, b in zip(bars, bars[1:]))
        assert len(kept) <= len(events)


def test_non_overlapping_is_deterministic_and_seed_free(run):
    cfg, h4, cands = run
    events = MV.events_from(h4, cands, atr_ref(h4, cfg.atr.period))
    a = [e.bar for e in MV.non_overlapping(events, 12)]
    b = [e.bar for e in MV.non_overlapping(list(reversed(events)), 12)]
    assert a == b


def test_overlap_is_material_on_the_fixture(study):
    """The non-overlapping sample has to be worth reporting, or it is ceremony."""
    assert study.overlap_share > 0.3


def test_stratified_keeps_only_cells_holding_both_groups(run):
    cfg, h4, cands = run
    events = MV.events_from(h4, cands, atr_ref(h4, cfg.atr.period))
    kept = MV.stratified_pairs(events)
    cells: dict[tuple[int, int], list] = {}
    for e in kept:
        cells.setdefault((e.hour, e.tercile), []).append(e)
    assert cells
    for group in cells.values():
        assert any(e.is_mss for e in group)
        assert any(not e.is_mss for e in group)
    assert all(e.tercile >= 0 for e in kept)


# ------------------------------------------------- multiple testing (Phase 7's lesson)


def test_benjamini_hochberg_never_reports_a_smaller_p_than_the_raw_one():
    raw = [0.001, 0.02, 0.04, 0.3, 0.8]
    adj = MV.benjamini_hochberg(raw)
    assert all(a >= r - 1e-12 for a, r in zip(adj, raw))
    assert all(a <= 1.0 for a in adj)


def test_benjamini_hochberg_actually_inflates_by_the_known_amount():
    """The two properties above are both satisfied by returning the input unchanged, so
    without an exact expectation a no-op correction passes the suite."""
    raw = [0.001, 0.02, 0.04, 0.3, 0.8]
    #  p * m / rank, then a running minimum taken from the largest rank downward.
    expected = [0.005, 0.05, 0.04 * 5 / 3, 0.375, 0.8]
    got = MV.benjamini_hochberg(raw)
    assert got == pytest.approx(expected, rel=1e-9)
    assert any(g > r + 1e-9 for g, r in zip(got, raw))


def test_benjamini_hochberg_is_monotone_in_the_ranking():
    raw = [0.001, 0.02, 0.04, 0.3, 0.8]
    adj = MV.benjamini_hochberg(raw)
    order = np.argsort(raw)
    ordered = [adj[i] for i in order]
    assert ordered == sorted(ordered)


def test_benjamini_hochberg_handles_nan_and_empty():
    assert MV.benjamini_hochberg([]) == []
    out = MV.benjamini_hochberg([float("nan"), 0.01])
    assert np.isnan(out[0]) and out[1] == pytest.approx(0.01)


def test_every_comparison_carries_a_corrected_p_value(study):
    """Three horizons on one population is three chances to find something, and Phase 7
    is this project's standing evidence that those chances get taken."""
    for c in study.comparisons:
        assert c.p_adjusted is not None
        if np.isfinite(c.p_value):
            assert c.p_adjusted >= c.p_value - 1e-12


# --------------------------------------------------------------- study assembly


def test_all_three_samples_are_run_at_every_horizon(study):
    for sample in ("all", "non_overlapping", "stratified"):
        assert [c.horizon for c in study.of(sample)] == list(MV.DEFAULT_HORIZONS)


def test_all_choch_is_exactly_the_union_of_the_two_groups(study):
    """SPEC 6.9's population (a). It is not independent of (b) and (c), and the report
    must not read it as a third piece of evidence."""
    for c in study.comparisons:
        assert c.all_choch.n == c.mss.n + c.not_mss.n


def test_pooling_combines_returns_not_verdicts(run):
    cfg, h4, cands = run
    a = MV.run_study(h4, cands, cfg, permutations=200, bootstrap=500)
    pooled = MV.pool_studies([a, a])
    assert pooled.n_mss == 2 * a.n_mss
    first_a = a.of("all")[0]
    first_p = pooled.of("all")[0]
    assert first_p.mss.n == 2 * first_a.mss.n
    # Duplicating a sample cannot move the mean, but it must shrink the interval.
    assert first_p.diff == pytest.approx(first_a.diff, abs=1e-9)
    assert (first_p.ci_high - first_p.ci_low) < (first_a.ci_high - first_a.ci_low)


def test_pool_of_nothing_is_empty_not_an_exception():
    assert MV.pool_studies([]).comparisons == []


def test_study_is_deterministic(run):
    cfg, h4, cands = run
    a = MV.run_study(h4, cands, cfg, permutations=200, bootstrap=500)
    b = MV.run_study(h4, cands, cfg, permutations=200, bootstrap=500)
    assert [c.verdict for c in a.comparisons] == [c.verdict for c in b.comparisons]
    assert [c.ci_low for c in a.comparisons] == [c.ci_low for c in b.comparisons]
    assert a.headline() == b.headline()


def test_the_fixture_cannot_resolve_the_margin_at_the_long_horizons(study):
    """The honest state of the result on synthetic data, pinned so that a future change
    which quietly turns UNDECIDED into a null result is caught."""
    assert study.verdict() is not MV.Verdict.NO_DATA
    long_rows = [c for c in study.of("all") if c.horizon == 12]
    assert long_rows[0].verdict is MV.Verdict.UNDERPOWERED
    assert long_rows[0].required_mss_n > study.n_mss
