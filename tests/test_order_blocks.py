"""Order Blocks (SPEC 13) and the definition bake-off (13.8).

Phase 11's deliverable is a *comparison between rules*, not a rule, so most of what is
tested here is the comparison machinery: the agreement matrix, the effective-test count
that feeds the multiple-testing correction, and the anchoring that makes the correlation
mean what it claims.

The hand-built series pin each of the four definitions to the bar it should pick. That
matters more than usual here — SPEC 13.1 says outright that different choices produce
zones tens of pips apart, so "roughly the right area" is not a passing standard.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from bot.config.loader import load_config
from bot.core.bars import build_series
from bot.core.displacement import Direction, leg_origin
from bot.core.fvg import detect_fvgs
from bot.core.indicators import atr_ref
from bot.core.mss import analyse_mss
from bot.core.order_blocks import (
    ObDefinition,
    ObReject,
    ObStatus,
    OrderBlock,
    propose,
    track_order_blocks,
    zone_for,
)
from bot.core.sessions import build_sessions
from bot.core.structure import analyse_structure
from bot.core.sweeps import analyse_sweeps
from bot.core.swings import detect_swings
from bot.data.resample import resample
from bot.data.synthetic import generate
from bot.research import ob_study as OB
from bot.research.stats import Verdict, detects_effect, null_calibration

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


#: SPEC 13.6's worked example, laid out so each definition has a distinguishable answer.
#: bar 20 (s)  : down bar, the sweep extreme
#: bar 21 (a)  : first displacement bar, up
#: bar 22 (b)  : break bar, up
SPEC_13_6 = [
    (1.08300, 1.08340, 1.08150, 1.08210),  # 20  s: down bar, lowest low
    (1.08215, 1.08480, 1.08200, 1.08460),  # 21  a: first displacement bar
    (1.08460, 1.08790, 1.08440, 1.08760),  # 22  b: break bar
]
S_BAR, A_BAR, B_BAR = WARM, WARM + 1, WARM + 2
REFERENCE = 1.08600


def ask(cfg, definition, tail=None, *, reference=REFERENCE, displaced=True, swings=()):
    s = make(SPEC_13_6 if tail is None else tail)
    return s, propose(
        s,
        cfg,
        direction=Direction.BULLISH,
        sweep_extreme_bar=S_BAR,
        leg_start=A_BAR,
        break_bar=B_BAR,
        reference_price=reference,
        displacement_confirmed=displaced,
        definition=definition,
        swings=swings,
        seq=1,
    )


# ------------------------------------------------- SPEC 13.2 the four definitions


def test_spec_13_6_worked_example_ob_a(cfg):
    """OB-A: the last down bar before the leg origin is bar `s`, zone [L, H]."""
    _, p = ask(cfg, ObDefinition.A_LAST_OPPOSING)
    assert p.ok
    assert p.ob.origin_index == S_BAR
    assert p.ob.zone_low == pytest.approx(1.08150)
    assert p.ob.zone_high == pytest.approx(1.08340)
    assert p.ob.proximal == pytest.approx(1.08340)  # SPEC 13.6's stated proximal
    assert p.ob.ce == pytest.approx(1.08245)  # and its stated CE


def test_spec_13_6_worked_example_ob_c_coincides(cfg):
    """OB-C picks the lowest low in [s, a], which SPEC 13.6 notes is usually the same bar.

    *"They frequently coincide, which is itself worth measuring: if OB-A and OB-C select
    the same bar 80% of the time, they are not two hypotheses."*
    """
    _, a = ask(cfg, ObDefinition.A_LAST_OPPOSING)
    _, c = ask(cfg, ObDefinition.C_EXTREME_ORIGIN)
    assert c.ok and c.ob.origin_index == S_BAR
    assert c.ob.origin_index == a.ob.origin_index


def test_ob_b_differs_when_the_leg_contains_an_opposing_bar(cfg):
    """OB-A and OB-B are the same rule with different right-hand bounds, so they can only
    disagree when the displacement leg itself holds an opposing bar."""
    tail = [
        (1.08300, 1.08340, 1.08150, 1.08210),  # 20  s: down
        (1.08215, 1.08480, 1.08200, 1.08460),  # 21  a: up
        (1.08460, 1.08500, 1.08380, 1.08400),  # 22  a red bar INSIDE the leg
        (1.08400, 1.08790, 1.08390, 1.08760),  # 23  break bar
    ]
    s = make(tail)
    common = dict(
        direction=Direction.BULLISH, sweep_extreme_bar=S_BAR, leg_start=A_BAR,
        break_bar=WARM + 3, reference_price=1.08790, displacement_confirmed=True, seq=1,
    )
    a = propose(s, cfg, definition=ObDefinition.A_LAST_OPPOSING, **common)
    b = propose(s, cfg, definition=ObDefinition.B_LAST_DOWN_CLOSE_BEFORE_BREAK, **common)
    assert a.ok and b.ok
    assert a.ob.origin_index == S_BAR
    assert b.ob.origin_index == WARM + 2  # the red bar inside the leg
    assert a.ob.origin_index != b.ob.origin_index


def test_a_doji_is_not_opposing(cfg):
    """SPEC 13.7: requires strict ``C < O`` for the bullish case."""
    tail = list(SPEC_13_6)
    tail[0] = (1.08300, 1.08340, 1.08150, 1.08300)  # C == O
    _, p = ask(cfg, ObDefinition.A_LAST_OPPOSING, tail=tail)
    # The warm-up bars are also dojis (O == C == MID), so nothing qualifies at all.
    assert not p.ok
    assert p.reason is ObReject.NO_OPPOSING_BAR


def test_the_bearish_mirror_is_real_code(cfg):
    tail = [
        (1.07400, 1.07550, 1.07360, 1.07500),  # s: up bar, highest high
        (1.07490, 1.07500, 1.07220, 1.07240),  # a: first displacement bar, down
        (1.07240, 1.07260, 1.06900, 1.06940),  # b: break bar
    ]
    s = make(tail)
    p = propose(
        s, cfg, direction=Direction.BEARISH, sweep_extreme_bar=S_BAR, leg_start=A_BAR,
        break_bar=B_BAR, reference_price=1.07100, displacement_confirmed=True,
        definition=ObDefinition.A_LAST_OPPOSING, seq=1,
    )
    assert p.ok
    assert p.ob.origin_index == S_BAR
    assert p.ob.proximal == pytest.approx(1.07360)  # the LOW, for a bearish OB
    assert p.ob.distal == pytest.approx(1.07550)


# ------------------------------------------------------------ SPEC 13.3 the zone


def test_the_three_zone_modes_give_three_different_zones(cfg):
    s = make(SPEC_13_6)
    zones = {
        m: zone_for(s, S_BAR, Direction.BULLISH, m)
        for m in ("full_range", "body", "wick_to_open")
    }
    assert zones["full_range"] == (pytest.approx(1.08150), pytest.approx(1.08340))
    assert zones["body"] == (pytest.approx(1.08210), pytest.approx(1.08300))
    assert zones["wick_to_open"] == (pytest.approx(1.08150), pytest.approx(1.08300))
    assert len(set(zones.values())) == 3


def test_unknown_zone_mode_raises(cfg):
    s = make(SPEC_13_6)
    with pytest.raises(ValueError, match="unknown ob.zone_mode"):
        zone_for(s, S_BAR, Direction.BULLISH, "midpoint")


# ------------------------------------------------ SPEC 13.4 the five constraints


def test_no_displacement_no_order_block(cfg):
    """Constraint 1, and the one that stops OB-A being trivial.

    Without it the rule degenerates into "the last red candle", which on any chart is
    never more than a few bars away and therefore always exists -- every definition would
    report a near-100% hit rate and the bake-off would measure nothing.
    """
    _, p = ask(cfg, ObDefinition.A_LAST_OPPOSING, displaced=False)
    assert not p.ok
    assert p.reason is ObReject.NO_DISPLACEMENT


def test_a_reference_too_far_away_is_rejected(cfg):
    tight, _ = load_config(overrides={"ob": {"max_distance_atr": 0.01}})
    _, p = ask(tight, ObDefinition.A_LAST_OPPOSING)
    assert not p.ok
    assert p.reason is ObReject.TOO_FAR


def test_an_order_block_older_than_max_age_is_rejected(cfg):
    narrow, _ = load_config(overrides={"ob": {"max_age_bars": 1}})
    _, p = ask(narrow, ObDefinition.A_LAST_OPPOSING)
    assert not p.ok
    assert p.reason is ObReject.TOO_OLD


def test_a_zone_above_the_reference_is_rejected(cfg):
    """SPEC 13.7: entering above the level whose break defined the setup means the
    "retracement" entry is not a retracement."""
    _, p = ask(cfg, ObDefinition.A_LAST_OPPOSING, reference=1.08200)
    assert not p.ok
    assert p.reason is ObReject.ABOVE_REFERENCE


def test_the_lookback_bound_is_enforced(cfg):
    short, _ = load_config(overrides={"ob": {"max_lookback_bars": 1}})
    tail = [
        (1.08300, 1.08340, 1.08150, 1.08210),  # 20  s: the only down bar
        (1.08215, 1.08480, 1.08200, 1.08460),  # 21
        (1.08460, 1.08500, 1.08440, 1.08490),  # 22  up bar
        (1.08490, 1.08790, 1.08480, 1.08760),  # 23  break bar
    ]
    s = make(tail)
    p = propose(
        s, cfg, direction=Direction.BULLISH, sweep_extreme_bar=S_BAR, leg_start=WARM + 2,
        break_bar=WARM + 3, reference_price=REFERENCE, displacement_confirmed=True,
        definition=ObDefinition.A_LAST_OPPOSING, seq=1,
    )
    assert p.ok and p.ob.origin_index == S_BAR
    p2 = propose(
        s, short, direction=Direction.BULLISH, sweep_extreme_bar=S_BAR, leg_start=WARM + 2,
        break_bar=WARM + 3, reference_price=REFERENCE, displacement_confirmed=True,
        definition=ObDefinition.A_LAST_OPPOSING, seq=1,
    )
    assert not p2.ok and p2.reason is ObReject.NO_OPPOSING_BAR


# --------------------------------------------------------- SPEC 13.5 the lifecycle


def _blk(cfg, tail, **kw):
    s, p = ask(cfg, ObDefinition.A_LAST_OPPOSING, tail=SPEC_13_6 + tail, **kw)
    assert p.ok
    book = track_order_blocks(s, cfg, [p.ob])
    return s, book, book.blocks[0]


def hold(price):
    return (price, price + 0.0002, price - 0.0002, price)


def test_a_block_nobody_returns_to_stays_unmitigated(cfg):
    _, _, b = _blk(cfg, [hold(1.08760)] * 4)
    assert b.status is ObStatus.UNMITIGATED
    assert b.first_touch_index is None


def test_a_touch_short_of_ce_is_partial(cfg):
    _, _, b = _blk(cfg, [(1.08760, 1.08770, 1.08320, 1.08700), hold(1.08700)])
    assert b.first_touch_index == B_BAR + 1
    assert b.status is ObStatus.PARTIAL
    assert not b.status.is_terminal


def test_reaching_ce_mitigates(cfg):
    _, _, b = _blk(cfg, [(1.08760, 1.08770, 1.08240, 1.08700), hold(1.08700)])
    assert b.status is ObStatus.MITIGATED
    assert b.mitigated_index == B_BAR + 1


def test_a_close_beyond_the_distal_edge_invalidates(cfg):
    """SPEC 13.5: *"A bullish OB whose low is closed through is invalid: the orders it
    represented have been run."*

    The bar gaps below the zone entirely, so it never traded inside -- otherwise
    mitigation would win the race, which is the trap D-011 §2 records for gaps.
    """
    below = (1.08000, 1.08050, 1.07900, 1.07950)
    _, _, b = _blk(cfg, [below, hold(1.07950)])
    assert b.first_touch_index is None
    assert b.status is ObStatus.INVALIDATED
    assert b.invalidated_index == B_BAR + 1


def test_invalidate_closes_requires_that_many_closes(cfg):
    """The count restarts when price comes back inside: SPEC 13.5 counts closes beyond
    the edge, and a bar back in the zone means the level held."""
    two, _ = load_config(overrides={"ob": {"invalidate_closes": 2}})
    below = (1.08000, 1.08050, 1.07900, 1.07950)
    _, _, one = _blk(two, [below, hold(1.08400)])
    assert one.status is not ObStatus.INVALIDATED
    _, _, twice = _blk(two, [below, below])
    assert twice.status is ObStatus.INVALIDATED


def test_a_block_expires(cfg):
    narrow, _ = load_config(overrides={"ob": {"max_age_bars": 4}})
    _, _, b = _blk(narrow, [hold(1.08760)] * 6)
    assert b.status is ObStatus.EXPIRED


def test_the_proposal_bar_cannot_mitigate_its_own_block(cfg):
    """The proposal bar is the break bar; letting the move that confirmed the setup fill
    its own order block would mitigate every one of them instantly."""
    _, _, b = _blk(cfg, [hold(1.08760)])
    assert b.first_touch_index is None or b.first_touch_index > B_BAR


def test_status_at_reports_what_was_known_then(cfg):
    _, _, b = _blk(cfg, [hold(1.08760), (1.08760, 1.08770, 1.08240, 1.08700)])
    assert b.status is ObStatus.MITIGATED
    assert b.status_at(B_BAR + 1) is ObStatus.UNMITIGATED
    assert b.is_available_at(B_BAR + 1)
    assert not b.is_available_at(b.mitigated_index)


def test_the_tracker_leaves_its_input_alone(cfg):
    s, p = ask(cfg, ObDefinition.A_LAST_OPPOSING, tail=SPEC_13_6 + [(1.08760, 1.08770, 1.08240, 1.08700)])
    assert p.ok
    book = track_order_blocks(s, cfg, [p.ob])
    assert p.ob.status is ObStatus.UNMITIGATED
    assert book.blocks[0].status is ObStatus.MITIGATED


# ---------------------------------------------- SPEC 13.8 the agreement machinery


def _row(offsets, ref_price=1.0800, ref_atr=0.0045):
    """A SetupProposals whose definitions propose entries at the given ATR offsets."""
    defs = list(ObDefinition)
    r = OB.SetupProposals("x", 100, Direction.BULLISH, ref_price=ref_price, ref_atr=ref_atr)
    for d, off in zip(defs, offsets):
        if off is None:
            r.proposals[d] = OB.ObProposal(d, None, ObReject.NO_OPPOSING_BAR)
            continue
        price = ref_price + off * ref_atr
        r.proposals[d] = OB.ObProposal(
            d,
            OrderBlock(
                id=f"ob-{d.value}", symbol="X", timeframe="H4", definition=d,
                direction=Direction.BULLISH, origin_index=int(abs(off * 1000)) % 97,
                zone_low=price - 0.0005, zone_high=price, zone_mode="full_range",
                formed_at=datetime(2026, 1, 1, tzinfo=UTC), proposed_index=100,
                size_atr=0.1,
            ),
        )
    return r


def test_agreement_counts_only_setups_where_both_produced_a_block(cfg):
    """A definition that declines to propose is not disagreeing about which bar; folding
    those in would make a rarely-firing definition look maximally independent."""
    defs = list(ObDefinition)
    rows = [_row([-1.0, -1.0, None, None]), _row([-2.0, -3.0, -2.0, None])]
    m = OB.agreement_matrix(rows, defs)
    n_ab, share_ab = m[(defs[0].value, defs[1].value)]
    assert n_ab == 2
    n_ac, _ = m[(defs[0].value, defs[2].value)]
    assert n_ac == 1  # only the second row has both
    n_ad, share_ad = m[(defs[0].value, defs[3].value)]
    assert n_ad == 0 and np.isnan(share_ad)


def test_effective_tests_is_exact_at_every_anchor():
    """1 for identical variants, k for independent ones, 2 for two identical pairs.

    Tolerance is 1e-6 rather than exact: ``eigvalsh`` on a matrix of ones returns a top
    eigenvalue of 3.999999996 rather than 4. That rounding is harmless for a continuous
    estimator and fatal for a discontinuous one -- see the next test.
    """
    assert OB.effective_tests(np.ones((4, 4))) == pytest.approx(1.0, abs=1e-6)
    assert OB.effective_tests(np.eye(4)) == pytest.approx(4.0, abs=1e-6)
    pairs = np.array([
        [1.0, 1.0, 0.0, 0.0],
        [1.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 1.0],
        [0.0, 0.0, 1.0, 1.0],
    ])
    assert OB.effective_tests(pairs) == pytest.approx(2.0, abs=1e-6)


def test_li_ji_is_discontinuous_where_this_study_lives():
    """Documented, and the reason Galwey is used instead.

    Li & Ji sums ``I(lambda>=1) + frac(lambda)``, which is discontinuous at integer
    eigenvalues. Analytically, four perfectly correlated variants give ``[4,0,0,0]`` and
    it returns 1 -- but **it never sees an exact 4**: ``eigvalsh`` on a matrix of ones
    returns 3.999999996, ``floor`` drops from 4 to 3, and the estimate is ~2.

    So the estimator is wrong by a whole test on the most redundant input possible, from
    floating-point noise alone, before any sampling noise is added. Near-identical
    variants are exactly this study's subject, which is why the reports use Galwey.
    """
    perfect = np.ones((4, 4))
    exact = np.array([4.0, 0.0, 0.0, 0.0])
    li_ji_exact = sum((1.0 if v >= 1 else 0.0) + (v - np.floor(v)) for v in exact)
    assert li_ji_exact == pytest.approx(1.0)  # the analytic answer

    assert OB.li_ji_effective_tests(perfect) > 1.9  # what it actually returns
    assert OB.effective_tests(perfect) == pytest.approx(1.0, abs=1e-6)  # Galwey is fine

    nudged = perfect * 0.999 + np.eye(4) * 0.001
    assert OB.effective_tests(nudged) < 1.2


def test_the_correlation_anchor_is_exogenous_to_the_variant_set():
    """The trap that produced a wrong matrix on the first attempt (D-012).

    Centring `k` variables by their own per-observation mean forces the deviations to sum
    to zero and pins the average pairwise correlation at ``-1/(k-1)``. Four *independent*
    variants must still read as four independent tests; under row-centring they read as
    three.
    """
    rng = np.random.default_rng(5)
    rows = [_row(list(rng.normal(size=4))) for _ in range(400)]
    corr, n = OB.price_correlations(rows, list(ObDefinition))
    assert n == 400
    off = corr[np.triu_indices(4, 1)]
    assert abs(off.mean()) < 0.15, f"anchor induced correlation: mean {off.mean():+.3f}"
    assert OB.effective_tests(corr) > 3.5


def test_identical_variants_read_as_one_test():
    rng = np.random.default_rng(6)
    rows = [_row([v, v, v, v]) for v in rng.normal(size=300)]
    corr, _ = OB.price_correlations(rows, list(ObDefinition))
    assert OB.effective_tests(corr) == pytest.approx(1.0, abs=0.05)


def test_correlation_needs_enough_complete_rows():
    corr, n = OB.price_correlations([_row([-1.0, -1.0, -1.0, -1.0])], list(ObDefinition))
    assert n == 1 and np.all(np.isnan(corr))
    assert np.isnan(OB.effective_tests(corr))


# ------------------------------------------------------- the standalone edge test


@pytest.fixture(scope="module")
def fixture_run():
    cfg, _ = load_config()
    src = generate(
        "EURUSD", datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 12, 31, 23, 59, tzinfo=UTC), cfg, timeframe="M15", seed=41,
    )
    h4 = resample(src, "H4", cfg)
    d1 = resample(src, "D1", cfg)
    st = analyse_structure(h4, cfg)
    _, sw = analyse_sweeps(
        cfg=cfg, h4=h4, d1=d1, w1=resample(src, "W1", cfg), mn1=resample(src, "MN1", cfg),
        sessions=build_sessions(src, cfg), h4_structure=st, d1_swings=detect_swings(d1, cfg),
    )
    res = analyse_mss(h4, cfg, sw.confirmed(), swings=st.swings, fvgs=detect_fvgs(h4, cfg))
    atr = atr_ref(h4, cfg.atr.period)
    setups = [c for c in res.candidates if c.is_choch]
    blocks = []
    for i, c in enumerate(setups):
        p = propose(
            h4, cfg, direction=c.direction, sweep_extreme_bar=c.sweep_extreme_bar,
            leg_start=leg_origin(c.sweep_extreme_bar, c.choch_bar, cfg),
            break_bar=c.choch_bar, reference_price=c.reference_price,
            displacement_confirmed=c.displacement.confirmed,
            definition=ObDefinition.A_LAST_OPPOSING, swings=st.swings.swings,
            atr=atr, seq=i,
        )
        if p.ok:
            blocks.append(p.ob)
    book = track_order_blocks(h4, cfg, blocks)
    return cfg, h4, book, OB.run_edge_study(h4, book, cfg, ObDefinition.A_LAST_OPPOSING,
                                            permutations=600, bootstrap=1500)


def test_the_fixture_produces_blocks_and_touches(fixture_run):
    _, _, book, st = fixture_run
    assert book.blocks
    assert st.n_touches > 10
    assert {"MITIGATED", "EXPIRED"} & set(book.by_status())


def test_positive_control_detects_an_injected_effect(fixture_run):
    cfg, h4, book, _ = fixture_run
    shifted = OB.run_edge_study(
        h4, book, cfg, ObDefinition.A_LAST_OPPOSING,
        permutations=600, bootstrap=1500, touch_shift=1.0,
    )
    assert shifted.verdict() is Verdict.DIFFERENT


def test_null_calibration_lands_in_a_plausible_band(fixture_run):
    _, _, _, st = fixture_run
    r = st.results[0]
    fpr = null_calibration(r.touch.returns, r.control.returns, trials=200, bootstrap=500)
    assert 0.0 < fpr < 0.15


def test_touches_on_one_bar_and_direction_are_one_observation(fixture_run):
    _, _, book, st = fixture_run
    events, raw = OB.touch_bars(book)
    assert len(events) == len(set(events))
    assert raw >= len(events)
    assert st.n_touches == len(events)


def test_forward_returns_are_signed_and_censored(fixture_run):
    _, h4, _, _ = fixture_run
    atr = atr_ref(h4, load_config()[0].atr.period)
    up = OB.forward_returns(h4, [(100, 1)], 3, atr)
    down = OB.forward_returns(h4, [(100, -1)], 3, atr)
    assert up[0] == pytest.approx(-down[0])
    assert len(OB.forward_returns(h4, [(h4.n - 1, 1)], 1, atr)) == 0


def test_multiple_testing_correction_is_applied(fixture_run):
    _, _, _, st = fixture_run
    for r in st.results:
        assert r.p_adjusted is not None
        if np.isfinite(r.p_value):
            assert r.p_adjusted >= r.p_value - 1e-12


def test_the_edge_study_is_deterministic(fixture_run):
    cfg, h4, book, _ = fixture_run
    a = OB.run_edge_study(h4, book, cfg, ObDefinition.A_LAST_OPPOSING, permutations=200, bootstrap=400)
    b = OB.run_edge_study(h4, book, cfg, ObDefinition.A_LAST_OPPOSING, permutations=200, bootstrap=400)
    assert [r.ci_low for r in a.results] == [r.ci_low for r in b.results]


def test_fill_curve_is_monotone_and_censored(fixture_run):
    _, h4, book, _ = fixture_run
    curve = book.fill_curve((1, 3, 5, 10, 30), h4.n)
    vals = [curve[k] for k in sorted(curve)]
    assert all(b >= a - 1e-12 for a, b in zip(vals, vals[1:]))
    assert all(0.0 <= v <= 1.0 for v in vals)


def test_pool_of_nothing_is_empty():
    assert OB.pool_edge_studies([]).results == []
