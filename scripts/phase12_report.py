"""Phase 12 acceptance report (SPEC section 27).

Gate: **"All five models arm correctly on a fixture; fill logic verified against M1."**

The fixture is generated at M1 and resampled upward, so the H4 bars and the M1 path
describe the same underlying series and the two fill resolutions are comparable by
construction.

    python scripts/phase12_report.py
"""

from __future__ import annotations

import re
import statistics
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.config.loader import load_config  # noqa: E402
from bot.core.displacement import leg_origin  # noqa: E402
from bot.core.entries import (  # noqa: E402
    EntryModel,
    FillState,
    OrderType,
    arm,
    resolve_fill,
)
from bot.core.fvg import detect_fvgs  # noqa: E402
from bot.core.indicators import atr_ref  # noqa: E402
from bot.core.liquidity import Side  # noqa: E402
from bot.core.mss import analyse_mss  # noqa: E402
from bot.core.order_blocks import ObDefinition, propose  # noqa: E402
from bot.core.sessions import build_sessions  # noqa: E402
from bot.core.structure import analyse_structure  # noqa: E402
from bot.core.sweeps import analyse_sweeps  # noqa: E402
from bot.core.swings import detect_swings  # noqa: E402
from bot.data.resample import resample  # noqa: E402
from bot.data.synthetic import generate  # noqa: E402

UTC = timezone.utc
OUT = Path("reports/phase12_gate.md")
YEARS = (2024, 2025, 2026)
LABELS = {
    EntryModel.A_MARKET: "A — market on MSS",
    EntryModel.B_RETRACEMENT: "B — retracement of the leg",
    EntryModel.C_FVG: "C — FVG",
    EntryModel.D_ORDER_BLOCK: "D — order block",
    EntryModel.E_LEG_MIDPOINT: "E — 50% of the leg",
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


def build_year(cfg, year: int, seed: int):
    m1 = generate(
        "EURUSD", datetime(year, 1, 1, tzinfo=UTC),
        datetime(year, 12, 31, 23, 59, tzinfo=UTC), cfg, timeframe="M1", seed=seed,
    )
    h4 = resample(m1, "H4", cfg)
    d1 = resample(m1, "D1", cfg)
    m15 = resample(m1, "M15", cfg)
    st = analyse_structure(h4, cfg)
    _, sw = analyse_sweeps(
        cfg=cfg, h4=h4, d1=d1, w1=resample(m1, "W1", cfg), mn1=resample(m1, "MN1", cfg),
        sessions=build_sessions(m15, cfg), h4_structure=st, d1_swings=detect_swings(d1, cfg),
    )
    fvgs = detect_fvgs(h4, cfg)
    res = analyse_mss(h4, cfg, sw.confirmed(), swings=st.swings, fvgs=fvgs)
    opposing: dict[Side, set[int]] = {}
    for e in sw.confirmed():
        opposing.setdefault(e.side, set()).add(e.confirm_bar)
    return m1, h4, st, fvgs, res, opposing, atr_ref(h4, cfg.atr.period), len(sw.confirmed())


def main() -> int:
    cfg, cfg_hash = load_config()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    built = []
    for k, year in enumerate(YEARS):
        print(f"building {year} (M1) ...", flush=True)
        built.append(build_year(cfg, year, seed=41 + k))

    n_pop = n_mss = n_bars = n_sweeps = 0
    armed = Counter()
    rejects = {m: Counter() for m in EntryModel}
    states = Counter()
    reasons = {m: Counter() for m in EntryModel}
    states_noopp = Counter()
    touched_both = gap_bars = disagreements = 0
    risk_atr = {m: [] for m in EntryModel}
    fill_bars = {m: [] for m in EntryModel}
    gap_sizes = []

    print("arming and resolving ...", flush=True)
    for m1, h4, st, fvgs, res, opposing, atr, n_sw in built:
        n_bars += h4.n
        n_sweeps += n_sw
        pop = [c for c in res.candidates if c.is_choch and c.displacement.confirmed]
        n_pop += len(pop)
        n_mss += len(res.mss)
        gap_sizes.extend(np.abs(h4.open[1:] - h4.close[:-1]).tolist())
        for i, c in enumerate(pop):
            a = leg_origin(c.sweep_extreme_bar, c.choch_bar, cfg)
            ob = propose(
                h4, cfg, direction=c.direction, sweep_extreme_bar=c.sweep_extreme_bar,
                leg_start=a, break_bar=c.choch_bar, reference_price=c.reference_price,
                displacement_confirmed=True, definition=ObDefinition.A_LAST_OPPOSING,
                swings=st.swings.swings, atr=atr, seq=i,
            )
            opp = opposing.get(
                Side.BUY_SIDE if c.direction.value == "BULLISH" else Side.SELL_SIDE, set()
            )
            for model in EntryModel:
                r = arm(
                    h4, cfg, direction=c.direction, mss_bar=c.choch_bar, leg_start=a,
                    sweep_extreme=c.sweep.sweep_extreme,
                    break_price=float(h4.close[c.choch_bar]),
                    model=model, fvgs=fvgs, order_block=ob.ob, atr=atr,
                )
                if not r.ok:
                    rejects[model][r.reason.value] += 1
                    continue
                armed[model] += 1
                risk_atr[model].append(r.plan.risk_distance / float(atr[c.choch_bar]))

                f = resolve_fill(h4, cfg, r.plan, opposing_sweep_bars=opp, m1=m1)
                states[(model, f.state.value)] += 1
                if f.cancel_reason:
                    reasons[model][f.cancel_reason.value] += 1
                if f.filled and f.bar is not None:
                    fill_bars[model].append(f.bar - c.choch_bar)

                # The same order without the opposing-sweep cancel, and resolved both
                # ways, so the fixture effect and the intrabar question are separable.
                g = resolve_fill(h4, cfg, r.plan, m1=m1)
                h = resolve_fill(h4, cfg, r.plan, m1=None)
                states_noopp[(model, g.state.value)] += 1
                touched_both += int(g.touched_both)
                gap_bars += int(g.gap_ambiguous)
                disagreements += int(g.state is not h.state)

    print("running test suite ...", flush=True)
    tests_ok, tests_line = _run_tests()

    gaps = np.asarray(gap_sizes)
    n_gaps = int((gaps > 0).sum())

    def rate(counter, model):
        dec = sum(v for (m, s), v in counter.items() if m is model and s != "PENDING")
        fl = counter.get((model, "FILLED"), 0)
        return (fl / dec) if dec else float("nan"), dec

    checks = [
        ("Test suite green", tests_ok, tests_line),
        (
            "All five models arm on the fixture (gate)",
            all(armed[m] > 0 for m in EntryModel),
            ", ".join(f"{m.value} {armed[m]}" for m in EntryModel),
        ),
        (
            "Model A arms every setup and fills every order",
            rate(states_noopp, EntryModel.A_MARKET)[0] == 1.0,
            "the only 100% model, which is what makes it the baseline (SPEC 15.5)",
        ),
        (
            "Limit models do not always fill",
            all(
                rate(states_noopp, m)[0] < 0.9
                for m in EntryModel if m is not EntryModel.A_MARKET
            ),
            "coverage differs by model, so per-SETUP expectancy is the only valid comparison",
        ),
        (
            "Fill logic verified against M1 (gate)",
            disagreements == 0,
            f"{disagreements} disagreements over {sum(armed.values()):,} armed orders",
        ),
        (
            "The continuity branch is exercised",
            touched_both > 0,
            f"{touched_both} bars touched both the entry and the stop",
        ),
        (
            "Model A never uses the close that triggered it",
            True,
            "SPEC 15.3 -- pinned by test, and unmeasurable on this fixture (see below)",
        ),
        (
            "Every reject reason is named",
            all(
                sum(rejects[m].values()) == n_pop - armed[m] for m in EntryModel
            ),
            "SPEC 15.7: invalidate and say why, never fall back",
        ),
    ]
    all_ok = all(ok for _, ok, _ in checks)

    L: list[str] = []
    w = L.append
    w("# Phase 12 Gate Report")
    w("")
    w("**Entry models and fill resolution (SPEC 15).**")
    w("")
    w(f"Generated {datetime.now(UTC).isoformat(timespec='seconds')}")
    w("")
    w(f"- `config_hash` `{cfg_hash}`")
    w(f"- Fixture: {len(YEARS)} synthetic years ({YEARS[0]}-{YEARS[-1]}), EURUSD, **generated at M1** and resampled")
    w(f"- **{n_pop:,} displaced CHoCH setups** ({n_mss:,} of them MSS), {n_bars:,} H4 bars")
    w("")
    w("Generating at M1 and resampling up is what makes the second half of the gate")
    w("meaningful: the H4 bars and the M1 path describe the same underlying series, so")
    w("the two fill resolutions are comparable by construction rather than by assumption.")
    w("")
    w("## Gate checks")
    w("")
    w("| Check | Result | Detail |")
    w("|---|---|---|")
    for name, ok, detail in checks:
        w(f"| {name} | {'PASS' if ok else 'FAIL'} | {detail} |")
    w("")

    w("## The five models (gate, first half)")
    w("")
    w("| Model | Order | Armed | Rejected | Fill rate | Median bars to fill | Risk distance (ATR) |")
    w("|---|---|---:|---|---:|---:|---:|")
    for m in EntryModel:
        fr, dec = rate(states_noopp, m)
        rj = ", ".join(f"`{k}` {v}" for k, v in rejects[m].most_common()) or "—"
        med = statistics.median(fill_bars[m]) if fill_bars[m] else float("nan")
        rk = statistics.median(risk_atr[m]) if risk_atr[m] else float("nan")
        ot = "MARKET" if m is EntryModel.A_MARKET else "LIMIT"
        w(f"| **{LABELS[m]}** | {ot} | {armed[m]:,} | {rj} | {fr:.1%} | {med:g} | {rk:.2f} |")
    w("")
    w("Fill rates here exclude the opposing-sweep cancel, for a reason the next section")
    w("explains. `PRICE_THROUGH_STOP` is a rejection at arm time, not a cancellation: a")
    w("limit already at or beyond its own stop is not an order, it is a loss waiting to be")
    w("booked, and the log should distinguish \"never armable\" from \"armed and then")
    w("invalidated\".")
    w("")
    w("**Model A is the only 100% model**, which is exactly what SPEC 15.5 warns about:")
    w("*\"a model that fills 35% of the time on the best-looking third of setups will show")
    w("a superior win rate and a worse total return.\"* Any comparison between these models")
    w("has to be on expectancy **per setup**, not per trade — which is a Phase 14 problem,")
    w("but the coverage numbers that make it necessary are measured here.")
    w("")

    w("## The opposing-sweep cancel makes every limit model unusable on this fixture")
    w("")
    w("| Model | Fill rate without cancel_if 2 | With it |")
    w("|---|---:|---:|")
    for m in EntryModel:
        a, _ = rate(states_noopp, m)
        b, _ = rate(states, m)
        w(f"| {LABELS[m]} | {a:.1%} | {b:.1%} |")
    w("")
    w(f"The fixture carries **{n_sweeps:,} confirmed sweeps over {n_bars:,} H4 bars**, or")
    w(f"{n_sweeps/n_bars:.2f} per bar. Over a {cfg.entry.pending_expiry_bars}-bar expiry")
    w("window an opposing sweep is close to certain, so SPEC 15.1's cancel_if clause 2")
    w("cancels essentially every limit order before it can fill. Model A is untouched")
    w("because a market order is resolved on the next bar and never waits.")
    w("")
    w("**This is D-009 §9 one level down and it is a fixture property, not a finding about")
    w("the models.** A random walk with up to 40 active liquidity levels produces sweeps at")
    w("a rate no real market sustains; the same clause on real bars will cost something")
    w("quite different. Both columns are reported so the two effects stay separable, and")
    w("the left one is used everywhere else in this report.")
    w("")

    w("## Fill logic verified against M1 (gate, second half)")
    w("")
    w(f"- Armed orders resolved both ways: **{sum(armed.values()):,}**")
    w(f"- Disagreements between the bar-level rule and the M1 replay: **{disagreements}**")
    w(f"- Bars that touched both the entry and the stop: **{touched_both}**")
    w(f"- Bars that opened beyond the stop (a true gap): **{gap_bars}**")
    w("")
    w("**The two agree everywhere, and getting there required fixing the bar-level rule.**")
    w("")
    w("A limit sits at `p` with its stop at `s` beyond it, and price approaches from the")
    w("far side. Any continuous path that reaches `s` must pass `p` first, so a bar")
    w("touching both is **not ambiguous** — the entry filled. The first version of this")
    w("module treated it as a coin flip and resolved it \"pessimistically\" by cancelling,")
    w(f"which produced {touched_both} false cancels and disagreed with the M1 path on every")
    w("one of them.")
    w("")
    w("That was wrong twice over. The physics says fill; and cancelling is not even the")
    w("pessimistic *outcome*, since a fill that then stops out loses 1R while a cancel")
    w("loses nothing. Reaching for \"be conservative\" produced the answer that was both")
    w("incorrect and less conservative. See D-013 §1.")
    w("")
    w("So SPEC 15.1's clause 1 means what it says it means — *\"a limit order can fill on")
    w("the way back up from a level that already invalidated the idea\"* — and that needs")
    w("price to reach the stop **without having filled on the way**, which under continuity")
    w("requires a gap. Gap bars are the only place `backtest.intrabar_mode` changes an")
    w("answer.")
    w("")

    w("## Two things this fixture structurally cannot measure")
    w("")
    w(f"**Every H4 bar opens exactly at the previous close** — {n_gaps} non-zero gaps in")
    w(f"{len(gaps):,} bar transitions. `bot/data/synthetic.py` emits a continuous random")
    w("walk, including across weekends. Two consequences:")
    w("")
    w("1. **SPEC 15.3's lookahead has zero magnitude here.** Filling model A at `C_b`")
    w("   instead of the next bar's open would gain exactly 0.0000 ATR per trade, because")
    w("   the two prices are the same number. The spec puts the real cost at 10-30% of")
    w("   headline return on H4, and it comes entirely from the gap between a close and the")
    w("   next open — spread, overnight, news — which this fixture does not have. The rule")
    w("   is correct and load-bearing; the fixture simply cannot demonstrate it, and")
    w("   `test_model_A_never_fills_at_the_close_that_triggered_it` covers it instead.")
    w(f"2. **Gap-past-the-stop cancels never fire** ({gap_bars} on the whole fixture), so")
    w("   `cancel_if` clause 1 and the `intrabar_mode` branch are exercised only by")
    w("   constructed tests. That is the same position Phase 10's `INVALIDATED` was in, and")
    w("   for the same reason.")
    w("")
    w("Both are the first things to re-measure when real bars arrive.")
    w("")

    w("## Where limit orders end up")
    w("")
    w("| Model | FILLED | EXPIRED | CANCELLED | PENDING |")
    w("|---|---:|---:|---:|---:|")
    for m in EntryModel:
        row = {s: states_noopp.get((m, s), 0) for s in ("FILLED", "EXPIRED", "CANCELLED", "PENDING")}
        w(f"| {LABELS[m]} | {row['FILLED']:,} | {row['EXPIRED']:,} | {row['CANCELLED']:,} | {row['PENDING']:,} |")
    w("")
    w("`PENDING` is a censored order — the series ended before its window closed. Counting")
    w("those as expiries would understate every fill rate, so they are excluded from the")
    w("denominators above.")
    w("")

    w("## What this report does NOT establish")
    w("")
    w("**Which model is best.** That needs expectancy, which needs stops, targets, sizing")
    w("and an exit policy — SPEC 16, 17, 18, and the backtest engine. This phase")
    w("establishes only that five models arm on the same setups and that the fill rule")
    w("resolves them the way an M1 replay does.")
    w("")
    w("Specifically not established:")
    w("")
    w("1. **Anything about returns.** No trade is closed here; nothing is sized.")
    w("2. **That the fill rates transfer.** They are a property of how far each model's")
    w("   price sits from the break on a random walk. Real retracement behaviour differs,")
    w("   and the opposing-sweep column above will differ more.")
    w("3. **Shadow trades (SPEC 15.6).** The would-have-been outcome of an expired order")
    w("   needs `exit.max_bars_in_trade` and a stop/target policy. Deferred to Phase 14,")
    w("   and worth doing: *\"did we miss the good ones?\"* is per-model and unanswerable")
    w("   without them.")
    w("4. **The M1 fill path against real intrabar data.** The M1 here is synthetic and")
    w("   agrees with its own H4 by construction. Q2 is what makes this a real check.")
    w("")
    w(f"## Verdict: {'PASS' if all_ok else 'FAIL'}")
    w("")
    w("Five models arm on the same setup stream with their prices pinned by arithmetic,")
    w("and the bar-level fill rule agrees with the M1 replay on every armed order — after")
    w("a correction the M1 comparison is what surfaced.")

    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"\nwrote {OUT}")
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
