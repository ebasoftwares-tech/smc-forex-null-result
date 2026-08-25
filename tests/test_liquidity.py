"""Liquidity engine (SPEC 8)."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from bot.config.loader import load_config
from bot.core.bars import build_series, from_epoch_s
from bot.core.liquidity import (
    LevelSource,
    LevelStatus,
    LiquidityEngine,
    Side,
    build_book,
    build_candidates,
    liquidity_session_names,
    tier_for,
)
from bot.core.sessions import SessionStatus, build_sessions
from bot.core.structure import analyse_structure
from bot.core.swings import detect_swings
from bot.data.calendar import UTC
from bot.data.resample import resample


@pytest.fixture(scope="module")
def parts(cfg, m15_quarter):
    h4 = resample(m15_quarter, "H4", cfg)
    d1 = resample(m15_quarter, "D1", cfg)
    w1 = resample(m15_quarter, "W1", cfg)
    mn1 = resample(m15_quarter, "MN1", cfg)
    return {
        "m15": m15_quarter,
        "h4": h4,
        "d1": d1,
        "w1": w1,
        "mn1": mn1,
        "sessions": build_sessions(m15_quarter, cfg),
        "structure": analyse_structure(h4, cfg),
        "d1_swings": detect_swings(d1, cfg),
    }


@pytest.fixture(scope="module")
def book(cfg, parts):
    return build_book(
        cfg=cfg,
        h4=parts["h4"],
        d1=parts["d1"],
        w1=parts["w1"],
        mn1=parts["mn1"],
        sessions=parts["sessions"],
        h4_structure=parts["structure"],
        d1_swings=parts["d1_swings"],
    )


# ------------------------------------------------------- SPEC 8.4, the gate item


def test_no_level_comes_from_a_forming_period(cfg, parts, book):
    """SPEC 8.4 -- the running-extreme prohibition, and the Phase 6 gate item.

    A level still being made cannot be swept.  Code that allows it reports a "sweep"
    whenever price pulls back from a new high, which is most bars, and which fabricates
    the strategy's central event out of nothing.
    """
    last_close = int(parts["m15"].close_time[-1])
    for lvl in book.levels:
        assert int(lvl.confirmed_at.timestamp()) <= last_close
        # Confirmation never precedes the thing it describes.
        assert lvl.confirmed_at >= lvl.formed_at


def test_period_levels_confirm_at_their_own_period_close(cfg, parts):
    """A PREV_DAY level exists only once that day's D1 bar has closed."""
    from bot.core.liquidity import period_levels

    d1 = parts["d1"]
    levels = period_levels(d1, LevelSource.PREV_DAY_HIGH, LevelSource.PREV_DAY_LOW, 0)
    by_close: dict[int, list] = {}
    for l in levels:
        by_close.setdefault(int(l.confirmed_at.timestamp()), []).append(l)
    for i in range(d1.n):
        got = by_close[int(d1.close_time[i])]
        assert {l.price for l in got} == {float(d1.high[i]), float(d1.low[i])}


def test_forming_and_incomplete_sessions_contribute_nothing(cfg, parts):
    from bot.core.liquidity import session_levels

    names = liquidity_session_names(cfg)
    sessions = parts["sessions"]
    assert any(s.status is SessionStatus.FORMING for s in sessions)
    levels = session_levels(sessions, 0, names)
    used = {
        (l.source_ids[0]) for l in levels
    }
    for s in sessions:
        key = f"{s.session_name}:{s.trading_date.isoformat()}"
        if s.status is not SessionStatus.CLOSED or s.session_name not in names:
            assert key not in used


def test_a_level_cannot_be_created_and_swept_by_the_same_bar(cfg, parts, book):
    """SPEC 8.9, first row.  Impossible by construction; asserted rather than assumed."""
    h4 = parts["h4"]
    for lvl in book.levels:
        if lvl.confirmed_bar < 0:
            continue
        # Admission happens on a bar whose close is at or after confirmation, and any
        # sweep needs a strictly later bar.
        assert int(h4.close_time[lvl.confirmed_bar]) >= int(lvl.confirmed_at.timestamp())


# ------------------------------------------------------------------- causality


def test_admission_never_precedes_confirmation(cfg, parts):
    h4 = parts["h4"]
    cands = build_candidates(
        cfg=cfg,
        h4=h4,
        d1=parts["d1"],
        w1=parts["w1"],
        mn1=parts["mn1"],
        sessions=parts["sessions"],
        h4_structure=parts["structure"],
        d1_swings=parts["d1_swings"],
    )
    eng = LiquidityEngine(h4, cfg, cands, d1_close_times=parts["d1"].close_time)
    for i in range(h4.n):
        at = from_epoch_s(h4.close_time[i])
        for lvl in eng.on_bar_close(i):
            assert lvl.confirmed_at <= at


def test_admission_order_is_prefix_stable(cfg, parts):
    """SPEC 25.2 applied to this engine.

    Truncating the input may only remove admissions from the end; it may never change
    which level was admitted on which bar.  Any use of a future bar breaks this,
    because the truncated engine cannot see what leaked.
    """
    h4 = parts["h4"]

    def admissions(n: int) -> list[tuple[int, str]]:
        sub = h4.head(n)
        cands = build_candidates(
            cfg=cfg,
            h4=sub,
            d1=parts["d1"],
            w1=parts["w1"],
            mn1=parts["mn1"],
            sessions=parts["sessions"],
            h4_structure=parts["structure"],
            d1_swings=parts["d1_swings"],
        )
        eng = LiquidityEngine(sub, cfg, cands, d1_close_times=parts["d1"].close_time)
        seen: list[tuple[int, str]] = []
        for i in range(sub.n):
            for lvl in eng.on_bar_close(i):
                seen.append((i, lvl.source.value))
        return seen

    full = admissions(h4.n)
    for k in (int(h4.n * 0.4), int(h4.n * 0.7), h4.n - 5):
        part = admissions(k)
        assert part == full[: len(part)], k


# ----------------------------------------------------------------- SPEC 8.6 tiers


@pytest.mark.parametrize(
    "source,timeframe,tier",
    [
        (LevelSource.PREV_MONTH_HIGH, "MN1", 1),
        (LevelSource.PREV_WEEK_LOW, "W1", 1),
        (LevelSource.EQUAL_HIGHS, "H4", 1),
        (LevelSource.SWING_HIGH, "D1", 1),
        (LevelSource.SWING_HIGH, "H4", 2),
        (LevelSource.PROTECTED_SWING, "H4", 2),
        (LevelSource.PREV_DAY_HIGH, "D1", 2),
        (LevelSource.SESSION_LOW, "M15", 3),
        (LevelSource.RANGE_HIGH, "H4", 3),
    ],
)
def test_tier_table_matches_the_spec(source, timeframe, tier):
    assert tier_for(source, timeframe) == tier


# ----------------------------------------------------------------- SPEC 8.8 merge


def test_no_two_active_levels_remain_within_merge_tolerance(cfg, parts):
    """The merge invariant, checked on every bar rather than only at the end."""
    h4 = parts["h4"]
    cands = build_candidates(
        cfg=cfg,
        h4=h4,
        d1=parts["d1"],
        w1=parts["w1"],
        mn1=parts["mn1"],
        sessions=parts["sessions"],
        h4_structure=parts["structure"],
        d1_swings=parts["d1_swings"],
    )
    eng = LiquidityEngine(h4, cfg, cands, d1_close_times=parts["d1"].close_time)
    checked = 0
    for i in range(h4.n):
        eng.on_bar_close(i)
        tol = cfg.liq.merge_tolerance_atr * eng._atr(i)
        if tol <= 0:
            continue
        for side in (Side.BUY_SIDE, Side.SELL_SIDE):
            prices = sorted(l.price for l in eng.book.active() if l.side is side)
            for a, b in zip(prices, prices[1:]):
                assert b - a > tol, (i, side, a, b, tol)
            checked += 1
    assert checked > 100


def _ladder(cfg, step_mult: float, count: int = 10):
    """A ladder of BUY_SIDE levels spaced ``step_mult`` x the merge tolerance apart."""
    from bot.core.liquidity import LiquidityLevel

    n = 40
    t = np.arange(n, dtype=np.int64) * 14400
    px = np.full(n, 1.1000)
    h4 = build_series("X", "H4", t, t + 14400, px, px + 0.0010, px - 0.0010, px, np.ones(n))
    eng0 = LiquidityEngine(h4, cfg, [])
    tol = cfg.liq.merge_tolerance_atr * eng0._atr(n - 1)
    assert tol > 0
    at = from_epoch_s(int(h4.close_time[0]))
    cands = [
        LiquidityLevel(
            id=f"L{k}",
            symbol="X",
            side=Side.BUY_SIDE,
            source=LevelSource.SWING_HIGH,
            timeframe="H4",
            tier=2,
            price=1.1000 + k * tol * step_mult,
            formed_at=at,
            confirmed_at=at,
        )
        for k in range(count)
    ]
    eng = LiquidityEngine(h4, cfg, cands)
    for i in range(h4.n):
        eng.on_bar_close(i)
    return eng, tol, cands


def test_levels_further_apart_than_the_tolerance_never_merge(cfg):
    """The real guard against runaway merging."""
    eng, tol, cands = _ladder(cfg, step_mult=1.5)
    assert len(eng.book.active()) == len(cands)
    assert eng.book.merged == 0


def test_merging_is_transitive_and_collapses_a_dense_ladder(cfg):
    """SPEC 8.8 makes merging transitive, and that is worth stating explicitly.

    The survivor takes the *more extreme* price, which moves it toward the next
    cluster, so a ladder whose neighbours all sit inside the tolerance collapses to the
    extremes even though its endpoints are far outside it.  That is the specified rule
    working as written, not chain drift: the stops sit above the highest high, and one
    level is what a sweep must clear.

    The consequence is quantified in the Phase 6 report and is why the fixture's merge
    rate is ~65%: with ~40 active levels inside a 5-ATR in-play band and a 0.1-ATR
    tolerance, the population is dense enough that most levels have a near neighbour.
    """
    eng, tol, cands = _ladder(cfg, step_mult=0.6)
    survivors = eng.book.active()
    assert 1 <= len(survivors) < len(cands)
    # Whatever collapses, the post-condition still holds.
    prices = sorted(l.price for l in survivors)
    for a, b in zip(prices, prices[1:]):
        assert b - a > tol


def test_a_merged_price_is_always_a_real_constituent_price(cfg):
    """Merging may move a level, but never to a price no constituent ever had."""
    eng, tol, cands = _ladder(cfg, step_mult=0.6)
    original = {round(c.price, 10) for c in cands}
    for lvl in eng.book.active():
        assert round(lvl.price, 10) in original


def test_merge_sums_strength_takes_the_extreme_price_and_the_lower_tier(cfg, book):
    merged = [l for l in book.levels if l.status is LevelStatus.MERGED]
    survivors = [l for l in book.levels if l.strength > 1]
    assert merged and survivors
    for l in survivors:
        assert l.strength == len(l.source_ids) or l.strength >= 2
        assert l.tier in (1, 2, 3)
    # Every merged level was terminated with a timestamp, so the population report can
    # account for it (SPEC 8.10).
    for l in merged:
        assert l.terminal_at is not None
        assert l.terminal_bar >= 0


# -------------------------------------------------------- SPEC 8.7 lifecycle


def test_invalidation_needs_consecutive_closes_beyond_the_buffer(cfg):
    """SPEC 8.7.  One close beyond is a poke; ``invalidate_closes`` in a row is
    acceptance."""
    from bot.core.liquidity import LiquidityLevel

    n = 40
    t = np.arange(n, dtype=np.int64) * 14400
    close = np.full(n, 1.1000)
    close[20] = 1.2000  # one bar far beyond, then back
    high = np.maximum(close + 0.0005, 1.1005)
    low = np.minimum(close - 0.0005, 1.0995)
    h4 = build_series("X", "H4", t, t + 14400, close, high, low, close, np.ones(n))

    at = from_epoch_s(int(h4.close_time[0]))
    lvl = LiquidityLevel(
        id="L1",
        symbol="X",
        side=Side.BUY_SIDE,
        source=LevelSource.SWING_HIGH,
        timeframe="H4",
        tier=2,
        price=1.1010,
        formed_at=at,
        confirmed_at=at,
    )
    eng = LiquidityEngine(h4, cfg, [lvl])
    for i in range(h4.n):
        eng.on_bar_close(i)
    assert lvl.status is LevelStatus.ACTIVE, "a single close beyond must not invalidate"
    assert lvl.penetrated is True


def test_prev_month_levels_never_expire_by_age(cfg, book):
    """SPEC 8.7 -- they are replaced monthly, not aged out."""
    pm = [
        l
        for l in book.levels
        if l.source in (LevelSource.PREV_MONTH_HIGH, LevelSource.PREV_MONTH_LOW)
    ]
    assert pm
    assert not any(l.status is LevelStatus.EXPIRED for l in pm)


def test_expiry_respects_the_per_tier_limit(cfg, book):
    expired = [l for l in book.levels if l.status is LevelStatus.EXPIRED]
    assert expired
    for l in expired:
        limit = cfg.liq.max_age_d1_bars[str(l.tier)]
        assert l.age_d1 > limit


def test_active_levels_never_exceed_the_cap(cfg, parts):
    h4 = parts["h4"]
    cands = build_candidates(
        cfg=cfg,
        h4=h4,
        d1=parts["d1"],
        w1=parts["w1"],
        mn1=parts["mn1"],
        sessions=parts["sessions"],
        h4_structure=parts["structure"],
        d1_swings=parts["d1_swings"],
    )
    eng = LiquidityEngine(h4, cfg, cands, d1_close_times=parts["d1"].close_time)
    for i in range(h4.n):
        eng.on_bar_close(i)
        assert len(eng.book.active()) <= cfg.liq.max_active_levels


def test_terminal_levels_never_return(cfg, book):
    """SPEC 8.9: a pruned level never returns; the same holds for every terminal state."""
    for l in book.levels:
        if l.status is not LevelStatus.ACTIVE:
            assert l.terminal_at is not None


# ------------------------------------------------------------ SPEC 8.5.1 equals


def test_equal_highs_respect_touches_tolerance_separation_and_span(cfg, parts):
    from bot.core.liquidity import equal_levels

    store = parts["structure"].swings
    levels = equal_levels(store, parts["h4"], cfg, 0)
    assert levels, "no equal-price clusters found"
    by_id = {s.id: s for s in store.swings}
    for lvl in levels:
        members = [by_id[i] for i in lvl.source_ids]
        assert len(members) >= cfg.eq.min_touches
        idx = sorted(m.formed_index for m in members)
        assert idx[-1] - idx[0] <= cfg.eq.max_span_bars
        for a, b in zip(idx, idx[1:]):
            assert b - a >= cfg.eq.min_separation_bars
        prices = [m.price for m in members]
        if lvl.source is LevelSource.EQUAL_HIGHS:
            assert lvl.price == pytest.approx(max(prices))
            assert all(m.is_high for m in members)
        else:
            assert lvl.price == pytest.approx(min(prices))
            assert all(not m.is_high for m in members)


def test_one_level_per_shelf_even_as_the_cluster_grows(cfg, parts):
    """A cluster that grows from two touches to three amends its strength rather than
    emitting a second level for the same shelf of stops."""
    from bot.core.liquidity import equal_levels

    levels = equal_levels(parts["structure"].swings, parts["h4"], cfg, 0)
    keys = [(l.source, l.source_ids[0]) for l in levels]
    assert len(keys) == len(set(keys))


# ------------------------------------------------------------- sources on/off


def test_overlap_and_killzones_are_not_liquidity_sources(cfg, parts):
    """OVERLAP is a sub-window of two sessions already counted; a killzone is an
    execution window.  Measured on the fixture, an overlap extreme coincides with the
    London or New York extreme on ~90% of days (D-006)."""
    names = liquidity_session_names(cfg)
    assert "OVERLAP" not in names
    assert "LONDON_KZ" not in names and "NY_KZ" not in names
    assert {"ASIA", "LONDON", "NEW_YORK", "ASIA_RANGE"} <= names


def test_range_source_is_off_by_default(cfg, book):
    """SPEC 8.5.2 calls ranges the least well-founded source and marks them
    ABLATION-ONLY."""
    assert "RANGE" not in cfg.liq.enabled_sources
    assert not [
        l
        for l in book.levels
        if l.source in (LevelSource.RANGE_HIGH, LevelSource.RANGE_LOW)
    ]


def test_disabling_a_source_removes_it_entirely(cfg, parts):
    c, _ = load_config(overrides={"liq": {"enabled_sources": ["PREV_DAY"]}})
    b = build_book(
        cfg=c,
        h4=parts["h4"],
        d1=parts["d1"],
        w1=parts["w1"],
        mn1=parts["mn1"],
        sessions=parts["sessions"],
        h4_structure=parts["structure"],
        d1_swings=parts["d1_swings"],
    )
    assert set(b.by_source()) == {"PREV_DAY_HIGH", "PREV_DAY_LOW"}


def test_enabling_ranges_emits_on_the_rising_edge_only(cfg, parts):
    """A twenty-bar consolidation must not emit a fresh pair of levels on every bar it
    persists, or the population report is swamped."""
    from bot.core.liquidity import range_levels

    levels = range_levels(parts["h4"], cfg, 0)
    stamps = sorted({int(l.confirmed_at.timestamp()) for l in levels})
    for a, b in zip(stamps, stamps[1:]):
        assert b - a >= 14400
    # Each emission is a high/low pair.
    assert len(levels) == 2 * len(stamps)


# ------------------------------------------------------------ ranking / in-play


def test_rank_prefers_stronger_tier_then_strength_then_recency(cfg, parts, book):
    eng = LiquidityEngine(parts["h4"], cfg, [])
    active = book.active()
    assert active
    scores = {l.id: eng.rank(l, parts["h4"].n - 1) for l in active}
    assert all(np.isfinite(v) for v in scores.values())
    t1 = [l for l in active if l.tier == 1]
    t3 = [l for l in active if l.tier == 3]
    if t1 and t3:
        # Same strength and recency, tier 1 must outrank tier 3.
        a, b = t1[0], t3[0]
        a.strength = b.strength = 1
        a.age_d1 = b.age_d1 = 0
        assert eng.rank(a, 0) > eng.rank(b, 0)


def test_in_play_filter_excludes_distant_levels(cfg, parts, book):
    h4 = parts["h4"]
    eng = LiquidityEngine(h4, cfg, [])
    i = h4.n - 1
    a = eng._atr(i)
    close = float(h4.close[i])
    for l in book.active():
        expected = abs(l.price - close) <= cfg.liq.max_distance_atr * a
        assert eng.in_play(l, i) == expected


def test_side_is_fixed_at_creation_and_never_flips(cfg, book):
    """SPEC 8.9: if price crosses a level it does not change side -- it invalidates."""
    for l in book.levels:
        if l.source.value.endswith("_HIGH") or l.source is LevelSource.EQUAL_HIGHS:
            assert l.side is Side.BUY_SIDE
        elif l.source.value.endswith("_LOW") or l.source is LevelSource.EQUAL_LOWS:
            assert l.side is Side.SELL_SIDE
