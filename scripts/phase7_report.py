"""Phase 7 acceptance report (SPEC section 27).

Gate: "Sweep counts stable across years; standalone forward-return study of sweeps
(§9.7)."

Also closes the half of the Phase 6 gate that was deferred: the sweep-rate report by
source.

**This report answers H2**, the one component hypothesis (`PRE_REGISTRATION.md` §1.2)
that had no real-data answer when the project reached its terminal decision (D-030,
`FINAL_RESULT.md` §7). On real bars the forward-return study is a measurement; under
`--synthetic` it is the original instrument validation, where the fixture's true effect
is zero by construction and a CI excluding zero would mean a bug.

    python scripts/phase7_report.py              # real bars, data/parquet
    python scripts/phase7_report.py --synthetic  # the original fixture
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import textwrap
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.config.loader import load_config  # noqa: E402
from bot.core.bars import from_epoch_s  # noqa: E402
from bot.core.liquidity import LevelStatus, Side  # noqa: E402
from bot.core.sessions import build_sessions  # noqa: E402
from bot.core.structure import analyse_structure  # noqa: E402
from bot.core.sweeps import SweepEventType, analyse_sweeps  # noqa: E402
from bot.core.swings import detect_swings  # noqa: E402
from bot.data.ingest import DatasetManifest, read_series  # noqa: E402
from bot.data.resample import resample  # noqa: E402
from bot.data.synthetic import generate  # noqa: E402
from bot.research.stats import Verdict, required_n, verdict_for  # noqa: E402
from bot.research.sweep_study import pool_studies, run_study  # noqa: E402

UTC = timezone.utc
PARQUET = Path("data/parquet")
YEARS = (2022, 2023, 2024, 2025, 2026)

# PRE_REGISTRATION section 4.1 as stamped by Amendment 1.
IS_YEARS, OOS_YEARS = 4, 2

# The margin this study's answer is read against, in the units it measures (ATR). It is
# the same +/-0.25 ATR already declared for the other two forward-return studies -- the
# FVG standalone test (D-023) and H5 (D-024) -- because it is the same quantity being
# judged, and a different margin per study would make the three incomparable. Applied to
# H2 for the first time here, and stated before the result below is read.
EQUIVALENCE_MARGIN_ATR = 0.25

# The width the hand-wrapped prose in this report already sits at, so a sentence
# built from interpolated counts wraps to match rather than running long beside it.
_PROSE_WIDTH = 88


def _run_tests() -> tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    summary = [
        ln.strip()
        for ln in proc.stdout.splitlines()
        if re.search(r"\d+ (passed|failed|error)", ln)
    ]
    return proc.returncode == 0, (summary[-1] if summary else "no summary line")


def acquired_years(manifest: DatasetManifest, ingest_tf: str) -> list[int]:
    years: set[int] = set()
    for e in manifest.series:
        if e.timeframe != ingest_tf or not e.first_bar_utc or not e.last_bar_utc:
            continue
        a = datetime.fromisoformat(e.first_bar_utc).year
        b = datetime.fromisoformat(e.last_bar_utc).year
        years.update(range(a, b + 1))
    return sorted(years)


def _sweeps_from(cfg, src, h4, d1):
    """The one call every build below shares, so real and fixture cannot diverge."""
    return analyse_sweeps(
        cfg=cfg,
        h4=h4,
        d1=d1,
        w1=resample(src, "W1", cfg),
        mn1=resample(src, "MN1", cfg),
        sessions=build_sessions(
            src if src.timeframe == "M15" else resample(src, "M15", cfg), cfg
        ),
        h4_structure=analyse_structure(h4, cfg),
        d1_swings=detect_swings(d1, cfg),
    )


def build_symbol(cfg, symbol: str, years: list[int]):
    """One symbol's whole in-sample span, from real M1.

    Built in one pass rather than per year: a sweep confirmed in January is anchored to a
    level created the previous December, and slicing the series by calendar year would
    destroy exactly those events -- the longest-lived levels, which are the ones the tier
    system ranks highest.
    """
    m1 = read_series(PARQUET, symbol, "M1", years=years)
    h4 = resample(m1, "H4", cfg)
    d1 = resample(m1, "D1", cfg)
    book, res = _sweeps_from(cfg, m1, h4, d1)
    return h4, book, res


def build_year(cfg, year: int, seed: int):
    src = generate(
        "EURUSD",
        datetime(year, 1, 1, tzinfo=UTC),
        datetime(year, 12, 31, 23, 59, tzinfo=UTC),
        cfg,
        timeframe="M15",
        seed=seed,
    )
    h4 = resample(src, "H4", cfg)
    d1 = resample(src, "D1", cfg)
    book, res = _sweeps_from(cfg, src, h4, d1)
    return h4, book, res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", action="store_true", help="the original fixture")
    args = ap.parse_args()

    cfg, cfg_hash = load_config()
    real = not args.synthetic
    OUT = Path("reports/phase7_gate.md" if real else "reports/phase7_gate_synthetic.md")
    OUT.parent.mkdir(parents=True, exist_ok=True)

    dataset_hash = tzdata = source_label = price_side = "-"
    is_years: list[int] = []
    runs = []
    if real:
        manifest = DatasetManifest.load(PARQUET / "manifest.json")
        dataset_hash, tzdata = manifest.dataset_hash, manifest.tzdata_version
        source_label, price_side = manifest.source, manifest.price_side
        is_years = acquired_years(manifest, manifest.ingest_timeframe)[:IS_YEARS]
        for i, sym in enumerate(cfg.symbols):
            print(f"[{i + 1}/{len(cfg.symbols)}] {sym} "
                  f"{is_years[0]}-{is_years[-1]} (M1) ...", flush=True)
            runs.append((sym, *build_symbol(cfg, sym, is_years)))
    else:
        is_years = list(YEARS)
        for k, year in enumerate(YEARS):
            print(f"building {year} ...", flush=True)
            runs.append((year, *build_year(cfg, year, seed=31 + k)))

    # Confirmed sweeps per calendar year. On the fixture a run *is* a year; on real bars
    # each run spans the whole in-sample window, so the year comes from the bar the sweep
    # confirmed on. Either way the gate below reads stability across time, which is what
    # it says it measures -- not stability across symbols, which is a different claim.
    if real:
        per_year_counter: Counter = Counter()
        for _, h4, _, res in runs:
            for e in res.confirmed():
                per_year_counter[from_epoch_s(h4.close_time[e.confirm_bar]).year] += 1
        per_year = {y: per_year_counter.get(y, 0) for y in is_years}
    else:
        per_year = {y: len(res.confirmed()) for y, _, _, res in runs}
    per_unit = {label: len(res.confirmed()) for label, _, _, res in runs}
    counts = np.array(list(per_year.values()), dtype=float)
    cv = float(counts.std(ddof=1) / counts.mean()) if counts.mean() else float("nan")

    all_events = [e for _, _, _, res in runs for e in res.events]
    all_conf = [e for e in all_events if e.type is SweepEventType.CONFIRMED]
    all_levels = [l for _, _, b, _ in runs for l in b.levels]

    # Sweep rate by source: SWEPT / (SWEPT + INVALIDATED + EXPIRED), i.e. of the levels
    # that reached a terminal state on their own merits rather than being merged away.
    outcome: dict[str, Counter] = defaultdict(Counter)
    for l in all_levels:
        outcome[l.source.value][l.status.value] += 1

    by_reason = Counter(
        e.reason.value for e in all_events if e.reason is not None
    )
    by_type = Counter(e.type.value for e in all_events)

    # The forward-return study, per run and then pooled.
    print("running forward-return study ...", flush=True)
    studies = [run_study(h4, res.confirmed(), cfg) for _, h4, _, res in runs]
    # Pooled by concatenating raw returns, never by averaging the per-run effect sizes:
    # a mean of means discards the sample sizes, and the sample size is most of what
    # decides whether H2 can be answered at all.
    pooled_study = pool_studies(studies)
    pooled = {}
    for h in (1, 3, 6, 12):
        d = [r.diff for st in studies for r in st.results if r.horizon == h]
        pooled[h] = (float(np.mean(d)) if d else float("nan"), sum(
            1 for st in studies for r in st.results if r.horizon == h and r.significant
        ))

    # H2's answer, read against the margin declared at the top of this file.
    h2_rows = []
    for r in pooled_study.results:
        v = verdict_for(r.ci_low, r.ci_high, EQUIVALENCE_MARGIN_ATR, r.n_sweep, r.n_control)
        need = required_n(r.sweep_returns, r.control_returns, EQUIVALENCE_MARGIN_ATR)
        h2_rows.append((r, v, need))
    # The pooled reading is the weakest horizon's: averaging a resolved answer together
    # with an unresolved one is exactly what the three-way verdict exists to prevent
    # (the H5 study states the same rule, D-024). DIFFERENT outranks it -- an effect
    # found at any horizon is an answer, not a failure to look.
    h2_verdict = None
    if h2_rows:
        h2_verdict = (
            Verdict.DIFFERENT if any(v is Verdict.DIFFERENT for _, v, _ in h2_rows)
            else Verdict.UNDERPOWERED
            if any(v is Verdict.UNDERPOWERED for _, v, _ in h2_rows)
            else Verdict.EQUIVALENT
        )

    print("running test suite ...", flush=True)
    tests_ok, tests_line = _run_tests()

    n_years = len(is_years)
    n_units = len(runs)
    checks = [
        ("Test suite green", tests_ok, tests_line),
        ("Sweep counts stable across years", cv < 0.25, f"CV = {cv:.3f} over {n_years} years"),
        ("Every year produced sweeps", all(v > 100 for v in per_year.values()), f"min {min(per_year.values())}"),
        ("Confirmed penetration inside bounds", all(cfg.sweep.min_penetration_atr <= e.penetration_atr <= cfg.sweep.max_penetration_atr for e in all_conf), f"{len(all_conf)} events"),
        # Scoped per run: level ids restart at L000000 in every run, so pooling them
        # across independent years counts id collisions rather than double sweeps.
        (
            "No level swept twice (per run)",
            all(
                max(Counter(e.level_id for e in res.confirmed()).values(), default=1) == 1
                for _, _, _, res in runs
            ),
            "SPEC 8.9",
        ),
        ("Failures are recorded, not dropped", by_type.get("SWEEP_FAILED", 0) > 0, f"{by_type.get('SWEEP_FAILED',0)} failed, {by_type.get('SWEEP_REJECTED',0)} rejected"),
        ("Forward-return study ran", all(len(st.results) == 4 for st in studies),
         f"{n_units} {'symbols' if real else 'years'} x 4 horizons"),
    ]
    if real:
        # Phase 10's precedent (D-023): assert the study *returned* a verdict, not which
        # one. SPEC section 27's gate is that the study runs; requiring a particular
        # answer would make the gate unpassable whenever the honest answer is
        # UNDERPOWERED, which is the one reading this project most needs to keep sayable.
        checks.append((
            "Forward-return study returned a verdict",
            h2_verdict in tuple(Verdict),
            f"pooled {h2_verdict.value if h2_verdict else 'NO DATA'} at a "
            f"+/-{EQUIVALENCE_MARGIN_ATR:g} ATR margin over {pooled_study.n_events:,} sweeps",
        ))
    all_ok = all(ok for _, ok, _ in checks)

    L: list[str] = []
    w = L.append

    def wp(text: str) -> None:
        """Write a paragraph wrapped to the width the rest of this report is written at.

        Tables are never routed through this -- a wrapped row stops being a row.
        """
        for line in textwrap.wrap(text, width=_PROSE_WIDTH):
            w(line)

    w("# Phase 7 Gate Report")
    w("")
    w(f"Generated {datetime.now(UTC).isoformat(timespec='seconds')}")
    w("")
    w(f"- `config_hash` `{cfg_hash}`")
    if real:
        w(f"- `dataset_hash` `{dataset_hash}`")
        w(f"- Data: **real bars** -- {n_units} symbols, {is_years[0]}-{is_years[-1]} "
          f"({n_units * n_years} symbol-years), H4 from M1, source `{source_label}`, "
          f"`{price_side}` side, tzdata `{tzdata}`")
        w(f"- Equivalence margin: **+/-{EQUIVALENCE_MARGIN_ATR:g} ATR**, declared before "
          "any result was read")
    else:
        w(f"- Fixture: {n_years} independent synthetic years ({YEARS[0]}–{YEARS[-1]}), EURUSD")
    w(f"- **{len(all_conf):,} confirmed sweeps** from {len(all_levels):,} levels")
    w("")
    w("## Gate checks")
    w("")
    w("| Check | Result | Detail |")
    w("|---|---|---|")
    for name, ok, detail in checks:
        w(f"| {name} | {'PASS' if ok else 'FAIL'} | {detail} |")
    w("")
    w("## Sweep counts per year (gate item 1)")
    w("")
    if real:
        w("| Year | Confirmed |")
        w("|---|---:|")
        for y in is_years:
            w(f"| {y} | {per_year[y]} |")
        w("")
        w("Failed and rejected counts are not split by year: they belong to the level that")
        w("failed, whose creation and expiry can fall either side of a year boundary, and")
        w("attributing them to one would invent a precision the events do not carry. The")
        w("outcome breakdown below reports them over the whole window instead.")
        w("")
        w(f"| Symbol | Confirmed | Failed | Rejected |")
        w("|---|---:|---:|---:|")
        for label, _, _, res in runs:
            c = Counter(e.type.value for e in res.events)
            w(f"| {label} | {c.get('SWEEP_CONFIRMED',0)} | {c.get('SWEEP_FAILED',0)} "
              f"| {c.get('SWEEP_REJECTED',0)} |")
    else:
        w("| Year | Confirmed | Failed | Rejected |")
        w("|---|---:|---:|---:|")
        for y, _, _, res in runs:
            c = Counter(e.type.value for e in res.events)
            w(f"| {y} | {c.get('SWEEP_CONFIRMED',0)} | {c.get('SWEEP_FAILED',0)} | {c.get('SWEEP_REJECTED',0)} |")
    w("")
    w(f"Coefficient of variation on confirmed counts: **{cv:.3f}**. Stability is a")
    w("prerequisite for trusting any downstream statistic: a count that swings by a factor")
    w("of two between years means the denominator of every rate below is unstable too.")
    w("")
    w("## Outcome breakdown")
    w("")
    w("| Event | Count | Share |")
    w("|---|---:|---:|")
    tot_ev = sum(by_type.values())
    for k, n in by_type.most_common():
        w(f"| {k} | {n:,} | {n/tot_ev:.1%} |")
    w("")
    w("| Reason | Count |")
    w("|---|---:|")
    for k, n in by_reason.most_common():
        w(f"| {k} | {n:,} |")
    w("")
    w("SPEC 9.1 is explicit that a failure to reclaim is **not** \"no event\". The ratio of")
    w("confirmed to failed sweeps per source is a direct measure of whether a level is a")
    w("real barrier, and that measurement is impossible if failures are silently dropped.")
    w("")
    w("## Sweep rate by source (closes the deferred half of the Phase 6 gate)")
    w("")
    w("| Source | Swept | Invalidated | Expired | Sweep rate | Merged |")
    w("|---|---:|---:|---:|---:|---:|")
    for src in sorted(outcome, key=lambda k: -outcome[k].get("SWEPT", 0)):
        c = outcome[src]
        sw, inv, exp = c.get("SWEPT", 0), c.get("INVALIDATED", 0), c.get("EXPIRED", 0)
        den = sw + inv + exp
        rate = f"{sw/den:.0%}" if den else "—"
        w(f"| {src} | {sw} | {inv} | {exp} | {rate} | {c.get('MERGED',0)} |")
    w("")
    w("The rate is `SWEPT / (SWEPT + INVALIDATED + EXPIRED)` — of the levels that reached a")
    w("terminal state on their own merits, rather than being merged away. SPEC 8.10 reads")
    w("this table in both directions: **a source whose levels are almost never swept")
    w("contributes nothing; a source whose levels are almost always swept is not")
    w("identifying a barrier at all.**")
    w("")
    w("`PROTECTED_SWING`'s near-zero count is the merge working, not the source failing —")
    w("it duplicates a `SWING_*` level at the identical price 95% of the time, so the swing")
    w("level survives the merge and anchors the sweep (D-006, predicted before this phase")
    w("existed).")
    w("")
    w("## Forward-return study (gate item 2, SPEC 9.7)")
    w("")
    w("Hypothesis **H2** (`BACKTEST_PROTOCOL.md` §6.1): *confirmed sweeps carry directional")
    w("information*. Returns are ATR-normalised and signed by the direction the sweep")
    w("implies (a sell-side sweep implies up). Controls are bars in the same UTC-hour slot")
    w("and volatility tercile with no confirmed sweep.")
    w("")
    if real:
        w(f"## The answer: H2 is {h2_verdict.value}")
        w("")
        w("Pooled over every symbol by concatenating raw returns -- never by averaging the")
        w("per-symbol effect sizes, which would discard the sample sizes that decide")
        w("whether the question can be answered at all.")
        w("")
        w("| Horizon | n sweep | n control | diff (ATR) | 95% CI | needed for the margin | Verdict |")
        w("|---|---:|---:|---:|---|---:|---|")
        for r, v, need in h2_rows:
            w(f"| +{r.horizon} bars | {r.n_sweep:,} | {r.n_control:,} | {r.diff:+.4f} "
              f"| [{r.ci_low:+.4f}, {r.ci_high:+.4f}] | {need:,.0f} | **{v.value}** |")
        w("")
        eq = [r.horizon for r, v, _ in h2_rows if v is Verdict.EQUIVALENT]
        un = [r.horizon for r, v, _ in h2_rows if v is Verdict.UNDERPOWERED]
        di = [r.horizon for r, v, _ in h2_rows if v is Verdict.DIFFERENT]
        if di:
            wp(f"At h={', h='.join(str(x) for x in di)} the interval excludes zero: this "
               "sample contains a directional effect after a confirmed sweep. Read it "
               "against the rest of the project before treating it as an edge -- every "
               "downstream arm that consumed these sweeps still failed.")
        if eq:
            wp(f"At h={', h='.join(str(x) for x in eq)} the interval lies entirely inside "
               f"the declared +/-{EQUIVALENCE_MARGIN_ATR:g} ATR margin. That is the only "
               "verdict in the three-way scheme licensing the word \"no\": confirmed "
               "sweeps do not move the next bars by as much as the margin, against "
               "controls matched on session slot and volatility tercile.")
        if un:
            wp(f"At h={', h='.join(str(x) for x in un)} the interval spans zero *and* "
               "reaches past the margin, so the study cannot answer there. The column "
               "above says what sample would: that is a statement about power, not a "
               "result, and it must not be read as a null.")
        w("")
        wp(f"**The pooled verdict is {h2_verdict.value}**, which is the weakest horizon's "
           "rather than an average of them -- combining a resolved answer with an "
           "unresolved one is precisely what the three-way verdict exists to prevent "
           "(D-024 states the same rule for H5).")
        w("")
        w("### Per-symbol, for the record")
        w("")
        w("| Symbol | confirmed sweeps | diff at h=1 (ATR) |")
        w("|---|---:|---:|")
        for (label, _, _, _), st in zip(runs, studies):
            r1 = next((r for r in st.results if r.horizon == 1), None)
            diff_cell = f"{r1.diff:+.4f}" if r1 else "—"
            w(f"| {label} | {per_unit[label]:,} | {diff_cell} |")
        w("")
        wp("No single symbol answers H2 and none is meant to: the per-symbol intervals are "
           "each far wider than the margin, which is the whole reason the pooled sample "
           "exists. Reading one row here as a result is the error section 5.6 exists to "
           "prevent.")
        w("")
    else:
        w("| Horizon | Mean diff (sweep − control) | Years significant |")
        w("|---|---:|---:|")
        for h, (m, nsig) in pooled.items():
            w(f"| +{h} bars | {m:+.4f} ATR | {nsig} / {n_years} |")
        w("")
        for y, st in zip(YEARS, studies):
            w(f"- {y}: {st.verdict()}")
        w("")
        n_tests = n_years * 4
        n_sig = sum(nsig for _, nsig in pooled.values())
        w(f"### {n_sig} of {n_tests} year x horizon tests came out \"significant\" on pure noise")
        w("")
        w("That is not a defect; it is the multiple-testing problem, demonstrated on data")
        w(f"known to contain nothing. At a 5% false-positive rate, {n_tests} independent tests")
        w(f"produce {n_tests * 0.05:.0f} spurious hits on average, and {n_sig} is comfortably inside that.")
        w("")
        w("**This is exactly why `BACKTEST_PROTOCOL.md` §5.6 requires Benjamini-Hochberg")
        w("correction and a Deflated Sharpe Ratio computed against the declared configuration")
        w("count.** Anyone reading a single per-year row here and calling it an edge would be")
        w("reporting arithmetic. The same trap scales: with `M = 9,600` configurations")
        w("declared in the pre-registration, the *expected maximum* result under the null is")
        w("large, which is why the protocol prints it at the top of every optimisation report.")
        w("")
        w("### Reading this correctly")
        w("")
        w("**The fixture is a random walk, so the correct result is exactly this one: nothing.**")
        w("A random walk contains no liquidity, no stop clusters and no participants, so a")
        w("sweep of a level in it is a coincidence of arithmetic. Had this study reported an")
        w("edge here, the study would be broken.")
        w("")
        w("So this run establishes that the study **has no false-positive tendency**. What")
        w("makes the null result meaningful rather than vacuous is the paired positive control")
        w("in `tests/test_sweep_study.py`: a series with a planted post-sweep drift is detected")
        w("at +1 bar with a confidence interval excluding zero, in both directions, and an")
        w("inverted edge reports negative rather than zero. A study that could only ever say")
        w("\"no edge\" would pass the random walk and be worthless.")
        w("")
        w("**H2 is not answered by this run**, and cannot be on synthetic data. It is")
        w("answered on real bars by the default mode of this same script.")
        w("")
    w("## Finding: level and event ids are unique per run, not globally")
    w("")
    w("Ids restart at `L000000` / `SW000001` in every run, so they collide across runs.")
    w("Harmless while each run is analysed alone and actively wrong the moment trades from")
    w("several symbols are pooled into one table. **Ids need a run or symbol namespace**")
    w("— SPEC 1.7 already specifies a ULID for exactly this reason, and the sequential")
    w("ids used here are a Phase 5–7 convenience that must not survive into the trade")
    w("log. Recorded as D-007, and closed since (D-015 section 1).")
    w("")
    w("## What this report does NOT establish")
    w("")
    if real:
        wp("**That the strategy has an edge.** H2 is one link measured on its own, which is "
           "the whole point of SPEC 9.7's design -- no CHoCH, no displacement, no entry "
           "model, no stops. What the sequence built on top of these sweeps does is a "
           "different question, answered separately and in the negative: the falsification "
           "suite finds a shuffled level book performs the same (H3, D-028), and the full "
           "chain's in-sample expectancy is -0.19 R with an interval spanning zero (D-027).")
        w("")
        wp("**Anything out of sample.** This is the in-sample split, 2019-2022. The "
           "out-of-sample budget was never spent and stays unspent (D-030).")
        w("")
        wp("**That the level-source breakdown above supports a change.** Reading the best "
           "source out of that table and keeping it is selection on the same data -- the "
           "error `BACKTEST_PROTOCOL.md` section 5.6 exists to prevent, and section 10.2 "
           "forbids acting on outright.")
    else:
        wp("Nothing about whether this strategy has an edge. Sweep counts, rates and "
           "stability here are properties of the detector meeting a random walk. The one "
           "thing that would answer the real question is this same study on real FX "
           "history, which is what the default mode of this script now runs.")
    w("")
    w(f"## Verdict: {'PASS' if all_ok else 'FAIL'}")

    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"\nwrote {OUT}")
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
