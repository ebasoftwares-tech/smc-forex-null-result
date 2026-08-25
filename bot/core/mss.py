"""CHoCH reference selection and MSS confirmation (SPEC 11).

This is the module the whole project has been building toward.  Everything before it
produces *candidates*; this is where a candidate becomes the tradable event, and its
output is the funnel that decides whether the design survives at all (SPEC 11.7).

Four things here are load-bearing, and three of them are places where the specification
says two things.

**1. MSS is the subset, CHoCH is the superset, and both are recorded.**  A reference
break inside the window is a CHoCH whether or not it displaces.  SPEC 6.9 asks for the
forward returns of *CHoCH-that-were-not-MSS* precisely so that "does the sweep and
displacement requirement add anything" is answerable -- which it is not if the failures
are dropped.  So a failed clause downgrades a candidate to ``CHOCH_NOT_MSS`` with the
clause named; it does not erase it.

**2. The WAIT and knowability are two different constraints.**  SPEC 11.5 measures the
window from the sweep *extreme* bar ``s``, but a sweep is not knowable until its
*confirm* bar, which is up to ``sweep.max_confirmation_bars`` later.  Both bind, and
enforcing only the first would let a break be judged against a sweep that had not yet
happened as far as any live engine was concerned.  ``sweep.same_bar_choch_allowed``
(FROZEN false) then pushes the earliest admissible break one bar past confirmation.

**3. SPEC 11.5 and SPEC 11.6 disagree about what invalidation means, and the
disagreement is resolved in favour of measurement.**  11.5 lists "no new extreme" and
"no opposing sweep" as *clauses evaluated at the break bar*; 11.6 lists the same two as
things that *invalidate the setup* when they occur.  Under the second reading a setup
that made a new extreme and then broke its reference is never recorded at all, and the
CHoCH-not-MSS population -- the thing item 1 exists to preserve -- loses exactly the
cases that most need explaining.  Both conditions are therefore tracked as **sticky
flags over ``(s, b]``** and read as clauses at the break bar, which satisfies 11.5
literally, and they surface as terminal outcomes when no break ever comes, which
satisfies 11.6.  See D-009.

**4. SPEC 6.6 carries a fourth MSS clause that SPEC 11.5 omits while calling itself
complete** -- that the swept level lie beyond the extreme of the leg producing the
CHoCH.  11.5 is operative here, being the more specific section and the one that claims
completeness; the 6.6 clause is evaluated anyway and reported as a diagnostic, so the
cost of adopting it is a number rather than an argument.  See D-009.

The MTF gate (SPEC 11.5's last clause) needs the bias engine of SPEC 7, which is
Phase 2-4 and not built.  It is injected as a predicate defaulting to "always passes",
which is exactly ``bias.gate_mode = none`` -- the scientific control SPEC 7.5 says MUST
be run.  Every count this module produces is therefore an **upper bound**: a real gate
can only remove MSS events, never add them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, Sequence

import numpy as np

from bot.config.schema import AppConfig
from bot.core.bars import BarSeries, from_epoch_s
from bot.core.displacement import Direction, DisplacementResult, evaluate, leg_origin
from bot.core.fvg import Fvg
from bot.core.indicators import atr_ref
from bot.core.liquidity import Side
from bot.core.structure import breaks_level
from bot.core.swings import (
    Swing,
    SwingKind,
    SwingStore,
    is_swing_high,
    is_swing_low,
    swing_prices,
)
from bot.core.sweeps import SweepEvent


class ReferenceMode(str, Enum):
    MAJOR = "major"
    MICRO = "micro"


class Outcome(str, Enum):
    """Terminal state of one setup candidate.  Every candidate reaches exactly one."""

    MSS_CONFIRMED = "MSS_CONFIRMED"
    CHOCH_NOT_MSS = "CHOCH_NOT_MSS"
    CHOCH_TIMEOUT = "CHOCH_TIMEOUT"
    NEW_EXTREME = "NEW_EXTREME"
    OPPOSING_SWEEP = "OPPOSING_SWEEP"
    NO_CHOCH_REFERENCE = "NO_CHOCH_REFERENCE"
    REFERENCE_TOO_FAR = "REFERENCE_TOO_FAR"
    NO_WINDOW = "NO_WINDOW"


class Clause(str, Enum):
    """The SPEC 11.5 conditions, named so a failure can say which one."""

    DISPLACEMENT = "DISPLACEMENT"
    NEW_EXTREME = "NEW_EXTREME"
    OPPOSING_SWEEP = "OPPOSING_SWEEP"
    MTF_GATE = "MTF_GATE"


#: SPEC 11.5's last clause, before the bias engine of SPEC 7 exists.  Signature is
#: ``(direction, bar_index) -> bool`` so Phase 2-4 can drop a real gate in unchanged.
MtfGate = Callable[[Direction, int], bool]


def _pass(direction: Direction, bar_index: int) -> bool:  # noqa: ARG001
    return True


@dataclass(frozen=True)
class MicroSwing:
    """A pullback swing detected at ``choch.micro_fractal_n`` (SPEC 11.1, micro mode).

    Deliberately *not* run through ``SwingStore``: normalisation would merge a pullback
    into a more extreme neighbour, and micro mode wants the **first** pullback after the
    sweep, not the most extreme one.
    """

    kind: SwingKind
    price: float
    formed_index: int
    confirmed_index: int


@dataclass(frozen=True)
class SetupCandidate:
    """One confirmed sweep, followed to its terminal outcome.

    Carries every component of the decision rather than the verdict alone, so the
    funnel can be broken down by source, tier and session without re-running anything.
    """

    id: str
    symbol: str
    timeframe: str
    direction: Direction
    reference_mode: ReferenceMode
    sweep: SweepEvent
    sweep_extreme_bar: int
    window_first_bar: int
    window_last_bar: int
    outcome: Outcome
    failed_clauses: tuple[Clause, ...] = ()

    reference_price: float | None = None
    reference_id: str | None = None
    reference_formed_index: int | None = None
    reference_distance_atr: float | None = None

    choch_bar: int | None = None
    choch_at: datetime | None = None
    bars_sweep_to_choch: int | None = None
    displacement: DisplacementResult | None = None
    new_extreme_bar: int | None = None
    opposing_sweep_bar: int | None = None
    level_beyond_leg: bool | None = None
    gap_break: bool = False

    @property
    def is_mss(self) -> bool:
        return self.outcome is Outcome.MSS_CONFIRMED

    @property
    def is_choch(self) -> bool:
        """Reference broken inside the window -- the SPEC 6.6 superset."""
        return self.choch_bar is not None

    @property
    def reference_found(self) -> bool:
        return self.reference_price is not None


@dataclass
class MssResult:
    symbol: str
    timeframe: str
    reference_mode: ReferenceMode
    candidates: list[SetupCandidate] = field(default_factory=list)

    @property
    def mss(self) -> list[SetupCandidate]:
        return [c for c in self.candidates if c.is_mss]

    @property
    def choch(self) -> list[SetupCandidate]:
        return [c for c in self.candidates if c.is_choch]

    @property
    def choch_not_mss(self) -> list[SetupCandidate]:
        """SPEC 6.9's comparison population: structure changed, but not our way."""
        return [c for c in self.candidates if c.is_choch and not c.is_mss]

    def funnel(self) -> dict[str, int]:
        """SPEC 11.7's stages, from confirmed sweep onward."""
        return {
            "confirmed_sweeps": len(self.candidates),
            "reached_window": sum(
                1 for c in self.candidates if c.outcome is not Outcome.NO_WINDOW
            ),
            "reference_found": sum(1 for c in self.candidates if c.reference_found),
            "choch": len(self.choch),
            "mss": len(self.mss),
        }

    def outcomes(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in self.candidates:
            out[c.outcome.value] = out.get(c.outcome.value, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    def clause_failures(self) -> dict[str, int]:
        """How often each SPEC 11.5 clause is what stopped a CHoCH becoming an MSS.

        Counted independently, so they overlap and do not sum to the CHoCH-not-MSS
        total -- the same convention as the Phase 8 rejection table, and for the same
        reason: the question is which clause binds, not how the failures partition.
        """
        out: dict[str, int] = {}
        for c in self.choch_not_mss:
            for cl in c.failed_clauses:
                out[cl.value] = out.get(cl.value, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    def bars_to_mss(self) -> list[int]:
        return [c.bars_sweep_to_choch for c in self.mss if c.bars_sweep_to_choch is not None]


# --------------------------------------------------------------------- micro swings


def detect_micro_swings(series: BarSeries, cfg: AppConfig) -> list[MicroSwing]:
    """Every ``micro_fractal_n`` fractal on the series, in confirmation order.

    Confirmation lag is ``n`` bars, the same non-repainting contract as SPEC 5.2: a
    fractal centred on bar ``f`` is not knowable until ``f + n`` closes.
    """
    n = cfg.choch.micro_fractal_n
    highs, lows = swing_prices(series, cfg.swing.price_source)
    out: list[MicroSwing] = []
    for f in range(n, series.n - n):
        if is_swing_high(highs, f, n, cfg.swing.tie_rule):
            out.append(MicroSwing(SwingKind.HIGH, float(highs[f]), f, f + n))
        if is_swing_low(lows, f, n, cfg.swing.tie_rule):
            out.append(MicroSwing(SwingKind.LOW, float(lows[f]), f, f + n))
    out.sort(key=lambda m: (m.confirmed_index, m.formed_index))
    return out


# --------------------------------------------------------------------------- engine


class MssEngine:
    """One pass per confirmed sweep.  Each pass walks forward only, and reads no bar
    beyond the one it is deciding at."""

    def __init__(
        self,
        series: BarSeries,
        cfg: AppConfig,
        sweeps: Sequence[SweepEvent],
        *,
        swings: SwingStore,
        fvgs: Sequence[Fvg] | None = None,
        atr: np.ndarray | None = None,
        gate: MtfGate | None = None,
        reference_mode: ReferenceMode | str | None = None,
    ) -> None:
        self.series = series
        self.cfg = cfg
        self.swings = swings
        self.fvgs = list(fvgs or [])
        self.atr = atr_ref(series, cfg.atr.period) if atr is None else atr
        self.gate = gate or _pass
        self.mode = ReferenceMode(reference_mode or cfg.choch.reference_mode)
        self.sweeps = sorted(sweeps, key=lambda s: (s.confirm_bar, s.id))
        self._micro = (
            detect_micro_swings(series, cfg) if self.mode is ReferenceMode.MICRO else []
        )
        # Confirmed sweeps indexed by the bar they became knowable on, for the
        # opposing-sweep clause.  confirm_bar, not trigger_bar: a sweep nobody could
        # yet see cannot invalidate anything.
        self._by_confirm: dict[int, list[SweepEvent]] = {}
        for sw in self.sweeps:
            self._by_confirm.setdefault(sw.confirm_bar, []).append(sw)
        self._seq = 0

    # ------------------------------------------------------------------- references

    def _major_reference(self, s: int, direction: Direction) -> Swing | None:
        """SPEC 11.1, ``major``: the last *unbroken* swing before the sweep.

        Selected from the swings a live engine held at bar ``s`` (see
        ``SwingStore.visible_at``), never from the finished store, which would have
        dropped any swing later superseded by normalisation.
        """
        kind = SwingKind.HIGH if direction is Direction.BULLISH else SwingKind.LOW
        lookback = self.cfg.choch.max_reference_lookback
        cands = [
            sw
            for sw in self.swings.visible_at(s, kind)
            if sw.formed_index < s and (s - sw.formed_index) <= lookback
        ]
        h, l = self.series.high, self.series.low
        for sw in reversed(cands):
            span_lo, span_hi = sw.formed_index + 1, s + 1
            if span_lo >= span_hi:
                return sw  # formed on the bar before the sweep: nothing to break it yet
            if direction is Direction.BULLISH:
                if float(h[span_lo:span_hi].max()) <= sw.price:
                    return sw
            elif float(l[span_lo:span_hi].min()) >= sw.price:
                return sw
        return None

    def _micro_reference(self, s: int, b: int, direction: Direction) -> MicroSwing | None:
        """SPEC 11.1, ``micro``: the FIRST pullback swing formed after the sweep and
        confirmed by bar ``b``.  Once it exists it is fixed -- "first" is not re-read
        as the window advances."""
        kind = SwingKind.HIGH if direction is Direction.BULLISH else SwingKind.LOW
        for m in self._micro:
            if m.kind is kind and m.formed_index > s and m.confirmed_index <= b:
                return m
        return None

    # ------------------------------------------------------------------------ walk

    def _mk_id(self) -> str:
        self._seq += 1
        return f"{self.series.symbol}:{self.series.timeframe}:SETUP:{self._seq:05d}"

    def evaluate_sweep(self, sw: SweepEvent) -> SetupCandidate:
        cfg, series = self.cfg, self.series
        s = sw.sweep_extreme_bar
        direction = Direction.BULLISH if sw.side is Side.SELL_SIDE else Direction.BEARISH
        bullish = direction is Direction.BULLISH

        # The window.  Two independent lower bounds (module docstring, item 2): the
        # WAIT measured from the sweep extreme, and knowability measured from the
        # sweep confirm bar.
        wait_floor = s + cfg.choch.min_bars_after_sweep
        known_floor = sw.confirm_bar + (0 if cfg.sweep.same_bar_choch_allowed else 1)
        first_bar = max(wait_floor, known_floor)
        last_bar = min(s + cfg.choch.max_bars_after_sweep, series.n - 1)

        base = dict(
            id=self._mk_id(),
            symbol=series.symbol,
            timeframe=series.timeframe,
            direction=direction,
            reference_mode=self.mode,
            sweep=sw,
            sweep_extreme_bar=s,
            window_first_bar=first_bar,
            window_last_bar=last_bar,
        )
        if first_bar > last_bar:
            # The series ended, or confirmation ate the whole window.  Not a finding
            # about the market -- a right-censored candidate, and counted as such.
            return SetupCandidate(outcome=Outcome.NO_WINDOW, **base)

        # ATR for both tolerances is taken at the sweep extreme bar and held fixed for
        # the life of the candidate.  A tolerance that moved with the window could let
        # an already-violated setup read as intact again on a quieter bar.
        a_s = self.atr[s]
        if not np.isfinite(a_s) or a_s <= 0:
            return SetupCandidate(outcome=Outcome.NO_WINDOW, **base)
        a_s = float(a_s)

        reference_price: float | None = None
        reference_id: str | None = None
        reference_formed: int | None = None
        if self.mode is ReferenceMode.MAJOR:
            ref = self._major_reference(s, direction)
            if ref is None:
                return SetupCandidate(outcome=Outcome.NO_CHOCH_REFERENCE, **base)
            dist = abs(ref.price - sw.sweep_extreme) / a_s
            if dist > cfg.choch.max_reference_distance_atr:
                return SetupCandidate(
                    outcome=Outcome.REFERENCE_TOO_FAR,
                    reference_price=ref.price,
                    reference_id=ref.id,
                    reference_formed_index=ref.formed_index,
                    reference_distance_atr=dist,
                    **base,
                )
            reference_price = ref.price
            reference_id = ref.id
            reference_formed = ref.formed_index

        tol = cfg.invalidate.new_extreme_atr * a_s
        new_extreme_bar: int | None = None
        opposing_bar: int | None = None
        ref_dist: float | None = (
            abs(reference_price - sw.sweep_extreme) / a_s
            if reference_price is not None
            else None
        )

        for b in range(s + 1, last_bar + 1):
            # Sticky over (s, b], read at the break bar as SPEC 11.5 clauses.
            if new_extreme_bar is None:
                broke_extreme = (
                    series.low[b] < sw.sweep_extreme - tol
                    if bullish
                    else series.high[b] > sw.sweep_extreme + tol
                )
                if broke_extreme:
                    new_extreme_bar = b
            if opposing_bar is None:
                for other in self._by_confirm.get(b, ()):
                    if other.id != sw.id and other.side is not sw.side:
                        opposing_bar = b
                        break

            if b < first_bar:
                continue

            if self.mode is ReferenceMode.MICRO and reference_price is None:
                m = self._micro_reference(s, b, direction)
                if m is None:
                    continue
                dist = abs(m.price - sw.sweep_extreme) / a_s
                if dist > cfg.choch.max_reference_distance_atr:
                    return SetupCandidate(
                        outcome=Outcome.REFERENCE_TOO_FAR,
                        reference_price=m.price,
                        reference_formed_index=m.formed_index,
                        reference_distance_atr=dist,
                        new_extreme_bar=new_extreme_bar,
                        opposing_sweep_bar=opposing_bar,
                        **base,
                    )
                reference_price = m.price
                reference_formed = m.formed_index
                ref_dist = dist

            if reference_price is None:
                continue
            if not breaks_level(
                series, b, reference_price, up=bullish, cfg=cfg, atr_value=self.atr[b]
            ):
                continue

            # --- CHoCH.  Everything from here decides only whether it is also an MSS.
            disp = evaluate(series, s, b, direction, cfg, self.fvgs, self.atr)
            failed: list[Clause] = []
            if not disp.confirmed:
                failed.append(Clause.DISPLACEMENT)
            if new_extreme_bar is not None:
                failed.append(Clause.NEW_EXTREME)
            if opposing_bar is not None:
                failed.append(Clause.OPPOSING_SWEEP)
            if not self.gate(direction, b):
                failed.append(Clause.MTF_GATE)

            a = leg_origin(s, b, cfg)
            leg_extreme = (
                float(series.low[a : b + 1].min())
                if bullish
                else float(series.high[a : b + 1].max())
            )
            beyond = sw.level_price >= leg_extreme if bullish else sw.level_price <= leg_extreme
            prev_close = series.close[b - 1] if b > 0 else series.close[b]
            gap_break = bool(
                prev_close <= reference_price < series.open[b]
                if bullish
                else prev_close >= reference_price > series.open[b]
            )

            return SetupCandidate(
                outcome=Outcome.MSS_CONFIRMED if not failed else Outcome.CHOCH_NOT_MSS,
                failed_clauses=tuple(failed),
                reference_price=reference_price,
                reference_id=reference_id,
                reference_formed_index=reference_formed,
                reference_distance_atr=ref_dist,
                choch_bar=b,
                choch_at=from_epoch_s(series.close_time[b]),
                bars_sweep_to_choch=b - s,
                displacement=disp,
                new_extreme_bar=new_extreme_bar,
                opposing_sweep_bar=opposing_bar,
                level_beyond_leg=beyond,
                gap_break=gap_break,
                **base,
            )

        # No break inside the window.  SPEC 11.6's terminal reasons, most specific first.
        if reference_price is None and self.mode is ReferenceMode.MICRO:
            outcome = Outcome.NO_CHOCH_REFERENCE
        elif new_extreme_bar is not None:
            outcome = Outcome.NEW_EXTREME
        elif opposing_bar is not None:
            outcome = Outcome.OPPOSING_SWEEP
        else:
            outcome = Outcome.CHOCH_TIMEOUT
        return SetupCandidate(
            outcome=outcome,
            reference_price=reference_price,
            reference_id=reference_id,
            reference_formed_index=reference_formed,
            reference_distance_atr=ref_dist,
            new_extreme_bar=new_extreme_bar,
            opposing_sweep_bar=opposing_bar,
            **base,
        )

    def run(self) -> MssResult:
        res = MssResult(self.series.symbol, self.series.timeframe, self.mode)
        for sw in self.sweeps:
            res.candidates.append(self.evaluate_sweep(sw))
        return res


def analyse_mss(
    series: BarSeries,
    cfg: AppConfig,
    sweeps: Sequence[SweepEvent],
    *,
    swings: SwingStore,
    fvgs: Sequence[Fvg] | None = None,
    atr: np.ndarray | None = None,
    gate: MtfGate | None = None,
    reference_mode: ReferenceMode | str | None = None,
) -> MssResult:
    """Follow every confirmed sweep to its terminal outcome (SPEC 11)."""
    return MssEngine(
        series,
        cfg,
        sweeps,
        swings=swings,
        fvgs=fvgs,
        atr=atr,
        gate=gate,
        reference_mode=reference_mode,
    ).run()
