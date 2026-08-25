"""Regenerate the structure golden file.

    python scripts/regen_golden.py

Run this ONLY when a change to swing detection or the structure engine is intended.
A golden diff that nobody meant to make is the whole point of the file: it is the
cheapest available detector of "I refactored the engine and quietly changed what it
sees", which no other test in the suite catches.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot.config.loader import load_config  # noqa: E402
from bot.data.synthetic import fixture_year  # noqa: E402
from tests.test_structure_golden import GOLDEN, build_golden  # noqa: E402


def main() -> int:
    cfg, cfg_hash = load_config()
    m15 = fixture_year(cfg, year=2026, timeframe="M15")
    payload = build_golden(cfg, m15)
    payload["_meta"] = {
        "config_hash": cfg_hash,
        "fixture": "synthetic fixture_year(2026, M15), seed 7",
        "note": "Random walk. These numbers are a regression baseline, not market truth.",
    }

    old = json.loads(GOLDEN.read_text(encoding="utf-8")) if GOLDEN.exists() else None
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"wrote {GOLDEN}")
    print(f"  bars            {payload['bars']}")
    print(f"  swings          {payload['swings']['count']}  {payload['swings']['by_kind']}")
    print(f"  labels          {payload['swings']['by_label']}")
    print(f"  amendments      {payload['swings']['amendments']}")
    print(f"  events          {payload['structure']['event_count']}  {payload['structure']['by_type']}")
    print(f"  final trend     {payload['structure']['final_trend']}")
    if old is not None:
        changed = [
            k
            for k in ("bars", "swings", "structure")
            if old.get(k) != payload.get(k)
        ]
        print(f"  CHANGED vs previous golden: {changed or 'nothing'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
