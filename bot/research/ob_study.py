"""The Order Block definition bake-off (SPEC 13.8) — Phase 11's gate.

> *"OB-A/B/C/D as four separate pre-registered variants, identical in every other
> respect. Report the agreement matrix (how often they pick the same bar) as well as
> performance, **because near-identical variants must not be counted as independent tests
> when applying the multiple-testing correction**."*

That last clause is the whole point, and it is a statistical instruction rather than a
reporting nicety. SPEC 13.6 states the consequence plainly: *"if OB-A and OB-C select the
same bar 80% of the time, they are not two hypotheses."* Four variants compared is
nominally four chances to find something; if they agree most of the time it is closer to
one, and correcting as though it were four would be as wrong in one direction as
correcting for one would be in the other.

So this module produces **an effective number of independent tests**, not just a table of
agreement percentages, and the gate report uses it in place of the nominal four.

Two things worth knowing about how that number is computed.

**Agreement is measured two ways, because they answer different questions.** Same-bar
agreement is what SPEC 13.8 literally asks for and is the more intuitive figure. But what
a trade actually consumes is the *entry price*, and two definitions that pick different
bars a pip apart are the same hypothesis for every purpose that matters downstream. The
effective-test count is therefore computed from the correlation of proposed entry prices,
with same-bar agreement reported alongside it.

**`M_eff` uses Galwey's eigenvalue estimator, not the more commonly cited Li & Ji**,
because Li & Ji is discontinuous at integer eigenvalues and near-identical variants are
this study's whole subject -- see ``effective_tests``. It runs on a listwise-complete
correlation matrix: pairwise-complete would use more of the data but need not be positive
semi-definite, and negative eigenvalues in a variance decomposition produce a number that
looks fine and means nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from typing import Sequence

import numpy as np

from bot.config.schema import AppConfig
from bot.core.bars import BarSeries, from_epoch_s
from bot.core.displacement import Direction
from bot.core.indicators import atr_ref
from bot.core.order_blocks import ObBook, ObDefinition, ObProposal, OrderBlock
from bot.research.stats import (
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

#: Mirrors `fvg_study.DEFAULT_HORIZONS` so the two standalone edge tests can be read
#: against each other -- SPEC 13.8 asks for "standalone edge test, as for FVG".
DEFAULT_HORIZONS = (1, 3, 6, 12)

#: Declared before any result is read (`BACKTEST_PROTOCOL.md` §10.2), and equal to the
#: FVG study's margin for the same reason it matched the H5 study's: two components
#: judged at different resolutions cannot be compared to each other.
EQUIVALENCE_MARGIN_ATR = 0.25


# ------------------------------------------------------------ the agreement matrix


@dataclass
class SetupProposals:
    """What every definition produced for one setup.

    ``ref_price`` and ``ref_atr`` are the break bar's close and ATR -- an anchor that is
    **exogenous to the set of definitions being compared**, which matters more than it
    looks (see ``entry_offset``).
    """

    setup_id: str
    break_bar: int
    direction: Direction
    ref_price: float = 0.0
    ref_atr: float = 1.0
    proposals: dict[ObDefinition, ObProposal] = field(default_factory=dict)

    def origin(self, d: ObDefinition) -> int | None:
        p = self.proposals.get(d)
        return p.ob.origin_index if p is not None and p.ok else None

    def entry_price(self, d: ObDefinition) -> float | None:
        p = self.proposals.get(d)
        return p.ob.proximal if p is not None and p.ok else None

    def entry_offset(self, d: ObDefinition) -> float | None:
        """How far the proposed entry sits from the break bar's close, in ATR.

        Raw entry prices cannot be correlated directly: every column would be dominated
        by the level of the exchange rate over the sample and every pair would correlate
        at ~1.0 whichever bar it picked. The obvious fix -- subtracting each setup's mean
        across the definitions -- is **wrong**, and quietly so: centring `k` variables by
        their own per-observation mean forces the deviations to sum to zero and pins the
        average pairwise correlation at exactly ``-1/(k-1)``. The first version of this
        module did that and produced a matrix where OB-A and OB-B correlated at 0.28
        while agreeing on the same bar 79% of the time, and everything correlated
        negatively with OB-D. See D-012.

        Anchoring on the break bar's close instead is exogenous to which definitions are
        in the comparison, so adding or removing a variant cannot move the others.
        """
        price = self.entry_price(d)
        if price is None or not np.isfinite(self.ref_atr) or self.ref_atr <= 0:
            return None
        return (price - self.ref_price) / self.ref_atr


def agreement_matrix(
    rows: Sequence[SetupProposals], definitions: Sequence[ObDefinition]
) -> dict[tuple[str, str], tuple[int, float]]:
    """``(a, b) -> (n compared, share picking the same bar)``.

    The denominator is setups where **both** definitions produced a block: a definition
    that declines to propose is not disagreeing about which bar, it is answering a
    different question, and folding those in would make a rarely-firing definition look
    maximally independent.
    """
    out: dict[tuple[str, str], tuple[int, float]] = {}
    for a, b in combinations(definitions, 2):
        both = [
            r for r in rows if r.origin(a) is not None and r.origin(b) is not None
        ]
        if not both:
            out[(a.value, b.value)] = (0, float("nan"))
            continue
        same = sum(1 for r in both if r.origin(a) == r.origin(b))
        out[(a.value, b.value)] = (len(both), same / len(both))
    return out


def price_correlations(
    rows: Sequence[SetupProposals], definitions: Sequence[ObDefinition]
) -> tuple[np.ndarray, int]:
    """Listwise-complete correlation matrix of proposed entry prices, and its ``n``.

    Listwise rather than pairwise: a pairwise-complete matrix uses more of the data but
    need not be positive semi-definite, and ``effective_tests`` decomposes its variance.
    Negative eigenvalues there would yield a number that looks reasonable and means
    nothing.

    Correlated on ``entry_offset`` -- the ATR-normalised distance from the break bar's
    close -- rather than on raw prices or on prices centred across the definitions. See
    ``SetupProposals.entry_offset`` for why the second of those is a trap.
    """
    complete = [
        r for r in rows if all(r.entry_offset(d) is not None for d in definitions)
    ]
    k = len(definitions)
    if len(complete) < 3:
        return np.full((k, k), np.nan), len(complete)
    m = np.asarray(
        [[r.entry_offset(d) for r in complete] for d in definitions], dtype=np.float64
    )
    if np.allclose(m.std(axis=1), 0):
        return np.full((k, k), np.nan), len(complete)
    with np.errstate(invalid="ignore", divide="ignore"):
        corr = np.corrcoef(m)
    return corr, len(complete)


def _eigenvalues(corr: np.ndarray) -> np.ndarray | None:
    if corr.size == 0 or not np.all(np.isfinite(corr)):
        return None
    # Numerical noise can push a zero eigenvalue slightly negative; a variance
    # decomposition cannot use one.
    return np.clip(np.linalg.eigvalsh(corr), 0.0, None)


def effective_tests(corr: np.ndarray) -> float:
    """Effective number of independent tests, by Galwey's estimator.

    ``M_eff = (sum_i sqrt(lambda_i))^2 / sum_i lambda_i`` over the eigenvalues of the
    correlation matrix. This is the number the multiple-testing correction should use in
    place of the nominal variant count (SPEC 13.8): reporting four when the variants
    agree almost always over-corrects as badly as reporting one would under-correct.

    **Galwey rather than the more commonly cited Li & Ji (2005), for a reason specific to
    this study.** Li & Ji sums ``I(lambda >= 1) + frac(lambda)``, which is *discontinuous
    at integer eigenvalues*: four perfectly correlated variants give eigenvalues
    ``[4, 0, 0, 0]`` and it correctly returns 1, but perturbing the top eigenvalue to 3.99
    -- which any real sample does -- makes ``floor`` drop to 3 and the estimate jumps to
    **1.99**. Near-identical variants are precisely this study's subject, so the estimator
    would be at its least stable exactly where it is needed.

    Galwey is continuous and exact at every anchor point: ``[4,0,0,0] -> 1``,
    ``[2,2,0,0] -> 2``, ``[1,1,1,1] -> 4``. ``li_ji_effective_tests`` is kept for
    reference and pinned by a test that documents the discontinuity, but the reports use
    this one. See D-012.
    """
    vals = _eigenvalues(corr)
    if vals is None or vals.sum() <= 0:
        return float("nan")
    return float((np.sqrt(vals).sum() ** 2) / vals.sum())


def li_ji_effective_tests(corr: np.ndarray) -> float:
    """Li & Ji (2005), reported for reference only -- see ``effective_tests``."""
    vals = _eigenvalues(corr)
    if vals is None:
        return float("nan")
    return float(sum((1.0 if v >= 1.0 else 0.0) + (v - np.floor(v)) for v in vals))


# ------------------------------------------------------- the standalone edge test


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
class ObEdgeStudy:
    definition: ObDefinition
    horizons: tuple[int, ...]
    results: list[HorizonResult] = field(default_factory=list)
    n_blocks: int = 0
    n_touches: int = 0
    n_raw_touches: int = 0

    def verdict(self) -> Verdict:
        if not self.results:
            return Verdict.NO_DATA
        if any(r.verdict is Verdict.DIFFERENT for r in self.results):
            return Verdict.DIFFERENT
        if all(r.verdict is Verdict.EQUIVALENT for r in self.results):
            return Verdict.EQUIVALENT
        return Verdict.UNDERPOWERED


def touch_bars(book: ObBook) -> tuple[list[tuple[int, int]], int]:
    """First-touch events as ``(bar, sign)``, one per ``(bar, direction)``.

    D-010 §3 again: a forward return is a function of ``(bar, direction)`` and nothing
    else, so two blocks touched by the same bar in the same direction contribute the
    identical number twice. Returns the raw count too, so the report can state how much
    collapsing happened rather than assert it did not matter.
    """
    raw = [b for b in book.blocks if b.first_touch_index is not None]
    keyed: dict[tuple[int, int], int] = {}
    for b in raw:
        sign = 1 if b.direction is Direction.BULLISH else -1
        keyed[(b.first_touch_index, sign)] = sign
    events = sorted((bar, sign) for (bar, sign) in keyed)
    return events, len(raw)


def forward_returns(
    series: BarSeries,
    events: Sequence[tuple[int, int]],
    horizon: int,
    atr: np.ndarray,
) -> np.ndarray:
    """ATR-normalised, direction-signed return ``horizon`` bars after each touch bar."""
    out: list[float] = []
    for bar, sign in events:
        j = bar + horizon
        if j >= series.n:
            continue
        a = atr[bar]
        if not np.isfinite(a) or a <= 0:
            continue
        out.append(sign * (float(series.close[j]) - float(series.close[bar])) / float(a))
    return np.asarray(out, dtype=np.float64)


def _controls(
    series: BarSeries,
    events: Sequence[tuple[int, int]],
    atr: np.ndarray,
    rng: np.random.Generator,
) -> list[tuple[int, int]]:
    """Untouched bars from the same (session slot, ATR tercile) cell.

    Same matching as `sweep_study.py` and `fvg_study.py`: touches are not uniform across
    the session or across volatility regimes, so an unmatched control compares against a
    different population.
    """
    hours = np.array([from_epoch_s(t).hour for t in series.open_time], dtype=np.int64)
    terc = terciles(atr)
    touched = np.zeros(series.n, dtype=bool)
    for bar, _ in events:
        touched[bar] = True

    cells: dict[tuple[int, int], np.ndarray] = {}
    for h in np.unique(hours):
        for t in (0, 1, 2):
            sel = np.flatnonzero((hours == h) & (terc == t) & ~touched)
            if len(sel):
                cells[(int(h), int(t))] = sel

    out: list[tuple[int, int]] = []
    used: dict[tuple[int, int], set[int]] = {k: set() for k in cells}
    for bar, sign in events:
        key = (int(hours[bar]), int(terc[bar]))
        pool = cells.get(key)
        if pool is None or len(pool) == 0:
            continue
        free = [x for x in pool if x not in used[key]]
        pick = int(rng.choice(free if free else pool))
        used[key].add(pick)
        out.append((pick, sign))
    return out


def run_edge_study(
    series: BarSeries,
    book: ObBook,
    cfg: AppConfig,
    definition: ObDefinition,
    *,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    bootstrap: int = 5000,
    permutations: int = 5000,
    seed: int = 20260825,
    margin: float = EQUIVALENCE_MARGIN_ATR,
    touch_shift: float = 0.0,
    atr: np.ndarray | None = None,
) -> ObEdgeStudy:
    """SPEC 13.8's standalone edge test for one definition."""
    if atr is None:
        atr = atr_ref(series, cfg.atr.period)
    study = ObEdgeStudy(definition=definition, horizons=horizons)
    study.n_blocks = len(book.blocks)
    events, raw = touch_bars(book)
    study.n_touches = len(events)
    study.n_raw_touches = raw
    if not events:
        return study

    rng = np.random.default_rng(seed)
    ctrl = _controls(series, events, atr, rng)
    for k in horizons:
        t = forward_returns(series, events, k, atr)
        if touch_shift:
            t = t + touch_shift
        c = forward_returns(series, ctrl, k, atr)
        if len(t) < 2 or len(c) < 2:
            continue
        lo, hi = bootstrap_diff_ci(t, c, bootstrap, rng)
        study.results.append(
            HorizonResult(
                horizon=k,
                touch=Group("OB touch", t),
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


def _apply_bh(study: ObEdgeStudy, q: float = 0.10) -> None:
    from dataclasses import replace as _replace

    adj = benjamini_hochberg([r.p_value for r in study.results], q)
    study.results = [_replace(r, p_adjusted=a) for r, a in zip(study.results, adj)]


def pool_edge_studies(studies: Sequence[ObEdgeStudy]) -> ObEdgeStudy:
    """Pool raw returns across symbol-years, not verdicts."""
    if not studies:
        return ObEdgeStudy(ObDefinition.A_LAST_OPPOSING, DEFAULT_HORIZONS)
    first = studies[0]
    out = ObEdgeStudy(first.definition, first.horizons)
    out.n_blocks = sum(s.n_blocks for s in studies)
    out.n_touches = sum(s.n_touches for s in studies)
    out.n_raw_touches = sum(s.n_raw_touches for s in studies)
    rng = np.random.default_rng(20260825)
    for k in first.horizons:
        rows = [r for s in studies for r in s.results if r.horizon == k]
        if not rows:
            continue
        t = np.concatenate([r.touch.returns for r in rows])
        c = np.concatenate([r.control.returns for r in rows])
        if len(t) < 2 or len(c) < 2:
            continue
        lo, hi = bootstrap_diff_ci(t, c, 5000, rng)
        out.results.append(
            HorizonResult(
                horizon=k,
                touch=Group("OB touch", t),
                control=Group("matched control", c),
                diff=float(t.mean() - c.mean()),
                ci_low=lo,
                ci_high=hi,
                p_value=permutation_p(t, c, 5000, rng),
                mde=minimum_detectable_effect(t, c),
                required_touch_n=required_n(t, c, rows[0].margin),
                margin=rows[0].margin,
            )
        )
    _apply_bh(out)
    return out
