"""Golden-file regression for swings and structure -- the Phase 5 gate (SPEC 27).

The point of a golden file here is not that these particular numbers are *correct* --
they come from a random walk, which has no market structure worth being right about.
It is that any change to swing detection or the structure engine which alters the
event stream must be a **deliberate** change: the diff shows up in CI, and whoever
made it has to say so.

Regenerate deliberately with:

    .venv/Scripts/python.exe scripts/regen_golden.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bot.core.structure import analyse_structure
from bot.core.swings import detect_swings
from bot.data.resample import resample

GOLDEN = Path(__file__).parent / "golden" / "structure_h4.json"


def build_golden(cfg, m15_year) -> dict:
    """The exact payload the golden file stores.  Shared with scripts/regen_golden.py."""
    h4 = resample(m15_year, "H4", cfg)
    store = detect_swings(h4, cfg)
    result = analyse_structure(h4, cfg)
    return {
        "bars": h4.n,
        "swings": {
            "count": len(store.swings),
            "by_kind": store.counts(),
            "by_label": {
                lbl: sum(1 for s in store.swings if s.label.value == lbl)
                for lbl in ("HH", "HL", "LH", "LL", "UNDEFINED")
            },
            "amendments": {
                act: sum(1 for a in store.amendments if a.action == act)
                for act in ("APPEND", "REPLACE", "REJECT")
            },
            "first_20": [
                [s.kind.value, s.formed_index, s.confirmed_index, round(s.price, 6), s.label.value]
                for s in store.swings[:20]
            ],
        },
        "structure": {
            "event_count": len(result.events),
            "by_type": {
                t: sum(1 for e in result.events if e.type.value == t)
                for t in ("BOS", "CHOCH", "INTERNAL_LIQUIDITY_GRAB", "TREND_INITIALISED")
            },
            "final_trend": result.state.trend.value,
            "all_events": [
                [e.type.value, e.side.value, e.bar_index, round(e.level, 6)]
                for e in result.events
            ],
        },
    }


@pytest.fixture(scope="module")
def golden() -> dict:
    if not GOLDEN.exists():
        pytest.skip("golden file missing; run scripts/regen_golden.py")
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_swing_detection_matches_golden(cfg, m15_year, golden):
    got = build_golden(cfg, m15_year)
    assert got["bars"] == golden["bars"]
    assert got["swings"] == golden["swings"]


def test_structure_events_match_golden(cfg, m15_year, golden):
    got = build_golden(cfg, m15_year)
    assert got["structure"]["by_type"] == golden["structure"]["by_type"]
    assert got["structure"]["final_trend"] == golden["structure"]["final_trend"]
    assert got["structure"]["all_events"] == golden["structure"]["all_events"]


def test_golden_is_reproducible_within_a_run(cfg, m15_year):
    """Two runs over the same data must agree exactly -- no wall clock, no RNG, no
    dict-ordering dependence anywhere in the signal path (SPEC 25.5)."""
    assert build_golden(cfg, m15_year) == build_golden(cfg, m15_year)
