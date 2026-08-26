"""Standalone FVG edge test (SPEC 12.6) — Phase 10's gate.

> *"Return from touching an unmitigated FVG in the direction of the gap, versus a matched
> control. This tests the FVG concept **independently of the strategy**, so a null result
> there localises the failure precisely."*

That independence is the point. If price returning into a fair value gap carries no
directional information, then `disp.require_fvg` is filtering setups on a coin flip, and
knowing that *before* the entry engine is built localises the failure to the concept
rather than to the machinery around it.

The shape is `sweep_study.py`'s, deliberately: touch bars against matched control bars in
the same session slot and volatility tercile. Same statistical primitives
(`bot/research/stats.py`), so the two studies are comparable to each other, which is what
`BACKTEST_PROTOCOL.md` §6 needs them to be.

Four things this module gets right on purpose, three of them lessons already paid for:

* **One touch bar is one observation.** A forward return is a function of
  ``(bar, direction)`` and nothing else, so two gaps touched by the same bar in the same
  direction contribute the identical number twice. That is D-010 §3, found the hard way
  in the H5 study, and it applies here unchanged — one bar routinely tags several stacked
  gaps.
* **Only the FIRST touch counts.** After that the gap is PARTIAL or MITIGATED, and SPEC
  12.6 asks about touching an *unmitigated* gap. Counting later re-entries would measure
  a different object and would multiply the sample with correlated rows.
* **The verdict is three-way** (`stats.Verdict`). "No edge" is a claim that needs the
  interval inside a declared margin, not merely containing zero.
* **Controls carry the direction of the gap they match**, so a signed return is compared
  against a signed baseline rather than against zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from bot.config.schema import AppConfig
from bot.core.bars import BarSeries, from_epoch_s
from bot.core.fvg import Fvg, FvgBook, FvgDirection
from bot.core.indicators import atr_ref
from bot.research.stats import (
    ALPHA,
    Group,
    Verdict,
    benjamini_hochberg,
    bootstrap_diff_ci,
    minimum_detectable_effect,
    permutation_p,
    required_n,
    terciles,
    verdict_for,
)

#: SPEC 12.6 does not name horizons; these mirror `sweep_study.py` so the FVG result and
#: the sweep result can be read against each other.
DEFAULT_HORIZONS = (1, 3, 6, 12)

#: The largest mean difference, in ATR units, that still counts as "touching a gap tells
#: you nothing tradable".
#:
#: **Declared here, before any result is read** (`BACKTEST_PROTOCOL.md` §10.2). Set to
#: match `marginal_value.EQUIVALENCE_MARGIN_ATR` so the two falsification studies answer
#: at the same resolution -- a component judged decoration by one and meaningful by the
#: other, purely because they used different margins, would be an artefact of this file.
EQUIVALENCE_MARGIN_ATR = 0.25


@dataclass(frozen=True)
class TouchEvent:
    """The first time price came back into a gap that was still unmitigated."""

    bar: int
    direction: FvgDirection
    hour: int
    tercile: int
    size_atr: float
    age_bars: int

    @property
    def sign(self) -> int:
        return 1 if self.direction is FvgDirection.BULLISH else -1


@dataclass(frozen=True)
class HorizonResult:
    horizon: int
    touch: Group
    control: Group
    diff: float
    ci_low: float
    ci_high: float
    p_value: float
    mde: float
    required_touch_n: float
    margin: float = EQUIVALENCE_MARGIN_ATR
    p_adjusted: float | None = None

    @property
    def verdict(self) -> Verdict:
        return verdict_for(
            self.ci_low, self.ci_high, self.margin, self.touch.n, self.control.n
        )


@dataclass
class FvgStudy:
    horizons: tuple[int, ...]
    results: list[HorizonResult] = field(default_factory=list)
    n_gaps: int = 0
    n_touches: int = 0
    n_raw_touches: int = 0
    bootstrap: int = 5000
    permutations: int = 5000
    seed: int = 20260825

    @property
    def any_significant(self) -> bool:
        return any(r.verdict is Verdict.DIFFERENT for r in self.results)

    def verdict(self) -> Verdict:
        """Conservative in the direction that matters.

        One DIFFERENT horizon keeps the concept alive; EQUIVALENT is only reported when
        *every* horizon is equivalent. Declaring the FVG concept dead on one horizon out
        of four, while the others could not resolve anything, is not a finding.
        """
        if not self.results:
            return Verdict.NO_DATA
        if self.any_significant:
            return Verdict.DIFFERENT
        if all(r.verdict is Verdict.EQUIVALENT for r in self.results):
            return Verdict.EQUIVALENT
        return Verdict.UNDERPOWERED

    def headline(self) -> str:
        v = self.verdict()
        if v is Verdict.DIFFERENT:
            return "FVG TOUCHES CARRY DIRECTIONAL INFORMATION in this sample"
        if v is Verdict.EQUIVALENT:
            return (
                "NO EDGE -- touching an unmitigated FVG is indistinguishable from a "
                "matched control, within the declared margin"
            )
        if v is Verdict.NO_DATA:
            return "NO DATA"
        return (
            "UNDECIDED -- this sample cannot resolve the declared margin. NOT a null "
            "result, and must not be reported as one"
        )


# ------------------------------------------------------------------ the sample


def touch_events(
    series: BarSeries, book: FvgBook, atr: np.ndarray
) -> tuple[list[TouchEvent], int]:
    """First-touch events, one per ``(bar, direction)``.

    Returns ``(events, raw_count)`` so the report can state how much collapsing the
    dedup did -- a number worth showing rather than asserting, since it is the difference
    between the sample size the study has and the one it appears to have.
    """
    terc = terciles(atr)
    raw = [f for f in book.fvgs if f.first_touch_index is not None]
    best: dict[tuple[int, FvgDirection], Fvg] = {}
    for f in raw:
        key = (f.first_touch_index, f.direction)
        cur = best.get(key)
        # Ties resolve to the larger gap: an arbitrary but fixed rule, and the only
        # field of the collapsed rows that is not already identical.
        if cur is None or f.size_atr > cur.size_atr:
            best[key] = f
    events = [
        TouchEvent(
            bar=f.first_touch_index,
            direction=f.direction,
            hour=from_epoch_s(series.open_time[f.first_touch_index]).hour,
            tercile=int(terc[f.first_touch_index]),
            size_atr=f.size_atr,
            age_bars=f.first_touch_index - f.confirmed_index,
        )
        for f in best.values()
    ]
    events.sort(key=lambda e: (e.bar, e.direction.value))
    return events, len(raw)


def forward_returns(
    series: BarSeries,
    bars: Sequence[int],
    signs: Sequence[int],
    horizon: int,
    atr: np.ndarray,
) -> np.ndarray:
    """ATR-normalised, direction-signed return ``horizon`` bars after each bar.

    Anchored on the **close of the touch bar**: the touch is only knowable once that bar
    closes, so an earlier anchor would credit the study with information no live system
    had.
    """
    out: list[float] = []
    for b, sgn in zip(bars, signs):
        j = b + horizon
        if j >= series.n:
            continue
        a = atr[b]
        if not np.isfinite(a) or a <= 0:
            continue
        out.append(sgn * (float(series.close[j]) - float(series.close[b])) / float(a))
    return np.asarray(out, dtype=np.float64)


def _match_controls(
    series: BarSeries,
    events: Sequence[TouchEvent],
    atr: np.ndarray,
    rng: np.random.Generator,
    controls_per_event: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Control bars from the same (hour, ATR tercile) cell with no FVG touch.

    Matching on the cell rather than against all bars matters for the same reason it
    does in `sweep_study.py`: touches are not uniformly distributed across the session or
    across volatility regimes, so an unmatched control compares against a different
    population.
    """
    hours = np.array([from_epoch_s(t).hour for t in series.open_time], dtype=np.int64)
    terc = terciles(atr)
    touched = np.zeros(series.n, dtype=bool)
    for e in events:
        touched[e.bar] = True

    cells: dict[tuple[int, int], np.ndarray] = {}
    for h in np.unique(hours):
        for t in (0, 1, 2):
            sel = np.flatnonzero((hours == h) & (terc == t) & ~touched)
            if len(sel):
                cells[(int(h), int(t))] = sel

    bars: list[int] = []
    signs: list[int] = []
    used: dict[tuple[int, int], set[int]] = {k: set() for k in cells}
    for e in events:
        key = (e.hour, e.tercile)
        pool = cells.get(key)
        if pool is None or len(pool) == 0:
            continue
        for _ in range(controls_per_event):
            free = [x for x in pool if x not in used[key]]
            pick = int(rng.choice(free if free else pool))
            used[key].add(pick)
            bars.append(pick)
            signs.append(e.sign)
    return np.asarray(bars, dtype=np.int64), np.asarray(signs, dtype=np.int64)


# --------------------------------------------------------------------- the study


def run_study(
    series: BarSeries,
    book: FvgBook,
    cfg: AppConfig,
    *,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    bootstrap: int = 5000,
    permutations: int = 5000,
    seed: int = 20260825,
    controls_per_event: int = 1,
    margin: float = EQUIVALENCE_MARGIN_ATR,
    touch_shift: float = 0.0,
    atr: np.ndarray | None = None,
) -> FvgStudy:
    """SPEC 12.6's standalone edge test on one symbol-year.

    ``touch_shift`` injects a known effect into the touch group and exists only for the
    positive control -- a study that could not detect an effect it was handed would pass
    every null test written for it.
    """
    if atr is None:
        atr = atr_ref(series, cfg.atr.period)
    study = FvgStudy(
        horizons=horizons, bootstrap=bootstrap, permutations=permutations, seed=seed
    )
    study.n_gaps = len(book.fvgs)
    events, raw = touch_events(series, book, atr)
    study.n_touches = len(events)
    study.n_raw_touches = raw
    if not events:
        return study

    rng = np.random.default_rng(seed)
    t_bars = np.array([e.bar for e in events], dtype=np.int64)
    t_signs = np.array([e.sign for e in events], dtype=np.int64)
    c_bars, c_signs = _match_controls(series, events, atr, rng, controls_per_event)

    for k in horizons:
        t = forward_returns(series, t_bars, t_signs, k, atr)
        if touch_shift:
            t = t + touch_shift
        c = forward_returns(series, c_bars, c_signs, k, atr)
        if len(t) == 0 or len(c) == 0:
            continue
        lo, hi = bootstrap_diff_ci(t, c, bootstrap, rng)
        study.results.append(
            HorizonResult(
                horizon=k,
                touch=Group("FVG touch", t),
                control=Group("matched control", c),
                diff=float(t.mean() - c.mean()),
                ci_low=lo,
                ci_high=hi,
                p_value=permutation_p(t, c, permutations, rng),
                mde=minimum_detectable_effect(t, c),
                required_touch_n=required_n(t, c, margin),
                margin=margin,
            )
        )
    _apply_bh(study)
    return study


def _apply_bh(study: FvgStudy, q: float = 0.10) -> None:
    """Correct across horizons.

    Four horizons on one population is four chances to find something, and Phase 7 --
    3 of 20 tests firing on a random walk -- is this project's standing evidence that
    those chances get taken.
    """
    from dataclasses import replace as _replace

    adj = benjamini_hochberg([r.p_value for r in study.results], q)
    study.results = [_replace(r, p_adjusted=a) for r, a in zip(study.results, adj)]


def pool_studies(studies: Sequence[FvgStudy]) -> FvgStudy:
    """Combine per-symbol-year studies by pooling raw returns, not verdicts.

    Averaging effect sizes across runs discards the sample sizes that make the power
    arithmetic meaningful, which is most of what this study has to say.
    """
    if not studies:
        return FvgStudy(horizons=DEFAULT_HORIZONS)
    first = studies[0]
    out = FvgStudy(
        horizons=first.horizons,
        bootstrap=first.bootstrap,
        permutations=first.permutations,
        seed=first.seed,
    )
    out.n_gaps = sum(s.n_gaps for s in studies)
    out.n_touches = sum(s.n_touches for s in studies)
    out.n_raw_touches = sum(s.n_raw_touches for s in studies)

    rng = np.random.default_rng(first.seed)
    for k in first.horizons:
        rows = [r for s in studies for r in s.results if r.horizon == k]
        if not rows:
            continue
        t = np.concatenate([r.touch.returns for r in rows])
        c = np.concatenate([r.control.returns for r in rows])
        lo, hi = bootstrap_diff_ci(t, c, out.bootstrap, rng)
        out.results.append(
            HorizonResult(
                horizon=k,
                touch=Group("FVG touch", t),
                control=Group("matched control", c),
                diff=float(t.mean() - c.mean()),
                ci_low=lo,
                ci_high=hi,
                p_value=permutation_p(t, c, out.permutations, rng),
                mde=minimum_detectable_effect(t, c),
                required_touch_n=required_n(t, c, rows[0].margin),
                margin=rows[0].margin,
            )
        )
    _apply_bh(out)
    return out


def by_size_tercile(
    series: BarSeries, book: FvgBook, cfg: AppConfig, horizon: int, atr: np.ndarray | None = None
) -> dict[str, tuple[int, float]]:
    """Mean forward return by gap size, as ``label -> (n, mean)``.

    Reported because "bigger gaps matter more" is the natural next claim after a null
    result, and it is cheaper to check now than to re-open the study later. Read as a
    breakdown, not as evidence: three cells on one population is three more chances.
    """
    if atr is None:
        atr = atr_ref(series, cfg.atr.period)
    events, _ = touch_events(series, book, atr)
    if not events:
        return {}
    sizes = np.array([e.size_atr for e in events])
    cut = terciles(sizes)
    out: dict[str, tuple[int, float]] = {}
    for idx, label in ((0, "small"), (1, "medium"), (2, "large")):
        sel = [e for e, c in zip(events, cut) if c == idx]
        if not sel:
            continue
        r = forward_returns(
            series,
            [e.bar for e in sel],
            [e.sign for e in sel],
            horizon,
            atr,
        )
        out[label] = (len(r), float(r.mean()) if len(r) else float("nan"))
    return out
