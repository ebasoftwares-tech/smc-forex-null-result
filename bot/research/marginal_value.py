"""MSS vs CHoCH-not-MSS: does the sweep-and-displacement requirement add anything?

This is the falsification test for **hypothesis H5** (`BACKTEST_PROTOCOL.md` §6.1):

> Displacement filtering adds value.
> *Falsified by:* MSS and CHoCH-not-MSS forward returns being indistinguishable.

SPEC 6.9 asks for three populations — all CHoCH, MSS only, CHoCH-not-MSS — at +1/+4/+12
bars, and §6.2 states the consequence: *"If MSS and CHoCH-not-MSS are indistinguishable,
the sweep-plus-displacement requirement is decoration."*  It is the most consequential
comparison in the project, because it tests the central claim of the methodology rather
than one parameter inside it.

**The statistical risk here is the mirror image of Phase 7's, and the whole module is
built around that.**  Phase 7 risked a *false positive*: 3 of 20 tests fired on a random
walk where the true effect is zero by construction.  H5 is falsified by a **negative**
result, so this study risks the opposite error — a confidence interval spanning zero
being written up as "displacement is decoration" when it is really "this sample cannot
tell".  Absence of evidence is not evidence of absence, and with a few dozen MSS events
against a wide return distribution the difference between the two is most of the answer.

So the verdict is deliberately **three-way, not two-way**:

| Verdict | Meaning |
|---|---|
| `DIFFERENT` | CI excludes zero. H5 survives in this sample |
| `EQUIVALENT` | CI lies **entirely inside** the equivalence margin. H5 falsified — this is the only result that licenses "decoration" |
| `UNDERPOWERED` | CI spans zero *and* extends past the margin. **The study cannot answer**, and says so |

``EQUIVALENCE_MARGIN_ATR`` is a **pre-declared judgement, not a discovery** (see its own
comment).  Every result also reports the minimum detectable effect and the number of MSS
events that would be needed to resolve the margin, so a reader applying a different
margin can do so without re-running anything.

Three further guards, each against a specific way this comparison goes wrong:

* **Overlapping windows inflate significance.**  Two CHoCH events three bars apart share
  nine of their twelve forward bars, so the events are not independent draws.  Every
  comparison is also run on a **non-overlapping subsample**, and the two are reported
  side by side.
* **The groups differ in more than displacement.**  MSS events are selected partly by
  when they occur, so a **stratified** estimate (matched on the D-001 session slot and
  the ATR tercile, exactly as `sweep_study.py` matches controls) is reported alongside
  the raw one.
* **A test that can only say "no difference" is worthless.**  ``positive_control`` injects
  a known effect and requires the study to find it; ``null_calibration`` shuffles the
  labels and requires the false-positive rate to land near alpha.  Both are asserted in
  `tests/test_marginal_value.py`.

**Out of scope, deliberately.**  §6.2 also asks for R-expectancy *"where a hypothetical
trade can be constructed"*.  It cannot: stops (SPEC 16) and targets (SPEC 17) are Phase
12, so there is no R to measure.  Forward returns only, and the report says so rather
than substituting an invented stop distance — which would make the answer a property of
that invention.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Sequence

import numpy as np

from bot.core.bars import BarSeries, from_epoch_s
from bot.core.displacement import Direction
from bot.core.indicators import atr_ref
from bot.core.mss import SetupCandidate
from bot.research.stats import (
    ALPHA,
    POWER,
    Group,
    Verdict,
    benjamini_hochberg,
    bootstrap_diff_ci,
    detects_effect,
    minimum_detectable_effect,
    null_calibration,
    permutation_p,
    required_n,
    terciles,
    verdict_for,
)

#: SPEC 6.9 names these three horizons explicitly.
DEFAULT_HORIZONS = (1, 4, 12)

#: The largest difference in mean forward return, in ATR units, that would still count as
#: "displacement adds nothing tradable".
#:
#: **This is a declared judgement and is not derived from the data.**  It is fixed here,
#: before any result is read, because choosing it afterwards would let the verdict be
#: selected rather than measured (`BACKTEST_PROTOCOL.md` §10.2).  The reasoning: the
#: displacement filter exists to select legs that moved at least 1.5 ATR, and a selector
#: that strong should shift the *subsequent* return by a non-trivial fraction of an ATR
#: if it carries information at all.  A sixth of the threshold it enforces is the line
#: taken.  Every result also reports its own minimum detectable effect, so a reader who
#: disagrees can substitute their own margin without re-running the study.
EQUIVALENCE_MARGIN_ATR = 0.25


@dataclass(frozen=True)
class Comparison:
    """MSS against CHoCH-not-MSS at one horizon, on one sample."""

    horizon: int
    sample: str  # "all" | "non_overlapping" | "stratified"
    mss: Group
    not_mss: Group
    all_choch: Group
    diff: float
    ci_low: float
    ci_high: float
    p_value: float
    mde: float
    required_mss_n: float
    margin: float = EQUIVALENCE_MARGIN_ATR
    p_adjusted: float | None = None

    @property
    def ci_excludes_zero(self) -> bool:
        return bool(np.isfinite(self.ci_low) and (self.ci_low > 0.0 or self.ci_high < 0.0))

    @property
    def ci_within_margin(self) -> bool:
        return bool(
            np.isfinite(self.ci_low)
            and self.ci_low > -self.margin
            and self.ci_high < self.margin
        )

    @property
    def verdict(self) -> Verdict:
        return verdict_for(
            self.ci_low, self.ci_high, self.margin, self.mss.n, self.not_mss.n
        )

    def describe(self) -> str:
        v = self.verdict
        if v is Verdict.DIFFERENT:
            return "MSS differs from CHoCH-not-MSS"
        if v is Verdict.EQUIVALENT:
            return f"indistinguishable within +/-{self.margin:g} ATR -- H5 falsified here"
        if v is Verdict.UNDERPOWERED:
            return f"cannot resolve {self.margin:g} ATR at n={self.mss.n}"
        return "insufficient data"


@dataclass
class MarginalValueStudy:
    horizons: tuple[int, ...]
    comparisons: list[Comparison] = field(default_factory=list)
    n_choch: int = 0
    n_mss: int = 0
    overlap_share: float = 0.0
    bootstrap: int = 5000
    permutations: int = 5000
    seed: int = 20260825

    def of(self, sample: str) -> list[Comparison]:
        return [c for c in self.comparisons if c.sample == sample]

    def verdict(self) -> Verdict:
        """The H5 reading over the primary ("all") sample.

        Deliberately conservative in one direction: a single DIFFERENT horizon is enough
        to keep H5 alive, but EQUIVALENT is only reported when *every* horizon is
        equivalent.  Falsifying the methodology's central claim on one horizon out of
        three, while the others could not resolve anything, is not a finding.
        """
        rows = self.of("all")
        if not rows:
            return Verdict.NO_DATA
        if any(r.verdict is Verdict.DIFFERENT for r in rows):
            return Verdict.DIFFERENT
        if all(r.verdict is Verdict.EQUIVALENT for r in rows):
            return Verdict.EQUIVALENT
        return Verdict.UNDERPOWERED

    def headline(self) -> str:
        v = self.verdict()
        if v is Verdict.DIFFERENT:
            return "H5 SURVIVES in this sample -- MSS forward returns differ from CHoCH-not-MSS"
        if v is Verdict.EQUIVALENT:
            return (
                "H5 FALSIFIED in this sample -- the sweep-plus-displacement requirement "
                "is decoration"
            )
        if v is Verdict.NO_DATA:
            return "NO DATA"
        return (
            "UNDECIDED -- this sample cannot resolve the pre-declared margin. NOT a null "
            "result, and must not be reported as one"
        )


# ------------------------------------------------------------------- primitives


def signed_forward_return(
    series: BarSeries, bar: int, direction: Direction, horizon: int, atr: np.ndarray
) -> float | None:
    """ATR-normalised return ``horizon`` bars after ``bar``, signed by setup direction.

    Anchored on the **close of the CHoCH bar**, which is the first moment the event is
    knowable, so the number is one a live system could have acted on.
    """
    j = bar + horizon
    if bar < 0 or j >= series.n:
        return None
    a = atr[bar]
    if not np.isfinite(a) or a <= 0:
        return None
    sign = 1.0 if direction is Direction.BULLISH else -1.0
    return sign * (float(series.close[j]) - float(series.close[bar])) / float(a)


# ---------------------------------------------------------------- sample builders


@dataclass(frozen=True)
class Event:
    """One CHoCH, reduced to what the comparison needs."""

    bar: int
    direction: Direction
    is_mss: bool
    hour: int
    tercile: int


def events_from(
    series: BarSeries, candidates: Sequence[SetupCandidate], atr: np.ndarray
) -> list[Event]:
    """Every candidate that reached a CHoCH, collapsed to one event per opportunity.

    Candidates that never broke their reference are in neither population: SPEC 6.9
    compares CHoCH against CHoCH, not against everything the funnel rejected.

    **Candidates sharing a break bar and a direction are one observation, not several.**
    The forward return is a function of ``(bar, direction)`` and nothing else, so two
    such candidates contribute the *identical number* twice. On the Phase 9 fixture that
    is not a rounding detail: 640 raw candidates collapse to 315 distinct observations,
    so half the apparent sample is one row counted again. Left in, every interval is
    about sqrt(2) too narrow and every required-sample figure is understated by two.

    Worse, 15 of those bars carried **both** labels — an MSS candidate and a
    CHoCH-not-MSS candidate breaking together — which puts one identical return into
    both groups and drags their means toward each other. That biases the study toward
    EQUIVALENT, which is precisely the verdict that would declare the methodology
    decoration. A bug in the safe direction would have been tolerable; this one is not.

    A bar carrying both labels is resolved as **MSS**: it was an opportunity the system
    would have taken. That is the same "best outcome represents the opportunity" rule
    `bot/research/funnel.py` applies to sweep clusters, and it is deliberately the
    stricter dedup of the two — SPEC 9.4's clustering keys on the *sweep*, while two
    sweeps from different clusters can still break on the same bar and produce the same
    number.
    """
    terc = terciles(atr)
    by_key: dict[tuple[int, Direction], bool] = {}
    for c in candidates:
        if not c.is_choch or c.choch_bar is None:
            continue
        key = (c.choch_bar, c.direction)
        by_key[key] = by_key.get(key, False) or c.is_mss
    out = [
        Event(
            bar=bar,
            direction=direction,
            is_mss=is_mss,
            hour=from_epoch_s(series.open_time[bar]).hour,
            tercile=int(terc[bar]),
        )
        for (bar, direction), is_mss in by_key.items()
    ]
    out.sort(key=lambda e: (e.bar, e.is_mss))
    return out


def non_overlapping(events: Sequence[Event], horizon: int) -> list[Event]:
    """Greedily thin the event list so no two forward windows overlap.

    Earliest-first rather than a random draw, so the subsample is deterministic and does
    not depend on a seed the reader would have to trust.  MSS events are the scarce group
    and this discards some of them, which is the cost of the independence it buys -- both
    versions are reported for exactly that reason.
    """
    out: list[Event] = []
    last = -(10**9)
    for e in sorted(events, key=lambda x: x.bar):
        if e.bar - last >= horizon:
            out.append(e)
            last = e.bar
    return out


def stratified_pairs(events: Sequence[Event]) -> list[Event]:
    """Keep only events in (hour, tercile) cells containing **both** groups.

    A cell with no MSS contributes nothing to a difference between the groups but does
    move the CHoCH-not-MSS mean, so including it compares the two populations partly on
    when they happened rather than on displacement.  Matching on the D-001 session slot
    and the ATR tercile is the same stratification `sweep_study.py` uses.
    """
    cells: dict[tuple[int, int], list[Event]] = {}
    for e in events:
        if e.tercile < 0:
            continue
        cells.setdefault((e.hour, e.tercile), []).append(e)
    out: list[Event] = []
    for group in cells.values():
        if any(e.is_mss for e in group) and any(not e.is_mss for e in group):
            out.extend(group)
    return sorted(out, key=lambda e: e.bar)


# --------------------------------------------------------------------- the study


def _returns(
    series: BarSeries, events: Sequence[Event], horizon: int, atr: np.ndarray, want_mss: bool | None
) -> np.ndarray:
    vals = []
    for e in events:
        if want_mss is not None and e.is_mss is not want_mss:
            continue
        r = signed_forward_return(series, e.bar, e.direction, horizon, atr)
        if r is not None:
            vals.append(r)
    return np.asarray(vals, dtype=np.float64)


def _compare(
    series: BarSeries,
    events: Sequence[Event],
    horizon: int,
    atr: np.ndarray,
    sample: str,
    rng: np.random.Generator,
    *,
    bootstrap: int,
    permutations: int,
    margin: float,
    mss_shift: float = 0.0,
) -> Comparison:
    mss = _returns(series, events, horizon, atr, True)
    if mss_shift:
        mss = mss + mss_shift  # positive control only
    other = _returns(series, events, horizon, atr, False)
    every = np.concatenate([mss, other]) if len(mss) or len(other) else np.asarray([])

    lo, hi = bootstrap_diff_ci(mss, other, bootstrap, rng)
    return Comparison(
        horizon=horizon,
        sample=sample,
        mss=Group("MSS", mss),
        not_mss=Group("CHoCH-not-MSS", other),
        all_choch=Group("all CHoCH", every),
        diff=float(mss.mean() - other.mean()) if len(mss) and len(other) else float("nan"),
        ci_low=lo,
        ci_high=hi,
        p_value=permutation_p(mss, other, permutations, rng),
        mde=minimum_detectable_effect(mss, other),
        required_mss_n=required_n(mss, other, margin),
        margin=margin,
    )


def run_study(
    series: BarSeries,
    candidates: Sequence[SetupCandidate],
    cfg,
    *,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    bootstrap: int = 5000,
    permutations: int = 5000,
    seed: int = 20260825,
    margin: float = EQUIVALENCE_MARGIN_ATR,
    mss_shift: float = 0.0,
    atr: np.ndarray | None = None,
) -> MarginalValueStudy:
    """SPEC 6.9's three-population comparison, on one symbol-year.

    ``mss_shift`` injects a known effect into the MSS group and exists only for the
    positive control -- a study that could not detect an effect it was handed would pass
    every null test in this file and be worthless.
    """
    if atr is None:
        atr = atr_ref(series, cfg.atr.period)
    study = MarginalValueStudy(
        horizons=horizons, bootstrap=bootstrap, permutations=permutations, seed=seed
    )
    events = events_from(series, candidates, atr)
    study.n_choch = len(events)
    study.n_mss = sum(1 for e in events if e.is_mss)
    if not events:
        return study

    rng = np.random.default_rng(seed)
    samples = {
        "all": lambda h: list(events),
        "non_overlapping": lambda h: non_overlapping(events, h),
        "stratified": lambda h: stratified_pairs(events),
    }
    for name, pick in samples.items():
        for h in horizons:
            study.comparisons.append(
                _compare(
                    series,
                    pick(h),
                    h,
                    atr,
                    name,
                    rng,
                    bootstrap=bootstrap,
                    permutations=permutations,
                    margin=margin,
                    mss_shift=mss_shift,
                )
            )

    # Overlap diagnostic at the longest horizon: the share of events whose forward window
    # is contaminated by a neighbour.
    longest = max(horizons)
    kept = len(non_overlapping(events, longest))
    study.overlap_share = 1.0 - (kept / len(events)) if events else 0.0

    _apply_bh(study)
    return study


def _apply_bh(study: MarginalValueStudy, q: float = 0.10) -> None:
    """Correct across horizons, within each sample.

    Positions are tracked explicitly rather than looked up with ``list.index``: a
    ``Comparison`` holds numpy arrays, so its generated ``__eq__`` returns an array and
    any equality-based lookup is one refactor away from raising "truth value of an array
    is ambiguous".  It happens to work today only because ``list.index`` short-circuits
    on identity first.
    """
    for name in sorted({c.sample for c in study.comparisons}):
        idxs = [i for i, c in enumerate(study.comparisons) if c.sample == name]
        adj = benjamini_hochberg([study.comparisons[i].p_value for i in idxs], q)
        for i, a in zip(idxs, adj):
            study.comparisons[i] = replace(study.comparisons[i], p_adjusted=a)


def pool_studies(studies: Sequence[MarginalValueStudy]) -> MarginalValueStudy:
    """Combine per-symbol-year studies by pooling the raw returns, not the verdicts.

    Averaging effect sizes across runs would throw away the sample sizes that make the
    power arithmetic meaningful, and it is precisely the small-sample behaviour this
    study exists to be honest about.
    """
    if not studies:
        return MarginalValueStudy(horizons=DEFAULT_HORIZONS)
    first = studies[0]
    out = MarginalValueStudy(
        horizons=first.horizons,
        bootstrap=first.bootstrap,
        permutations=first.permutations,
        seed=first.seed,
    )
    out.n_choch = sum(s.n_choch for s in studies)
    out.n_mss = sum(s.n_mss for s in studies)
    out.overlap_share = float(np.mean([s.overlap_share for s in studies]))

    rng = np.random.default_rng(first.seed)
    keys = sorted({(c.sample, c.horizon) for s in studies for c in s.comparisons})
    for sample, h in keys:
        mss = np.concatenate(
            [c.mss.returns for s in studies for c in s.comparisons
             if c.sample == sample and c.horizon == h] or [np.asarray([])]
        )
        oth = np.concatenate(
            [c.not_mss.returns for s in studies for c in s.comparisons
             if c.sample == sample and c.horizon == h] or [np.asarray([])]
        )
        lo, hi = bootstrap_diff_ci(mss, oth, out.bootstrap, rng)
        out.comparisons.append(
            Comparison(
                horizon=h,
                sample=sample,
                mss=Group("MSS", mss),
                not_mss=Group("CHoCH-not-MSS", oth),
                all_choch=Group("all CHoCH", np.concatenate([mss, oth])),
                diff=float(mss.mean() - oth.mean()) if len(mss) and len(oth) else float("nan"),
                ci_low=lo,
                ci_high=hi,
                p_value=permutation_p(mss, oth, out.permutations, rng),
                mde=minimum_detectable_effect(mss, oth),
                required_mss_n=required_n(mss, oth, EQUIVALENCE_MARGIN_ATR),
                margin=first.comparisons[0].margin if first.comparisons else EQUIVALENCE_MARGIN_ATR,
            )
        )
    _apply_bh(out)
    return out


# ------------------------------------------------------------------- the controls


