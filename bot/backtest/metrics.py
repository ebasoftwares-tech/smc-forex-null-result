"""Headline metrics and breakdowns (BACKTEST_PROTOCOL sections 4 and 5).

**Expectancy in R is primary, and the reason is worth restating at the point of
computation.** Section 4.1: *"net return conflates edge with position sizing and with the
compounding path; R-expectancy is the property of the strategy itself."* Everything here
reports R first and currency second, and the engine's two-pass design is what makes that
distinction real rather than nominal (see ``engine``'s docstring).

Three rules from section 4 and 5 are enforced here rather than left to whoever writes the
report:

* **Every breakdown cell carries its ``n``, and small cells are labelled.** Section 4.2:
  under 30 is *not reportable*, 30–99 is *suggestive* only. ``Cell.label`` returns the word,
  so a table cannot quietly present a 12-trade cell as a finding.
* **``n_eff`` sits next to ``n``.** Trades are not independent (section 5.3), so ``n`` is
  not a subgroup's information content.
* **Per-setup expectancy is computed alongside per-trade** (section 4.4). Models B–E do
  not always fill, so per-trade comparison is invalid on its own: *"a model that fills 35%
  of the time on the best-looking third of setups will show a superior win rate and a worse
  total return."*

**Kelly is computed and never used.** Section 4.1 lists it as *"reported only, never used
for sizing"*, and SPEC 18.1's sizing function has no way to receive it even if someone
wanted to — that is the invariant doing its job one module away.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable, Sequence

import numpy as np

from bot.backtest.engine import BacktestResult, Trade
from bot.research.stats import bootstrap_ci, effective_sample_size

#: BACKTEST_PROTOCOL section 5.3: mean block length for the stationary block bootstrap,
#: in trades.  20 trading days is the protocol's figure; on a strategy producing a few
#: trades a week that is a similar number of trades.
BLOCK = 20
TRADING_DAYS = 252


@dataclass(frozen=True)
class Cell:
    """One breakdown cell, with the sample-size discipline attached to it."""

    key: str
    n: int
    n_eff: float
    expectancy_r: float
    win_rate: float
    total_r: float

    @property
    def label(self) -> str:
        """Section 4.2's own words, so a table cannot overstate a thin cell."""
        if self.n < 30:
            return "not reportable"
        if self.n < 100:
            return "suggestive"
        return "reportable"

    @property
    def reportable(self) -> bool:
        return self.n >= 100


@dataclass(frozen=True)
class Metrics:
    """Section 4.1, computed from one variant's closed trades and equity curve."""

    n: int
    n_eff: float
    wins: int
    losses: int
    win_rate: float
    expectancy_r: float
    expectancy_r_ci: tuple[float, float]
    expectancy_r_ci_block: tuple[float, float]
    total_r: float
    profit_factor: float
    avg_win_r: float
    avg_loss_r: float
    largest_win_r: float
    largest_loss_r: float
    max_consecutive_wins: int
    max_consecutive_losses: int
    avg_duration_bars: float
    net_return_pct: float
    cagr_pct: float
    max_drawdown_pct: float
    max_drawdown_r: float
    drawdown_duration_days: float
    mar: float
    sharpe: float
    sortino: float
    ulcer: float
    time_in_market_pct: float
    kelly_fraction: float
    censored: int
    intrabar_ambiguous: int
    gapped: int

    @property
    def reportable(self) -> bool:
        """Section 5.1: a headline strategy result needs 200 trades."""
        return self.n >= 200


def _streak(flags: Sequence[bool], want: bool) -> int:
    best = run = 0
    for f in flags:
        run = run + 1 if f == want else 0
        best = max(best, run)
    return best


def _drawdown(equity: Sequence[float]) -> tuple[float, int]:
    """Maximum drawdown as a fraction, and its duration in curve points."""
    if len(equity) < 2:
        return 0.0, 0
    arr = np.asarray(equity, dtype=np.float64)
    peaks = np.maximum.accumulate(arr)
    dd = np.where(peaks > 0, (peaks - arr) / peaks, 0.0)
    worst = float(dd.max())
    # Longest stretch below a running peak, which is the duration that matters -- the
    # depth says how bad it was and this says how long it had to be lived with.
    longest = cur = 0
    for i in range(len(arr)):
        cur = 0 if arr[i] >= peaks[i] else cur + 1
        longest = max(longest, cur)
    return worst, longest


def _ulcer(equity: Sequence[float]) -> float:
    if len(equity) < 2:
        return 0.0
    arr = np.asarray(equity, dtype=np.float64)
    peaks = np.maximum.accumulate(arr)
    dd = np.where(peaks > 0, (peaks - arr) / peaks * 100.0, 0.0)
    return float(np.sqrt(np.mean(dd**2)))


def _daily_returns(curve: Sequence[tuple[datetime, float]]) -> np.ndarray:
    """Equity sampled to one point per calendar day, then differenced.

    Section 4.1 asks for Sharpe on *daily* equity annualised by sqrt(252). Computing it
    per trade instead inflates it by however many trades a day happens to contain, which
    is a function of the strategy's frequency and not of its risk.
    """
    if len(curve) < 3:
        return np.zeros(0)
    by_day: dict = {}
    for at, eq in curve:
        by_day[at.date()] = eq
    vals = np.asarray([by_day[d] for d in sorted(by_day)], dtype=np.float64)
    if len(vals) < 3:
        return np.zeros(0)
    with np.errstate(divide="ignore", invalid="ignore"):
        rets = np.diff(vals) / vals[:-1]
    return rets[np.isfinite(rets)]


def compute(
    result: BacktestResult,
    *,
    seed: int = 20260827,
    n_boot: int = 10_000,
    total_bars: int | None = None,
) -> Metrics:
    trades = result.trades
    n = len(trades)
    if n == 0:
        z = (float("nan"), float("nan"))
        return Metrics(
            0, 0.0, 0, 0, float("nan"), float("nan"), z, z, 0.0, float("nan"),
            float("nan"), float("nan"), float("nan"), float("nan"), 0, 0, float("nan"),
            0.0, float("nan"), 0.0, 0.0, 0.0, float("nan"), float("nan"), float("nan"),
            0.0, 0.0, float("nan"), 0, 0, 0,
        )

    rng = np.random.default_rng(seed)
    r = np.asarray([t.r_net for t in trades], dtype=np.float64)
    wins, losses = r[r > 0], r[r <= 0]
    curve = [eq for _, eq in result.equity_curve]

    gross_win = float(wins.sum()) if wins.size else 0.0
    gross_loss = float(-losses.sum()) if losses.size else 0.0
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")

    start, end = curve[0], curve[-1]
    span_days = max(
        1.0,
        (result.equity_curve[-1][0] - result.equity_curve[0][0]).total_seconds() / 86400.0,
    )
    years = span_days / 365.25
    cagr = ((end / start) ** (1 / years) - 1) * 100.0 if start > 0 and years > 0 else float("nan")

    dd, dd_len = _drawdown(curve)
    daily = _daily_returns(result.equity_curve)
    sharpe = (
        float(daily.mean() / daily.std(ddof=1) * math.sqrt(TRADING_DAYS))
        if daily.size > 2 and daily.std(ddof=1) > 0
        else float("nan")
    )
    downside = daily[daily < 0]
    sortino = (
        float(daily.mean() / downside.std(ddof=1) * math.sqrt(TRADING_DAYS))
        if downside.size > 2 and downside.std(ddof=1) > 0
        else float("nan")
    )

    # Kelly on the win/loss geometry.  Reported only (section 4.1).
    wr = len(wins) / n
    aw = float(wins.mean()) if wins.size else 0.0
    al = float(-losses.mean()) if losses.size else 0.0
    kelly = (wr - (1 - wr) / (aw / al)) if aw > 0 and al > 0 else float("nan")

    bars_in = sum(t.duration_bars for t in trades)
    tim = (bars_in / total_bars * 100.0) if total_bars else float("nan")

    # Drawdown in R: the same curve measured in units of risk rather than of equity, so
    # it is comparable across account sizes and is what section 4.1 asks for alongside.
    cum = np.cumsum(r)
    peak_r = np.maximum.accumulate(np.concatenate([[0.0], cum]))
    dd_r = float((peak_r - np.concatenate([[0.0], cum])).max())

    return Metrics(
        n=n,
        n_eff=effective_sample_size(r),
        wins=int(wins.size),
        losses=int(losses.size),
        win_rate=wr,
        expectancy_r=float(r.mean()),
        expectancy_r_ci=bootstrap_ci(r, n_boot, rng),
        expectancy_r_ci_block=bootstrap_ci(r, min(n_boot, 2_000), rng, block=BLOCK),
        total_r=float(r.sum()),
        profit_factor=pf,
        avg_win_r=aw,
        avg_loss_r=-al,
        largest_win_r=float(r.max()),
        largest_loss_r=float(r.min()),
        max_consecutive_wins=_streak([x > 0 for x in r], True),
        max_consecutive_losses=_streak([x > 0 for x in r], False),
        avg_duration_bars=float(np.mean([t.duration_bars for t in trades])),
        net_return_pct=(end / start - 1) * 100.0 if start > 0 else float("nan"),
        cagr_pct=cagr,
        max_drawdown_pct=dd * 100.0,
        max_drawdown_r=dd_r,
        drawdown_duration_days=float(dd_len),
        mar=(cagr / (dd * 100.0)) if dd > 0 and np.isfinite(cagr) else float("nan"),
        sharpe=sharpe,
        sortino=sortino,
        ulcer=_ulcer(curve),
        time_in_market_pct=tim,
        kelly_fraction=kelly,
        censored=sum(1 for t in trades if t.censored),
        intrabar_ambiguous=sum(1 for t in trades if t.intrabar_ambiguous),
        gapped=sum(1 for t in trades if t.gapped),
    )


# ------------------------------------------------------- per-setup expectancy


@dataclass(frozen=True)
class ModelComparison:
    """Section 4.4's three numbers, plus the fourth they turn out to need.

    Section 4.4 defines ``E_setup = fill_rate x E_trade`` with ``fill_rate = filled setups
    / qualified setups``, which compares like with like **only if every model qualifies on
    the same setups**. On this project they do not: model A enters at the break price and
    stops at the sweep extreme, so its stop distance *is* the displacement leg, and SPEC
    16.3's 2.5-ATR cap rejects it on setups where that leg is large. Measured on the Phase
    14 fixture, model A arms on 66 of 165 setups against model C's 149 -- and the ones it
    loses have a **higher** median displacement (2.57 ATR against 2.10), so the cap takes
    its strongest setups specifically.

    ``e_all_setups`` divides by the shared denominator instead: every MSS setup in the
    stream, whether the model could arm on it or not. A model that cannot arm takes no
    trade and earns nothing, which is exactly what a per-setup figure is meant to charge
    for. See D-015 section 6.
    """

    model: str
    qualified_setups: int
    filled: int
    fill_rate: float
    e_trade: float
    e_setup: float
    e_all_setups: float
    total_setups: int
    shadow_n: int
    shadow_e: float

    @property
    def comparable(self) -> float:
        """``E_setup`` -- the one that answers "which model would I rather run?"

        It charges a model for the opportunities it declines to take, which per-trade
        expectancy does not.
        """
        return self.e_setup


def compare_models(results: dict, *, total_setups: int | None = None) -> list[ModelComparison]:
    """Section 4.4 / SPEC 15.5, over a paired run of the entry models.

    **Run this on results produced with ``apply_limits=False``.** SPEC 18.4's position cap
    rejects whichever model fills most, so a bake-off with the portfolio engaged measures
    the cap rather than the models. The limits belong in the equity-curve run, which is
    what SPEC 18.9's on/off pair is for.
    """
    out: list[ModelComparison] = []
    # The shared denominator: every setup the stream produced, identical for every model.
    shared = total_setups or max(
        (res.funnel.get("setups", 0) for res in results.values()), default=0
    )
    for model, res in results.items():
        q = res.qualified_setups
        r = [t.r_net for t in res.trades]
        e_trade = float(np.mean(r)) if r else float("nan")
        fr = (len(r) / q) if q else float("nan")
        shadows = [s.r_multiple for s in res.shadows]
        out.append(ModelComparison(
            model=getattr(model, "value", str(model)),
            qualified_setups=q,
            filled=len(r),
            fill_rate=fr,
            e_trade=e_trade,
            # E_setup charges the model for what it declined to take.  An unfilled setup
            # contributes 0, which is exactly what it paid.
            e_setup=(float(np.sum(r)) / q) if q else float("nan"),
            e_all_setups=(float(np.sum(r)) / shared) if shared else float("nan"),
            total_setups=shared,
            shadow_n=len(shadows),
            shadow_e=float(np.mean(shadows)) if shadows else float("nan"),
        ))
    return out


# ------------------------------------------------------------------ breakdowns


def breakdown(
    trades: Iterable[Trade], key: Callable[[Trade], object], *, name: str = ""
) -> list[Cell]:
    """One section 4.2 breakdown, as cells that know whether they may be reported."""
    groups: dict[object, list[float]] = defaultdict(list)
    for t in trades:
        groups[key(t)].append(t.r_net)
    cells: list[Cell] = []
    for k, vals in groups.items():
        arr = np.asarray(vals, dtype=np.float64)
        cells.append(Cell(
            key=str(k),
            n=len(arr),
            n_eff=effective_sample_size(arr),
            expectancy_r=float(arr.mean()),
            win_rate=float((arr > 0).mean()),
            total_r=float(arr.sum()),
        ))
    return sorted(cells, key=lambda c: c.n, reverse=True)


def session_matrix(trades: Iterable[Trade]) -> dict[tuple[str, str], Cell]:
    """BACKTEST_PROTOCOL section 4.2.1 -- required, not optional, and added by D-002.

    Cross-tabulates sweep session against entry session. Under H4-only confirmation the
    minimum sweep-to-MSS distance is two H4 bars, so a sweep during London can rarely be
    entered during London — and *"if the diagonal is nearly empty, the strategy being
    tested is not the one the brief's section 6 example describes, and the report must say
    so in those words."*
    """
    groups: dict[tuple[str, str], list[float]] = defaultdict(list)
    for t in trades:
        groups[(t.sweep_session, t.entry_session)].append(t.r_net)
    out: dict[tuple[str, str], Cell] = {}
    for k, vals in groups.items():
        arr = np.asarray(vals, dtype=np.float64)
        out[k] = Cell(f"{k[0]}->{k[1]}", len(arr), effective_sample_size(arr),
                      float(arr.mean()), float((arr > 0).mean()), float(arr.sum()))
    return out


def diagonal_share(matrix: dict[tuple[str, str], Cell]) -> float:
    """Fraction of trades whose sweep and entry fell in the same session."""
    total = sum(c.n for c in matrix.values())
    if not total:
        return float("nan")
    same = sum(c.n for (a, b), c in matrix.items() if a == b)
    return same / total


def rejection_expectancy(rejections: Iterable) -> list[Cell]:
    """SPEC 21.3's query: for each gate, what did the trades it rejected go on to do?

    *"A gate whose rejected population has positive expectancy is destroying edge; one
    whose rejected population has strongly negative expectancy is earning its place."*
    Computed from one run, so it costs nothing against the out-of-sample budget.
    """
    groups: dict[str, list[float]] = defaultdict(list)
    for r in rejections:
        if r.forward_return_atr is not None:
            groups[r.reason].append(r.forward_return_atr)
    cells = []
    for k, vals in groups.items():
        arr = np.asarray(vals, dtype=np.float64)
        cells.append(Cell(k, len(arr), effective_sample_size(arr), float(arr.mean()),
                          float((arr > 0).mean()), float(arr.sum())))
    return sorted(cells, key=lambda c: c.n, reverse=True)


def exit_reasons(trades: Iterable[Trade]) -> Counter:
    return Counter(t.exit_reason.value for t in trades)
