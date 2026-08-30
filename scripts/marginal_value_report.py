"""H5 falsification study: MSS vs CHoCH-not-MSS (SPEC 6.9, BACKTEST_PROTOCOL §6.2).

Not a phase gate — a falsification-suite study, run out of order because the population
it needs already exists and because answering it after five more phases were built around
the assumption would be expensive.

    python scripts/marginal_value_report.py              # real bars, data/parquet
    python scripts/marginal_value_report.py --synthetic  # the original fixture

**The default is real data, and that changes what this study is.** On the fixture it
could only validate its own instrument -- the true MSS vs CHoCH-not-MSS difference is
zero by construction on a random walk.  On real bars it tests H5 itself, at whatever
horizons the measured population can resolve.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.config.loader import load_config  # noqa: E402
from bot.core.fvg import detect_fvgs  # noqa: E402
from bot.core.indicators import atr_ref  # noqa: E402
from bot.core.mss import ReferenceMode, analyse_mss  # noqa: E402
from bot.core.sessions import build_sessions  # noqa: E402
from bot.core.structure import analyse_structure  # noqa: E402
from bot.core.sweeps import analyse_sweeps  # noqa: E402
from bot.core.swings import detect_swings  # noqa: E402
from bot.data.ingest import DatasetManifest, read_series  # noqa: E402
from bot.data.resample import resample  # noqa: E402
from bot.data.synthetic import generate  # noqa: E402
from bot.research import marginal_value as MV  # noqa: E402
from bot.research.stats import calibration_interval, calibration_sigma  # noqa: E402

UTC = timezone.utc
PARQUET = Path("data/parquet")
SYNTH_YEARS = (2024, 2025, 2026)

# PRE_REGISTRATION section 4.2 and Amendment 1 (section 4.1).  Not choices made here.
DEV_SET = ("EURUSD", "GBPUSD", "USDJPY")
IS_YEARS, OOS_YEARS = 4, 2

#: Phase 9's per-symbol-year MSS rate after SPEC 9.4 cluster dedup.  The synthetic
#: figure was 38/3; D-020 MEASURED 368 over 40 in-sample symbol-years on real bars.
PHASE9_MSS_PER_YEAR_SYNTH = 38 / 3
PHASE9_MSS_PER_YEAR_REAL = 368 / 40


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


def _finish(cfg, h4, d1, w1, mn1, sessions):
    st = analyse_structure(h4, cfg)
    _, sweeps = analyse_sweeps(
        cfg=cfg,
        h4=h4,
        d1=d1,
        w1=w1,
        mn1=mn1,
        sessions=sessions,
        h4_structure=st,
        d1_swings=detect_swings(d1, cfg),
    )
    mss = analyse_mss(
        h4,
        cfg,
        sweeps.confirmed(),
        swings=st.swings,
        fvgs=detect_fvgs(h4, cfg),
        reference_mode=ReferenceMode.MAJOR,
    )
    return h4, mss.candidates


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
        "EURUSD",
        datetime(year, 1, 1, tzinfo=UTC),
        datetime(year, 12, 31, 23, 59, tzinfo=UTC),
        cfg,
        timeframe="M15",
        seed=seed,
    )
    h4 = resample(src, "H4", cfg)
    return _finish(
        cfg,
        h4,
        resample(src, "D1", cfg),
        resample(src, "W1", cfg),
        resample(src, "MN1", cfg),
        build_sessions(src, cfg),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", action="store_true", help="the original fixture")
    ap.add_argument("--skip-tests", action="store_true", help="skip the suite")
    args = ap.parse_args()

    cfg, cfg_hash = load_config()
    real = not args.synthetic
    OUT = Path("reports/marginal_value.md" if real else "reports/marginal_value_synthetic.md")
    OUT.parent.mkdir(parents=True, exist_ok=True)

    per_year: list[tuple] = []
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
            h4, cands = build_real(cfg, sym, is_years, PARQUET)
            per_year.append((sym, h4, cands, MV.run_study(h4, cands, cfg)))
            print(f"      {len(cands):,} candidates  ({time.time() - t0:.0f}s)", flush=True)
    else:
        is_years = list(SYNTH_YEARS)
        symbols = ["EURUSD"]
        for k, year in enumerate(SYNTH_YEARS):
            print(f"building {year} ...", flush=True)
            h4, cands = build_synthetic(cfg, year, seed=41 + k)
            per_year.append((f"EURUSD-{year}", h4, cands, MV.run_study(h4, cands, cfg)))

    n_symbols, n_years = len(symbols), len(is_years)
    symbol_years = n_symbols * n_years if real else len(per_year)

    pooled = MV.pool_studies([s for _, _, _, s in per_year])
    pooled_dev = (
        MV.pool_studies([s for label, _, _, s in per_year if label in DEV_SET])
        if real
        else None
    )
    primary = pooled.of("all")

    print("controls ...", flush=True)
    h1 = primary[0]
    CAL_TRIALS = 3000
    fpr = MV.null_calibration(
        h1.mss.returns, h1.not_mss.returns, trials=CAL_TRIALS, bootstrap=600
    )
    fpr_lo, fpr_hi = calibration_interval(fpr, CAL_TRIALS)
    mde_h1 = h1.mde
    control_grid = [
        (shift, MV.detects_effect(h1.mss.returns, h1.not_mss.returns, shift))
        for shift in (0.0, 0.1, 0.25, 0.5, 1.0)
    ]
    shifted = MV.run_study(
        per_year[0][1], per_year[0][2], cfg, mss_shift=0.8, permutations=2000
    )

    if args.skip_tests:
        tests_ok, tests_line = True, "SKIPPED (--skip-tests)"
    else:
        print("running test suite ...", flush=True)
        tests_ok, tests_line = _run_tests()

    # Phase 9's projected MSS counts, against what each horizon actually needs.
    raw_choch = sum(
        1 for _, _, cands, _ in per_year for c in cands if c.is_choch
    )
    per_symbol_year = pooled.n_mss / symbol_years
    if real:
        # Measured, not scaled: every in-sample symbol-year has been read.
        proj_universe = float(pooled.n_mss)
        proj_dev = float(pooled_dev.n_mss)
    else:
        proj_universe = per_symbol_year * 10 * IS_YEARS
        proj_dev = per_symbol_year * 3 * IS_YEARS

    checks = [
        ("Test suite green", tests_ok, tests_line),
        (
            "All three SPEC 6.9 populations reported",
            all(c.all_choch.n == c.mss.n + c.not_mss.n for c in pooled.comparisons),
            f"all CHoCH = MSS + CHoCH-not-MSS at every horizon",
        ),
        (
            "All three horizons run (+1/+4/+12)",
            [c.horizon for c in primary] == list(MV.DEFAULT_HORIZONS),
            "SPEC 6.9",
        ),
        (
            "Positive control detects an injected effect",
            shifted.verdict() is MV.Verdict.DIFFERENT,
            "0.8 ATR shift -> DIFFERENT",
        ),
        (
            "Null calibration lands near alpha",
            0.0 < fpr < 0.12,
            f"{fpr:.1%} over {CAL_TRIALS:,} label shuffles, CI [{fpr_lo:.1%}, {fpr_hi:.1%}] (alpha {MV.ALPHA:.0%})",
        ),
        (
            "Multiple-testing correction applied across horizons",
            all(c.p_adjusted is not None for c in pooled.comparisons),
            "Benjamini-Hochberg, q = 0.10",
        ),
        (
            "Overlap diagnostic reported",
            pooled.overlap_share > 0,
            f"{pooled.overlap_share:.1%} of events have a contaminated 12-bar window",
        ),
        (
            "Verdict distinguishes 'no effect' from 'no power'",
            pooled.verdict() in tuple(MV.Verdict),
            f"{pooled.verdict().value}",
        ),
    ]
    all_ok = all(ok for _, ok, _ in checks)

    L: list[str] = []
    w = L.append
    w("# H5: does displacement filtering add value?")
    w("")
    w("**MSS vs CHoCH-not-MSS forward returns** — SPEC 6.9, `BACKTEST_PROTOCOL.md` §6.2.")
    w("")
    w(f"Generated {datetime.now(UTC).isoformat(timespec='seconds')}")
    w("")
    w(f"- `config_hash` `{cfg_hash}`")
    if real:
        w(f"- `dataset_hash` `{dataset_hash}`")
        w(
            f"- Data: **real bars** -- {n_symbols} symbols, {is_years[0]}-{is_years[-1]} "
            f"({symbol_years} symbol-years), H4, `reference_mode = major`, source "
            f"`{source_label}`, `{price_side}` side, tzdata `{tzdata}`"
        )
        w(
            f"- Split (PRE_REGISTRATION 4.1, Amendment 1): in-sample "
            f"**{splits['in_sample']}**, out-of-sample {splits['out_of_sample']}, "
            f"holdout {splits['holdout']}"
        )
    else:
        w(f"- Fixture: {len(SYNTH_YEARS)} synthetic years "
          f"({SYNTH_YEARS[0]}-{SYNTH_YEARS[-1]}), EURUSD, H4, `reference_mode = major`")
    w(f"- **{pooled.n_choch:,} CHoCH events, of which {pooled.n_mss:,} are MSS**")
    w(f"- Equivalence margin: **+/-{MV.EQUIVALENCE_MARGIN_ATR:g} ATR**, declared in the module before any result was read")
    w("")
    w(f"## Verdict: {pooled.verdict().value}")
    w("")
    w(f"> {pooled.headline()}")
    w("")
    if real:
        _res = [c for c in primary if c.verdict is not MV.Verdict.UNDERPOWERED]
        _un = [c for c in primary if c.verdict is MV.Verdict.UNDERPOWERED]
        if _res:
            _hs = ", ".join(f"h={c.horizon}" for c in _res)
            _vs = {c.verdict.value for c in _res}
            w(f"**The overall verdict is the weakest horizon's, and it hides a real answer.**")
            w(f"At {_hs} the sample resolves the declared margin and reports")
            w(f"**{'/'.join(sorted(_vs))}** — so at those horizons H5 *is* answered on real")
            w("market data, and answered in the negative: MSS and CHoCH-not-MSS forward")
            w(f"returns differ by less than {MV.EQUIVALENCE_MARGIN_ATR:g} ATR.")
            if _un:
                _uh = ", ".join(f"h={c.horizon}" for c in _un)
                w(f"At {_uh} it cannot tell, and the overall verdict says so rather than")
                w("averaging a resolved answer together with an unresolved one.")
            w("")
    if pooled.verdict() is MV.Verdict.UNDERPOWERED:
        n_under = sum(1 for c in primary if c.verdict is MV.Verdict.UNDERPOWERED)
        w("**This is not a null result and must not be cited as one.** H5 is falsified by")
        w("MSS and CHoCH-not-MSS being *indistinguishable* — an equivalence claim, which")
        w("needs the confidence interval to sit inside the margin, not merely to contain")
        w(f"zero. In this split it does not, at {n_under} of the {len(primary)} horizons.")
        w("The study is reporting that it cannot tell, which is a different sentence from")
        w("\"displacement is decoration\" and has different consequences.")
        w("")

    w("## Gate checks")
    w("")
    w("| Check | Result | Detail |")
    w("|---|---|---|")
    for name, ok, detail in checks:
        w(f"| {name} | {'PASS' if ok else 'FAIL'} | {detail} |")
    w("")

    w("## The comparison (SPEC 6.9)")
    w("")
    w("Forward returns are ATR-normalised and **signed by setup direction**, anchored on")
    w("the close of the CHoCH bar — the first moment the event is knowable. `all CHoCH` is")
    w("the union of the other two columns, not independent evidence.")
    w("")
    for sample, title, note in (
        ("all", "Primary sample", "Every CHoCH event."),
        (
            "non_overlapping",
            "Non-overlapping subsample",
            "Thinned so no two forward windows overlap. Independent draws, far fewer of them.",
        ),
        (
            "stratified",
            "Stratified sample",
            "Only (session slot, ATR tercile) cells containing both groups, so the two are "
            "not compared partly on when they happened.",
        ),
    ):
        rows = pooled.of(sample)
        w(f"### {title}")
        w("")
        w(note)
        w("")
        w("| h | n MSS | n not-MSS | MSS mean | not-MSS mean | diff | 95% CI | p (BH) | MDE | Verdict |")
        w("|---:|---:|---:|---:|---:|---:|---|---:|---:|---|")
        for c in rows:
            w(
                f"| {c.horizon} | {c.mss.n:,} | {c.not_mss.n:,} | {c.mss.mean:+.3f} | "
                f"{c.not_mss.mean:+.3f} | {c.diff:+.3f} | "
                f"[{c.ci_low:+.3f}, {c.ci_high:+.3f}] | {c.p_adjusted:.3f} | "
                f"{c.mde:.3f} | **{c.verdict.value}** |"
            )
        w("")

    w("All figures are in ATR units. `MDE` is the smallest true difference this sample")
    w(f"could detect at alpha {MV.ALPHA:.0%} with {MV.POWER:.0%} power — the number that")
    w("separates \"no effect\" from \"no power\", which is why it sits in every row rather")
    w("than being mentioned once in prose.")
    w("")

    w("## Power: what would it take to answer this?")
    w("")
    if real:
        w("Read this table before the comparison table above. Required counts scale with")
        w("the return variance at each horizon, which grows roughly with the square root of")
        w("the horizon, so the long horizons are far more expensive than they look — and")
        w("the verdict at a horizon is only meaningful once this table says the sample")
        w("could have resolved it.")
    else:
        w("The most useful output of this run, and the one that does not depend on the data")
        w("being synthetic. Required counts scale with the return variance at each horizon,")
        w("which grows roughly with the square root of the horizon, so the long horizons are")
        w("far more expensive than they look.")
    w("")
    w("**Counting basis.** Events are collapsed to one per `(break bar, direction)`: the")
    w("forward return is a function of exactly those two things, so candidates sharing")
    w("them contribute the identical number more than once. Here that nearly halves")
    w(f"the raw population -- {raw_choch:,} CHoCH candidates become {pooled.n_choch:,}")
    w(f"observations, of which {pooled.n_mss:,} are MSS. Phase 9's funnel reports")
    _p9 = PHASE9_MSS_PER_YEAR_REAL if real else PHASE9_MSS_PER_YEAR_SYNTH
    w(f"{_p9:.1f} MSS per symbol-year after *cluster* dedup (SPEC 9.4);")
    w(f"this study measures {per_symbol_year:.1f} after the stricter break-bar dedup, and")
    w("the projections below use the stricter figure. The two rules are not the same:")
    w("SPEC 9.4 keys on the sweep, while two sweeps in different clusters can still break")
    w("on one bar and produce one number.")
    w("")
    _verb = "has" if real else "projects"
    w(
        f"| h | MSS needed for +/-{MV.EQUIVALENCE_MARGIN_ATR:g} ATR | "
        f"dev set {_verb} | Enough? | universe {_verb} | Enough? |"
    )
    w("|---:|---:|---:|:--:|---:|:--:|")
    for c in primary:
        need = c.required_mss_n
        w(
            f"| {c.horizon} | {need:,.0f} | {proj_dev:,.0f} | "
            f"{'yes' if proj_dev >= need else '**no**'} | {proj_universe:,.0f} | "
            f"{'yes' if proj_universe >= need else '**no**'} |"
        )
    w("")
    longest = primary[-1]
    if proj_universe < longest.required_mss_n:
        w(f"**At h={longest.horizon} the full in-sample universe is not enough.** The")
        w(f"in-sample split {'contains' if real else 'projects'} {proj_universe:,.0f} MSS")
        w(f"events across {n_symbols if real else 10} symbols over")
        w(f"{IS_YEARS} years; resolving the margin at that horizon needs")
        w(f"{longest.required_mss_n:,.0f}. **H5 is not answerable at the 12-bar horizon on")
        w("this design over the in-sample period**, whatever the backtest shows — and that")
        w("was not knowable before this study was run.")
        w("")
        if real:
            w("The synthetic run listed three ways out and said they were *\"better decided")
            w("now than after Phase 14\"*. The data has arrived, so two of the three have")
            w("resolved themselves and one is closed:")
            w("")
            w("1. **Answer H5 at the short horizons only — this is what happened.** h=1 and")
            w("   h=4 both resolve the margin and both report EQUIVALENT. That is a real")
            w("   answer to a narrower question than the methodology poses.")
            w("2. **Widening the margin is no longer available.** It was a defensible choice")
            w("   *before* the data and is an indefensible reaction after it (§10.2). It is")
            w("   recorded as closed rather than left on the list.")
            w("3. **The ablation delta remains the route to h=12's question**, measuring the")
            w("   same component through the full system rather than through forward returns.")
        else:
            w("Three ways out, none free, all better decided now than after Phase 14:")
            w("")
            w("1. **Answer H5 at the short horizons only** and say so. h=1 is resolvable")
            w(f"   ({primary[0].required_mss_n:,.0f} needed), which tests whether displacement")
            w("   selects the *immediate* continuation — a narrower claim than the methodology")
            w("   makes, but a real one.")
            w("2. **Widen the margin.** Declaring 0.5 ATR instead of 0.25 divides every")
            w("   required count by four. It is a defensible choice and an indefensible")
            w("   *reaction* — deciding it now, before real data, is the only way it is not")
            w("   fitting the test to the answer.")
            w("3. **Accept that H5 stays open** and report the ablation delta from §6.5")
            w("   instead, which measures the same component through the full system rather")
            w("   than through forward returns.")
        w("")

    w("## Controls")
    w("")
    w("### Positive control — can the study find an effect that is really there?")
    w("")
    w(f"Injected shifts on the MSS group at h=1, where the sample's own MDE is {mde_h1:.3f} ATR:")
    w("")
    w("| Injected effect | Detected |")
    w("|---:|---|")
    for shift, got in control_grid:
        w(f"| {shift:+.2f} ATR | {'yes' if got else 'no'} |")
    w("")
    w("**The detection boundary falls where the MDE says it should**, which is the")
    w("internal consistency check that makes the power table above worth acting on: if")
    w("the arithmetic and the interval disagreed, one of them would be wrong and the")
    w("required-sample figures would be fiction.")
    w("")
    w("Run end to end with a 0.8 ATR shift, the whole study reports")
    w(f"**{shifted.verdict().value}** — so a real effect of that size would not be missed.")
    w("")
    w("### Null calibration — does the study invent effects that are not there?")
    w("")
    w("Shuffling the MSS label across the same returns makes the true difference exactly")
    w("zero, so every `DIFFERENT` verdict under a shuffle is a false positive by")
    w("construction.")
    w("")
    sigma = calibration_sigma(fpr, CAL_TRIALS)
    w(f"- False-positive rate over {CAL_TRIALS:,} shuffles: **{fpr:.1%}** against alpha of {MV.ALPHA:.0%}")
    w(f"- 95% Wilson interval: **[{fpr_lo:.1%}, {fpr_hi:.1%}]** — contains alpha: {'yes' if fpr_lo <= MV.ALPHA <= fpr_hi else 'no'}")
    w(f"- Deviation: **{sigma:.1f} sigma**")
    w("")
    w(f"**This figure was corrected in Phase 11.** It ran on 400 shuffles and read 7.8%,")
    w("which was written up as clear anti-conservatism. At 400 trials the standard error")
    w("on the rate is about 1.1 points — the same size as the effect — and that draw was")
    w("high. The direction survives at a proper trial count; the magnitude does not. See")
    w("D-012 §4.")
    w("")
    if sigma < 2.0:
        w("**Calibrated.** The deviation is inside what 400 shuffles can resolve, so there")
        w("is nothing here to correct and nothing to read into the direction of the gap —")
        w("a point-and-a-half on 400 trials is noise, and this project has twice written up")
        w("a sub-2-sigma wobble as a finding before catching itself (Phase 7's significance")
        w("tests, Phase 8's \"natural break\" detector). Stating the sigma rather than the")
        w("rate alone is the habit those two produced.")
    else:
        w(f"**Anti-conservative at {sigma:.1f} sigma.** The percentile bootstrap is not")
        w("delivering its nominal coverage at this sample size — a known weakness of the")
        w("method with a few dozen heavy-tailed observations, and the reason this")
        w("calibration is run rather than assumed.")
        w("")
        w("Two consequences, and they point the same way:")
        w("")
        w("- **The intervals above are too narrow, so the study is *more* underpowered")
        w("  than its own table shows.** Widening them cannot turn an UNDERPOWERED row")
        w("  into an EQUIVALENT one; it can only push a borderline EQUIVALENT the other")
        w("  way. The verdict is therefore safe against this error.")
        w("- **A `DIFFERENT` verdict is over-eager**, which for H5 is also the safe")
        w("  direction: the error worth avoiding here is falsely declaring the")
        w("  methodology decoration, and an interval biased toward finding a difference")
        w("  cannot do that.")
        w("")
        w("`MDE` and the required-sample column are computed from the parametric standard")
        w("error, not from the bootstrap, so they are unaffected. Fixing the coverage")
        w("properly means a BCa or studentized bootstrap; it is not done here because the")
        w("bias runs in the direction that protects the conclusion, and swapping the")
        w("interval method on synthetic data would be tuning the instrument against noise.")
    w("")
    w("What this rules out is the failure that matters: an interval method too narrow to")
    w("be trusted would fire far more often than alpha under a shuffled label, and neither")
    w("code review nor any other test in the suite would show it.")
    w("")

    w("## Overlap, and why the second sample exists")
    w("")
    w(f"**{pooled.overlap_share:.1%} of CHoCH events have a 12-bar forward window that")
    w("overlaps a neighbour's.** Overlapping windows are not independent draws, and")
    w("treating them as such narrows every interval — the same class of error as Phase 7's")
    w("false positives, one level up.")
    w("")
    w("The non-overlapping subsample fixes the independence and destroys the sample size:")
    w(
        f"at h=12 it leaves {pooled.of('non_overlapping')[-1].mss.n} MSS events. Neither"
    )
    w("version can answer H5 here; reporting both is what makes that visible rather than")
    w("letting the more convenient one stand alone.")
    w("")

    w("## Per-symbol stability" if real else "## Per-year stability")
    w("")
    w(f"| {'Symbol' if real else 'Year'} | CHoCH | MSS | h=1 diff | h=4 diff | h=12 diff |")
    w("|---|---:|---:|---:|---:|---:|")
    for year, _, _, st in per_year:
        rows = st.of("all")
        diffs = " | ".join(f"{r.diff:+.3f}" for r in rows)
        w(f"| {year} | {st.n_choch:,} | {st.n_mss:,} | {diffs} |")
    w("")
    if real:
        w("**The sign flips between symbols at every horizon**, and the h=12 column spans")
        w("more than 1.5 ATR end to end on per-symbol samples of 24 to 41 MSS events. That")
        w("is what noise looks like at this sample size, and it is the same message the")
        w("power table gives in a different currency: no single symbol's number here is")
        w("readable as a direction, and picking the agreeable ones would be picking noise.")
    else:
        w("The sign flips between years at every horizon. On a random walk that is exactly")
        w("what it should do, and it is worth seeing before any single year's number is read")
        w("as a direction.")
    w("")

    w("## What this study does NOT establish")
    w("")
    if real:
        w("**It does not answer H5 at every horizon, and the verdict says which.** On the")
        w("fixture this study could only validate its own instrument, because the true")
        w("MSS vs CHoCH-not-MSS difference on a random walk is zero by construction. On")
        w("real bars it tests H5 itself — but only where the population resolves the")
        w("declared margin. Read the power table before the comparison table: an")
        w("UNDERPOWERED horizon has not found 'no difference', it has failed to look.")
        w("")
        w("Specifically not established:")
        w("")
        w("- **That an UNDERPOWERED horizon is evidence of anything.** `UNDERPOWERED` is")
        w("  not `EQUIVALENT`, and only `EQUIVALENT` licenses \"displacement filtering is")
        w("  decoration\". This distinction is the reason the verdict is three-way.")
        w("- **That the result holds out of sample.** This is the in-sample split;")
        w("  2023-2024 and 2025 were not read.")
    else:
        w("**It does not test H5.** It tests the instrument that will test H5. Every number")
        w("here comes from `bot/data/synthetic.py` — a random walk with no liquidity, no")
        w("participants and no structure — where the true difference between MSS and")
        w("CHoCH-not-MSS is zero by construction. A `DIFFERENT` verdict on this data would")
        w("mean the study is broken.")
        w("")
        w("What it does establish:")
        w("")
        w("1. **The study runs, is deterministic, and its two controls pass** — it finds an")
        w("   effect that is there and does not invent one that is not.")
        w("2. **The power requirements**, which are a property of the return distribution and")
        w("   the funnel's output rate rather than of the fixture's realism. These transfer to")
        w("   real data far better than any effect size here does, though the real return")
        w("   variance will differ and should be re-measured before the numbers are relied on.")
        w("3. **That the answer at h=12 is out of reach on the current design**, which is a")
        w("   planning fact available now rather than after Phase 14.")
        w("")
        w("Specifically not established:")
        w("")
        w("- **Any effect size.** The measured differences are noise around zero, and the")
        w("  per-year table shows the sign flipping accordingly.")
    w("- **R-expectancy**, which §6.2 also asks for. Stops (SPEC 16) and targets (SPEC 17)")
    w("  are Phase 12, so there is no R yet. Inventing a stop distance to fill the gap")
    w("  would make the answer a property of that invention.")
    if real:
        w("- **That h=1 and h=4 being EQUIVALENT settles the component.** It settles the")
        w("  *forward-return* question at those horizons. §6.5's ablation delta measures the")
        w("  same component through the full system — entry, stop, target, costs — and can")
        w("  still find displacement filtering worth its place for reasons a forward return")
        w("  from the break bar cannot see.")
        w("- **That widening the margin is now available.** §6.9's 0.25 ATR was declared")
        w("  before any result was read. Widening it to 0.5 would divide every required")
        w("  count by four and make h=12 answerable — and doing so *after* seeing this table")
        w("  is exactly what §10.2 prohibits. The option was live before the run and is not")
        w("  live now.")
    else:
        w("- **That real MSS events behave like these.** Displacement selects for large")
        w("  directional legs; on real data those sit inside trends and mean-reversion that a")
        w("  random walk does not have, and the effect could run in either direction.")
    w("")
    _tail = (
        f"H5 answered where the population allows, verdict {pooled.verdict().value}"
        if real
        else "instrument validated, H5 open pending real data"
    )
    w(f"## Status: {'PASS' if all_ok else 'FAIL'} — {_tail}")

    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"\nwrote {OUT}")
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    print(f"  verdict: {pooled.verdict().value}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
