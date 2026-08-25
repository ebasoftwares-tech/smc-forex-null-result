"""Market structure engine: BOS, CHoCH, protected levels (SPEC 6)."""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest

from bot.config.loader import load_config
from bot.core.structure import EventType, Side, StructureEngine, Trend, analyse_structure
from bot.core.swings import SwingKind
from bot.data.resample import resample
from tests.test_swings import make_series

# A hand-built sequence that establishes HH + HL and therefore a BULLISH trend.
# With fractal_n = 1: swing low 5 at i=1, swing high 12 at i=2, swing low 6 at i=3
# (HL, above 5), swing high 14 at i=4 (HH, above 12).  Trend initialises at bar 5.
BULL_BASE = [
    (9.0, 10.0, 8.0, 9.0),
    (9.0, 9.0, 5.0, 6.0),
    (6.0, 12.0, 7.0, 11.0),
    (11.0, 11.0, 6.0, 7.0),
    (7.0, 14.0, 10.0, 11.5),  # close stays under 12 so no first-break BOS pre-empts init
    (11.5, 13.0, 11.0, 12.5),
]


@pytest.fixture(scope="module")
def n1_cfg():
    c, _ = load_config(
        overrides={"swing": {"fractal_n": {"MN1": 1, "W1": 1, "D1": 2, "H4": 1, "H1": 3, "M15": 5}}}
    )
    return c


def run(bars, cfg):
    return analyse_structure(make_series(bars), cfg)


# ------------------------------------------------------------- initialisation


def test_trend_is_undefined_until_two_swings_of_each_kind(n1_cfg):
    r = run(BULL_BASE[:4], n1_cfg)
    assert r.state.trend is Trend.UNDEFINED
    assert not r.of_type(EventType.TREND_INITIALISED)


def test_hh_plus_hl_initialises_a_bullish_trend(n1_cfg):
    r = run(BULL_BASE, n1_cfg)
    init = r.of_type(EventType.TREND_INITIALISED)
    assert len(init) == 1
    assert init[0].side is Side.BULLISH
    assert r.state.trend is Trend.BULLISH
    assert r.state.protected_low is not None
    assert r.state.protected_low.price == 6.0


def test_undefined_is_a_real_state_not_a_fallback(n1_cfg):
    """A flat series has no structure, and the engine must say so rather than guess."""
    flat = [(1.0, 1.0, 1.0, 1.0)] * 20
    r = run(flat, n1_cfg)
    assert r.state.trend is Trend.UNDEFINED
    assert r.events == []


def test_first_break_resolves_undefined(n1_cfg):
    """SPEC 6.4: while UNDEFINED the first break in either direction sets the trend."""
    # LH + HL: labels disagree, so initialisation cannot fire.
    bars = [
        (9.0, 10.0, 8.0, 9.0),
        (9.0, 9.0, 5.0, 6.0),  # low 5
        (6.0, 12.0, 7.0, 11.0),  # high 12
        (11.0, 11.0, 6.0, 7.0),  # low 6  -> HL
        (7.0, 11.0, 8.0, 10.0),  # high 11 -> LH  (mixed: stays UNDEFINED)
        (10.0, 10.5, 9.0, 10.0),
        (10.0, 13.0, 9.5, 12.5),  # closes above 11 -> first break
    ]
    r = run(bars, n1_cfg)
    assert not r.of_type(EventType.TREND_INITIALISED)
    bos = r.of_type(EventType.BOS)
    assert bos and bos[0].resolved_undefined
    assert bos[0].trend_before is Trend.UNDEFINED
    assert r.state.trend is Trend.BULLISH


# ---------------------------------------------------------------------- BOS


def test_bos_fires_on_a_close_beyond_the_last_swing_high(n1_cfg):
    bars = BULL_BASE + [(12.5, 16.0, 12.0, 15.5)]
    r = run(bars, n1_cfg)
    bos = [e for e in r.of_type(EventType.BOS) if not e.resolved_undefined]
    assert len(bos) == 1
    assert bos[0].side is Side.BULLISH
    assert bos[0].level == 14.0
    assert r.state.trend is Trend.BULLISH


def test_a_wick_beyond_the_level_is_not_a_bos(n1_cfg):
    """SPEC 6.3.  Accepting wick breaks makes the system trade the pattern it fades."""
    bars = BULL_BASE + [(12.5, 16.0, 12.0, 13.0)]  # high 16 > 14, close 13 < 14
    r = run(bars, n1_cfg)
    assert not [e for e in r.of_type(EventType.BOS) if not e.resolved_undefined]


def test_a_broken_level_cannot_break_again(n1_cfg):
    """A break is an event, not a state.

    Without this, one sustained move emits a BOS on every bar until the next swing
    confirms N bars later -- 274 events where 49 were real, on the first run.
    """
    bars = BULL_BASE + [
        (12.5, 16.0, 12.0, 15.5),  # BOS above 14
        (15.5, 17.0, 15.0, 16.5),  # still above 14, no new swing yet
        (16.5, 17.5, 16.0, 17.0),
    ]
    r = run(bars, n1_cfg)
    bos = [e for e in r.of_type(EventType.BOS) if not e.resolved_undefined]
    assert len(bos) == 1
    assert len({(e.type, e.swing_id) for e in r.events}) == len(r.events)


# -------------------------------------------------------------------- CHoCH


def test_choch_flips_the_trend_on_a_close_below_the_protected_low(n1_cfg):
    bars = BULL_BASE + [(12.5, 13.0, 5.0, 5.5)]  # closes below protected low 6.0
    r = run(bars, n1_cfg)
    ch = r.of_type(EventType.CHOCH)
    assert len(ch) == 1
    assert ch[0].side is Side.BEARISH
    assert ch[0].level == 6.0
    assert ch[0].trend_before is Trend.BULLISH and ch[0].trend_after is Trend.BEARISH
    assert r.state.trend is Trend.BEARISH
    assert r.state.protected_low is None
    assert r.state.protected_high is not None


def test_a_wick_below_the_protected_low_is_a_liquidity_grab_not_a_choch(n1_cfg):
    """The trend survives, the protected level does NOT move, and the sweep is recorded."""
    bars = BULL_BASE + [(12.5, 13.0, 5.0, 8.0)]  # dips under 6.0, closes above
    r = run(bars, n1_cfg)
    assert not r.of_type(EventType.CHOCH)
    grabs = r.of_type(EventType.INTERNAL_LIQUIDITY_GRAB)
    assert len(grabs) == 1
    assert grabs[0].level == 6.0
    assert r.state.trend is Trend.BULLISH
    assert r.state.protected_low.price == 6.0  # unmoved (on_wick_below_protected = keep)


def test_a_level_is_grabbed_only_once(n1_cfg):
    bars = BULL_BASE + [
        (12.5, 13.0, 5.0, 8.0),
        (8.0, 9.0, 5.2, 8.5),
        (8.5, 9.0, 5.4, 8.6),
    ]
    r = run(bars, n1_cfg)
    assert len(r.of_type(EventType.INTERNAL_LIQUIDITY_GRAB)) == 1


def test_choch_cannot_fire_before_a_protected_level_exists(n1_cfg):
    """SPEC 6.8.  Only BOS is possible until the trend has a protected swing."""
    bars = [
        (9.0, 10.0, 8.0, 9.0),
        (9.0, 9.0, 5.0, 6.0),
        (6.0, 12.0, 7.0, 11.0),
        (11.0, 11.0, 2.0, 3.0),
    ]
    r = run(bars, n1_cfg)
    assert not r.of_type(EventType.CHOCH)


# --------------------------------------------------------- protected ratchet


def test_protected_low_ratchets_up_and_never_down(n1_cfg):
    """SPEC 6.4.  This is what makes CHoCH meaningful."""
    bars = BULL_BASE + [
        (12.5, 16.0, 12.0, 15.5),  # BOS
        (15.5, 16.0, 11.0, 12.0),  # low 11 at i=7
        (12.0, 17.0, 12.5, 16.5),  # confirms swing low 11 -> higher than 6, ratchets up
        (16.5, 18.0, 13.0, 17.0),
    ]
    r = run(bars, n1_cfg)
    assert r.state.trend is Trend.BULLISH
    assert r.state.protected_low.price == 11.0


def _monotone_between_bos(cfg, series, bullish: bool) -> None:
    """The protected level may only move toward price, EXCEPT at a BOS.

    SPEC 6.9 states the invariant without the exception; SPEC 6.4 defines the exception.
    The contradiction is resolved by ``structure.protected_on_bos`` -- see D-005 -- and
    this checks the invariant the default setting actually promises.
    """
    eng = StructureEngine(series, cfg)
    prev = None
    for i in range(series.n):
        events = eng.on_bar_close(i)
        st = eng.state
        reset = any(e.type is EventType.BOS for e in events)
        want = Trend.BULLISH if bullish else Trend.BEARISH
        level = st.protected_low if bullish else st.protected_high
        if st.trend is want and level is not None:
            if prev is not None and not reset:
                ok = level.price >= prev if bullish else level.price <= prev
                assert ok, f"protected level moved away from price at bar {i} without a BOS"
            prev = level.price
        else:
            prev = None


def test_protected_low_moves_toward_price_except_at_a_bos(cfg, m15_quarter):
    _monotone_between_bos(cfg, resample(m15_quarter, "H4", cfg), bullish=True)


def test_protected_high_moves_toward_price_except_at_a_bos(cfg, m15_quarter):
    _monotone_between_bos(cfg, resample(m15_quarter, "H4", cfg), bullish=False)


def test_ratchet_only_ablation_is_strictly_monotone(cfg, m15_quarter):
    """The other side of the SPEC 6.4 / 6.9 contradiction, available and measurable."""
    ratchet, _ = load_config(overrides={"structure": {"protected_on_bos": "ratchet_only"}})
    h4 = resample(m15_quarter, "H4", cfg)
    eng = StructureEngine(h4, ratchet)
    prev = None
    for i in range(h4.n):
        eng.on_bar_close(i)
        st = eng.state
        if st.trend is Trend.BULLISH and st.protected_low is not None:
            if prev is not None:
                assert st.protected_low.price >= prev
            prev = st.protected_low.price
        else:
            prev = None
    # And it is a real fork: the two settings do not produce the same structure.
    assert [e.id for e in analyse_structure(h4, ratchet).events] != [
        e.id for e in analyse_structure(h4, cfg).events
    ]


# ------------------------------------------------------------------ invariants


def test_trend_never_changes_without_a_choch_or_an_initialising_event(cfg, m15_quarter):
    r = analyse_structure(resample(m15_quarter, "H4", cfg), cfg)
    for e in r.events:
        if e.trend_before is not e.trend_after:
            assert e.type is EventType.CHOCH or e.type is EventType.TREND_INITIALISED or e.resolved_undefined


def test_trend_transitions_are_continuous(cfg, m15_quarter):
    """Each event's trend_before must equal the previous event's trend_after."""
    r = analyse_structure(resample(m15_quarter, "H4", cfg), cfg)
    for a, b in zip(r.events, r.events[1:]):
        assert b.trend_before is a.trend_after


def test_every_event_references_a_swing_that_had_confirmed(cfg, m15_quarter):
    h4 = resample(m15_quarter, "H4", cfg)
    r = analyse_structure(h4, cfg)
    by_id = {s.id: s for s in r.swings.swings}
    for e in r.events:
        if e.swing_id is None:
            continue
        s = by_id.get(e.swing_id)
        if s is None:
            continue  # replaced by normalisation after the event fired; legal (SPEC 5.4)
        assert s.confirmed_index <= e.bar_index
        assert s.confirmed_at <= e.at


def test_whipsaw_is_flagged_when_two_flips_are_too_close(n1_cfg):
    fast, _ = load_config(
        overrides={
            "swing": {"fractal_n": {"MN1": 1, "W1": 1, "D1": 2, "H4": 1, "H1": 3, "M15": 5}},
            "structure": {"min_bars_between_flips": 20},
        }
    )
    bars = BULL_BASE + [
        (12.5, 13.0, 5.0, 5.5),  # CHoCH bearish
        (5.5, 6.0, 4.0, 4.5),
        (4.5, 5.0, 3.0, 3.5),
        (3.5, 20.0, 3.0, 19.0),  # violent reversal -> CHoCH bullish, close behind
    ]
    r = analyse_structure(make_series(bars), fast)
    chs = r.of_type(EventType.CHOCH)
    assert len(chs) >= 2
    assert chs[-1].whipsaw


# ------------------------------------------------------------------ causality


def test_structure_is_prefix_stable(cfg, m15_quarter):
    """SPEC 25.2 for the structure engine: an emitted event is never revised."""
    h4 = resample(m15_quarter, "H4", cfg)
    full = analyse_structure(h4, cfg)
    rng = np.random.default_rng(4)
    for k in rng.choice(np.arange(80, h4.n), size=40, replace=False):
        trunc = analyse_structure(h4.head(int(k)), cfg)
        assert len(trunc.events) <= len(full.events)
        for a, b in zip(trunc.events, full.events):
            assert (a.id, a.type, a.side, a.bar_index, a.level) == (
                b.id,
                b.type,
                b.side,
                b.bar_index,
                b.level,
            )


def test_incremental_equals_batch(cfg, m15_quarter):
    """The live path and the research path must produce identical event streams."""
    h4 = resample(m15_quarter, "H4", cfg)
    batch = analyse_structure(h4, cfg)
    eng = StructureEngine(h4, cfg)
    stepped = []
    for i in range(h4.n):
        stepped.extend(eng.on_bar_close(i))
    assert [e.id for e in stepped] == [e.id for e in batch.events]
    assert eng.state.trend is batch.state.trend


def test_no_event_uses_a_future_bar(cfg, m15_quarter):
    h4 = resample(m15_quarter, "H4", cfg)
    r = analyse_structure(h4, cfg)
    for e in r.events:
        assert e.at == h4.close_dt(e.bar_index)


# ------------------------------------------------------------- ablation paths


def test_wick_confirmation_changes_the_structure(cfg, m15_quarter):
    """The ablation must be reachable and must actually change what the engine sees.

    Deliberately NOT asserted as "more events": an earlier wick break consumes its
    level and can flip the trend sooner, which changes the whole downstream trajectory,
    so the totals are not ordered.  Measured on the fixture quarter: 51 events against
    52.  The naive expectation was wrong, and pinning it would have pinned a coincidence.
    """
    wick, _ = load_config(overrides={"structure": {"break_confirmation": "wick"}})
    h4 = resample(m15_quarter, "H4", cfg)
    close_events = analyse_structure(h4, cfg).events
    wick_events = analyse_structure(h4, wick).events
    assert [(e.type, e.bar_index) for e in wick_events] != [
        (e.type, e.bar_index) for e in close_events
    ]
    # Wick breaks fire no later than close breaks on the same level.
    assert wick_events[0].bar_index <= close_events[0].bar_index


def test_min_penetration_reduces_breaks(cfg, m15_quarter):
    strict, _ = load_config(overrides={"structure": {"min_break_penetration_atr": 0.15}})
    h4 = resample(m15_quarter, "H4", cfg)
    assert len(analyse_structure(h4, strict).events) < len(analyse_structure(h4, cfg).events)
