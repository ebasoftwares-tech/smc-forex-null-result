"""Phase 5 acceptance report (SPEC section 27).

Gate: "Golden-file swings and BOS/CHoCH; replay test passes."

Both are satisfied by the test suite; this script produces the human-readable
artefact and the population statistics that make the numbers reviewable.

    python scripts/phase5_report.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot.config.loader import load_config  # noqa: E402
from bot.core.structure import EventType, StructureEngine, Trend, analyse_structure  # noqa: E402
from bot.core.swings import detect_swings, has_min_history  # noqa: E402
from bot.data.resample import resample  # noqa: E402
from bot.data.synthetic import fixture_year, generate  # noqa: E402

UTC = timezone.utc
OUT = ROOT / "reports" / "phase5_gate.md"


def _tests() -> tuple[bool, str]:
    proc = subprocess.run(
        # -q is already in pyproject addopts; passing it again makes -qq, which
        # suppresses the very summary line this function parses.
        [sys.executable, "-m", "pytest", "tests/"],
        capture_output=True, text=True, cwd=ROOT,
    )
    # pytest -q ends with a progress line; the summary is the line naming passed/failed.
    summary = [
        ln.strip()
        for ln in proc.stdout.splitlines()
        if re.search(r"\d+ (passed|failed|error)", ln)
    ]
    return proc.returncode == 0, (summary[-1] if summary else "no summary line")


def main() -> int:
    cfg, cfg_hash = load_config()
    m15 = fixture_year(cfg, year=2026, timeframe="M15")
    h4 = resample(m15, "H4", cfg)
    store = detect_swings(h4, cfg)
    res = analyse_structure(h4, cfg)

    # Prefix stability over 60 cut points, the SPEC 25.2 shape.
    import numpy as np

    rng = np.random.default_rng(2)
    stable = True
    for k in rng.choice(np.arange(100, h4.n), size=60, replace=False):
        t = analyse_structure(h4.head(int(k)), cfg)
        if len(t.events) > len(res.events):
            stable = False
            break
        for a, b in zip(t.events, res.events):
            if (a.id, a.type, a.bar_index, a.level) != (b.id, b.type, b.bar_index, b.level):
                stable = False
                break
        if not stable:
            break

    # Incremental == batch.
    eng = StructureEngine(h4, cfg)
    stepped: list = []
    for i in range(h4.n):
        stepped.extend(eng.on_bar_close(i))
    incremental_ok = [e.id for e in stepped] == [e.id for e in res.events]

    # How often does label-based initialisation actually fire?
    init_hits = fb_hits = 0
    for seed in range(12):
        src = generate("X", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 12, 31, tzinfo=UTC),
                       cfg, timeframe="M15", seed=seed)
        r = analyse_structure(resample(src, "H4", cfg), cfg)
        init_hits += len(r.of_type(EventType.TREND_INITIALISED))
        fb_hits += sum(1 for e in r.events if e.resolved_undefined)

    tests_ok, tests_line = _tests()
    golden = (ROOT / "tests" / "golden" / "structure_h4.json").exists()

    alternating = all(
        a.kind is not b.kind for a, b in zip(store.swings, store.swings[1:])
    )
    unique_breaks = len({(e.type, e.swing_id) for e in res.events}) == len(res.events)
    lag_ok = all(s.confirmed_index == s.formed_index + cfg.swing.n_for("H4") for s in store.swings)

    checks = [
        ("Test suite green", tests_ok, tests_line),
        ("Golden file present and matching", golden and tests_ok, "tests/golden/structure_h4.json"),
        ("Replay / prefix stability (60 cut points)", stable, "no emitted event revised"),
        ("Incremental == batch", incremental_ok, f"{len(res.events)} events"),
        ("Swing sequence strictly alternates", alternating, f"{len(store.swings)} swings"),
        ("Confirmation lag is exactly N bars", lag_ok, f"N={cfg.swing.n_for('H4')}"),
        ("No level produces two break events", unique_breaks, "one break per level"),
    ]
    all_ok = all(ok for _, ok, _ in checks)

    ev = Counter(e.type.value for e in res.events)
    lbl = Counter(s.label.value for s in store.swings)
    amd = Counter(a.action for a in store.amendments)

    L: list[str] = []
    w = L.append
    w("# Phase 5 Gate Report")
    w("")
    w(f"Generated {datetime.now(UTC).isoformat(timespec='seconds')}")
    w("")
    w(f"- `config_hash` `{cfg_hash}`")
    w(f"- Fixture: synthetic year 2026, M15 -> H4, {h4.n} bars")
    w(f"- `swing.fractal_n[H4]` = {cfg.swing.n_for('H4')}, tie rule `{cfg.swing.tie_rule}`, "
      f"price source `{cfg.swing.price_source}`")
    w(f"- `structure.break_confirmation` = `{cfg.structure.break_confirmation}`, "
      f"`protected_on_bos` = `{cfg.structure.protected_on_bos}`")
    w("")
    w("## Gate checks")
    w("")
    w("| Check | Result | Detail |")
    w("|---|---|---|")
    for name, ok, detail in checks:
        w(f"| {name} | {'PASS' if ok else 'FAIL'} | {detail} |")
    w("")
    w("## Populations")
    w("")
    w(f"Swings: **{len(store.swings)}** ({len(store.swings) * 1000 // h4.n} per 1000 bars) — "
      f"{store.counts()}")
    w("")
    w("| Label | Count |")
    w("|---|---:|")
    for k in ("HH", "HL", "LH", "LL", "UNDEFINED"):
        w(f"| {k} | {lbl.get(k, 0)} |")
    w("")
    w(f"Normalisation amendments: APPEND {amd['APPEND']}, REPLACE {amd['REPLACE']}, "
      f"REJECT {amd['REJECT']}.")
    w("")
    w("| Structure event | Count |")
    w("|---|---:|")
    for k in ("BOS", "CHOCH", "INTERNAL_LIQUIDITY_GRAB", "TREND_INITIALISED"):
        w(f"| {k} | {ev.get(k, 0)} |")
    w("")
    w(f"Final trend: **{res.state.trend.value}**. "
      f"H4 min-history floor met: **{has_min_history(h4, cfg)}** "
      f"({h4.n} of {cfg.swing.min_history['H4']} bars).")
    w("")
    w("## Findings")
    w("")
    w(f"**SPEC 6.2's label-based trend initialisation is nearly unreachable.** Across twelve")
    w(f"fixture years it fired **{init_hits}** times; the first-break path resolved `UNDEFINED`")
    w(f"**{fb_hits}** times. Both define the trend within the first ~20 bars, so the choice")
    w("affects at most the first event of a dataset — a warm-up artefact, not a strategy")
    w("behaviour. Recorded so its rarity is not later mistaken for a bug (D-005).")
    w("")
    w("**MSS is deliberately absent.** SPEC 6.6 defines it as CHoCH + sweep context +")
    w("displacement; neither exists before Phases 7 and 8. This engine emits the unfiltered")
    w("superset, which is what makes the marginal value of those filters measurable later.")
    w("")
    w("## What this report does NOT establish")
    w("")
    w("The fixture is a random walk. These counts prove the engine is deterministic, causal")
    w("and self-consistent; they say nothing about whether the structure it finds is")
    w("tradeable, and no strategy result may be produced from this data.")
    w("")
    w(f"## Verdict: {'PASS' if all_ok else 'FAIL'}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
