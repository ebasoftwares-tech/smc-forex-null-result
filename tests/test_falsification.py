"""`BACKTEST_PROTOCOL.md` sections 6.3/6.4 -- the falsification suite.

What these tests are for, given that the arms themselves can only produce nulls on this
fixture: **the null is guaranteed here, so nothing about a null is evidence.** What can be
checked is that each arm is the arm it claims to be, that it is built by the same engines
as the baseline, and that the comparison machinery would report a difference if one
existed. That last one is the positive control, and without it the whole suite would pass
by being unable to say anything.

Two hazards get their own tests because both fail *silently*:

* **Setup-id collisions.** ``Trade.setup_id`` is the sweep id, so two setups sharing one
  would be scored as a filled trade plus a phantom 0.0 -- and would collide in ``run``'s
  own ``live`` dict. The sweepless arms mint their own ids, so this is theirs to get wrong.
* **The declared margin drifting.** ``EQUIVALENCE_MARGIN_R`` is section 10.1's own
  go/no-go threshold and was fixed before any arm was run. A test pins it to that number
  so it cannot be quietly re-chosen once a verdict is unwelcome (section 10.2).
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from bot.backtest.engine import build_market, run
from bot.core.entries import EntryModel
from bot.core.displacement import Direction
from bot.core.liquidity import Side
from bot.core.sweeps import analyse_sweeps
from bot.core.swings import SwingKind, detect_swings
from bot.core.structure import analyse_structure
from bot.core.sessions import build_sessions
from bot.data.resample import resample
from bot.data.synthetic import generate
from bot.research import falsification as F
from bot.research import stats

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
def arms(cfg, market):
    """Every non-seeded arm, built once."""
    return {
        "sweep_only": F.sweep_only(cfg, market),
        "choch_only": F.choch_only(cfg, market),
        "reversed_order": F.reversed_order(cfg, market),
    }


# ------------------------------------------------- the seam is inert when unused


def test_setup_override_is_absent_on_a_normally_built_market(market):
    """The strategy is what a market says when nothing has substituted a stream."""
    assert market.setup_override is None
    assert market.setups == [
        c for c in market.mss.candidates if c.is_choch and c.displacement.confirmed
    ]


def test_level_transform_none_is_the_identity(cfg, m1_half_year):
    """Section 6.3's seam must not perturb the arm it is a control for.

    Compared on the book rather than on a hash: the point is that the *levels* are the
    same objects with the same prices and ids, which is what everything downstream reads.
    """
    h4 = resample(m1_half_year, "H4", cfg)
    d1 = resample(m1_half_year, "D1", cfg)
    kw = dict(
        cfg=cfg, h4=h4, d1=d1, w1=resample(m1_half_year, "W1", cfg),
        mn1=resample(m1_half_year, "MN1", cfg),
        sessions=build_sessions(resample(m1_half_year, "M15", cfg), cfg),
        h4_structure=analyse_structure(h4, cfg), d1_swings=detect_swings(d1, cfg),
    )
    plain, sweeps_a = analyse_sweeps(**kw)
    passthrough, sweeps_b = analyse_sweeps(**kw, level_transform=lambda c: c)

    assert len(plain.levels) == len(passthrough.levels)
    assert [(l.id, l.price) for l in plain.levels] == [
        (l.id, l.price) for l in passthrough.levels
    ]
    assert [e.id for e in sweeps_a.confirmed()] == [e.id for e in sweeps_b.confirmed()]


# ------------------------------------------------------------ each arm is itself


def test_every_arm_produces_setups(cfg, market, arms):
    """A control with no setups tests nothing, and would report a null for that reason."""
    assert market.setups, "baseline fixture produced no setups"
    for name, mk in arms.items():
        assert mk.setups, f"{name} produced no setups"
    assert F.random_time(cfg, market, 0).setups


def test_setup_ids_are_unique_within_every_arm(cfg, market, arms):
    """``Trade.setup_id`` is the sweep id, so a duplicate is a silently lost trade."""
    populations = dict(arms)
    populations["baseline"] = market
    for seed in (0, 1, 7):
        populations[f"random_time:{seed}"] = F.random_time(cfg, market, seed)

    for name, mk in populations.items():
        ids = [s.sweep.id for s in mk.setups]
        assert len(ids) == len(set(ids)), f"{name} has duplicate setup ids"


def test_sweep_only_fires_exactly_on_sweep_confirmations(cfg, market, arms):
    confirms = {(c.sweep.confirm_bar, c.sweep.id) for c in market.mss.candidates}
    got = {(s.choch_bar, s.sweep.id) for s in arms["sweep_only"].setups}
    assert got == confirms
    # And its direction convention is the project's: a sell-side sweep is bullish.
    for s in arms["sweep_only"].setups:
        want = Direction.BULLISH if s.sweep.side is Side.SELL_SIDE else Direction.BEARISH
        assert s.direction is want


def test_choch_only_needs_no_sweep_but_does_need_displacement(cfg, market, arms):
    setups = arms["choch_only"].setups
    assert all(s.displacement.confirmed for s in setups)
    # Its sweeps are placeholders, and say so in every field that would be a measurement.
    for s in setups:
        assert np.isnan(s.sweep.penetration_atr)
        assert s.sweep.level_tier == 0
        assert s.sweep.level_strength == 0


def test_choch_only_fires_once_per_reference(cfg, market, arms):
    """Without a sweep window nothing else stops three consecutive bars re-breaking the
    same swing and reporting three setups where the baseline sees one."""
    seen = [(s.direction, s.reference_price) for s in arms["choch_only"].setups]
    assert len(seen) == len(set(seen))


def test_choch_only_uses_the_baselines_own_reference_selection(cfg, market, arms):
    """Not ``structure.py``'s CHOCH events, which are a stricter and different thing.

    A structure CHoCH is a trend flip through the *protected* level; SPEC 11.2's is a
    break of the *last unbroken swing*. Building the arm on the former would make it
    differ from the baseline in the definition of the thing under test, and its null
    would be read as being about the sweep requirement.

    Asserted on *what the arm broke*, not on how many events it found: the two counts are
    the same order of magnitude and which is larger flips between fixtures, so a count
    comparison would pass a wrong implementation about half the time.
    """
    from bot.core.structure import EventType

    setups = arms["choch_only"].setups
    structural_bars = {
        e.bar_index for e in market.structure.events if e.type is EventType.CHOCH
    }
    fired_bars = {s.choch_bar for s in setups}
    assert fired_bars - structural_bars, (
        "every trigger coincides with a structure CHOCH -- this arm is built on the "
        "wrong event type"
    )

    # And every reference it broke is a swing that was *visible at its own leg extreme*
    # -- which is both the property that makes this the baseline's selection rule and
    # the reason the check cannot be made against ``store.swings``.  Normalisation
    # deletes a superseded swing from every earlier bar too, so the final store is
    # missing references that genuinely existed when the engine read them (D-009 section
    # 4, ``STATE.md`` section 6 item 14).  Asserting against it fails here, on real data,
    # for exactly that reason.
    store = market.structure.swings
    for s in setups:
        kind = SwingKind.HIGH if s.direction is Direction.BULLISH else SwingKind.LOW
        visible = {round(sw.price, 10) for sw in store.visible_at(s.sweep_extreme_bar, kind)}
        assert round(s.reference_price, 10) in visible


def test_reversed_order_really_is_reversed(cfg, market, arms):
    """Every setup has a displaced CHoCH strictly *before* the sweep it enters on."""
    prior = {Direction.BULLISH: [], Direction.BEARISH: []}
    for s in F.choch_only_setups(cfg, market):
        prior[s.direction].append(s.choch_bar)

    lo, hi = cfg.choch.min_bars_after_sweep, cfg.choch.max_bars_after_sweep
    assert arms["reversed_order"].setups
    for s in arms["reversed_order"].setups:
        b = s.choch_bar
        qualifying = [cb for cb in prior[s.direction] if b - hi <= cb <= b - lo]
        assert qualifying, "a setup with no CHoCH before it is not this control"
        assert max(qualifying) < b


def test_reversed_order_is_a_subset_of_sweep_only(cfg, arms):
    """It is sweep-only plus a requirement, so it can only ever be smaller."""
    sweeps = {s.sweep.id for s in arms["sweep_only"].setups}
    rev = {s.sweep.id for s in arms["reversed_order"].setups}
    assert rev <= sweeps
    assert len(rev) < len(sweeps)


# ------------------------------------------------------------------ random-time


def test_random_time_matches_session_and_volatility(cfg, market):
    """The matching *is* the control. A floor drawn from all bars regardless of session
    would be beaten by the baseline for the trivial reason that the baseline does not
    trade the Asian range."""
    atr = market.atr
    finite = np.isfinite(atr) & (atr > 0)
    lo, hi = (float(x) for x in np.quantile(atr[finite], [1 / 3, 2 / 3]))

    def bucket(i):
        a = atr[i]
        return (
            market.sessions_by_bar.get(i, "OTHER"),
            0 if not np.isfinite(a) else int(a > lo) + int(a > hi),
        )

    drawn = F.random_time(cfg, market, 3).setups
    base = market.setups
    assert drawn

    # Each draw inherits its baseline setup's direction, so the mix can only shrink,
    # never shift -- a floor that was net long against a baseline that was net short
    # would be measuring direction rather than timing.
    from collections import Counter

    got, want = Counter(s.direction for s in drawn), Counter(s.direction for s in base)
    assert all(got[k] <= want[k] for k in got)

    # Every draw sits in a (session, ATR tercile) bucket the baseline actually used.
    assert {bucket(s.choch_bar) for s in drawn} <= {bucket(s.choch_bar) for s in base}


def test_random_time_is_deterministic_per_seed_and_varies_across_them(cfg, market):
    a = [s.choch_bar for s in F.random_time(cfg, market, 5).setups]
    b = [s.choch_bar for s in F.random_time(cfg, market, 5).setups]
    c = [s.choch_bar for s in F.random_time(cfg, market, 6).setups]
    assert a == b
    assert a != c


def test_random_time_never_draws_the_same_bar_twice_in_one_direction(cfg, market):
    for seed in (0, 2, 11):
        pairs = [(s.choch_bar, s.direction) for s in F.random_time(cfg, market, seed).setups]
        assert len(pairs) == len(set(pairs))


# ---------------------------------------------------------------- the shuffle


@pytest.fixture(scope="module")
def shuffle_pair(cfg, m1_half_year, market):
    h4 = market.h4
    d1 = resample(m1_half_year, "D1", cfg)
    from bot.core.liquidity import build_candidates

    real = build_candidates(
        cfg=cfg, h4=h4, d1=d1, w1=resample(m1_half_year, "W1", cfg),
        mn1=resample(m1_half_year, "MN1", cfg),
        sessions=build_sessions(resample(m1_half_year, "M15", cfg), cfg),
        h4_structure=market.structure, d1_swings=detect_swings(d1, cfg),
    )
    rng = np.random.default_rng(0)
    return real, F.shuffle_levels(real, h4, market.atr, rng)


def test_the_shuffle_preserves_everything_except_where_a_level_is(shuffle_pair):
    """Section 6.3: same count per day, same age, same side -- a random price.

    Count and timing are held *exactly* rather than in distribution, which is stronger
    than the protocol asks and removes a whole class of confound: any difference between
    the arms cannot be that one of them had more levels, or older ones.
    """
    real, fake = shuffle_pair
    assert len(real) == len(fake)
    for a, b in zip(real, fake):
        assert (a.side, a.source, a.tier, a.timeframe) == (b.side, b.source, b.tier, b.timeframe)
        assert (a.confirmed_at, a.formed_at, a.strength) == (b.confirmed_at, b.formed_at, b.strength)
    moved = sum(1 for a, b in zip(real, fake) if a.price != b.price)
    assert moved > 0.9 * len(real), "the shuffle barely moved anything"


def test_the_shuffle_redraws_ids_because_price_is_part_of_the_key(shuffle_pair):
    """D-015 section 1: a level's id is content-addressed. Keeping it at a new price
    would give two different levels the same id across the two arms."""
    real, fake = shuffle_pair
    for a, b in zip(real, fake):
        if a.price != b.price:
            assert a.id != b.id


def test_the_shuffle_keeps_the_per_side_distance_distribution(shuffle_pair, market):
    """Drawn per side and signed. Losing that would test whether levels are on the
    correct side of the market -- an easier question the real book wins trivially."""
    real, fake = shuffle_pair
    h4, atr = market.h4, market.atr

    def distances(levels, side):
        out = []
        for l in levels:
            if l.side is not side:
                continue
            i = int(np.searchsorted(h4.close_time, l.confirmed_at.timestamp(), side="left"))
            i = min(max(i, 0), h4.n - 1)
            a = float(atr[i])
            if np.isfinite(a) and a > 0:
                out.append((l.price - float(h4.close[i])) / a)
        return np.array(out)

    for side in (Side.BUY_SIDE, Side.SELL_SIDE):
        r, f = distances(real, side), distances(fake, side)
        assert r.size and f.size
        # Same pool, resampled: the means agree to well inside the pool's own spread.
        assert abs(r.mean() - f.mean()) < 0.5 * r.std()


def test_the_shuffled_arm_is_rebuilt_by_the_real_engines(cfg, m1_half_year, market):
    """Not patched afterwards: sweeps, structure and MSS are all re-derived, so the arm
    differs from the baseline in the level book and in nothing else."""
    shuffled = F.build_shuffled_market(cfg, m1_half_year, market, 0)
    assert shuffled.levels_created == market.levels_created  # count held exactly
    assert shuffled.sweeps_confirmed != market.sweeps_confirmed  # different prices
    assert shuffled.setup_override is None  # a real stream, not a substituted one
    assert shuffled.h4.n == market.h4.n


# ------------------------------------------------- the arms run through run()


def test_every_arm_runs_through_the_unmodified_engine(cfg, market, arms):
    """The controls are a setup stream, not a second engine. If any arm needed a
    different ``run``, none of them would be comparable to the baseline."""
    for name, mk in arms.items():
        res = run(cfg, mk, entry_model=EntryModel.A_MARKET, apply_limits=False)
        assert res.funnel["setups"] == len(mk.setups), name
        assert res.trades, f"{name} produced no trades at model A"


def test_arm_from_scores_an_unfilled_setup_as_zero_not_as_missing(cfg, market, arms):
    """Section 4.4's shared denominator. Dropping unfilled setups instead is D-013
    section 5's trap: a model that fills on the best-looking third looks better."""
    mk = arms["choch_only"]
    res = run(cfg, mk, entry_model=EntryModel.C_FVG, apply_limits=False)
    arm = F.arm_from(F.BY_NAME["choch_only"], mk, res)
    assert arm.per_setup.size == len(mk.setups)
    assert arm.per_trade.size == len(res.trades)
    assert arm.per_setup.size > arm.per_trade.size, "fixture needs some unfilled setups"
    assert np.count_nonzero(arm.per_setup == 0.0) >= arm.per_setup.size - arm.per_trade.size


def test_the_default_entry_model_cannot_run_two_of_the_four_sequence_controls(cfg, arms):
    """A structural finding, pinned so that "fixing" it has to be deliberate.

    ``sweep_only`` and ``reversed_order`` enter at the sweep *confirmation*, so their leg
    spans at most ``sweep.max_confirmation_bars`` and is usually zero bars long. An FVG
    needs three bars. Entry model C -- the shipped default -- therefore rejects every
    setup in both arms with ``NO_FVG_AVAILABLE``, on any data, and section 10.1's
    falsification row cannot be evaluated at that default.
    """
    for name in ("sweep_only", "reversed_order"):
        res = run(cfg, arms[name], entry_model=EntryModel.C_FVG, apply_limits=False)
        assert res.funnel["orders_armed"] == 0, name
        assert {r.reason for r in res.rejections} == {"NO_FVG_AVAILABLE"}, name
        legs = [s.choch_bar - s.sweep_extreme_bar for s in arms[name].setups]
        assert max(legs) <= cfg.sweep.max_confirmation_bars


# ------------------------------------------------------- the comparison layer


def test_the_declared_margin_is_the_protocols_own_go_no_go_threshold():
    """Section 10.1 requires expectancy >= +0.10 R to trade this live. Fixed before any
    arm was run; changing it after seeing a verdict selects the answer (section 10.2)."""
    assert F.EQUIVALENCE_MARGIN_R == 0.10


def test_compare_detects_a_real_difference_and_gets_its_sign_right():
    """**The positive control.** A suite that could only ever report "no difference"
    would pass this fixture and be worthless.

    Note what is and is not covered: this exercises the comparison layer, and the tests
    above exercise each arm's construction. An end-to-end control -- an injected edge
    surviving the whole chain -- would need a price series with a real conditional SMC
    edge, which is exactly the fixture this project does not have (``STATE.md`` section 8).
    """
    rng = np.random.default_rng(0)
    strong = F.synthetic_arm(np.full(400, 0.60))
    flat = F.synthetic_arm(np.zeros(400))
    c = F.compare(strong, flat, rng=rng, n_boot=2_000, n_perm=500)

    assert c.verdict == stats.Verdict.DIFFERENT.value
    assert c.delta == pytest.approx(0.60)
    assert c.baseline_beats, "a real difference must satisfy section 10.1's row"


def test_compare_reports_underpowered_rather_than_equivalent_when_it_cannot_tell():
    """``UNDERPOWERED`` is not ``EQUIVALENT``. Only an interval sitting *inside* the
    margin licenses "this component contributes nothing" -- the H5 lesson (D-010)."""
    rng = np.random.default_rng(1)
    noisy_a = F.synthetic_arm(rng.normal(0.0, 2.0, 40))
    noisy_b = F.synthetic_arm(rng.normal(0.0, 2.0, 40))
    c = F.compare(noisy_a, noisy_b, rng=np.random.default_rng(2), n_boot=2_000, n_perm=500)

    assert c.verdict == stats.Verdict.UNDERPOWERED.value
    assert not c.baseline_beats
    assert c.mde > F.EQUIVALENCE_MARGIN_R, "a wide sample must admit it cannot resolve"


def test_compare_can_reach_equivalent_when_the_sample_is_tight_enough():
    """The verdict that licenses a null must be reachable, or the three-way reading is a
    two-way one wearing a third label."""
    rng = np.random.default_rng(3)
    a = F.synthetic_arm(rng.normal(0.0, 0.05, 4_000))
    b = F.synthetic_arm(rng.normal(0.0, 0.05, 4_000))
    c = F.compare(a, b, rng=np.random.default_rng(4), n_boot=2_000, n_perm=200)
    assert c.verdict == stats.Verdict.EQUIVALENT.value


def test_pooling_seeds_adds_observations_not_independence(cfg, market):
    """Documented rather than assumed: the pooled ``n`` is 20 correlated draws over one
    price series, so its CI is optimistic and the across-seed spread is the honest
    uncertainty."""
    spec = F.BY_NAME["random_time"]
    made = []
    for seed in (0, 1, 2):
        mk = F.random_time(cfg, market, seed)
        made.append(F.arm_from(spec, mk, run(cfg, mk, entry_model=EntryModel.A_MARKET,
                                             apply_limits=False), seed=seed))
    p = F.pooled(spec, made)
    assert p.n_setups == sum(a.n_setups for a in made)
    assert p.per_setup.size == p.n_setups
    assert p.seed is None


# ------------------------------------- guards nothing in the fixture reaches
#
# Three mutations survived the first pass of the mutation check, and all three were the
# same shape: a rule that is correct, load-bearing, and never exercised by a fixture that
# happens not to reach it.  D-014 section 8 and D-015 named this pattern twice already --
# *when a rule is enforced somewhere the front door never goes, no test that comes in
# through the front door can see it*.  These three go in the side door.


def test_a_placeholder_sweep_id_separates_two_setups_sharing_a_leg_extreme():
    """Caught nothing on the fixture, because no two legs there happen to coincide.

    They can: ``choch_only`` scans every bar, and two references broken three bars apart
    can share a leg extreme.  The cost of the collision is silent and doubled --
    ``arm_from`` credits one setup's R and scores its twin 0.0, and ``run``'s ``live``
    dict loses a position to the overwritten key.
    """
    common = dict(
        symbol="EURUSD", timeframe="H4", direction=Direction.BULLISH,
        extreme_bar=100, extreme_price=1.2345,
        at=datetime(2026, 3, 1, tzinfo=UTC), control="choch_only",
    )
    a = F.placeholder_sweep(**common, trigger_bar=104)
    b = F.placeholder_sweep(**common, trigger_bar=107)
    assert a.id != b.id
    # ...and is still deterministic, which is the property the id scheme exists for.
    assert a.id == F.placeholder_sweep(**common, trigger_bar=104).id


def test_leg_extreme_takes_the_low_for_a_bullish_leg_and_the_high_for_a_bearish_one(cfg, market):
    """The stop anchor.  Inverting it points every stop the wrong way, and the arms still
    produce trades and still report a null -- so only a direct test sees it."""
    h4 = market.h4
    for b in (200, 350, 500):
        lo = max(0, b - cfg.disp.max_leg_bars + 1)
        bull_bar, bull_price = F._leg_extreme(h4, b, Direction.BULLISH, cfg)
        bear_bar, bear_price = F._leg_extreme(h4, b, Direction.BEARISH, cfg)

        assert bull_price == pytest.approx(float(h4.low[lo : b + 1].min()))
        assert bear_price == pytest.approx(float(h4.high[lo : b + 1].max()))
        assert bull_price == pytest.approx(float(h4.low[bull_bar]))
        assert bear_price == pytest.approx(float(h4.high[bear_bar]))
        assert lo <= bull_bar <= b and lo <= bear_bar <= b


def test_the_reference_distance_cap_is_live_code_the_default_never_reaches(cfg, market):
    """SPEC 11.1's ``REFERENCE_TOO_FAR``, applied in ``choch_only`` as the baseline does.

    At the FROZEN default of 3.0 it rejects **nothing** on this fixture -- the widest
    reference sits at about 2.8 ATR -- so a mutation deleting it changes no number and
    every other test still passes.  That is a measurement, not an arithmetic
    impossibility like D-014's four unreachable defaults: it is an ABLATION parameter
    over {2.0, 3.0, 4.0}, and **at 2.0 it binds hard**.  Exercising it through that value
    is what makes the branch tested rather than merely present.
    """
    from bot.config.loader import load_config

    tight, _ = load_config(overrides={"choch": {"max_reference_distance_atr": 2.0}})
    wide, _ = load_config(overrides={"choch": {"max_reference_distance_atr": 4.0}})

    n_default = len(F.choch_only_setups(cfg, market))
    n_tight = len(F.choch_only_setups(tight, market))
    n_wide = len(F.choch_only_setups(wide, market))

    assert n_tight < n_default, "the cap must bind at 2.0, or the branch is dead code"
    assert n_default == n_wide, "the default already rejects nothing on this fixture"


# ------------------------------------------- R is a ratio, and the arms differ in it


def test_arms_entering_at_the_sweep_have_a_much_tighter_stop_than_the_baseline(cfg, market, arms):
    """The mechanism behind the study's main finding, pinned as a measurement.

    ``sweep_only`` and ``reversed_order`` enter at the sweep confirmation, so their stop
    sits just beyond an extreme a bar or two old; the baseline waits for a CHoCH and its
    stop sits beyond an extreme up to twelve bars back. That is roughly a factor of two
    in stop width -- and therefore a factor of two in what a fixed spread and commission
    cost **per R**, because R divides by exactly that distance.
    """
    def median_sl(mk):
        res = run(cfg, mk, entry_model=EntryModel.A_MARKET, apply_limits=False)
        return float(np.median([t.sl_distance_atr for t in res.trades]))

    base = median_sl(market)
    for name in ("sweep_only", "reversed_order"):
        assert median_sl(arms[name]) < 0.65 * base, name
    # The arm that also waits for a structure break keeps the baseline's geometry.
    assert median_sl(arms["choch_only"]) > 0.8 * base


def test_compare_reports_both_currencies_so_a_cost_win_cannot_pass_as_signal(cfg, market, arms):
    """Section 10.1's row is stated in expectancy, which is **net** R. An arm with a
    tighter stop loses more of its R to a fixed cost, so the row can be cleared on stop
    width alone. ``gross_delta`` is the same comparison in cost-free R, and
    ``cost_explains_it`` names the case where the two disagree."""
    spec_b, spec_c = F.BASELINE, F.BY_NAME["sweep_only"]
    b = F.arm_from(spec_b, market,
                   run(cfg, market, entry_model=EntryModel.A_MARKET, apply_limits=False))
    c = F.arm_from(spec_c, arms["sweep_only"],
                   run(cfg, arms["sweep_only"], entry_model=EntryModel.A_MARKET,
                       apply_limits=False))
    cmp_ = F.compare(b, c, rng=np.random.default_rng(0), n_boot=2_000, n_perm=200)

    # Both currencies are populated and they are genuinely different numbers.
    assert np.isfinite(cmp_.delta) and np.isfinite(cmp_.gross_delta)
    assert cmp_.delta != pytest.approx(cmp_.gross_delta)
    # Costs take strictly more out of the tighter-stopped arm, per setup.
    assert abs(c.cost_r_per_setup) > abs(b.cost_r_per_setup)
    # ...and on this fixture that is the whole of the baseline's apparent advantage.
    assert cmp_.delta > cmp_.gross_delta
