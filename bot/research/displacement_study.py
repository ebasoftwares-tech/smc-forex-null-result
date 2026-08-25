"""Displacement threshold distribution and rejection rates (SPEC 10.6).

The Phase 8 gate is not "does the code run" — it is:

> Distribution of ``net/ATR`` for all post-sweep legs, with the threshold marked. **If
> the default 1.5 sits in the middle of a smooth unimodal distribution, it is an
> arbitrary cut and should be reported as such rather than defended.**

and

> A threshold that never rejects anything is not a filter, and the ablation must show
> the rejection rate per setting. (SPEC 10.4)

So this module measures the filter, not the strategy.  For every confirmed sweep it
walks every candidate break bar in the CHoCH window, builds the leg SPEC 10.1 defines,
and records every component — whether or not the leg passes.  Nothing here decides a
trade; Phase 9 does that.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from bot.config.schema import AppConfig
from bot.core.bars import BarSeries
from bot.core.displacement import (
    Direction,
    DisplacementReason,
    evaluate_leg,
    leg_origin,
)
from bot.core.fvg import Fvg
from bot.core.indicators import atr_ref
from bot.core.liquidity import Side
from bot.core.sweeps import SweepEvent

#: Mirrors ``choch.max_bars_after_sweep`` (SPEC 11.4), which is Phase 9 config.  Passed
#: as an argument rather than read from config so Phase 8 does not reach into a phase
#: that does not exist yet.
DEFAULT_WINDOW = 12

#: SPEC 10.6's declared ablation grid.
THRESHOLD_GRID = (0.0, 1.0, 1.25, 1.5, 2.0, 2.5)


@dataclass
class LegSample:
    net_atr: float
    body_ratio: float
    dir_bars: int
    fvg_count: int
    leg_bars: int
    bars_after_sweep: int
    confirmed: bool
    reasons: tuple[str, ...]
    spans_gap: bool


@dataclass
class DisplacementStudy:
    samples: list[LegSample] = field(default_factory=list)
    n_sweeps: int = 0
    window: int = DEFAULT_WINDOW

    # ------------------------------------------------------------------ summary

    @property
    def net_atr(self) -> np.ndarray:
        return np.array([s.net_atr for s in self.samples], dtype=float)

    def percentiles(self, qs: Sequence[float] = (5, 25, 50, 75, 90, 95, 99)) -> dict[float, float]:
        v = self.net_atr
        if not len(v):
            return {}
        return {q: float(np.percentile(v, q)) for q in qs}

    def histogram(self, edges: Sequence[float]) -> list[tuple[str, int, float]]:
        v = self.net_atr
        if not len(v):
            return []
        counts, _ = np.histogram(v, bins=list(edges) + [np.inf])
        total = counts.sum()
        labels = [f"{edges[i]:.2f}–{edges[i+1]:.2f}" for i in range(len(edges) - 1)]
        labels.append(f"{edges[-1]:.2f}+")
        return [(labels[i], int(counts[i]), float(counts[i] / total)) for i in range(len(counts))]

    def rejection_by_threshold(self, grid: Sequence[float] = THRESHOLD_GRID) -> dict[float, float]:
        """Share of legs rejected on ``net`` alone, per ``disp.min_leg_atr`` setting.

        Isolated from the other four conditions on purpose: a rejection rate that mixes
        them cannot say whether the threshold is doing any work.
        """
        v = self.net_atr
        if not len(v):
            return {}
        return {float(t): float((v < t).mean()) for t in grid}

    def rejection_by_reason(self) -> dict[str, float]:
        """Share of legs each condition rejects, counted independently.

        Conditions overlap — a weak leg usually fails several — so these do not sum to
        the overall rejection rate, and that is the point: it shows which condition is
        load-bearing and which is along for the ride.
        """
        if not self.samples:
            return {}
        n = len(self.samples)
        c: Counter = Counter()
        for s in self.samples:
            for r in s.reasons:
                c[r] += 1
        return {k: v / n for k, v in sorted(c.items(), key=lambda kv: -kv[1])}

    def pass_rate(self) -> float:
        if not self.samples:
            return 0.0
        return sum(1 for s in self.samples if s.confirmed) / len(self.samples)

    def has_natural_break(self, threshold: float, width: float = 0.5, bins: int = 24) -> bool:
        """Is there any structure in the distribution near ``threshold``?

        A shoulder, a gap or a local minimum would mark the cut as *discovered*.  A
        density that simply decays through it marks the cut as *chosen*.

        The increase must clear **2x the Poisson noise** of the preceding bin.  Without
        that guard the test fires on any bin-to-bin wobble: on the fixture the largest
        rise near 1.5 is +11 counts against a standard deviation of 18, i.e. 0.6 sigma,
        which is nothing at all — and it was enough to report a smooth decay as
        structure until the histogram was actually looked at.
        """
        v = self.net_atr
        if len(v) < 100:
            return False
        counts, edges = np.histogram(v, bins=bins, range=(0.0, float(np.percentile(v, 99))))
        centres = (edges[:-1] + edges[1:]) / 2
        near = np.flatnonzero(np.abs(centres - threshold) <= width)
        if len(near) < 3:
            return False
        seg = counts[near].astype(float)
        rises = np.diff(seg)
        noise = 2.0 * np.sqrt(np.maximum(seg[:-1], 1.0))
        return bool(np.any(rises > noise))

    def unimodal_verdict(self, threshold: float) -> str:
        """SPEC 10.6's actual question, answered rather than dodged.

        The question is not only *where* the cut sits but whether the data marks it as
        special.  A percentile alone cannot say that: every threshold sits at some
        percentile.  What distinguishes a discovered cut from a chosen one is structure
        in the distribution around it.
        """
        v = self.net_atr
        if len(v) < 100:
            return "INSUFFICIENT SAMPLE"
        pct = float((v < threshold).mean())
        if self.has_natural_break(threshold):
            return (
                f"STRUCTURED — the {threshold} cut rejects {pct:.0%} of legs and the "
                "distribution is non-monotonic around it, so the data marks it out"
            )
        return (
            f"ARBITRARY — the {threshold} cut rejects {pct:.0%} of legs, but the density "
            "decays smoothly through it with no shoulder or gap. Nothing in the data "
            "marks 1.5 as special; it is a choice, not a discovery, which is why it is "
            "TUNABLE under a plateau requirement (BACKTEST_PROTOCOL 5.5)"
        )


def run_study(
    series: BarSeries,
    sweeps: Sequence[SweepEvent],
    cfg: AppConfig,
    *,
    window: int = DEFAULT_WINDOW,
    fvgs: list[Fvg] | None = None,
    min_bars_after_sweep: int = 1,
) -> DisplacementStudy:
    """Evaluate every candidate displacement leg following every confirmed sweep.

    ``min_bars_after_sweep = 1`` enforces SPEC 9.6's WAIT: the bar that confirms the
    sweep can never also be the break bar, so it is not a candidate here either.
    """
    study = DisplacementStudy(window=window, n_sweeps=len(sweeps))
    if not sweeps or series.n == 0:
        return study

    atr = atr_ref(series, cfg.atr.period)
    if fvgs is None:
        from bot.core.fvg import detect_fvgs

        fvgs = detect_fvgs(series, cfg)

    for e in sweeps:
        direction = Direction.BULLISH if e.side is Side.SELL_SIDE else Direction.BEARISH
        first = e.confirm_bar + min_bars_after_sweep
        for b in range(first, min(e.confirm_bar + window + 1, series.n)):
            a = leg_origin(e.sweep_extreme_bar, b, cfg)
            r = evaluate_leg(series, a, b, direction, cfg, fvgs, atr)
            if r.failed_on is DisplacementReason.NO_ATR:
                continue
            study.samples.append(
                LegSample(
                    net_atr=r.net_atr,
                    body_ratio=r.body_ratio,
                    dir_bars=r.dir_bars,
                    fvg_count=r.fvg_count,
                    leg_bars=r.leg_bars,
                    bars_after_sweep=b - e.confirm_bar,
                    confirmed=r.confirmed,
                    reasons=tuple(x.value for x in r.reasons),
                    spans_gap=r.spans_gap,
                )
            )
    return study


def joint_ablation(
    series: BarSeries,
    sweeps: Sequence[SweepEvent],
    cfg: AppConfig,
    *,
    window: int = DEFAULT_WINDOW,
    grid: Sequence[float] = THRESHOLD_GRID,
) -> dict[bool, dict[float, float]]:
    """SPEC 10.6: ``min_leg_atr`` x ``require_fvg``, because they are partially redundant.

    §10.2 argues the FVG requirement *is* the displacement condition expressed
    structurally.  If that is right, the two should overlap heavily and the joint table
    should show the FVG requirement adding little once the net threshold is already
    strict — testing them one at a time would credit each with the other's work.
    """
    from bot.core.fvg import detect_fvgs

    out: dict[bool, dict[float, float]] = {}
    for require_fvg in (False, True):
        c = cfg.model_copy(update={"disp": cfg.disp.model_copy(update={"require_fvg": require_fvg})})
        fvgs = detect_fvgs(series, c)
        row: dict[float, float] = {}
        for t in grid:
            cc = c.model_copy(
                update={"disp": c.disp.model_copy(update={"min_leg_atr": float(t)})}
            )
            st = run_study(series, sweeps, cc, window=window, fvgs=fvgs)
            row[float(t)] = st.pass_rate()
        out[require_fvg] = row
    return out
