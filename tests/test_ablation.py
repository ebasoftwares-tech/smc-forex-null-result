"""`BACKTEST_PROTOCOL.md` section 6.5 -- the ablation matrix.

Same footing as `test_falsification.py`: the deltas themselves are guaranteed nulls on this
fixture, so nothing about a delta is evidence. What is checked is that each row is the row
it claims to be, that the paired rows are genuinely paired, that the block bootstrap is the
one section 5.3 asks for, and — the part with the most leverage — that a row which measured
**nothing** is not reported as a row which measured **no effect**.

That last distinction is the study's main output and it is easy to lose: an inert toggle
and a toggle that fired without mattering produce the identical delta of 0.0000, and
section 6.5's own sentence covers both.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from bot.backtest.engine import build_market, run
from bot.config.loader import load_config
from bot.core.entries import EntryModel
from bot.core.order_blocks import ObDefinition
from bot.data.synthetic import generate
from bot.research import ablation as A
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
def base_arm(cfg, market):
    return F.arm_from(F.BASELINE, market, run(cfg, market, apply_limits=False))


def _arm(cfg_v, market):
    return F.arm_from(F.BASELINE, market, run(cfg_v, market, apply_limits=False))


# ------------------------------------------------------- the matrix is complete


def test_every_one_of_section_6_5_s_components_is_accounted_for():
    """Section 6.5 names nineteen. A component quietly missing from the matrix is the
    failure mode this test exists for -- `STATE.md` §10: a gate item that cannot be
    evaluated is reported BLOCKED or DEFERRED, never quietly skipped."""
    cov = A.coverage()
    assert set(cov) == set(A.PROTOCOL_ROWS)
    for row in A.PROTOCOL_ROWS:
        assert cov[row], f"{row} has no spec at all"


def test_no_spec_invents_a_component_the_protocol_did_not_name():
    assert {s.row for s in A.MATRIX} <= set(A.PROTOCOL_ROWS)


def test_runnable_specs_carry_overrides_and_unrunnable_ones_carry_a_reason():
    for s in A.MATRIX:
        if s.kind in (A.Kind.PAIRED, A.Kind.UNPAIRED):
            assert s.overrides, f"{s.name} is runnable with nothing to change"
        else:
            assert s.note, f"{s.name} is not runnable and does not say why"


def test_every_override_is_a_real_config_key(cfg):
    """`extra=forbid` makes a typo'd parameter raise, so this also proves each override
    actually reaches the field it names rather than silently testing the default."""
    for s in A.runnable():
        c, _ = load_config(overrides=dict(s.overrides))
        assert c is not None


def test_the_absent_rows_really_are_absent_from_the_codebase():
    """The finding, pinned. If someone implements a session filter, this test fails and
    the matrix has to be updated rather than silently continuing to report ABSENT."""
    import bot.config.schema as schema
    from pathlib import Path

    root = Path(schema.__file__).resolve().parents[2]
    # `ablation.py` documents the absence at length, so scanning it would find every term
    # this test looks for. The claim is about the engine, not about the report on it.
    py = [p for p in (root / "bot").rglob("*.py") if p.name != "ablation.py"]
    blob = "\n".join(p.read_text(encoding="utf-8") for p in py)

    # `liq.tier_confirmation_tf` -- section 6.5's "D-002 counterfactual" -- is declared in
    # the schema and read by nothing.
    assert blob.count("tier_confirmation_tf") == 1

    # `role` is read only to pick liquidity SOURCES, never to gate an entry, so the word
    # appears nowhere outside the enum that admits it as a value.
    without_enum = blob.replace(
        'Literal["liquidity", "execution", "liquidity+execution", "killzone"]', ""
    )
    assert "killzone" not in without_enum


# ------------------------------------------------------------- pairing


def test_paired_specs_do_not_touch_the_market(cfg, m1_half_year, market):
    """The whole basis of the paired comparison: if a PAIRED override changed the setup
    stream, the per-setup arrays would not line up and the delta would compare setup *i*
    of one arm with a different setup *i* of another."""
    for s in A.MATRIX:
        if s.kind is not A.Kind.PAIRED:
            continue
        c, _ = load_config(overrides=dict(s.overrides))
        rebuilt = build_market(c, m1_half_year)
        assert len(rebuilt.setups) == len(market.setups), s.name
        assert [x.sweep.id for x in rebuilt.setups] == [
            x.sweep.id for x in market.setups
        ], s.name


#: The two UNPAIRED toggles that produce a byte-identical setup stream, and why. Both are
#: findings rather than mistakes, so they are named here rather than absorbed by a count
#: threshold -- which would have swallowed a third one silently.
_KNOWN_STREAM_IDENTICAL = {
    # Raising the cap from 1.0 admits more confirmed sweeps and **no** more setups on
    # this fixture: they are removed downstream. The cap itself is far from idle -- over
    # the three report years it rejects 460 sweeps as OVER_PENETRATION at the default,
    # and those rejections reappear as ACCEPTED_THROUGH as it loosens. Which is exactly
    # why "this filter does nothing" and "this filter changes nothing" are different
    # statements, and why only the second is asserted here.
    "sweep.max_penetration_atr = 2.0",
    # Relaxing the magnitude threshold to 0 admits nothing, so `require_fvg` and
    # `min_body_ratio` already imply it. SPEC 10.6 calls them partially redundant and
    # ablates them jointly for that reason; on this fixture the redundancy is total.
    "disp.min_leg_atr = 0 (off)",
}


def test_unpaired_specs_change_the_setup_stream_except_where_recorded(
    cfg, m1_half_year, market
):
    """The converse of the paired test, and the two exceptions are the interesting part.

    A spec marked UNPAIRED that did not move the stream pays the unpaired penalty -- a
    much wider CI -- for nothing, and its delta is structurally zero. Both cases here are
    real properties of the configuration, so they are named rather than counted.
    """
    base_ids = [x.sweep.id for x in market.setups]
    identical = set()
    for s in A.MATRIX:
        if s.kind is not A.Kind.UNPAIRED:
            continue
        c, _ = load_config(overrides=dict(s.overrides))
        rebuilt = build_market(c, m1_half_year)
        if [x.sweep.id for x in rebuilt.setups] == base_ids:
            identical.add(s.name)
    assert identical == _KNOWN_STREAM_IDENTICAL


def test_paired_deltas_refuses_a_length_mismatch(base_arm):
    short = F.synthetic_arm(base_arm.per_setup[:-1])
    with pytest.raises(ValueError, match="not paired"):
        A.paired_deltas(base_arm, short)


# --------------------------------------------------------- the block bootstrap


def test_block_length_is_derived_from_the_calendar_not_the_observation_count():
    """Section 5.3 states the block in trading days. Two arms over the same calendar with
    different trade densities need different observation counts to span it."""
    assert A.block_length(100, 250) == A.block_length(400, 1000)  # same density
    assert A.block_length(400, 250) == 4 * A.block_length(100, 250)
    assert A.block_length(0, 250) == 1
    assert A.block_length(100, 0) == 1


def test_trading_days_counts_distinct_dates(market):
    td = A.trading_days_in(market.h4.close_time)
    assert 100 < td < 200  # about half a year of weekdays
    assert td == A.trading_days_in(market.h4.close_time)


def test_the_block_bootstrap_widens_the_interval_on_autocorrelated_data():
    """The reason section 5.3 requires it. An i.i.d. resample of a series with positive
    autocorrelation understates the uncertainty; if the block version did not widen the
    interval it would not be doing anything."""
    rng = np.random.default_rng(0)
    noise = rng.normal(0, 1, 600)
    x = np.convolve(noise, np.ones(25) / 25, mode="same")  # strongly autocorrelated

    iid = stats.bootstrap_ci(x, 3000, np.random.default_rng(1))
    blk = stats.bootstrap_ci(x, 3000, np.random.default_rng(1), block=25)
    assert (blk[1] - blk[0]) > 1.5 * (iid[1] - iid[0])


def test_the_two_sample_block_bootstrap_takes_a_block_per_arm():
    """They are separate because the arms rarely share a trade density."""
    rng = np.random.default_rng(2)
    a = np.convolve(rng.normal(0, 1, 400), np.ones(20) / 20, mode="same")
    b = np.convolve(rng.normal(0, 1, 400), np.ones(20) / 20, mode="same")

    iid = stats.bootstrap_diff_ci(a, b, 2000, np.random.default_rng(3))
    blk = stats.bootstrap_diff_ci(
        a, b, 2000, np.random.default_rng(3), block_a=20, block_b=20
    )
    assert (blk[1] - blk[0]) > 1.3 * (iid[1] - iid[0])


def test_paired_permutation_uses_sign_flips_not_pooling():
    """A paired ablation's exchangeable unit is the sign of each pair's difference. The
    pooled two-sample test throws the pairing away, which is the power it was for."""
    rng = np.random.default_rng(4)
    # A small, consistent, per-pair shift buried in noise many times its size -- exactly
    # the regime an ablation lives in, where the market moves far more than the toggle
    # does. Obvious paired, invisible unpaired.
    base = rng.normal(0, 3.0, 300)
    variant = base - 0.15
    d = base - variant

    p_paired = stats.paired_permutation_p(d, 2000, np.random.default_rng(5))
    p_pooled = stats.permutation_p(base, variant, 2000, np.random.default_rng(5))
    assert p_paired < 0.01
    assert p_pooled > 0.10
    # And the paired design resolves an effect an order of magnitude smaller.
    assert stats.paired_mde(d) < 0.1 * stats.minimum_detectable_effect(base, variant)


# ------------------------------------------- the degenerate rows are the finding


def test_an_inert_toggle_is_not_reported_as_no_measurable_effect(cfg, market, base_arm):
    """**The study's main output.** A toggle that changed zero setups and a toggle that
    fired without mattering produce the identical delta of 0.0000, and section 6.5's rule
    covers both with one sentence that is true of only one of them."""
    spec = next(s for s in A.MATRIX if s.name.startswith("exit.max_bars_in_trade = 60"))
    c, _ = load_config(overrides=dict(spec.overrides))
    r = A.evaluate(spec, base_arm, _arm(c, market), trading_days=130.0,
                   rng=np.random.default_rng(0), n_boot=500, n_perm=100)

    assert r.n_changed == 0
    assert r.inert
    assert r.reported_verdict == "INERT"
    assert not r.measurable and not r.measurable_gross


def test_a_variant_that_arms_nothing_is_reported_as_such(cfg, market, base_arm):
    """T3's delta would otherwise read as a small effect. It is the baseline's own
    expectancy with zero subtracted from it.

    **T3, not T2, and the distinction is the point.** T3 is dead *structurally*: its
    `tp_1` is the 1R rung against `tp.min_rr` = 1.5, so SPEC 17.2 rejects it on every
    setup at any `min_rr` above 1.0 (D-014 item 4). T2 was dead for a reason that turned
    out to be a bug (D-019) and now arms; pinning this behaviour to T2 would have made the
    test depend on a fixture coincidence rather than on a fact about the configuration.
    """
    spec = next(s for s in A.MATRIX if s.name == "tp.model = T3")
    c, _ = load_config(overrides=dict(spec.overrides))
    variant = _arm(c, market)
    r = A.evaluate(spec, base_arm, variant, trading_days=130.0,
                   rng=np.random.default_rng(0), n_boot=500, n_perm=100)

    assert variant.filled == 0
    assert r.variant_dead
    assert r.reported_verdict == "NO_TRADES"
    assert not r.measurable


def test_a_toggle_that_really_fires_is_not_flagged_degenerate(cfg, market, base_arm):
    """The positive control for the two statuses above: a live toggle must still get a
    real verdict, or INERT would just be a label for everything."""
    spec = next(s for s in A.MATRIX if s.name == "entry.model = A")
    c, _ = load_config(overrides=dict(spec.overrides))
    r = A.evaluate(spec, base_arm, _arm(c, market), trading_days=130.0,
                   rng=np.random.default_rng(0), n_boot=500, n_perm=100)

    assert r.n_changed > 0
    assert not r.inert and not r.variant_dead
    assert r.reported_verdict in {v.value for v in stats.Verdict}


# ------------------------------------------------- the order-block wiring (D-017)


def test_the_engine_reads_the_configured_ob_definition(cfg, market):
    """It was pinned to OB-A as a literal, so the four SPEC 13.2 variants -- a documented
    ABLATION and the whole subject of Phase 11 -- were unreachable through the engine.

    Asserted at entry model D, the model that actually consumes an order block.
    """
    seen = {}
    for d in ObDefinition:
        c, _ = load_config(overrides={"ob": {"definition": d.value}})
        res = run(c, market, entry_model=EntryModel.D_ORDER_BLOCK, apply_limits=False)
        seen[d] = (res.funnel["orders_armed"], len(res.trades))
    assert len(set(seen.values())) > 1, "the OB definition still reaches nothing"


def test_the_default_ob_definition_leaves_the_baseline_unchanged(cfg, market):
    """The fix must be a no-op at the shipped default, or it is not a fix but a change."""
    explicit, _ = load_config(overrides={"ob": {"definition": "last_opposing"}})
    a = run(cfg, market, entry_model=EntryModel.D_ORDER_BLOCK, apply_limits=False)
    b = run(explicit, market, entry_model=EntryModel.D_ORDER_BLOCK, apply_limits=False)
    assert [t.trade_id for t in a.trades] == [t.trade_id for t in b.trades]
    assert [t.r_net for t in a.trades] == [t.r_net for t in b.trades]


def test_the_ob_definition_is_inert_under_the_default_entry_and_stop_models(cfg, market, base_arm):
    """Fixed wiring, still nothing to ablate at the shipped defaults: entry model C reads
    an FVG and stop S1 reads the sweep extreme, so neither consumes an order block."""
    for name in ("ob.definition = B", "ob.definition = C", "ob.definition = D"):
        spec = next(s for s in A.MATRIX if s.name == name)
        c, _ = load_config(overrides=dict(spec.overrides))
        r = A.evaluate(spec, base_arm, _arm(c, market), trading_days=130.0,
                       rng=np.random.default_rng(0), n_boot=200, n_perm=50)
        assert r.inert, name


# ------------------------------------------------ evaluate() wires up what it claims
#
# Nine of the first nineteen mutations survived, and six of them were one gap: the tests
# above exercise `stats` directly, and nothing checked that `evaluate` actually *calls*
# the block bootstrap or the paired test. Swapping either for its weaker sibling changed
# only numbers no test asserted on. These spy on the wiring instead.


class _Spy:
    """Records calls to the stats primitives `evaluate` is supposed to use."""

    def __init__(self, monkeypatch):
        self.calls: list[tuple[str, dict]] = []
        for name in ("bootstrap_ci", "bootstrap_diff_ci",
                     "paired_permutation_p", "permutation_p"):
            monkeypatch.setattr(stats, name, self._wrap(name, getattr(stats, name)))

    def _wrap(self, name, fn):
        def inner(*a, **kw):
            self.calls.append((name, kw))
            return fn(*a, **kw)
        return inner

    def names(self) -> set[str]:
        return {n for n, _ in self.calls}

    def kwargs_for(self, name) -> list[dict]:
        return [kw for n, kw in self.calls if n == name]


def _evaluate_spec(name, base_arm, market, monkeypatch, *, trading_days=130.0):
    spy = _Spy(monkeypatch)
    spec = next(s for s in A.MATRIX if s.name == name)
    c, _ = load_config(overrides=dict(spec.overrides))
    mk = build_market(c, market.m1) if spec.rebuilds_market else market
    variant = F.arm_from(F.BASELINE, mk, run(c, mk, apply_limits=False))
    r = A.evaluate(spec, base_arm, variant, trading_days=trading_days,
                   rng=np.random.default_rng(0), n_boot=200, n_perm=50)
    return r, spy


def test_a_paired_row_uses_the_paired_test_and_the_block_bootstrap(
    cfg, market, base_arm, monkeypatch
):
    r, spy = _evaluate_spec("entry.model = A", base_arm, market, monkeypatch)

    assert "paired_permutation_p" in spy.names()
    assert "permutation_p" not in spy.names(), "a paired row must not pool"
    assert "bootstrap_ci" in spy.names()
    # Both currencies, both blocked, at the length the calendar implies.
    blocks = [kw.get("block") for kw in spy.kwargs_for("bootstrap_ci")]
    assert len(blocks) == 2
    assert all(b == r.block and b > 1 for b in blocks), blocks


def test_an_unpaired_row_uses_the_two_sample_block_bootstrap(
    cfg, market, base_arm, monkeypatch
):
    r, spy = _evaluate_spec("disp.require_fvg = False", base_arm, market, monkeypatch)

    assert "bootstrap_diff_ci" in spy.names()
    assert "paired_permutation_p" not in spy.names(), "the streams differ; it is not paired"
    kws = spy.kwargs_for("bootstrap_diff_ci")
    assert len(kws) == 2
    for kw in kws:
        assert kw.get("block_a", 0) > 1 and kw.get("block_b", 0) > 1


def test_each_unpaired_arm_gets_its_own_block_length(cfg, market, base_arm, monkeypatch):
    """Section 5.3 states the block in trading days, and this arm has roughly twice the
    baseline's setup count over the same calendar -- so a shared block would resample it
    over half the horizon."""
    r, spy = _evaluate_spec("disp.require_fvg = False", base_arm, market, monkeypatch)
    kw = spy.kwargs_for("bootstrap_diff_ci")[0]
    assert kw["block_a"] != kw["block_b"], (kw["block_a"], kw["block_b"])


def test_the_stationary_walk_has_variable_run_lengths(cfg):
    """The *stationary* in stationary bootstrap. A fixed block length makes the resampled
    series' dependence structure depend on where the blocks happen to land, which is the
    thing Politis & Romano's geometric lengths exist to avoid -- and a fixed-block
    implementation still widens the interval, so the widening test cannot see it."""
    idx = stats._stationary_indices(500, 10, np.random.default_rng(0))

    runs, length = [], 1
    for a, b in zip(idx, idx[1:]):
        if b == (a + 1) % 500:
            length += 1
        else:
            runs.append(length)
            length = 1
    runs.append(length)

    assert len(set(runs)) > 3, "block lengths are not varying -- this is not stationary"
    assert min(runs) == 1, "a geometric length must sometimes be 1"
    assert max(runs) > 10, "a geometric length must sometimes exceed the mean"


def test_thin_marks_a_variant_under_section_5_1s_reportability_floor():
    """*"30-99 suggestive; <30 not reportable."* A row carried by a handful of fills still
    gets a delta, because the paired design computes it over every setup."""
    def made(filled):
        x = np.zeros(200)
        x[:filled] = 0.4
        return F.synthetic_arm(x)

    spec = next(s for s in A.MATRIX if s.name == "entry.model = A")
    rng = np.random.default_rng(0)
    thin = A.evaluate(spec, made(50), made(29), trading_days=130.0, rng=rng,
                      n_boot=200, n_perm=50)
    fat = A.evaluate(spec, made(50), made(31), trading_days=130.0, rng=rng,
                     n_boot=200, n_perm=50)
    assert thin.thin and not fat.thin
    assert not A.evaluate(spec, made(50), made(0), trading_days=130.0, rng=rng,
                          n_boot=200, n_perm=50).thin, "a dead arm is NO_TRADES, not thin"


def test_a_dead_variant_is_not_measurable_even_when_its_ci_excludes_zero():
    """The guard that the fixture cannot exercise.

    On the fixture T2's CI happens to span zero, so removing the `variant_dead` check from
    `measurable` changes nothing there and a mutation deleting it survived. Constructed
    here instead: a variant that arms nothing against a baseline that always wins produces
    a CI far from zero, and reporting that as a measured effect would credit the toggle
    with the baseline's own expectancy.
    """
    base = F.synthetic_arm(np.full(300, 0.5))
    dead = F.synthetic_arm(np.zeros(300))
    spec = next(s for s in A.MATRIX if s.name == "tp.model = T2")
    r = A.evaluate(spec, base, dead, trading_days=130.0,
                   rng=np.random.default_rng(0), n_boot=500, n_perm=100)

    assert r.variant_dead
    assert r.ci_low > 0.0, "the constructed CI must exclude zero, or this proves nothing"
    assert not r.measurable and not r.measurable_gross
    assert r.reported_verdict == "NO_TRADES"
