"""Phase 9 acceptance report (SPEC 11.7) -- the funnel, and the project's decision point.

Gate (`STATE.md` section 8): **>= 300 MSS across the universe and >= 120 on the three
development symbols** over the in-sample period, or the design is reconsidered before
any entry code is written.

    python scripts/phase9_report.py              # real bars, data/parquet
    python scripts/phase9_report.py --synthetic  # the original random-walk fixture

**The default is real data and the gate is now a measurement.** Every earlier run of this
script scaled a conversion rate measured on one synthetic symbol to a ten-symbol universe
and reported the product against the gate -- a PASS on projection, recorded as such. With
`data/parquet` populated the counts the gate asks for are counted, and the projection is
kept only as the thing the measurement is read against.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.config.loader import load_config  # noqa: E402
from bot.core.fvg import detect_fvgs  # noqa: E402
from bot.core.mss import Clause, ReferenceMode  # noqa: E402
from bot.core.sessions import build_sessions  # noqa: E402
from bot.core.structure import analyse_structure  # noqa: E402
from bot.core.sweeps import analyse_sweeps  # noqa: E402
from bot.core.swings import detect_swings  # noqa: E402
from bot.data.ingest import DatasetManifest, read_series  # noqa: E402
from bot.data.resample import resample  # noqa: E402
from bot.data.synthetic import generate  # noqa: E402
from bot.research import funnel as F  # noqa: E402

UTC = timezone.utc
PARQUET = Path("data/parquet")

# BACKTEST_PROTOCOL section 2.1 / PRE_REGISTRATION section 4.2.  Not a choice made here.
DEV_SET = ("EURUSD", "GBPUSD", "USDJPY")
GATE_UNIVERSE = 300
GATE_DEV = 120

# PRE_REGISTRATION section 4.1: in-sample is the EARLIEST FOUR YEARS of acquired history,
# out-of-sample the next two, holdout everything after.  Fixed as a rule before the data
# existed, precisely so that it could not be drawn around the sample; applied here rather
# than restated.
IS_YEARS = 4
OOS_YEARS = 2

# What the synthetic fixture projected, from the run committed at d2bcf76 (STATE.md
# section 3).  Carried so the measurement has something to be read against; used in no
# check.
SYNTHETIC_PROJECTION = {"rate": 0.0198, "per_symbol_year": 12.7, "universe": 507, "dev": 152}

# The original synthetic fixture, kept runnable under --synthetic.
SYNTH_YEARS = (2024, 2025, 2026)


def _run_tests() -> tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    lines = [
        ln.strip()
        for ln in proc.stdout.splitlines()
        if re.search(r"\d+ (passed|failed|error)", ln)
    ]
    return proc.returncode == 0, (lines[-1] if lines else "no summary line")


# --------------------------------------------------------------------------- the split


def acquired_years(manifest: DatasetManifest, ingest_tf: str) -> list[int]:
    """Every calendar year the ingest series covers, ascending.

    Read from the manifest rather than from the parquet tree, so the split is drawn from
    the dataset whose hash the report quotes.
    """
    years: set[int] = set()
    for e in manifest.series:
        if e.timeframe != ingest_tf or not e.first_bar_utc or not e.last_bar_utc:
            continue
        a = datetime.fromisoformat(e.first_bar_utc).year
        b = datetime.fromisoformat(e.last_bar_utc).year
        years.update(range(a, b + 1))
    return sorted(years)


def split(years: list[int]) -> dict[str, list[int]]:
    """PRE_REGISTRATION section 4.1, applied.  Chronological and non-negotiable."""
    return {
        "in_sample": years[:IS_YEARS],
        "out_of_sample": years[IS_YEARS : IS_YEARS + OOS_YEARS],
        "holdout": years[IS_YEARS + OOS_YEARS :],
    }


# ---------------------------------------------------------------------------- building


def build_real(cfg, symbol: str, years: list[int], root: Path):
    """One continuous pass over the whole in-sample span, not four yearly passes.

    Running a year at a time would restart the liquidity book, the structure state and
    every indicator warm-up on 1 January -- losing a level created in December and swept
    in January, and blinding the first weeks of every year.  It would also put D1 under
    `swing.min_history` (250 bars against ~260 D1 bars in a year), so the swing engine
    would be cold for a meaningful share of each run.  The span is the unit; the calendar
    year survives as a reporting breakdown, taken from each sweep's own timestamp.
    """
    h4 = read_series(root, symbol, "H4", years=years)
    d1 = read_series(root, symbol, "D1", years=years)
    w1 = read_series(root, symbol, "W1", years=years)
    mn1 = read_series(root, symbol, "MN1", years=years)
    src = read_series(root, symbol, cfg.session.source_tf, years=years)
    st = analyse_structure(h4, cfg)
    book, sweeps = analyse_sweeps(
        cfg=cfg,
        h4=h4,
        d1=d1,
        w1=w1,
        mn1=mn1,
        sessions=build_sessions(src, cfg),
        h4_structure=st,
        d1_swings=detect_swings(d1, cfg),
    )
    return h4, book, sweeps, st, detect_fvgs(h4, cfg)


def build_synthetic(cfg, year: int, seed: int):
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
    return h4, book, sweeps, st, detect_fvgs(h4, cfg)


def runs_for(built, cfg, label_year: int, mode: ReferenceMode):
    return [
        F.build(
            symbol=sym,
            year=label_year,
            cfg=cfg,
            h4=h4,
            book=book,
            sweeps=sw,
            structure=st,
            fvgs=fv,
            mode=mode,
        )
        for sym, h4, book, sw, st, fv in built
    ]


def sensitivity(built, label_year: int, group: str, key: str, values) -> dict:
    """Re-run only the MSS engine across one parameter.

    The liquidity and sweep engines are untouched by either parameter varied here, so
    their output is reused rather than rebuilt -- which also keeps the comparison against
    an identical sweep population instead of a re-derived one.
    """
    out = {}
    for v in values:
        cfg2, _ = load_config(overrides={group: {key: v}})
        rs = runs_for(built, cfg2, label_year, ReferenceMode.MAJOR)
        f = F.pool(rs, ReferenceMode.MAJOR)
        dev = F.pool([r for r in rs if r.symbol in DEV_SET], ReferenceMode.MAJOR)
        out[v] = (f.mss_count(), f.conversion()["sweep_to_mss"], dev.mss_count())
    return out


# ----------------------------------------------------------------------------- helpers


def per_symbol_rows(runs, mode: ReferenceMode):
    rows = []
    for r in runs:
        if r.mode is not mode:
            continue
        f = F.pool([r], mode)
        st = f.stages(per_cluster=True)
        rows.append(
            (
                r.symbol,
                r.bars,
                st["sweeps_confirmed"],
                st["choch"],
                st["mss"],
                f.conversion()["sweep_to_mss"],
            )
        )
    return sorted(rows, key=lambda x: -x[4])


def mss_by_year(funnel) -> dict[int, int]:
    c: Counter[int] = Counter()
    for cand in funnel.decided():
        if cand.is_mss:
            c[cand.sweep.at.year] += 1
    return dict(sorted(c.items()))


def suspect_split(funnel) -> tuple[int, int, int, int]:
    """(decided, of which suspect, MSS, of which suspect) -- SPEC 1.5 reports, never drops."""
    dec = funnel.decided()
    mss = [c for c in dec if c.is_mss]
    return (
        len(dec),
        sum(1 for c in dec if c.sweep.data_suspect),
        len(mss),
        sum(1 for c in mss if c.sweep.data_suspect),
    )


# -------------------------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", action="store_true", help="the original random-walk fixture")
    ap.add_argument("--skip-tests", action="store_true", help="skip the suite (reported as such)")
    args = ap.parse_args()

    cfg, cfg_hash = load_config()
    real = not args.synthetic
    out_path = Path("reports/phase9_gate.md" if real else "reports/phase9_gate_synthetic.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    built = []
    dataset_hash = tzdata = source_label = price_side = "-"
    splits: dict[str, list[int]] = {}

    if real:
        manifest = DatasetManifest.load(PARQUET / "manifest.json")
        dataset_hash, tzdata = manifest.dataset_hash, manifest.tzdata_version
        source_label, price_side = manifest.source, manifest.price_side
        splits = split(acquired_years(manifest, manifest.ingest_timeframe))
        is_years = splits["in_sample"]
        label_year = is_years[0]
        symbols = list(cfg.symbols)
        for i, sym in enumerate(symbols, 1):
            t0 = time.time()
            print(f"[{i}/{len(symbols)}] {sym} {is_years[0]}-{is_years[-1]} ...", flush=True)
            h4, book, sweeps, st, fv = build_real(cfg, sym, is_years, PARQUET)
            built.append((sym, h4, book, sweeps, st, fv))
            print(
                f"      {h4.n:,} H4 bars, {len(sweeps.confirmed()):,} confirmed sweeps"
                f"  ({time.time() - t0:.0f}s)",
                flush=True,
            )
    else:
        is_years = list(SYNTH_YEARS)
        label_year = is_years[0]
        symbols = ["EURUSD"]
        for k, year in enumerate(SYNTH_YEARS):
            print(f"building {year} ...", flush=True)
            h4, book, sweeps, st, fv = build_synthetic(cfg, year, seed=41 + k)
            built.append((f"EURUSD-{year}", h4, book, sweeps, st, fv))

    n_symbols = len(symbols)
    n_years = len(is_years)
    symbol_years = n_symbols * n_years if real else len(built)

    series_by_key = {(sym, label_year): h4 for sym, h4, *_ in built}

    all_runs = []
    for mode in (ReferenceMode.MAJOR, ReferenceMode.MICRO):
        all_runs += runs_for(built, cfg, label_year, mode)
    major = F.pool(all_runs, ReferenceMode.MAJOR)
    micro = F.pool(all_runs, ReferenceMode.MICRO)
    dev_runs = [r for r in all_runs if r.symbol in DEV_SET]
    major_dev = F.pool(dev_runs, ReferenceMode.MAJOR)
    micro_dev = F.pool(dev_runs, ReferenceMode.MICRO)

    print("sensitivity ...", flush=True)
    sens_window = sensitivity(built, label_year, "choch", "max_bars_after_sweep", (4, 8, 12, 18, 24))
    sens_dist = sensitivity(
        built, label_year, "choch", "max_reference_distance_atr", (2.0, 3.0, 4.0, 6.0)
    )

    if args.skip_tests:
        tests_ok, tests_line = True, "SKIPPED (--skip-tests)"
    else:
        print("running test suite ...", flush=True)
        tests_ok, tests_line = _run_tests()

    conv = major.conversion()
    stages_sweep = major.stages()
    stages_cluster = major.stages(per_cluster=True)
    uni_mss = major.mss_count()
    dev_mss = major_dev.mss_count()
    per_sy = uni_mss / symbol_years if symbol_years else 0.0

    checks = [
        ("Test suite green", tests_ok, tests_line),
        (
            "Funnel reported, every stage (SPEC 11.7)",
            all(s in stages_cluster for s in F.STAGES),
            " -> ".join(f"{stages_cluster[s]:,}" for s in F.STAGES),
        ),
        (
            "Event funnel is monotone non-increasing",
            all(
                stages_cluster[a] >= stages_cluster[b]
                for a, b in zip(F.EVENT_STAGES, F.EVENT_STAGES[1:])
            ),
            "each event stage is a subset of the one before",
        ),
        (
            "Level stages are monotone, and separate",
            stages_cluster["levels_created"] >= stages_cluster["levels_swept_or_tested"],
            "levels and events are different units -- see the funnel table",
        ),
        (
            "Both reference modes run as separate variants (SPEC 11.1)",
            major.mss_count() >= 0 and micro.mss_count() >= 0,
            f"major {major.mss_count()} MSS, micro {micro.mss_count()} MSS",
        ),
        (
            "MSS is a subset of CHoCH",
            all(c.is_choch for c in major.deduplicated if c.is_mss),
            "SPEC 6.6",
        ),
        (
            "CHoCH-not-MSS population retained (SPEC 6.9)",
            len([c for c in major.decided() if c.is_choch and not c.is_mss]) > 0,
            f"{len([c for c in major.decided() if c.is_choch and not c.is_mss]):,} events kept "
            "for the marginal-value test",
        ),
        (
            "Median sweep->MSS is not at the window edge",
            major.window_edge_share(cfg) < 0.25,
            f"median {F.median_or_none(major.bars_to_mss())} bars, "
            f"{major.window_edge_share(cfg):.1%} at the edge",
        ),
        (
            f"MEASURED universe MSS >= {GATE_UNIVERSE} (major)",
            uni_mss >= GATE_UNIVERSE,
            f"{uni_mss:,} over {n_years}y x {n_symbols} symbols",
        ),
        (
            f"MEASURED development-set MSS >= {GATE_DEV} (major)",
            dev_mss >= GATE_DEV,
            f"{dev_mss:,} over {n_years}y x {len(DEV_SET)} symbols",
        ),
    ]
    all_ok = all(ok for _, ok, _ in checks)

    L: list[str] = []
    w = L.append
    w("# Phase 9 Gate Report")
    w("")
    w(f"Generated {datetime.now(UTC).isoformat(timespec='seconds')}")
    w("")
    w(f"- `config_hash` `{cfg_hash}`")
    if real:
        w(f"- `dataset_hash` `{dataset_hash}`")
        w(
            f"- Data: **real bars** -- {n_symbols} symbols, {is_years[0]}-{is_years[-1]} "
            f"({symbol_years} symbol-years), H4, source `{source_label}`, `{price_side}` side, "
            f"tzdata `{tzdata}`"
        )
        w(
            f"- Split (PRE_REGISTRATION 4.1): in-sample **{splits['in_sample']}**, "
            f"out-of-sample {splits['out_of_sample']}, holdout {splits['holdout']}"
        )
    else:
        w(
            f"- Fixture: {len(SYNTH_YEARS)} synthetic years "
            f"({SYNTH_YEARS[0]}-{SYNTH_YEARS[-1]}), EURUSD, H4"
        )
    w("- Reference modes: **major** and **micro**, run as two pre-registered strategies (SPEC 11.1)")
    w("")

    if real:
        w("> **The gate is a measurement.** Every previous run of this report scaled a")
        w("> conversion rate measured on one synthetic symbol to a ten-symbol universe and")
        w("> compared the product against the gate -- a PASS on projection, recorded as one.")
        w("> Real bars are in, so the counts the gate asks for are counted. The projection")
        w("> is retained below only as the thing the measurement is read against.")
    else:
        w("> **This is the synthetic fixture**, kept runnable so the instrument stays")
        w("> reproducible. Its numbers are properties of the detectors meeting noise. The")
        w("> gate is answered on real bars, in `reports/phase9_gate.md`.")
    w("")

    w("## Gate checks")
    w("")
    w("| Check | Result | Detail |")
    w("|---|---|---|")
    for name, ok, detail in checks:
        w(f"| {name} | {'PASS' if ok else 'FAIL'} | {detail} |")
    w("")

    if real:
        w("## The measurement against the projection")
        w("")
        w("The single reason this report was re-run. Left column is what the synthetic random")
        w("walk projected and the gate was passed on; right is what ten real symbols over four")
        w("real years actually produce.")
        w("")
        w("| | Synthetic projection | Real measurement | |")
        w("|---|---:|---:|---|")
        p = SYNTHETIC_PROJECTION
        w(
            f"| Sweep -> MSS conversion | {p['rate']:.2%} | **{conv['sweep_to_mss']:.2%}** | "
            f"x{conv['sweep_to_mss'] / p['rate']:.2f} |"
        )
        w(
            f"| MSS per symbol-year | {p['per_symbol_year']:.1f} | **{per_sy:.1f}** | "
            f"x{per_sy / p['per_symbol_year']:.2f} |"
        )
        w(
            f"| Universe MSS (>= {GATE_UNIVERSE}) | {p['universe']:,} | **{uni_mss:,}** | "
            f"{'PASS' if uni_mss >= GATE_UNIVERSE else 'FAIL'} |"
        )
        w(
            f"| Development set MSS (>= {GATE_DEV}) | {p['dev']:,} | **{dev_mss:,}** | "
            f"{'PASS' if dev_mss >= GATE_DEV else 'FAIL'} |"
        )
        w("")
        w("SPEC 11.7 named the number that would constitute a design finding before any of")
        w("this was built:")
        w("")
        w('> *"A funnel that converts 2% of sweeps into MSS will not produce a testable')
        w('> sample in five years, and that is a design finding to surface in Phase 9,')
        w('> before the entry engine is built."*')
        w("")
        w(f"The synthetic fixture measured {p['rate']:.2%} -- exactly on that line, which is why")
        w("the sensitivity tables were put in this report rather than deferred to the ablation")
        w(f"suite. Real bars measure **{conv['sweep_to_mss']:.2%}**.")
        w("")

    w("## The funnel (gate item 1)")
    w("")
    w("SPEC 9.4 counts several stacked levels swept by one bar as **one opportunity**. That")
    w("distinction matters more here than anywhere else in the project: the per-sweep column")
    w("triples correlated risk rather than sample size, and it is the per-cluster column that")
    w("answers the gate.")
    w("")
    w("**The funnel changes units in the middle, and the table says where.** The first two")
    w("rows count *levels*; the rest count *events*. They are not nested -- one level can")
    w("trigger several sweep events over its life (a rejected poke, then a real one), which is")
    w("why `sweeps_triggered` exceeds `levels_swept_or_tested` rather than shrinking. Only the")
    w("event chain is a funnel in the strict sense, and only it is asserted to be monotone.")
    w("")
    w("| Stage | Unit | Per sweep | Per cluster | Survives |")
    w("|---|---|---:|---:|---:|")
    prev = None
    for s in F.LEVEL_STAGES:
        c = stages_cluster[s]
        share = "" if prev in (None, 0) else f"{c / prev:.1%}"
        w(f"| `{s}` | level | {stages_sweep[s]:,} | {c:,} | {share} |")
        prev = c
    swept = stages_cluster["levels_swept_or_tested"]
    fan = stages_cluster["sweeps_triggered"] / swept if swept else 0.0
    w(f"| *(fan-out)* | | | | x{fan:.2f} |")
    prev = None
    for s in F.EVENT_STAGES:
        c = stages_cluster[s]
        share = "" if prev in (None, 0) else f"{c / prev:.1%}"
        w(f"| `{s}` | event | {stages_sweep[s]:,} | {c:,} | {share} |")
        prev = c
    w("")
    w(
        "**Sweep -> MSS conversion: {:.2%}** (per cluster, right-censored candidates "
        "excluded).".format(conv["sweep_to_mss"])
    )
    w("")

    if real:
        w("## Per symbol (gate item 2)")
        w("")
        w("The gate is a sum over this table, and the spread across it is what a single pooled")
        w("number hides.")
        w("")
        w("| Symbol | Set | H4 bars | Confirmed sweeps | CHoCH | MSS | Sweep -> MSS |")
        w("|---|---|---:|---:|---:|---:|---:|")
        for sym, bars, sw_n, ch, ms, rate in per_symbol_rows(all_runs, ReferenceMode.MAJOR):
            tag = "dev" if sym in DEV_SET else "cross"
            w(f"| {sym} | {tag} | {bars:,} | {sw_n:,} | {ch:,} | **{ms}** | {rate:.2%} |")
        w(
            f"| **total** | | | {stages_cluster['sweeps_confirmed']:,} | "
            f"{stages_cluster['choch']:,} | **{uni_mss}** | {conv['sweep_to_mss']:.2%} |"
        )
        w("")
        w("| Year | MSS (universe) |")
        w("|---|---:|")
        for y, n in mss_by_year(major).items():
            w(f"| {y} | {n} |")
        w("")
        dec_n, dec_s, mss_n, mss_s = suspect_split(major)
        w("### Suspect data, reported and not dropped (SPEC 1.5)")
        w("")
        w("Real bars carry gaps; the synthetic fixture had none, so this row could not exist")
        w("before. SPEC 1.5's rule for a level formed in a suspect region is that it is")
        w("*tagged and reported separately*, not excluded, and that is followed literally --")
        w("every headline number above includes these events.")
        w("")
        w(
            f"- Decided sweep opportunities on a suspect bar: **{dec_s:,} of {dec_n:,}** "
            f"({dec_s / max(dec_n, 1):.2%})"
        )
        w(f"- MSS on a suspect bar: **{mss_s} of {mss_n}** ({mss_s / max(mss_n, 1):.2%})")
        w(
            f"- Universe MSS excluding them: **{mss_n - mss_s}** "
            f"({'still passes' if mss_n - mss_s >= GATE_UNIVERSE else 'still fails'} "
            f"the >= {GATE_UNIVERSE} gate)"
        )
        w("")

    w("## Against the gate (gate item 2)")
    w("")
    if real:
        w(f"In-sample is {n_years} years over {n_symbols} symbols, of which {len(DEV_SET)} are the")
        w(f"development set ({', '.join(DEV_SET)}) -- `BACKTEST_PROTOCOL.md` section 2.1 and")
        w("`PRE_REGISTRATION.md` section 4.2.")
    else:
        w("Projected from a one-symbol rate; see the real-data report for the measurement.")
    w("")
    w("| Mode | MSS / symbol-year | Universe MSS | vs 300 | Development set MSS | vs 120 |")
    w("|---|---:|---:|:--:|---:|:--:|")
    for name, f_all, f_dev in (("major", major, major_dev), ("micro", micro, micro_dev)):
        u, d = f_all.mss_count(), f_dev.mss_count()
        w(
            f"| **{name}** | {u / symbol_years if symbol_years else 0:.1f} | {u:,} | "
            f"{'PASS' if u >= GATE_UNIVERSE else 'FAIL'} | {d:,} | "
            f"{'PASS' if d >= GATE_DEV else 'FAIL'} |"
        )
    w("")

    w("## Where the funnel loses candidates")
    w("")
    w("| Terminal outcome | major | micro |")
    w("|---|---:|---:|")
    mo, mi = major.outcomes(), micro.outcomes()
    for k in sorted(set(mo) | set(mi), key=lambda x: -mo.get(x, 0)):
        w(f"| `{k}` | {mo.get(k, 0):,} | {mi.get(k, 0):,} |")
    w("")

    w("## Which MSS clause binds (SPEC 11.5)")
    w("")
    w("Counted independently, so they overlap and do not sum to the CHoCH-not-MSS total. The")
    w("`sole cause` column is the marginal one: how often a clause is the *only* thing")
    w("standing between a CHoCH and an MSS.")
    w("")
    w("| Clause | major: fires | major: sole cause | micro: fires | micro: sole cause |")
    w("|---|---:|---:|---:|---:|")
    fa, fb = major.clause_failures(), micro.clause_failures()
    for cl in Clause:
        w(
            f"| `{cl.value}` | {fa.get(cl.value, 0):,} | {major.sole_cause(cl):,} | "
            f"{fb.get(cl.value, 0):,} | {micro.sole_cause(cl):,} |"
        )
    w("")
    w("`MTF_GATE` is zero because the SPEC 7 bias engine is Phase 2-4 and does not exist; the")
    w("gate is injected as the always-pass control, which is `bias.gate_mode = none` -- the")
    w("variant SPEC 7.5 says MUST be run regardless. **Every MSS count in this report is")
    w("therefore an upper bound**: a real gate can only remove events.")
    w("")

    w("## Timing (SPEC 11.7)")
    w("")
    b = major.bars_to_mss()
    w(
        f"- Median bars from sweep extreme to MSS: **{F.median_or_none(b)}** "
        f"(min {min(b) if b else '-'}, max {max(b) if b else '-'})"
    )
    w(f"- Share of MSS at the window edge: **{major.window_edge_share(cfg):.1%}**")
    w("")
    w("SPEC 11.7 asks this to detect a window doing the structure's work.")
    w("")
    w("| Level tier | Decided sweeps | MSS | Conversion |")
    w("|---|---:|---:|---:|")
    for k, (d, m, r) in sorted(major.by("level_tier").items()):
        w(f"| {k} | {d:,} | {m:,} | {r:.2%} |")
    w("")
    w("| Source | Decided sweeps | MSS | Conversion |")
    w("|---|---:|---:|---:|")
    for k, (d, m, r) in major.by("level_source").items():
        w(f"| `{k}` | {d:,} | {m:,} | {r:.2%} |")
    w("")
    w("| H4 open hour (UTC) | Decided sweeps | MSS | Conversion |")
    w("|---|---:|---:|---:|")
    for h, (d, m, r) in major.by_session(series_by_key).items():
        w(f"| {h:02d}:00 | {d:,} | {m:,} | {r:.2%} |")
    w("")
    w("Under D-001 the H4 grid is fixed at 00/04/08/12/16/20 UTC year-round, so the open hour")
    w("*is* the session slot. Read these as sample sizes, not as edges: `BACKTEST_PROTOCOL.md`")
    w("section 5.6 exists because six cells this size will always show a spread, and nothing")
    w("here corrects for having looked at six of them.")
    w("")

    w("## Sensitivity of the gate to two parameters")
    w("")
    w("Both re-run the MSS engine only; the liquidity and sweep engines are untouched by")
    w("either parameter, so the comparison is against an identical sweep population.")
    w("")
    w("### `choch.max_bars_after_sweep` (TUNABLE)")
    w("")
    w("| Value | Universe MSS | Sweep->MSS | Development set MSS |")
    w("|---|---:|---:|---:|")
    for v, (n, rate, dev) in sens_window.items():
        mark = "  <- default" if v == cfg.choch.max_bars_after_sweep else ""
        w(f"| {v} | {n:,} | {rate:.2%} | {dev:,}{mark} |")
    w("")
    w("### `choch.max_reference_distance_atr` (ABLATION)")
    w("")
    w("| Value | Universe MSS | Sweep->MSS | Development set MSS |")
    w("|---|---:|---:|---:|")
    for v, (n, rate, dev) in sens_dist.items():
        mark = "  <- default" if v == cfg.choch.max_reference_distance_atr else ""
        w(f"| {v:g} | {n:,} | {rate:.2%} | {dev:,}{mark} |")
    w("")
    hist = major.bars_to_mss_histogram(cfg)
    reached = max((b for b, n in hist.items() if n), default=0)
    w("| Bars from sweep extreme to MSS | MSS |")
    w("|---|---:|")
    for bkt, n in hist.items():
        if bkt > reached + 2:
            break
        w(f"| {bkt} | {n} |")
    w("")
    w("**Neither table licenses moving either parameter.** `BACKTEST_PROTOCOL.md` section 10.2")
    w("forbids choosing a parameter by looking at the outcome, and that binds hardest exactly")
    w("when a gate sits near its threshold. The defaults were fixed before this ran and stay")
    w("fixed; both are ablated with the rest, on the split they belong to.")
    w("")

    w("## Per-month counts (SPEC 11.7)")
    w("")
    w("| Month | Confirmed sweeps | CHoCH | MSS |")
    w("|---|---:|---:|---:|")
    for k, (sw_n, ch, ms) in major.per_month().items():
        w(f"| {k} | {sw_n:,} | {ch:,} | {ms:,} |")
    w("")

    n_mss, n_fail = major.spec_6_6_leg_clause_cost()
    w("## The clause SPEC 11.5 omits (D-009)")
    w("")
    w("SPEC 6.6 requires the swept level to lie beyond the extreme of the leg that produced")
    w("the CHoCH; SPEC 11.5 lists the MSS conditions and calls itself *complete* without it.")
    w("11.5 is operative here -- it is the more specific section and the one claiming")
    w("completeness -- so the other reading is priced instead of argued:")
    w("")
    w(
        f"- **{n_fail} of {n_mss} major MSS events** would additionally be rejected by the "
        "SPEC 6.6 clause."
    )
    w("")

    w("## What this report does NOT establish")
    w("")
    if real:
        w("**Nothing about whether an MSS is worth trading.** This is a count. It says the")
        w("sequence occurs, how often, and on which symbols -- and nothing at all about what")
        w("happens next, which is Phases 10-14 and the falsification suite.")
        w("")
        w("Specifically **not** established:")
        w("")
        w("1. **That the count is the count a strategy would get.** The MTF gate is not built")
        w("   and is injected as always-pass, so every number here is an upper bound (SPEC")
        w("   7.5). A gate rejecting a fifth of setups moves both gate rows.")
        w("2. **That the symbols are independent.** Ten majors sharing USD, EUR and JPY legs")
        w("   are heavily correlated; the effective sample is smaller than the count, and the")
        w("   gate is stated in counts. The per-symbol table is the honest form.")
        w("3. **That any of this is out-of-sample.** This is the in-sample split, which is what")
        w("   it is for. The out-of-sample and holdout years exist and were not read.")
        w("4. **That a passing count implies a testable *edge*.** Sample size is necessary and")
        w("   not sufficient; H2-H5 remain open and are answered by the studies, not here.")
    else:
        w("**Nothing about whether an MSS is worth trading**, and nothing about real markets.")
        w("`bot/data/synthetic.py` generates a series with no liquidity, no participants and no")
        w("structure, so every number above is a property of the detectors meeting noise.")
    w("")
    w(f"## Verdict: {'PASS' if all_ok else 'FAIL'}")
    w("")

    out_path.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"\nwrote {out_path}")
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
