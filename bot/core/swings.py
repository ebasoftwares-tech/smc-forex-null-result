"""Swing detection (SPEC 5).

Everything structural in this system is built on these two objects, so their
determinism and their confirmation lag propagate everywhere.

The rule that shapes the module: **a swing formed at bar ``i`` is not knowable until
bar ``i + N`` closes.**  With ``fractal_n[H4] = 2`` an H4 swing low is known eight
hours after the bar that made it.  Every downstream rule that "waits for a swing"
inherits that delay.  It is the price of not repainting.

Object ids are derived deterministically from ``(symbol, timeframe, kind,
formed_index)`` rather than being ULIDs as SPEC 1.7 says.  A ULID embeds wall-clock
time, so a ULID-keyed event log cannot be byte-identical across two runs of the same
data -- which SPEC 25.5 requires.  Recorded as a spec correction in D-005.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Literal

import numpy as np

from bot.config.schema import AppConfig
from bot.core.bars import BarSeries, from_epoch_s
from bot.core.ids import object_id


class SwingKind(str, Enum):
    HIGH = "HIGH"
    LOW = "LOW"


class SwingLabel(str, Enum):
    HH = "HH"
    HL = "HL"
    LH = "LH"
    LL = "LL"
    UNDEFINED = "UNDEFINED"


@dataclass(frozen=True)
class Swing:
    id: str
    symbol: str
    timeframe: str
    kind: SwingKind
    price: float
    formed_index: int
    confirmed_index: int
    formed_at: datetime
    confirmed_at: datetime
    label: SwingLabel = SwingLabel.UNDEFINED
    spans_gap: bool = False
    data_suspect: bool = False

    @property
    def is_high(self) -> bool:
        return self.kind is SwingKind.HIGH


@dataclass(frozen=True)
class SwingSpan:
    """A swing plus the half-open bar range ``[visible_from, visible_until)`` over which
    it was the live swing of its kind.

    The store keeps only *surviving* swings: SPEC 5.4 normalisation REPLACEs a swing
    when a more extreme same-kind swing confirms with no opposite swing between them,
    and the superseded object is gone from ``swings`` afterwards.  Reading the finished
    store to ask "which swings existed at bar i" therefore answers with **less** than a
    live engine had -- a superseded swing vanishes retroactively from every earlier bar
    too.

    That is safe (it can never invent information) but it is not faithful, and SPEC 11.1
    selects the CHoCH reference from exactly this set as it stood at the sweep bar.  The
    spans make the historical query exact instead of merely conservative.
    """

    swing: Swing
    visible_from: int
    visible_until: int | None = None

    def visible_at(self, bar_index: int) -> bool:
        return self.visible_from <= bar_index and (
            self.visible_until is None or bar_index < self.visible_until
        )


@dataclass(frozen=True)
class SwingAmendment:
    """A revision to the swing *labelling*, never to an emitted signal.

    SPEC 1.2 consequence 3 permits amendment and forbids retraction.  Each record
    names the bar close that caused it, so the replay test can verify the amendment
    *sequence* rather than only the final state.
    """

    at: datetime
    bar_index: int
    action: Literal["APPEND", "REPLACE", "REJECT"]
    swing_id: str
    replaced_id: str | None
    reason: str


def swing_prices(series: BarSeries, price_source: str) -> tuple[np.ndarray, np.ndarray]:
    """The (high, low) arrays swing detection compares.

    ``wick`` is the default and the SMC convention.  ``body`` exists because a
    non-trivial part of the literature reads structure on closes; the two produce
    materially different structure and the difference is an ablation, not a detail.
    """
    if price_source == "wick":
        return series.high, series.low
    if price_source == "body":
        return np.maximum(series.open, series.close), np.minimum(series.open, series.close)
    raise ValueError(f"unknown swing.price_source {price_source!r}")


def is_swing_high(highs: np.ndarray, i: int, n: int, tie_rule: str = "leftmost") -> bool:
    """SPEC 5.1.

    The asymmetry -- strict on one side, non-strict on the other -- is the tie-break.
    Without it a plateau of equal highs produces either several adjacent swings or
    none, depending on the operators, which is a silent data-dependent inconsistency.
    """
    if i < n or i + n >= len(highs):
        return False
    left, right, p = highs[i - n : i], highs[i + 1 : i + n + 1], highs[i]
    if tie_rule == "leftmost":
        return bool(np.all(left < p) and np.all(right <= p))
    return bool(np.all(left <= p) and np.all(right < p))


def is_swing_low(lows: np.ndarray, i: int, n: int, tie_rule: str = "leftmost") -> bool:
    if i < n or i + n >= len(lows):
        return False
    left, right, p = lows[i - n : i], lows[i + 1 : i + n + 1], lows[i]
    if tie_rule == "leftmost":
        return bool(np.all(left > p) and np.all(right >= p))
    return bool(np.all(left >= p) and np.all(right > p))


class SwingStore:
    """The normalised, strictly alternating swing sequence.

    Raw fractals can produce two consecutive highs with no intervening low.  Structure
    rules require alternation, so the store normalises **in confirmation order**
    (SPEC 5.4): when a same-kind swing confirms, the more extreme of the two survives
    and the other is dropped.  Both outcomes are recorded as amendments.
    """

    def __init__(self, symbol: str, timeframe: str) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.swings: list[Swing] = []
        self.amendments: list[SwingAmendment] = []
        self.history: list[SwingSpan] = []

    # ------------------------------------------------------------------ accessors

    @property
    def last(self) -> Swing | None:
        return self.swings[-1] if self.swings else None

    def last_of(self, kind: SwingKind) -> Swing | None:
        for s in reversed(self.swings):
            if s.kind is kind:
                return s
        return None

    def confirmed_by(self, bar_index: int, kind: SwingKind | None = None) -> list[Swing]:
        return [
            s
            for s in self.swings
            if s.confirmed_index <= bar_index and (kind is None or s.kind is kind)
        ]

    def last_confirmed_by(self, bar_index: int, kind: SwingKind) -> Swing | None:
        for s in reversed(self.swings):
            if s.kind is kind and s.confirmed_index <= bar_index:
                return s
        return None

    def _close_span(self, swing_id: str, bar_index: int) -> None:
        for k in range(len(self.history) - 1, -1, -1):
            sp = self.history[k]
            if sp.swing.id == swing_id and sp.visible_until is None:
                self.history[k] = replace(sp, visible_until=bar_index)
                return

    def visible_at(self, bar_index: int, kind: SwingKind | None = None) -> list[Swing]:
        """The swings a live engine held at the close of ``bar_index``, oldest first.

        Confirmation is already implied -- a swing enters the store on the bar that
        confirms it -- so no separate ``confirmed_index`` filter is needed here.
        """
        return [
            sp.swing
            for sp in self.history
            if sp.visible_at(bar_index) and (kind is None or sp.swing.kind is kind)
        ]

    def counts(self) -> dict[str, int]:
        return {
            "HIGH": sum(1 for s in self.swings if s.is_high),
            "LOW": sum(1 for s in self.swings if not s.is_high),
        }

    # --------------------------------------------------------------- normalisation

    def add(self, s: Swing, bar_index: int, at: datetime) -> None:
        last = self.last
        if last is None or last.kind is not s.kind:
            self.swings.append(s)
            self._relabel_last()
            self.history.append(SwingSpan(self.swings[-1], bar_index))
            self.amendments.append(
                SwingAmendment(at, bar_index, "APPEND", s.id, None, "alternating")
            )
            return

        more_extreme = (s.price > last.price) if s.is_high else (s.price < last.price)
        if more_extreme:
            self.swings[-1] = s
            self._relabel_last()
            self._close_span(last.id, bar_index)
            self.history.append(SwingSpan(self.swings[-1], bar_index))
            self.amendments.append(
                SwingAmendment(at, bar_index, "REPLACE", s.id, last.id, "more extreme, same kind")
            )
        else:
            # Ties keep the earlier swing, consistent with tie_rule = leftmost.
            self.amendments.append(
                SwingAmendment(at, bar_index, "REJECT", s.id, last.id, "less extreme, same kind")
            )

    def _relabel_last(self) -> None:
        """SPEC 5.5.  Ties resolve to the weaker label.

        An equal-highs plateau is a liquidity pattern (SPEC 8.5), not continuation, so
        an equal high is LH rather than HH.
        """
        idx = len(self.swings) - 1
        s = self.swings[idx]
        prev = None
        for j in range(idx - 1, -1, -1):
            if self.swings[j].kind is s.kind:
                prev = self.swings[j]
                break
        if prev is None:
            label = SwingLabel.UNDEFINED
        elif s.is_high:
            label = SwingLabel.HH if s.price > prev.price else SwingLabel.LH
        else:
            label = SwingLabel.HL if s.price > prev.price else SwingLabel.LL
        self.swings[idx] = replace(s, label=label)


def detect_at(
    series: BarSeries,
    bar_index: int,
    cfg: AppConfig,
    highs: np.ndarray,
    lows: np.ndarray,
) -> list[Swing]:
    """Swings that become *knowable* at the close of ``bar_index``.

    The candidate is ``bar_index - N``.  Every bar the test reads lies in
    ``[bar_index - 2N, bar_index]``, so the result depends on no future bar.

    When one bar is both a swing high and a swing low -- an inside-bar cluster -- both
    are returned, HIGH first.  The order is arbitrary but fixed, and pinned by test.
    """
    n = cfg.swing.n_for(series.timeframe)
    c = bar_index - n
    if c < n:
        return []

    out: list[Swing] = []
    window = slice(max(0, c - n), min(series.n, c + n + 1))
    spans_gap = bool(series.flag("spans_gap")[window].any())
    suspect = bool(series.flag("data_suspect")[window].any())
    formed_at = from_epoch_s(series.open_time[c])
    confirmed_at = from_epoch_s(series.close_time[bar_index])

    for kind, arr, hit in (
        (SwingKind.HIGH, highs, is_swing_high(highs, c, n, cfg.swing.tie_rule)),
        (SwingKind.LOW, lows, is_swing_low(lows, c, n, cfg.swing.tie_rule)),
    ):
        if not hit:
            continue
        out.append(
            Swing(
                id=object_id(
                    f"{kind.value[0]}S",
                    symbol=series.symbol,
                    timeframe=series.timeframe,
                    at=confirmed_at,
                    key=(int(series.open_time[c]), float(arr[c])),
                ),
                symbol=series.symbol,
                timeframe=series.timeframe,
                kind=kind,
                price=float(arr[c]),
                formed_index=c,
                confirmed_index=bar_index,
                formed_at=formed_at,
                confirmed_at=confirmed_at,
                spans_gap=spans_gap,
                data_suspect=suspect,
            )
        )
    return out


def detect_swings(series: BarSeries, cfg: AppConfig) -> SwingStore:
    """Batch driver: process every bar close in order and return the normalised store.

    Strictly left-to-right, and each step reads only bars at or before the current
    one, so the batch result is identical to what an incremental live engine would
    have produced.  ``tests/test_swings.py`` asserts that equivalence directly.
    """
    store = SwingStore(series.symbol, series.timeframe)
    if series.n == 0:
        return store
    highs, lows = swing_prices(series, cfg.swing.price_source)
    for i in range(series.n):
        for s in detect_at(series, i, cfg, highs, lows):
            store.add(s, i, from_epoch_s(series.close_time[i]))
    return store


def has_min_history(series: BarSeries, cfg: AppConfig) -> bool:
    """SPEC 5.3.  Whether this timeframe has enough bars for its structure to be usable."""
    return series.n >= cfg.swing.min_history.get(series.timeframe, 0)
