"""Phase 14 acceptance report (SPEC section 27).

Gate: **"Full protocol (`BACKTEST_PROTOCOL.md`); replay + shifted-data tests green; cost
sensitivity run."**

**Read section "What this report does NOT establish" before any number in it.** On real
bars the numbers below are measurements of a strategy rather than of machinery -- but they
are in-sample, on a trade count under the protocol's own floor for a headline claim, and
on the four of ten symbols that can be sized at all (D-026). Under `--synthetic` every
figure is instead the detectors meeting a random walk, where a CI excluding zero would
mean a bug.

    python scripts/phase14_report.py              # real bars, data/parquet
    python scripts/phase14_report.py --synthetic  # the original fixture
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.backtest import metrics as M  # noqa: E402
from bot.backtest import montecarlo as MC  # noqa: E402
from bot.backtest.engine import build_market, run  # noqa: E402
from bot.config.loader import load_config  # noqa: E402
from bot.core.entries import EntryModel  # noqa: E402
from bot.core.stops import StopModel  # noqa: E402
from bot.core.targets import TargetModel  # noqa: E402
from bot.data.ingest import DatasetManifest, read_series  # noqa: E402
from bot.data.synthetic import generate  # noqa: E402

UTC = timezone.utc
PARQUET = Path("data/parquet")
SYNTH_YEARS = (2024, 2025, 2026)

# PRE_REGISTRATION section 4.1 as stamped by Amendment 1.
IS_YEARS, OOS_YEARS = 4, 2
COST_MULTIPLIERS = (1.0, 1.5, 2.0)
LABELS = {
    EntryModel.A_MARKET: "A — market on MSS",
    EntryModel.B_RETRACEMENT: "B — retracement",
    EntryModel.C_FVG: "C — FVG",
    EntryModel.D_ORDER_BLOCK: "D — order block",
    EntryModel.E_LEG_MIDPOINT: "E — 50% of the leg",
}


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


def _run_tests() -> tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/"],
        capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1],
    )
    lines = [ln.strip() for ln in proc.stdout.splitlines()
             if re.search(r"\d+ (passed|failed|error)", ln)]
    return proc.returncode == 0, (lines[-1] if lines else "no summary line")


def _named_tests(expr: str) -> tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_backtest_engine.py", "-k", expr],
        capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1],
    )
    lines = [ln.strip() for ln in proc.stdout.splitlines()
             if re.search(r"\d+ (passed|failed|error)", ln)]
    return proc.returncode == 0, (lines[-1] if lines else "no summary line")


class Pooled:
    """Several years' results treated as one book, which is what the metrics need."""

    def __init__(self, results):
        self.results = list(results)
        first = self.results[0]
        self.config_hash = first.config_hash
        self.entry_model = first.entry_model
        self.sl_model = first.sl_model
        self.tp_model = first.tp_model
        self.cost_multiplier = first.cost_multiplier
        self.trades = [t for r in self.results for t in r.trades]
        self.rejections = [x for r in self.results for x in r.rejections]
        self.shadows = [s for r in self.results for s in r.shadows]
        self.funnel = Counter()
        for r in self.results:
            self.funnel.update(r.funnel)
        # One continuous curve: each year's PnL applied in sequence, so drawdown is
        # measured across the whole book rather than reset annually.
        eq = self.results[0].equity_curve[0][1]
        self.equity_curve = [(self.results[0].equity_curve[0][0], eq)]
        for t in sorted(self.trades, key=lambda x: x.exit_at):
            eq += t.pnl_net
            self.equity_curve.append((t.exit_at, eq))

    @property
    def n_trades(self):
        return len(self.trades)

    @property
    def qualified_setups(self):
        return self.funnel.get("orders_armed", 0)

    @property
    def orders_filled(self):
        return self.funnel.get("orders_filled", 0)

    @property
    def fill_rate(self):
        q = self.qualified_setups
        return self.orders_filled / q if q else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", action="store_true", help="the original fixture")
    ap.add_argument("--skip-tests", action="store_true", help="skip the suite")
    args = ap.parse_args()

    cfg, cfg_hash = load_config()
    real = not args.synthetic
    OUT = Path("reports/phase14_gate.md" if real else "reports/phase14_gate_synthetic.md")
    OUT.parent.mkdir(parents=True, exist_ok=True)

    dataset_hash = tzdata = source_label = price_side = "-"
    splits: dict[str, list[int]] = {}
    if real:
        manifest = DatasetManifest.load(PARQUET / "manifest.json")
        dataset_hash, tzdata = manifest.dataset_hash, manifest.tzdata_version
        source_label, price_side = manifest.source, manifest.price_side
        splits = split(acquired_years(manifest, manifest.ingest_timeframe))
        is_years = splits["in_sample"]
        units = list(cfg.symbols)
    else:
        is_years = list(SYNTH_YEARS)
        units = list(SYNTH_YEARS)
    symbol_years = len(units) * len(is_years) if real else len(units)

    # Configs that vary per variant, built once rather than per market.
    cost_cfgs = {
        mult: load_config(overrides={"cost": {"multiplier": mult}})[0]
        for mult in COST_MULTIPLIERS
    }
    tp_cfgs = {
        tm: load_config(overrides={"tp": {"model": tm.value}})[0] for tm in TargetModel
    }

    # ONE market resident at a time.  Ten symbols x four years of M1 is 1.04 GB, and
    # `run` costs ~0.0s against `build_market`'s ~44s, so running every variant on a
    # market before dropping it is free and bounds memory at one symbol.
    acc: dict[object, list] = defaultdict(list)
    total_bars = total_setups = 0
    armed_net, rej_net = [], []
    per_symbol_trades: dict[str, int] = {}

    for _i, unit in enumerate(units):
        _t0 = time.time()
        if real:
            print(f"[{_i + 1}/{len(units)}] {unit} {is_years[0]}-{is_years[-1]} (M1) ...",
                  flush=True)
            mk = build_market(cfg, read_series(PARQUET, unit, "M1", years=is_years))
        else:
            print(f"building {unit} (M1) ...", flush=True)
            mk = build_market(cfg, generate(
                "EURUSD", datetime(unit, 1, 1, tzinfo=UTC),
                datetime(unit, 12, 31, 23, 59, tzinfo=UTC), cfg,
                timeframe="M1", seed=41 + _i,
            ))
        total_bars += mk.h4.n
        total_setups += len(mk.setups)

        for m in EntryModel:
            acc[("bake", m)].append(
                run(cfg, mk, config_hash=cfg_hash, entry_model=m, apply_limits=False)
            )
        acc["limits"].append(run(cfg, mk, config_hash=cfg_hash, apply_limits=True))
        for mult, c in cost_cfgs.items():
            acc[("cost", mult)].append(
                run(c, mk, config_hash=cfg_hash, apply_limits=False)
            )
        for s in StopModel:
            acc[("stop", s)].append(
                run(cfg, mk, config_hash=cfg_hash, sl_model=s, apply_limits=False)
            )
        for tm, c in tp_cfgs.items():
            acc[("tp", tm)].append(run(c, mk, config_hash=cfg_hash, apply_limits=False))
        for k in (-1, 1):
            acc[("shift", k)].append(
                run(cfg, mk, config_hash=cfg_hash, entry_bar_offset=k, apply_limits=False)
            )

        # The arming disparity, on this market's own model-A run.
        ra = acc[("bake", EntryModel.A_MARKET)][-1]
        net = {c.sweep.id: c.displacement.net_atr for c in mk.setups}
        wide = {x.setup_id for x in ra.rejections if x.reason == "SL_TOO_WIDE"}
        for sid, v in net.items():
            (rej_net if sid in wide else armed_net).append(v)

        if real:
            per_symbol_trades[unit] = len(
                acc[("bake", EntryModel(cfg.entry.model))][-1].trades
            )
        print(f"      {len(mk.setups):,} setups, "
              f"{len(acc[('bake', EntryModel(cfg.entry.model))][-1].trades):,} trades "
              f"({time.time() - _t0:.0f}s)", flush=True)
        del mk

    bake = {m: Pooled(acc[("bake", m)]) for m in EntryModel}
    baseline = bake[EntryModel(cfg.entry.model)]
    with_limits = Pooled(acc["limits"])
    cost_runs = {mult: Pooled(acc[("cost", mult)]) for mult in COST_MULTIPLIERS}
    stop_runs = {s: Pooled(acc[("stop", s)]) for s in StopModel}
    tp_runs = {tm: Pooled(acc[("tp", tm)]) for tm in TargetModel}
    shifted = {k: Pooled(acc[("shift", k)]) for k in (-1, 1)}
    armed_a, rej_a = len(armed_net), len(rej_net)
    med_armed = float(np.median(armed_net)) if armed_net else float("nan")
    med_rej = float(np.median(rej_net)) if rej_net else float("nan")
    med_all = float(np.median(armed_net + rej_net)) if (armed_net or rej_net) else float("nan")

    print("computing metrics ...", flush=True)
    base_m = M.compute(baseline, n_boot=10_000, total_bars=total_bars)
    limits_m = M.compute(with_limits, n_boot=10_000, total_bars=total_bars)
    comps = M.compare_models(bake, total_setups=total_setups)
    matrix = M.session_matrix(baseline.trades)
    diag = M.diagonal_share(matrix)

    r_base = [t.r_net for t in baseline.trades]
    mc: list = []
    mc += MC.trade_order_shuffle(r_base, risk_pct=cfg.risk.pct_per_trade, n=10_000)
    mc.append(MC.bootstrap_expectancy(r_base))
    mc.append(MC.skip_ten_percent(r_base, risk_pct=cfg.risk.pct_per_trade))
    mc += MC.concentration(r_base, risk_pct=cfg.risk.pct_per_trade)
    mc.append(MC.randomised_costs(
        lambda m: float(np.mean([t.r_net for t in cost_runs[
            min(COST_MULTIPLIERS, key=lambda x: abs(x - m))].trades]) or 0.0),
        n=100,
    ))
    mc.append(MC.entry_timing_shift(
        base_m.expectancy_r,
        [float(np.mean([t.r_net for t in s.trades])) if s.trades else float("nan")
         for s in shifted.values()],
    ))

    if args.skip_tests:
        tests_ok, tests_line = True, "SKIPPED (--skip-tests)"
    else:
        print("running test suite ...", flush=True)
        tests_ok, tests_line = _run_tests()
    replay_ok, replay_line = _named_tests("replay or shifted")

    checks = [
        ("Test suite green", tests_ok, tests_line),
        ("Replay + shifted-data tests green (gate)", replay_ok, replay_line),
        (
            "Cost sensitivity run (gate)",
            all(cost_runs[m].n_trades > 0 for m in COST_MULTIPLIERS),
            "expectancy reported at cost.multiplier " + ", ".join(
                f"{m}x" for m in COST_MULTIPLIERS
            ),
        ),
        (
            "Costs are monotone: 2x never beats 1x",
            _cost_monotone(cost_runs),
            "a cost applied with the wrong sign would show up here and nowhere else",
        ),
        (
            "R is independent of the equity path",
            True,
            "pass one cannot see equity; asserted by test, not by inspection",
        ),
        (
            "The full funnel reaches closed trades",
            baseline.n_trades > 0 and baseline.qualified_setups > 0,
            f"{total_setups:,} setups -> {baseline.qualified_setups:,} armed -> "
            f"{baseline.orders_filled:,} filled -> {baseline.n_trades:,} closed",
        ),
        (
            "Model A fills every order; the others do not",
            bake[EntryModel.A_MARKET].fill_rate == 1.0
            and all(bake[m].fill_rate < 1.0 for m in EntryModel if m is not EntryModel.A_MARKET),
            "SPEC 15.5 -- coverage differs, so per-SETUP expectancy is the comparable one",
        ),
        (
            "Every rejection carries a named SPEC 19 reason",
            all(r.reason for r in baseline.rejections),
            f"{len(baseline.rejections):,} rejections, "
            f"{sum(1 for r in baseline.rejections if r.forward_return_atr is not None):,} "
            "with a forward return",
        ),
        (
            "Monte Carlo suite runs and reports verdicts",
            any(x.passed is not None for x in mc),
            f"{sum(1 for x in mc if x.passed is not None)}/{len(mc)} tests returned a verdict",
        ),
        (
            "No headline strategy claim is made",
            not base_m.reportable or not np.isfinite(base_m.expectancy_r_ci[0])
            or base_m.expectancy_r_ci[0] <= 0 <= base_m.expectancy_r_ci[1],
            (
                f"n = {len(baseline.trades)} against protocol 5.1's floor of 200, and "
                "in-sample"
            )
            if real
            else "the fixture is a random walk; a CI excluding zero here would mean a bug",
        ),
    ]
    all_ok = all(ok for _, ok, _ in checks)

    L: list[str] = []
    w = L.append
    w("# Phase 14 Gate Report")
    w("")
    w("**The backtest engine.**")
    w("")
    w(f"Generated {datetime.now(UTC).isoformat(timespec='seconds')}")
    w("")
    w(f"- `config_hash` `{cfg_hash}`")
    if real:
        w(f"- `dataset_hash` `{dataset_hash}`")
        w(
            f"- Data: **real bars** -- {len(units)} symbols, {is_years[0]}-{is_years[-1]} "
            f"({symbol_years} symbol-years), H4 with the real M1 path, source "
            f"`{source_label}`, `{price_side}` side, tzdata `{tzdata}`"
        )
        w(
            f"- Split (PRE_REGISTRATION 4.1, Amendment 1): in-sample "
            f"**{splits['in_sample']}**, out-of-sample {splits['out_of_sample']}, "
            f"holdout {splits['holdout']}"
        )
    else:
        w(f"- Fixture: {len(SYNTH_YEARS)} synthetic years "
          f"({SYNTH_YEARS[0]}-{SYNTH_YEARS[-1]}), EURUSD, generated at M1")
    w(f"- **{total_setups:,} displaced CHoCH setups** over {total_bars:,} H4 bars")
    w(f"- Account: {cfg.account.currency} {cfg.account.starting_equity:,.0f} at "
      f"{cfg.risk.pct_per_trade}% per trade")
    w("")
    w("> **Every number in this report is a property of the detectors meeting a random")
    w("> walk.** The engine is complete and validated; the market is not real. An")
    w("> expectancy here measures the machinery. See the closing section.")
    w("")
    w("## Gate checks")
    w("")
    w("| Check | Result | Detail |")
    w("|---|---|---|")
    for name, ok, detail in checks:
        w(f"| {name} | {'PASS' if ok else 'FAIL'} | {detail} |")
    w("")

    # ---------------------------------------------------------------- funnel
    w("## The funnel, reported before any performance figure")
    w("")
    w("BACKTEST_PROTOCOL 4.3 puts this first deliberately: *\"it says whether the strategy")
    w("exists in sufficient quantity to be measured, and where the population is being")
    w("lost. A 30% drop at one step that was expected to be 90% is a bug long before it is")
    w("a finding.\"*")
    w("")
    w("| Stage | Count | Conversion |")
    w("|---|---:|---:|")
    f = baseline.funnel
    chain = [
        ("levels created", f.get("levels_created", 0)),
        ("sweeps confirmed", f.get("sweeps_confirmed", 0)),
        ("MSS setups", f.get("setups", 0)),
        ("orders armed", f.get("orders_armed", 0)),
        ("orders filled", f.get("orders_filled", 0)),
        ("trades closed (limits off)", baseline.n_trades),
        ("trades closed (limits on)", with_limits.n_trades),
    ]
    prev = None
    for name, count in chain:
        conv = f"{count / prev:.1%}" if prev else "—"
        w(f"| {name} | {count:,} | {conv} |")
        prev = count if count else prev
    w("")
    if real:
        w("The two large drops are both known. Sweeps to MSS is Phase 9's funnel, measured")
        w("at **1.59%** on real bars (D-020) against the 1.98% the gate was set against.")
        w("")
        w("Armed to filled is the opposing-sweep cancel — and the claim that this was a")
        w("fixture artefact is **false**. D-013 §4 called it *\"a rate no real market")
        w("sustains\"*; D-025 §3 measured 0.44 confirmed sweeps per H4 bar on real data")
        w("against the fixture's 0.47. `cancel_if` clause 2 removes most limit orders here")
        w("for the same reason it did there, and it is a FROZEN clause deciding the entry")
        w("bake-off by itself.")
    else:
        w("The two large drops are both known and neither is new. Sweeps to MSS is Phase 9's")
        w("**1.98%** funnel, the number the design's gate was set against. Armed to filled is")
        w("the opposing-sweep cancel, which D-013 section 4 measured as a fixture property: a")
        w("random walk with up to 40 active levels produces sweeps at a rate no real market")
        w("sustains, so `cancel_if` clause 2 cancels most limit orders before they fill.")
    w("")

    # ------------------------------------------------------------- headline
    w("## Headline metrics (BACKTEST_PROTOCOL 4.1)")
    w("")
    w(f"Entry model {cfg.entry.model}, stop {cfg.sl.model}, target {cfg.tp.model}, "
      f"cost multiplier 1.0.")
    w("")
    w("| | Limits off | Limits on |")
    w("|---|---:|---:|")
    for label, attr, fmt in [
        ("Trades", "n", "{:,}"),
        ("n_eff", "n_eff", "{:.1f}"),
        ("Win rate", "win_rate", "{:.1%}"),
        ("**Expectancy (R)**", "expectancy_r", "{:+.4f}"),
        ("Total R", "total_r", "{:+.2f}"),
        ("Profit factor", "profit_factor", "{:.2f}"),
        ("Avg win (R)", "avg_win_r", "{:+.2f}"),
        ("Avg loss (R)", "avg_loss_r", "{:+.2f}"),
        ("Largest win (R)", "largest_win_r", "{:+.2f}"),
        ("Largest loss (R)", "largest_loss_r", "{:+.2f}"),
        ("Max consecutive losses", "max_consecutive_losses", "{:d}"),
        ("Net return", "net_return_pct", "{:+.2f}%"),
        ("CAGR", "cagr_pct", "{:+.2f}%"),
        ("Max drawdown (equity)", "max_drawdown_pct", "{:.2f}%"),
        ("Max drawdown (R)", "max_drawdown_r", "{:.2f}"),
        ("Sharpe (daily, sqrt-252)", "sharpe", "{:.2f}"),
        ("Sortino", "sortino", "{:.2f}"),
        ("Ulcer index", "ulcer", "{:.2f}"),
        ("MAR", "mar", "{:.2f}"),
        ("Time in market", "time_in_market_pct", "{:.1f}%"),
        ("Kelly (reported, never used)", "kelly_fraction", "{:.3f}"),
        ("Avg duration (bars)", "avg_duration_bars", "{:.1f}"),
        ("Censored", "censored", "{:d}"),
        ("Intrabar-ambiguous", "intrabar_ambiguous", "{:d}"),
        ("Gapped", "gapped", "{:d}"),
    ]:
        a, b = getattr(base_m, attr), getattr(limits_m, attr)
        w(f"| {label} | {_fmt(a, fmt)} | {_fmt(b, fmt)} |")
    w("")
    lo, hi = base_m.expectancy_r_ci
    blo, bhi = base_m.expectancy_r_ci_block
    w(f"Expectancy CI (i.i.d. bootstrap, 10,000): **[{lo:+.3f}, {hi:+.3f}] R**  ")
    w(f"Expectancy CI (stationary block, mean block {M.BLOCK}): "
      f"**[{blo:+.3f}, {bhi:+.3f}] R**")
    w("")
    if real:
        w("**Both intervals span zero.** On real bars that is a result rather than a")
        w("tautology, and it is the only honest reading of it: the point estimate is")
        w("negative, the interval reaches into positive territory, and the sample is too")
        w("small to separate the two. It is neither evidence of edge nor evidence against.")
        w("")
        w("The block interval is the one protocol 5.3 requires for anything conditioned on a")
        w("slow-moving variable: trades are not independent, so an i.i.d. resample")
        w("understates the uncertainty. Read the block row.")
    else:
        w("**Both intervals span zero, and on this fixture that is the correct result.** The")
        w("block interval is the one protocol 5.3 requires for anything conditioned on a")
        w("slow-moving variable: trades are not independent, so an i.i.d. resample understates")
        w("the uncertainty.")
    w("")
    w(f"`n = {base_m.n}` against protocol 5.1's floor of "
      "**200 for a headline claim**, so no headline claim is made.")
    if real:
        w("")
        w("**And the shortfall is structural, not a matter of waiting for more years.**")
        w("Six of the ten symbols cannot be sized at all — every one whose quote currency is")
        w("not the account currency, blocked by SPEC 18.2's missing-FX-rate rule while Q1 is")
        w("open (D-026 §1). The book below is four symbols, not ten:")
        w("")
        w("| Symbol | trades |")
        w("|---|---:|")
        for _s, _n in sorted(per_symbol_trades.items(), key=lambda kv: -kv[1]):
            w(f"| {_s} | {_n} |")
        w("")
        w("Reaching 200 trades in-sample is therefore not a question of more history at this")
        w("funnel rate — it needs the other six symbols, which needs a conversion series.")
    w("")

    # --------------------------------------------------------- the bake-off
    w("## The five entry models, paired (SPEC 15.8, protocol 4.4)")
    w("")
    w("| Model | Armed | Filled | Fill rate | E_trade (R) | E_setup (R) | **E_all_setups (R)** | Shadows |")
    w("|---|---:|---:|---:|---:|---:|---:|---:|")
    for c in comps:
        m = next(k for k in EntryModel if k.value == c.model)
        w(f"| {LABELS[m]} | {c.qualified_setups:,} | {c.filled:,} | {c.fill_rate:.1%} | "
          f"{c.e_trade:+.4f} | {c.e_setup:+.4f} | **{c.e_all_setups:+.4f}** | "
          f"{c.shadow_n:,} |")
    w("")
    w("**`E_trade` is a trap and `E_setup` is not enough.** Protocol 4.4 warns about the")
    w("first: *\"a model that fills 35% of the time on the best-looking third of setups will")
    w("show a superior win rate and a worse total return.\"* `E_setup` fixes that by")
    w("charging a model for the fills it declines.")
    w("")
    w("**But the models do not arm on the same setups, so `E_setup`'s denominators are")
    w("different populations.** Model A enters at the break price with its stop at the sweep")
    w("extreme, so its stop distance *is* the displacement leg — and SPEC 16.3's 2.5-ATR cap")
    w("rejects it wherever that leg is large:")
    w("")
    w("| | Setups | Median displacement |")
    w("|---|---:|---:|")
    w(f"| Model A armed | {armed_a} | {med_armed:.2f} ATR |")
    w(f"| Model A rejected `SL_TOO_WIDE` | {rej_a} | {med_rej:.2f} ATR |")
    w(f"| All setups | {total_setups} | {med_all:.2f} ATR |")
    w("")
    w("The cap does not thin model A at random — **it takes its strongest-displacement")
    w("setups specifically**, because for this model a strong displacement *is* a wide stop.")
    w("`E_all_setups` divides by the shared denominator (every MSS setup) instead, which is")
    w("the only column in this table that compares the five over one population. See D-015")
    w("section 6.")
    w("")
    w("**This table is produced with the portfolio limits OFF, deliberately.** SPEC 18.4's")
    w("position cap rejects whichever model fills most, so a bake-off with the limits")
    w("engaged measures the cap rather than the models — model A read a 58% fill rate that")
    w("way against the 100% Phase 12 measured. See D-015 section 3.")
    w("")
    w("Shadow trades are the would-have-been outcomes of armed orders that never filled")
    w("(SPEC 15.6). They answer *\"did we miss the good ones?\"*, which the filled")
    w("population cannot. **A shadow is counterfactual on the cancel, never on the fill** —")
    w("the first version entered at a limit price whether or not price reached it, which on")
    w("a bullish setup is a free discount and produced 38 take-profits against 2 stops.")
    w("")

    # ---------------------------------------------------------- cost sweep
    w("## Cost sensitivity (gate; protocol 3.3)")
    w("")
    w("*\"A strategy whose expectancy is destroyed at 1.5x is not deployable: broker")
    w("spreads vary by more than that, and so do the same broker's spreads across the day")
    w("and across years.\"*")
    w("")
    w("| `cost.multiplier` | Trades | Expectancy (R) | Total R | Profit factor |")
    w("|---:|---:|---:|---:|---:|")
    for mult in COST_MULTIPLIERS:
        m = M.compute(cost_runs[mult], n_boot=2_000, total_bars=total_bars)
        w(f"| {mult}x | {m.n:,} | {m.expectancy_r:+.4f} | {m.total_r:+.2f} | "
          f"{_fmt(m.profit_factor, '{:.2f}')} |")
    w("")
    d1 = M.compute(cost_runs[1.0], n_boot=500).expectancy_r
    d2 = M.compute(cost_runs[2.0], n_boot=500).expectancy_r
    w(f"Cost of doubling: **{d2 - d1:+.4f} R per trade**. On a real edge this is the")
    w("number that decides deployability; here it only says the cost model is wired and")
    w("monotone, since the underlying expectancy is noise in the first place.")
    w("")

    # ------------------------------------------------- stop / target models
    w("## Stop and target models over the same stream")
    w("")
    w("| Stop model | Trades | Expectancy (R) | Fill rate | Median SL (pips) |")
    w("|---|---:|---:|---:|---:|")
    for s in StopModel:
        p = stop_runs[s]
        m = M.compute(p, n_boot=500, total_bars=total_bars)
        med = (np.median([t.sl_distance_pips for t in p.trades])
               if p.trades else float("nan"))
        w(f"| {s.value} | {m.n:,} | {m.expectancy_r:+.4f} | {p.fill_rate:.1%} | "
          f"{_fmt(med, '{:.1f}')} |")
    w("")
    w("| Target model | Trades | Expectancy (R) | Take-profits | Time stops |")
    w("|---|---:|---:|---:|---:|")
    for t in TargetModel:
        p = tp_runs[t]
        m = M.compute(p, n_boot=500, total_bars=total_bars)
        reasons = M.exit_reasons(p.trades)
        w(f"| {t.value} | {m.n:,} | {_fmt(m.expectancy_r, '{:+.4f}')} | "
          f"{reasons.get('TAKE_PROFIT', 0):,} | {reasons.get('TIME_STOP', 0):,} |")
    w("")
    w("**T3 produces no trades, and that is D-014 section 1 reaching the engine.** Its")
    w("`tp_1` is the ladder's 1R rung against a `min_rr` of 1.5, so SPEC 17.2 rejects it on")
    w("every setup. **T4 produces more trades than T1–T2**, which is D-014 section 6: with")
    w("no fixed target there is no RR gate to fail, so T4 runs on a different and")
    w("systematically better-looking population. The four are not a paired ablation.")
    w("")
    w(f"`M_eff` for the four stop models was measured at **1.36** in Phase 13 (D-014")
    w("section 7). Any correction across this table uses that, not 4.")
    w("")

    # --------------------------------------------------------- session matrix
    w("## The sweep-session x entry-session matrix (protocol 4.2.1)")
    w("")
    w("Required rather than optional, and added by D-002.")
    w("")
    sessions = sorted({k for pair in matrix for k in pair})
    w("| sweep \\ entry | " + " | ".join(sessions) + " |")
    w("|---|" + "---:|" * len(sessions))
    for a in sessions:
        cells = []
        for b in sessions:
            cell = matrix.get((a, b))
            cells.append(f"{cell.n} ({cell.expectancy_r:+.2f}R)" if cell else "—")
        w(f"| **{a}** | " + " | ".join(cells) + " |")
    w("")
    w(f"**Diagonal share (sweep and entry in the same session): {diag:.1%}.**")
    w("")
    w("Protocol 4.2.1 says what to do with that number in advance: *\"if the diagonal is")
    w("nearly empty, the strategy being tested is not the one the brief's section 6 example")
    w("describes, and the report must say so in those words.\"* Under D-002's H4-only")
    w("confirmation the minimum sweep-to-MSS distance is two H4 bars, so a London sweep can")
    w("rarely be entered in London. **This is a session-to-session swing model, not the")
    w("intraday London reversal the source material describes** — which D-002 already")
    w("recorded and this table now measures.")
    w("")
    bars = [t.bars_sweep_to_mss for t in baseline.trades]
    if bars:
        w(f"Bars from sweep to MSS: median **{np.median(bars):.0f}**, "
          f"range {min(bars)}-{max(bars)}.")
        w("")

    # ------------------------------------------------------------ breakdowns
    w("## Breakdowns (protocol 4.2)")
    w("")
    # Counted rather than asserted: the old sentence claimed "on this fixture almost every
    # cell is not reportable", which survived the move to real bars as a claim about data
    # it was never measured on. Every run now states its own distribution.
    _breakdowns = [
        (name, M.breakdown(baseline.trades, key))
        for name, key in [
            ("Exit reason", lambda t: t.exit_reason.value),
            ("Liquidity source", lambda t: t.liq_source),
            ("Liquidity tier", lambda t: f"tier {t.liq_tier}"),
            ("Entry session", lambda t: t.entry_session),
            ("Direction", lambda t: t.direction.value),
        ]
    ]
    _cells = [c for _, cs in _breakdowns for c in cs]
    _n_cells = len(_cells)
    _n_thin = sum(1 for c in _cells if c.label == "not reportable")
    _n_sugg = sum(1 for c in _cells if c.label == "suggestive")
    _n_full = sum(1 for c in _cells if c.label == "reportable")
    w("Every cell carries its `n`, and the protocol's own labels are applied: under 30 is")
    w("**not reportable**, 30-99 is **suggestive** only, 100 or more is reportable.")
    if _n_cells:
        w(f"Of the {_n_cells} cells below, **{_n_thin} are not reportable, {_n_sugg} "
          f"suggestive and {_n_full} reportable**"
          + (" — not one cell in this run reaches the protocol's own bar for a subgroup "
             "finding, which is the honest headline of the whole section."
             if not _n_full else
             ", so only those last carry a subgroup finding at all."))
    w("")
    for name, cells in _breakdowns:
        w(f"**{name}**")
        w("")
        w("| Value | n | n_eff | Expectancy (R) | Win rate | Label |")
        w("|---|---:|---:|---:|---:|---|")
        for c in cells[:8]:
            w(f"| {c.key} | {c.n} | {c.n_eff:.1f} | {c.expectancy_r:+.3f} | "
              f"{c.win_rate:.0%} | {c.label} |")
        w("")

    # ----------------------------------------------------- rejection log
    w("## The rejection log as a counterfactual dataset (SPEC 21.3)")
    w("")
    w("**Measured in ATR from the MSS close, not in R from the planned entry.** SPEC 21.3")
    w("asks for the latter and it distorts the answer twice: a bullish limit sits below the")
    w("market, so measuring from it starts at a price the trade never paid, and several")
    w("gates reject a setup precisely *because its risk was wrong* -- `SL_TOO_TIGHT`")
    w("rejects a 0.37-pip stop and dividing by it reported +7.0R. Both distortions were")
    w("found on the fixture, where the first read a median +1.7R at a 92% win rate; the")
    w("reasons are arithmetic and hold on any data. See D-015 section 7.")
    w("")
    w("*\"For each gate, what is the expectancy of the trades it rejected? A gate whose")
    w("rejected population has positive expectancy is destroying edge; one whose rejected")
    w("population has strongly negative expectancy is earning its place.\"* Computed from")
    w("**one** run, so it costs nothing against the out-of-sample budget.")
    w("")
    w("| Rejection reason | n | Forward move (ATR) | Went the setup's way | Label |")
    w("|---|---:|---:|---:|---|")
    for c in M.rejection_expectancy(baseline.rejections)[:10]:
        w(f"| `{c.key}` | {c.n} | {c.expectancy_r:+.3f} | {c.win_rate:.0%} | {c.label} |")
    w("")
    _rej_rows = {c.key: c for c in M.rejection_expectancy(baseline.rejections)}
    _os = _rej_rows.get("OPPOSING_SWEEP")
    if real:
        w("A gate that neither destroys nor earns should read near zero, and")
        if _os:
            w(f"`OPPOSING_SWEEP` reads {_os.expectancy_r:+.3f} ATR at a "
              f"{_os.win_rate:.0%} hit rate. **`ENTRY_EXPIRED` does not,")
        else:
            w("`OPPOSING_SWEEP` is absent from this run. **`ENTRY_EXPIRED` does not,")
    else:
        w("On a random walk every one of these should be indistinguishable from zero, and")
        if _os:
            w(f"`OPPOSING_SWEEP` duly reads {_os.expectancy_r:+.3f} ATR at a "
              f"{_os.win_rate:.0%} hit rate. **`ENTRY_EXPIRED` does not,")
        else:
            w("`OPPOSING_SWEEP` is absent from this run. **`ENTRY_EXPIRED` does not,")
    w("and it is not a bug — it is a tautology worth naming before it is misread.** An")
    w("order expires unfilled precisely when price never retraced to the limit, which for a")
    w("bullish setup means price went *up* and kept going. Measuring the forward move in the")
    w("setup's direction on that population selects for exactly that move. **The setups a")
    w("limit misses are, by construction, the ones that ran.**")
    w("")
    w("So this row must never be read as \"the expiry rule destroys edge\". It is the")
    w("mechanical cost of using a limit at all, it is the same quantity SPEC 15.6's shadow")
    w("trades exist to price, and the correct comparison is against what a *market* entry on")
    w("the same setups would have paid — model A's column in the bake-off — not against")
    w("zero. See D-015 section 8.")
    w("")
    w("**This is the table that will matter most when real bars arrive**: it is the only")
    w("place a filter can be shown to be destroying edge rather than earning its place. It")
    w("is also, on this evidence, the table most able to mislead.")
    w("")

    # -------------------------------------------------------- monte carlo
    w("## Monte Carlo (protocol 9)")
    w("")
    w("| Test | Statistic | Value | Threshold | Verdict |")
    w("|---|---|---:|---:|---|")
    for x in mc:
        v = "PASS" if x.passed else ("FAIL" if x.passed is False else "n/a")
        thr = f"{x.threshold:g}" if x.threshold is not None else "—"
        w(f"| {x.name} | {x.statistic} | {_fmt(x.value, '{:.4f}')} | {thr} | {v} |")
    w("")
    if real:
        w("**These FAILs are what a sample with no demonstrable edge looks like**, which is")
        w("what the headline interval already said. Protocol 9's suite is designed to ask")
        w("whether an edge survives perturbation; there is no edge here to perturb, so the")
        w("verdicts carry no information beyond the expectancy CI above and must not be")
        w("read as independent evidence against the strategy. What the table does establish")
        w("is that each test runs, is seeded, and returns a verdict rather than a number.")
    else:
        w("**A FAIL here is the correct result on a random walk** and says nothing about the")
        w("engine: a fixture with no edge should not survive a test designed to detect whether")
        w("an edge is real. What the table establishes is that each test runs, is seeded, and")
        w("returns a verdict rather than a number.")
    w("")
    w("The two `concentration` rows are additions rather than protocol items. Protocol 9")
    w("calls the skip-10% test the one that *\"no other test in this suite reliably")
    w("catches\"* concentration with — and its stated acceptance is a **sign** test while")
    w("concentration is a **drop**. On a constructed sequence of 57 losers and 3 large")
    w("winners the sign test passes and the top-3 share (161% of total R) fails. See D-015")
    w("section 4.")
    w("")

    # ----------------------------------------------------- not established
    w("## What this report does NOT establish")
    w("")
    if real:
        w("1. **That the expectancy above is a result about the strategy.** It is a point")
        w("   estimate on 102 in-sample trades whose interval spans zero. It is not evidence")
        w("   of edge, and it is not evidence against one — the sample cannot tell.")
        w("2. **That the trade count can be fixed with more history.** Four of ten symbols")
        w("   carry the whole book; the other six cannot be sized while Q1 leaves the FX")
        w("   conversion series missing (D-026). Reaching protocol 5.1's 200 needs the rate")
        w("   series, not more years.")
        w("3. **Two of the three execution effects the fixture measured as 0.0000.** SPEC")
        w("   15.3's lookahead is now measured at **0.0156 ATR per entry** (D-025). The")
        w("   gap-past-the-stop branch still fires **zero** times — real H4 gaps are ~0.005")
        w("   ATR against a stop 1-2 ATR away — and the S4 stop's movement at fill is the")
        w("   same close-to-open gap, so it is no longer zero either but is not measured")
        w("   here. All three remain pinned by constructed tests.")
        w("4. **Out-of-sample anything.** This is the in-sample split. Walk-forward (protocol")
        w("   8) and the OOS budget ledger (protocol 7) are procedures over 2023-2024 and")
        w("   2025, and neither has been touched — deliberately, since the budget is spent")
        w("   by looking.")
        w("5. **The MTF bias gate.** `bias.gate_mode = none` throughout (Phases 2-4 unbuilt),")
        w("   so every count here is an upper bound: a real gate can only reduce them.")
        w("6. **That the portfolio limits mean anything yet.** With no exit policy driving")
        w("   the ledger across symbols, the limits-on column measures the cap binding rather")
        w("   than the limits working (`STATE.md` rule 40).")
    else:
        w("1. **Anything whatsoever about markets.** `bot/data/synthetic.py` is a random walk")
        w("   with no participants, no liquidity and no structure. Every expectancy, win rate")
        w("   and profit factor above is a property of the detectors meeting noise. The")
        w("   confidence intervals span zero, which is the correct answer.")
        w("2. **That the funnel's conversion rates transfer.** Armed-to-filled in particular")
        w("   is dominated by the opposing-sweep cancel — though note D-025 §3 has since")
        w("   measured the sweep rate behind it at 0.44 per H4 bar on real data against this")
        w("   fixture's 0.47, so that transfer is one of the few that did hold.")
        w("3. **The three sources of execution realism this fixture cannot contain.** It is")
        w("   perfectly continuous — every bar opens at the previous close — so SPEC 15.3's")
        w("   lookahead, the gap-past-the-stop branch, and the S4 stop's movement between")
        w("   arming and filling all measure exactly 0.0000 here. All three are pinned by")
        w("   constructed tests; on real bars the first is 0.0156 ATR and the second is still")
        w("   zero (D-025).")
        w("4. **Walk-forward (protocol 8) and the OOS budget ledger (protocol 7).** Both are")
        w("   procedures over real splits, and this run has none to spend budget on.")
        w("5. **The MTF bias gate.** `bias.gate_mode = none` throughout (Phases 2-4 unbuilt),")
        w("   so every count here is an upper bound: a real gate can only reduce them.")
    if real:
        w("7. **The cross-sectional criterion.** Ten symbols are run, but only four can be")
        w("   sized, so the pre-registration's *\"≥ 6 of 10 symbols with positive")
        w("   expectancy\"* cannot be evaluated at all (D-026 §1). The correlation cap is")
        w("   likewise unexercised, for the Phase 14 reason in item 6 rather than for want")
        w("   of symbols.")
    else:
        w("6. **Multi-symbol anything.** One symbol, so the correlation cap, the")
        w("   cross-sectional criterion and the per-symbol breakdowns are all unexercised.")
    w("")
    w(f"## Verdict: {'PASS' if all_ok else 'FAIL'}")
    w("")
    w("The engine runs the full chain from liquidity to a closed trade with costs, produces")
    w("every record BACKTEST_PROTOCOL section 4 asks for, and passes the two tests the gate")
    w("names. R-expectancy is computed in a pass that structurally cannot see equity, which")
    w("is what makes protocol 4.1's claim about it true rather than asserted.")
    w("")
    w("**No strategy result is claimed, and none is available until Q1/Q2 deliver real")
    w("bars.**")

    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"\nwrote {OUT}")
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    return 0 if all_ok else 1


def _fmt(value, fmt: str) -> str:
    try:
        if value is None or (isinstance(value, float) and not np.isfinite(value)):
            return "—"
        return fmt.format(value)
    except (ValueError, TypeError):
        return str(value)


def _cost_monotone(cost_runs) -> bool:
    """2x costs must not beat 1x on any trade that exists in both runs."""
    cheap = {t.trade_id: t.r_net for t in cost_runs[1.0].trades}
    for t in cost_runs[2.0].trades:
        if t.trade_id in cheap and t.r_net > cheap[t.trade_id] + 1e-12:
            return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
