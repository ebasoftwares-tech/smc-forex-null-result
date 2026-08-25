"""Phase 1 acceptance report (SPEC section 27).

Gate: "DST fixture year passes; resampled H4/D1 reconciles with broker candles to
within a known, explained difference; data-quality report clean."

Runs the whole Phase 1 pipeline over the DST fixture year and writes
``reports/phase1_gate.md``.  The broker-reconciliation item cannot be evaluated
without real broker data and is reported as BLOCKED rather than quietly skipped.

    python scripts/phase1_report.py
"""

from __future__ import annotations

import subprocess
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.config.loader import load_config, tzdata_version  # noqa: E402
from bot.core.sessions import SessionStatus, build_sessions  # noqa: E402
from bot.data import ingest, quality  # noqa: E402
from bot.data.calendar import DayBoundary, is_dst_desync  # noqa: E402
from bot.data.resample import DERIVED_TIMEFRAMES, resample  # noqa: E402
from bot.data.synthetic import generate  # noqa: E402

UTC = timezone.utc
YEAR = 2026
OUT = Path("reports/phase1_gate.md")


def _run_tests() -> tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    tail = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()][-1:]
    return proc.returncode == 0, (tail[0] if tail else "no output")


def main() -> int:
    cfg, cfg_hash = load_config()
    root = Path(__file__).resolve().parents[1]
    OUT.parent.mkdir(parents=True, exist_ok=True)

    print("building fixture year (M1, both DST transitions) ...", flush=True)
    m1 = generate(
        "EURUSD",
        datetime(YEAR, 1, 1, tzinfo=UTC),
        datetime(YEAR, 12, 31, 23, 59, tzinfo=UTC),
        cfg,
        timeframe="M1",
    )

    print("resampling and persisting ...", flush=True)
    manifest = ingest.build_dataset(
        {"EURUSD": m1}, cfg, cfg_hash, root / "data" / "parquet", source_label="synthetic-fixture"
    )
    series = {e.timeframe: e for e in manifest.series}

    m15 = ingest.read_series(root / "data" / "parquet", "EURUSD", "M15")
    h4 = ingest.read_series(root / "data" / "parquet", "EURUSD", "H4")
    d1 = ingest.read_series(root / "data" / "parquet", "EURUSD", "D1")

    print("sessions ...", flush=True)
    sessions = build_sessions(m15, cfg)
    _, m15_q = quality.analyse(m15, cfg)

    b = DayBoundary(cfg.tf.day_boundary_tz, cfg.tf.day_boundary_time)
    day_lengths = Counter(b.day_length_hours(date(YEAR, m, 1)) for m in range(1, 13))
    h4_hours = sorted({h4.open_dt(i).hour for i in range(h4.n)})
    merged = int(d1.flag("merged_stub").sum())
    sunday_d1 = sum(1 for i in range(d1.n) if (int(d1.close_time[i]) - int(d1.open_time[i])) < 20 * 3600)

    ov = [s for s in sessions if s.session_name == "OVERLAP"]
    ov_durations = Counter(round((s.end_utc - s.start_utc).total_seconds() / 3600, 2) for s in ov)
    desync_ok = all(
        (round((s.end_utc - s.start_utc).total_seconds() / 3600, 2) == 4.5) == is_dst_desync(s.trading_date)
        for s in ov
    )

    print("running test suite ...", flush=True)
    tests_ok, tests_line = _run_tests()

    checks: list[tuple[str, bool, str]] = [
        ("Test suite green", tests_ok, tests_line),
        ("UTC trading day is always 24h (D-001)", set(day_lengths) == {24.0}, str(sorted(day_lengths))),
        ("H4 grid fixed at 00/04/08/12/16/20 UTC", h4_hours == [0, 4, 8, 12, 16, 20], str(h4_hours)),
        ("No Sunday stub D1 bar survives (D-001a)", sunday_d1 == 0, f"{sunday_d1} short D1 bars"),
        ("Sunday stubs merged into Monday", merged > 40, f"{merged} merged bars"),
        ("Overlap is 3.5h / 4.5h only", set(ov_durations) == {3.5, 4.5}, str(dict(ov_durations))),
        ("Widened overlap == DST desync date", desync_ok, "1:1 match"),
        ("Source structurally clean", m15_q.is_clean, "no duplicates/non-monotonic/invalid OHLC"),
        ("No week-anchor violations", m15_q.week_anchor_violations == 0, f"{len(m15_q.week_anchors)} weeks checked"),
        ("No unexplained data gaps", len(m15_q.suspect_gaps) == 0, f"{len(m15_q.suspect_gaps)} suspect"),
        ("Every session window well formed", all(s.start_utc < s.end_utc for s in sessions), f"{len(sessions)} instances"),
    ]
    all_ok = all(ok for _, ok, _ in checks)

    lines: list[str] = []
    w = lines.append
    w("# Phase 1 Gate Report")
    w("")
    w(f"Generated {datetime.now(UTC).isoformat(timespec='seconds')}")
    w("")
    w(f"- `config_hash`  `{cfg_hash}`")
    w(f"- `dataset_hash` `{manifest.dataset_hash}`")
    w(f"- tzdata `{tzdata_version()}` — decides every historical DST transition, so it is")
    w("  part of the dataset identity, not an environment detail")
    w(f"- day boundary **{manifest.day_boundary}** (DECISION D-001)")
    w(f"- session source **{manifest.session_source_tf}**, price side **{manifest.price_side}**")
    w("")
    w("## Gate checks")
    w("")
    w("| Check | Result | Detail |")
    w("|---|---|---|")
    for name, ok, detail in checks:
        w(f"| {name} | {'PASS' if ok else 'FAIL'} | {detail} |")
    w("")
    w("| Broker-candle reconciliation | **BLOCKED** | No broker connected — Q1/Q2. See below |")
    w("")
    w("## Series built")
    w("")
    w("| Timeframe | Bars | First | Last |")
    w("|---|---:|---|---|")
    for tf in ("M1",) + DERIVED_TIMEFRAMES:
        e = series[tf]
        w(f"| {tf} | {e.n_bars:,} | {e.first_bar_utc} | {e.last_bar_utc} |")
    w("")
    w("## Sessions")
    w("")
    w("| Session | Instances | Closed | Incomplete | Forming |")
    w("|---|---:|---:|---:|---:|")
    for name in sorted({s.session_name for s in sessions}):
        sub = [s for s in sessions if s.session_name == name]
        w(
            f"| {name} | {len(sub)} | {sum(1 for s in sub if s.status is SessionStatus.CLOSED)} | "
            f"{sum(1 for s in sub if s.status is SessionStatus.INCOMPLETE)} | "
            f"{sum(1 for s in sub if s.status is SessionStatus.FORMING)} |"
        )
    w("")
    w(f"London/New York overlap durations: {dict(ov_durations)} "
      f"({sum(1 for s in ov if s.dst_desync)} desync days).")
    w("")
    w("## What this report does NOT establish")
    w("")
    w("The fixture is a **random walk**, not a market. It exercises the calendar, the")
    w("bucket arithmetic, the merge rule and the session engine — everything whose")
    w("correctness is a property of *time*, not of price. It says nothing about whether")
    w("any strategy works, and no strategy result may ever be produced from it.")
    w("")
    w("**The broker-reconciliation half of the gate is genuinely blocked**, not skipped:")
    w("it compares our resampled H4/D1 against a broker's own candles to establish the")
    w("known, explained difference between them, and that requires a chosen broker (Q1)")
    w("and downloaded history (Q2). Until it is done, the *shape* of our timeframes is")
    w("verified but their agreement with a live venue is not.")
    w("")
    w(f"## Verdict: {'PASS (2 of 3 gate items; third blocked)' if all_ok else 'FAIL'}")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nwrote {OUT}")
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    print(f"  [BLOCKED] Broker-candle reconciliation: needs Q1/Q2")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
