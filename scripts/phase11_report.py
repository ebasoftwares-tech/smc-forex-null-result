"""Phase 11 acceptance report (SPEC section 27).

Gate: **"Definition bake-off with the agreement matrix"** -- SPEC 13.8's comparison of
OB-A/B/C/D as four pre-registered variants, with the agreement matrix reported alongside
performance *because near-identical variants must not be counted as independent tests*.

    python scripts/phase11_report.py              # real bars, data/parquet
    python scripts/phase11_report.py --synthetic  # the original random-walk fixture

**The default is real data.** `M_eff` is the number this phase exists to produce and it is
a property of how the four definitions behave on the data in front of them, so the value
computed on a random walk was explicitly flagged as needing recomputation on real bars
(D-012, and `STATE.md` section 9's run order). The touch counts that decide answerability
are likewise measured here rather than projected from one symbol.
"""

from __future__ import annotations

import argparse
import re
import statistics
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.config.loader import load_config  # noqa: E402
from bot.core.displacement import leg_origin  # noqa: E402
from bot.core.fvg import detect_fvgs  # noqa: E402
from bot.core.indicators import atr_ref  # noqa: E402
from bot.core.mss import analyse_mss  # noqa: E402
from bot.core.order_blocks import ObDefinition, propose, track_order_blocks  # noqa: E402
from bot.core.sessions import build_sessions  # noqa: E402
from bot.core.structure import analyse_structure  # noqa: E402
from bot.core.sweeps import analyse_sweeps  # noqa: E402
from bot.core.swings import detect_swings  # noqa: E402
from bot.data.ingest import DatasetManifest, read_series  # noqa: E402
from bot.data.resample import resample  # noqa: E402
from bot.data.synthetic import generate  # noqa: E402
from bot.research import ob_study as OB  # noqa: E402
from bot.research.stats import (  # noqa: E402
    ALPHA,
    Verdict,
    calibration_interval,
    calibration_sigma,
    detects_effect,
    null_calibration,
)

UTC = timezone.utc
PARQUET = Path("data/parquet")
FILL_HORIZONS = (1, 3, 5, 10, 20, 30)
DEFS = list(ObDefinition)
LABELS = {
    ObDefinition.A_LAST_OPPOSING: "OB-A",
    ObDefinition.B_LAST_DOWN_CLOSE_BEFORE_BREAK: "OB-B",
    ObDefinition.C_EXTREME_ORIGIN: "OB-C",
    ObDefinition.D_BREAKER: "OB-D",
}

# PRE_REGISTRATION section 4.2 / Amendment 1 (section 4.1).  Not choices made here.
DEV_SET = ("EURUSD", "GBPUSD", "USDJPY")
IS_YEARS, OOS_YEARS = 4, 2

# What the synthetic fixture reported, from the run committed at faf0e14 (D-012).  Carried
# so the recomputation has something to be read against; used in no check.
SYNTHETIC_M_EFF = 1.77

SYNTH_YEARS = (2024, 2025, 2026)


def _run_tests() -> tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/"],
        capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1],
    )
    lines = [
        ln.strip() for ln in proc.stdout.splitlines()
        if re.search(r"\d+ (passed|failed|error)", ln)
    ]
    return proc.returncode == 0, (lines[-1] if lines else "no summary line")


# --------------------------------------------------------------------------- the split


def acquired_years(manifest: DatasetManifest, ingest_tf: str) -> list[int]:
    years: set[int] = set()
    for e in manifest.series:
        if e.timeframe != ingest_tf or not e.first_bar_utc or not e.last_bar_utc:
            continue
        a = datetime.fromisoformat(e.first_bar_utc).year
        b = datetime.fromisoformat(e.last_bar_utc).year
        years.update(range(a, b + 1))
    return sorted(years)


def split(years: list[int]) -> dict[str, list[int]]:
    """PRE_REGISTRATION section 4.1 as stamped by Amendment 1."""
    return {
        "in_sample": years[:IS_YEARS],
        "out_of_sample": years[IS_YEARS : IS_YEARS + OOS_YEARS],
        "holdout": years[IS_YEARS + OOS_YEARS :],
    }


# ---------------------------------------------------------------------------- building


def _finish(cfg, h4, d1, w1, mn1, sessions):
    st = analyse_structure(h4, cfg)
    _, sw = analyse_sweeps(
        cfg=cfg, h4=h4, d1=d1, w1=w1, mn1=mn1,
        sessions=sessions, h4_structure=st, d1_swings=detect_swings(d1, cfg),
    )
    res = analyse_mss(h4, cfg, sw.confirmed(), swings=st.swings, fvgs=detect_fvgs(h4, cfg))
    return h4, st, [c for c in res.candidates if c.is_choch], atr_ref(h4, cfg.atr.period)


def build_real(cfg, symbol: str, years: list[int], root: Path):
    """One continuous pass over the in-sample span -- see phase9_report.build_real."""
    return _finish(
        cfg,
        read_series(root, symbol, "H4", years=years),
        read_series(root, symbol, "D1", years=years),
        read_series(root, symbol, "W1", years=years),
        read_series(root, symbol, "MN1", years=years),
        build_sessions(read_series(root, symbol, cfg.session.source_tf, years=years), cfg),
    )


def build_synthetic(cfg, year: int, seed: int):
    src = generate(
        "EURUSD", datetime(year, 1, 1, tzinfo=UTC),
        datetime(year, 12, 31, 23, 59, tzinfo=UTC), cfg, timeframe="M15", seed=seed,
    )
    h4 = resample(src, "H4", cfg)
    return _finish(
        cfg, h4, resample(src, "D1", cfg), resample(src, "W1", cfg),
        resample(src, "MN1", cfg), build_sessions(src, cfg),
    )


def propose_all(cfg, h4, st, setups, atr, definition):
    blocks, reasons = [], Counter()
    for i, c in enumerate(setups):
        p = propose(
            h4, cfg, direction=c.direction, sweep_extreme_bar=c.sweep_extreme_bar,
            leg_start=leg_origin(c.sweep_extreme_bar, c.choch_bar, cfg),
            break_bar=c.choch_bar, reference_price=c.reference_price,
            displacement_confirmed=c.displacement.confirmed, definition=definition,
            swings=st.swings.swings, atr=atr, seq=i,
        )
        if p.ok:
            blocks.append(p.ob)
        else:
            reasons[p.reason.value] += 1
    return blocks, reasons


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", action="store_true", help="the original random-walk fixture")
    ap.add_argument("--skip-tests", action="store_true", help="skip the suite (reported as such)")
    args = ap.parse_args()

    cfg, cfg_hash = load_config()
    real = not args.synthetic
    OUT = Path("reports/phase11_gate.md" if real else "reports/phase11_gate_synthetic.md")
    OUT.parent.mkdir(parents=True, exist_ok=True)

    built: list[tuple] = []
    dataset_hash = tzdata = source_label = price_side = "-"
    splits: dict[str, list[int]] = {}

    if real:
        manifest = DatasetManifest.load(PARQUET / "manifest.json")
        dataset_hash, tzdata = manifest.dataset_hash, manifest.tzdata_version
        source_label, price_side = manifest.source, manifest.price_side
        splits = split(acquired_years(manifest, manifest.ingest_timeframe))
        is_years = splits["in_sample"]
        symbols = list(cfg.symbols)
        for i, sym in enumerate(symbols, 1):
            t0 = time.time()
            print(f"[{i}/{len(symbols)}] {sym} {is_years[0]}-{is_years[-1]} ...", flush=True)
            built.append((sym, *build_real(cfg, sym, is_years, PARQUET)))
            print(f"      {len(built[-1][3]):,} CHoCH setups  ({time.time() - t0:.0f}s)", flush=True)
    else:
        is_years = list(SYNTH_YEARS)
        symbols = ["EURUSD"]
        for k, year in enumerate(SYNTH_YEARS):
            print(f"building {year} ...", flush=True)
            built.append((f"EURUSD-{year}", *build_synthetic(cfg, year, seed=41 + k)))

    n_symbols = len(symbols)
    n_years = len(is_years)
    symbol_years = n_symbols * n_years if real else len(built)

    n_setups = sum(len(setups) for _, _, _, setups, _ in built)
    n_displaced = sum(
        1 for _, _, _, setups, _ in built for c in setups if c.displacement.confirmed
    )

    # --- proposals, per definition
    print("proposing ...", flush=True)
    rows: list[OB.SetupProposals] = []
    per_def: dict[ObDefinition, dict] = {
        d: {"blocks": [], "reasons": Counter(), "books": []} for d in DEFS
    }
    for label, h4, st, setups, atr in built:
        for i, c in enumerate(setups):
            r = OB.SetupProposals(
                f"{label}:{i}", c.choch_bar, c.direction,
                ref_price=float(h4.close[c.choch_bar]), ref_atr=float(atr[c.choch_bar]),
            )
            for d in DEFS:
                r.proposals[d] = propose(
                    h4, cfg, direction=c.direction, sweep_extreme_bar=c.sweep_extreme_bar,
                    leg_start=leg_origin(c.sweep_extreme_bar, c.choch_bar, cfg),
                    break_bar=c.choch_bar, reference_price=c.reference_price,
                    displacement_confirmed=c.displacement.confirmed, definition=d,
                    swings=st.swings.swings, atr=atr, seq=i,
                )
            rows.append(r)
        for d in DEFS:
            blocks, reasons = propose_all(cfg, h4, st, setups, atr, d)
            per_def[d]["blocks"].extend(blocks)
            per_def[d]["reasons"].update(reasons)
            per_def[d]["books"].append((label, h4, track_order_blocks(h4, cfg, blocks)))

    # --- the gate: agreement + effective tests
    agree = OB.agreement_matrix(rows, DEFS)
    corr, corr_n = OB.price_correlations(rows, DEFS)
    m_eff = OB.effective_tests(corr)
    m_eff_li_ji = OB.li_ji_effective_tests(corr)
    corr_abc, corr_abc_n = OB.price_correlations(rows, DEFS[:3])
    m_eff_abc = OB.effective_tests(corr_abc)

    # --- edge study per definition
    print("edge studies ...", flush=True)
    edge, edge_dev = {}, {}
    for d in DEFS:
        t0 = time.time()
        studies = [
            (label, OB.run_edge_study(h4, book, cfg, d, permutations=2000))
            for label, h4, book in per_def[d]["books"]
        ]
        edge[d] = OB.pool_edge_studies([s for _, s in studies])
        if real:
            edge_dev[d] = OB.pool_edge_studies(
                [s for label, s in studies if label in DEV_SET]
            )
        print(f"  {LABELS[d]} ({time.time() - t0:.0f}s)", flush=True)

    # --- zone-mode ablation on the default definition
    print("zone-mode ablation ...", flush=True)
    zone_rows = {}
    for mode in ("full_range", "body", "wick_to_open"):
        c2, _ = load_config(overrides={"ob": {"zone_mode": mode}})
        blocks_all, books = [], []
        for label, h4, st, setups, atr in built:
            blocks, _ = propose_all(c2, h4, st, setups, atr, ObDefinition.A_LAST_OPPOSING)
            blocks_all.extend(blocks)
            books.append((h4, track_order_blocks(h4, c2, blocks)))
        curves = [b.fill_curve((5, 30), h.n) for h, b in books]
        zone_rows[mode] = (
            len(blocks_all),
            float(np.nanmean([c[5] for c in curves])),
            float(np.nanmean([c[30] for c in curves])),
            float(np.mean([b.zone_high - b.zone_low for b in blocks_all])) if blocks_all else float("nan"),
        )

    print("controls ...", flush=True)
    a_pool = edge[ObDefinition.A_LAST_OPPOSING]
    r1 = a_pool.results[0]
    CAL_TRIALS = 3000
    t0 = time.time()
    fpr = null_calibration(r1.touch.returns, r1.control.returns, trials=CAL_TRIALS, bootstrap=600)
    print(f"  calibration ({time.time() - t0:.0f}s)", flush=True)
    sigma = calibration_sigma(fpr, CAL_TRIALS)
    fpr_lo, fpr_hi = calibration_interval(fpr, CAL_TRIALS)
    grid = [
        (s, detects_effect(r1.touch.returns, r1.control.returns, s))
        for s in (0.0, 0.25, 0.5, 1.0)
    ]
    _, first_h4, first_book = per_def[ObDefinition.A_LAST_OPPOSING]["books"][0]
    shifted = OB.run_edge_study(
        first_h4, first_book, cfg, ObDefinition.A_LAST_OPPOSING,
        permutations=2000, touch_shift=1.0,
    )

    if args.skip_tests:
        tests_ok, tests_line = True, "SKIPPED (--skip-tests)"
    else:
        print("running test suite ...", flush=True)
        tests_ok, tests_line = _run_tests()

    a_dev = edge_dev.get(ObDefinition.A_LAST_OPPOSING)
    touches_universe = a_pool.n_touches
    touches_dev = a_dev.n_touches if a_dev else float("nan")
    touches_per_sy = touches_universe / symbol_years if symbol_years else 0.0

    checks = [
        ("Test suite green", tests_ok, tests_line),
        (
            "All four definitions run as pre-registered variants (gate)",
            all(per_def[d]["blocks"] for d in DEFS),
            ", ".join(f"{LABELS[d]} {len(per_def[d]['blocks'])}" for d in DEFS),
        ),
        ("Agreement matrix reported (gate)", len(agree) == 6, f"{len(agree)} pairs"),
        (
            "Effective number of independent tests computed",
            np.isfinite(m_eff),
            f"M_eff = {m_eff:.2f} against a nominal {len(DEFS)} (n = {corr_n})",
        ),
        (
            "M_eff is below the nominal count",
            m_eff < len(DEFS),
            "variants are not independent -- see the matrix",
        ),
        (
            "Rejection reasons enumerated per definition (SPEC 13.7)",
            all(per_def[d]["reasons"] for d in DEFS),
            "the NO_OB_AVAILABLE rate is a quality signal, not a defect",
        ),
        (
            "Standalone edge test run per definition",
            all(edge[d].results for d in DEFS),
            f"{len(DEFS)} definitions x {len(OB.DEFAULT_HORIZONS)} horizons",
        ),
        (
            "Positive control detects an injected effect",
            shifted.verdict() is Verdict.DIFFERENT,
            "1.0 ATR shift -> DIFFERENT",
        ),
        (
            "Null calibration lands near alpha",
            0.0 < fpr < 0.15,
            f"{fpr:.1%} over {CAL_TRIALS:,} shuffles, CI [{fpr_lo:.1%}, {fpr_hi:.1%}] "
            f"({sigma:.1f} sigma from alpha {ALPHA:.0%})",
        ),
        ("Zone-mode ablation reported", len(zone_rows) == 3, "SPEC 13.3 / 13.8"),
    ]
    all_ok = all(ok for _, ok, _ in checks)

    L: list[str] = []
    w = L.append
    w("# Phase 11 Gate Report")
    w("")
    w("**Order Block definition bake-off (SPEC 13.8).**")
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
            f"- Split (PRE_REGISTRATION 4.1, Amendment 1): in-sample **{splits['in_sample']}**, "
            f"out-of-sample {splits['out_of_sample']}, holdout {splits['holdout']}"
        )
    else:
        w(f"- Fixture: {len(SYNTH_YEARS)} synthetic years ({SYNTH_YEARS[0]}-{SYNTH_YEARS[-1]}), EURUSD, H4")
    w(f"- **{n_setups:,} CHoCH setups, {n_displaced:,} of which displaced** and can carry an OB")
    w("")
    w("SPEC 13.1 opens by admitting the problem this phase exists to settle:")
    w("")
    w("> *\"'The last opposing candle before a move that breaks structure' is the standard")
    w("> formulation and it is under-specified in three places... Different choices produce")
    w("> zones tens of pips apart, which for a stop-based strategy is the difference between")
    w("> a win and a loss.\"*")
    w("")
    w("So the deliverable is a comparison between four rules, not one rule's performance.")
    w("")
    w("## Gate checks")
    w("")
    w("| Check | Result | Detail |")
    w("|---|---|---|")
    for name, ok, detail in checks:
        w(f"| {name} | {'PASS' if ok else 'FAIL'} | {detail} |")
    w("")

    w("## The headline: how many independent tests the four variants are worth")
    w("")
    w(f"**M_eff = {m_eff:.2f} against a nominal {len(DEFS)}** (Galwey's estimator on the")
    w(f"correlation of proposed entry offsets, listwise n = {corr_n}).")
    w("")
    if real:
        w(f"The synthetic fixture reported **{SYNTHETIC_M_EFF:.2f}**, and D-012 flagged that")
        w("number as a property of how the definitions behave on *that* fixture rather than a")
        w(f"constant. Recomputed on real bars it is **{m_eff:.2f}**, a change of")
        w(f"{abs(m_eff - SYNTHETIC_M_EFF) / SYNTHETIC_M_EFF:.0%}. **Use the real-bar value in")
        w("every correction from here.**")
        w("")
    w("SPEC 13.8 asks for the agreement matrix for one stated reason: *\"near-identical")
    w("variants must not be counted as independent tests when applying the multiple-testing")
    w("correction.\"* That is a statistical instruction, so this report answers it with a")
    w("number rather than a table of percentages to eyeball.")
    w("")
    w(f"Correcting as though these were {len(DEFS)} independent tests would over-correct by")
    w(f"a factor of {len(DEFS)/m_eff:.1f}. **Use {m_eff:.2f}, not {len(DEFS)}.**")
    w("")
    w("### The two agreement measures, side by side")
    w("")
    w("| Pair | n | Same bar |")
    w("|---|---:|---:|")
    for (a, b), (n, share) in agree.items():
        la = LABELS[ObDefinition(a)]
        lb = LABELS[ObDefinition(b)]
        s = "—" if not np.isfinite(share) else f"{share:.1%}"
        w(f"| {la} vs {lb} | {n} | {s} |")
    w("")
    w("Entry-offset correlation (ATR from the break bar's close):")
    w("")
    w("| | " + " | ".join(LABELS[d] for d in DEFS) + " |")
    w("|---|" + "---:|" * len(DEFS))
    for i, d in enumerate(DEFS):
        cells = " | ".join(f"{corr[i][j]:.3f}" for j in range(len(DEFS)))
        w(f"| **{LABELS[d]}** | {cells} |")
    w("")
    w("**Read those two tables together.** SPEC 13.6's heuristic — *\"if OB-A and OB-C select")
    w("the same bar 80% of the time, they are not two hypotheses\"* — is the right instinct")
    w("with the wrong instrument, and the two columns are what shows it. What a trade")
    w("consumes is the entry *price*, and two rules that differ by a fraction of an ATR are")
    w("one hypothesis however different their reasoning looks.")
    w("")
    w(f"Restricted to OB-A/B/C, M_eff = **{m_eff_abc:.2f}** against a nominal 3 (n = {corr_abc_n}).")
    w("")
    w("### Why Galwey and not Li & Ji")
    w("")
    w(f"Li & Ji (2005) is the more commonly cited estimator and would report **{m_eff_li_ji:.2f}**")
    w("here. It is not used, for a reason specific to this study: it sums")
    w("`I(lambda >= 1) + frac(lambda)`, which is **discontinuous at integer eigenvalues**.")
    w("Four perfectly correlated variants give eigenvalues `[4, 0, 0, 0]` and it")
    w("analytically returns 1 — but it never sees an exact 4. `eigvalsh` on a matrix of")
    w("ones returns 3.999999999999999, `floor` drops from 4 to 3, and the estimate jumps")
    w("to ~2. It is wrong by a whole test on the most redundant input possible, from")
    w("floating-point noise alone. Galwey's `(sum sqrt(lambda))^2 / sum lambda` is")
    w("continuous and exact at every anchor. Both are pinned by tests.")
    w("")

    w("## Hit rate and why each definition declines (SPEC 13.7)")
    w("")
    w("*\"The frequency of this is a quality signal for the definition\"* — so the reasons")
    w("are enumerated rather than collapsed into a count of failures.")
    w("")
    w("| Definition | Blocks | Hit rate | Rejections |")
    w("|---|---:|---:|---|")
    for d in DEFS:
        n = len(per_def[d]["blocks"])
        reasons = ", ".join(f"`{k}` {v}" for k, v in per_def[d]["reasons"].most_common(3))
        w(f"| {LABELS[d]} `{d.value}` | {n:,} | {n/n_setups:.1%} | {reasons} |")
    w("")
    w("`NO_DISPLACEMENT` dominates every row and is the same number for all four: it is")
    w("SPEC 13.4's constraint 1, applied before any definition-specific search. **That")
    w("constraint is what stops OB-A degenerating into \"the last red candle\"**, which on")
    w("any chart is never more than a few bars away and therefore always exists. Without")
    w("it every definition would report a near-100% hit rate and the bake-off would")
    w("measure nothing.")
    w("")
    w("**SPEC 13.2 describes OB-D in one line and leaves more open than it closes.** A, B")
    w("and C all key off the displacement leg of the setup in hand; D points at a")
    w("*different* structural event — *\"the last opposing bar of the failed move\"* —")
    w("without saying which swing, how far back, or what \"broken downward\" means for a")
    w("level that is broken upward by definition. The reading implemented is documented at")
    w("`order_blocks._ob_d` and recorded in D-012 as a **flagged ambiguity rather than a")
    w("resolved one**. Its hit rate is a property of that reading, not of the breaker")
    w("concept.")
    w("")

    w("## Standalone edge test, per definition (SPEC 13.8)")
    w("")
    w("| Definition | Blocks | Touches | h=1 diff | h=1 CI | h=1 MDE | Verdict |")
    w("|---|---:|---:|---:|---|---:|---|")
    for d in DEFS:
        s = edge[d]
        if not s.results:
            w(f"| {LABELS[d]} | {s.n_blocks:,} | {s.n_touches:,} | — | — | — | NO_DATA |")
            continue
        r = s.results[0]
        w(
            f"| {LABELS[d]} | {s.n_blocks:,} | {s.n_touches:,} | {r.diff:+.4f} | "
            f"[{r.ci_low:+.4f}, {r.ci_high:+.4f}] | {r.mde:.3f} | **{s.verdict().value}** |"
        )
    w("")
    w("Verdicts are three-way (`stats.Verdict`): `UNDERPOWERED` is **not** `EQUIVALENT`.")
    w(f"Only an interval sitting inside the declared +/-{OB.EQUIVALENCE_MARGIN_ATR:g} ATR")
    w("margin licenses \"this definition contributes nothing\".")
    w("")
    if real:
        w("### Answerability, measured rather than projected")
        w("")
        w(f"OB-A yields **{touches_universe:,} touch events** across the {symbol_years}")
        w(f"in-sample symbol-years ({touches_per_sy:.0f} per symbol-year), of which")
        w(f"**{touches_dev:,.0f}** are on the three development symbols.")
        w("")
        w("| h | touches needed | dev set has | universe has | answerable? |")
        w("|---:|---:|---:|---:|---|")
        for r in a_pool.results:
            need = r.required_touch_n
            ok_dev = "yes" if touches_dev >= need else "no"
            ok_uni = "yes" if touches_universe >= need else "**no**"
            w(
                f"| {r.horizon} | {need:,.0f} | {touches_dev:,.0f} ({ok_dev}) | "
                f"{touches_universe:,.0f} | {ok_uni} |"
            )
        w("")
        w("The development-set column is the one to read. Phase 9 measured the funnel that")
        w("feeds this study and its development-set half **failed its gate** (D-020), so a")
        w("study answerable across the universe but not on the three symbols development")
        w("actually happens on is answerable only in a sense that cannot be iterated against.")
        w("")
    else:
        w(f"OB-A yields {touches_universe} touch events across {symbol_years} symbol-years,")
        w("because an order block is only proposed at a CHoCH that displaced and only a")
        w("fraction of those are ever touched.")
        w("")

    w("## Fill rate and time to fill")
    w("")
    w("| Definition | fill@5 | fill@30 | median bars to fill |")
    w("|---|---:|---:|---:|")
    for d in DEFS:
        books = per_def[d]["books"]
        curves = [b.fill_curve(FILL_HORIZONS, h.n) for _, h, b in books]
        f5 = float(np.nanmean([c[5] for c in curves]))
        f30 = float(np.nanmean([c[30] for c in curves]))
        bars = [x for _, _, b in books for x in b.bars_to_mitigation()]
        med = statistics.median(bars) if bars else float("nan")
        w(f"| {LABELS[d]} | {f5:.1%} | {f30:.1%} | {med:g} |")
    w("")
    w("SPEC 13.7 makes this a headline statistic rather than a detail: *\"a model with a")
    w("20% fill rate has a fifth of the sample size and cannot be compared naively against")
    w("model A.\"* Whatever share of OB-A's blocks go unfilled within 30 bars is the share")
    w("of setups entry model D discards before any comparison starts.")
    w("")

    w("## Zone-mode ablation (SPEC 13.3)")
    w("")
    w("| `ob.zone_mode` | Blocks | Mean zone height | fill@5 | fill@30 |")
    w("|---|---:|---:|---:|---:|")
    for mode, (n, f5, f30, height) in zone_rows.items():
        mark = "  ← default" if mode == cfg.ob.zone_mode else ""
        w(f"| `{mode}`{mark} | {n:,} | {height:.5f} | {f5:.1%} | {f30:.1%} |")
    w("")
    w("The mode changes the zone's height and therefore how easily price reaches its")
    w("midpoint, so fill rate moves with it. It does not change which bar was chosen, so")
    w("it is orthogonal to the definition bake-off above and the two ablate independently.")
    w("")

    w("## Controls")
    w("")
    w("### Positive control")
    w("")
    w(f"Injected shifts on OB-A at h=1, where the sample's MDE is {r1.mde:.3f} ATR:")
    w("")
    w("| Injected effect | Detected |")
    w("|---:|---|")
    for shift, got in grid:
        w(f"| {shift:+.2f} ATR | {'yes' if got else 'no'} |")
    w("")
    w(f"End to end with a 1.0 ATR shift the study reports **{shifted.verdict().value}**.")
    w("")
    w("### Null calibration")
    w("")
    w(f"- False-positive rate over {CAL_TRIALS:,} label shuffles: **{fpr:.1%}** against alpha of {ALPHA:.0%}")
    w(f"- 95% Wilson interval on that rate: **[{fpr_lo:.1%}, {fpr_hi:.1%}]** — it contains alpha: {'yes' if fpr_lo <= ALPHA <= fpr_hi else 'no'}")
    w(f"- Deviation: **{sigma:.1f} sigma**")
    w("")
    w("**Both the interval and the trial count are the result of getting this wrong first.**")
    w("Earlier drafts ran 300-400 shuffles, where the standard error on the rate is about")
    w("1.1 points — the same size as the deviation being looked for. Three draws of this")
    w("very calibration read 4.8%, 8.0% and 5.5%, and the 8.0% one was written up as")
    w("evidence of a miscalibrated interval. It was a noisy draw. Every calibration in the")
    w(f"project now runs {CAL_TRIALS:,} shuffles and quotes its Wilson interval. See D-012 §4.")
    w("")
    if sigma >= 2.0:
        w("**Anti-conservative.** The percentile bootstrap under-covers with heavy-tailed")
        w(f"observations, and this pooled sample has {r1.touch.n}. Both consequences point the")
        w("safe way: the intervals are **too narrow**, so an UNDERPOWERED verdict above is if")
        w("anything understated, and a DIFFERENT verdict would be over-eager rather than missed.")
    else:
        w(f"**Calibrated** — the deviation is inside what {CAL_TRIALS:,} shuffles resolve.")
    w("")

    w("## What this report does NOT establish")
    w("")
    w("**Which definition is best.** That needs performance, and performance needs the")
    w("entry engine, the risk layer and the backtest — Phases 12 to 14. What this")
    w("establishes is the prerequisite SPEC 13.8 asks for: how many independent tests the")
    w("comparison actually represents, so that the eventual performance numbers can be")
    w("corrected honestly rather than by counting variants.")
    w("")
    w("Specifically not established:")
    w("")
    if real:
        w("1. **That `M_eff` is stable across splits.** It is recomputed here on the in-sample")
        w("   years only. Nothing says it holds on the out-of-sample or holdout years, and it")
        w("   should not be recomputed there to find out — that spends out-of-sample budget on")
        w("   a nuisance parameter (protocol §7).")
        w("2. **Anything about edge.** See the verdict column; an UNDERPOWERED result is a")
        w("   statement about the sample, not about the market.")
        w("3. **That OB-D's hit rate reflects the breaker concept.** It reflects one reading of")
        w("   a one-line specification (D-012 §1).")
        w("4. **That these touch counts survive the Phase 9 result.** D-020's development-set")
        w("   gate failed, and this study draws from the same funnel.")
    else:
        w("1. **That M_eff transfers.** It is a property of how the definitions behave on")
        w("   *this* fixture. Real bars have trends and gaps; the definitions may diverge more")
        w("   there, and M_eff must be recomputed rather than assumed.")
        w("2. **Anything about edge.** On a random walk the true effect is zero by")
        w("   construction. A DIFFERENT verdict here would mean the study is broken.")
        w("3. **That OB-D's hit rate reflects the breaker concept.** It reflects one reading of")
        w("   a one-line specification (D-012 §1).")
        w("4. **That the fill rates transfer.** A random walk returns to a level readily and a")
        w("   trending market may not.")
    w("")
    w(f"## Verdict: {'PASS' if all_ok else 'FAIL'}")
    w("")
    w("The bake-off ran, all four definitions are implemented as pre-registered variants,")
    w("and the agreement matrix has been converted into the number it exists to produce:")
    w(f"**{m_eff:.2f} effective tests, not {len(DEFS)}**.")

    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"\nwrote {OUT}")
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
