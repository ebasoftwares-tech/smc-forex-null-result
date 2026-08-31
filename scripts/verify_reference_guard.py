"""Measure the `choch.max_reference_distance_atr` guard's population on real bars.

`reports/falsification.md` section 4 asserts that at the FROZEN default of 3.0 this guard
*"rejects nothing -- the widest reference on this fixture sits at about 2.8 ATR"*. That
sentence is a hardcoded literal in `scripts/falsification_report.py`: it was written from
a fixture observation and carried into the real-bars report unchanged, which D-028 section
6 recorded as **unverified** and warned may be false, since D-020 found this same
parameter binding differently on real data.

This script measures it rather than asserting it. It reproduces the guard's population
exactly -- the same `_leg_extreme` and the same `MssEngine._major_reference`, imported
from the arm rather than copied, at the same point in the same loop -- and reports the
distance distribution *before* the guard is applied. What it deliberately does not do is
touch `choch_only_setups` itself: the arm's published results are cited in
`FINAL_RESULT.md`, and refactoring a load-bearing research function to instrument it
would put those numbers at risk to settle a sentence.

Cheap by comparison with the 52-minute suite: one `build_market` per symbol and no arms.

    python scripts/verify_reference_guard.py
    python scripts/verify_reference_guard.py --symbols EURUSD,GBPUSD
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.backtest.engine import build_market  # noqa: E402
from bot.config.loader import load_config  # noqa: E402
from bot.core.mss import MssEngine  # noqa: E402
from bot.core.displacement import Direction  # noqa: E402
from bot.data.ingest import DatasetManifest, read_series  # noqa: E402
from bot.research.falsification import _leg_extreme  # noqa: E402

PARQUET = Path("data/parquet")
IS_YEARS = 4


def acquired_years(manifest: DatasetManifest, ingest_tf: str) -> list[int]:
    years: set[int] = set()
    for e in manifest.series:
        if e.timeframe != ingest_tf or not e.first_bar_utc or not e.last_bar_utc:
            continue
        years.update(range(
            datetime.fromisoformat(e.first_bar_utc).year,
            datetime.fromisoformat(e.last_bar_utc).year + 1,
        ))
    return sorted(years)


def distances_for(cfg, market) -> list[float]:
    """Every reference distance in ATR the guard sees, before it is applied.

    The loop below is `choch_only_setups`' loop up to the guard line and no further: same
    bar range, same two directions, same `_leg_extreme`, same `_major_reference`.
    Everything after the guard -- `breaks_level`, displacement -- is intentionally absent,
    because the guard is applied before them and so sees this population whatever they
    later reject.

    **No de-duplication, deliberately, and this is the subtle part.** The arm adds a
    reference to `fired` only *after* the whole chain passes, so a reference that the
    guard rejects is never marked and comes back for evaluation on the next bar, at a new
    leg extreme and a new ATR -- that is, at a different distance. Recording one distance
    per reference would therefore measure a population the guard never sees. What is
    returned is every *evaluation*, which is a superset of the arm's: it includes the few
    that `fired` would have suppressed after a setup was produced. The superset is the
    conservative direction for the question being asked -- if nothing in it exceeds the
    threshold, nothing in the arm's subset can either.
    """
    h4 = market.h4
    atr = market.atr
    engine = MssEngine(
        h4, cfg, [], swings=market.structure.swings, fvgs=market.fvgs, atr=atr
    )
    out: list[float] = []
    for b in range(cfg.disp.max_leg_bars, h4.n):
        a = float(atr[b])
        if not np.isfinite(a) or a <= 0:
            continue
        for direction in (Direction.BULLISH, Direction.BEARISH):
            a_bar, a_price = _leg_extreme(h4, b, direction, cfg)
            ref = engine._major_reference(a_bar, direction)
            if ref is None:
                continue
            out.append(abs(ref.price - a_price) / a)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="", help="comma-separated subset")
    args = ap.parse_args()

    cfg, _ = load_config()
    manifest = DatasetManifest.load(PARQUET / "manifest.json")
    years = acquired_years(manifest, manifest.ingest_timeframe)[:IS_YEARS]
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] or list(cfg.symbols)
    threshold = cfg.choch.max_reference_distance_atr

    per_symbol: dict[str, list[float]] = {}
    for i, sym in enumerate(symbols):
        print(f"[{i + 1}/{len(symbols)}] {sym} {years[0]}-{years[-1]} ...", flush=True)
        mk = build_market(cfg, read_series(PARQUET, sym, "M1", years=years), keep_m1=False)
        per_symbol[sym] = distances_for(cfg, mk)

    print(f"\nGuard: choch.max_reference_distance_atr = {threshold}")
    print(f"{'symbol':<10} {'n':>7} {'max':>8} {'p99':>8} {'over':>7} {'share':>8}")
    alld: list[float] = []
    for sym, d in per_symbol.items():
        alld.extend(d)
        a = np.asarray(d)
        over = int((a > threshold).sum())
        print(f"{sym:<10} {len(d):>7,} {a.max():>8.2f} {np.percentile(a, 99):>8.2f} "
              f"{over:>7,} {over / len(a):>7.1%}" if len(a) else f"{sym:<10} {'0':>7}")

    a = np.asarray(alld)
    over = int((a > threshold).sum())
    print(f"\nPOOLED  n={len(a):,}  max={a.max():.2f}  p99={np.percentile(a, 99):.2f}  "
          f"over {threshold} = {over:,} ({over / len(a):.1%})")
    print(
        f"\nVERDICT: the guard rejects "
        + ("NOTHING" if over == 0 else f"{over:,} of {len(a):,} references")
        + " on real bars."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
