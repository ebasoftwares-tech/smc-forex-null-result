"""The Phase 9 funnel (SPEC 11.7).

The funnel produces the single number the whole design decision rests on, so the ways
it can be quietly wrong matter more than usual: counting one opportunity several times,
counting a candidate the data ran out on as a failure, or scaling a rate without saying
what it was measured over.  Each has a test here.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bot.config.loader import load_config
from bot.core.fvg import detect_fvgs
from bot.core.liquidity import LevelSource, Side
from bot.core.mss import Clause, MssResult, Outcome, ReferenceMode, SetupCandidate
from bot.core.displacement import Direction
from bot.core.sessions import build_sessions
from bot.core.structure import analyse_structure
from bot.core.sweeps import SweepCluster, analyse_sweeps
from bot.core.swings import detect_swings
from bot.data.resample import resample
from bot.data.synthetic import generate
from bot.research import funnel as F

from tests.test_mss import mk_sweep, make, BULLISH_TAIL

UTC = timezone.utc


@pytest.fixture(scope="module")
def built():
    cfg, _ = load_config()
    out = []
    for k, year in enumerate((2024, 2025)):
        src = generate(
            "EURUSD",
            datetime(year, 1, 1, tzinfo=UTC),
            datetime(year, 12, 31, 23, 59, tzinfo=UTC),
            cfg,
            timeframe="M15",
            seed=41 + k,
        )
        h4 = resample(src, "H4", cfg)
        d1 = resample(src, "D1", cfg)
        st = analyse_structure(h4, cfg)
        book, sweeps = analyse_sweeps(
            cfg=cfg,
            h4=h4,
            d1=d1,
            w1=resample(src, "W1", cfg),
            mn1=resample(src, "MN1", cfg),
            sessions=build_sessions(src, cfg),
            h4_structure=st,
            d1_swings=detect_swings(d1, cfg),
        )
        out.append((year, h4, book, sweeps, st, detect_fvgs(h4, cfg)))
    return cfg, out


@pytest.fixture(scope="module")
def major(built):
    cfg, rows = built
    runs = [
        F.build(
            symbol="EURUSD",
            year=y,
            cfg=cfg,
            h4=h4,
            book=book,
            sweeps=sw,
            structure=st,
            fvgs=fv,
            mode=ReferenceMode.MAJOR,
        )
        for y, h4, book, sw, st, fv in rows
    ]
    return cfg, F.pool(runs, ReferenceMode.MAJOR)


# ------------------------------------------------------------------ stage shape


def test_the_event_chain_is_monotone_and_the_level_stages_are_separate(major):
    """Levels and events are different units and are not nested (SPEC 9.1): one level
    can trigger a rejected poke and later a real sweep.  Asserting one descending chain
    across all seven stages would fail on correct output."""
    _, f = major
    s = f.stages(per_cluster=True)
    for a, b in zip(F.EVENT_STAGES, F.EVENT_STAGES[1:]):
        assert s[a] >= s[b], f"{a} -> {b}"
    assert s["levels_created"] >= s["levels_swept_or_tested"]
    assert s["sweeps_triggered"] > s["levels_swept_or_tested"], (
        "if no level ever triggered twice, the split would be untestable here"
    )


def test_clustering_never_inflates_a_stage(major):
    _, f = major
    per_sweep = f.stages()
    per_cluster = f.stages(per_cluster=True)
    for stage in F.EVENT_STAGES[1:]:
        assert per_cluster[stage] <= per_sweep[stage]


# ------------------------------------------------- SPEC 9.4: one opportunity, once


def _candidate(cfg, series, sweep, outcome, **kw) -> SetupCandidate:
    return SetupCandidate(
        id=f"C:{sweep.id}",
        symbol="EURUSD",
        timeframe="H4",
        direction=Direction.BULLISH,
        reference_mode=ReferenceMode.MAJOR,
        sweep=sweep,
        sweep_extreme_bar=sweep.sweep_extreme_bar,
        window_first_bar=sweep.confirm_bar + 1,
        window_last_bar=sweep.confirm_bar + 12,
        outcome=outcome,
        **kw,
    )


def test_a_cluster_of_stacked_levels_counts_once(cfg):
    """SPEC 9.4.  Three stacked levels swept by one bar are ONE opportunity; counting
    them separately triples correlated risk while looking like triple the sample."""
    series = make(BULLISH_TAIL)
    sweeps = [
        mk_sweep(
            series,
            level_price=1.07400 - i * 0.0002,
            sweep_extreme=1.07300,
            sweep_extreme_bar=23,
            ident=f"SW:{i}",
        )
        for i in range(3)
    ]
    cluster = SweepCluster(
        id="CL:1", at=sweeps[0].at, confirm_bar=23, side=Side.SELL_SIDE, events=list(sweeps)
    )
    res = MssResult("EURUSD", "H4", ReferenceMode.MAJOR)
    res.candidates = [
        _candidate(cfg, series, sweeps[0], Outcome.CHOCH_TIMEOUT),
        _candidate(cfg, series, sweeps[1], Outcome.MSS_CONFIRMED, choch_bar=25),
        _candidate(cfg, series, sweeps[2], Outcome.CHOCH_NOT_MSS, choch_bar=25,
                   failed_clauses=(Clause.DISPLACEMENT,)),
    ]
    sy = F.SymbolYear(
        symbol="EURUSD", year=2024, mode=ReferenceMode.MAJOR, bars=series.n,
        levels_created=3, levels_swept_or_tested=3, sweeps_triggered=3,
        sweeps_confirmed=3, result=res, clusters=[cluster],
    )
    dedup = sy.deduplicated()
    assert len(dedup) == 1
    assert dedup[0].outcome is Outcome.MSS_CONFIRMED  # the best outcome represents it
    assert sy.stages(per_cluster=True)["mss"] == 1
    assert sy.stages()["mss"] == 1  # only one of the three was an MSS anyway
    assert sy.stages()["sweeps_confirmed"] == 3


def test_a_sweep_in_no_cluster_is_its_own_opportunity(cfg):
    series = make(BULLISH_TAIL)
    a = mk_sweep(series, level_price=1.074, sweep_extreme=1.073, sweep_extreme_bar=23, ident="SW:a")
    b = mk_sweep(series, level_price=1.075, sweep_extreme=1.0735, sweep_extreme_bar=24, ident="SW:b")
    res = MssResult("EURUSD", "H4", ReferenceMode.MAJOR)
    res.candidates = [
        _candidate(cfg, series, a, Outcome.MSS_CONFIRMED, choch_bar=25),
        _candidate(cfg, series, b, Outcome.MSS_CONFIRMED, choch_bar=25),
    ]
    sy = F.SymbolYear(
        symbol="EURUSD", year=2024, mode=ReferenceMode.MAJOR, bars=series.n,
        levels_created=2, levels_swept_or_tested=2, sweeps_triggered=2,
        sweeps_confirmed=2, result=res, clusters=[],
    )
    assert len(sy.deduplicated()) == 2


# --------------------------------------------------------------- right censoring


def test_a_candidate_the_data_ran_out_on_is_not_a_failure(cfg):
    """Folding NO_WINDOW into the denominator understates every conversion rate, and
    the gate is a conversion rate scaled up."""
    series = make(BULLISH_TAIL)
    good = mk_sweep(series, level_price=1.074, sweep_extreme=1.073, sweep_extreme_bar=23, ident="SW:g")
    cut = mk_sweep(series, level_price=1.075, sweep_extreme=1.0735, sweep_extreme_bar=25, ident="SW:c")
    res = MssResult("EURUSD", "H4", ReferenceMode.MAJOR)
    res.candidates = [
        _candidate(cfg, series, good, Outcome.MSS_CONFIRMED, choch_bar=25),
        _candidate(cfg, series, cut, Outcome.NO_WINDOW),
    ]
    sy = F.SymbolYear(
        symbol="EURUSD", year=2024, mode=ReferenceMode.MAJOR, bars=series.n,
        levels_created=2, levels_swept_or_tested=2, sweeps_triggered=2,
        sweeps_confirmed=2, result=res, clusters=[],
    )
    f = F.Funnel(ReferenceMode.MAJOR, [sy])
    assert len(f.decided()) == 1
    assert f.conversion()["sweep_to_mss"] == pytest.approx(1.0)
    assert "NO_WINDOW" not in f.outcomes()


def test_no_window_candidates_exist_on_the_fixture(major):
    """The censoring rule has to be reachable, or the test above is hypothetical."""
    _, f = major
    assert any(c.outcome is Outcome.NO_WINDOW for c in f.candidates)
    assert len(f.decided()) < len(f.deduplicated)


# --------------------------------------------------------- clause accounting


def test_sole_cause_is_never_greater_than_the_independent_count(major):
    """The two columns answer different questions: how often a clause fires, and how
    often it is the only thing in the way.  Reporting only the first would credit each
    clause with the others' work -- the same trap as Phase 8's joint FVG ablation."""
    _, f = major
    fires = f.clause_failures()
    for cl in Clause:
        assert f.sole_cause(cl) <= fires.get(cl.value, 0)


def test_clause_failures_are_recorded_for_every_choch_that_missed(major):
    _, f = major
    for c in f.decided():
        assert bool(c.failed_clauses) == (c.outcome is Outcome.CHOCH_NOT_MSS)
        if c.is_mss:
            assert not c.failed_clauses


# -------------------------------------------------------------------- projection


def test_projection_is_a_rate_times_a_stated_universe(major):
    _, f = major
    p = f.project(symbols=10, years=4)
    assert p["mss_per_symbol_year"] == pytest.approx(f.mss_count() / f.symbol_years())
    assert p["universe"] == pytest.approx(p["mss_per_symbol_year"] * 10 * 4)
    assert p["development_set"] == pytest.approx(p["mss_per_symbol_year"] * 3 * 4)
    assert p["universe"] > p["development_set"]


def test_projection_scales_linearly(major):
    _, f = major
    a = f.project(symbols=10, years=2)["universe"]
    b = f.project(symbols=10, years=4)["universe"]
    assert b == pytest.approx(2 * a)


# ---------------------------------------------------------------- breakdowns


def test_breakdowns_partition_the_decided_population(major):
    _, f = major
    decided = len(f.decided())
    for attr in ("level_tier", "level_source", "side"):
        rows = f.by(attr)
        assert sum(d for d, _, _ in rows.values()) == decided
        assert sum(m for _, m, _ in rows.values()) == f.mss_count()


def test_per_month_sums_to_the_decided_population(major):
    _, f = major
    rows = f.per_month()
    assert sum(sw for sw, _, _ in rows.values()) == len(f.decided())
    assert sum(m for _, _, m in rows.values()) == f.mss_count()
    assert all(ch >= m for _, ch, m in rows.values())  # MSS is a subset of CHoCH


def test_window_edge_share_reports_zero_rather_than_dividing_by_zero(cfg):
    empty = F.Funnel(ReferenceMode.MICRO, [])
    assert empty.window_edge_share(cfg) == 0.0
    assert empty.conversion()["sweep_to_mss"] == 0.0
    assert F.median_or_none([]) is None
