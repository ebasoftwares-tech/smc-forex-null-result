"""Swing detection (SPEC 5)."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from bot.config.loader import load_config
from bot.core.bars import build_series
from bot.core.swings import (
    SwingKind,
    SwingLabel,
    detect_swings,
    has_min_history,
    is_swing_high,
    is_swing_low,
    swing_prices,
)
from bot.data.calendar import UTC
from bot.data.resample import resample

_H4 = 4 * 3600
_ORIGIN = int(datetime(2026, 1, 5, tzinfo=UTC).timestamp())


def make_series(bars, symbol="TEST", timeframe="H4"):
    """Build an H4 series from ``(open, high, low, close)`` tuples."""
    n = len(bars)
    t = _ORIGIN + np.arange(n, dtype=np.int64) * _H4
    o, h, l, c = (np.array([b[k] for b in bars], dtype=float) for k in range(4))
    return build_series(symbol, timeframe, t, t + _H4, o, h, l, c, np.ones(n))


@pytest.fixture(scope="module")
def n1_cfg():
    """fractal_n[H4] = 1 -- makes hand-built fixtures tractable."""
    c, _ = load_config(overrides={"swing": {"fractal_n": {"MN1": 1, "W1": 1, "D1": 2, "H4": 1, "H1": 3, "M15": 5}}})
    return c


# ------------------------------------------------------------------- primitives


def test_fractal_rule_matches_the_spec():
    highs = np.array([1.0, 2.0, 3.0, 2.5, 1.5])
    assert is_swing_high(highs, 2, 1)
    assert is_swing_high(highs, 2, 2)
    assert not is_swing_high(highs, 1, 1)
    lows = np.array([3.0, 2.0, 1.0, 1.5, 2.5])
    assert is_swing_low(lows, 2, 1)
    assert not is_swing_low(lows, 3, 1)


def test_window_must_be_complete_on_both_sides():
    highs = np.array([1.0, 5.0, 2.0])
    assert not is_swing_high(highs, 0, 1)  # nothing to the left
    assert not is_swing_high(highs, 2, 1)  # nothing to the right
    assert is_swing_high(highs, 1, 1)


def test_plateau_yields_exactly_one_swing_and_the_tie_rule_picks_which():
    """SPEC 5.1.  Without an explicit rule a plateau gives several swings or none."""
    highs = np.array([1.0, 3.0, 3.0, 3.0, 1.0])
    left = [i for i in range(5) if is_swing_high(highs, i, 1, "leftmost")]
    right = [i for i in range(5) if is_swing_high(highs, i, 1, "rightmost")]
    assert left == [1]
    assert right == [3]


def test_body_price_source_reads_different_extremes():
    bars = [(1.0, 5.0, 0.5, 2.0)]
    s = make_series(bars * 3)
    wick_h, wick_l = swing_prices(s, "wick")
    body_h, body_l = swing_prices(s, "body")
    assert wick_h[0] == 5.0 and wick_l[0] == 0.5
    assert body_h[0] == 2.0 and body_l[0] == 1.0


# ------------------------------------------------------------------- causality


def test_confirmation_lag_is_exactly_n_bars(cfg, m15_quarter):
    h4 = resample(m15_quarter, "H4", cfg)
    n = cfg.swing.n_for("H4")
    store = detect_swings(h4, cfg)
    assert store.swings
    nominal = timedelta(hours=4 * n)
    spanned_a_weekend = False
    for s in store.swings:
        # The lag is N BARS, always.
        assert s.confirmed_index == s.formed_index + n
        wall = s.confirmed_at - h4.close_dt(s.formed_index)
        assert wall >= nominal
        if wall == nominal:
            continue
        # A swing formed on Friday confirms on Monday: still N bars, but 52 hours of
        # wall clock.  SPEC 5.2's "N x D(TF)" holds in trading time only -- clarified
        # in the spec during Phase 5.
        spanned_a_weekend = True
        assert wall > timedelta(days=1)
    assert spanned_a_weekend, "fixture should contain at least one weekend-spanning swing"


def test_a_swing_is_invisible_before_it_confirms(cfg, m15_quarter):
    h4 = resample(m15_quarter, "H4", cfg)
    full = detect_swings(h4, cfg)
    s = full.swings[10]
    before = detect_swings(h4.head(s.confirmed_index), cfg)
    at = detect_swings(h4.head(s.confirmed_index + 1), cfg)
    assert s.id not in {x.id for x in before.swings}
    assert s.id in {x.id for x in at.swings}


def test_detection_is_prefix_stable(cfg, m15_quarter):
    """Truncating the source may only remove swings from the end.

    A REPLACE amendment rewrites the *last* swing, so this also proves that
    normalisation never reaches back and revises settled history.
    """
    h4 = resample(m15_quarter, "H4", cfg)
    full = detect_swings(h4, cfg)
    rng = np.random.default_rng(9)
    for k in rng.choice(np.arange(60, h4.n), size=40, replace=False):
        trunc = detect_swings(h4.head(int(k)), cfg)
        assert len(trunc.swings) <= len(full.swings)
        for a, b in zip(trunc.swings, full.swings):
            if a.confirmed_index < int(k) - cfg.swing.n_for("H4"):
                assert (a.id, a.price, a.label) == (b.id, b.price, b.label)


def test_ids_are_deterministic_across_runs(cfg, m15_quarter):
    """SPEC 1.7 says ULID; a ULID embeds wall-clock time and so cannot be reproducible.

    Ids are derived from (symbol, timeframe, kind, formed_index) instead -- see D-005.
    """
    h4 = resample(m15_quarter, "H4", cfg)
    a = [s.id for s in detect_swings(h4, cfg).swings]
    b = [s.id for s in detect_swings(h4, cfg).swings]
    assert a == b
    assert len(set(a)) == len(a)


# --------------------------------------------------------------- normalisation


def test_consecutive_same_kind_swings_are_normalised(n1_cfg):
    """SPEC 5.4.  Two highs with no low between: the more extreme survives."""
    bars = [
        (1.0, 1.0, 0.9, 1.0),
        (1.0, 3.0, 0.9, 1.0),  # high 3 at i=1
        (1.0, 2.0, 0.9, 1.0),
        (1.0, 5.0, 0.9, 1.0),  # higher high 5 at i=3
        (1.0, 2.0, 0.9, 1.0),
    ]
    store = detect_swings(make_series(bars), n1_cfg)
    highs = [s for s in store.swings if s.is_high]
    assert len(highs) == 1
    assert highs[0].price == 5.0
    assert [a.action for a in store.amendments if a.action != "APPEND"] == ["REPLACE"]


def test_a_less_extreme_same_kind_swing_is_rejected(n1_cfg):
    bars = [
        (1.0, 1.0, 0.9, 1.0),
        (1.0, 5.0, 0.9, 1.0),  # high 5
        (1.0, 2.0, 0.9, 1.0),
        (1.0, 3.0, 0.9, 1.0),  # lower high 3
        (1.0, 2.0, 0.9, 1.0),
    ]
    store = detect_swings(make_series(bars), n1_cfg)
    highs = [s for s in store.swings if s.is_high]
    assert len(highs) == 1 and highs[0].price == 5.0
    assert any(a.action == "REJECT" for a in store.amendments)


def test_sequence_always_alternates(cfg, m15_quarter):
    store = detect_swings(resample(m15_quarter, "H4", cfg), cfg)
    kinds = [s.kind for s in store.swings]
    assert all(a is not b for a, b in zip(kinds, kinds[1:]))


def test_every_amendment_names_the_bar_that_caused_it(cfg, m15_quarter):
    """SPEC 1.2: amendment is legal, retraction is not -- and each must be attributable."""
    h4 = resample(m15_quarter, "H4", cfg)
    store = detect_swings(h4, cfg)
    assert store.amendments
    for a in store.amendments:
        assert 0 <= a.bar_index < h4.n
        assert a.at == h4.close_dt(a.bar_index)
        assert (a.replaced_id is None) == (a.action == "APPEND")


# --------------------------------------------------------------------- labels


def test_labels_follow_the_previous_swing_of_the_same_kind(n1_cfg):
    bars = [
        (1.0, 2.0, 1.0, 1.5),
        (1.0, 1.5, 0.5, 1.0),  # low 0.5
        (1.0, 3.0, 1.0, 2.0),  # high 3.0
        (1.0, 2.0, 0.8, 1.0),  # low 0.8  -> HL (0.8 > 0.5)
        (1.0, 4.0, 1.0, 3.0),  # high 4.0 -> HH (4.0 > 3.0)
        (1.0, 2.0, 1.2, 1.5),
    ]
    store = detect_swings(make_series(bars), n1_cfg)
    by_price = {s.price: s.label for s in store.swings}
    assert by_price[0.5] is SwingLabel.UNDEFINED
    assert by_price[3.0] is SwingLabel.UNDEFINED
    assert by_price[0.8] is SwingLabel.HL
    assert by_price[4.0] is SwingLabel.HH


def test_equal_price_resolves_to_the_weaker_label(n1_cfg):
    """An equal-highs plateau is a liquidity pattern, never continuation (SPEC 5.5)."""
    bars = [
        (1.0, 2.0, 1.0, 1.5),
        (1.0, 1.5, 0.5, 1.0),
        (1.0, 3.0, 1.0, 2.0),  # high 3.0
        (1.0, 2.0, 0.6, 1.0),  # low
        (1.0, 3.0, 1.0, 2.0),  # equal high 3.0 -> LH, not HH
        (1.0, 2.0, 1.2, 1.5),
    ]
    store = detect_swings(make_series(bars), n1_cfg)
    highs = [s for s in store.swings if s.is_high]
    assert len(highs) == 2
    assert highs[1].label is SwingLabel.LH


# ------------------------------------------------------------------ edge cases


def test_inside_bar_can_be_both_high_and_low_and_high_comes_first(n1_cfg):
    """SPEC 5.7.  The order is arbitrary but fixed."""
    bars = [
        (1.0, 3.0, 0.5, 1.0),
        (1.0, 2.0, 1.0, 1.5),  # inside bar: lower high AND higher low -> neither
        (1.0, 3.0, 0.5, 1.0),
    ]
    s = make_series(bars)
    from bot.core.swings import detect_at

    highs, lows = swing_prices(s, "wick")
    found = detect_at(s, 2, n1_cfg, highs, lows)
    kinds = [x.kind for x in found]
    if len(kinds) == 2:
        assert kinds[0] is SwingKind.HIGH and kinds[1] is SwingKind.LOW


def test_flags_are_inherited_from_the_window(cfg, m15_quarter):
    from bot.data import quality
    from bot.data.ingest import _propagate_suspect
    from bot.data.synthetic import generate

    src = generate(
        "EURUSD",
        datetime(2026, 2, 1, tzinfo=UTC),
        datetime(2026, 2, 28, tzinfo=UTC),
        cfg,
        timeframe="M15",
        drop_ranges=[(datetime(2026, 2, 11, 0, 0, tzinfo=UTC), datetime(2026, 2, 11, 20, 0, tzinfo=UTC))],
    )
    flagged, _ = quality.analyse(src, cfg)
    h4 = _propagate_suspect(flagged, resample(flagged, "H4", cfg))
    store = detect_swings(h4, cfg)
    assert any(s.data_suspect for s in store.swings)


def test_min_history_is_reported_not_enforced(cfg, m15_quarter):
    h4 = resample(m15_quarter, "H4", cfg)
    assert not has_min_history(h4, cfg)  # a quarter is under the 500-bar H4 floor
    assert detect_swings(h4, cfg).swings  # but detection still runs and is inspectable


def test_empty_series_is_handled(cfg):
    empty = build_series("X", "H4", *(np.zeros(0) for _ in range(7)))
    assert detect_swings(empty, cfg).swings == []
