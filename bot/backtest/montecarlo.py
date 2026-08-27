"""Monte Carlo robustness (BACKTEST_PROTOCOL section 9).

Five tests, and the protocol singles one of them out: *"the skip-10% test deserves
emphasis: a strategy whose entire profit comes from three trades will fail it, and no
other test in this suite reliably catches that."* That is the one to read first.

**Three of the five are resamples of the realised trade sequence and are computed here.**
They need no re-run and no market data, only the ordered list of R-multiples, which makes
them cheap enough to run on every variant rather than on the headline alone.

**Two are re-runs and take a callable**, because randomising costs or shifting entry timing
changes what the engine produces and cannot be simulated by reshuffling its output. Passing
the re-run in rather than importing the engine keeps this module free of the pipeline and
lets a test drive it with an arithmetic stub.

Every test takes a seed and uses ``numpy.random.Generator`` explicitly. SPEC 25.5 permits
seeded randomness **here and nowhere else**: *"used only in Monte Carlo, never in the
strategy."* A result that moved between runs of this module would be indistinguishable
from a result that moved because the strategy changed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

import numpy as np


@dataclass(frozen=True)
class McResult:
    name: str
    statistic: str
    value: float
    threshold: float | None
    passed: bool | None
    detail: str = ""


def _equity_path(r: np.ndarray, risk_pct: float) -> np.ndarray:
    """Compound an R sequence at a fixed fractional risk.

    Multiplicative, not additive: ruin is a property of compounding, and an additive path
    cannot go to zero however bad the sequence is, so an additive model would report a
    ruin probability of exactly zero for every strategy ever tested.
    """
    return np.cumprod(1.0 + r * risk_pct / 100.0)


def _max_drawdown(path: np.ndarray) -> float:
    peaks = np.maximum.accumulate(path)
    return float(np.max((peaks - path) / peaks)) if path.size else 0.0


def trade_order_shuffle(
    r: Sequence[float],
    *,
    risk_pct: float,
    n: int = 10_000,
    seed: int = 20260827,
    dd_tolerance_pct: float = 20.0,
    ruin_threshold: float = 0.01,
) -> list[McResult]:
    """Section 9: 10,000 permutations of the realised sequence.

    **The order of trades is not information the strategy produced** -- it is one draw from
    the set of orders the same trades could have arrived in. The realised drawdown is
    therefore one sample from a distribution, and quoting it alone reports the luck of the
    ordering as if it were a property of the system.

    Ruin is measured at 50% of starting equity rather than at zero: an account down 50%
    needs a 100% gain to recover, and no operator continues.
    """
    arr = np.asarray(r, dtype=np.float64)
    if arr.size < 5:
        return []
    rng = np.random.default_rng(seed)
    dds = np.empty(n)
    ruined = 0
    for i in range(n):
        path = _equity_path(rng.permutation(arr), risk_pct)
        dds[i] = _max_drawdown(path)
        if path.min() <= 0.5:
            ruined += 1
    p95 = float(np.percentile(dds, 95) * 100.0)
    ruin = ruined / n
    return [
        McResult("Trade-order shuffle", "95th-percentile max drawdown", p95,
                 dd_tolerance_pct, p95 <= dd_tolerance_pct,
                 f"realised ordering is one draw of {n:,}"),
        McResult("Trade-order shuffle", "ruin probability", ruin, ruin_threshold,
                 ruin < ruin_threshold, f"ruin = equity halved at {risk_pct}% per trade"),
    ]


def bootstrap_expectancy(
    r: Sequence[float], *, n: int = 10_000, seed: int = 20260827
) -> McResult:
    """Section 9: 5th-percentile expectancy over 10,000 resamples must exceed zero."""
    arr = np.asarray(r, dtype=np.float64)
    if arr.size < 5:
        return McResult("Bootstrap resample", "5th-percentile expectancy (R)",
                        float("nan"), 0.0, None, "too few trades")
    rng = np.random.default_rng(seed)
    means = arr[rng.integers(0, arr.size, size=(n, arr.size))].mean(axis=1)
    p5 = float(np.percentile(means, 5))
    return McResult("Bootstrap resample", "5th-percentile expectancy (R)", p5, 0.0,
                    p5 > 0.0, f"{n:,} resamples")


def skip_ten_percent(
    r: Sequence[float],
    *,
    risk_pct: float,
    n: int = 1_000,
    seed: int = 20260827,
    skip: float = 0.10,
) -> McResult:
    """Section 9's most diagnostic test, and the protocol says so.

    *"A strategy whose entire profit comes from three trades will fail it, and no other
    test in this suite reliably catches that."* Dropping a random tenth of the trades
    should barely move a real edge and should destroy a result carried by a handful of
    outliers.
    """
    arr = np.asarray(r, dtype=np.float64)
    if arr.size < 10:
        return McResult("Skip 10% of trades", "5th-percentile net return (%)",
                        float("nan"), 0.0, None, "too few trades")
    rng = np.random.default_rng(seed)
    keep = max(1, int(round(arr.size * (1 - skip))))
    out = np.empty(n)
    for i in range(n):
        idx = rng.choice(arr.size, size=keep, replace=False)
        idx.sort()
        out[i] = (_equity_path(arr[idx], risk_pct)[-1] - 1.0) * 100.0
    p5 = float(np.percentile(out, 5))
    return McResult("Skip 10% of trades", "5th-percentile net return (%)", p5, 0.0,
                    p5 > 0.0, f"{n:,} runs dropping {skip:.0%} at random")


def concentration(
    r: Sequence[float], *, risk_pct: float, k: int = 3, seed: int = 20260827
) -> list[McResult]:
    """How much of the result rests on its best few trades -- section 9's skip test, sharpened.

    **The skip-10% test is the protocol's most-emphasised one and its stated threshold does
    not always reach the failure mode it is named for.** Acceptance is *"5th-percentile net
    return > 0"*, which is a **sign** test, while concentration shows up as a large **drop**.
    Measured on constructed sequences:

    ==============================  ======  =======  ==========  ==========
    sequence                        top-3   p5 ret   degradation  sign test
    ==============================  ======  =======  ==========  ==========
    diffuse edge, 300 trades          26%    +7.9%       37%      passes
    carried by 3 of 300              571%    -1.2%      158%      fails
    carried by 1 of 40               190%    -1.3%      189%      fails
    **carried by 3 of 60**           161%    +1.7%       49%      **passes**
    ==============================  ======  =======  ==========  ==========

    The last row is the gap: dropping 10% of 60 trades removes 6, so it rarely removes all
    three that carry the result, and the sign survives. Two companion statistics close it
    and cost nothing -- the **share of total R held by the best k trades**, and the
    **degradation** of the skip test's own 5th percentile against the unskipped return. A
    share above 100% means the rest of the book loses money. See D-015 section 4.
    """
    arr = np.asarray(r, dtype=np.float64)
    if arr.size < 10:
        return []
    total = float(arr.sum())
    if total <= 0:
        # Concentration is a question about how a PROFIT is distributed. On a losing book
        # the ratio is negative and would sail past a "< 1.0" threshold while meaning
        # nothing -- the Phase 14 fixture reported -5.97 and "passed". No verdict is the
        # honest output.
        return [McResult(f"Top-{k} concentration", f"share of total R in the best {k}",
                         float("nan"), 1.0, None,
                         "undefined: total R is not positive")]
    top = float(np.sort(arr)[-k:].sum())
    share = top / total
    base = float((_equity_path(arr, risk_pct)[-1] - 1.0) * 100.0)
    p5 = skip_ten_percent(arr, risk_pct=risk_pct, seed=seed).value
    degradation = (base - p5) / abs(base) if base else float("nan")
    return [
        McResult(f"Top-{k} concentration", f"share of total R in the best {k}", share,
                 1.0, bool(np.isfinite(share) and share < 1.0),
                 "above 1.0 means the rest of the book loses money"),
        McResult("Skip-10% degradation", "drop from baseline net return", degradation,
                 0.5, bool(np.isfinite(degradation) and degradation < 0.5),
                 "the companion the sign threshold needs -- see the docstring table"),
    ]


def randomised_costs(
    rerun: Callable[[float], float],
    *,
    multipliers: Sequence[float] = (1.0, 1.25, 1.5, 1.75, 2.0),
    n: int = 200,
    seed: int = 20260827,
) -> McResult:
    """Section 9: costs drawn from a distribution; median expectancy must exceed zero.

    ``rerun(multiplier) -> expectancy`` re-runs the engine. The distribution is over
    ``cost.multiplier`` because that is the only cost dimension this project can vary
    honestly -- SPEC 26's spread is a session constant until Q2, so drawing a spread from
    a "measured distribution" would be drawing from a distribution nobody measured.
    """
    rng = np.random.default_rng(seed)
    cache: dict[float, float] = {}
    draws = rng.choice(np.asarray(multipliers, dtype=np.float64), size=n)
    vals = []
    for m in draws:
        key = float(m)
        if key not in cache:
            cache[key] = rerun(key)
        vals.append(cache[key])
    med = float(np.median(vals))
    return McResult("Randomised costs", "median expectancy (R)", med, 0.0, med > 0.0,
                    f"{n} draws over cost.multiplier in {tuple(multipliers)}")


def entry_timing_shift(
    baseline: float, shifted: Sequence[float], *, max_degradation: float = 0.40
) -> McResult:
    """Section 9: entry shifted +/-1 bar; expectancy degradation must stay under 40%.

    A strategy that only works when entered on exactly the right bar is fitting the bar
    grid rather than the market. Degradation is measured on the *worst* shift, not the
    mean of them, because the question is whether the result survives being wrong -- and
    averaging a good shift against a bad one hides exactly the fragility being tested.
    """
    vals = [v for v in shifted if np.isfinite(v)]
    if not np.isfinite(baseline) or not vals:
        return McResult("Entry timing shift", "worst degradation", float("nan"),
                        max_degradation, None, "not computable")
    worst = min(vals)
    if baseline == 0:
        return McResult("Entry timing shift", "worst degradation", float("nan"),
                        max_degradation, None, "baseline expectancy is zero")
    degradation = (baseline - worst) / abs(baseline)
    return McResult("Entry timing shift", "worst degradation", degradation,
                    max_degradation, degradation < max_degradation,
                    f"baseline {baseline:+.3f}R, worst shift {worst:+.3f}R")
