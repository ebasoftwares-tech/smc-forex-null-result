"""The displacement threshold study (SPEC 10.6) — the Phase 8 gate."""

from __future__ import annotations

import numpy as np
import pytest

from bot.core.sessions import build_sessions
from bot.core.structure import analyse_structure
from bot.core.sweeps import analyse_sweeps
from bot.core.swings import detect_swings
from bot.research.displacement_study import (
    THRESHOLD_GRID,
    DisplacementStudy,
    LegSample,
    joint_ablation,
    run_study,
)
from bot.data.resample import resample


@pytest.fixture(scope="module")
def swept(cfg, m15_quarter):
    h4 = resample(m15_quarter, "H4", cfg)
    d1 = resample(m15_quarter, "D1", cfg)
    book, res = analyse_sweeps(
        cfg=cfg,
        h4=h4,
        d1=d1,
        w1=resample(m15_quarter, "W1", cfg),
        mn1=resample(m15_quarter, "MN1", cfg),
        sessions=build_sessions(m15_quarter, cfg),
        h4_structure=analyse_structure(h4, cfg),
        d1_swings=detect_swings(d1, cfg),
    )
    return h4, res.confirmed()


def _study_from(values: list[float]) -> DisplacementStudy:
    st = DisplacementStudy()
    st.samples = [
        LegSample(
            net_atr=v,
            body_ratio=0.5,
            dir_bars=1,
            fvg_count=1,
            leg_bars=3,
            bars_after_sweep=1,
            confirmed=True,
            reasons=(),
            spans_gap=False,
        )
        for v in values
    ]
    return st


# ---------------------------------------------------------------------- shape


def test_the_study_walks_every_candidate_break_bar(cfg, swept):
    h4, sweeps = swept
    st = run_study(h4, sweeps, cfg, window=12)
    assert st.n_sweeps == len(sweeps)
    assert len(st.samples) > 5 * len(sweeps), "each sweep contributes several candidate legs"
    assert all(1 <= s.bars_after_sweep <= 12 for s in st.samples)


def test_the_wait_is_enforced_in_the_study_too(cfg, swept):
    """SPEC 9.6: the bar that confirms a sweep can never also be the break bar."""
    h4, sweeps = swept
    st = run_study(h4, sweeps, cfg)
    assert min(s.bars_after_sweep for s in st.samples) >= 1


def test_leg_length_never_exceeds_the_configured_maximum(cfg, swept):
    h4, sweeps = swept
    st = run_study(h4, sweeps, cfg)
    assert max(s.leg_bars for s in st.samples) <= cfg.disp.max_leg_bars


# ----------------------------------------------------------- rejection rates


def test_rejection_rises_monotonically_with_the_threshold(cfg, swept):
    h4, sweeps = swept
    st = run_study(h4, sweeps, cfg)
    rates = st.rejection_by_threshold(THRESHOLD_GRID)
    vals = [rates[t] for t in sorted(rates)]
    assert vals == sorted(vals)
    assert rates[0.0] == 0.0, "a zero threshold is the filter switched off"
    assert rates[2.5] > rates[1.0]


def test_a_zero_threshold_rejects_nothing(cfg, swept):
    """SPEC 10.4: 'a threshold that never rejects anything is not a filter'.

    The off setting must therefore be visibly, exactly, zero.
    """
    h4, sweeps = swept
    st = run_study(h4, sweeps, cfg)
    assert st.rejection_by_threshold((0.0,))[0.0] == 0.0


def test_reasons_are_counted_independently_and_may_overlap(cfg, swept):
    h4, sweeps = swept
    st = run_study(h4, sweeps, cfg)
    by_reason = st.rejection_by_reason()
    assert by_reason
    # Overlapping by construction, so the total exceeds the failure rate.
    assert sum(by_reason.values()) > (1 - st.pass_rate())
    assert all(0.0 <= v <= 1.0 for v in by_reason.values())


# ---------------------------------------------------- SPEC 10.6's real question


def test_a_smoothly_decaying_distribution_is_called_arbitrary(cfg):
    """An exponential decay has no shoulder anywhere, so no cut in it is discovered."""
    rng = np.random.default_rng(3)
    st = _study_from(list(rng.exponential(0.8, size=8000)))
    assert not st.has_natural_break(1.5)
    assert st.unimodal_verdict(1.5).startswith("ARBITRARY")


def test_poisson_noise_does_not_count_as_structure(cfg):
    """The guard that was missing.

    Before it, a single bin rising by +11 counts against a standard deviation of 18 was
    reported as the data marking the threshold out.
    """
    rng = np.random.default_rng(11)
    for seed_shift in range(6):
        st = _study_from(list(rng.exponential(0.8, size=6000)))
        assert not st.has_natural_break(1.5), seed_shift


def test_a_genuinely_bimodal_distribution_is_called_structured(cfg):
    """The positive control: if the data really does mark a cut out, say so."""
    rng = np.random.default_rng(5)
    small = rng.normal(0.5, 0.15, size=4000)
    large = rng.normal(2.0, 0.15, size=4000)
    st = _study_from([abs(x) for x in np.concatenate([small, large])])
    assert st.has_natural_break(1.5, width=0.8)
    assert st.unimodal_verdict(1.5).startswith("STRUCTURED")


def test_an_insufficient_sample_says_so_rather_than_guessing(cfg):
    st = _study_from([1.0] * 40)
    assert st.unimodal_verdict(1.5) == "INSUFFICIENT SAMPLE"


# ------------------------------------------------------------ joint ablation


def test_joint_ablation_covers_both_fvg_settings(cfg, swept):
    """SPEC 10.6 requires them jointly because SPEC 10.2 argues they are near-substitutes."""
    h4, sweeps = swept
    ja = joint_ablation(h4, sweeps, cfg, grid=(1.0, 1.5, 2.0))
    assert set(ja) == {False, True}
    for require_fvg, row in ja.items():
        assert set(row) == {1.0, 1.5, 2.0}
        vals = [row[t] for t in sorted(row)]
        assert vals == sorted(vals, reverse=True), "a stricter threshold cannot pass more"
    # Requiring an FVG can only ever remove legs.
    for t in (1.0, 1.5, 2.0):
        assert ja[True][t] <= ja[False][t]


def test_empty_input_is_handled(cfg, swept):
    h4, _ = swept
    st = run_study(h4, [], cfg)
    assert st.samples == []
    assert st.pass_rate() == 0.0
    assert st.percentiles() == {}
    assert st.rejection_by_reason() == {}
