"""Liquidity engine (SPEC 8).

A liquidity level is **a price at which resting stop orders are inferred to sit,
identified by a rule that could have been applied at the time the level formed**
(SPEC 8.1).  No level here is identified by how it looks; every one traces to an
enumerated source with a formation rule and a formation timestamp.

Two rules shape the whole module:

* **SPEC 8.4, the running-extreme prohibition.**  A level is never created from a
  FORMING period or session.  The current day's high is not liquidity; the *previous*
  day's high is.  A level still being made cannot be swept, and code that allows it
  reports a "sweep" whenever price pulls back from a new high -- which is most bars,
  and which fabricates the strategy's central event out of nothing.
* **SPEC 8.7, swept versus accepted-through.**  A level price simply trades past is
  INVALIDATED, not left ACTIVE forever to produce a spurious sweep months later.

Sweep detection is Phase 7.  This engine owns creation, merging, ageing, invalidation,
pruning and ranking; ``LevelStatus.SWEPT`` and ``CONSUMED`` exist in the lifecycle but
are only ever set by the phases that follow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Iterable, Sequence

import numpy as np

from bot.config.schema import AppConfig
from bot.core.bars import BarSeries, from_epoch_s
from bot.core.ids import object_id
from bot.core.indicators import atr_ref
from bot.core.sessions import SessionInstance, SessionStatus
from bot.core.structure import ProtectedChange, StructureResult
from bot.core.swings import Swing, SwingKind, SwingStore


class Side(str, Enum):
    """Which side of price the resting orders sit on.

    BUY_SIDE is *above* price: stops of shorts, and breakout buy orders.  Deliberately
    a different enum from ``structure.Side`` (BULLISH/BEARISH) -- a bullish setup
    sweeps SELL_SIDE liquidity, so conflating the two would invert every setup.
    """

    BUY_SIDE = "BUY_SIDE"
    SELL_SIDE = "SELL_SIDE"


class LevelSource(str, Enum):
    PREV_DAY_HIGH = "PREV_DAY_HIGH"
    PREV_DAY_LOW = "PREV_DAY_LOW"
    PREV_WEEK_HIGH = "PREV_WEEK_HIGH"
    PREV_WEEK_LOW = "PREV_WEEK_LOW"
    PREV_MONTH_HIGH = "PREV_MONTH_HIGH"
    PREV_MONTH_LOW = "PREV_MONTH_LOW"
    SESSION_HIGH = "SESSION_HIGH"
    SESSION_LOW = "SESSION_LOW"
    SWING_HIGH = "SWING_HIGH"
    SWING_LOW = "SWING_LOW"
    EQUAL_HIGHS = "EQUAL_HIGHS"
    EQUAL_LOWS = "EQUAL_LOWS"
    PROTECTED_SWING = "PROTECTED_SWING"
    RANGE_HIGH = "RANGE_HIGH"
    RANGE_LOW = "RANGE_LOW"

    @property
    def family(self) -> str:
        """The ``liq.enabled_sources`` switch this source belongs to."""
        return _FAMILY[self]


_FAMILY: dict[LevelSource, str] = {
    LevelSource.PREV_DAY_HIGH: "PREV_DAY",
    LevelSource.PREV_DAY_LOW: "PREV_DAY",
    LevelSource.PREV_WEEK_HIGH: "PREV_WEEK",
    LevelSource.PREV_WEEK_LOW: "PREV_WEEK",
    LevelSource.PREV_MONTH_HIGH: "PREV_MONTH",
    LevelSource.PREV_MONTH_LOW: "PREV_MONTH",
    LevelSource.SESSION_HIGH: "SESSION",
    LevelSource.SESSION_LOW: "SESSION",
    LevelSource.SWING_HIGH: "SWING",
    LevelSource.SWING_LOW: "SWING",
    LevelSource.EQUAL_HIGHS: "EQUAL",
    LevelSource.EQUAL_LOWS: "EQUAL",
    LevelSource.PROTECTED_SWING: "PROTECTED_SWING",
    LevelSource.RANGE_HIGH: "RANGE",
    LevelSource.RANGE_LOW: "RANGE",
}


class LevelStatus(str, Enum):
    """SPEC 8.7, plus the two terminal states SPEC 8.8/8.9 imply.

    ``MERGED`` and ``PRUNED`` are not in the 8.7 list but are required by 8.8 (two
    levels become one) and 8.9 (levels beyond the cap are dropped and never return).
    They are recorded as distinct statuses rather than deletions so that the population
    report of 8.10 can account for every level ever created.
    """

    ACTIVE = "ACTIVE"
    SWEPT = "SWEPT"  # set by Phase 7
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    CONSUMED = "CONSUMED"  # set by Phase 14 when a setup terminates
    MERGED = "MERGED"
    PRUNED = "PRUNED"


_TERMINAL = {
    LevelStatus.SWEPT,
    LevelStatus.INVALIDATED,
    LevelStatus.EXPIRED,
    LevelStatus.CONSUMED,
    LevelStatus.MERGED,
    LevelStatus.PRUNED,
}


def tier_for(source: LevelSource, timeframe: str) -> int:
    """SPEC 8.6.  Tier drives ranking and expiry; under D-002 it no longer drives the
    confirmation timeframe, which is H4 for every tier."""
    if source in (
        LevelSource.PREV_MONTH_HIGH,
        LevelSource.PREV_MONTH_LOW,
        LevelSource.PREV_WEEK_HIGH,
        LevelSource.PREV_WEEK_LOW,
        LevelSource.EQUAL_HIGHS,
        LevelSource.EQUAL_LOWS,
    ):
        return 1
    if source in (
        LevelSource.SWING_HIGH,
        LevelSource.SWING_LOW,
        LevelSource.PROTECTED_SWING,
    ):
        return 1 if timeframe == "D1" else 2
    if source in (LevelSource.PREV_DAY_HIGH, LevelSource.PREV_DAY_LOW):
        return 2
    return 3


#: Merge precedence within one tier and one confirmation bar (SPEC 8.8, D-015 section 2).
#: Lower wins. The principle is that a **primary** structural object beats one that
#: annotates it or is derived from it, so a merge never dissolves the thing the derived
#: level was describing.
_SOURCE_PRECEDENCE: dict[LevelSource, int] = {
    LevelSource.PREV_MONTH_HIGH: 0,
    LevelSource.PREV_MONTH_LOW: 0,
    LevelSource.PREV_WEEK_HIGH: 1,
    LevelSource.PREV_WEEK_LOW: 1,
    LevelSource.PREV_DAY_HIGH: 2,
    LevelSource.PREV_DAY_LOW: 2,
    LevelSource.SESSION_HIGH: 3,
    LevelSource.SESSION_LOW: 3,
    LevelSource.SWING_HIGH: 4,
    LevelSource.SWING_LOW: 4,
    # Derived from swings: a cluster of them, and a window over them.
    LevelSource.EQUAL_HIGHS: 5,
    LevelSource.EQUAL_LOWS: 5,
    LevelSource.RANGE_HIGH: 6,
    LevelSource.RANGE_LOW: 6,
    # An annotation on a swing, never a level in its own right (D-006).
    LevelSource.PROTECTED_SWING: 7,
}


def _source_precedence(source: LevelSource) -> int:
    return _SOURCE_PRECEDENCE.get(source, 99)


@dataclass
class LiquidityLevel:
    """SPEC 8.2.  Mutable: status, strength and price all change over a level's life."""

    id: str
    symbol: str
    side: Side
    source: LevelSource
    timeframe: str
    tier: int
    price: float
    formed_at: datetime
    confirmed_at: datetime
    strength: int = 1
    status: LevelStatus = LevelStatus.ACTIVE
    source_ids: list[str] = field(default_factory=list)
    swept_by: str | None = None
    # Set when this level is absorbed by another (SPEC 8.8).  The sweep engine follows
    # the chain so that an open sweep window survives its level being merged away --
    # with ~65% of levels merging, dropping those windows would lose real sweeps.
    merged_into: str | None = None
    formed_in_gap: bool = False
    formed_in_data_suspect: bool = False
    # Bookkeeping, filled by the engine as bars close.
    confirmed_bar: int = -1
    confirmed_d1: int = 0
    age_d1: int = 0
    terminal_at: datetime | None = None
    terminal_bar: int = -1
    beyond_closes: int = 0
    penetrated: bool = False

    @property
    def is_active(self) -> bool:
        return self.status is LevelStatus.ACTIVE

    def __repr__(self) -> str:  # pragma: no cover - diagnostics
        return (
            f"<{self.source.value} {self.side.value} {self.price:.5f} "
            f"t{self.tier} s{self.strength} {self.status.value}>"
        )


# --------------------------------------------------------------------- candidates


def _mk(
    seq: int,
    symbol: str,
    side: Side,
    source: LevelSource,
    timeframe: str,
    price: float,
    formed_at: datetime,
    confirmed_at: datetime,
    *,
    strength: int = 1,
    source_ids: Sequence[str] = (),
    formed_in_gap: bool = False,
    formed_in_data_suspect: bool = False,
) -> LiquidityLevel:
    return LiquidityLevel(
        id=object_id(
            "LV",
            symbol=symbol,
            timeframe=timeframe,
            at=confirmed_at,
            # Natural key: what makes two levels the same level.  **``seq`` is
            # deliberately absent.** It counts candidates built so far, which
            # depends on how much history the run was given -- so putting it in the
            # key made a level's id change under truncation and broke the SPEC 25.2
            # prefix-stability test, since admission order tie-breaks on the id.
            # Two levels sharing all four of these are the same level, and SPEC 8.8
            # would merge them anyway.
            key=(source.value, side.value, price, formed_at),
        ),
        symbol=symbol,
        side=side,
        source=source,
        timeframe=timeframe,
        tier=tier_for(source, timeframe),
        price=float(price),
        formed_at=formed_at,
        confirmed_at=confirmed_at,
        strength=strength,
        source_ids=list(source_ids),
        formed_in_gap=formed_in_gap,
        formed_in_data_suspect=formed_in_data_suspect,
    )


def period_levels(
    series: BarSeries, source_high: LevelSource, source_low: LevelSource, start_seq: int
) -> list[LiquidityLevel]:
    """PREV_DAY / PREV_WEEK / PREV_MONTH levels (SPEC 8.3 sources 1-3).

    A period's extremes become liquidity at the moment that period **closes**, which is
    exactly the running-extreme prohibition of SPEC 8.4: the resampler only emits a
    higher-timeframe bar once its end instant has passed, so a FORMING period has no
    bar here to read.
    """
    out: list[LiquidityLevel] = []
    seq = start_seq
    for i in range(series.n):
        formed = from_epoch_s(series.open_time[i])
        confirmed = from_epoch_s(series.close_time[i])
        gap = bool(series.flag("spans_gap")[i] or series.flag("merged_stub")[i])
        susp = bool(series.flag("data_suspect")[i])
        for side, src, price in (
            (Side.BUY_SIDE, source_high, series.high[i]),
            (Side.SELL_SIDE, source_low, series.low[i]),
        ):
            out.append(
                _mk(
                    seq,
                    series.symbol,
                    side,
                    src,
                    series.timeframe,
                    price,
                    formed,
                    confirmed,
                    formed_in_gap=gap,
                    formed_in_data_suspect=susp,
                )
            )
            seq += 1
    return out


def liquidity_session_names(cfg: AppConfig) -> set[str]:
    """Sessions whose extremes are treated as pools of resting orders.

    Driven by the configured ``role``, which excludes two kinds of window that would
    otherwise inflate the population:

    * **OVERLAP** is derived (London n New York) and is not a configured window at all.
      Its extremes are a *sub-window* of two sessions already counted -- measured on the
      fixture, an overlap extreme coincides exactly with the London or New York extreme
      on 90% of days -- so it is not an independent inference about resting orders.
    * **Killzones** are execution windows, not liquidity pools.

    See D-006.
    """
    return {w.name for w in cfg.session.windows if w.enabled and "liquidity" in w.role}


def session_levels(
    sessions: Iterable[SessionInstance],
    start_seq: int,
    liquidity_names: set[str] | None = None,
) -> list[LiquidityLevel]:
    """SESSION_HIGH / SESSION_LOW (SPEC 8.3 source 4).

    Only ``CLOSED`` instances of a liquidity-role session contribute.  A FORMING
    session's running extreme is drawn on the chart but is never liquidity (SPEC 3.5),
    and an INCOMPLETE one -- a half-holiday or an outage, detected from bar coverage
    rather than a hard-coded calendar -- is excluded because its extremes are not the
    session's.

    **``PREV_SESSION_EXTREME`` (SPEC 8.3 source 9) is deliberately folded into this
    source rather than implemented separately.**  A tier-3 level lives for 5 D1 bars
    (SPEC 8.7), so yesterday's Asian high is still an ACTIVE ``SESSION_HIGH``; emitting
    it a second time under another name would double-count every level and every sweep
    of it.  See D-006.
    """
    out: list[LiquidityLevel] = []
    seq = start_seq
    for s in sessions:
        if s.status is not SessionStatus.CLOSED:
            continue
        if liquidity_names is not None and s.session_name not in liquidity_names:
            continue
        for side, src, price, ts in (
            (Side.BUY_SIDE, LevelSource.SESSION_HIGH, s.high, s.high_ts),
            (Side.SELL_SIDE, LevelSource.SESSION_LOW, s.low, s.low_ts),
        ):
            out.append(
                _mk(
                    seq,
                    s.symbol,
                    side,
                    src,
                    s.source_tf,
                    price,
                    ts,
                    s.end_utc,
                    source_ids=[f"{s.session_name}:{s.trading_date.isoformat()}"],
                )
            )
            seq += 1
    return out


def swing_levels(store: SwingStore, timeframe: str, start_seq: int) -> list[LiquidityLevel]:
    """SWING_HIGH / SWING_LOW (SPEC 8.3 source 5), confirmed at the swing's own
    ``confirmed_at`` -- N bars after the bar it describes, never before."""
    out: list[LiquidityLevel] = []
    seq = start_seq
    for s in store.swings:
        side = Side.BUY_SIDE if s.is_high else Side.SELL_SIDE
        src = LevelSource.SWING_HIGH if s.is_high else LevelSource.SWING_LOW
        out.append(
            _mk(
                seq,
                s.symbol,
                side,
                src,
                timeframe,
                s.price,
                s.formed_at,
                s.confirmed_at,
                source_ids=[s.id],
                formed_in_gap=s.spans_gap,
                formed_in_data_suspect=s.data_suspect,
            )
        )
        seq += 1
    return out


def protected_levels(
    changes: Iterable[ProtectedChange], timeframe: str, start_seq: int
) -> list[LiquidityLevel]:
    """PROTECTED_SWING (SPEC 8.3 source 7).

    Arguably the highest-quality level in the model: it is defined by the structure the
    market is currently trading, and SPEC 6.4 already treats a wick through it as an
    ``INTERNAL_LIQUIDITY_GRAB``.
    """
    out: list[LiquidityLevel] = []
    seq = start_seq
    for ch in changes:
        s = ch.swing
        side = Side.SELL_SIDE if s.kind is SwingKind.LOW else Side.BUY_SIDE
        out.append(
            _mk(
                seq,
                s.symbol,
                side,
                LevelSource.PROTECTED_SWING,
                timeframe,
                s.price,
                s.formed_at,
                ch.at,
                source_ids=[s.id],
            )
        )
        seq += 1
    return out


def _equal_cluster(
    anchor: Swing, prior: list[Swing], tol: float, cfg: AppConfig
) -> list[Swing] | None:
    """The maximal equal-price cluster containing ``anchor`` (SPEC 8.5.1).

    Three filters, in order: span in bars, pairwise price tolerance, and minimum
    separation.  The pairwise check is done as a contiguous window over sorted prices
    rather than "within tolerance of the anchor", because the latter is not transitive
    -- two members each within ``tol`` of the anchor can be ``2*tol`` apart, which is
    not an equal-highs cluster by any reading.
    """
    window = [
        s
        for s in prior
        if 0 < anchor.formed_index - s.formed_index <= cfg.eq.max_span_bars
    ]
    if not window:
        return None

    cand = sorted(window + [anchor], key=lambda s: s.price)
    ai = next(i for i, s in enumerate(cand) if s.id == anchor.id)
    lo = ai
    while lo > 0 and cand[ai].price - cand[lo - 1].price <= tol:
        lo -= 1
    hi = ai
    while hi + 1 < len(cand) and cand[hi + 1].price - cand[lo].price <= tol:
        hi += 1
    members = sorted(cand[lo : hi + 1], key=lambda s: s.formed_index)

    # Minimum separation: adjacent extremes on near-adjacent bars are one extreme, not
    # two touches.  Greedy from the oldest keeps the earliest cluster start stable, so
    # a growing cluster keeps its identity.
    kept: list[Swing] = []
    for s in members:
        if not kept or s.formed_index - kept[-1].formed_index >= cfg.eq.min_separation_bars:
            kept.append(s)
    if anchor.id not in {s.id for s in kept}:
        return None
    if len(kept) < cfg.eq.min_touches:
        return None
    return kept


def equal_levels(
    store: SwingStore, series: BarSeries, cfg: AppConfig, start_seq: int
) -> list[LiquidityLevel]:
    """EQUAL_HIGHS / EQUAL_LOWS (SPEC 8.3 source 6, SPEC 8.5.1).

    One level per cluster, identified by its **earliest** member, so a cluster that
    grows from two touches to three amends its strength and price rather than emitting
    a second level for the same shelf of stops.
    """
    atr = atr_ref(series, cfg.atr.period)
    out: dict[str, LiquidityLevel] = {}
    order: list[str] = []
    seq = start_seq

    for kind, src, side in (
        (SwingKind.HIGH, LevelSource.EQUAL_HIGHS, Side.BUY_SIDE),
        (SwingKind.LOW, LevelSource.EQUAL_LOWS, Side.SELL_SIDE),
    ):
        same = [s for s in store.swings if s.kind is kind]
        for idx, anchor in enumerate(same):
            a = atr[anchor.confirmed_index] if anchor.confirmed_index < len(atr) else np.nan
            if not np.isfinite(a) or a <= 0:
                continue
            tol = cfg.eq.tolerance_atr * float(a)
            cluster = _equal_cluster(anchor, same[:idx], tol, cfg)
            if cluster is None:
                continue
            prices = [s.price for s in cluster]
            if cfg.eq.cluster_price == "extreme":
                price = max(prices) if kind is SwingKind.HIGH else min(prices)
            else:
                price = float(np.mean(prices))
            key = f"{src.value}:{cluster[0].id}"
            if key in out:
                # Growth AMENDS an existing level; it must never move its confirmation
                # later.  The shelf became knowable when the cluster first reached
                # `min_touches`; a third touch arriving twenty bars afterwards does not
                # un-know it.  Re-stamping `confirmed_at` here made the level's
                # admission time depend on swings that had not happened yet -- caught by
                # the prefix-stability test, which is precisely the shape of leak that
                # test exists for.  SPEC 1.2 permits amending a label, never retracting
                # or re-dating a signal.
                lvl = out[key]
                lvl.price = float(price)
                lvl.strength = len(cluster)
                lvl.source_ids = [s.id for s in cluster]
            else:
                lvl = _mk(
                    seq,
                    anchor.symbol,
                    side,
                    src,
                    series.timeframe,
                    price,
                    cluster[0].formed_at,
                    anchor.confirmed_at,
                    strength=len(cluster),
                    source_ids=[s.id for s in cluster],
                )
                seq += 1
                out[key] = lvl
                order.append(key)
    return [out[k] for k in order]


def range_levels(series: BarSeries, cfg: AppConfig, start_seq: int) -> list[LiquidityLevel]:
    """RANGE_HIGH / RANGE_LOW (SPEC 8.3 source 8, SPEC 8.5.2).

    Emitted on the **rising edge** only: a twenty-bar consolidation would otherwise
    produce a fresh pair of levels on every bar it persists, which would swamp the
    population report and every statistic keyed on level counts.
    """
    out: list[LiquidityLevel] = []
    seq = start_seq
    w = cfg.range.window_bars
    atr = atr_ref(series, cfg.atr.period)
    prev_ok = False
    for i in range(w - 1, series.n):
        lo_i = i - w + 1
        hi = float(series.high[lo_i : i + 1].max())
        lo = float(series.low[lo_i : i + 1].min())
        a = atr[i]
        ok = False
        if np.isfinite(a) and a > 0 and (hi - lo) <= cfg.range.max_height_atr * float(a):
            mid_lo = lo + 0.25 * (hi - lo)
            mid_hi = lo + 0.75 * (hi - lo)
            closes = series.close[lo_i : i + 1]
            outside = int(np.count_nonzero((closes < mid_lo) | (closes > mid_hi)))
            ok = outside <= cfg.range.max_breakout_bars
        if ok and not prev_ok:
            confirmed = from_epoch_s(series.close_time[i])
            formed = from_epoch_s(series.open_time[lo_i])
            for side, src, price in (
                (Side.BUY_SIDE, LevelSource.RANGE_HIGH, hi),
                (Side.SELL_SIDE, LevelSource.RANGE_LOW, lo),
            ):
                out.append(
                    _mk(seq, series.symbol, side, src, series.timeframe, price, formed, confirmed)
                )
                seq += 1
        prev_ok = ok
    return out


# ------------------------------------------------------------------------- engine


@dataclass
class LiquidityBook:
    """The result of a run: every level ever created, plus the live view."""

    symbol: str
    timeframe: str
    levels: list[LiquidityLevel] = field(default_factory=list)
    pruned: int = 0
    merged: int = 0

    def active(self) -> list[LiquidityLevel]:
        return [l for l in self.levels if l.is_active]

    def by_source(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for l in self.levels:
            out[l.source.value] = out.get(l.source.value, 0) + 1
        return dict(sorted(out.items()))

    def by_status(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for l in self.levels:
            out[l.status.value] = out.get(l.status.value, 0) + 1
        return dict(sorted(out.items()))


class LiquidityEngine:
    """Consumes H4 closes; admits, merges, ages, invalidates and prunes levels.

    The confirmation timeframe is H4 for every tier under DECISION D-002, so this
    engine steps one bar at a time over the H4 series and admits each candidate on the
    first bar whose close is at or after the candidate's own ``confirmed_at``.  A
    candidate can therefore never be admitted before the event that created it was
    knowable, whatever timeframe that event came from.
    """

    def __init__(
        self,
        series: BarSeries,
        cfg: AppConfig,
        candidates: Sequence[LiquidityLevel],
        d1_close_times: np.ndarray | None = None,
    ) -> None:
        self.series = series
        self.cfg = cfg
        self.atr = atr_ref(series, cfg.atr.period)
        self.book = LiquidityBook(series.symbol, series.timeframe)
        self._d1 = (
            np.asarray(d1_close_times, dtype=np.int64)
            if d1_close_times is not None
            else np.zeros(0, dtype=np.int64)
        )
        enabled = set(cfg.liq.enabled_sources)
        pending = [c for c in candidates if c.source.family in enabled]
        pending.sort(key=lambda c: (c.confirmed_at, c.id))
        self._pending = pending
        self._next = 0

    # ------------------------------------------------------------------ helpers

    def _d1_index(self, t: int) -> int:
        if self._d1.size == 0:
            return 0
        return int(np.searchsorted(self._d1, t, side="right"))

    def _atr(self, i: int) -> float:
        a = self.atr[i]
        return float(a) if np.isfinite(a) and a > 0 else 0.0

    def _terminate(self, lvl: LiquidityLevel, status: LevelStatus, i: int, at: datetime) -> None:
        lvl.status = status
        lvl.terminal_at = at
        lvl.terminal_bar = i

    # --------------------------------------------------------------------- steps

    def _admit(self, i: int, at: datetime) -> list[LiquidityLevel]:
        added: list[LiquidityLevel] = []
        while self._next < len(self._pending) and self._pending[self._next].confirmed_at <= at:
            lvl = self._pending[self._next]
            self._next += 1
            lvl.confirmed_bar = i
            lvl.confirmed_d1 = self._d1_index(int(self.series.close_time[i]))
            self.book.levels.append(lvl)
            added.append(lvl)
        return added

    def _merge(self, i: int, at: datetime) -> None:
        """SPEC 8.8.  A previous week high one pip above a previous day high is one
        level, not two; treating it as two double-counts every sweep of it.

        Run to a fixpoint.  One pass is not enough: SPEC 8.8 also says the survivor
        takes the *more extreme* price, which moves it toward the next cluster and can
        leave two active levels inside the tolerance again.  Iterating until nothing
        changes makes the post-condition -- no two active levels on a side within
        ``tol`` -- actually true at the end of every bar, and makes merging within a
        bar behave the same as merging across bars instead of quietly differing.
        """
        for _ in range(self.cfg.liq.max_active_levels):
            if not self._merge_pass(i, at):
                return

    def _merge_pass(self, i: int, at: datetime) -> bool:
        """One clustering sweep.  Returns True if anything merged."""
        tol = self.cfg.liq.merge_tolerance_atr * self._atr(i)
        if tol <= 0:
            return False
        changed = False
        for side in (Side.BUY_SIDE, Side.SELL_SIDE):
            group = [l for l in self.book.levels if l.is_active and l.side is side]
            if len(group) < 2:
                continue
            group.sort(key=lambda l: l.price)

            # Complete linkage: a cluster's whole span must fit inside `tol`.  Single
            # linkage ("each within tol of the previous") lets a chain of near-neighbours
            # collapse levels that are arbitrarily far apart, and because the survivor's
            # price is then moved to the cluster extreme, the chain walks further with
            # every step.  That is not "two levels one pip apart are one level".
            clusters: list[list[LiquidityLevel]] = []
            for lvl in group:
                if clusters and lvl.price - clusters[-1][0].price <= tol:
                    clusters[-1].append(lvl)
                else:
                    clusters.append([lvl])

            for cluster in clusters:
                if len(cluster) < 2:
                    continue
                winner = cluster[0]
                for other in cluster[1:]:
                    winner, _ = self._pick_survivor(winner, other, side)
                for loser in cluster:
                    if loser is winner:
                        continue
                    winner.strength += loser.strength
                    winner.source_ids = list(dict.fromkeys(winner.source_ids + loser.source_ids))
                    winner.tier = min(winner.tier, loser.tier)
                    loser.merged_into = winner.id
                    self._terminate(loser, LevelStatus.MERGED, i, at)
                    self.book.merged += 1
                    changed = True
                prices = [l.price for l in cluster]
                winner.price = max(prices) if side is Side.BUY_SIDE else min(prices)
        return changed

    @staticmethod
    def _pick_survivor(
        a: LiquidityLevel, b: LiquidityLevel, side: Side
    ) -> tuple[LiquidityLevel, LiquidityLevel]:
        """Lower tier wins, then the earlier level, then source precedence, then the id.

        **The third key exists because the first two do not settle the commonest merge in
        the book, and until Phase 14 nothing settled it on purpose.** A ``PROTECTED_SWING``
        duplicates a ``SWING_*`` at the identical price ~95% of the time (D-006), and the
        two share a tier and a confirmation bar -- so the tier and time keys both tie, and
        the winner fell out of whatever order the levels happened to sit in, which came
        from the id, which came from the order ``build_candidates`` happens to construct
        sources in. Changing the id format in Phase 14 flipped it and pushed
        ``PROTECTED_SWING``'s share of anchored sweeps from under 2% to 3.3%.

        D-006's finding was right and its mechanism was accidental. The rule it relied on
        is now stated: **a primary structural object beats one that annotates or is derived
        from it.** ``PROTECTED_SWING`` is a strength annotation on a swing (D-006, and
        STATE.md section 6 item 6), so the swing is the level and the annotation merges
        into it. See D-015 section 2.

        The id is the final key so the order is total and no pair can depend on list
        position -- which is what the docstring above always claimed and did not deliver.
        """
        if a.tier != b.tier:
            return (a, b) if a.tier < b.tier else (b, a)
        if a.confirmed_at != b.confirmed_at:
            return (a, b) if a.confirmed_at < b.confirmed_at else (b, a)
        pa, pb = _source_precedence(a.source), _source_precedence(b.source)
        if pa != pb:
            return (a, b) if pa < pb else (b, a)
        return (a, b) if a.id <= b.id else (b, a)

    def _age_and_expire(self, i: int, at: datetime) -> None:
        d1_now = self._d1_index(int(self.series.close_time[i]))
        for lvl in self.book.levels:
            if not lvl.is_active:
                continue
            lvl.age_d1 = max(0, d1_now - lvl.confirmed_d1)
            # SPEC 8.7: PREV_MONTH_* never ages out; it is replaced monthly.
            if lvl.source in (LevelSource.PREV_MONTH_HIGH, LevelSource.PREV_MONTH_LOW):
                continue
            limit = self.cfg.liq.max_age_d1_bars.get(str(lvl.tier))
            if limit is not None and lvl.age_d1 > limit:
                self._terminate(lvl, LevelStatus.EXPIRED, i, at)

    def _invalidate(self, i: int, at: datetime) -> None:
        """SPEC 8.7.  Accepted-through, not poked and rejected."""
        buf = self.cfg.liq.invalidate_buffer_atr * self._atr(i)
        c = float(self.series.close[i])
        hi = float(self.series.high[i])
        lo = float(self.series.low[i])
        for lvl in self.book.levels:
            if not lvl.is_active:
                continue
            if lvl.side is Side.BUY_SIDE:
                if hi > lvl.price:
                    lvl.penetrated = True
                beyond = c > lvl.price + buf
            else:
                if lo < lvl.price:
                    lvl.penetrated = True
                beyond = c < lvl.price - buf
            lvl.beyond_closes = lvl.beyond_closes + 1 if beyond else 0
            if lvl.beyond_closes >= self.cfg.liq.invalidate_closes:
                self._terminate(lvl, LevelStatus.INVALIDATED, i, at)

    def _prune(self, i: int, at: datetime) -> None:
        cap = self.cfg.liq.max_active_levels
        active = self.book.active()
        if len(active) <= cap:
            return
        ranked = sorted(active, key=lambda l: (self.rank(l, i), l.confirmed_at), reverse=True)
        for lvl in ranked[cap:]:
            self._terminate(lvl, LevelStatus.PRUNED, i, at)
            self.book.pruned += 1

    # ---------------------------------------------------------------- public API

    def rank(self, lvl: LiquidityLevel, i: int, bias_alignment: float = 0.0) -> float:
        """SPEC 8.8.  Ordering only -- this never decides a trade.

        ``bias_alignment`` is always 0 until the MTF bias engine exists; the term is
        wired so that adding it later is a one-line change rather than a re-derivation.
        """
        c = self.cfg.liq
        limit = c.max_age_d1_bars.get(str(lvl.tier), 30) or 1
        recency = max(0.0, 1.0 - lvl.age_d1 / limit)
        return (
            c.rank_tier_weight.get(str(lvl.tier), 0.0)
            + c.rank_strength_weight * min(lvl.strength, 4)
            + c.rank_recency_weight * recency
            + c.rank_bias_weight * bias_alignment
        )

    def in_play(self, lvl: LiquidityLevel, i: int) -> bool:
        """SPEC 8.8 in-play filter."""
        a = self._atr(i)
        if a <= 0:
            return False
        return abs(lvl.price - float(self.series.close[i])) <= self.cfg.liq.max_distance_atr * a

    def on_bar_close(self, i: int) -> list[LiquidityLevel]:
        at = from_epoch_s(self.series.close_time[i])
        added = self._admit(i, at)
        # Order matters: invalidate and expire BEFORE merging, so a level that is
        # already dead cannot absorb a live one and resurrect its price.
        self._invalidate(i, at)
        self._age_and_expire(i, at)
        self._merge(i, at)
        self._prune(i, at)
        return added

    def run(self) -> LiquidityBook:
        for i in range(self.series.n):
            self.on_bar_close(i)
        return self.book


def build_candidates(
    *,
    cfg: AppConfig,
    h4: BarSeries,
    d1: BarSeries | None = None,
    w1: BarSeries | None = None,
    mn1: BarSeries | None = None,
    sessions: Sequence[SessionInstance] = (),
    h4_structure: StructureResult | None = None,
    d1_swings: SwingStore | None = None,
) -> list[LiquidityLevel]:
    """Assemble every candidate level from the Phase 1 and Phase 5 outputs.

    Returns them unsorted; :class:`LiquidityEngine` sorts by ``confirmed_at`` and
    admits them causally.
    """
    out: list[LiquidityLevel] = []
    seq = 0

    def _ext(new: list[LiquidityLevel]) -> None:
        nonlocal seq, out
        out.extend(new)
        seq += len(new) + 1

    if d1 is not None:
        _ext(period_levels(d1, LevelSource.PREV_DAY_HIGH, LevelSource.PREV_DAY_LOW, seq))
    if w1 is not None:
        _ext(period_levels(w1, LevelSource.PREV_WEEK_HIGH, LevelSource.PREV_WEEK_LOW, seq))
    if mn1 is not None:
        _ext(period_levels(mn1, LevelSource.PREV_MONTH_HIGH, LevelSource.PREV_MONTH_LOW, seq))
    if sessions:
        _ext(session_levels(sessions, seq, liquidity_session_names(cfg)))
    if h4_structure is not None:
        if "H4" in cfg.liq.swing_timeframes:
            _ext(swing_levels(h4_structure.swings, "H4", seq))
        _ext(protected_levels(h4_structure.protected_changes, "H4", seq))
        _ext(equal_levels(h4_structure.swings, h4, cfg, seq))
    if d1_swings is not None and d1 is not None and "D1" in cfg.liq.swing_timeframes:
        _ext(swing_levels(d1_swings, "D1", seq))
    _ext(range_levels(h4, cfg, seq))
    return out


def build_book(
    *,
    cfg: AppConfig,
    h4: BarSeries,
    d1: BarSeries | None = None,
    w1: BarSeries | None = None,
    mn1: BarSeries | None = None,
    sessions: Sequence[SessionInstance] = (),
    h4_structure: StructureResult | None = None,
    d1_swings: SwingStore | None = None,
) -> LiquidityBook:
    candidates = build_candidates(
        cfg=cfg,
        h4=h4,
        d1=d1,
        w1=w1,
        mn1=mn1,
        sessions=sessions,
        h4_structure=h4_structure,
        d1_swings=d1_swings,
    )
    engine = LiquidityEngine(
        h4, cfg, candidates, d1_close_times=d1.close_time if d1 is not None else None
    )
    return engine.run()
