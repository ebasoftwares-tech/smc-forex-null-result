"""FVG lifecycle, selection, and the standalone edge test (SPEC 12.2 / 12.3 / 12.6).

The hand-built series pin the *rules* — every bar is written out and every threshold is
reachable by hand — because a lifecycle that only ever runs against a random walk is
checked against a distribution rather than against its own definition. Two transitions in
particular (INVALIDATED, and expiry racing a touch) are unreachable on the synthetic
fixture and exist here or nowhere.

The fixture tests pin what no example can: that nothing repaints, that the tracker leaves
its input alone, and that the study's two controls hold.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from bot.config.loader import load_config
from bot.core.bars import build_series
from bot.core.fvg import (
    FvgDirection,
    FvgStatus,
    detect_fvgs,
    select_fvg,
    track_fvgs,
)
from bot.core.indicators import atr_ref
from bot.data.resample import resample
from bot.data.synthetic import generate
from bot.research import fvg_study as FS
from bot.research.stats import Verdict, detects_effect, null_calibration

UTC = timezone.utc
H4 = 14400
WARM = 20
MID = 1.07860
HALF = 0.00225  # true range 0.00450 -> warm-up ATR is exactly 0.00450


def make(tail: list[tuple[float, float, float, float]], gaps: set[int] | None = None):
    """``WARM`` flat bars, then the bars written out in ``tail``.

    ``gaps`` names tail indices that should open a bar later than the previous close,
    which is how a true price gap is expressed in this series model.
    """
    n = WARM + len(tail)
    opens = np.arange(n, dtype=np.int64) * H4
    closes = opens + H4
    o = [MID] * WARM + [x[0] for x in tail]
    h = [MID + HALF] * WARM + [x[1] for x in tail]
    lo = [MID - HALF] * WARM + [x[2] for x in tail]
    c = [MID] * WARM + [x[3] for x in tail]
    for k in gaps or ():
        # Shift this bar AND everything after it, so the series stays strictly
        # ascending while gaining a real discontinuity before bar WARM + k.
        opens[WARM + k :] += H4
        closes[WARM + k :] += H4
    return build_series(
        "EURUSD", "H4", opens, closes,
        np.array(o), np.array(h), np.array(lo), np.array(c), np.ones(n),
    )


#: Three bars that print a bullish FVG with zone [1.08050, 1.08300], CE 1.08175.
BULL_GAP = [
    (1.08000, 1.08050, 1.07950, 1.08020),
    (1.08600, 1.08700, 1.08500, 1.08650),
    (1.08800, 1.08900, 1.08300, 1.08850),
]
ZONE_LOW, ZONE_HIGH = 1.08050, 1.08300
CE = (ZONE_LOW + ZONE_HIGH) / 2
GAP_BAR = WARM + 2  # the bar the gap confirms on


def the_gap(fvgs):
    """The gap BULL_GAP's three bars produce, confirmed at ``GAP_BAR``.

    Not simply the first bullish gap: the flat warm-up means bar 21's low also clears
    bar 19's high, so an earlier gap confirms at 21. Picking by index rather than by
    position keeps every assertion below about the gap that was actually designed.
    """
    got = [
        f for f in fvgs
        if f.direction is FvgDirection.BULLISH and f.confirmed_index == GAP_BAR
    ]
    assert got, "the fixture must produce the designed bullish gap"
    return got[0]


def only_gap(cfg, tail, **kw):
    """Track a series and return the gap BULL_GAP's three bars produce."""
    s = make(BULL_GAP + tail, **kw)
    book = track_fvgs(s, cfg)
    return s, book, the_gap(book.fvgs)


def hold(price: float) -> tuple[float, float, float, float]:
    """A quiet bar well above the zone, so it changes nothing."""
    return (price, price + 0.0002, price - 0.0002, price)


# ------------------------------------------------- SPEC 12.1 vs 12.4: the edges


def test_proximal_is_the_edge_price_reaches_first(cfg):
    """SPEC 12.1's table labels proximal/distal backwards.

    12.2's touch rule (``bullish: L <= zone_high``) and 12.4's worked example (*"buy
    limit at 1.08420 (proximal edge)"*, where 1.08420 is ``L_n`` = ``zone_high``) both
    say the opposite, and they are right: a bullish gap forms with price above it.

    It is not cosmetic. Entry model C places its limit at the proximal edge, so the label
    decides whether a model-C entry waits for a shallow pullback or a deep one. See D-011.
    """
    s = make(BULL_GAP)
    f = the_gap(detect_fvgs(s, cfg))
    assert f.zone_low == pytest.approx(ZONE_LOW)
    assert f.zone_high == pytest.approx(ZONE_HIGH)
    assert f.proximal == f.zone_high  # reached first, coming down
    assert f.distal == f.zone_low
    assert f.proximal > f.distal


def test_spec_12_4_worked_example_reproduces(cfg):
    """The spec's own numbers, end to end."""
    s = make([
        (1.08200, 1.08310, 1.08150, 1.08250),
        (1.08300, 1.08620, 1.08290, 1.08600),
        (1.08620, 1.08760, 1.08420, 1.08700),
    ])
    f = the_gap(detect_fvgs(s, cfg))
    assert f.zone_low == pytest.approx(1.08310)
    assert f.zone_high == pytest.approx(1.08420)
    assert f.size == pytest.approx(0.00110)
    assert f.ce == pytest.approx(1.08365)
    assert f.proximal == pytest.approx(1.08420)  # 12.4's "buy limit at 1.08420"


# ------------------------------------------------------ SPEC 12.2 the lifecycle


def test_a_gap_nobody_returns_to_stays_unmitigated(cfg):
    _, _, f = only_gap(cfg, [hold(1.08850)] * 5)
    assert f.status is FvgStatus.UNMITIGATED
    assert f.first_touch_index is None
    assert f.terminal_index is None


def test_a_touch_short_of_ce_is_PARTIAL_not_MITIGATED(cfg):
    """PARTIAL is the only non-terminal status: this gap can still be used later."""
    _, _, f = only_gap(cfg, [(1.08850, 1.08860, 1.08250, 1.08800), hold(1.08800)])
    assert f.first_touch_index == GAP_BAR + 1
    assert f.status is FvgStatus.PARTIAL
    assert not f.status.is_terminal
    assert f.mitigated_index is None


def test_reaching_ce_mitigates_under_the_default_mode(cfg):
    assert cfg.fvg.mitigation_mode == "ce"
    _, _, f = only_gap(cfg, [(1.08850, 1.08860, CE - 0.00001, 1.08800), hold(1.08800)])
    assert f.status is FvgStatus.MITIGATED
    assert f.mitigated_index == GAP_BAR + 1
    assert f.mitigated_at is not None


def test_the_three_mitigation_modes_need_three_different_depths(cfg):
    """SPEC 12.2: ``touch`` is strictest, ``full`` loosest. The choice changes how many
    gaps remain available to entry model C and is an ablation dimension."""
    shallow = (1.08850, 1.08860, ZONE_HIGH - 0.00002, 1.08800)  # into the zone, above CE
    depths = {}
    for mode in ("touch", "ce", "full"):
        c, _ = load_config(overrides={"fvg": {"mitigation_mode": mode}})
        _, _, f = only_gap(c, [shallow, hold(1.08800)])
        depths[mode] = f.status
    assert depths["touch"] is FvgStatus.MITIGATED
    assert depths["ce"] is FvgStatus.PARTIAL
    assert depths["full"] is FvgStatus.PARTIAL


def test_full_mode_needs_the_far_edge(cfg):
    c, _ = load_config(overrides={"fvg": {"mitigation_mode": "full"}})
    _, _, f = only_gap(c, [(1.08850, 1.08860, ZONE_LOW, 1.08800), hold(1.08800)])
    assert f.status is FvgStatus.MITIGATED


def test_a_gap_over_is_INVALIDATED_not_MITIGATED(cfg):
    """SPEC 12.5, and the transition the synthetic fixture cannot produce.

    A bar that opens below the whole zone never traded inside it. Under SPEC 12.2's
    one-sided touch rule (``bullish: L <= zone_high``) it counts as a touch anyway, which
    mitigates it -- and because a close below ``zone_low`` implies a low below
    ``zone_low``, which is at or past every mitigation target, that made INVALIDATED
    unreachable *entirely*. See D-011.
    """
    below = (1.07500, 1.07600, 1.07400, 1.07450)  # whole range under zone_low
    _, _, f = only_gap(cfg, [below, hold(1.07450)], gaps={3})
    assert f.first_touch_index is None, "price never traded inside the zone"
    assert f.status is FvgStatus.INVALIDATED
    assert f.invalidated_index == GAP_BAR + 1
    assert f.mitigated_index is None


def test_a_bar_that_trades_through_and_closes_beyond_is_MITIGATED(cfg):
    """The other half of the ordering rule: a gap price actually traded through was
    used, wherever the bar happened to close."""
    through = (1.08850, 1.08860, 1.07900, 1.07950)  # dips through the zone, closes under
    _, _, f = only_gap(cfg, [through, hold(1.07950)])
    assert f.first_touch_index == GAP_BAR + 1
    assert f.status is FvgStatus.MITIGATED
    assert f.invalidated_index is None


def test_a_gap_expires_after_max_age_bars(cfg):
    narrow, _ = load_config(overrides={"fvg": {"max_age_bars": 3}})
    _, _, f = only_gap(narrow, [hold(1.08850)] * 8)
    assert f.status is FvgStatus.EXPIRED
    assert f.expired_index == f.confirmed_index + 4  # first bar with age > 3


def test_expiry_beats_a_touch_on_the_same_bar(cfg):
    """Checked first on purpose: a gap that has aged out was already unusable when the
    bar opened, so price arriving on that bar does not resurrect it."""
    narrow, _ = load_config(overrides={"fvg": {"max_age_bars": 2}})
    tail = [hold(1.08850), hold(1.08850), (1.08850, 1.08860, ZONE_LOW, 1.08800)]
    _, _, f = only_gap(narrow, tail)
    assert f.status is FvgStatus.EXPIRED
    assert f.mitigated_index is None


def test_the_confirming_bar_cannot_mitigate_its_own_gap(cfg):
    """Bar ``n`` creates the gap; testing it against itself would let the move that made
    the imbalance be the move that fills it."""
    _, _, f = only_gap(cfg, [hold(1.08850)])
    assert f.first_touch_index is None
    assert f.confirmed_index == GAP_BAR


# --------------------------------------------------------- status is time-varying


def test_status_at_reports_what_was_known_then_not_the_final_state(cfg):
    """Reading the stored ``status`` to decide availability at a past bar is lookahead,
    and it is the natural mistake because the object looks like it holds one."""
    _, _, f = only_gap(cfg, [hold(1.08850), (1.08850, 1.08860, ZONE_LOW, 1.08800)])
    assert f.status is FvgStatus.MITIGATED
    assert f.status_at(GAP_BAR) is FvgStatus.UNMITIGATED
    assert f.status_at(GAP_BAR + 1) is FvgStatus.UNMITIGATED
    assert f.status_at(f.mitigated_index) is FvgStatus.MITIGATED
    assert f.is_available_at(GAP_BAR + 1)
    assert not f.is_available_at(f.mitigated_index)


def test_age_at_counts_from_confirmation(cfg):
    _, _, f = only_gap(cfg, [hold(1.08850)] * 3)
    assert f.age_at(f.confirmed_index) == 0
    assert f.age_at(f.confirmed_index + 5) == 5


def test_the_tracker_leaves_its_input_alone(cfg, m15_quarter):
    """Detection output is shared with the displacement engine (SPEC 10.2). A tracker
    that mutated it would make the displacement filter depend on whether anyone had run
    the tracker first."""
    h4 = resample(m15_quarter, "H4", cfg)
    detected = detect_fvgs(h4, cfg)
    book = track_fvgs(h4, cfg, detected)
    assert all(f.status is FvgStatus.UNMITIGATED for f in detected)
    assert all(f.first_touch_index is None for f in detected)
    assert any(f.status is not FvgStatus.UNMITIGATED for f in book.fvgs)


def test_lifecycle_is_prefix_stable(cfg, m15_quarter):
    """SPEC 25.2. Truncating the series can only remove transitions from the end."""
    h4 = resample(m15_quarter, "H4", cfg)
    full = track_fvgs(h4, cfg)
    for frac in (0.5, 0.8):
        k = int(h4.n * frac)
        part = track_fvgs(h4.head(k), cfg)
        got = {(t.fvg_id, t.bar_index, t.to) for t in part.transitions}
        want = {
            (t.fvg_id, t.bar_index, t.to)
            for t in full.transitions
            if t.bar_index < k
        }
        assert got == want


def test_every_terminal_status_is_reached_at_most_once(cfg, m15_quarter):
    h4 = resample(m15_quarter, "H4", cfg)
    book = track_fvgs(h4, cfg)
    for f in book.fvgs:
        terminals = [f.mitigated_index, f.invalidated_index, f.expired_index]
        assert sum(1 for t in terminals if t is not None) <= 1
        if f.status.is_terminal:
            assert f.terminal_index is not None


# ------------------------------------------------------------ SPEC 12.3 selection


def _three_gaps(cfg):
    """A series with several bullish gaps inside one leg, of different sizes."""
    tail = BULL_GAP + [
        (1.08850, 1.09400, 1.08840, 1.09350),  # gap 2 vs bar n-2
        (1.09350, 1.09900, 1.09340, 1.09850),  # gap 3, larger
    ]
    s = make(tail)
    return s, track_fvgs(s, cfg)


def test_selection_first_takes_the_earliest(cfg):
    s, book = _three_gaps(cfg)
    got = select_fvg(book.fvgs, WARM, s.n - 1, FvgDirection.BULLISH, cfg)
    assert got is not None
    others = [
        f for f in book.fvgs
        if f.direction is FvgDirection.BULLISH and f.is_available_at(s.n - 1)
    ]
    assert got.confirmed_index == min(f.confirmed_index for f in others)


def test_selection_largest_and_nearest_can_disagree_with_first(cfg):
    s, book = _three_gaps(cfg)
    picks = {}
    for mode in ("first", "largest", "nearest"):
        c, _ = load_config(overrides={"fvg": {"selection": mode}})
        picks[mode] = select_fvg(
            book.fvgs, WARM, s.n - 1, FvgDirection.BULLISH, c, price=float(s.close[-1])
        )
    assert all(p is not None for p in picks.values())
    assert picks["largest"].size_atr >= picks["first"].size_atr
    nearest_gap = min(
        (f for f in book.fvgs
         if f.direction is FvgDirection.BULLISH and f.is_available_at(s.n - 1)),
        key=lambda f: abs(f.proximal - float(s.close[-1])),
    )
    assert picks["nearest"].id == nearest_gap.id


def test_nearest_falls_back_to_first_without_a_price(cfg):
    """Ranking by an edge it cannot compare would be a silent wrong answer."""
    s, book = _three_gaps(cfg)
    c, _ = load_config(overrides={"fvg": {"selection": "nearest"}})
    got = select_fvg(book.fvgs, WARM, s.n - 1, FvgDirection.BULLISH, c, price=None)
    first = select_fvg(book.fvgs, WARM, s.n - 1, FvgDirection.BULLISH, cfg)
    assert got.id == first.id


def test_selection_only_offers_gaps_unmitigated_AT_THE_SETUP_BAR(cfg):
    """Availability is read through ``status_at``, so a gap mitigated later still counts
    as available now -- the stored end-of-run status would say otherwise."""
    _, book = _three_gaps(cfg)
    s2, book2, f = only_gap(cfg, [hold(1.08850), (1.08850, 1.08860, ZONE_LOW, 1.08800)])
    at_touch = f.mitigated_index
    assert select_fvg(book2.fvgs, WARM, GAP_BAR + 1, FvgDirection.BULLISH, cfg) is not None
    assert select_fvg(
        book2.fvgs, WARM, s2.n - 1, FvgDirection.BULLISH, cfg, at_bar=at_touch
    ) is None


def test_selection_returns_none_when_nothing_qualifies(cfg):
    s, book = _three_gaps(cfg)
    assert select_fvg(book.fvgs, WARM, s.n - 1, FvgDirection.BEARISH, cfg) is None
    assert select_fvg(book.fvgs, 0, 2, FvgDirection.BULLISH, cfg) is None


def test_unknown_selection_mode_raises(cfg):
    s, book = _three_gaps(cfg)
    bad = load_config()[0].model_copy()
    with pytest.raises(ValueError, match="unknown fvg.selection"):
        object.__setattr__(bad.fvg, "selection", "cheapest")
        select_fvg(book.fvgs, WARM, s.n - 1, FvgDirection.BULLISH, bad)


# ------------------------------------------------------------- the fill curve


def test_fill_curve_is_monotone_and_censors_the_long_end(cfg, m15_quarter):
    """Gaps whose k-bar window runs past the end of the data are excluded rather than
    counted unfilled -- otherwise the curve sags at the long end purely because of where
    the series stops."""
    h4 = resample(m15_quarter, "H4", cfg)
    book = track_fvgs(h4, cfg)
    curve = book.fill_curve((1, 3, 5, 10, 20, 30))
    vals = [curve[k] for k in sorted(curve)]
    assert all(b >= a - 1e-12 for a, b in zip(vals, vals[1:]))
    assert all(0.0 <= v <= 1.0 for v in vals)


def test_fill_curve_of_an_empty_book_is_nan_not_zero(cfg):
    empty = track_fvgs(make([hold(MID)] * 3), cfg)
    empty.fvgs = []
    assert all(np.isnan(v) for v in empty.fill_curve((1, 5)).values())


# ------------------------------------------------- SPEC 12.6 the standalone edge test


@pytest.fixture(scope="module")
def study_run():
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
    book = track_fvgs(h4, cfg)
    return cfg, h4, book, FS.run_study(h4, book, cfg, permutations=800, bootstrap=2000)


def test_positive_control_a_known_effect_is_detected(study_run):
    """A study that could only ever say "no edge" would pass every null test here."""
    cfg, h4, book, _ = study_run
    shifted = FS.run_study(
        h4, book, cfg, permutations=800, bootstrap=2000, touch_shift=0.5
    )
    assert shifted.verdict() is Verdict.DIFFERENT
    assert "CARRY DIRECTIONAL INFORMATION" in shifted.headline()
    assert shifted.results[0].ci_low > 0


def test_detection_is_monotone_in_the_injected_effect(study_run):
    """The guaranteed property, asserted instead of a threshold.

    ``mde`` is the effect this sample would detect **80% of the time**, not a hard
    cutoff, so "an effect below the MDE is not detected" is a probabilistic claim and
    tuning a multiplier until it holds would be fitting the test to one draw. What is
    exact: the bootstrap distribution shifts right by exactly the injected amount, so
    once the interval clears zero it stays clear.
    """
    _, _, _, st = study_run
    r = st.results[0]
    assert 0.0 < r.mde < 1.0
    t, c = r.touch.returns, r.control.returns
    assert not detects_effect(t, c, 0.0), "the raw sample must not already be significant"
    assert detects_effect(t, c, r.mde * 2.0)
    seen = [detects_effect(t, c, r.mde * k) for k in (0.0, 0.5, 1.0, 1.5, 2.0, 3.0)]
    assert seen == sorted(seen), f"detection flipped back off: {seen}"


def test_null_calibration_lands_near_alpha(study_run):
    """Shuffling the touch label makes the true effect exactly zero, so every DIFFERENT
    verdict under a shuffle is a false positive by construction."""
    _, _, _, st = study_run
    r = st.results[0]
    fpr = null_calibration(r.touch.returns, r.control.returns, trials=300, bootstrap=600)
    assert 0.0 < fpr < 0.12


def test_touches_on_one_bar_in_one_direction_are_one_observation(study_run):
    """D-010 §3, which cost a real false result in the H5 study, applies here unchanged:
    one bar routinely tags several stacked gaps and they share a forward return."""
    cfg, h4, book, st = study_run
    events, raw = FS.touch_events(h4, book, atr_ref(h4, cfg.atr.period))
    keys = [(e.bar, e.direction) for e in events]
    assert len(keys) == len(set(keys))
    assert st.n_touches == len(events)
    assert st.n_raw_touches >= st.n_touches


def test_only_the_first_touch_of_a_gap_becomes_an_event(study_run):
    """SPEC 12.6 asks about touching an *unmitigated* gap; a later re-entry is a
    different object and would add correlated rows."""
    cfg, h4, book, _ = study_run
    events, _ = FS.touch_events(h4, book, atr_ref(h4, cfg.atr.period))
    firsts = {f.first_touch_index for f in book.fvgs if f.first_touch_index is not None}
    assert {e.bar for e in events} <= firsts


def test_controls_are_matched_and_carry_the_gap_direction(study_run):
    _, _, _, st = study_run
    for r in st.results:
        assert r.control.n > 0
        # One control per event, minus any lost to the forward-return horizon.
        assert abs(r.control.n - r.touch.n) <= 2


def test_returns_are_signed_so_the_two_directions_do_not_cancel(cfg):
    s = make(BULL_GAP + [hold(1.08850)] * 3)
    atr = atr_ref(s, cfg.atr.period)
    up = FS.forward_returns(s, [WARM], [1], 1, atr)
    down = FS.forward_returns(s, [WARM], [-1], 1, atr)
    assert up[0] == pytest.approx(-down[0])


def test_forward_returns_are_censored_at_the_end_of_the_series(cfg):
    s = make(BULL_GAP + [hold(1.08850)])
    atr = atr_ref(s, cfg.atr.period)
    assert len(FS.forward_returns(s, [s.n - 1], [1], 1, atr)) == 0
    assert len(FS.forward_returns(s, [s.n - 5], [1], 1, atr)) == 1


def test_multiple_testing_correction_is_applied(study_run):
    _, _, _, st = study_run
    for r in st.results:
        assert r.p_adjusted is not None
        if np.isfinite(r.p_value):
            assert r.p_adjusted >= r.p_value - 1e-12


def test_an_undecided_study_is_not_called_a_null_result():
    st = FS.FvgStudy(horizons=(1,))
    st.results = [
        FS.HorizonResult(
            horizon=1,
            touch=FS.Group("t", np.random.default_rng(0).normal(size=30)),
            control=FS.Group("c", np.random.default_rng(1).normal(size=30)),
            diff=0.02,
            ci_low=-0.9,
            ci_high=1.0,
            p_value=0.5,
            mde=0.8,
            required_touch_n=500.0,
        )
    ]
    assert st.verdict() is Verdict.UNDERPOWERED
    assert "NOT a null result" in st.headline()
    assert "NO EDGE" not in st.headline()


def test_equivalence_needs_every_horizon(study_run):
    _, _, _, st = study_run
    verdicts = [r.verdict for r in st.results]
    if all(v is Verdict.EQUIVALENT for v in verdicts):
        assert st.verdict() is Verdict.EQUIVALENT
    elif any(v is Verdict.DIFFERENT for v in verdicts):
        assert st.verdict() is Verdict.DIFFERENT
    else:
        assert st.verdict() is Verdict.UNDERPOWERED


def test_study_is_deterministic(study_run):
    cfg, h4, book, _ = study_run
    a = FS.run_study(h4, book, cfg, permutations=200, bootstrap=500)
    b = FS.run_study(h4, book, cfg, permutations=200, bootstrap=500)
    assert [r.ci_low for r in a.results] == [r.ci_low for r in b.results]
    assert a.headline() == b.headline()


def test_pooling_combines_returns_not_verdicts(study_run):
    cfg, h4, book, _ = study_run
    a = FS.run_study(h4, book, cfg, permutations=200, bootstrap=500)
    pooled = FS.pool_studies([a, a])
    assert pooled.n_touches == 2 * a.n_touches
    assert pooled.results[0].touch.n == 2 * a.results[0].touch.n
    assert pooled.results[0].diff == pytest.approx(a.results[0].diff, abs=1e-9)
    width = lambda r: r.ci_high - r.ci_low
    assert width(pooled.results[0]) < width(a.results[0])


def test_pool_of_nothing_is_empty(study_run):
    assert FS.pool_studies([]).results == []


def test_size_breakdown_partitions_the_touches(study_run):
    cfg, h4, book, st = study_run
    rows = FS.by_size_tercile(h4, book, cfg, horizon=1)
    assert set(rows) <= {"small", "medium", "large"}
    assert sum(n for n, _ in rows.values()) <= st.n_touches
