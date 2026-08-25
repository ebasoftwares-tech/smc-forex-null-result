"""Build a Parquet dataset from CSV exports.

    python scripts/build_dataset.py --raw data/raw --out data/parquet --tf M1

Expects one CSV per symbol named ``<SYMBOL>.csv`` with UTC timestamps.  Non-UTC source
timestamps are refused rather than converted -- see ``ingest.read_csv``: most broker
exports are in *server* time, and reading one as UTC shifts every bar by the broker's
offset, moving every session and every H4 boundary in the system.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.config.loader import load_config  # noqa: E402
from bot.data import ingest  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw", type=Path, default=Path("data/raw"))
    ap.add_argument("--out", type=Path, default=Path("data/parquet"))
    ap.add_argument("--tf", default=None, help="source timeframe (default: config)")
    ap.add_argument("--source-label", default="csv")
    ap.add_argument("--symbols", nargs="*", default=None)
    ap.add_argument("--profile", type=Path, default=None)
    args = ap.parse_args()

    cfg, cfg_hash = load_config(profile=args.profile)
    tf = args.tf or cfg.data.ingest_timeframe
    symbols = [s.upper() for s in (args.symbols or cfg.symbols)]

    sources = {}
    for sym in symbols:
        path = args.raw / f"{sym}.csv"
        if not path.exists():
            print(f"  skip {sym}: {path} not found")
            continue
        print(f"  reading {path} ...", flush=True)
        sources[sym] = ingest.read_csv(path, sym, tf)

    if not sources:
        print("no input files found", file=sys.stderr)
        return 1

    manifest = ingest.build_dataset(
        sources, cfg, cfg_hash, args.out, source_label=args.source_label
    )
    print(f"\nconfig_hash   {manifest.config_hash}")
    print(f"dataset_hash  {manifest.dataset_hash}")
    print(f"tzdata        {manifest.tzdata_version}")
    print(f"day boundary  {manifest.day_boundary}")
    print(f"series        {len(manifest.series)}")
    for e in manifest.series:
        q = e.quality
        print(
            f"  {e.symbol} {e.timeframe:<4} {e.n_bars:>8} bars  "
            f"suspect={q['suspect_bar_count']:<6} anchor_violations={q['week_anchor_violations']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
