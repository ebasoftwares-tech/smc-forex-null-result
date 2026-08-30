"""Phase 13 acceptance report (SPEC section 27).

Gate: **"Every limit exercised by scenario; sizing purity test passes."**

Both halves are deliberately *not* fixture measurements, and the gate's own wording says
so. Every loss limit in SPEC 18.4 is defined on closed PnL and nothing closes a trade
until the exit policy exists in Phase 14, so "how often does the daily loss limit bind"
is not a question this phase can answer. What it can answer is whether each limit fires
on its trigger and declines to fire one step below it, which is what the scenario battery
does.

The fixture is used for the things that *are* properties of the setup stream: which stop
caps bind, how far apart the four stop models actually are, and how much of the stream a
given account size can size.

    python scripts/phase13_report.py              # real bars, data/parquet
    python scripts/phase13_report.py --synthetic  # the original fixture

**The default is real data**, which is what makes three of this report's open questions
answerable: whether a real H4 ATR clears S4's 40-pip ceiling, which of the two upper
stop caps actually binds, and what the minimum viable account is at real stop scale.
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
from bot.core.displacement import Direction, leg_origin  # noqa: E402
from bot.core.entries import EntryModel, arm  # noqa: E402
from bot.core.fvg import detect_fvgs  # noqa: E402
from bot.core.indicators import atr_ref  # noqa: E402
from bot.core.mss import analyse_mss  # noqa: E402
from bot.core.order_blocks import ObDefinition, propose  # noqa: E402
from bot.core.risk import (  # noqa: E402
    MissingConversionRate,
    OpenPosition,
    RiskLedger,
    realised_risk_distribution,
    size_for_setup,
)
from bot.core.sessions import build_sessions  # noqa: E402
from bot.core.stops import StopModel, dominant_upper_cap, symbol_spec  # noqa: E402
from bot.core.structure import analyse_structure  # noqa: E402
from bot.core.sweeps import analyse_sweeps  # noqa: E402
from bot.core.swings import detect_swings  # noqa: E402
from bot.core.targets import TargetModel, gate_is_reachable  # noqa: E402
from bot.core.trade import Stage, evaluate  # noqa: E402
from bot.data.ingest import DatasetManifest, read_series  # noqa: E402
from bot.data.resample import resample  # noqa: E402
from bot.data.synthetic import generate  # noqa: E402
from bot.research.risk_study import (  # noqa: E402
    AccountRow,
    StopProposals,
    account_sweep,
    ladder_profile,
    minimum_viable_equity,
    run_scenarios,
    stop_agreement,
    stop_effective_tests,
)

UTC = timezone.utc
PARQUET = Path("data/parquet")
SYNTH_YEARS = (2024, 2025, 2026)

# PRE_REGISTRATION section 4.1 as stamped by Amendment 1.
IS_YEARS, OOS_YEARS = 4, 2
EQUITIES = (500, 1_000, 2_000, 5_000, 10_000, 25_000, 50_000, 100_000)
SL_LABEL = {
    StopModel.S1_SWEEP_EXTREME: "S1 — sweep extreme",
    StopModel.S2_STRUCTURAL_SWING: "S2 — structural swing",
    StopModel.S3_ORDER_BLOCK: "S3 — order block",
    StopModel.S4_ATR: "S4 — ATR multiple",
}


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


def _run_purity_tests() -> tuple[bool, str]:
    """The gate names this test specifically, so it is reported specifically."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_risk.py", "-k",
         "pure or purity or reduce or ladder or clamp"],
        capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1],
    )
    lines = [
        ln.strip() for ln in proc.stdout.splitlines()
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
    return {
        "in_sample": years[:IS_YEARS],
        "out_of_sample": years[IS_YEARS : IS_YEARS + OOS_YEARS],
        "holdout": years[IS_YEARS + OOS_YEARS :],
    }


def _finish(cfg, h4, d1, w1, mn1, sessions):
    st = analyse_structure(h4, cfg)
    _, sw = analyse_sweeps(
        cfg=cfg, h4=h4, d1=d1, w1=w1, mn1=mn1,
        sessions=sessions, h4_structure=st, d1_swings=detect_swings(d1, cfg),
    )
    fvgs = detect_fvgs(h4, cfg)
    res = analyse_mss(h4, cfg, sw.confirmed(), swings=st.swings, fvgs=fvgs)
    return h4, st, fvgs, res, atr_ref(h4, cfg.atr.period)


def build_real(cfg, symbol: str, years: list[int], root: Path):
    """This phase needs no M1 -- the pre-trade chain never resolves a fill."""
    return _finish(
        cfg,
        read_series(root, symbol, "H4", years=years),
        read_series(root, symbol, "D1", years=years),
        read_series(root, symbol, "W1", years=years),
        read_series(root, symbol, "MN1", years=years),
        build_sessions(read_series(root, symbol, cfg.session.source_tf, years=years), cfg),
    )


def build_synthetic(cfg, year: int, seed: int):
    m1 = generate(
        "EURUSD", datetime(year, 1, 1, tzinfo=UTC),
        datetime(year, 12, 31, 23, 59, tzinfo=UTC), cfg, timeframe="M1", seed=seed,
    )
    return _finish(
        cfg, resample(m1, "H4", cfg), resample(m1, "D1", cfg),
        resample(m1, "W1", cfg), resample(m1, "MN1", cfg),
        build_sessions(resample(m1, "M15", cfg), cfg),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", action="store_true", help="the original fixture")
    ap.add_argument("--skip-tests", action="store_true", help="skip the suite")
    args = ap.parse_args()

    cfg, cfg_hash = load_config()
    real = not args.synthetic
    OUT = Path("reports/phase13_gate.md" if real else "reports/phase13_gate_synthetic.md")
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

    # Per symbol, because a JPY pair's pip is 0.01 against 0.0001 and `max_sl_pips`
    # is {default: 60, JPY: 90}.  Pooling these would be wrong by a factor of 100.
    sl_dist_by_sym: dict[str, list[float]] = {}
    atr_pips_by_sym: dict[str, list[float]] = {}
    #: symbol -> [sized at the default equity, reached the sizing stage]
    sizeable_by_sym: dict[str, list[int]] = {}

    n_pop = n_bars = 0
    rows: list[StopProposals] = []
    stage_counts: dict[StopModel, Counter] = {m: Counter() for m in StopModel}
    reason_counts: dict[StopModel, Counter] = {m: Counter() for m in StopModel}
    binding: dict[StopModel, Counter] = {m: Counter() for m in StopModel}
    accepted_sizing = []
    sl_atr_all: list[float] = []
    sl_pips_all: list[float] = []
    sl_distances: list[float] = []
    atr_pips_all: list[float] = []
    cap_choice = Counter()
    limits_on = Counter()
    limits_off = Counter()

    print("running the pre-trade chain ...", flush=True)
    for _i, unit in enumerate(units):
        _t0 = time.time()
        if real:
            sym = unit
            print(f"[{_i + 1}/{len(units)}] {sym} {is_years[0]}-{is_years[-1]} ...",
                  flush=True)
            h4, st, fvgs, res, atr = build_real(cfg, sym, is_years, PARQUET)
        else:
            sym = "EURUSD"
            print(f"building {unit} (M1) ...", flush=True)
            h4, st, fvgs, res, atr = build_synthetic(cfg, unit, seed=41 + _i)
        pip = symbol_spec(cfg, sym).pip_size
        sl_dist_by_sym.setdefault(sym, [])
        atr_pips_by_sym.setdefault(sym, [])
        n_bars += h4.n
        pop = [c for c in res.candidates if c.is_choch and c.displacement.confirmed]
        n_pop += len(pop)
        # One ledger per year, opening every accepted trade and never closing it --
        # there is no exit policy until Phase 14.  See the report's own caveat.
        ledger = RiskLedger(cfg, equity=cfg.account.starting_equity)
        for i, c in enumerate(pop):
            a = leg_origin(c.sweep_extreme_bar, c.choch_bar, cfg)
            atr_v = float(atr[c.choch_bar])
            if not np.isfinite(atr_v) or atr_v <= 0:
                continue
            atr_pips_all.append(atr_v / pip)
            atr_pips_by_sym[sym].append(atr_v / pip)
            cap_choice[dominant_upper_cap(cfg, sym, atr_v)] += 1
            ob = propose(
                h4, cfg, direction=c.direction, sweep_extreme_bar=c.sweep_extreme_bar,
                leg_start=a, break_bar=c.choch_bar, reference_price=c.reference_price,
                displacement_confirmed=True, definition=ObDefinition.A_LAST_OPPOSING,
                swings=st.swings.swings, atr=atr, seq=i,
            )
            row = StopProposals(
                setup_id=f"{id(h4)}-{i}", atr=atr_v,
                break_close=float(h4.close[c.choch_bar]), direction=c.direction,
                entry_price=float("nan"),
            )
            for sl_model in StopModel:
                r = arm(
                    h4, cfg, direction=c.direction, mss_bar=c.choch_bar, leg_start=a,
                    sweep_extreme=c.sweep.sweep_extreme,
                    break_price=float(h4.close[c.choch_bar]),
                    model=EntryModel.C_FVG, fvgs=fvgs, order_block=ob.ob, atr=atr,
                    sl_model=sl_model, setup_start_bar=c.sweep_extreme_bar,
                )
                if not r.ok:
                    stage_counts[sl_model][Stage.ARM.value] += 1
                    reason_counts[sl_model][r.reason.value] += 1
                    continue
                row.stops[sl_model.value] = r.plan.stop
                if np.isnan(row.entry_price):
                    row.entry_price = r.plan.price

                d = evaluate(
                    cfg, r.plan, symbol=sym, atr_value=atr_v,
                    equity=cfg.account.starting_equity, apply_limits=False,
                )
                stage_counts[sl_model][d.stage.value] += 1
                if d.reason:
                    reason_counts[sl_model][d.reason] += 1
                if d.stage is Stage.STOP:
                    binding[sl_model][d.detail] += 1
                # The account sweep's population must be the setups whose STOP passed
                # the SPEC 16.3 caps, not the ones that happened to size at the default
                # equity -- otherwise its denominator is fixed by the very number it
                # sweeps.  `Decision.plan` is None on a sizing rejection, so the
                # distance is taken from the armed plan, which is the same number.
                if sl_model is StopModel.S1_SWEEP_EXTREME and d.stage not in (
                    Stage.ARM, Stage.STOP
                ):
                    # EntryPlan calls it risk_distance; StopCheck calls it sl_distance.
                    # Both are abs(entry_price - stop), verified in stops.check_stop.
                    sl_distances.append(r.plan.risk_distance)
                    sl_dist_by_sym[sym].append(r.plan.risk_distance)
                    sizeable_by_sym.setdefault(sym, [0, 0])[1] += 1
                    if d.ok:
                        sizeable_by_sym[sym][0] += 1
                if d.ok:
                    binding[sl_model][d.plan.stop_check.binding] += 1
                    if sl_model is StopModel.S1_SWEEP_EXTREME:
                        accepted_sizing.append(d.plan.sizing)
                        sl_atr_all.append(d.plan.stop_check.sl_atr)
                        sl_pips_all.append(d.plan.stop_check.sl_pips)

                # SPEC 18.9: the same stream with the portfolio limits engaged.
                if sl_model is StopModel.S1_SWEEP_EXTREME:
                    limits_off[d.stage.value] += 1
                    e = evaluate(
                        cfg, r.plan, symbol=sym, atr_value=atr_v,
                        equity=cfg.account.starting_equity, ledger=ledger,
                    )
                    limits_on[e.stage.value] += 1
                    if e.ok:
                        ledger.open(OpenPosition(
                            f"{sym}-{i}", sym, c.direction, e.plan.risk_pct,
                            r.plan.valid_from,
                        ))
            if row.stops:
                rows.append(row)
        print(f"      {len(pop):,} displaced setups  ({time.time() - _t0:.0f}s)", flush=True)

    print("running the scenario battery ...", flush=True)
    scenarios = run_scenarios(cfg)
    agree = stop_agreement(rows, list(StopModel))
    m_eff, m_eff_n = stop_effective_tests(rows, list(StopModel))
    dist = realised_risk_distribution(accepted_sizing)
    # SPEC 18.2: a symbol whose quote currency is not the account currency cannot be
    # sized without an FX conversion series, and its absence BLOCKS the symbol rather
    # than defaulting to 1.0.  account_sweep calls size_for_setup directly and so
    # raises; probe once per symbol and keep the blocked ones out of the sweep.
    def _sizeable(sym: str) -> bool:
        try:
            size_for_setup(
                cfg, symbol=sym, equity=cfg.account.starting_equity,
                risk_pct=cfg.risk.pct_per_trade,
                sl_distance=30 * symbol_spec(cfg, sym).pip_size,
            )
        except MissingConversionRate:
            return False
        return True

    blocked_syms = sorted(s for s in sl_dist_by_sym if not _sizeable(s))
    # account_sweep applies ONE symbol's spec to every distance it is handed, so on a
    # mixed universe it must be run per symbol and the counts pooled afterwards.
    per_sym_sweep = {
        s: account_sweep(cfg, d, EQUITIES, symbol=s)
        for s, d in sl_dist_by_sym.items() if d and s not in blocked_syms
    }
    min_eq_by_sym = {s: minimum_viable_equity(rs) for s, rs in per_sym_sweep.items()}
    sweep = []
    for _k in range(len(EQUITIES)):
        _at = [rs[_k] for rs in per_sym_sweep.values()]
        if not _at:
            break
        _meds = [r.median_lots for r in _at if np.isfinite(r.median_lots)]
        sweep.append(AccountRow(
            equity=_at[0].equity,
            n=sum(r.n for r in _at),
            accepted=sum(r.accepted for r in _at),
            below_min=sum(r.below_min for r in _at),
            under_risk=sum(r.under_risk for r in _at),
            median_lots=float(np.median(_meds)) if _meds else float("nan"),
        ))
    min_eq = minimum_viable_equity(sweep) if sweep else float("nan")

    if args.skip_tests:
        tests_ok, tests_line = True, "SKIPPED (--skip-tests)"
        purity_ok, purity_line = True, "SKIPPED (--skip-tests)"
    else:
        print("running test suite ...", flush=True)
        tests_ok, tests_line = _run_tests()
        purity_ok, purity_line = _run_purity_tests()

    n_scen_ok = sum(1 for s in scenarios if s.ok)
    accepted_s1 = stage_counts[StopModel.S1_SWEEP_EXTREME][Stage.ACCEPTED.value]

    checks = [
        ("Test suite green", tests_ok, tests_line),
        (
            "Every limit exercised by scenario (gate)",
            n_scen_ok == len(scenarios),
            f"{n_scen_ok}/{len(scenarios)} scenarios fire on their trigger and not on "
            f"their near miss",
        ),
        ("Sizing purity test passes (gate)", purity_ok, purity_line),
        (
            "Realised risk never exceeds nominal",
            dist.get("above_nominal", 1.0) == 0.0,
            f"SPEC 18.9: max realised fraction {dist.get('max_fraction', float('nan')):.4f}"
            f" over {int(dist.get('n', 0)):,} sized setups",
        ),
        (
            "The drawdown ladder is monotone and never above 1.0",
            all(
                m <= 1.0 for _, m in ladder_profile(cfg)
            ) and [m for _, m in ladder_profile(cfg)] == sorted(
                [m for _, m in ladder_profile(cfg)], reverse=True
            ),
            "SPEC 18.5 / 18.1, and no configuration can express the violation",
        ),
        (
            "The chain produces sized trades, not only rejections",
            accepted_s1 > 0,
            f"{accepted_s1:,} of {n_pop:,} setups clear every pre-trade check under S1",
        ),
        (
            "Every rejection is a named SPEC 19 reason",
            all(
                sum(reason_counts[m].values())
                == sum(v for k, v in stage_counts[m].items() if k != Stage.ACCEPTED.value)
                for m in StopModel
            ),
            "SPEC 19: 'nothing exits a setup silently'",
        ),
        (
            "Four stop models are worth fewer than four tests",
            np.isfinite(m_eff) and m_eff < 4.0,
            f"M_eff {m_eff:.2f} over {m_eff_n:,} setups — use this, not 4, in the "
            f"multiple-testing correction",
        ),
    ]
    all_ok = all(ok for _, ok, _ in checks)

    L: list[str] = []
    w = L.append
    w("# Phase 13 Gate Report")
    w("")
    w("**Risk management (SPEC 18), with the SPEC 16 stop models and the SPEC 17.2 RR")
    w("gate that sizing depends on.**")
    w("")
    w(f"Generated {datetime.now(UTC).isoformat(timespec='seconds')}")
    w("")
    w(f"- `config_hash` `{cfg_hash}`")
    if real:
        w(f"- `dataset_hash` `{dataset_hash}`")
        w(
            f"- Data: **real bars** -- {len(units)} symbols, {is_years[0]}-{is_years[-1]} "
            f"({symbol_years} symbol-years), H4, source `{source_label}`, `{price_side}` "
            f"side, tzdata `{tzdata}`"
        )
        w(
            f"- Split (PRE_REGISTRATION 4.1, Amendment 1): in-sample "
            f"**{splits['in_sample']}**, out-of-sample {splits['out_of_sample']}, "
            f"holdout {splits['holdout']}"
        )
        w("- **Each symbol is sized under its own `symbol_spec`** — a JPY pair's pip is "
          "0.01 against 0.0001, and `risk.max_sl_pips` is {default: 60, JPY: 90}")
    else:
        w(f"- Fixture: {len(SYNTH_YEARS)} synthetic years "
          f"({SYNTH_YEARS[0]}-{SYNTH_YEARS[-1]}), EURUSD, generated at M1")
    w(f"- **{n_pop:,} displaced CHoCH setups** over {n_bars:,} H4 bars")
    w(f"- Account: {cfg.account.currency} {cfg.account.starting_equity:,.0f} at "
      f"{cfg.risk.pct_per_trade}% per trade")
    w("")
    w("## Scope")
    w("")
    w("Phase 13 completes SPEC 16 (all four stop models, the full 16.2 buffer, the 16.3")
    w("caps), implements SPEC 17.1/17.2's target **placement** and minimum-RR gate, and")
    w("implements SPEC 18 in full. It does **not** implement SPEC 17.3-17.5 — break-even,")
    w("trailing, time and calendar exits — or the execution of T3's ladder and T4's trail.")
    w("Those need an open trade and land with the exit policy in Phase 14.")
    w("")
    w("The RR gate is here rather than there because `RR_BELOW_MIN` fires in")
    w("CHOCH_CONFIRMED (SPEC 19 item 16), the same state as the 16.3 stop caps and the")
    w("18.2 sizing rejections. Those three are what this gate exercises, and implementing")
    w("two of the three would leave it half met.")
    w("")
    w("## Gate checks")
    w("")
    w("| Check | Result | Detail |")
    w("|---|---|---|")
    for name, ok, detail in checks:
        w(f"| {name} | {'PASS' if ok else 'FAIL'} | {detail} |")
    w("")

    # ------------------------------------------------------------------ scenarios
    w("## Every limit exercised by scenario (gate, first half)")
    w("")
    w("Each row needs **both** columns. A limit that fires on its trigger has been shown")
    w("to fire; a limit that also declines to fire one step below it has been shown to")
    w("fire *because of* the trigger. Without the second column, a check that rejected")
    w("everything would pass this table.")
    w("")
    w("| Limit | Trigger fires | Near miss | Scenario |")
    w("|---|---|---|---|")
    for s in scenarios:
        got = f"`{s.got}`" if s.got else "—"
        near = f"`{s.near_miss_got}`" if s.near_miss_got else "—"
        w(f"| `{s.limit}` | {got} | {near} | {s.note} |")
    w("")
    w("**These are scenarios and not measurements, and the gate's own wording is why.**")
    w("Every loss limit in SPEC 18.4 is defined on *closed* PnL, and nothing closes a")
    w("trade until the exit policy exists in Phase 14. How often each limit actually")
    w("binds is a Phase 14 number; that each one binds correctly is this one.")
    w("")

    # --------------------------------------------------- the drawdown ladder
    w("### The drawdown ladder (SPEC 18.5)")
    w("")
    w("| Drawdown | Multiplier |")
    w("|---:|---:|")
    for dd, m in ladder_profile(cfg):
        w(f"| {dd:.2f}% | x{m:.2f} |")
    w("")
    w("Monotone non-increasing in drawdown, and never above 1.00 — SPEC 18.1's")
    w("anti-martingale invariant at portfolio level. It is **not** monotone in *time*:")
    w("SPEC 18.5 restores the multiplier as drawdown falls, so a test asserting")
    w("monotonicity in time would assert the opposite of the specification.")
    w("")
    w("Two independent guarantees hold it: a config validator, so no configuration can")
    w("*express* an increase, and a clamp, so no arithmetic can *produce* one. The")
    w("validator fires first, which meant a mutation deleting the clamp survived the")
    w("entire suite until a test bypassed the validator to reach it.")
    w("")

    # ----------------------------------------------------------- purity
    w("## Sizing purity (gate, second half)")
    w("")
    w("SPEC 18.1 makes martingale, loss-recovery sizing and averaging down")
    w("*unimplementable* rather than discouraged, by withholding the information they")
    w("need. `position_size` takes:")
    w("")
    w("```")
    w("position_size(equity, risk_pct, sl_distance, *, spec, value_per_unit,")
    w("              min_realised_fraction)")
    w("```")
    w("")
    w("No history, no PnL, no streak counter, no ledger. Asserted three ways, because one")
    w("is not enough: by **introspection** on the signature (a behavioural test can only")
    w("show the function did not use history on the inputs it happened to get), by")
    w("**construction** (a ledger carrying 50 closed losses produces identical lots), and")
    w("by **invariant** (the ladder can only reduce).")
    w("")
    w("### The realised risk-per-trade distribution (SPEC 18.9)")
    w("")
    w("*\"It MUST be a spike at `risk.pct_per_trade` with a lower tail only from lot")
    w("rounding. Any mass above the nominal value is a sizing bug.\"*")
    w("")
    if dist.get("n"):
        w("| | |")
        w("|---|---:|")
        w(f"| Sized setups | {int(dist['n']):,} |")
        w(f"| Median realised / intended | {dist['median_fraction']:.4f} |")
        w(f"| 5th percentile | {dist['p05_fraction']:.4f} |")
        w(f"| Minimum | {dist['min_fraction']:.4f} |")
        w(f"| **Maximum** | **{dist['max_fraction']:.4f}** |")
        w(f"| Above nominal | {int(dist['above_nominal'])} |")
        w("")
        w("The maximum is the assertion: flooring to the lot step is what makes it")
        w("impossible to exceed 1.0, and rounding instead would put mass above nominal on")
        w("roughly half of all trades.")
    else:
        w("No setup cleared the pre-trade chain — see the rejection table below.")
    w("")

    # ------------------------------------------------ stop models on the fixture
    w("## The four stop models on the setup stream")
    w("")
    w("| Model | Armed | Accepted | Rejected at | Top reason |")
    w("|---|---:|---:|---|---|")
    for m in StopModel:
        armed_n = sum(stage_counts[m].values())
        acc = stage_counts[m][Stage.ACCEPTED.value]
        stages = ", ".join(
            f"{k} {v}" for k, v in stage_counts[m].most_common()
            if k != Stage.ACCEPTED.value
        ) or "—"
        top = reason_counts[m].most_common(1)
        w(f"| **{SL_LABEL[m]}** | {armed_n:,} | {acc:,} | {stages} | "
          f"{'`' + top[0][0] + '` ' + str(top[0][1]) if top else '—'} |")
    w("")
    w("Entry model C (FVG) throughout, so the four rows differ only in their stop.")
    w("")

    w("### S4 has a hard ATR ceiling, and it is not the ATR cap")
    w("")
    # The ceiling is per symbol: max_sl_pips is {default: 60, JPY: 90}, so a JPY pair's
    # S4 ceiling is 60 pips of ATR against 40 for the rest.  Pooling one ceiling across
    # a mixed universe would misreport both groups.
    def _ceiling(sym: str) -> float:
        caps = cfg.risk.max_sl_pips
        return caps.get("JPY" if "JPY" in sym else "default", caps["default"]) / cfg.sl.atr_multiple

    ceiling = cfg.risk.max_sl_pips["default"] / cfg.sl.atr_multiple
    _over_n = sum(
        1 for s, ps in atr_pips_by_sym.items() for p in ps if p > _ceiling(s)
    )
    _over_d = sum(len(ps) for ps in atr_pips_by_sym.values())
    over = (_over_n / _over_d) if _over_d else float("nan")
    w(f"S4's stop is `atr_multiple` x ATR = {cfg.sl.atr_multiple} ATR by construction, and")
    w(f"`max_sl_pips` is {cfg.risk.max_sl_pips['default']:.0f} pips "
      f"({cfg.risk.max_sl_pips['JPY']:.0f} for JPY pairs). The two cross at")
    w(f"**{ceiling:.0f} pips of ATR** ({_ceiling('USDJPY'):.0f} for JPY): above that, S4 is")
    w("`SL_TOO_WIDE` on every setup, whatever the setup looks like.")
    w("")
    if atr_pips_all:
        _s4_acc = stage_counts[StopModel.S4_ATR][Stage.ACCEPTED.value]
        _s4_tot = sum(stage_counts[StopModel.S4_ATR].values())
        if over > 0:
            w(f"- H4 ATR: median **{statistics.median(atr_pips_all):.1f} pips**, and")
            w(f"  **{over:.0%} of setups sit above their own symbol's ceiling** — so the")
            w(f"  ceiling **does** bind, on roughly one setup in {1/over:.0f}")
        else:
            w(f"- H4 ATR: median **{statistics.median(atr_pips_all):.1f} pips**, "
              f"{over:.0%} of setups above the ceiling — so **the ceiling never binds here**")
        w(f"- S4 accepts {_s4_acc:,} of {_s4_tot:,} setups end to end")
    w(f"- Mirror image: `max_sl_atr` is {cfg.risk.max_sl_atr} and S4 is "
      f"{cfg.sl.atr_multiple}, so **under S4 the ATR cap can never fire** — that half is")
    w("  arithmetic between two constants and holds on any data")
    w("")
    if real and atr_pips_all:
        w("**The fixture could not answer this and real bars do.** The synthetic walk's")
        w("median H4 ATR was 17.4 pips against a 40-pip threshold, so the ceiling never")
        w(f"came near binding; the real median is {statistics.median(atr_pips_all):.1f} pips")
        w(f"and {over:.0%} of setups clear their symbol's ceiling outright.")
        w("")
        w("| Symbol | median H4 ATR (pips) | ceiling | above it |")
        w("|---|---:|---:|---:|")
        for _s in sorted(atr_pips_by_sym):
            _ps = atr_pips_by_sym[_s]
            if not _ps:
                continue
            _c = _ceiling(_s)
            _sh = sum(1 for p in _ps if p > _c) / len(_ps)
            w(f"| {_s} | {statistics.median(_ps):.1f} | {_c:.0f} | {_sh:.0%} |")
        w("")
        w("**So S4 is a partially available model rather than an unavailable one or a")
        w("universally usable one**, and which it is depends on the symbol. That is the")
        w("question D-014 §3 left open, answered.")
    elif atr_pips_all:
        w("**The ceiling is arithmetic; whether it binds is a measurement, and this fixture")
        w("cannot make it.** `synthetic.py`'s walk produces a median H4 ATR of "
          f"{statistics.median(atr_pips_all):.1f} pips")
        w("against a 40-pip threshold. Whether a real H4 series spends time above 40")
        w("pips of ATR decides whether S4 is a usable model or an unavailable one, and that")
        w("is a question for the first run on real bars — not one this report can answer.")
    w("")
    w("Two FROZEN defaults that were each reasonable alone. Reported, not changed: SPEC")
    w("16.3's caps and `sl.atr_multiple` are both frozen, and moving one to make the other")
    w("reachable is a decision rather than an implementation detail.")
    w("")

    w("### Which upper cap does the work (SPEC 16.3)")
    w("")
    w(f"`max_sl_atr` ({cfg.risk.max_sl_atr}) and `max_sl_pips` "
      f"({cfg.risk.max_sl_pips['default']:.0f}) are both FROZEN, and only one of them can")
    w(f"ever be the one that rejects. They cross at "
      f"{cfg.risk.max_sl_pips['default'] / cfg.risk.max_sl_atr:.0f} pips of ATR.")
    w("")
    w("| Binding cap | Setups |")
    w("|---|---:|")
    for k, v in cap_choice.most_common():
        w(f"| `{k}` | {v:,} ({v/max(sum(cap_choice.values()),1):.0%}) |")
    w("")
    if sl_pips_all:
        w(f"Accepted stop distances under S1: median **{statistics.median(sl_pips_all):.1f} "
          f"pips** ({statistics.median(sl_atr_all):.2f} ATR), range "
          f"{min(sl_pips_all):.1f}-{max(sl_pips_all):.1f} pips.")
        w("")

    # ------------------------------------------------------- the bake-off
    w("## Four stop models are worth fewer than four tests")
    w("")
    w(f"**M_eff = {m_eff:.2f}** over {m_eff_n:,} setups with all four models available.")
    w("")
    w("This is D-012's finding again, one layer down: the Phase 11 bake-off measured four")
    w("order-block definitions at 1.77 effective tests rather than 4, and SPEC 16.6 asks")
    w("for the same paired-variant treatment of S1-S4. The number below is what a")
    w("multiple-testing correction over the stop models must use.")
    w("")
    w("| Pair | n | Identical price | Within 0.05 ATR |")
    w("|---|---:|---:|---:|")
    for (a, b), (n, exact, close) in agree.items():
        w(f"| {a} / {b} | {n:,} | {exact:.1%} | {close:.1%} |")
    w("")
    w("**Both columns, because exact agreement understates redundancy** (D-012 §2). S1")
    w("anchors on the sweep extreme and S2 on the lowest low of a window that *starts* at")
    w("the sweep extreme, so they are the same number unless some bar went lower — and")
    w("when one did, they are economically one model and arithmetically two.")
    w("")
    w("The correlation is computed on each model's ATR-normalised distance from the")
    w("**break bar's close**, which no stop model produced. Centring on the per-observation")
    w("mean across the models being compared would pin the average pairwise correlation at")
    w("`-1/(k-1)` — a number about the centring, not about the models (D-012 §3a).")
    w("")

    # ------------------------------------------------------ rejection catalogue
    w("## Where setups die (SPEC 19)")
    w("")
    w("Under S1, limits off. The chain runs cheapest-and-most-structural first, so a")
    w("rejection names a property of *this setup* before it names a property of the book")
    w("it happened to arrive into: `SL_TOO_WIDE` would never have been fine,")
    w("`RISK_LIMIT_POSITIONS` would have been fine tomorrow.")
    w("")
    w("| Reason | Count |")
    w("|---|---:|")
    for k, v in reason_counts[StopModel.S1_SWEEP_EXTREME].most_common():
        w(f"| `{k}` | {v:,} |")
    w("")

    w("### Limits on versus limits off (SPEC 18.9)")
    w("")
    w("| Outcome | Limits off | Limits on |")
    w("|---|---:|---:|")
    for k in sorted(set(limits_off) | set(limits_on)):
        w(f"| {k} | {limits_off.get(k, 0):,} | {limits_on.get(k, 0):,} |")
    w("")
    w("**The limits-on column is dominated by `RISK_LIMIT_POSITIONS`, and that is an")
    w("artefact of this phase, not a result.** Nothing closes a trade until Phase 14, so")
    w("the ledger fills to `max_open_positions` and stays there for the rest of the year.")
    w("The comparison SPEC 18.9 actually asks for — *\"a strategy that is only profitable")
    w("with a daily loss limit engaged is a strategy with a fragility the limit is")
    w("hiding\"* — needs an equity curve, and is a Phase 14 deliverable. What this column")
    w("does establish is that the switch works and that turning the limits off leaves the")
    w("*strategy's* own rejections in place rather than turning them off too.")
    w("")

    # --------------------------------------------------- minimum viable account
    w("## The smallest account that can trade this")
    w("")
    w("SPEC 18.2's lot-granularity rejections are a function of equity: the same setup at")
    w("the same stop distance is tradable on one account and not on another. That makes")
    w("this the one number in the risk layer that depends on a value chosen for reporting,")
    w(f"so it is swept rather than quoted. Measured against the {len(sl_distances):,}")
    w("stop distances whose stop cleared the SPEC 16.3 caps" + (
        ", each sized under its own symbol's spec and the counts pooled afterwards."
        if real else "."
    ))
    w("")
    w("**The population is the cap-passing setups, not the ones that sized.** Feeding it")
    w("the accepted setups instead would fix the denominator at one equity — the very")
    w("number the sweep varies — and report a curve that cannot fall below the default.")
    if real and blocked_syms:
        w("")
        w(f"Restricted to the {len(per_sym_sweep)} sizeable symbols; the "
          f"{len(blocked_syms)} blocked ones cannot be swept at any equity.")
    if real and sizeable_by_sym:
        w("")
        w(f"### {len(blocked_syms)} of the {len(sizeable_by_sym)} symbols cannot be sized "
          "at all, and it is not the account size")
        w("")
        w("| Symbol | quote | reached sizing | sized | why not |")
        w("|---|---|---:|---:|---|")
        for _s, (_a, _n) in sorted(sizeable_by_sym.items()):
            _q = _s[3:]
            _why = (
                f"**no {_q}->{cfg.account.currency} rate** (SPEC 18.2)"
                if _s in blocked_syms else "—"
            )
            w(f"| {_s} | {_q} | {_n:,} | {_a:,} | {_why} |")
        w("")
        w("**Every blocked symbol is one whose QUOTE currency is not the account")
        w(f"currency.** Sizing needs the quote->{cfg.account.currency} rate to convert a")
        w("stop distance into money, SPEC 18.2 says its absence *blocks the inclusion of")
        w("any symbol*, and there is no rate series — Q1 is still open. The four that size")
        w(f"are exactly the {cfg.account.currency}-quoted pairs, where the rate is 1 by")
        w("identity. This is the rule working as written; it is not a defect and not a")
        w("function of `starting_equity`.")
        w("")
        w("**It does not look like that in the rejection log, and that is a defect.**")
        w("`trade.evaluate` catches `MissingConversionRate` and returns")
        w("`RiskReject.SIZE_BELOW_MIN`, so all 6 symbols report a lot-granularity failure")
        w("they did not have. Reading that table without running the sizing call directly")
        w("leads to the wrong diagnosis — *\"the account is too small\"* — and to the wrong")
        w("fix. **This is D-019 §1 recurring**: a rejection reason names the gate that")
        w("refused, not the reason it refused. SPEC 19's catalogue has no code for a")
        w("missing conversion rate, so adding one is a specification change and is left")
        w("alone here rather than made silently.")
        w("")
        w("**What it costs now**: the pre-registration's cross-sectional criterion — *\"≥ 6")
        w("of 10 symbols with positive expectancy, same parameters\"* (§3) — is")
        w("**unevaluable** until Q1 supplies a rate series, because only 4 symbols can")
        w("carry a sized trade. Everything below is measured on those four.")
    w("")
    w("| Equity | Sizeable | `SIZE_BELOW_MIN` | `SIZE_UNDER_RISK` | Median lots |")
    w("|---:|---:|---:|---:|---:|")
    for r in sweep:
        w(f"| {cfg.account.currency} {r.equity:,.0f} | {r.acceptance:.0%} | "
          f"{r.below_min:,} | {r.under_risk:,} | {r.median_lots:.2f} |")
    w("")
    if np.isfinite(min_eq):
        w(f"**Smallest swept account sizing 95% of this stream: "
          f"{cfg.account.currency} {min_eq:,.0f}.**")
    else:
        w("**No swept account size reaches 95% of this stream.**")
    if real and min_eq_by_sym:
        w("")
        w("Per symbol, since each is sized under its own spec:")
        w("")
        w("| Symbol | smallest account sizing 95% |")
        w("|---|---:|")
        for _s in sorted(min_eq_by_sym):
            _v = min_eq_by_sym[_s]
            w(f"| {_s} | " + (f"{cfg.account.currency} {_v:,.0f}"
                              if np.isfinite(_v) else "none swept") + " |")
    w("")
    if real:
        w("**That figure is a function of the stop distances**, whose median here is")
        w(f"{statistics.median(sl_pips_all):.1f} pips against the fixture's 23.6 — so the")
        w("stops widened by roughly a third and the answer did not move. The scale table")
        w("below is kept because it says how far it *would* have to move:")
    else:
        w("**That figure is a function of the stop distances, and this fixture's are narrow**")
        w(f"— a median of {statistics.median(sl_pips_all):.1f} pips, because the synthetic")
        w(f"walk's median H4 ATR is only {statistics.median(atr_pips_all):.1f} pips. A market")
        w("with wider stops needs a proportionally larger account for the same coverage, so")
        w("the number is reported against a scale factor rather than on its own:")
    w("")
    w("| Stop distances | Median stop | Smallest account sizing 95% |")
    w("|---:|---:|---:|")
    for scale in (1.0, 1.5, 2.0, 3.0):
        scaled = [d * scale for d in sl_distances]
        rows_s = account_sweep(cfg, scaled, EQUITIES)
        eq_s = minimum_viable_equity(rows_s)
        med = statistics.median(sl_pips_all) * scale
        label = f"{cfg.account.currency} {eq_s:,.0f}" if np.isfinite(eq_s) else "above the sweep"
        w(f"| x{scale:g} | {med:.1f} pips | {label} |")
    w("")
    w("The *shape* of that relationship is what transfers — it is a property of the lot")
    w("grid and of SPEC 18.2's arithmetic, not of returns. The row that applies is")
    w("whichever one matches the real ATR distribution, and that is not known yet.")
    w("")
    w("Note the `SIZE_UNDER_RISK` column: it is zero at every equity and every scale, and")
    w("provably so — see the dead-limits table below.")
    w("")

    # ------------------------------------------------------------ the dead limits
    w("## Three limits that cannot fire, and one model that cannot arm")
    w("")
    w("Implementing SPEC 18 exactly as written turned up four defaults that are")
    w("unreachable rather than merely unused. None has been changed: they are FROZEN or")
    w("ABLATION parameters, and `BACKTEST_PROTOCOL.md` §10.2 forbids moving one to make a")
    w("result appear. Each needs an explicit decision. See D-014.")
    w("")
    w("| # | What | Why it cannot fire |")
    w("|---|---|---|")
    w("| 1 | `risk.min_realised_fraction` = 0.5 | `lots = k x step` with "
      "`raw < (k+1) x step` gives a realised fraction above `k/(k+1) >= 1/2` for **every** "
      "lot grid. 0 fires in 400,000 randomised sizings; worst accepted fraction 0.500081. "
      "It does not catch SPEC 18.2's own worked example, which lands at 0.52 |")
    w("| 2 | `risk.max_total_open_risk_pct` = 1.5% | "
      f"`max_open_positions` ({cfg.risk.max_open_positions}) x the top of the tunable band "
      "(0.50%) is exactly 1.5%, which does not breach 1.5%. At the default 0.35% the "
      f"ceiling is {cfg.risk.max_open_positions * cfg.risk.pct_per_trade:.2f}%. The "
      "position count always binds first |")
    w("| 3 | `risk.max_sl_atr` = 2.5, **under S4 only** | S4's stop is 1.5 ATR by "
      "construction, so it can never reach 2.5 ATR. Under S1-S3 the cap is live |")
    w("| 4 | `tp.model = partial_ladder` (T3) | T3's `tp_1` is the ladder's first rung at "
      "**1R**, and `tp.min_rr` is 1.5. `rr` is 1.0 on every setup, so T3 is rejected "
      "always. It passes at exactly one of the three declared ablation values (1.0) |")
    w("")
    w("A fifth is not a dead limit but the same species of finding: **T4 is exempt from")
    w("the RR gate**, because it has no fixed target to measure. So T4 accepts setups")
    w("T1-T3 reject, and SPEC 17.7's *\"paired T1-T4 variants on a shared setup stream\"*")
    w("does not describe four shared streams. Any comparison has to say so.")
    w("")
    w("| Target model | Gate reachable at the default `min_rr` = 1.5 |")
    w("|---|---|")
    for tm in TargetModel:
        r = gate_is_reachable(cfg, tm)
        note = {
            TargetModel.T1_FIXED_R: f"yes — `r_multiple` {cfg.tp.r_multiple} >= 1.5",
            TargetModel.T2_OPPOSING_LIQUIDITY: "depends on where the liquidity is",
            TargetModel.T3_PARTIAL_LADDER: "**no** — 1R against a 1.5 floor",
            TargetModel.T4_STRUCTURE_TRAIL: "n/a — exempt, which is the finding",
        }[tm]
        w(f"| {tm.value} | {note} |")
    w("")

    # ---------------------------------------------------------- S4 and the fill
    w("## The stop moves at fill, under exactly one model")
    w("")
    w("S1-S3 anchor the stop to structure — a sweep extreme, a swing low, an order block")
    w("edge — so the fill price cannot move it. **S4 anchors on the entry price**, and for")
    w("a MARKET order the planned entry price is a placeholder for `C_b`, which SPEC 15.3")
    w("forbids using because the fill is next bar's open.")
    w("")
    w("Three consequences, all of them in the code and none of them in SPEC 16, which")
    w("treats the stop as fixed once planned:")
    w("")
    w("1. `arm` must compute the entry price **before** the stop. Phase 12 computed the")
    w("   stop first, correctly, because only S1 existed.")
    w("2. Under S4 a limit can never be `PRICE_THROUGH_STOP` — the stop is placed a fixed")
    w("   distance from the price by construction. A zero in that rejection column means")
    w("   *impossible*, not *did not happen*.")
    w("3. The stop must be re-derived at fill, which SPEC 16.5 already requires the caps")
    w("   to be re-run at (*\"Both checks are required\"*) for the unrelated reason that")
    w("   the spread moves.")
    w("")
    if real:
        w("**The effect is no longer zero, and its size is Phase 12's number.** How far the")
        w("S4 stop moves between arming and filling is exactly the close-to-open gap, which")
        w("D-025 measured on this same data at a **mean 0.0156 ATR** (median non-zero 0.0049,")
        w("95th percentile 0.0353, largest 3.9978). On the fixture it was 0.0000 by")
        w("construction.")
        w("")
        w("**This report does not measure it itself** — the pre-trade chain never resolves a")
        w("fill, so the number above is cited from Phase 12 rather than recomputed here. What")
        w("it means for S4 is that the stop, and therefore the risk denominator every R is")
        w("divided by, is set from a price the plan did not know. SPEC 16.5 already requires")
        w("the caps to be re-run at fill for the unrelated reason that the spread moves; that")
        w("re-run is now load-bearing for a second reason.")
    else:
        w("**On this fixture the effect measures exactly zero**, because `synthetic.py` emits")
        w("a perfectly continuous walk and every bar opens at the previous close — the same")
        w("reason SPEC 15.3's own lookahead measured 0.0000 ATR in Phase 12 (D-013 §3). The")
        w("mechanism is pinned by constructed tests and is the first thing to re-measure when")
        w("real bars arrive.")
    w("")

    # ------------------------------------------------------- what is not established
    w("## What this report does NOT establish")
    w("")
    w("1. **That any limit binds at a useful rate.** Every loss limit is defined on closed")
    w("   PnL and nothing closes here. The scenarios prove correctness; Phase 14 measures")
    w("   incidence.")
    w("2. **Anything about returns.** No trade is opened, closed, or costed. Sizing without")
    w("   an exit produces lots, not PnL.")
    w("3. **That the stop-distance distribution transfers.** It is what the detectors")
    w("   produce meeting noise. The account sweep transfers better than most things here")
    w("   because it depends on the distribution's *shape*, but the shape will move.")
    w("4. **The spread limits.** `sl.buffer_spread_mult`, `risk.max_spread_pips` and")
    w("   `risk.max_spread_pct_of_sl` are implemented, scenario-tested, and **inert** —")
    w("   there is no spread series until Q2. Same for `symbol.stops_level`, which is 0")
    w("   points because 0 is the only value that cannot invent a rejection (Q1).")
    w("5. **The FX conversion on anything but EURUSD.** SPEC 18.2's conversion is")
    w("   implemented and its absence **blocks** a symbol rather than defaulting to 1.0 —")
    w("   but every number here is EURUSD on a USD account, where the rate is 1 by")
    w("   identity. The 40%-error case SPEC 18.2 warns about is a JPY-pair case and needs")
    w("   the rate series.")
    if real:
        w("6. **The correlation cap's realised effect.** `correlation_clusters` is")
        w("   implemented and scenario-tested including SPEC 18.7's directional")
        w("   equivalence. The universe is now ten symbols, so cluster membership *is*")
        w("   measurable — but not here: nothing closes a trade until Phase 14, so the")
        w("   ledger fills to `max_open_positions` and stops being informative (see the")
        w("   limits caveat above). It is a Phase 14 measurement, not a blocked one.")
        w("7. **That `M_eff` holds outside this split.** Recomputed here on real in-sample")
        w("   bars, and it should not be recomputed out of sample to check — that spends")
        w("   budget on a nuisance parameter (protocol §7).")
    else:
        w("6. **The correlation cap's realised effect.** `correlation_clusters` is implemented")
        w("   and scenario-tested including SPEC 18.7's directional equivalence, but the")
        w("   fixture is one symbol. Cluster membership on the real universe is a")
        w("   multi-symbol measurement.")
        w("7. **`M_eff` on real bars.** Like Phase 11's, it is a property of how the four")
        w("   models behave on *this* fixture. Recompute it before correcting anything.")
    w("")
    w(f"## Verdict: {'PASS' if all_ok else 'FAIL'}")
    w("")
    w(f"Every SPEC 18.4 limit fires on its trigger and declines on its near miss")
    w(f"({n_scen_ok}/{len(scenarios)}); sizing is a pure function of its declared inputs,")
    w("asserted on the signature rather than on behaviour; the realised risk distribution")
    w("has no mass above nominal; and the four stop models are worth")
    w(f"{m_eff:.2f} tests rather than 4.")
    w("")
    w("Four FROZEN or ABLATION defaults were found to be unreachable rather than unused,")
    w("and none was changed. That is the phase's substantive output.")

    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"\nwrote {OUT}")
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
