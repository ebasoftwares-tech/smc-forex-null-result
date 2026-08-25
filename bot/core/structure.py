"""Market structure engine: BOS and CHoCH (SPEC 6).

**MSS is not implemented here, deliberately.** SPEC 6.6 defines MSS as a CHoCH that
additionally sits in sweep context and displaces, and neither the sweep engine
(Phase 7) nor displacement (Phase 8) exists yet.  This module emits the *superset* --
every structural break, unfiltered -- which is exactly what makes the marginal value
of those filters measurable later (SPEC 6.9): if MSS and CHoCH-not-MSS turn out to be
statistically indistinguishable, the whole sweep-plus-displacement requirement is
decoration, and that comparison is only possible because the unfiltered event was
recorded too.

The three rules that carry the most weight:

* **Break by close, not by wick.**  A wick break of a level is precisely what a
  liquidity sweep of that level looks like; accepting one makes the system trade the
  pattern it exists to fade.
* **The protected level ratchets one way only.**  Without it a deep pullback that
  prints a lower swing low would quietly move the protected level down and the
  reversal signal would never fire.
* **A wick through the protected level is an event, not a break.**  It is recorded as
  ``INTERNAL_LIQUIDITY_GRAB`` and is arguably the highest-quality liquidity source in
  the model, because the level is defined by the structure the market is currently
  trading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

import numpy as np

from bot.config.schema import AppConfig
from bot.core.bars import BarSeries, from_epoch_s
from bot.core.indicators import atr_ref
from bot.core.swings import Swing, SwingKind, SwingLabel, SwingStore, detect_at, swing_prices


class Trend(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    UNDEFINED = "UNDEFINED"


class EventType(str, Enum):
    BOS = "BOS"
    CHOCH = "CHOCH"
    INTERNAL_LIQUIDITY_GRAB = "INTERNAL_LIQUIDITY_GRAB"
    TREND_INITIALISED = "TREND_INITIALISED"


class Side(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


@dataclass(frozen=True)
class StructureEvent:
    id: str
    symbol: str
    timeframe: str
    type: EventType
    side: Side
    at: datetime
    bar_index: int
    level: float
    swing_id: str | None
    trend_before: Trend
    trend_after: Trend
    gap_break: bool = False
    whipsaw: bool = False
    resolved_undefined: bool = False
    detail: str = ""


@dataclass
class StructureState:
    """SPEC 6.1."""

    symbol: str
    timeframe: str
    trend: Trend = Trend.UNDEFINED
    last_swing_high: Swing | None = None
    last_swing_low: Swing | None = None
    protected_low: Swing | None = None
    protected_high: Swing | None = None
    last_event: StructureEvent | None = None
    last_flip_index: int | None = None

    def snapshot(self) -> dict:
        return {
            "trend": self.trend.value,
            "last_swing_high": self.last_swing_high.price if self.last_swing_high else None,
            "last_swing_low": self.last_swing_low.price if self.last_swing_low else None,
            "protected_low": self.protected_low.price if self.protected_low else None,
            "protected_high": self.protected_high.price if self.protected_high else None,
            "last_event": self.last_event.type.value if self.last_event else None,
        }


@dataclass(frozen=True)
class ProtectedChange:
    """The moment a swing became the protected level.

    Recorded separately from ``events`` deliberately.  The liquidity engine needs every
    protected swing as a ``PROTECTED_SWING`` level (SPEC 8.3, source 7), but adding a
    new entry to the structure event stream would change what BOS/CHoCH counts mean and
    would rewrite the Phase 5 golden file for a reason that has nothing to do with
    structure.
    """

    at: datetime
    bar_index: int
    side: Side  # BULLISH -> protected_low, BEARISH -> protected_high
    swing: Swing


@dataclass
class StructureResult:
    state: StructureState
    swings: SwingStore
    events: list[StructureEvent] = field(default_factory=list)
    protected_changes: list[ProtectedChange] = field(default_factory=list)

    def of_type(self, t: EventType) -> list[StructureEvent]:
        return [e for e in self.events if e.type is t]

    def as_of(self, ts: datetime) -> list[StructureEvent]:
        return [e for e in self.events if e.at <= ts]


class StructureEngine:
    """Incremental engine.  One bar close in, zero or more events out."""

    def __init__(self, series: BarSeries, cfg: AppConfig) -> None:
        self.series = series
        self.cfg = cfg
        self.state = StructureState(series.symbol, series.timeframe)
        self.swings = SwingStore(series.symbol, series.timeframe)
        self.events: list[StructureEvent] = []
        self._highs, self._lows = swing_prices(series, cfg.swing.price_source)
        self._atr = atr_ref(series, cfg.atr.period)
        self._seq = 0
        # A break is an EVENT, not a state.  Once a swing has been broken, later bars
        # that also close beyond it are continuation, not a fresh break -- without this
        # a single sustained move emits a BOS on every bar until the next swing
        # confirms N bars later.  Same principle as SPEC 8.9: a level is consumed once.
        self._broken: set[str] = set()
        self._grabbed: set[str] = set()
        self.protected_changes: list[ProtectedChange] = []
        self._last_protected: tuple[str | None, str | None] = (None, None)

    # ------------------------------------------------------------------- helpers

    def _pen(self, i: int) -> float:
        """Break penetration threshold in price units, from ATR_ref(i).

        NaN during ATR warm-up means the threshold is unknown; a zero-configured
        penetration is still well defined, so warm-up only matters when the ablation
        is switched on.
        """
        mult = self.cfg.structure.min_break_penetration_atr
        if mult == 0.0:
            return 0.0
        a = self._atr[i]
        return float(mult * a) if np.isfinite(a) else float("inf")

    def _break_up(self, i: int, level: float, swing_id: str | None = None) -> bool:
        if swing_id is not None and swing_id in self._broken:
            return False
        s = self.series
        price = s.close[i] if self.cfg.structure.break_confirmation == "close" else s.high[i]
        return bool(price > level + self._pen(i))

    def _break_down(self, i: int, level: float, swing_id: str | None = None) -> bool:
        if swing_id is not None and swing_id in self._broken:
            return False
        s = self.series
        price = s.close[i] if self.cfg.structure.break_confirmation == "close" else s.low[i]
        return bool(price < level - self._pen(i))

    def _is_gap_break(self, i: int, level: float, up: bool) -> bool:
        """True when the bar opened beyond a level the previous bar closed short of."""
        if i == 0:
            return False
        prev_c, o = self.series.close[i - 1], self.series.open[i]
        return bool(prev_c <= level < o) if up else bool(prev_c >= level > o)

    def _emit(self, **kw) -> StructureEvent:
        self._seq += 1
        ev = StructureEvent(
            id=f"{self.series.symbol}:{self.series.timeframe}:EV:{self._seq:05d}", **kw
        )
        self.events.append(ev)
        self.state.last_event = ev
        return ev

    # ------------------------------------------------------------------ main loop

    def _record_protected_change(self, i: int, at: datetime) -> None:
        """Note every distinct swing that becomes a protected level.

        Compared once at the end of the bar rather than at each assignment site, so
        that initialisation, the BOS reset, the ratchet and a CHoCH flip are all
        covered by one rule and cannot drift apart as those paths change.
        """
        st = self.state
        lo_id = st.protected_low.id if st.protected_low else None
        hi_id = st.protected_high.id if st.protected_high else None
        prev_lo, prev_hi = self._last_protected
        if lo_id is not None and lo_id != prev_lo:
            self.protected_changes.append(
                ProtectedChange(at, i, Side.BULLISH, st.protected_low)
            )
        if hi_id is not None and hi_id != prev_hi:
            self.protected_changes.append(
                ProtectedChange(at, i, Side.BEARISH, st.protected_high)
            )
        self._last_protected = (lo_id, hi_id)

    def on_bar_close(self, i: int) -> list[StructureEvent]:
        st = self.state
        at = from_epoch_s(self.series.close_time[i])
        before = len(self.events)

        # 1. Swings that became knowable at this close.  A swing confirmed now cannot
        #    be broken by this same bar: for a swing high at c, C_i <= H_i <= H_c by
        #    the fractal condition itself, so the break test is self-consistent.
        for s in detect_at(self.series, i, self.cfg, self._highs, self._lows):
            self.swings.add(s, i, at)
        st.last_swing_high = self.swings.last_of(SwingKind.HIGH)
        st.last_swing_low = self.swings.last_of(SwingKind.LOW)

        # 2. Snapshot the levels both break tests will read (SPEC 6.8): BOS and CHoCH
        #    are evaluated against the state as it stood before either fired, so a bar
        #    that does both is not judged against a level its own BOS just moved.
        snap_high = st.last_swing_high
        snap_low = st.last_swing_low
        snap_prot_low = st.protected_low
        snap_prot_high = st.protected_high
        trend_before = st.trend

        if st.trend is Trend.UNDEFINED:
            self._try_initialise(i, at)
            if st.trend is Trend.UNDEFINED:
                self._try_first_break(i, at, snap_high, snap_low)
        elif st.trend is Trend.BULLISH:
            self._bullish_bar(i, at, snap_high, snap_prot_low, trend_before)
        else:
            self._bearish_bar(i, at, snap_low, snap_prot_high, trend_before)

        self._ratchet(i)
        self._record_protected_change(i, at)
        return self.events[before:]

    # ------------------------------------------------------------- initialisation

    def _try_initialise(self, i: int, at: datetime) -> None:
        """SPEC 6.2.  Two confirmed swings of each kind, and agreeing labels.

        Trend is derived from structure only.  Inferring it from a moving average or
        the direction of the last N bars would make UNDEFINED meaningless -- and
        UNDEFINED is load-bearing: it is how the engine says "there is no structure to
        trade here", which is a legitimate and frequent answer.
        """
        st = self.state
        hi, lo = st.last_swing_high, st.last_swing_low
        if hi is None or lo is None:
            return
        if hi.label is SwingLabel.HH and lo.label is SwingLabel.HL:
            st.trend = Trend.BULLISH
            st.protected_low = lo
            self._emit(
                symbol=self.series.symbol, timeframe=self.series.timeframe,
                type=EventType.TREND_INITIALISED, side=Side.BULLISH, at=at, bar_index=i,
                level=lo.price, swing_id=lo.id, trend_before=Trend.UNDEFINED,
                trend_after=Trend.BULLISH, detail="HH + HL",
            )
        elif hi.label is SwingLabel.LH and lo.label is SwingLabel.LL:
            st.trend = Trend.BEARISH
            st.protected_high = hi
            self._emit(
                symbol=self.series.symbol, timeframe=self.series.timeframe,
                type=EventType.TREND_INITIALISED, side=Side.BEARISH, at=at, bar_index=i,
                level=hi.price, swing_id=hi.id, trend_before=Trend.UNDEFINED,
                trend_after=Trend.BEARISH, detail="LH + LL",
            )

    def _try_first_break(
        self, i: int, at: datetime, hi: Swing | None, lo: Swing | None
    ) -> None:
        """SPEC 6.4: while UNDEFINED, the first break in either direction sets the trend."""
        st = self.state
        if hi is not None and self._break_up(i, hi.price, hi.id):
            self._broken.add(hi.id)
            st.trend = Trend.BULLISH
            st.protected_low = self.swings.last_confirmed_by(i, SwingKind.LOW)
            self._emit(
                symbol=self.series.symbol, timeframe=self.series.timeframe,
                type=EventType.BOS, side=Side.BULLISH, at=at, bar_index=i, level=hi.price,
                swing_id=hi.id, trend_before=Trend.UNDEFINED, trend_after=Trend.BULLISH,
                gap_break=self._is_gap_break(i, hi.price, up=True), resolved_undefined=True,
                detail="first break resolved UNDEFINED",
            )
        elif lo is not None and self._break_down(i, lo.price, lo.id):
            self._broken.add(lo.id)
            st.trend = Trend.BEARISH
            st.protected_high = self.swings.last_confirmed_by(i, SwingKind.HIGH)
            self._emit(
                symbol=self.series.symbol, timeframe=self.series.timeframe,
                type=EventType.BOS, side=Side.BEARISH, at=at, bar_index=i, level=lo.price,
                swing_id=lo.id, trend_before=Trend.UNDEFINED, trend_after=Trend.BEARISH,
                gap_break=self._is_gap_break(i, lo.price, up=False), resolved_undefined=True,
                detail="first break resolved UNDEFINED",
            )

    # ----------------------------------------------------------------- trend bars

    def _bullish_bar(
        self, i: int, at: datetime, hi: Swing | None, prot: Swing | None, before: Trend
    ) -> None:
        st = self.state
        # BOS first, then CHoCH, in that fixed order (SPEC 6.8).  Both are logged when
        # both fire -- rare, but real on high-impact news bars.
        if hi is not None and self._break_up(i, hi.price, hi.id):
            self._broken.add(hi.id)
            self._emit(
                symbol=self.series.symbol, timeframe=self.series.timeframe,
                type=EventType.BOS, side=Side.BULLISH, at=at, bar_index=i, level=hi.price,
                swing_id=hi.id, trend_before=before, trend_after=Trend.BULLISH,
                gap_break=self._is_gap_break(i, hi.price, up=True),
            )
            self._reset_protected_on_bos(i, bullish=True)

        if prot is None:
            return
        if self._break_down(i, prot.price, prot.id):
            self._broken.add(prot.id)
            whip = self._is_whipsaw(i)
            st.trend = Trend.BEARISH
            st.protected_high = self.swings.last_confirmed_by(i, SwingKind.HIGH)
            st.protected_low = None
            st.last_flip_index = i
            self._emit(
                symbol=self.series.symbol, timeframe=self.series.timeframe,
                type=EventType.CHOCH, side=Side.BEARISH, at=at, bar_index=i, level=prot.price,
                swing_id=prot.id, trend_before=before, trend_after=Trend.BEARISH,
                gap_break=self._is_gap_break(i, prot.price, up=False), whipsaw=whip,
            )
        elif self.series.low[i] < prot.price and prot.id not in self._grabbed:
            # Wick through, close above: the trend is intact and the protected level
            # does NOT move (on_wick_below_protected = keep).  This is a sweep of the
            # level the market is currently trading against.
            self._emit(
                symbol=self.series.symbol, timeframe=self.series.timeframe,
                type=EventType.INTERNAL_LIQUIDITY_GRAB, side=Side.BULLISH, at=at,
                bar_index=i, level=prot.price, swing_id=prot.id, trend_before=before,
                trend_after=before, detail="wick below protected low, close above",
            )
            self._grabbed.add(prot.id)

    def _bearish_bar(
        self, i: int, at: datetime, lo: Swing | None, prot: Swing | None, before: Trend
    ) -> None:
        st = self.state
        if lo is not None and self._break_down(i, lo.price, lo.id):
            self._broken.add(lo.id)
            self._emit(
                symbol=self.series.symbol, timeframe=self.series.timeframe,
                type=EventType.BOS, side=Side.BEARISH, at=at, bar_index=i, level=lo.price,
                swing_id=lo.id, trend_before=before, trend_after=Trend.BEARISH,
                gap_break=self._is_gap_break(i, lo.price, up=False),
            )
            self._reset_protected_on_bos(i, bullish=False)

        if prot is None:
            return
        if self._break_up(i, prot.price, prot.id):
            self._broken.add(prot.id)
            whip = self._is_whipsaw(i)
            st.trend = Trend.BULLISH
            st.protected_low = self.swings.last_confirmed_by(i, SwingKind.LOW)
            st.protected_high = None
            st.last_flip_index = i
            self._emit(
                symbol=self.series.symbol, timeframe=self.series.timeframe,
                type=EventType.CHOCH, side=Side.BULLISH, at=at, bar_index=i, level=prot.price,
                swing_id=prot.id, trend_before=before, trend_after=Trend.BULLISH,
                gap_break=self._is_gap_break(i, prot.price, up=True), whipsaw=whip,
            )
        elif self.series.high[i] > prot.price and prot.id not in self._grabbed:
            self._emit(
                symbol=self.series.symbol, timeframe=self.series.timeframe,
                type=EventType.INTERNAL_LIQUIDITY_GRAB, side=Side.BEARISH, at=at,
                bar_index=i, level=prot.price, swing_id=prot.id, trend_before=before,
                trend_after=before, detail="wick above protected high, close below",
            )
            self._grabbed.add(prot.id)

    # -------------------------------------------------------------------- helpers

    def _reset_protected_on_bos(self, i: int, *, bullish: bool) -> None:
        """SPEC 6.4 vs 6.9 -- see ``structure.protected_on_bos`` and D-005.

        Under ``most_recent_low`` the level lands on the origin of the leg that broke
        structure, which may be BELOW the old protected low if a liquidity grab printed
        one.  Under ``ratchet_only`` it can only move toward price.
        """
        st = self.state
        kind = SwingKind.LOW if bullish else SwingKind.HIGH
        cand = self.swings.last_confirmed_by(i, kind)
        if cand is None:
            return
        cur = st.protected_low if bullish else st.protected_high
        if self.cfg.structure.protected_on_bos == "ratchet_only" and cur is not None:
            better = cand.price > cur.price if bullish else cand.price < cur.price
            if not better:
                return
        if bullish:
            st.protected_low = cand
        else:
            st.protected_high = cand

    def _is_whipsaw(self, i: int) -> bool:
        last = self.state.last_flip_index
        return last is not None and (i - last) < self.cfg.structure.min_bars_between_flips

    def _ratchet(self, i: int) -> None:
        """SPEC 6.4.  The protected level moves toward price, never away from it.

        In a bullish trend the protected low is replaced only by a *higher* confirmed
        swing low.  This is what makes CHoCH meaningful: without it, a deep pullback
        printing a lower swing low would move the level down and the reversal signal
        could never fire.
        """
        st = self.state
        if st.trend is Trend.BULLISH:
            cand = self.swings.last_confirmed_by(i, SwingKind.LOW)
            if cand is not None and (st.protected_low is None or cand.price > st.protected_low.price):
                st.protected_low = cand
        elif st.trend is Trend.BEARISH:
            cand = self.swings.last_confirmed_by(i, SwingKind.HIGH)
            if cand is not None and (st.protected_high is None or cand.price < st.protected_high.price):
                st.protected_high = cand

    # ---------------------------------------------------------------------- batch

    def run(self) -> StructureResult:
        for i in range(self.series.n):
            self.on_bar_close(i)
        return StructureResult(
            state=self.state,
            swings=self.swings,
            events=self.events,
            protected_changes=self.protected_changes,
        )


def analyse_structure(series: BarSeries, cfg: AppConfig) -> StructureResult:
    return StructureEngine(series, cfg).run()
