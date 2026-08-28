"""Liquidity sweep detection (SPEC 9).

A sweep is a two-part event: **penetration then reclaim**, both bounded in size and in
time.  It is the strategy's central event, and the whole point of defining it
arithmetically is that "this looks like a stop run" is not a rule anyone can backtest.

Three things about this module are load-bearing:

* **Failure to reclaim is not "no event" (SPEC 9.1).**  It invalidates the level with
  reason ``ACCEPTED_THROUGH`` and emits ``SWEEP_FAILED``.  The ratio of confirmed to
  failed sweeps per source is a direct measure of whether a level is a real barrier,
  and that measurement is impossible if failures are silently dropped.
* **Over-penetration is a breakout, not a sweep (SPEC 9.6).**  This is the deliberate
  boundary between the two regimes, and misclassifying it in either direction is the
  main way this engine can be wrong.
* **The age rule applies to swing-derived levels only (SPEC 9.2.1).**  Applying it to
  session levels put the earliest sweepable moment after the London close and made the
  flagship setup unreachable.

Sweeps run *after* the liquidity engine's own bar step (`STATE_MACHINE.md` §4, steps 6
then 7), so a level that the acceptance rule has already killed this bar is seen as
dead here -- which is correct, since two consecutive closes beyond a level is a
stronger statement than "did not reclaim within three bars".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Mapping, Sequence

import numpy as np

from bot.config.schema import AppConfig
from bot.core.bars import BarSeries, from_epoch_s
from bot.core.ids import object_id
from bot.core.indicators import atr_ref
from bot.core.liquidity import (
    LevelSource,
    LevelStatus,
    LiquidityBook,
    LiquidityLevel,
    Side,
)

#: Sources whose levels can be swept by the very impulse that created them, and which
#: therefore need the age guard (SPEC 9.2.1).  A period- or session-derived level is
#: the extreme of a *completed* period, so the move that formed it is already over.
_AGE_GUARDED = {
    LevelSource.SWING_HIGH,
    LevelSource.SWING_LOW,
    LevelSource.PROTECTED_SWING,
    LevelSource.EQUAL_HIGHS,
    LevelSource.EQUAL_LOWS,
    LevelSource.RANGE_HIGH,
    LevelSource.RANGE_LOW,
}


class SweepEventType(str, Enum):
    CONFIRMED = "SWEEP_CONFIRMED"
    FAILED = "SWEEP_FAILED"
    REJECTED = "SWEEP_REJECTED"


class SweepReason(str, Enum):
    # FAILED
    NO_RECLAIM = "NO_RECLAIM"
    OVER_PENETRATION = "OVER_PENETRATION"
    ACCEPTED_THROUGH = "ACCEPTED_THROUGH"
    GAPPED_THROUGH = "GAPPED_THROUGH"
    LEVEL_GONE = "LEVEL_GONE"
    # REJECTED
    UNDER_PENETRATION = "UNDER_PENETRATION"
    WICK_RATIO = "WICK_RATIO"
    CLOSE_POSITION = "CLOSE_POSITION"


@dataclass(frozen=True)
class SweepEvent:
    """One outcome for one level.  SPEC 9.1: identity is (level, trigger, confirm, extreme)."""

    id: str
    symbol: str
    timeframe: str
    type: SweepEventType
    reason: SweepReason | None
    side: Side  # side of the LEVEL; a SELL_SIDE sweep opens a BULLISH setup
    level_id: str
    level_source: LevelSource
    level_tier: int
    level_price: float
    level_strength: int
    trigger_bar: int
    confirm_bar: int
    at: datetime
    sweep_extreme: float
    sweep_extreme_bar: int
    penetration: float
    penetration_atr: float
    wick_ratio: float
    close_position: float
    confirmation_bars: int
    single_bar_sweep: bool
    data_suspect: bool = False

    @property
    def setup_direction(self) -> str:
        """A sweep of SELL_SIDE liquidity opens a BULLISH setup, and vice versa."""
        return "BULLISH" if self.side is Side.SELL_SIDE else "BEARISH"


@dataclass
class SweepCluster:
    """SPEC 9.4.  Several stacked levels swept by one bar are ONE opportunity.

    Without this, three stacked levels produce three near-identical trades and triple
    the apparent sample size while tripling correlated risk.
    """

    id: str
    at: datetime
    confirm_bar: int
    side: Side
    events: list[SweepEvent] = field(default_factory=list)

    @property
    def anchor(self) -> SweepEvent:
        """The deepest level: lowest price for a sell-side cluster, highest for buy."""
        if self.side is Side.SELL_SIDE:
            return min(self.events, key=lambda e: e.level_price)
        return max(self.events, key=lambda e: e.level_price)

    @property
    def strength(self) -> int:
        return sum(e.level_strength for e in self.events)

    @property
    def sweep_extreme(self) -> float:
        vals = [e.sweep_extreme for e in self.events]
        return min(vals) if self.side is Side.SELL_SIDE else max(vals)


@dataclass
class _Window:
    level: LiquidityLevel
    trigger_bar: int
    extreme: float
    # The BAR the extreme printed on, not just its price.  SPEC 10.1 clamps the
    # displacement leg's origin to it, so without this Phase 8 would have to recover it
    # by scanning for a matching price -- which is ambiguous the moment two bars in the
    # window share a low.
    extreme_bar: int
    atr: float
    trigger_wick_ratio: float
    range_high: float
    range_low: float


@dataclass
class SweepResult:
    symbol: str
    timeframe: str
    events: list[SweepEvent] = field(default_factory=list)
    clusters: list[SweepCluster] = field(default_factory=list)

    def confirmed(self) -> list[SweepEvent]:
        return [e for e in self.events if e.type is SweepEventType.CONFIRMED]

    def of_type(self, t: SweepEventType) -> list[SweepEvent]:
        return [e for e in self.events if e.type is t]

    def counts_by(self, attr: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.events:
            v = getattr(e, attr)
            k = v.value if isinstance(v, Enum) else str(v)
            out[k] = out.get(k, 0) + 1
        return dict(sorted(out.items()))


class SweepEngine:
    """Detects sweeps against an active liquidity book, one confirmation bar at a time."""

    def __init__(
        self,
        series: BarSeries,
        cfg: AppConfig,
        book: LiquidityBook,
        tf_close_times: Mapping[str, np.ndarray] | None = None,
    ) -> None:
        self.series = series
        self.cfg = cfg
        self.book = book
        self.atr = atr_ref(series, cfg.atr.period)
        self.result = SweepResult(series.symbol, series.timeframe)
        self._windows: dict[str, _Window] = {}
        # Rebuilt lazily: the book grows as bars close, so a snapshot taken at
        # construction would leave every later level unresolvable through a merge chain.
        self._by_id: dict[str, LiquidityLevel] = {}
        self._by_id_n = -1
        self._tf_close = {k: np.asarray(v, dtype=np.int64) for k, v in (tf_close_times or {}).items()}
        self._seq = 0

    # ------------------------------------------------------------------ helpers

    def _atr(self, i: int) -> float:
        a = self.atr[i]
        return float(a) if np.isfinite(a) and a > 0 else 0.0

    def _refresh_index(self) -> None:
        if len(self.book.levels) != self._by_id_n:
            self._by_id = {l.id: l for l in self.book.levels}
            self._by_id_n = len(self.book.levels)

    def _resolve(self, lvl: LiquidityLevel) -> LiquidityLevel:
        """Follow a merge chain to the level that actually survived.

        With ~65% of levels merging (D-006), a window keyed on a level that is absorbed
        mid-sweep would otherwise be dropped and the sweep lost.
        """
        seen = 0
        while lvl.merged_into and seen < 64:
            nxt = self._by_id.get(lvl.merged_into)
            if nxt is None:
                break
            lvl = nxt
            seen += 1
        return lvl

    def age_ok(self, lvl: LiquidityLevel, i: int) -> bool:
        """SPEC 9.2.1.  Swing-derived sources only, in bars of their own detection TF."""
        if lvl.source not in _AGE_GUARDED:
            return True
        need = self.cfg.sweep.require_prior_level_age_bars
        if need <= 0:
            return True
        closes = self._tf_close.get(lvl.timeframe)
        now = int(self.series.close_time[i])
        conf = int(lvl.confirmed_at.timestamp())
        if closes is None or closes.size == 0:
            # No clock for that timeframe: fall back to the confirmation series, which
            # is never more permissive than the level's own (slower) timeframe.
            closes = self.series.close_time
        a = int(np.searchsorted(closes, conf, side="right"))
        b = int(np.searchsorted(closes, now, side="right"))
        return (b - a) >= need

    def _mk_id(self, level_id: str, i: int, w: _Window) -> str:
        """SPEC 9.1: "identity is (level, trigger, confirm, extreme)". Literally that."""
        self._seq += 1
        return object_id(
            "SW",
            symbol=self.series.symbol,
            timeframe=self.series.timeframe,
            at=from_epoch_s(self.series.close_time[i]),
            key=(
                level_id,
                int(self.series.open_time[w.trigger_bar]),
                int(self.series.close_time[i]),
                w.extreme,
            ),
        )

    def _emit(
        self,
        w: _Window,
        i: int,
        type_: SweepEventType,
        reason: SweepReason | None,
        *,
        close_position: float = 0.0,
    ) -> SweepEvent:
        lvl = w.level
        pen = (
            lvl.price - w.extreme if lvl.side is Side.SELL_SIDE else w.extreme - lvl.price
        )
        ev = SweepEvent(
            id=self._mk_id(lvl.id, i, w),
            symbol=self.series.symbol,
            timeframe=self.series.timeframe,
            type=type_,
            reason=reason,
            side=lvl.side,
            level_id=lvl.id,
            level_source=lvl.source,
            level_tier=lvl.tier,
            level_price=lvl.price,
            level_strength=lvl.strength,
            trigger_bar=w.trigger_bar,
            confirm_bar=i,
            at=from_epoch_s(self.series.close_time[i]),
            sweep_extreme=w.extreme,
            sweep_extreme_bar=w.extreme_bar,
            penetration=pen,
            penetration_atr=pen / w.atr if w.atr > 0 else 0.0,
            wick_ratio=w.trigger_wick_ratio,
            close_position=close_position,
            confirmation_bars=i - w.trigger_bar + 1,
            single_bar_sweep=i == w.trigger_bar,
            data_suspect=bool(self.series.flag("data_suspect")[i]),
        )
        self.result.events.append(ev)
        return ev

    def _wick_ratio(self, i: int, side: Side) -> float:
        o, h, l, c = (
            float(self.series.open[i]),
            float(self.series.high[i]),
            float(self.series.low[i]),
            float(self.series.close[i]),
        )
        rng = h - l
        if rng <= 0:
            return 0.0
        wick = (min(o, c) - l) if side is Side.SELL_SIDE else (h - max(o, c))
        # Clamped to [0, 1].  A negative wick is arithmetically impossible for a
        # well-formed bar, but a malformed one (close outside [low, high]) would
        # otherwise produce a negative ratio that fails `< min_wick_ratio` even when the
        # filter is switched off at 0.0 -- silently rejecting a valid sweep.  Malformed
        # bars are the ingest layer's job to reject (SPEC 1.5, `quality.analyse`); this
        # is only here so a filter that is off behaves as if it is off.
        return min(1.0, max(0.0, wick / rng))

    # -------------------------------------------------------------------- the rule

    def on_bar_close(self, i: int) -> list[SweepEvent]:
        before = len(self.result.events)
        self._refresh_index()
        a = self._atr(i)
        if a <= 0:
            return []

        c = float(self.series.close[i])
        h = float(self.series.high[i])
        l = float(self.series.low[i])
        cfg = self.cfg.sweep

        # Existing windows first, so a level that was killed this bar closes its window
        # with the right reason instead of silently vanishing.
        for level_id, w in list(self._windows.items()):
            lvl = self._resolve(w.level)
            if not lvl.is_active:
                reason = (
                    SweepReason.ACCEPTED_THROUGH
                    if lvl.status is LevelStatus.INVALIDATED
                    else SweepReason.LEVEL_GONE
                )
                self._emit(w, i, SweepEventType.FAILED, reason)
                del self._windows[level_id]
                continue
            w.level = lvl
            new_extreme = min(w.extreme, l) if lvl.side is Side.SELL_SIDE else max(w.extreme, h)
            if new_extreme != w.extreme:
                w.extreme, w.extreme_bar = new_extreme, i
            w.range_high = max(w.range_high, h)
            w.range_low = min(w.range_low, l)
            self._step(level_id, w, i, c, a)

        # New windows.
        for lvl in self.book.active():
            if lvl.id in self._windows:
                continue
            if int(lvl.confirmed_at.timestamp()) > int(self.series.open_time[i]):
                continue  # SPEC 9.1: level.confirmed_at <= open_time(s)
            if not self.age_ok(lvl, i):
                continue
            # Gap first.  A bar that opens beyond the level and never traded at it has
            # penetrated it on paper -- `low < price` is true -- but there was no
            # opportunity to sweep, so classifying it by penetration depth would report
            # a breakout where the market simply was not there (SPEC 9.6).
            if self._check_gap_through(lvl, i, a):
                continue
            penetrated = l < lvl.price if lvl.side is Side.SELL_SIDE else h > lvl.price
            if not penetrated:
                continue
            w = _Window(
                level=lvl,
                trigger_bar=i,
                extreme=l if lvl.side is Side.SELL_SIDE else h,
                extreme_bar=i,
                atr=a,
                trigger_wick_ratio=self._wick_ratio(i, lvl.side),
                range_high=h,
                range_low=l,
            )
            self._windows[lvl.id] = w
            self._step(lvl.id, w, i, c, a)

        return self.result.events[before:]

    def _step(self, level_id: str, w: _Window, i: int, c: float, a: float) -> None:
        cfg = self.cfg.sweep
        lvl = w.level
        sell = lvl.side is Side.SELL_SIDE
        pen = lvl.price - w.extreme if sell else w.extreme - lvl.price

        # Over-penetration first: it is a breakout, and a breakout that later closes
        # back is still a breakout (SPEC 9.6).
        if pen > cfg.max_penetration_atr * w.atr:
            self._emit(w, i, SweepEventType.FAILED, SweepReason.OVER_PENETRATION)
            lvl.status = LevelStatus.INVALIDATED
            lvl.terminal_at = from_epoch_s(self.series.close_time[i])
            lvl.terminal_bar = i
            del self._windows[level_id]
            return

        buf = cfg.reclaim_buffer_atr * w.atr
        reclaimed = c > lvl.price + buf if sell else c < lvl.price - buf
        if reclaimed:
            if pen < cfg.min_penetration_atr * w.atr:
                self._emit(w, i, SweepEventType.REJECTED, SweepReason.UNDER_PENETRATION)
                del self._windows[level_id]
                return
            if w.trigger_wick_ratio < cfg.min_wick_ratio:
                self._emit(w, i, SweepEventType.REJECTED, SweepReason.WICK_RATIO)
                del self._windows[level_id]
                return
            span = (w.range_high - w.extreme) if sell else (w.extreme - w.range_low)
            pos = ((c - w.extreme) / span if sell else (w.extreme - c) / span) if span > 0 else 0.0
            if pos < cfg.min_close_position:
                self._emit(
                    w, i, SweepEventType.REJECTED, SweepReason.CLOSE_POSITION, close_position=pos
                )
                del self._windows[level_id]
                return
            ev = self._emit(w, i, SweepEventType.CONFIRMED, None, close_position=pos)
            lvl.status = LevelStatus.SWEPT
            lvl.swept_by = ev.id
            lvl.terminal_at = ev.at
            lvl.terminal_bar = i
            del self._windows[level_id]
            return

        if i - w.trigger_bar + 1 >= cfg.max_confirmation_bars:
            self._emit(w, i, SweepEventType.FAILED, SweepReason.NO_RECLAIM)
            lvl.status = LevelStatus.INVALIDATED
            lvl.terminal_at = from_epoch_s(self.series.close_time[i])
            lvl.terminal_bar = i
            del self._windows[level_id]

    def _check_gap_through(self, lvl: LiquidityLevel, i: int, a: float) -> bool:
        """SPEC 9.6.  A weekend gap that reopens beyond a level never swept it.

        No penetration bar exists, so there is nothing to reclaim; the level is
        invalidated rather than left ACTIVE to produce a spurious sweep on the way back.
        """
        if i == 0:
            return False
        prev_h = float(self.series.high[i - 1])
        prev_l = float(self.series.low[i - 1])
        h = float(self.series.high[i])
        l = float(self.series.low[i])
        if lvl.side is Side.SELL_SIDE:
            gapped = prev_l > lvl.price and h < lvl.price
        else:
            gapped = prev_h < lvl.price and l > lvl.price
        if not gapped:
            return False
        w = _Window(lvl, i, lvl.price, i, a, 0.0, h, l)
        self._emit(w, i, SweepEventType.FAILED, SweepReason.GAPPED_THROUGH)
        lvl.status = LevelStatus.INVALIDATED
        lvl.terminal_at = from_epoch_s(self.series.close_time[i])
        lvl.terminal_bar = i
        return True

    # -------------------------------------------------------------------- clusters

    def _build_clusters(self) -> None:
        """SPEC 9.4.  Group same-bar, same-side confirmations into one opportunity."""
        groups: dict[tuple[int, Side], list[SweepEvent]] = {}
        for e in self.result.confirmed():
            groups.setdefault((e.confirm_bar, e.side), []).append(e)
        for k, (key, evs) in enumerate(sorted(groups.items())):
            bar, side = key
            self.result.clusters.append(
                SweepCluster(
                    id=object_id(
                        "SC",
                        symbol=self.series.symbol,
                        timeframe=self.series.timeframe,
                        at=evs[0].at,
                        key=(side.value, tuple(sorted(e.id for e in evs))),
                    ),
                    at=evs[0].at,
                    confirm_bar=bar,
                    side=side,
                    events=sorted(evs, key=lambda e: e.level_price),
                )
            )

    def run(self) -> SweepResult:
        for i in range(self.series.n):
            self.on_bar_close(i)
        self._build_clusters()
        return self.result


# ---------------------------------------------------------------- orchestration


def analyse_sweeps(
    *,
    cfg: AppConfig,
    h4: BarSeries,
    d1: BarSeries | None = None,
    w1: BarSeries | None = None,
    mn1: BarSeries | None = None,
    sessions: Sequence = (),
    h4_structure=None,
    d1_swings=None,
    level_transform=None,
) -> tuple[LiquidityBook, SweepResult]:
    """Run the liquidity and sweep engines interleaved, one H4 bar at a time.

    The order inside a bar is `STATE_MACHINE.md` §4: liquidity (step 6) then sweeps
    (step 7).  It matters -- a level the acceptance rule kills on this bar must be seen
    as dead by the sweep engine, and a level admitted on this bar must be visible to it.
    Running the two engines to completion one after the other instead would let a sweep
    fire against a level that had already been invalidated, or miss one admitted late.

    ``level_transform`` is the seam `BACKTEST_PROTOCOL.md` §6.3's shuffled-liquidity
    control enters through: it replaces the candidate levels and nothing else, so every
    engine below this line -- admission, merging, ageing, sweeps -- is provably the same
    code on both arms.  Reimplementing this loop in the research module instead would
    put the interleave order above at risk of drifting between the control and the
    thing it is a control for, which is the one difference that must not exist.
    """
    from bot.core.liquidity import LiquidityEngine, build_candidates

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
    if level_transform is not None:
        candidates = list(level_transform(candidates))
    liq = LiquidityEngine(
        h4, cfg, candidates, d1_close_times=d1.close_time if d1 is not None else None
    )
    tf_close = {"H4": h4.close_time}
    if d1 is not None:
        tf_close["D1"] = d1.close_time
    if w1 is not None:
        tf_close["W1"] = w1.close_time
    if mn1 is not None:
        tf_close["MN1"] = mn1.close_time
    sweeps = SweepEngine(h4, cfg, liq.book, tf_close_times=tf_close)

    for i in range(h4.n):
        liq.on_bar_close(i)
        sweeps.on_bar_close(i)
    sweeps._build_clusters()
    return liq.book, sweeps.result
