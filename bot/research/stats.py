"""Shared statistical primitives for the falsification suite.

Extracted when Phase 10's FVG edge test would have become the **third** copy of the same
percentile bootstrap. `sweep_study.py` and `marginal_value.py` had one each, and two
copies of an interval method is how two studies quietly start answering the same question
differently -- which matters more here than in ordinary code, because the whole point of
`BACKTEST_PROTOCOL.md` §6 is that the studies are comparable to each other.

Nothing here knows about sweeps, gaps or structure. It takes arrays and returns numbers.

**The three-way verdict is the load-bearing idea**, and it belongs here rather than in any
one study. A hypothesis falsified by a *negative* result -- "displacement adds value",
"FVGs carry information" -- cannot be falsified by a confidence interval that merely
contains zero. That is absence of evidence. Falsification needs the interval to sit
**inside** a declared equivalence margin, and a study that cannot achieve either must say
so rather than pick whichever conclusion the reader expected:

| Verdict | Condition |
|---|---|
| `DIFFERENT` | CI excludes zero |
| `EQUIVALENT` | CI lies entirely inside +/- margin. The only verdict that licenses "no effect" |
| `UNDERPOWERED` | CI spans zero *and* extends past the margin -- the study cannot answer |

Every margin is a **declared judgement, fixed before results are read** (§10.2), and every
comparison reports its own minimum detectable effect so a reader applying a different
margin needs no re-run.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

import numpy as np

#: Two-sided alpha and the power target for the minimum-detectable-effect arithmetic.
ALPHA = 0.05
POWER = 0.80
_Z_ALPHA = 1.959963985  # Phi^-1(0.975)
_Z_POWER = 0.841621234  # Phi^-1(0.80)


class Verdict(str, Enum):
    DIFFERENT = "DIFFERENT"
    EQUIVALENT = "EQUIVALENT"
    UNDERPOWERED = "UNDERPOWERED"
    NO_DATA = "NO_DATA"


# ------------------------------------------------------------------ intervals


def bootstrap_diff_ci(
    a: np.ndarray,
    b: np.ndarray,
    n_boot: int,
    rng: np.random.Generator,
    alpha: float = ALPHA,
) -> tuple[float, float]:
    """Percentile bootstrap CI on ``mean(a) - mean(b)``.

    Known to under-cover with a few dozen heavy-tailed observations; the studies that use
    it measure their own false-positive rate with ``null_calibration`` rather than
    assuming nominal coverage (D-010 §5).
    """
    if len(a) < 2 or len(b) < 2:
        return (float("nan"), float("nan"))
    ia = rng.integers(0, len(a), size=(n_boot, len(a)))
    ib = rng.integers(0, len(b), size=(n_boot, len(b)))
    diffs = a[ia].mean(axis=1) - b[ib].mean(axis=1)
    lo, hi = np.quantile(diffs, [alpha / 2, 1 - alpha / 2])
    return float(lo), float(hi)


def permutation_p(
    a: np.ndarray, b: np.ndarray, n_perm: int, rng: np.random.Generator
) -> float:
    """Two-sided permutation p-value for a difference in means.

    A permutation test rather than a t-test because forward-return distributions are
    heavy-tailed and the treated group is usually small -- exactly the regime where the
    normal approximation is least trustworthy. The ``+1`` convention keeps p strictly
    positive, so a finite number of shuffles never reports p = 0.
    """
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    obs = abs(a.mean() - b.mean())
    pool = np.concatenate([a, b])
    n_a = len(a)
    count = 0
    for _ in range(n_perm):
        rng.shuffle(pool)
        if abs(pool[:n_a].mean() - pool[n_a:].mean()) >= obs:
            count += 1
    return (count + 1) / (n_perm + 1)


# --------------------------------------------------------------------- power


def _pooled_sd(a: np.ndarray, b: np.ndarray) -> float:
    va, vb = a.var(ddof=1), b.var(ddof=1)
    return float(np.sqrt(((len(a) - 1) * va + (len(b) - 1) * vb) / (len(a) + len(b) - 2)))


def minimum_detectable_effect(a: np.ndarray, b: np.ndarray) -> float:
    """Smallest true difference this sample could detect at ALPHA with POWER.

    The number that separates "no effect" from "no power". Reported per comparison
    rather than mentioned once in prose, because it is the only thing that makes a
    spanning interval interpretable.
    """
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    se = _pooled_sd(a, b) * np.sqrt(1 / len(a) + 1 / len(b))
    return float((_Z_ALPHA + _Z_POWER) * se)


def required_n(a: np.ndarray, b: np.ndarray, margin: float) -> float:
    """Size of the *treated* group needed to resolve ``margin``, holding the observed
    group-size ratio.

    Turns "the study could not tell" into a planning number, which is the form in which
    it can actually be acted on.
    """
    if len(a) < 2 or len(b) < 2 or margin <= 0:
        return float("nan")
    ratio = len(b) / len(a)
    sd = _pooled_sd(a, b)
    return float(((_Z_ALPHA + _Z_POWER) ** 2) * (sd**2) * (1 + 1 / ratio) / (margin**2))


def verdict_for(
    ci_low: float, ci_high: float, margin: float, n_a: int, n_b: int
) -> Verdict:
    """The three-way reading. See the module docstring for why it is not two-way."""
    if not np.isfinite(ci_low) or not np.isfinite(ci_high) or n_a < 2 or n_b < 2:
        return Verdict.NO_DATA
    if ci_low > 0.0 or ci_high < 0.0:
        return Verdict.DIFFERENT
    if ci_low > -margin and ci_high < margin:
        return Verdict.EQUIVALENT
    return Verdict.UNDERPOWERED


# ------------------------------------------------------- multiple testing


def benjamini_hochberg(p_values: Sequence[float], q: float = 0.10) -> list[float]:
    """BH-adjusted p-values (`BACKTEST_PROTOCOL.md` §5.6 uses q = 0.10).

    Several horizons on one population is several chances to find something, and Phase 7
    -- 3 of 20 tests firing on a random walk whose true effect is zero by construction --
    is this project's standing evidence that those chances get taken.
    """
    finite = [(i, p) for i, p in enumerate(p_values) if np.isfinite(p)]
    out = [float("nan")] * len(p_values)
    if not finite:
        return out
    finite.sort(key=lambda t: t[1])
    m = len(finite)
    prev = 1.0
    for rank in range(m, 0, -1):
        i, p = finite[rank - 1]
        prev = min(prev, p * m / rank)
        out[i] = float(min(1.0, prev))
    return out


# ------------------------------------------------------------- the controls


def null_calibration(
    treated: np.ndarray,
    control: np.ndarray,
    *,
    trials: int = 200,
    bootstrap: int = 1000,
    seed: int = 7,
) -> float:
    """False-positive rate when the treatment label is shuffled onto the same values.

    Under a shuffled label the true difference is exactly zero, so a `DIFFERENT` verdict
    is a false positive by construction and the rate should land near ``ALPHA``. An
    interval method too narrow to trust shows up here and nowhere else -- not in code
    review, and not in any other test.
    """
    pool = np.concatenate([treated, control])
    n_t = len(treated)
    if n_t < 2 or len(pool) - n_t < 2:
        return float("nan")
    rng = np.random.default_rng(seed)
    hits = 0
    for _ in range(trials):
        rng.shuffle(pool)
        lo, hi = bootstrap_diff_ci(pool[:n_t], pool[n_t:], bootstrap, rng)
        if np.isfinite(lo) and (lo > 0 or hi < 0):
            hits += 1
    return hits / trials


def detects_effect(
    treated: np.ndarray,
    control: np.ndarray,
    shift: float,
    *,
    bootstrap: int = 2000,
    seed: int = 11,
) -> bool:
    """Whether a known effect of ``shift`` added to the treated group is detected.

    The positive control. A study that could only ever return "no difference" would pass
    every null test written for it and be worthless.
    """
    rng = np.random.default_rng(seed)
    lo, hi = bootstrap_diff_ci(treated + shift, control, bootstrap, rng)
    return bool(np.isfinite(lo) and (lo > 0 or hi < 0))


def calibration_interval(
    rate: float, trials: int, z: float = _Z_ALPHA
) -> tuple[float, float]:
    """Wilson score interval on a measured false-positive rate.

    Reported alongside the rate because the rate is itself an estimate from a modest
    number of shuffles, and a point value invites reading a swing between two runs as a
    change in calibration. Observed directly: the same study measured 8.0% on 300 trials
    and 4.8% on 400, which the interval shows are compatible and the point estimates
    alone do not.

    Wilson rather than the normal approximation: at rates near 0.05 with a few hundred
    trials the normal interval can extend below zero, which is not a probability.
    """
    if trials <= 0 or not np.isfinite(rate):
        return (float("nan"), float("nan"))
    n = float(trials)
    denom = 1.0 + z * z / n
    centre = (rate + z * z / (2 * n)) / denom
    half = (z / denom) * np.sqrt(rate * (1 - rate) / n + z * z / (4 * n * n))
    return (float(max(0.0, centre - half)), float(min(1.0, centre + half)))


def calibration_sigma(rate: float, trials: int, alpha: float = ALPHA) -> float:
    """How far a measured false-positive rate sits from nominal, in standard errors.

    Exists because this project has three times written up a sub-2-sigma wobble as a
    finding before catching itself (D-007 §5, D-008 §3, D-010 §5). Reporting the rate
    without its standard error is what makes that easy.
    """
    if trials <= 0 or not np.isfinite(rate):
        return float("nan")
    se = (alpha * (1 - alpha) / trials) ** 0.5
    return float(abs(rate - alpha) / se) if se > 0 else float("nan")


# ------------------------------------------------------------------ strata


def terciles(values: np.ndarray) -> np.ndarray:
    """Tercile index 0/1/2, with NaN mapped to -1 (excluded from matching)."""
    out = np.full(len(values), -1, dtype=np.int64)
    ok = np.isfinite(values)
    if ok.sum() < 3:
        return out
    q1, q2 = np.quantile(values[ok], [1 / 3, 2 / 3])
    out[ok & (values <= q1)] = 0
    out[ok & (values > q1) & (values <= q2)] = 1
    out[ok & (values > q2)] = 2
    return out


@dataclass(frozen=True)
class Group:
    """A named sample of ATR-normalised returns."""

    label: str
    returns: np.ndarray

    @property
    def n(self) -> int:
        return int(len(self.returns))

    @property
    def mean(self) -> float:
        return float(self.returns.mean()) if self.n else float("nan")

    @property
    def median(self) -> float:
        return float(np.median(self.returns)) if self.n else float("nan")

    @property
    def sd(self) -> float:
        return float(self.returns.std(ddof=1)) if self.n > 1 else float("nan")

    @property
    def win_rate(self) -> float:
        """Share of values above zero.

        Reported next to the mean because a heavy-tailed distribution can have its mean
        set entirely by a handful of observations, and the two disagreeing is itself
        informative.
        """
        return float((self.returns > 0).mean()) if self.n else float("nan")


# ------------------------------------------- effective number of independent tests


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
