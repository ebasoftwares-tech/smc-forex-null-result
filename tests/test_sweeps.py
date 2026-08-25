"""Liquidity sweep detection (SPEC 9)."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

import numpy as np
import pytest

from bot.config.loader import load_config
from bot.core.bars import build_series, from_epoch_s
from bot.core.liquidity import LevelSource, LevelStatus, LiquidityBook, LiquidityLevel, Side
from bot.core.sessions import build_sessions
from bot.core.structure import analyse_structure
from bot.core.sweeps import (
    SweepEngine,
    SweepEventType,
    SweepReason,
    analyse_sweeps,
)
from bot.core.swings import detect_swings
from bot.data.calendar import UTC
from bot.data.resample import resample

H4 = 14400


# --------------------------------------------------------------------- harness


def flat_series(n: int, mid: float, half_range: float, symbol: str = "EURUSD"):
    """Warm-up bars with a constant true range, so ATR is exactly ``2*half_range``."""
    t = np.arange(n, dtype=np.int64) * H4
    o = np.full(n, mid)
    c = np.full(n, mid)
    h = np.full(n, mid + half_range)
    l = np.full(n, mid - half_range)
    return t, o, h, l, c


def series_with_tail(warm: int, mid: float, half_range: float, tail: list[tuple]):
    """``warm`` flat bars followed by explicit (o, h, l, c) tuples."""
    t, o, h, l, c = flat_series(warm, mid, half_range)
    ts = list(t) + [(warm + k) * H4 for k in range(len(tail))]
    O = list(o) + [x[0] for x in tail]
    H = list(h) + [x[1] for x in tail]
    L = list(l) + [x[2] for x in tail]
    C = list(c) + [x[3] for x in tail]
    ts = np.asarray(ts, dtype=np.int64)
    return build_series(
        "EURUSD",
        "H4",
        ts,
        ts + H4,
        np.asarray(O),
        np.asarray(H),
        np.asarray(L),
        np.asarray(C),
        np.ones(len(ts)),
    )


def one_level(price: float, side: Side, series, source=LevelSource.SESSION_LOW, tf="M15"):
    at = from_epoch_s(int(series.close_time[0]))
    return LiquidityLevel(
        id="L000001",
        symbol="EURUSD",
        side=side,
        source=source,
        timeframe=tf,
        tier=3,
        price=price,
        formed_at=at,
        confirmed_at=at,
    )


def run_one(cfg, series, levels):
    book = LiquidityBook("EURUSD", "H4")
    book.levels.extend(levels)
    for l in levels:
        l.confirmed_bar = 0
    eng = SweepEngine(series, cfg, book, tf_close_times={"H4": series.close_time})
    return eng.run()


# --------------------------------------------------------- SPEC 9.5 worked example


def test_spec_9_5_worked_example_reproduces_exactly(cfg):
    """The spec's own numbers, checked digit for digit.

    Warm-up bars carry a constant 0.00380 true range so ``ATR_ref`` at the trigger bar
    is exactly the 0.00380 the example states.
    """
    warm = 20
    series = series_with_tail(
        warm,
        mid=1.17000,
        half_range=0.00190,  # range 0.00380 -> ATR 0.00380
        tail=[(1.16560, 1.16600, 1.16420, 1.16540)],
    )
    eng_probe = SweepEngine(series, cfg, LiquidityBook("EURUSD", "H4"))
    assert eng_probe._atr(warm) == pytest.approx(0.00380, abs=1e-9)

    lvl = one_level(1.16500, Side.SELL_SIDE, series)
    res = run_one(cfg, series, [lvl])

    conf = res.confirmed()
    assert len(conf) == 1
    e = conf[0]
    assert e.trigger_bar == warm and e.confirm_bar == warm
    assert e.single_bar_sweep is True
    assert e.sweep_extreme == pytest.approx(1.16420)
    assert e.penetration == pytest.approx(0.00080)
    assert e.penetration_atr == pytest.approx(0.2105, abs=1e-4)
    assert e.wick_ratio == pytest.approx(0.6667, abs=1e-4)
    assert e.close_position == pytest.approx(0.6667, abs=1e-4)
    assert e.setup_direction == "BULLISH"
    assert lvl.status is LevelStatus.SWEPT
    assert lvl.swept_by == e.id


def test_the_example_still_confirms_with_the_optional_filters_on():
    """SPEC 9.5 states the wick (0.3) and close-position (0.5) filters both pass."""
    c, _ = load_config(overrides={"sweep": {"min_wick_ratio": 0.3, "min_close_position": 0.5}})
    series = series_with_tail(20, 1.17000, 0.00190, [(1.16560, 1.16600, 1.16420, 1.16540)])
    res = run_one(c, series, [one_level(1.16500, Side.SELL_SIDE, series)])
    assert len(res.confirmed()) == 1


# ------------------------------------------------------------- SPEC 9.1 the rule


def test_penetration_below_the_minimum_is_rejected(cfg):
    """A sub-pip nick is usually a spread artefact, not a stop run (SPEC 9.2)."""
    # 0.04 ATR of penetration, under the 0.05 floor.
    series = series_with_tail(20, 1.17000, 0.00190, [(1.16560, 1.16600, 1.16485, 1.16540)])
    res = run_one(cfg, series, [one_level(1.16500, Side.SELL_SIDE, series)])
    assert not res.confirmed()
    rej = res.of_type(SweepEventType.REJECTED)
    assert len(rej) == 1 and rej[0].reason is SweepReason.UNDER_PENETRATION


def test_over_penetration_is_a_breakout_and_invalidates(cfg):
    """SPEC 9.6: the deliberate boundary between sweep and breakout."""
    # 1.5 ATR deep, then closes back above -- still a breakout.
    series = series_with_tail(20, 1.17000, 0.00190, [(1.16560, 1.16600, 1.15930, 1.16540)])
    lvl = one_level(1.16500, Side.SELL_SIDE, series)
    res = run_one(cfg, series, [lvl])
    assert not res.confirmed()
    failed = res.of_type(SweepEventType.FAILED)
    assert len(failed) == 1 and failed[0].reason is SweepReason.OVER_PENETRATION
    assert lvl.status is LevelStatus.INVALIDATED


def test_no_reclaim_within_the_window_fails_and_invalidates(cfg):
    """SPEC 9.1: failure to reclaim is NOT 'no event'.

    The ratio of confirmed to failed sweeps per source is a direct measure of whether a
    level is a real barrier, and that measurement is impossible if failures are dropped.
    """
    # The first bar must STRADDLE the level: a bar entirely below it would be a
    # gap-through (SPEC 9.6), which is a different event with a different ruling.
    tail = [(1.16560, 1.16580, 1.16420, 1.16450)] + [
        (1.16450, 1.16490, 1.16420, 1.16450)
    ] * 3
    series = series_with_tail(20, 1.17000, 0.00190, tail)
    lvl = one_level(1.16500, Side.SELL_SIDE, series)
    res = run_one(cfg, series, [lvl])
    assert not res.confirmed()
    failed = res.of_type(SweepEventType.FAILED)
    assert len(failed) == 1
    assert failed[0].reason is SweepReason.NO_RECLAIM
    assert failed[0].confirmation_bars == cfg.sweep.max_confirmation_bars
    assert lvl.status is LevelStatus.INVALIDATED


def test_reclaim_on_the_last_allowed_bar_still_confirms(cfg):
    tail = [
        (1.16560, 1.16580, 1.16420, 1.16450),
        (1.16450, 1.16490, 1.16440, 1.16460),
        (1.16460, 1.16600, 1.16450, 1.16560),  # bar 3 of 3
    ]
    series = series_with_tail(20, 1.17000, 0.00190, tail)
    res = run_one(cfg, series, [one_level(1.16500, Side.SELL_SIDE, series)])
    conf = res.confirmed()
    assert len(conf) == 1
    assert conf[0].confirmation_bars == 3
    assert conf[0].single_bar_sweep is False


def test_sweep_extreme_is_the_minimum_over_the_whole_window(cfg):
    """SPEC 9.6: penetrate, reclaim, penetrate again inside the window.

    The stop sits below the deepest point, so the extreme must be the running minimum
    and not merely the trigger bar's low.
    """
    tail = [
        (1.16560, 1.16580, 1.16440, 1.16460),
        (1.16460, 1.16490, 1.16380, 1.16400),  # deeper
        (1.16400, 1.16600, 1.16390, 1.16560),  # reclaim
    ]
    series = series_with_tail(20, 1.17000, 0.00190, tail)
    res = run_one(cfg, series, [one_level(1.16500, Side.SELL_SIDE, series)])
    conf = res.confirmed()
    assert len(conf) == 1
    assert conf[0].sweep_extreme == pytest.approx(1.16380)


def test_buy_side_is_the_exact_mirror(cfg):
    series = series_with_tail(20, 1.16000, 0.00190, [(1.16440, 1.16580, 1.16400, 1.16460)])
    lvl = one_level(1.16500, Side.BUY_SIDE, series, source=LevelSource.SESSION_HIGH)
    res = run_one(cfg, series, [lvl])
    conf = res.confirmed()
    assert len(conf) == 1
    assert conf[0].sweep_extreme == pytest.approx(1.16580)
    assert conf[0].penetration == pytest.approx(0.00080)
    assert conf[0].setup_direction == "BEARISH"


def test_level_must_be_confirmed_before_the_trigger_bar_opens(cfg):
    """SPEC 9.1: ``level.confirmed_at <= open_time(s)``."""
    series = series_with_tail(20, 1.17000, 0.00190, [(1.16560, 1.16600, 1.16420, 1.16540)])
    lvl = one_level(1.16500, Side.SELL_SIDE, series)
    # Confirmed one second after the trigger bar opens.
    lvl.confirmed_at = from_epoch_s(int(series.open_time[20])) + timedelta(seconds=1)
    res = run_one(cfg, series, [lvl])
    assert not res.events


# ----------------------------------------------------------- SPEC 9.2.1 age rule


def test_session_levels_are_exempt_from_the_age_rule(cfg):
    """D-002a.  Applying the age rule to session levels put the earliest sweepable
    moment after the London close and made the flagship setup unreachable."""
    series = series_with_tail(20, 1.17000, 0.00190, [(1.16560, 1.16600, 1.16420, 1.16540)])
    lvl = one_level(1.16500, Side.SELL_SIDE, series, source=LevelSource.SESSION_LOW)
    lvl.confirmed_at = from_epoch_s(int(series.open_time[20]))  # brand new
    res = run_one(cfg, series, [lvl])
    assert len(res.confirmed()) == 1


def test_swing_levels_are_guarded_by_the_age_rule(cfg):
    series = series_with_tail(20, 1.17000, 0.00190, [(1.16560, 1.16600, 1.16420, 1.16540)])
    lvl = one_level(1.16500, Side.SELL_SIDE, series, source=LevelSource.SWING_LOW, tf="H4")
    lvl.confirmed_at = from_epoch_s(int(series.close_time[19]))  # one H4 bar old
    res = run_one(cfg, series, [lvl])
    assert not res.events, "a one-bar-old swing level must not be sweepable"

    lvl2 = one_level(1.16500, Side.SELL_SIDE, series, source=LevelSource.SWING_LOW, tf="H4")
    lvl2.id = "L000002"
    lvl2.confirmed_at = from_epoch_s(int(series.close_time[16]))  # four bars old
    res2 = run_one(cfg, series, [lvl2])
    assert len(res2.confirmed()) == 1


# ------------------------------------------------------------------ SPEC 9.6 gap


def test_a_level_gapped_through_is_invalidated_never_swept(cfg):
    """No penetration bar exists, so there is nothing to reclaim."""
    tail = [(1.16000, 1.16100, 1.15900, 1.16000)]  # whole bar below the level
    series = series_with_tail(20, 1.17000, 0.00190, tail)
    lvl = one_level(1.16500, Side.SELL_SIDE, series)
    res = run_one(cfg, series, [lvl])
    assert not res.confirmed()
    failed = res.of_type(SweepEventType.FAILED)
    assert len(failed) == 1 and failed[0].reason is SweepReason.GAPPED_THROUGH
    assert lvl.status is LevelStatus.INVALIDATED


# --------------------------------------------------------- SPEC 9.4 multi-level


def test_stacked_levels_swept_by_one_bar_form_one_cluster(cfg):
    """Without this, three stacked levels produce three near-identical trades and
    triple the apparent sample size while tripling correlated risk."""
    series = series_with_tail(20, 1.17000, 0.00190, [(1.16560, 1.16600, 1.16420, 1.16540)])
    prices = [1.16500, 1.16470, 1.16450]
    levels = []
    for k, p in enumerate(prices):
        l = one_level(p, Side.SELL_SIDE, series)
        l.id = f"L{k:06d}"
        l.strength = k + 1
        levels.append(l)
    res = run_one(cfg, series, levels)

    assert len(res.confirmed()) == 3, "one event per level (SPEC 9.4)"
    assert len(res.clusters) == 1, "but one cluster"
    cl = res.clusters[0]
    assert cl.side is Side.SELL_SIDE
    assert cl.strength == 1 + 2 + 3
    assert cl.anchor.level_price == pytest.approx(min(prices)), "anchor is the deepest"
    assert cl.sweep_extreme == pytest.approx(1.16420)


def test_opposite_sides_on_one_bar_are_separate_clusters(cfg):
    series = series_with_tail(20, 1.16500, 0.00190, [(1.16500, 1.16700, 1.16300, 1.16500)])
    lo = one_level(1.16400, Side.SELL_SIDE, series)
    hi = one_level(1.16600, Side.BUY_SIDE, series, source=LevelSource.SESSION_HIGH)
    hi.id = "L000002"
    res = run_one(cfg, series, [lo, hi])
    assert len(res.confirmed()) == 2
    assert len(res.clusters) == 2
    assert {c.side for c in res.clusters} == {Side.SELL_SIDE, Side.BUY_SIDE}


# ------------------------------------------------------------- optional filters


def test_wick_ratio_filter_rejects_a_body_close_through():
    c, _ = load_config(overrides={"sweep": {"min_wick_ratio": 0.5}})
    # Opens low, so the lower wick is small relative to range.
    series = series_with_tail(20, 1.17000, 0.00190, [(1.16430, 1.16600, 1.16420, 1.16540)])
    res = run_one(c, series, [one_level(1.16500, Side.SELL_SIDE, series)])
    rej = res.of_type(SweepEventType.REJECTED)
    assert len(rej) == 1 and rej[0].reason is SweepReason.WICK_RATIO


def test_close_position_filter_rejects_a_weak_reclaim():
    c, _ = load_config(overrides={"sweep": {"min_close_position": 0.9}})
    series = series_with_tail(20, 1.17000, 0.00190, [(1.16560, 1.16600, 1.16420, 1.16540)])
    res = run_one(c, series, [one_level(1.16500, Side.SELL_SIDE, series)])
    rej = res.of_type(SweepEventType.REJECTED)
    assert len(rej) == 1 and rej[0].reason is SweepReason.CLOSE_POSITION
    assert rej[0].close_position == pytest.approx(0.6667, abs=1e-4)


def test_reclaim_buffer_requires_clearing_the_level_by_a_margin():
    c, _ = load_config(overrides={"sweep": {"reclaim_buffer_atr": 0.20}})
    # Closes 0.00040 above the level = 0.105 ATR, under the 0.20 buffer.
    series = series_with_tail(20, 1.17000, 0.00190, [(1.16560, 1.16600, 1.16420, 1.16540)])
    res = run_one(c, series, [one_level(1.16500, Side.SELL_SIDE, series)])
    assert not res.confirmed()


# ------------------------------------------------------------------- integration


@pytest.fixture(scope="module")
def run_year(cfg, m15_quarter):
    h4 = resample(m15_quarter, "H4", cfg)
    d1 = resample(m15_quarter, "D1", cfg)
    book, res = analyse_sweeps(
        cfg=cfg,
        h4=h4,
        d1=d1,
        w1=resample(m15_quarter, "W1", cfg),
        mn1=resample(m15_quarter, "MN1", cfg),
        sessions=build_sessions(m15_quarter, cfg),
        h4_structure=analyse_structure(h4, cfg),
        d1_swings=detect_swings(d1, cfg),
    )
    return h4, book, res


def test_a_level_is_swept_at_most_once(cfg, run_year):
    """SPEC 8.9: a level's status is SWEPT only once; re-sweeps need a new level."""
    _, book, res = run_year
    swept = Counter(e.level_id for e in res.confirmed())
    assert swept and max(swept.values()) == 1
    for l in book.levels:
        if l.status is LevelStatus.SWEPT:
            assert l.swept_by is not None


def test_every_confirmed_sweep_has_a_penetration_inside_the_bounds(cfg, run_year):
    _, _, res = run_year
    conf = res.confirmed()
    assert conf
    for e in conf:
        assert cfg.sweep.min_penetration_atr <= e.penetration_atr <= cfg.sweep.max_penetration_atr
        assert e.penetration > 0
        assert 1 <= e.confirmation_bars <= cfg.sweep.max_confirmation_bars


def test_sweep_extreme_is_beyond_the_level_on_the_right_side(cfg, run_year):
    _, _, res = run_year
    for e in res.confirmed():
        if e.side is Side.SELL_SIDE:
            assert e.sweep_extreme < e.level_price
        else:
            assert e.sweep_extreme > e.level_price


def test_confirmation_never_precedes_the_trigger(cfg, run_year):
    _, _, res = run_year
    for e in res.events:
        assert e.confirm_bar >= e.trigger_bar


def test_clusters_partition_the_confirmed_events(cfg, run_year):
    _, _, res = run_year
    clustered = [e.id for c in res.clusters for e in c.events]
    assert sorted(clustered) == sorted(e.id for e in res.confirmed())


def test_sweep_detection_is_prefix_stable(cfg, m15_quarter):
    """SPEC 25.2 applied to this engine."""
    h4_full = resample(m15_quarter, "H4", cfg)
    d1 = resample(m15_quarter, "D1", cfg)
    w1 = resample(m15_quarter, "W1", cfg)
    mn1 = resample(m15_quarter, "MN1", cfg)
    sessions = build_sessions(m15_quarter, cfg)

    def run(n: int):
        h4 = h4_full.head(n)
        _, res = analyse_sweeps(
            cfg=cfg,
            h4=h4,
            d1=d1,
            w1=w1,
            mn1=mn1,
            sessions=sessions,
            h4_structure=analyse_structure(h4, cfg),
            d1_swings=detect_swings(d1, cfg),
        )
        return [
            (e.confirm_bar, e.type.value, e.level_source.value, round(e.sweep_extreme, 8))
            for e in res.events
        ]

    full = run(h4_full.n)
    for k in (int(h4_full.n * 0.5), int(h4_full.n * 0.8)):
        part = run(k)
        assert part == full[: len(part)], k


def test_protected_swing_almost_never_anchors_a_sweep(cfg, run_year):
    """D-006 predicted this before Phase 7 existed, and it must not be misread.

    ``PROTECTED_SWING`` duplicates a ``SWING_*`` level at the identical price 95% of the
    time, so the swing level survives the merge and anchors the sweep instead.  A
    near-zero sweep count here is the merge working, not the source failing.
    """
    _, _, res = run_year
    by_source = Counter(e.level_source.value for e in res.confirmed())
    total = sum(by_source.values())
    assert total > 20
    assert by_source.get("PROTECTED_SWING", 0) / total < 0.02
