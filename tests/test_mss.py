"""CHoCH reference selection and MSS confirmation (SPEC 11).

Two kinds of test here, and both are needed.

The hand-built series pin the *rules*: which swing is selected, when the window opens,
what each clause rejects.  They are deliberately arithmetic -- every bar is written out
and every threshold is reachable by hand -- because a rule that only ever runs against
a random walk is checked against a distribution, not against its own definition.

The synthetic-fixture tests pin the *properties* that no example can establish: that
MSS is a subset of CHoCH, that nothing repaints, and that the engine reads no bar it
has not reached.  SPEC 25.2 requires the replay test of every engine; this is Phase 9's
instance of it.

There is also a positive control (``test_positive_control_*``).  An engine that could
only ever return "no MSS" would pass every rejection test in this file and be useless,
which is the same trap the study modules guard against with their own controls.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from bot.config.loader import load_config
from bot.core.bars import build_series, from_epoch_s
from bot.core.displacement import Direction
from bot.core.fvg import detect_fvgs
from bot.core.indicators import atr_ref
from bot.core.liquidity import LevelSource, Side
from bot.core.mss import (
    Clause,
    MssEngine,
    Outcome,
    ReferenceMode,
    analyse_mss,
    detect_micro_swings,
)
from bot.core.sessions import build_sessions
from bot.core.structure import analyse_structure
from bot.core.sweeps import SweepEvent, SweepEventType, analyse_sweeps
from bot.core.swings import SwingKind, detect_swings
from bot.data.resample import resample
from bot.data.synthetic import generate

UTC = timezone.utc
H4_SECONDS = 14400
WARM = 20
MID = 1.07860
HALF = 0.00225  # true range 0.00450 -> warm-up ATR is exactly 0.00450


# --------------------------------------------------------------------- fixtures


def make(tail: list[tuple[float, float, float, float]]):
    """``WARM`` identical bars, then the bars written out in ``tail``.

    The flat warm-up gives a known ATR and, because the tie rule is ``leftmost`` and
    every warm high is equal, produces no swings of its own to interfere with.
    """
    n = WARM + len(tail)
    t = np.arange(n, dtype=np.int64) * H4_SECONDS
    o = [MID] * WARM + [x[0] for x in tail]
    h = [MID + HALF] * WARM + [x[1] for x in tail]
    lo = [MID - HALF] * WARM + [x[2] for x in tail]
    c = [MID] * WARM + [x[3] for x in tail]
    return build_series(
        "EURUSD",
        "H4",
        t,
        t + H4_SECONDS,
        np.array(o),
        np.array(h),
        np.array(lo),
        np.array(c),
        np.ones(n),
    )


def mk_sweep(
    series,
    *,
    side: Side = Side.SELL_SIDE,
    level_price: float,
    sweep_extreme: float,
    sweep_extreme_bar: int,
    confirm_bar: int | None = None,
    trigger_bar: int | None = None,
    ident: str = "SW:1",
    tier: int = 2,
    source: LevelSource = LevelSource.SWING_LOW,
) -> SweepEvent:
    """A confirmed sweep, fabricated.

    Phase 9 consumes sweeps; producing one through the real engine would make every
    rule test here depend on the sweep engine's own thresholds as well as its own.
    """
    trig = sweep_extreme_bar if trigger_bar is None else trigger_bar
    conf = trig if confirm_bar is None else confirm_bar
    return SweepEvent(
        id=ident,
        symbol=series.symbol,
        timeframe=series.timeframe,
        type=SweepEventType.CONFIRMED,
        reason=None,
        side=side,
        level_id=f"LVL:{ident}",
        level_source=source,
        level_tier=tier,
        level_price=level_price,
        level_strength=1,
        trigger_bar=trig,
        confirm_bar=conf,
        at=from_epoch_s(series.close_time[conf]),
        sweep_extreme=sweep_extreme,
        sweep_extreme_bar=sweep_extreme_bar,
        penetration=abs(level_price - sweep_extreme),
        penetration_atr=0.3,
        wick_ratio=0.6,
        close_position=0.8,
        confirmation_bars=conf - trig,
        single_bar_sweep=conf == trig,
    )


#: A bullish setup that satisfies every SPEC 11.5 clause.  Index 20 is the reference
#: swing high (1.08500, confirmed at 22); index 23 sweeps down to 1.07300; the leg
#: 23-25 displaces up through the reference and leaves a bullish FVG at bar 25.
BULLISH_TAIL = [
    (MID, 1.08500, MID - HALF, 1.08300),  # 20  reference swing high
    (1.08300, 1.08350, 1.08000, 1.08050),  # 21
    (1.08050, 1.08100, 1.07800, 1.07850),  # 22  reference confirms here
    (1.07850, 1.07900, 1.07300, 1.07800),  # 23  sweep: extreme 1.07300
    (1.07800, 1.08100, 1.07790, 1.08080),  # 24
    (1.08080, 1.08700, 1.08060, 1.08660),  # 25  closes through 1.08500
]
REF_PRICE = 1.08500
SWEEP_BAR = WARM + 3  # 23
BREAK_BAR = WARM + 5  # 25


def bullish_case(cfg, tail=None, **sweep_kw):
    series = make(BULLISH_TAIL if tail is None else tail)
    swings = detect_swings(series, cfg)
    sweep = mk_sweep(
        series,
        level_price=1.07400,
        sweep_extreme=1.07300,
        sweep_extreme_bar=SWEEP_BAR,
        **sweep_kw,
    )
    res = analyse_mss(
        series, cfg, [sweep], swings=swings, fvgs=detect_fvgs(series, cfg)
    )
    return series, res.candidates[0]


# --------------------------------------------------------- the positive control


def test_positive_control_a_clean_setup_confirms(cfg):
    """A hand-built bullish setup that satisfies every clause must produce an MSS.

    Without this, every rejection test below would still pass against an engine that
    returned ``CHOCH_TIMEOUT`` unconditionally.
    """
    series, c = bullish_case(cfg)
    assert c.outcome is Outcome.MSS_CONFIRMED
    assert c.failed_clauses == ()
    assert c.direction is Direction.BULLISH
    assert c.reference_price == pytest.approx(REF_PRICE)
    assert c.choch_bar == BREAK_BAR
    assert c.bars_sweep_to_choch == BREAK_BAR - SWEEP_BAR
    assert c.displacement is not None and c.displacement.confirmed
    assert c.displacement.fvg_id is not None  # disp.require_fvg is on by default


def test_the_break_is_by_close_not_by_wick(cfg):
    """SPEC 11.2.  A wick through the reference is what a sweep of it looks like."""
    tail = list(BULLISH_TAIL)
    # Same high, close back under the reference.
    tail[5] = (1.08080, 1.08700, 1.08060, 1.08400)
    _, c = bullish_case(cfg, tail=tail)
    assert c.outcome is Outcome.CHOCH_TIMEOUT
    assert c.choch_bar is None


# ------------------------------------------------- SPEC 11.1 reference selection


def test_major_reference_is_the_last_unbroken_swing_high(cfg):
    _, c = bullish_case(cfg)
    assert c.reference_formed_index == WARM
    assert c.reference_price == pytest.approx(REF_PRICE)


def test_major_reference_skips_a_swing_high_already_broken(cfg):
    """A level price has already traded through is not a structural reference.

    Bar 20 prints the higher high; bar 23 -- before the sweep -- trades above the
    *later*, lower swing high, so the walk must keep going back to bar 20.
    """
    tail = [
        (MID, 1.08900, MID - HALF, 1.08700),  # 20  high high
        (1.08700, 1.08750, 1.08300, 1.08350),  # 21
        (1.08350, 1.08400, 1.08100, 1.08150),  # 22  lower swing high candidate at 21
        (1.08150, 1.08800, 1.08100, 1.08750),  # 23  trades back above 1.08750? no: 1.08800
        (1.08750, 1.08800, 1.08200, 1.08250),  # 24
        (1.08250, 1.08300, 1.07300, 1.08250),  # 25  sweep down, extreme 1.07300
        (1.08250, 1.08600, 1.08240, 1.08560),  # 26
        (1.08560, 1.09000, 1.08540, 1.08950),  # 27  closes through 1.08900
    ]
    series = make(tail)
    swings = detect_swings(series, cfg)
    sweep = mk_sweep(
        series,
        level_price=1.07400,
        sweep_extreme=1.07300,
        sweep_extreme_bar=WARM + 5,
    )
    c = analyse_mss(
        series, cfg, [sweep], swings=swings, fvgs=detect_fvgs(series, cfg)
    ).candidates[0]
    # Whatever the outcome, the reference must be the unbroken high, not a broken one.
    if c.reference_price is not None:
        highs = series.high[c.reference_formed_index + 1 : WARM + 6]
        assert float(highs.max()) <= c.reference_price + 1e-12


def test_no_reference_when_nothing_formed_before_the_sweep(cfg):
    """SPEC 11.1: candidates must have ``formed_index < s``."""
    series = make(
        [
            (MID, MID + HALF, 1.07300, 1.07800),  # 20  the sweep bar itself
            (1.07800, 1.08100, 1.07790, 1.08080),
            (1.08080, 1.08700, 1.08060, 1.08660),
        ]
    )
    swings = detect_swings(series, cfg)
    sweep = mk_sweep(
        series, level_price=1.07400, sweep_extreme=1.07300, sweep_extreme_bar=WARM
    )
    c = analyse_mss(series, cfg, [sweep], swings=swings).candidates[0]
    assert c.outcome is Outcome.NO_CHOCH_REFERENCE
    assert c.reference_price is None


def test_reference_beyond_the_distance_cap_is_rejected_up_front(cfg):
    """SPEC 11.1: REFERENCE_TOO_FAR, not a stop the risk layer rejects later.

    The distinction is what the rejection log is for -- a setup killed here and a setup
    killed by position sizing are different counterfactuals.
    """
    tight, _ = load_config(overrides={"choch": {"max_reference_distance_atr": 0.5}})
    _, c = bullish_case(tight)
    assert c.outcome is Outcome.REFERENCE_TOO_FAR
    assert c.reference_distance_atr is not None
    assert c.reference_distance_atr > 0.5
    assert c.choch_bar is None


def test_reference_lookback_is_measured_from_the_sweep(cfg):
    short, _ = load_config(overrides={"choch": {"max_reference_lookback": 2}})
    _, c = bullish_case(short)
    assert c.outcome is Outcome.NO_CHOCH_REFERENCE  # the reference formed 3 bars back


# ----------------------------------------------------- SPEC 11.4 the two floors


def test_the_wait_is_measured_from_the_sweep_extreme(cfg):
    """SPEC 11.4 / 9.6.  ``min_bars_after_sweep`` counts from ``s``."""
    patient, _ = load_config(overrides={"choch": {"min_bars_after_sweep": 3}})
    _, c = bullish_case(patient)
    # The break is 2 bars after the extreme, so a 3-bar WAIT must exclude it.
    assert c.window_first_bar == SWEEP_BAR + 3
    assert c.outcome is not Outcome.MSS_CONFIRMED


def test_a_break_before_the_sweep_is_knowable_is_not_a_break(cfg):
    """The second floor: a sweep confirming at bar 25 cannot be confirmed *by* bar 25.

    ``min_bars_after_sweep`` alone would admit it -- the extreme is at 23 -- but no
    live engine knew a sweep had happened until 25 closed.  The tail is extended past
    the break so the window genuinely stays open: a CHOCH_TIMEOUT says the engine kept
    looking and declined this break, where a NO_WINDOW would only say it ran out of
    series.
    """
    quiet = (1.08300, 1.08340, 1.08200, 1.08260)
    _, c = bullish_case(cfg, tail=BULLISH_TAIL + [quiet] * 3, confirm_bar=BREAK_BAR)
    assert c.window_first_bar == BREAK_BAR + 1
    assert c.window_last_bar > c.window_first_bar
    assert c.outcome is Outcome.CHOCH_TIMEOUT
    assert c.choch_bar is None


def test_same_bar_choch_allowed_moves_only_the_knowability_floor(cfg):
    loose, _ = load_config(overrides={"sweep": {"same_bar_choch_allowed": True}})
    _, c = bullish_case(loose, confirm_bar=BREAK_BAR)
    assert c.window_first_bar == BREAK_BAR
    assert c.outcome is Outcome.MSS_CONFIRMED


def test_window_closes_after_max_bars_after_sweep(cfg):
    narrow, _ = load_config(overrides={"choch": {"max_bars_after_sweep": 4}})
    tail = list(BULLISH_TAIL)
    # Push the break out to 7 bars after the sweep.
    tail = tail[:5] + [(1.08080, 1.08100, 1.08060, 1.08080)] * 3 + [BULLISH_TAIL[5]]
    _, c = bullish_case(narrow, tail=tail)
    assert c.window_last_bar == SWEEP_BAR + 4
    assert c.outcome is Outcome.CHOCH_TIMEOUT


# --------------------------------------------------- SPEC 11.5 the extra clauses


def test_a_weak_leg_is_a_choch_but_not_an_mss(cfg):
    """SPEC 6.6 / 6.9.  The failure is *recorded*, not dropped.

    This population is the direct measurement of whether the displacement requirement
    adds anything; an engine that discarded it could never answer that question.
    """
    tail = list(BULLISH_TAIL)
    # Same break, but crawl there: tiny bodies, no gap, small net.
    tail[3] = (1.07850, 1.07900, 1.07300, 1.07880)
    tail[4] = (1.08200, 1.08560, 1.07880, 1.08240)
    tail[5] = (1.08300, 1.08700, 1.08240, 1.08560)
    _, c = bullish_case(cfg, tail=tail)
    assert c.outcome is Outcome.CHOCH_NOT_MSS
    assert c.is_choch and not c.is_mss
    assert Clause.DISPLACEMENT in c.failed_clauses
    assert c.displacement is not None and not c.displacement.confirmed


def test_a_new_extreme_beyond_tolerance_fails_the_setup(cfg):
    """SPEC 11.5 clause 5.  A sweep that price then accepts through has failed."""
    tail = list(BULLISH_TAIL)
    tail[4] = (1.07800, 1.08100, 1.06900, 1.08080)  # dives well below the sweep low
    _, c = bullish_case(cfg, tail=tail)
    assert Clause.NEW_EXTREME in c.failed_clauses
    assert c.outcome is Outcome.CHOCH_NOT_MSS
    assert c.new_extreme_bar == WARM + 4


def test_the_new_extreme_tolerance_is_a_boundary_not_a_threshold_in_name(cfg):
    """``invalidate.new_extreme_atr`` exists so a single tick is not a regime change.

    Both variants are identical except for how far bar 24 dips below the sweep low, so
    the only thing that can differ between them is this clause.  Testing it as a pair
    rather than as a single case keeps the assertion about the tolerance instead of
    about the rest of the setup, which a deeper low would also perturb.
    """
    series = make(BULLISH_TAIL)
    tol = cfg.invalidate.new_extreme_atr * float(atr_ref(series, cfg.atr.period)[SWEEP_BAR])

    def dip(depth: float):
        tail = list(BULLISH_TAIL)
        tail[4] = (1.07800, 1.08100, 1.07300 - depth, 1.08080)
        return bullish_case(cfg, tail=tail)[1]

    inside, outside = dip(0.4 * tol), dip(1.6 * tol)
    assert inside.new_extreme_bar is None
    assert outside.new_extreme_bar == WARM + 4
    assert Clause.NEW_EXTREME not in inside.failed_clauses
    assert Clause.NEW_EXTREME in outside.failed_clauses
    # Nothing else may move between the two.
    assert inside.choch_bar == outside.choch_bar
    assert set(outside.failed_clauses) - {Clause.NEW_EXTREME} == set(inside.failed_clauses)


def test_a_setup_untouched_below_the_sweep_low_confirms(cfg):
    """The clean case: no dip at all, so the clause cannot be what decides it."""
    _, c = bullish_case(cfg)
    assert c.new_extreme_bar is None
    assert c.outcome is Outcome.MSS_CONFIRMED


def test_new_extreme_with_no_break_is_the_terminal_outcome(cfg):
    """SPEC 11.6 lists it as an invalidation; with no break there is nothing else to
    report, so it surfaces as the outcome rather than as a clause."""
    tail = list(BULLISH_TAIL)
    tail[4] = (1.07800, 1.07900, 1.06900, 1.07100)
    tail[5] = (1.07100, 1.07200, 1.07000, 1.07150)  # never breaks the reference
    _, c = bullish_case(cfg, tail=tail)
    assert c.outcome is Outcome.NEW_EXTREME
    assert c.choch_bar is None


def test_an_opposing_confirmed_sweep_fails_the_setup(cfg):
    series = make(BULLISH_TAIL)
    swings = detect_swings(series, cfg)
    bull = mk_sweep(
        series,
        level_price=1.07400,
        sweep_extreme=1.07300,
        sweep_extreme_bar=SWEEP_BAR,
        ident="SW:bull",
    )
    against = mk_sweep(
        series,
        side=Side.BUY_SIDE,
        level_price=1.08050,
        sweep_extreme=1.08100,
        sweep_extreme_bar=WARM + 4,
        ident="SW:bear",
        source=LevelSource.SWING_HIGH,
    )
    res = analyse_mss(
        series, cfg, [bull, against], swings=swings, fvgs=detect_fvgs(series, cfg)
    )
    c = next(x for x in res.candidates if x.sweep.id == "SW:bull")
    assert Clause.OPPOSING_SWEEP in c.failed_clauses
    assert c.opposing_sweep_bar == WARM + 4
    assert c.outcome is Outcome.CHOCH_NOT_MSS


def test_a_same_side_sweep_is_not_opposing(cfg):
    series = make(BULLISH_TAIL)
    swings = detect_swings(series, cfg)
    a = mk_sweep(
        series,
        level_price=1.07400,
        sweep_extreme=1.07300,
        sweep_extreme_bar=SWEEP_BAR,
        ident="SW:a",
    )
    b = mk_sweep(
        series,
        level_price=1.07500,
        sweep_extreme=1.07350,
        sweep_extreme_bar=WARM + 4,
        ident="SW:b",
    )
    res = analyse_mss(series, cfg, [a, b], swings=swings, fvgs=detect_fvgs(series, cfg))
    c = next(x for x in res.candidates if x.sweep.id == "SW:a")
    assert c.opposing_sweep_bar is None
    assert c.outcome is Outcome.MSS_CONFIRMED


def test_the_mtf_gate_is_injectable_and_rejects_as_a_clause(cfg):
    """SPEC 11.5's last clause, before the SPEC 7 bias engine exists.

    The default is ``bias.gate_mode = none``, the control SPEC 7.5 requires; a real
    gate can only remove MSS events, so every count here is an upper bound.
    """
    series = make(BULLISH_TAIL)
    swings = detect_swings(series, cfg)
    sweep = mk_sweep(
        series, level_price=1.07400, sweep_extreme=1.07300, sweep_extreme_bar=SWEEP_BAR
    )
    seen: list[tuple[Direction, int]] = []

    def gate(direction: Direction, bar: int) -> bool:
        seen.append((direction, bar))
        return False

    c = analyse_mss(
        series,
        cfg,
        [sweep],
        swings=swings,
        fvgs=detect_fvgs(series, cfg),
        gate=gate,
    ).candidates[0]
    assert seen == [(Direction.BULLISH, BREAK_BAR)]
    assert c.outcome is Outcome.CHOCH_NOT_MSS
    assert c.failed_clauses == (Clause.MTF_GATE,)


# ------------------------------------------------------------ bearish mirror


def test_the_bearish_mirror_confirms(cfg):
    """Every rule above is written for the bullish case; the mirror must be real code,
    not an assumption."""
    tail = [
        (MID, MID + HALF, 1.07200, 1.07400),  # 20  reference swing low 1.07200
        (1.07400, 1.07700, 1.07350, 1.07650),  # 21
        (1.07650, 1.07900, 1.07600, 1.07850),  # 22  reference confirms
        (1.07850, 1.08400, 1.07800, 1.07900),  # 23  sweep up: extreme 1.08400
        (1.07900, 1.07910, 1.07600, 1.07620),  # 24
        (1.07620, 1.07640, 1.07000, 1.07040),  # 25  closes through 1.07200
    ]
    series = make(tail)
    swings = detect_swings(series, cfg)
    sweep = mk_sweep(
        series,
        side=Side.BUY_SIDE,
        level_price=1.08300,
        sweep_extreme=1.08400,
        sweep_extreme_bar=SWEEP_BAR,
        source=LevelSource.SWING_HIGH,
    )
    c = analyse_mss(
        series, cfg, [sweep], swings=swings, fvgs=detect_fvgs(series, cfg)
    ).candidates[0]
    assert c.direction is Direction.BEARISH
    assert c.outcome is Outcome.MSS_CONFIRMED
    assert c.reference_price == pytest.approx(1.07200)


# ------------------------------------------------------------ micro mode (11.1)


def test_micro_swings_carry_the_n_bar_confirmation_lag(cfg):
    series = make(BULLISH_TAIL)
    for m in detect_micro_swings(series, cfg):
        assert m.confirmed_index == m.formed_index + cfg.choch.micro_fractal_n


def test_micro_reference_forms_after_the_sweep_not_before(cfg):
    """SPEC 11.1: micro breaks the first *pullback* high, which is a different level
    -- and a different strategy -- from major's last unbroken high."""
    series = make(BULLISH_TAIL)
    swings = detect_swings(series, cfg)
    sweep = mk_sweep(
        series, level_price=1.07400, sweep_extreme=1.07300, sweep_extreme_bar=SWEEP_BAR
    )
    c = analyse_mss(
        series,
        cfg,
        [sweep],
        swings=swings,
        fvgs=detect_fvgs(series, cfg),
        reference_mode=ReferenceMode.MICRO,
    ).candidates[0]
    assert c.reference_mode is ReferenceMode.MICRO
    if c.reference_formed_index is not None:
        assert c.reference_formed_index > SWEEP_BAR
        assert c.reference_price != pytest.approx(REF_PRICE)


def test_the_two_modes_are_different_strategies(cfg):
    """SPEC 11.1 is explicit that these are pre-registered variants, not a sweep.

    If they picked the same reference the distinction would be cosmetic, and reporting
    them separately would be the multiple-testing problem in disguise.
    """
    series = make(BULLISH_TAIL)
    swings = detect_swings(series, cfg)
    sweep = mk_sweep(
        series, level_price=1.07400, sweep_extreme=1.07300, sweep_extreme_bar=SWEEP_BAR
    )
    fvgs = detect_fvgs(series, cfg)
    major = analyse_mss(series, cfg, [sweep], swings=swings, fvgs=fvgs).candidates[0]
    micro = analyse_mss(
        series, cfg, [sweep], swings=swings, fvgs=fvgs, reference_mode="micro"
    ).candidates[0]
    assert major.reference_formed_index != micro.reference_formed_index


# --------------------------------------------------- properties on real fixtures


@pytest.fixture(scope="module")
def fixture_run():
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
    _, res = analyse_sweeps(
        cfg=cfg,
        h4=h4,
        d1=d1,
        w1=resample(src, "W1", cfg),
        mn1=resample(src, "MN1", cfg),
        sessions=build_sessions(src, cfg),
        h4_structure=st,
        d1_swings=detect_swings(d1, cfg),
    )
    return cfg, h4, st, res.confirmed(), detect_fvgs(h4, cfg)


def test_mss_is_a_strict_subset_of_choch(fixture_run):
    cfg, h4, st, sweeps, fvgs = fixture_run
    for mode in (ReferenceMode.MAJOR, ReferenceMode.MICRO):
        r = analyse_mss(h4, cfg, sweeps, swings=st.swings, fvgs=fvgs, reference_mode=mode)
        assert all(c.is_choch for c in r.mss)
        assert len(r.mss) + len(r.choch_not_mss) == len(r.choch)


def test_every_mss_satisfies_every_clause(fixture_run):
    cfg, h4, st, sweeps, fvgs = fixture_run
    r = analyse_mss(h4, cfg, sweeps, swings=st.swings, fvgs=fvgs)
    assert r.mss, "the fixture must produce at least one MSS or this proves nothing"
    for c in r.mss:
        assert c.failed_clauses == ()
        assert c.displacement is not None and c.displacement.confirmed
        assert c.new_extreme_bar is None and c.opposing_sweep_bar is None
        assert c.window_first_bar <= c.choch_bar <= c.window_last_bar
        assert c.bars_sweep_to_choch >= cfg.choch.min_bars_after_sweep
        assert c.bars_sweep_to_choch <= cfg.choch.max_bars_after_sweep
        assert c.choch_bar > c.sweep.confirm_bar  # same_bar_choch_allowed is false


def test_the_break_really_closed_beyond_the_reference(fixture_run):
    cfg, h4, st, sweeps, fvgs = fixture_run
    r = analyse_mss(h4, cfg, sweeps, swings=st.swings, fvgs=fvgs)
    for c in r.choch:
        close = float(h4.close[c.choch_bar])
        if c.direction is Direction.BULLISH:
            assert close > c.reference_price
        else:
            assert close < c.reference_price


def test_every_candidate_reaches_exactly_one_outcome(fixture_run):
    cfg, h4, st, sweeps, fvgs = fixture_run
    r = analyse_mss(h4, cfg, sweeps, swings=st.swings, fvgs=fvgs)
    assert len(r.candidates) == len(sweeps)
    assert sum(r.outcomes().values()) == len(sweeps)
    for c in r.candidates:
        assert (c.outcome is Outcome.CHOCH_NOT_MSS) == bool(c.failed_clauses)


def test_the_reference_was_visible_when_it_was_chosen(fixture_run):
    """SPEC 11.1's ``confirmed_at <= close_time(s)``, checked against the swing store's
    own history rather than against the finished store -- which is the point of
    ``SwingStore.visible_at``."""
    cfg, h4, st, sweeps, fvgs = fixture_run
    r = analyse_mss(h4, cfg, sweeps, swings=st.swings, fvgs=fvgs)
    for c in r.candidates:
        if c.reference_id is None:
            continue
        kind = SwingKind.HIGH if c.direction is Direction.BULLISH else SwingKind.LOW
        live = {s.id for s in st.swings.visible_at(c.sweep_extreme_bar, kind)}
        assert c.reference_id in live


def test_a_reference_the_finished_store_no_longer_holds_is_still_selectable(fixture_run):
    """The live-view fix has to be *reachable*, or it is decoration.

    SPEC 5.4 normalisation drops a swing that a later, more extreme same-kind swing
    supersedes.  Reading the finished store would therefore hide, at the sweep bar, a
    swing that was live there.  It is nearly self-correcting -- the move that
    supersedes a swing high has usually already broken it, and a broken level fails
    11.1's "unbroken since it formed" test anyway -- but not entirely: when the sweep
    precedes the superseding swing's formation, the two views disagree, on 4 of 2,323
    fixture sweeps (0.17%).

    Without this test the finished-store shortcut passes the whole suite, which is how
    it was found.
    """
    cfg, h4, st, sweeps, fvgs = fixture_run
    r = analyse_mss(h4, cfg, sweeps, swings=st.swings, fvgs=fvgs)
    survived = {s.id for s in st.swings.swings}
    chosen_but_superseded = [
        c for c in r.candidates if c.reference_id and c.reference_id not in survived
    ]
    assert chosen_but_superseded, (
        "no reference was drawn from a superseded swing, so this fixture cannot tell "
        "SwingStore.visible_at apart from the finished store"
    )
    for c in chosen_but_superseded:
        span = next(
            sp for sp in st.swings.history if sp.swing.id == c.reference_id
        )
        assert span.visible_at(c.sweep_extreme_bar)
        assert span.visible_until is not None  # it really was superseded later


def test_prefix_stability_nothing_repaints(fixture_run):
    """SPEC 25.2's replay test for this engine.

    Truncating the series can only remove candidates from the end.  Any candidate
    whose whole window fits inside the truncation must come back byte-identical --
    same outcome, same reference, same break bar, same failed clauses.
    """
    cfg, h4, _st, sweeps, _fvgs = fixture_run
    full = analyse_mss(
        h4, cfg, sweeps, swings=analyse_structure(h4, cfg).swings, fvgs=detect_fvgs(h4, cfg)
    )
    by_sweep = {c.sweep.id: c for c in full.candidates}

    for cut in (400, 800, 1200):
        view = h4.as_of(int(h4.close_time[cut - 1]))
        assert view.n == cut
        visible = [s for s in sweeps if s.confirm_bar < cut]
        part = analyse_mss(
            view,
            cfg,
            visible,
            swings=analyse_structure(view, cfg).swings,
            fvgs=detect_fvgs(view, cfg),
        )
        checked = 0
        for c in part.candidates:
            if c.sweep_extreme_bar + cfg.choch.max_bars_after_sweep >= cut - 1:
                continue  # window still open at the truncation: not yet decided
            ref = by_sweep[c.sweep.id]
            assert c.outcome is ref.outcome, c.sweep.id
            assert c.choch_bar == ref.choch_bar
            assert c.reference_price == ref.reference_price
            assert c.failed_clauses == ref.failed_clauses
            checked += 1
        assert checked > 20, "the truncation proved almost nothing"


def test_the_engine_reads_no_bar_beyond_the_one_it_decides_at(fixture_run):
    """Direct lookahead check: corrupting every bar after a candidate's break must not
    change that candidate."""
    cfg, h4, st, sweeps, fvgs = fixture_run
    r = analyse_mss(h4, cfg, sweeps, swings=st.swings, fvgs=fvgs)
    decided = [c for c in r.mss if c.choch_bar < h4.n - 60][:5]
    assert decided

    for c in decided:
        cut = c.choch_bar + 1
        view = h4.as_of(int(h4.close_time[cut - 1]))
        visible = [s for s in sweeps if s.confirm_bar < cut]
        again = analyse_mss(
            view,
            cfg,
            visible,
            swings=analyse_structure(view, cfg).swings,
            fvgs=detect_fvgs(view, cfg),
        )
        got = next(x for x in again.candidates if x.sweep.id == c.sweep.id)
        assert got.outcome is Outcome.MSS_CONFIRMED
        assert got.choch_bar == c.choch_bar
        assert got.reference_price == c.reference_price


def test_engine_is_deterministic(fixture_run):
    cfg, h4, st, sweeps, fvgs = fixture_run
    a = MssEngine(h4, cfg, sweeps, swings=st.swings, fvgs=fvgs).run()
    b = MssEngine(h4, cfg, sweeps, swings=st.swings, fvgs=fvgs).run()
    assert [c.id for c in a.candidates] == [c.id for c in b.candidates]
    assert a.outcomes() == b.outcomes()
    assert a.funnel() == b.funnel()
