"""Standalone forward-return study of confirmed sweeps (SPEC 9.7).

This is the falsification test for **hypothesis H2** (`BACKTEST_PROTOCOL.md` §6.1):

> Confirmed sweeps carry directional information.
> *Falsified by:* the forward-return study showing no difference against matched
> controls.

It is deliberately **independent of the strategy**.  No CHoCH, no displacement, no
entry model, no stops — just: after a confirmed sweep, does price move in the direction
the sweep implies, more than it does at comparable bars where no sweep occurred?

If the answer is no, the strategy's foundation is unsupported, and SPEC 9.7 is explicit
about what to do: **report that, rather than adding filters until an edge appears.**

Matching (SPEC 9.7 asks for "the same session/volatility profile"):

* **Session stratum** — the H4 bar's UTC open hour.  Under DECISION D-001 the H4 grid is
  fixed at 00/04/08/12/16/20 UTC year-round, so the open hour *is* the session slot and
  needs no lookup.  It is exact and reproducible rather than approximately right.
* **Volatility stratum** — tercile of ``ATR_ref`` measured over the whole sample.

Controls are drawn from the same (hour, tercile) cell as each sweep, from bars with no
confirmed sweep, without replacement where possible.  Matching on the cell rather than
comparing against all bars matters: sweeps are not uniformly distributed across the
session or across volatility regimes, so an unmatched control is a comparison against a
different population.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from bot.core.bars import BarSeries, from_epoch_s
from bot.core.indicators import atr_ref
from bot.core.liquidity import Side
from bot.core.sweeps import SweepEvent
from bot.research.stats import bootstrap_diff_ci, terciles

DEFAULT_HORIZONS = (1, 3, 6, 12)


@dataclass
class HorizonResult:
    horizon: int
    n_sweep: int
    n_control: int
    sweep_mean: float
    sweep_median: float
    control_mean: float
    control_median: float
    diff: float
    ci_low: float
    ci_high: float

    @property
    def significant(self) -> bool:
        """The confidence interval on the difference excludes zero."""
        return self.ci_low > 0.0 or self.ci_high < 0.0


@dataclass
class SweepStudy:
    horizons: tuple[int, ...]
    results: list[HorizonResult] = field(default_factory=list)
    n_events: int = 0
    bootstrap: int = 5000
    seed: int = 20260825
    by_source: dict[str, dict[int, float]] = field(default_factory=dict)

    @property
    def any_significant(self) -> bool:
        return any(r.significant for r in self.results)

    def verdict(self) -> str:
        """The H2 reading, stated in the terms SPEC 9.7 uses."""
        if not self.results:
            return "NO DATA"
        if self.any_significant:
            return "SWEEPS CARRY DIRECTIONAL INFORMATION in this sample"
        return "NO MEASURABLE DIRECTIONAL EDGE — H2 unsupported in this sample"


def _direction(e: SweepEvent) -> int:
    """A sweep of SELL_SIDE liquidity implies UP; BUY_SIDE implies DOWN."""
    return 1 if e.side is Side.SELL_SIDE else -1


def forward_returns(
    series: BarSeries, bars: Sequence[int], dirs: Sequence[int], horizon: int, atr: np.ndarray
) -> np.ndarray:
    """ATR-normalised, direction-signed return ``horizon`` bars after each bar.

    Normalising by ``ATR_ref`` at the event bar is what makes returns comparable across
    volatility regimes and, later, across symbols -- the same reason every threshold in
    the specification is written in ATR multiples (SPEC 1.6).
    """
    out: list[float] = []
    n = series.n
    for b, d in zip(bars, dirs):
        j = b + horizon
        if j >= n:
            continue
        a = atr[b]
        if not np.isfinite(a) or a <= 0:
            continue
        out.append(d * (float(series.close[j]) - float(series.close[b])) / float(a))
    return np.asarray(out, dtype=np.float64)


def run_study(
    series: BarSeries,
    events: Sequence[SweepEvent],
    cfg,
    *,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    bootstrap: int = 5000,
    seed: int = 20260825,
    controls_per_event: int = 1,
) -> SweepStudy:
    """Compare forward returns after confirmed sweeps against matched control bars."""
    study = SweepStudy(horizons=horizons, bootstrap=bootstrap, seed=seed)
    if not events or series.n == 0:
        return study

    atr = atr_ref(series, cfg.atr.period)
    hours = np.array([from_epoch_s(t).hour for t in series.open_time], dtype=np.int64)
    terc = terciles(atr)

    sweep_bars = np.array([e.confirm_bar for e in events], dtype=np.int64)
    sweep_dirs = np.array([_direction(e) for e in events], dtype=np.int64)
    study.n_events = len(sweep_bars)

    swept = np.zeros(series.n, dtype=bool)
    swept[sweep_bars] = True

    # Match each sweep to control bars in the same (hour, volatility tercile) cell.
    rng = np.random.default_rng(seed)
    cells: dict[tuple[int, int], np.ndarray] = {}
    for h in np.unique(hours):
        for t in (0, 1, 2):
            sel = np.flatnonzero((hours == h) & (terc == t) & ~swept)
            if len(sel):
                cells[(int(h), int(t))] = sel

    ctrl_bars: list[int] = []
    ctrl_dirs: list[int] = []
    used: dict[tuple[int, int], set[int]] = {k: set() for k in cells}
    for b, d in zip(sweep_bars, sweep_dirs):
        key = (int(hours[b]), int(terc[b]))
        pool = cells.get(key)
        if pool is None or len(pool) == 0:
            continue
        for _ in range(controls_per_event):
            free = [x for x in pool if x not in used[key]]
            pick = int(rng.choice(free if free else pool))
            used[key].add(pick)
            ctrl_bars.append(pick)
            # Controls carry the same direction as the sweep they match, so the
            # comparison is like-for-like: a signed return against a signed baseline.
            ctrl_dirs.append(int(d))

    ctrl_bars_a = np.asarray(ctrl_bars, dtype=np.int64)
    ctrl_dirs_a = np.asarray(ctrl_dirs, dtype=np.int64)

    for k in horizons:
        s = forward_returns(series, sweep_bars, sweep_dirs, k, atr)
        c = forward_returns(series, ctrl_bars_a, ctrl_dirs_a, k, atr)
        if len(s) == 0 or len(c) == 0:
            continue
        lo, hi = bootstrap_diff_ci(s, c, bootstrap, rng)
        study.results.append(
            HorizonResult(
                horizon=k,
                n_sweep=len(s),
                n_control=len(c),
                sweep_mean=float(s.mean()),
                sweep_median=float(np.median(s)),
                control_mean=float(c.mean()),
                control_median=float(np.median(c)),
                diff=float(s.mean() - c.mean()),
                ci_low=lo,
                ci_high=hi,
            )
        )

    # Per-source means, so a source that does carry information is not hidden inside an
    # aggregate dominated by SESSION levels (D-006: they are 61% of the population).
    sources = sorted({e.level_source.value for e in events})
    for src in sources:
        sub = [e for e in events if e.level_source.value == src]
        bars = np.array([e.confirm_bar for e in sub], dtype=np.int64)
        dirs = np.array([_direction(e) for e in sub], dtype=np.int64)
        study.by_source[src] = {}
        for k in horizons:
            r = forward_returns(series, bars, dirs, k, atr)
            study.by_source[src][k] = float(r.mean()) if len(r) else float("nan")
    return study
