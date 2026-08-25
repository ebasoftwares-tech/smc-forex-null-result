"""The standalone forward-return study (SPEC 9.7 / BACKTEST_PROTOCOL H2).

A study that reports "no edge" on everything would pass the random-walk fixture while
being completely broken.  These tests therefore pin **both** directions:

* a **negative control** — random data must show nothing;
* a **positive control** — data with a planted edge must show it.

Without the second, the Phase 7 verdict is worthless.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from bot.core.bars import build_series, from_epoch_s
from bot.core.liquidity import LevelSource, Side
from bot.core.sweeps import SweepEvent, SweepEventType
from bot.research.sweep_study import (
    DEFAULT_HORIZONS,
    forward_returns,
    run_study,
)

H4 = 14400
UTC = timezone.utc


def make_series(closes: np.ndarray, half_range: float = 0.0019) -> "object":
    n = len(closes)
    t = np.arange(n, dtype=np.int64) * H4
    o = np.concatenate(([closes[0]], closes[:-1]))
    h = np.maximum(o, closes) + half_range
    l = np.minimum(o, closes) - half_range
    return build_series("EURUSD", "H4", t, t + H4, o, h, l, closes, np.ones(n))


def mk_event(bar: int, side: Side, series, source=LevelSource.SESSION_LOW) -> SweepEvent:
    return SweepEvent(
        id=f"SW{bar:06d}",
        symbol="EURUSD",
        timeframe="H4",
        type=SweepEventType.CONFIRMED,
        reason=None,
        side=side,
        level_id=f"L{bar}",
        level_source=source,
        level_tier=3,
        level_price=float(series.close[bar]),
        level_strength=1,
        trigger_bar=bar,
        confirm_bar=bar,
        at=from_epoch_s(int(series.close_time[bar])),
        sweep_extreme=float(series.low[bar]),
        sweep_extreme_bar=bar,
        penetration=0.0004,
        penetration_atr=0.2,
        wick_ratio=0.5,
        close_position=0.6,
        confirmation_bars=1,
        single_bar_sweep=True,
    )


def _planted(n: int, drift_atr: float, seed: int, side: Side):
    """A series where price drifts in the sweep's implied direction just after each sweep."""
    rng = np.random.default_rng(seed)
    step = rng.normal(0.0, 0.0004, size=n)
    bars = list(range(60, n - 20, 9))
    sign = 1.0 if side is Side.SELL_SIDE else -1.0
    for b in bars:
        step[b + 1] += sign * drift_atr * 0.0038
    closes = 1.1000 + np.cumsum(step)
    series = make_series(closes)
    return series, [mk_event(b, side, series) for b in bars]


# ------------------------------------------------------------- positive control


def test_a_planted_edge_is_detected(cfg):
    """If the study cannot find an edge that is definitely there, its null result on
    real data means nothing."""
    series, events = _planted(900, drift_atr=0.6, seed=1, side=Side.SELL_SIDE)
    study = run_study(series, events, cfg, bootstrap=2000)

    assert study.n_events == len(events)
    r1 = next(r for r in study.results if r.horizon == 1)
    assert r1.diff > 0.3, r1
    assert r1.ci_low > 0, "the planted edge must be significant at +1"
    assert study.any_significant
    assert "CARRY DIRECTIONAL INFORMATION" in study.verdict()


def test_a_planted_edge_in_the_other_direction_is_also_detected(cfg):
    """A BUY_SIDE sweep implies DOWN; the sign convention must not be inverted."""
    series, events = _planted(900, drift_atr=0.6, seed=2, side=Side.BUY_SIDE)
    study = run_study(series, events, cfg, bootstrap=2000)
    r1 = next(r for r in study.results if r.horizon == 1)
    assert r1.diff > 0.3
    assert r1.ci_low > 0


def test_an_inverted_edge_shows_as_negative(cfg):
    """Price moving *against* the sweep must report a negative difference, not zero."""
    series, events = _planted(900, drift_atr=-0.6, seed=3, side=Side.SELL_SIDE)
    study = run_study(series, events, cfg, bootstrap=2000)
    r1 = next(r for r in study.results if r.horizon == 1)
    assert r1.diff < -0.3
    assert r1.ci_high < 0


def test_the_planted_edge_decays_as_designed(cfg):
    """A one-bar drift must not still be significant twelve bars later."""
    series, events = _planted(900, drift_atr=0.6, seed=4, side=Side.SELL_SIDE)
    study = run_study(series, events, cfg, bootstrap=2000)
    by_h = {r.horizon: r for r in study.results}
    assert by_h[1].diff > 0.3
    # The drift is a single bar, so the *difference* stays roughly constant while the
    # noise around it grows -- significance should weaken, not the estimate.
    assert by_h[12].ci_high - by_h[12].ci_low > by_h[1].ci_high - by_h[1].ci_low


# ------------------------------------------------------------- negative control


def test_pure_noise_shows_nothing(cfg):
    """The random-walk case.  Any 'edge' here would be a bug in the study."""
    rng = np.random.default_rng(11)
    closes = 1.1000 + np.cumsum(rng.normal(0.0, 0.0004, size=900))
    series = make_series(closes)
    events = [mk_event(b, Side.SELL_SIDE, series) for b in range(60, 880, 9)]
    study = run_study(series, events, cfg, bootstrap=2000)
    assert not study.any_significant, [(r.horizon, r.diff, r.ci_low, r.ci_high) for r in study.results]
    assert "NO MEASURABLE DIRECTIONAL EDGE" in study.verdict()


# -------------------------------------------------------------------- mechanics


def test_forward_returns_are_atr_normalised_and_direction_signed(cfg):
    closes = np.linspace(1.1000, 1.1100, 200)
    series = make_series(closes)
    from bot.core.indicators import atr_ref

    atr = atr_ref(series, cfg.atr.period)
    up = forward_returns(series, [100], [1], 5, atr)
    down = forward_returns(series, [100], [-1], 5, atr)
    assert up[0] > 0 and down[0] < 0
    assert up[0] == pytest.approx(-down[0])
    expected = (closes[105] - closes[100]) / atr[100]
    assert up[0] == pytest.approx(expected)


def test_events_too_close_to_the_end_are_dropped_not_clamped(cfg):
    closes = 1.1000 + np.cumsum(np.random.default_rng(5).normal(0, 0.0004, 300))
    series = make_series(closes)
    events = [mk_event(b, Side.SELL_SIDE, series) for b in (100, 295, 298)]
    study = run_study(series, events, cfg, bootstrap=500)
    r12 = next(r for r in study.results if r.horizon == 12)
    assert r12.n_sweep == 1, "only the bar with 12 bars ahead of it survives"


def test_controls_are_matched_on_hour_and_volatility(cfg):
    """Sweeps are not uniformly distributed across the session or across volatility, so
    an unmatched control is a comparison against a different population."""
    rng = np.random.default_rng(7)
    closes = 1.1000 + np.cumsum(rng.normal(0.0, 0.0004, size=900))
    series = make_series(closes)
    events = [mk_event(b, Side.SELL_SIDE, series) for b in range(60, 880, 9)]
    study = run_study(series, events, cfg, bootstrap=500)
    assert study.results
    for r in study.results:
        assert r.n_control > 0
        # One control per event, minus any dropped for lack of forward bars.
        assert abs(r.n_control - r.n_sweep) <= 3


def test_no_events_yields_an_empty_study(cfg):
    closes = 1.1000 + np.zeros(100)
    study = run_study(make_series(closes), [], cfg)
    assert study.n_events == 0
    assert study.results == []
    assert study.verdict() == "NO DATA"


def test_study_is_deterministic_for_a_fixed_seed(cfg):
    series, events = _planted(600, drift_atr=0.4, seed=9, side=Side.SELL_SIDE)
    a = run_study(series, events, cfg, bootstrap=800, seed=123)
    b = run_study(series, events, cfg, bootstrap=800, seed=123)
    assert [(r.diff, r.ci_low, r.ci_high) for r in a.results] == [
        (r.diff, r.ci_low, r.ci_high) for r in b.results
    ]


def test_per_source_breakdown_covers_every_source_present(cfg):
    """D-006: SESSION levels are 61% of the population, so an aggregate mean can hide a
    source that does carry information."""
    series, events = _planted(600, drift_atr=0.4, seed=13, side=Side.SELL_SIDE)
    for k, e in enumerate(events):
        if k % 2:
            events[k] = mk_event(e.confirm_bar, e.side, series, source=LevelSource.PREV_DAY_LOW)
    study = run_study(series, events, cfg, bootstrap=500)
    assert set(study.by_source) == {"SESSION_LOW", "PREV_DAY_LOW"}
    for src, per_h in study.by_source.items():
        assert set(per_h) == set(DEFAULT_HORIZONS)
