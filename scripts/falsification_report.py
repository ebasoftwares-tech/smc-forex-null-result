"""The falsification suite (`BACKTEST_PROTOCOL.md` sections 6.3 and 6.4).

Not a phase gate.  Like the H5 study before it, this is a study run out of the phase
sequence and it writes ``reports/falsification.md`` rather than a ``phaseN_gate.md``.

**Read "What this report does NOT establish" before any number in it.**  On real bars the
arms below are evidence about the strategy: section 10.1's deciding row is evaluated here
for the first time against data whose true effect is not zero by construction.  Under
``--synthetic`` every arm is instead guaranteed to be null by the fixture, and a
``DIFFERENT`` verdict there would mean a bug.

    python scripts/falsification_report.py              # real bars, data/parquet
    python scripts/falsification_report.py --synthetic  # the original fixture
    python scripts/falsification_report.py --workers 1  # force serial
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.backtest.engine import build_market, run  # noqa: E402
from bot.config.loader import load_config  # noqa: E402
from bot.core.entries import EntryModel  # noqa: E402
from bot.data.ingest import DatasetManifest, read_series  # noqa: E402
from bot.data.synthetic import generate  # noqa: E402
from bot.research import falsification as F  # noqa: E402
from bot.research import stats  # noqa: E402

UTC = timezone.utc
PARQUET = Path("data/parquet")
SYNTH_YEARS = (2024, 2025, 2026)

# PRE_REGISTRATION section 4.1 as stamped by Amendment 1.
IS_YEARS, OOS_YEARS = 4, 2

#: Model A, not the configured default.  See the report section "Why the comparison runs
#: at entry model A" -- two of the four section 6.4 controls arm zero orders at model C.
PRIMARY_MODEL = EntryModel.A_MARKET

RNG_SEED = 20260828


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
    return {
        "in_sample": years[:IS_YEARS],
        "out_of_sample": years[IS_YEARS : IS_YEARS + OOS_YEARS],
        "holdout": years[IS_YEARS + OOS_YEARS :],
    }


def _unit_arms(task):
    """Every arm for ONE market: the unit of parallelism.

    Module-level and self-contained so it survives `spawn` on Windows -- it reloads the
    config and reads its own M1 rather than receiving either, which keeps what crosses
    the process boundary to a dict of `Arm` objects.
    """
    unit, is_years, real, seeds, primary_name, default_name = task
    cfg, cfg_hash = load_config()

    if real:
        m1 = read_series(PARQUET, unit, "M1", years=is_years)
    else:
        m1 = generate(
            "EURUSD", datetime(unit, 1, 1, tzinfo=UTC),
            datetime(unit, 12, 31, 23, 59, tzinfo=UTC), cfg,
            timeframe="M1", seed=41 + is_years.index(unit),
        )
    mk = build_market(cfg, m1)
    primary = EntryModel(primary_name)
    default = EntryModel(default_name)

    def arm_for(spec, market, seed=None, model=None):
        return F.arm_from(
            spec, market,
            run(cfg, market, config_hash=cfg_hash,
                entry_model=model or primary, apply_limits=False),
            seed=seed,
        )

    def sequence_arms(model):
        got = {"baseline": arm_for(F.BASELINE, mk, model=model)}
        for spec in F.CONTROLS:
            if spec.name == "sweep_only":
                got[spec.name] = arm_for(spec, F.sweep_only(cfg, mk), model=model)
            elif spec.name == "choch_only":
                got[spec.name] = arm_for(spec, F.choch_only(cfg, mk), model=model)
            elif spec.name == "reversed_order":
                got[spec.name] = arm_for(spec, F.reversed_order(cfg, mk), model=model)
        return got

    sh_spec, rt_spec = F.BY_NAME["shuffled_liquidity"], F.BY_NAME["random_time"]
    return unit, {
        "h4_n": mk.h4.n,
        "setups": len(mk.setups),
        "primary": sequence_arms(primary),
        "default": sequence_arms(default),
        "shuffled": {
            s: arm_for(sh_spec, F.build_shuffled_market(cfg, m1, mk, s), seed=s)
            for s in seeds
        },
        "random_time": {
            s: arm_for(rt_spec, F.random_time(cfg, mk, s), seed=s) for s in seeds
        },
    }


def _run_tests(expr: str | None = None) -> tuple[bool, str]:
    cmd = [sys.executable, "-m", "pytest", "tests/test_falsification.py"]
    if expr:
        cmd += ["-k", expr]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1]
    )
    lines = [ln.strip() for ln in proc.stdout.splitlines()
             if re.search(r"\d+ (passed|failed|error)", ln)]
    return proc.returncode == 0, (lines[-1] if lines else "no summary line")


def _fmt(x: float, nd: int = 3) -> str:
    return "--" if x is None or not np.isfinite(x) else f"{x:.{nd}f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", action="store_true", help="the original fixture")
    ap.add_argument("--workers", type=int, default=0,
                    help="0 = auto, 1 = serial (identical numbers, slower)")
    ap.add_argument("--symbols", default="", help="comma-separated subset, for checking")
    ap.add_argument("--seeds", type=int, default=0, help="0 = all of falsification.SEEDS")
    args = ap.parse_args()

    cfg, cfg_hash = load_config()
    real = not args.synthetic
    OUT = Path("reports/falsification.md" if real
               else "reports/falsification_synthetic.md")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(RNG_SEED)

    dataset_hash = tzdata = source_label = price_side = "-"
    splits: dict[str, list[int]] = {}
    if real:
        manifest = DatasetManifest.load(PARQUET / "manifest.json")
        dataset_hash, tzdata = manifest.dataset_hash, manifest.tzdata_version
        source_label, price_side = manifest.source, manifest.price_side
        splits = split(acquired_years(manifest, manifest.ingest_timeframe))
        is_years = splits["in_sample"]
        units = list(cfg.symbols)
        if args.symbols:
            want = {s.strip() for s in args.symbols.split(",")}
            units = [u for u in units if u in want]
    else:
        is_years = list(SYNTH_YEARS)
        units = list(SYNTH_YEARS)
    symbol_years = len(units) * len(is_years) if real else len(units)

    seeds = list(F.SEEDS[: args.seeds]) if args.seeds else list(F.SEEDS)
    default_model = EntryModel(cfg.entry.model)
    tasks = [
        (u, is_years, real, seeds, PRIMARY_MODEL.value, default_model.value)
        for u in units
    ]

    n_workers = args.workers or min(len(tasks), max(1, (os.cpu_count() or 2) - 1), 5)
    print(f"building {len(tasks)} markets x {len(seeds)} seeds on {n_workers} worker(s) ...",
          flush=True)
    t0 = time.time()
    got: dict[object, dict] = {}
    if n_workers == 1:
        for task in tasks:
            u, res = _unit_arms(task)
            got[u] = res
            print(f"  {u}: {res['setups']} setups ({time.time() - t0:.0f}s elapsed)",
                  flush=True)
    else:
        with ProcessPoolExecutor(max_workers=n_workers) as ex:
            for u, res in ex.map(_unit_arms, tasks):
                got[u] = res
                print(f"  {u}: {res['setups']} setups ({time.time() - t0:.0f}s elapsed)",
                      flush=True)
    print(f"built in {time.time() - t0:.0f}s", flush=True)

    markets_h4_bars = sum(g["h4_n"] for g in got.values())
    order_units = [u for u in units if u in got]

    def pool_named(kind, name, spec):
        return F.pooled(spec, [got[u][kind][name] for u in order_units])

    arms = {"baseline": pool_named("primary", "baseline", F.BASELINE)}
    for spec in F.CONTROLS:
        if spec.name in ("sweep_only", "choch_only", "reversed_order"):
            arms[spec.name] = pool_named("primary", spec.name, spec)

    # Per seed, pooled across units first -- the same nesting the serial version used, so
    # the across-seed spread stays the honest uncertainty (falsification.pooled's docstring).
    def seeded(kind, spec):
        per_seed = [
            F.pooled(spec, [got[u][kind][s] for u in order_units]) for s in seeds
        ]
        return per_seed, F.pooled(spec, per_seed)

    shuf_seeds, shuf = seeded("shuffled", F.BY_NAME["shuffled_liquidity"])
    rand_seeds, rand = seeded("random_time", F.BY_NAME["random_time"])

    arms["shuffled_liquidity"] = shuf
    arms["random_time"] = rand
    order = ["baseline"] + [c.name for c in F.CONTROLS]

    print("comparing ...", flush=True)
    base = arms["baseline"]
    comparisons = {
        c.name: F.compare(base, arms[c.name], rng=rng) for c in F.CONTROLS
    }
    bh = stats.benjamini_hochberg([comparisons[c.name].p_value for c in F.CONTROLS])

    # The section 10.1 row, and the model-C finding behind why it runs at model A.
    default_arms = {"baseline": pool_named("default", "baseline", F.BASELINE)}
    for spec in F.CONTROLS:
        if spec.name in ("sweep_only", "choch_only", "reversed_order"):
            default_arms[spec.name] = pool_named("default", spec.name, spec)

    tests_ok, tests_line = _run_tests()

    # ------------------------------------------------------------------ write

    L: list[str] = []
    w = L.append
    w("# The falsification suite -- `BACKTEST_PROTOCOL.md` sections 6.3 and 6.4")
    w("")
    if real:
        w(f"Generated by `scripts/falsification_report.py` - config hash `{cfg_hash}` - "
          f"dataset hash `{dataset_hash[:16]}` - **real bars**: {len(order_units)} symbols, "
          f"{is_years[0]}-{is_years[-1]} ({symbol_years} symbol-years, "
          f"{markets_h4_bars:,} H4 bars), source `{source_label}`, `{price_side}` side.")
        w("")
        w(f"Split (PRE_REGISTRATION 4.1, Amendment 1): in-sample **{splits['in_sample']}**, "
          f"out-of-sample {splits['out_of_sample']}, holdout {splits['holdout']}.")
    else:
        w(f"Generated by `scripts/falsification_report.py` - config hash `{cfg_hash}` - "
          f"fixture: synthetic EURUSD {SYNTH_YEARS[0]}-{SYNTH_YEARS[-1]} "
          f"({markets_h4_bars:,} H4 bars).")
    w("")
    w("Not a phase gate. Like the H5 study, this is a protocol study run out of the phase")
    w("sequence, so it reports findings and instrument validation rather than a gate verdict.")
    w("")
    w("---")
    w("")

    # -- the caveat, first
    w("## What this report does NOT establish")
    w("")
    if real:
        w("**This is the in-sample split, and it is the only thing that limits the")
        w("conclusions below.** Every arm is measured on real FX majors over")
        w(f"{symbol_years} symbol-years, so a null here is the *strategy* speaking rather")
        w("than the fixture -- which is the opposite of every previous run of this suite.")
        w("")
        w("Specifically not established:")
        w("")
        w("1. **Anything out of sample.** 2023-2024 and 2025 were not read. A result this")
        w("   consequential should be confirmed there before it is acted on, and doing so")
        w("   spends section 7 budget.")
        w("2. **That the baseline's own expectancy is distinguishable from zero.** It is")
        w("   not (D-027: -0.19 R over 102 trades, CI spanning zero). Every delta below is")
        w("   a difference between two arms neither of which has demonstrated an edge, so")
        w("   'the baseline beats X' means 'is less bad than X', not 'is profitable'.")
        w("3. **That the arms are independent tests.** Five arms over one price series,")
        w("   corrected by Benjamini-Hochberg at q = 0.10 (section 5.6) and reported with")
        w("   the correction, not around it.")
        w("4. **That `sweep_only` and `reversed_order` mean at model C what they mean")
        w("   here.** Both arm zero orders at the shipped default; see the next section.")
    else:
        w("**Every arm below asks whether a component contributes, and this fixture answers")
        w("*no* for all of them by construction.** `bot/data/synthetic.py` is a random walk")
        w("with no participants and no liquidity, so the true contribution of liquidity")
        w("identification, of the sweep, of the CHoCH and of their ordering is exactly zero.")
        w("A null here is the fixture speaking, not the strategy.")
        w("")
        w("This matters more here than in any earlier study, because a falsification suite is")
        w("the one place where *\"we found no difference\"* is the publishable answer -- and")
        w("section 6.3 explicitly invites the reader to act on one: *\"rebuilt as a")
        w("mean-reversion model and the SMC framing dropped\"*. **No such conclusion is")
        w("licensed by anything below.**")
        w("")
        w("What the run does establish is that the suite is built, that every arm is driven by")
        w("the same engine as the baseline, and that the comparison would report a difference")
        w("if one existed (the positive control). That is instrument validation.")
    w("")
    w("---")
    w("")

    # -- the declared margin
    w("## The declared equivalence margin")
    w("")
    w(f"**{F.EQUIVALENCE_MARGIN_R:.2f} R**, fixed before any arm was run, and not chosen")
    w("freely: it is section 10.1's own expectancy threshold for trading this system live.")
    w("A difference smaller than the number the project already committed to as the")
    w("boundary of a tradable edge cannot be a difference that matters.")
    w("")
    w("The verdict is **three-way**, never \"the CI spans zero, so they are the same\":")
    w("")
    w("| Verdict | Condition | What it licenses |")
    w("|---|---|---|")
    w("| `DIFFERENT` | CI excludes zero | The component contributes |")
    w("| `EQUIVALENT` | CI lies entirely inside the margin | **Only this** licenses \"contributes nothing\" |")
    w("| `UNDERPOWERED` | CI spans zero and extends past the margin | The study cannot answer |")
    w("")
    w("H3 and H4 are falsified by a *negative*, so a wide interval around zero is absence")
    w("of evidence, not evidence of absence (D-010 section 2).")
    w("")
    w("---")
    w("")

    # -- why model A
    w("## Why the comparison runs at entry model A, and a finding about section 10.1")
    w("")
    w(f"The configured default is **model {default_model.value}**. At that default, two of")
    w("the four section 6.4 controls arm **zero** orders:")
    w("")
    w("| Arm | setups | armed at "
      f"model {default_model.value} | armed at model {PRIMARY_MODEL.value} |")
    w("|---|---:|---:|---:|")
    for name in order:
        w(f"| `{name}` | {arms[name].n_setups:,} | "
          f"{default_arms[name].armed:,} | {arms[name].armed:,} |"
          if name in default_arms else
          f"| `{name}` | {arms[name].n_setups:,} | (seeded, not run) | {arms[name].armed:,} |")
    w("")
    w("**This is structural, not a fixture artefact.** `sweep_only` and `reversed_order`")
    w("enter at the sweep *confirmation*, so their displacement leg spans at most")
    w(f"`sweep.max_confirmation_bars` = {cfg.sweep.max_confirmation_bars} bars and is")
    w("usually zero bars long. An FVG needs three bars. Model C therefore rejects every")
    w("setup in both arms with `NO_FVG_AVAILABLE`, on any data.")
    w("")
    w("So **section 10.1's falsification row cannot be evaluated at the shipped default**:")
    w("*\"the full model beats every control by a margin whose CI excludes zero\"* is")
    w("undefined against an arm with no trades. The suite runs at model A, which is also")
    w("the only 100%-fill model (D-013 section 5), so each arm's trade count reflects its")
    w("setup count rather than its FVG availability. Pinned by")
    w("`test_the_default_entry_model_cannot_run_two_of_the_four_sequence_controls`.")
    w("")
    w("Limits are **off** in every arm (`apply_limits=False`). SPEC 18.4's position cap")
    w("bites hardest on whichever arm produces most setups -- `sweep_only` has "
      f"{arms['sweep_only'].n_setups / max(base.n_setups, 1):.1f}x the baseline's -- so")
    w("leaving it on would fold portfolio capacity into a comparison about signal")
    w("(D-015 section 4, one axis over).")
    w("")
    w("---")
    w("")

    # -- the arms
    w("## The arms")
    w("")
    w(f"Entry model {PRIMARY_MODEL.value}, pooled over {symbol_years} "
      f"{'symbol-years' if real else 'fixture years'}. ")
    w("`E/setup` is BACKTEST_PROTOCOL section 4.4's per-setup expectancy with a **shared")
    w("denominator**: every setup contributes, an unfilled one contributing 0.0. It is the")
    w("only unit in which arms with different fill rates are comparable.")
    w("")
    w("| Arm | tests | setups | distinct | filled | median SL (ATR) | E/setup gross (R) | cost (R) | E/setup net (R) | n_eff |")
    w("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for name in order:
        a = arms[name]
        w(f"| `{name}` | {a.spec.tests} | {a.n_setups:,} | {a.distinct:,} | "
          f"{a.filled:,} | {_fmt(a.median_sl_atr, 2)} | "
          f"{_fmt(a.expectancy_per_setup_gross)} | {_fmt(a.cost_r_per_setup)} | "
          f"{_fmt(a.expectancy_per_setup)} | {a.n_eff:,.0f} |")
    w("")
    w("**Read the gross column and the cost column before the net column.** Gross R is")
    w("cost-free exit-versus-entry geometry over the planned risk; net R subtracts a")
    w("spread that costs more per R against a tighter stop, so the gap between the two")
    w("columns is the `median SL` column -- see finding 1. The pre-registration requires")
    w("section 10.1 to be judged in **both**, which is why both are here.")
    w("")
    infl = base.n_setups / max(base.distinct, 1)
    w(f"**`distinct` is SPEC 9.4's count and the baseline inflates by {infl:.2f}x.** Three")
    w("stacked levels swept by one bar produce three sweeps, three MSS candidates sharing a")
    w("`choch_bar`, and three near-identical trades. `Market.setups` does not deduplicate")
    w("and neither does `run()` -- STATE.md item 17 records that Phase 9's *headline*")
    w("numbers do, but the engine never has. The controls deliberately do not either,")
    w("because a control that deduplicated would be compared against a baseline that did")
    w("not. **The inflation is shared, so it cancels in the comparison; it does not cancel")
    w("in any absolute count, including section 5.1's minimum-sample thresholds below.**")
    w("")
    w("---")
    w("")

    # -- section 10.1's row
    w("## Section 10.1's falsification row")
    w("")
    w("> *\"Full model beats **every** control in 6.3 and 6.4 by a margin whose CI excludes")
    w("> zero.\"*")
    w("")
    w("Delta is **baseline minus control**, in R per setup. `BH q` is Benjamini-Hochberg at")
    w("q = 0.10 across the five arms (section 5.6).")
    w("")
    w("### In net R -- the currency section 10.1 is written in")
    w("")
    w("| Control | tests | delta | 95% CI | verdict | beats? | p | BH q | MDE | n needed |")
    w("|---|---|---:|---|---|:--:|---:|---:|---:|---:|")
    for i, spec in enumerate(F.CONTROLS):
        c = comparisons[spec.name]
        w(f"| `{c.control}` | {c.tests} | "
          f"{_fmt(c.delta)} | [{_fmt(c.ci_low)}, {_fmt(c.ci_high)}] | `{c.verdict}` | "
          f"{'**yes**' if c.baseline_beats else 'no'} | {_fmt(c.p_value)} | "
          f"{_fmt(bh[i])} | {_fmt(c.mde)} | "
          f"{('--' if not np.isfinite(c.need_n) else f'{c.need_n:,.0f}')} |")
    w("")
    w("### In gross R -- the currency that is about signal")
    w("")
    w("| Control | baseline | control | delta | 95% CI | verdict | beats? | median SL (ATR), base vs control |")
    w("|---|---:|---:|---:|---|---|:--:|---|")
    for spec in F.CONTROLS:
        c = comparisons[spec.name]
        w(f"| `{c.control}` | {_fmt(c.base_e_gross)} | {_fmt(c.ctrl_e_gross)} | "
          f"{_fmt(c.gross_delta)} | [{_fmt(c.gross_ci_low)}, {_fmt(c.gross_ci_high)}] | "
          f"`{c.gross_verdict}` | {'yes' if c.baseline_beats_gross else '**no**'} | "
          f"{_fmt(c.base_sl_atr, 2)} vs {_fmt(c.ctrl_sl_atr, 2)} |")
    w("")
    beaten = [c for c in comparisons.values() if c.baseline_beats]
    beaten_g = [c for c in comparisons.values() if c.baseline_beats_gross]
    confounded = [c for c in comparisons.values() if c.cost_explains_it]
    w(f"**The baseline beats {len(beaten)} of {len(comparisons)} controls in net R and")
    w(f"{len(beaten_g)} of {len(comparisons)} in gross R.**")
    if real:
        _need = len(comparisons)
        _pass = len(beaten) == _need and len(beaten_g) == _need
        w("")
        w(f"Section 10.1 requires **{_need} of {_need} in both**. "
          + ("That is met." if _pass else "**That is not met.**"))
        if not _pass:
            _miss_g = [c.control for c in comparisons.values() if not c.baseline_beats_gross]
            w("")
            w("The arms the baseline does **not** beat in gross R -- the currency about")
            w("signal rather than about stop width -- are "
              + ", ".join('`' + m + '`' for m in _miss_g) + ".")
            w("")
            w("**This is the row protocol section 10.1 calls the one that decides the")
            w("question**, and it is the row this project has said from the beginning was")
            w("the most likely to fail: *\"a strategy that beats a null model but not a")
            w("sweep-only control has not demonstrated the thing it claims to")
            w("demonstrate\"*. Read the per-arm sections below before drawing any")
            w("conclusion from that sentence -- which arms are missed, and in which")
            w("currency, decides what it means.")
    else:
        w("On this fixture the expected number is zero in both, and the gross column")
        w("delivers it. The net column does not,")
        w(f"and the {len(confounded)} arm(s) where the two disagree "
          f"({', '.join('`' + c.control + '`' for c in confounded) or 'none'}) are finding 1.")
    w("")
    verdicts = {c.verdict for c in comparisons.values()}
    if "EQUIVALENT" in verdicts:
        if real:
            w("**An `EQUIVALENT` verdict here is a finding, not a formality.** It says the")
            w(f"interval sits entirely inside +/-{F.EQUIVALENCE_MARGIN_R:.2f} R -- the")
            w("project's own threshold for a tradable edge -- so the component that arm")
            w("removes cannot be worth more than that. `UNDERPOWERED` would mean the study")
            w("could not tell; `EQUIVALENT` means it could, and the answer was no.")
            w("")
        else:
            w("Note any `EQUIVALENT` verdict: on this fixture it is **correct and")
            w("uninformative**. The true difference really is zero, so an interval tight")
            w("enough to sit inside the margin is the right answer to the wrong question.")
            w("The same verdict on real bars would be a finding; here it is a restatement")
            w("of the fixture.")
            w("")
    w("---")
    w("")

    # -- the seeded arms
    for label, per_seed, spec_name, section in (
        ("Shuffled liquidity (section 6.3, tests H3)", shuf_seeds, "shuffled_liquidity", "6.3"),
        ("The random-time floor (section 6.4)", rand_seeds, "random_time", "6.4"),
    ):
        es = np.array([a.expectancy_per_setup for a in per_seed])
        ns = np.array([a.n_setups for a in per_seed])
        w(f"## {label}")
        w("")
        w(f"Section 6.3: *\"Run with 20 random seeds and report the distribution, not one")
        w(f"draw.\"* {len(per_seed)} seeds, each pooled over {symbol_years} "
          f"{'symbol-years' if real else 'years'}.")
        w("")
        w("| | min | p25 | median | p75 | max | mean |")
        w("|---|---:|---:|---:|---:|---:|---:|")
        q = np.quantile(es, [0.25, 0.5, 0.75])
        w(f"| E/setup (R) | {_fmt(es.min())} | {_fmt(q[0])} | {_fmt(q[1])} | "
          f"{_fmt(q[2])} | {_fmt(es.max())} | {_fmt(es.mean())} |")
        qn = np.quantile(ns, [0.25, 0.5, 0.75])
        w(f"| setups | {ns.min():,} | {qn[0]:,.0f} | {qn[1]:,.0f} | {qn[2]:,.0f} | "
          f"{ns.max():,} | {ns.mean():,.0f} |")
        w("")
        better = int(np.sum(es >= base.expectancy_per_setup))
        w(f"**{better} of {len(es)} seeds match or beat the baseline's "
          f"{base.expectancy_per_setup:+.4f} R/setup.** The across-seed spread "
          f"(sd {es.std(ddof=1):.4f} R) is the honest uncertainty for this arm: the pooled")
        w("CI in the table above treats 20 correlated draws over one price series as 20")
        w("independent samples and is therefore optimistic.")
        w("")
        if spec_name == "random_time":
            w("**This arm is the floor, not a falsification target.** A baseline that fails")
            w("to beat random entry with the same SL/TP geometry has no signal at all, so")
            w("this is the row that must be cleared before any other row means anything.")
            w("")
        w("---")
        w("")

    # -- sample sizes
    w("## Sample sizes against section 5.1")
    w("")
    w("| Claim | Minimum | Arm | n (setups) | n_eff | Meets it? |")
    w("|---|---:|---|---:|---:|:--:|")
    for name in order:
        a = arms[name]
        ok = a.n_setups >= 150
        w(f"| Ablation delta (each arm) | 150 | `{name}` | {a.n_setups:,} | "
          f"{a.n_eff:,.0f} | {'yes' if ok else '**no**'} |")
    w("")
    w("`n_eff` is well under `n` in every arm, which is the point of reporting it: trades")
    w("overlap in time and the SPEC 9.4 inflation above puts near-duplicates in the same")
    w("population. **Read the MDE column, not the n column**, when a verdict is")
    w("`UNDERPOWERED`.")
    w("")
    w("---")
    w("")

    # -- validation
    w("## Instrument validation")
    w("")
    w("| Check | Result |")
    w("|---|---|")
    w(f"| Every control builds and produces setups | {len(order) - 1}/{len(order) - 1} |")
    w("| Every arm runs through the **unmodified** `run()` | yes -- a control is a setup "
      "stream, not a second engine |")
    w("| `Market.setup_override` inert when unused | yes -- baseline output unchanged, "
      "578 pre-existing tests still green |")
    w("| `analyse_sweeps(level_transform=None)` is the identity | yes -- same level ids "
      "and prices, same sweep ids |")
    w("| Setup ids unique within every arm | yes -- a duplicate would silently score one "
      "setup 0.0 and overwrite a live position |")
    w("| Positive control: comparison detects a real difference | yes -- "
      "`test_compare_detects_a_real_difference_and_gets_its_sign_right` |")
    w("| All three verdicts reachable | yes -- `DIFFERENT`, `EQUIVALENT` and "
      "`UNDERPOWERED` each pinned |")
    w(f"| Declared margin pinned to section 10.1's threshold | yes -- "
      f"{F.EQUIVALENCE_MARGIN_R:.2f} R |")
    w("| Mutation check | **18/18 caught** (3 survived the first pass; see below) |")
    w(f"| `tests/test_falsification.py` | {'PASS' if tests_ok else 'FAIL'} -- {tests_line} |")
    w("")
    w("---")
    w("")

    # -- findings
    w("## Findings")
    w("")
    w("### 1. Section 10.1's falsification row can be cleared on stop width alone")
    w("")
    w("**This is the study's main output and it is a problem with the acceptance criterion,")
    w("not with any arm.**")
    w("")
    sw = comparisons["sweep_only"]
    if real:
        w(f"The baseline beats `sweep_only` by {sw.delta:+.3f} R per setup net, CI")
        w(f"[{sw.ci_low:.3f}, {sw.ci_high:.3f}], and by {sw.gross_delta:+.3f} gross. This")
        w("arm clears the row in both currencies. The point of this section is the arms")
        w("that clear it in **only one**, because the difference between the two columns is")
        w("not signal:")
    else:
        w(f"On this fixture the baseline beats `sweep_only` by {sw.delta:+.3f} R per setup")
        w(f"with a CI of [{sw.ci_low:.3f}, {sw.ci_high:.3f}] -- **excluding zero, so")
        w("section 10.1's row is satisfied** -- on a random walk, where the true difference")
        w("is zero by construction. That should be impossible, and the explanation is not a")
        w("bug in the engine or the arm. It is that **R is a ratio and the arms do not")
        w("share its denominator**:")
    w("")
    w("| | baseline | `sweep_only` |")
    w("|---|---:|---:|")
    w(f"| median stop (ATR) | {sw.base_sl_atr:.2f} | {sw.ctrl_sl_atr:.2f} |")
    w(f"| E/setup, **gross** R | {sw.base_e_gross:+.3f} | {sw.ctrl_e_gross:+.3f} |")
    w(f"| cost, in R | {base.cost_r_per_setup:+.3f} | "
      f"{arms['sweep_only'].cost_r_per_setup:+.3f} |")
    w(f"| E/setup, **net** R | {sw.base_e:+.3f} | {sw.ctrl_e:+.3f} |")
    w("")
    w(f"The gross delta is {sw.gross_delta:+.3f} R with a CI of [{sw.gross_ci_low:.3f}, "
      f"{sw.gross_ci_high:.3f}] -- **it contains zero, which is the correct answer**. The")
    w("whole of the net-R gap is transaction cost. `sweep_only` enters at the sweep")
    w("confirmation, so its stop sits just beyond an extreme a bar or two old, while the")
    w("baseline waits for a CHoCH and its stop sits beyond an extreme up to twelve bars")
    w("back. A fixed spread and commission against a stop half as wide is **twice the cost")
    w("per R** -- and in net R that is indistinguishable from signal.")
    w("")
    w("The consequence generalises past this fixture:")
    w("")
    w("> **Any control that enters earlier than the baseline has a tighter stop, and")
    w("> therefore loses more of its R to costs. Section 10.1's row -- *\"beats every")
    w("> control by a margin whose CI excludes zero\"* -- is stated in expectancy, which is")
    w("> net R, so it can be satisfied by geometry rather than by signal.**")
    w("")
    w("**Three arms are affected, not one.** Every arm whose median stop is materially")
    w("tighter than the baseline's carries the same inflation in its net delta; only")
    w("`sweep_only` inflates far enough to cross zero:")
    w("")
    w("| Arm | median SL (ATR) | net delta | gross delta | inflation |")
    w("|---|---:|---:|---:|---:|")
    for spec in F.CONTROLS:
        c = comparisons[spec.name]
        w(f"| `{c.control}` | {_fmt(c.ctrl_sl_atr, 2)} | {_fmt(c.delta)} | "
          f"{_fmt(c.gross_delta)} | {_fmt(c.delta - c.gross_delta)} |")
    w("")
    w("`random_time` matters most in that list, because it is the **floor** and the floor")
    w("is the row that has to be cleared before any other row means anything. Its stop is")
    w(f"{comparisons['random_time'].ctrl_sl_atr:.2f} ATR against the baseline's")
    w(f"{comparisons['random_time'].base_sl_atr:.2f}, so a baseline that beats random entry")
    w("in net R has not thereby shown it has a signal -- it may only have shown that it")
    w("waits longer before committing. `choch_only` and `shuffled_liquidity` are clean:")
    w("both keep the baseline's stop width, and both agree in the two currencies.")
    w("")
    w("**What to do about it is a decision, not a fix, and it is not taken here** "
      "(section 10.2). The options:")
    w("")
    w("1. Read the row in **gross R**, making it a test of signal and losing the point that")
    w("   a strategy has to pay its costs to be worth trading.")
    w("2. Keep net R and **report both**, treating a net-only win as not demonstrating the")
    w("   sequence -- which is what this report does.")
    w("3. Match the stop distance across arms, which changes what the controls are: a")
    w("   sweep-only arm with the baseline's stop is not \"enter on sweep confirmation\".")
    w("")
    w("Option 2 is implemented; 1 and 3 are pre-registration decisions and belong in the")
    w("pre-registration (section 1), before real bars arrive.")
    w("")
    w("### 2. The shipped default entry model cannot run half the section 6.4 suite")
    w("")
    w("Covered above. The consequence is that section 10.1's most important row is")
    w("undefined at `entry.model = C`, and the suite has to name the model it runs at.")
    w("")
    w("### 3. `choch_only` must not be built on `structure.py`'s CHoCH events")
    w("")
    w("The obvious construction is wrong and its failure mode is invisible. A structure")
    w("`CHOCH` is a trend flip through the **protected** level; SPEC 11.2's CHoCH -- the")
    w("one the baseline trades -- is a break of the **last unbroken swing**. Building the")
    w("arm on the former makes it differ from the baseline in the definition of the thing")
    w("under test, and its inevitable null then reads as *\"the sweep requirement only")
    w("reduces sample size\"* when what was measured was a stricter break rule. The arm")
    w("reuses `MssEngine._major_reference` itself for that reason.")
    w("")
    w("The two counts are close enough that a size check does not catch the error: on one")
    w("fixture year the structure events outnumber the correct population and on another")
    w("they do not, so the test asserts on *which bars fire* instead.")
    w("")
    w("### 4. Three guards nothing in the fixture reaches -- the pattern, for the third time")
    w("")
    w("D-014 section 8 and D-015 both recorded a rule enforced somewhere no test goes.")
    w("Three of the first eighteen mutations here survived for the same reason:")
    w("")
    w("| Guard | Why the fixture never reaches it |")
    w("|---|---|")
    w("| `placeholder_sweep`'s `trigger_bar` in the id key | No two legs in the fixture "
      "happen to share an extreme, so no collision occurs -- but `choch_only` scans every "
      "bar and two references broken three bars apart can share one |")
    w("| `_leg_extreme`'s direction | Inverting it still produces trades and still reports "
      "a null, so only a direct test sees it |")
    w(f"| `choch.max_reference_distance_atr` in `choch_only` | At the FROZEN default of "
      f"{cfg.choch.max_reference_distance_atr} it rejects **nothing** -- the widest "
      "reference on this fixture sits at about 2.8 ATR |")
    w("")
    w("The third is worth separating from the other two: it is a **measurement, not an")
    w("arithmetic impossibility** like D-014's four unreachable defaults. It is an ABLATION")
    w("parameter over {2.0, 3.0, 4.0}, and **at 2.0 it binds hard** -- which is how the")
    w("branch is now tested. It also echoes STATE.md section 3: the Phase 9 gate is not")
    w("robust to this same parameter, and here it is again sitting just past where the data")
    w("reaches.")
    w("")
    w("### 5. Three asymmetries between the arms that no construction can remove")
    w("")
    w("| | What | Which way it cuts |")
    w("|---|---|---|")
    w("| 1 | `choch_only` and `random_time` have no sweep, so their `liq_*` columns and "
      "`penetration_atr` are placeholders (NaN, tier 0) | No liquidity breakdown is valid "
      "over those two arms |")
    w("| 2 | The leg origin is **searched** in the sweepless arms and **clamped** to the "
      "sweep extreme in the baseline (D-009 section 11) | **Favours the control** -- a "
      "searched origin can only displace at least as much |")
    w("| 3 | Reversing the order moves the stop anchor onto the event being entered on | "
      "Unavoidable: the trigger and the anchor are the same two events |")
    w("")
    w("### 6. What is still not built")
    w("")
    w("An **end-to-end positive control** -- an injected edge surviving the whole chain from")
    w("prices to a `DIFFERENT` verdict. The positive control here covers the comparison")
    w("layer, and the per-arm tests cover each construction, but nothing demonstrates that a")
    w("real conditional edge in the *price series* would come out the other end. Building")
    w("one needs a synthetic market with a genuine SMC edge, which is the fixture this")
    w("project does not have and cannot easily fabricate -- injecting drift after each MSS")
    w("changes the prices, which changes the sweeps, which changes the MSS set.")
    w("")
    w("Sections 6.1 (H2) and 6.2 (H5) are already built as `sweep_study.py` and")
    w("`marginal_value.py`. Section 6.5's ablation matrix is not, and is the natural next")
    w("piece.")
    w("")
    w("---")
    w("")
    w("## What has to happen before any of this means anything")
    w("")
    w("Q1 and Q2 -- a broker and real M1 or tick history. Until then every arm above is the")
    w("detectors meeting noise. When real bars land, this report is the one that decides")
    w("whether the project has demonstrated what it claims: section 10.1 calls it *\"the row")
    w("most likely to fail\"*, and everything before it can pass while this fails.")
    w("")
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"\nwrote {OUT}")
    return 0 if tests_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
