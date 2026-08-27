"""SPEC 18.9's three reporting requirements, plus what implementing them turned up.

SPEC 18.9 asks for exactly three things, and the shape of this module follows them:

1. *"Every limit is exercised by a synthetic scenario (forced losing streak, forced
   drawdown)."* -> ``run_scenarios``.
2. *"The realised distribution of risk-per-trade is reported: it MUST be a spike at
   ``risk.pct_per_trade`` with a lower tail only from lot rounding. Any mass above the
   nominal value is a sizing bug."* -> ``bot.core.risk.realised_risk_distribution``, fed
   from the fixture by the report script.
3. *"Reported both with limits on and off ... a strategy that is only profitable with a
   daily loss limit engaged is a strategy with a fragility the limit is hiding."* ->
   ``evaluate(..., apply_limits=False)`` in ``bot.core.trade``.

**Scenarios are the evidence here, and that is the gate's own choice of word.** Every
loss limit in SPEC 18.4 is defined on *closed* PnL, and nothing closes a trade until the
exit policy exists in Phase 14. A fixture measurement of "how often does the daily loss
limit bind" is therefore not available at this phase and would be invented if reported.
What *is* available is a constructed situation per limit, which proves the limit fires,
fires on the right input, and does not fire on a near-miss -- the near-miss half matters
as much, because a limit that fires on everything passes a naive "was it exercised?" check.

Two further studies live here because they answer questions the gate implies rather than
states.

**The stop-model bake-off (``stop_agreement`` / ``stop_correlations``).** SPEC 16.6 asks
for S1-S4 as paired variants on a shared setup stream, which is the same request SPEC 13.8
made for the four order-block definitions -- and D-012 established that four such variants
were worth **1.77** independent tests, not 4. The same arithmetic has to be run here before
any S1-S4 comparison is corrected for multiple testing, and for the same reason: S1 anchors
on the sweep extreme and S2 on the lowest low of a window that *starts* at the sweep
extreme, so they are the same number whenever no lower low occurred.

**The minimum viable account (``account_sweep``).** SPEC 18.2's lot-granularity rejections
are a function of equity, and the specification picks EUR2,000 for its example without
saying what the rule implies. Sweeping equity against the realised stop-distance
distribution answers "how small an account can actually trade this" -- a planning number
that transfers better than most things measured on a synthetic fixture, because it is a
property of the lot grid and the stop-distance *shape* rather than of the returns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from itertools import combinations
from typing import Sequence

import numpy as np

from bot.config.schema import AppConfig, SymbolSpec
from bot.core.displacement import Direction
from bot.core.risk import (
    ClosedTrade,
    OpenPosition,
    RiskLedger,
    RiskReject,
    drawdown_multiplier,
    position_size,
    size_for_setup,
)
from bot.core.stops import StopModel
from bot.research.stats import effective_tests

UTC = timezone.utc


# ------------------------------------------------------------------- scenarios


@dataclass(frozen=True)
class Scenario:
    """One constructed situation and what it proves.

    ``near_miss`` is not decoration. A limit that fires on its trigger has been shown to
    fire; a limit that also *fails to fire* one step below its trigger has been shown to
    fire **because of** the trigger. Without the second half, a check that rejected
    everything would pass this battery.
    """

    limit: str
    expected: str | None
    got: str | None
    near_miss_expected: str | None
    near_miss_got: str | None
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.got == self.expected and self.near_miss_got == self.near_miss_expected


def _ledger(cfg: AppConfig, equity: float = 100_000.0, **kw) -> RiskLedger:
    return RiskLedger(cfg, equity=equity, **kw)


def _pos(symbol: str, i: int, risk_pct: float = 0.35, bullish: bool = True) -> OpenPosition:
    return OpenPosition(
        setup_id=f"s{i}",
        symbol=symbol,
        direction=Direction.BULLISH if bullish else Direction.BEARISH,
        risk_pct=risk_pct,
        opened_at=datetime(2026, 3, 2, 12, tzinfo=UTC),
    )


def _check(led: RiskLedger, at: datetime, symbol: str = "EURUSD", **kw) -> str | None:
    r = led.check(at, symbol=symbol, direction=Direction.BULLISH,
                  risk_pct=kw.pop("risk_pct", 0.35), **kw)
    return r.value if r else None


def _losses(led: RiskLedger, days: Sequence[tuple[datetime, float]]) -> None:
    for at, pnl in days:
        led.record_close(ClosedTrade("EURUSD", at, pnl))


def run_scenarios(cfg: AppConfig) -> list[Scenario]:
    """One constructed scenario per SPEC 18.4 limit, plus 18.5's ladder and 18.6.

    Dates are chosen inside a single trading week (Mon 2 - Fri 6 March 2026) so that a
    daily scenario cannot be a weekly one by accident, and the weekly and monthly
    scenarios deliberately keep every individual day under the daily cap for the same
    reason. A limit that fires because a *different* limit's condition was also met has
    not been exercised.
    """
    out: list[Scenario] = []
    eq = 100_000.0
    mon = datetime(2026, 3, 2, 12, tzinfo=UTC)

    # --- SPEC 18.4 daily -----------------------------------------------------
    led = _ledger(cfg, eq)
    _losses(led, [(mon, -eq * cfg.risk.max_daily_loss_pct / 100.0)])
    got = _check(led, mon + timedelta(hours=4))
    near = _ledger(cfg, eq)
    _losses(near, [(mon, -eq * (cfg.risk.max_daily_loss_pct - 0.1) / 100.0)])
    out.append(Scenario(
        "max_daily_loss_pct", RiskReject.RISK_LIMIT_DAILY.value, got, None,
        _check(near, mon + timedelta(hours=4)),
        "one day's closed losses at the cap; the near miss is 0.1pp under it",
    ))

    # --- SPEC 18.4 weekly ----------------------------------------------------
    # Three days at 1.5% each: 4.5% for the week, and no single day near the 2% daily cap.
    led = _ledger(cfg, eq)
    _losses(led, [(mon + timedelta(days=d), -eq * 0.015) for d in range(3)])
    got = _check(led, mon + timedelta(days=2, hours=4))
    near = _ledger(cfg, eq)
    _losses(near, [(mon + timedelta(days=d), -eq * 0.012) for d in range(3)])
    out.append(Scenario(
        "max_weekly_loss_pct", RiskReject.RISK_LIMIT_WEEKLY.value, got, None,
        _check(near, mon + timedelta(days=2, hours=4)),
        "3 x 1.5% across one trading week; every day under the daily cap",
    ))

    # --- SPEC 18.4 monthly ---------------------------------------------------
    # Three weeks at 3.0% each: 9% for the month, every week under the 4% weekly cap and
    # every day under the 2% daily one.
    led = _ledger(cfg, eq)
    marks = [
        mon + timedelta(days=7 * w + d) for w in range(3) for d in range(3)
    ]
    _losses(led, [(t, -eq * 0.010) for t in marks])
    got = _check(led, marks[-1] + timedelta(hours=4))
    near = _ledger(cfg, eq)
    _losses(near, [(t, -eq * 0.008) for t in marks])
    out.append(Scenario(
        "max_monthly_loss_pct", RiskReject.RISK_LIMIT_MONTHLY.value, got, None,
        _check(near, marks[-1] + timedelta(hours=4)),
        "9 x 1.0% across three weeks of one month; every week and day under their caps",
    ))

    # --- SPEC 18.4 consecutive losses ---------------------------------------
    led = _ledger(cfg, eq)
    for i in range(cfg.risk.max_consecutive_losses):
        led.record_close(ClosedTrade("EURUSD", mon + timedelta(hours=i), -10.0))
    got = _check(led, mon + timedelta(hours=6))
    near = _ledger(cfg, eq)
    for i in range(cfg.risk.max_consecutive_losses - 1):
        near.record_close(ClosedTrade("EURUSD", mon + timedelta(hours=i), -10.0))
    out.append(Scenario(
        "max_consecutive_losses", RiskReject.RISK_LIMIT_CONSECUTIVE.value, got, None,
        _check(near, mon + timedelta(hours=6)),
        f"{cfg.risk.max_consecutive_losses} losses in a row pauses for "
        f"{cfg.risk.consecutive_loss_pause_hours}h; the near miss is one short",
    ))

    # The pause expires on its own -- a halt that never lifts is a different limit.
    led2 = _ledger(cfg, eq)
    for i in range(cfg.risk.max_consecutive_losses):
        led2.record_close(ClosedTrade("EURUSD", mon + timedelta(hours=i), -10.0))
    after = mon + timedelta(hours=cfg.risk.consecutive_loss_pause_hours + 6)
    out.append(Scenario(
        "consecutive_loss_pause_hours", None, _check(led2, after),
        RiskReject.RISK_LIMIT_CONSECUTIVE.value,
        _check(led2, mon + timedelta(hours=6)),
        "the pause lifts by itself; the 'near miss' column is the same ledger during it",
    ))

    # --- SPEC 18.4 position counts ------------------------------------------
    led = _ledger(cfg, eq)
    for i in range(cfg.risk.max_open_positions):
        led.open(_pos(f"SYM{i}USD", i))
    near = _ledger(cfg, eq)
    for i in range(cfg.risk.max_open_positions - 1):
        near.open(_pos(f"SYM{i}USD", i))
    out.append(Scenario(
        "max_open_positions", RiskReject.RISK_LIMIT_POSITIONS.value,
        _check(led, mon), None, _check(near, mon),
        f"{cfg.risk.max_open_positions} concurrent positions",
    ))

    led = _ledger(cfg, eq)
    led.open(_pos("EURUSD", 0))
    near = _ledger(cfg, eq)
    near.open(_pos("GBPUSD", 0))
    out.append(Scenario(
        "max_positions_per_symbol", RiskReject.RISK_LIMIT_SYMBOL.value,
        _check(led, mon, symbol="EURUSD"), None, _check(near, mon, symbol="EURUSD"),
        "the near miss holds the same count in a different symbol",
    ))

    # --- SPEC 18.7 correlation cluster --------------------------------------
    clusters = {"EURUSD": "C+", "GBPUSD": "C+", "AUDUSD": "C+", "USDCHF": "C-"}
    led = _ledger(cfg, eq, clusters=clusters)
    led.open(_pos("GBPUSD", 0))
    led.open(_pos("AUDUSD", 1))
    near = _ledger(cfg, eq, clusters=clusters)
    near.open(_pos("GBPUSD", 0))
    out.append(Scenario(
        "max_correlated_positions", RiskReject.RISK_LIMIT_CORRELATION.value,
        _check(led, mon, symbol="EURUSD"), None, _check(near, mon, symbol="EURUSD"),
        f"{cfg.risk.max_correlated_positions} in one cluster, under the position cap",
    ))

    # Directional equivalence (SPEC 18.7): long EURUSD and SHORT USDCHF are one exposure.
    led = _ledger(cfg, eq, clusters=clusters)
    led.open(_pos("GBPUSD", 0))
    led.open(_pos("USDCHF", 1, bullish=False))
    opposite = _ledger(cfg, eq, clusters=clusters)
    opposite.open(_pos("GBPUSD", 0))
    opposite.open(_pos("USDCHF", 1, bullish=True))
    out.append(Scenario(
        "correlated (directional equivalence)", RiskReject.RISK_LIMIT_CORRELATION.value,
        _check(led, mon, symbol="EURUSD"), None,
        _check(opposite, mon, symbol="EURUSD"),
        "long EURUSD + short USDCHF is one exposure; long USDCHF is the other side",
    ))

    # --- SPEC 18.4 total open risk ------------------------------------------
    # Reachable only with a risk_pct outside the tunable band -- see D-014 section 2.
    led = _ledger(cfg, eq)
    led.open(_pos("GBPUSD", 0, risk_pct=0.50))
    led.open(_pos("AUDUSD", 1, risk_pct=0.50))
    got = _check(led, mon, risk_pct=0.60)
    legal = _ledger(cfg, eq)
    legal.open(_pos("GBPUSD", 0, risk_pct=0.50))
    legal.open(_pos("AUDUSD", 1, risk_pct=0.50))
    out.append(Scenario(
        "max_total_open_risk_pct", RiskReject.RISK_LIMIT_EXPOSURE.value, got, None,
        _check(legal, mon, risk_pct=0.50),
        "UNREACHABLE legally: the near miss is the largest legal third trade (0.50%), "
        "which lands on exactly the cap; the trigger needs 0.60%, outside [0.10, 0.50]",
    ))

    # --- SPEC 18.4 spread ----------------------------------------------------
    spec = cfg.symbol_specs["EURUSD"]
    pip = spec.pip_size
    cap = cfg.risk.max_spread_pips["default"]
    led = _ledger(cfg, eq)
    out.append(Scenario(
        "max_spread_pips", RiskReject.SPREAD_TOO_WIDE.value,
        _check(led, mon, spread=(cap + 0.5) * pip, sl_distance=60 * pip), None,
        _check(led, mon, spread=(cap - 0.5) * pip, sl_distance=60 * pip),
        f"absolute cap {cap} pips, measured against a wide (60-pip) stop so the relative "
        f"cap cannot be what fires",
    ))
    led = _ledger(cfg, eq)
    out.append(Scenario(
        "max_spread_pct_of_sl", RiskReject.SPREAD_TOO_WIDE.value,
        _check(led, mon, spread=1.5 * pip, sl_distance=10 * pip), None,
        _check(led, mon, spread=1.5 * pip, sl_distance=30 * pip),
        "1.5 pips is inside the 2.0-pip absolute cap either way; only the stop changes",
    ))

    # --- SPEC 18.6 kill switch ----------------------------------------------
    led = _ledger(cfg, eq)
    led.mark_equity(eq * (1 - cfg.risk.equity_dd_kill_pct / 100.0))
    near = _ledger(cfg, eq)
    near.mark_equity(eq * (1 - (cfg.risk.equity_dd_kill_pct - 0.5) / 100.0))
    out.append(Scenario(
        "equity_dd_kill_pct", RiskReject.KILL_SWITCH.value, _check(led, mon), None,
        _check(near, mon),
        "measured on equity INCLUDING floating PnL, unlike the loss limits",
    ))

    led = _ledger(cfg, eq)
    for wk in range(2):
        for i in range(cfg.risk.max_consecutive_losses):
            led.record_close(
                ClosedTrade("EURUSD", mon + timedelta(days=wk, hours=i), -10.0)
            )
    near = _ledger(cfg, eq)
    for i in range(cfg.risk.max_consecutive_losses):
        near.record_close(ClosedTrade("EURUSD", mon + timedelta(hours=i), -10.0))
    out.append(Scenario(
        "consecutive streak twice in a week", RiskReject.KILL_SWITCH.value,
        _check(led, mon + timedelta(days=5)), RiskReject.RISK_LIMIT_CONSECUTIVE.value,
        _check(near, mon + timedelta(hours=6)),
        "SPEC 18.6: twice in one week is a halt, not the timeout one streak causes",
    ))

    led = _ledger(cfg, eq)
    led.trip()
    out.append(Scenario(
        "manual kill switch", RiskReject.KILL_SWITCH.value, _check(led, mon), None,
        _check(_ledger(cfg, eq), mon),
        "SPEC 18.6's file trigger, so the person watching the screen can use it",
    ))

    # --- SPEC 18.2 sizing ----------------------------------------------------
    tiny = size_for_setup(cfg, symbol="EURUSD", equity=200.0, risk_pct=0.10,
                          sl_distance=0.0060)
    okay = size_for_setup(cfg, symbol="EURUSD", equity=20_000.0, risk_pct=0.35,
                          sl_distance=0.0026)
    out.append(Scenario(
        "min_lot", RiskReject.SIZE_BELOW_MIN.value,
        tiny.reason.value if tiny.reason else None, None,
        okay.reason.value if okay.reason else None,
        "a $200 account at 0.10% with a 60-pip stop wants 0.0033 lots",
    ))
    big = position_size(1e9, 0.50, 0.0026, spec=SymbolSpec(), value_per_unit=100_000.0,
                        min_realised_fraction=cfg.risk.min_realised_fraction)
    out.append(Scenario(
        "max_lot", RiskReject.SIZE_ABOVE_MAX.value,
        big.reason.value if big.reason else None, None,
        okay.reason.value if okay.reason else None,
        "a $1bn account clears the 100-lot ceiling",
    ))

    # min_realised_fraction is unreachable at its default (D-014 section 3), so the
    # scenario has to raise the threshold to reach it at all.  Reported as what it is.
    raised = position_size(2_000.0, 0.25, 26 * 0.0001, spec=SymbolSpec(),
                           value_per_unit=100_000.0, min_realised_fraction=0.75)
    at_default = position_size(2_000.0, 0.25, 26 * 0.0001, spec=SymbolSpec(),
                               value_per_unit=100_000.0,
                               min_realised_fraction=cfg.risk.min_realised_fraction)
    out.append(Scenario(
        "min_realised_fraction", RiskReject.SIZE_UNDER_RISK.value,
        raised.reason.value if raised.reason else None, None,
        at_default.reason.value if at_default.reason else None,
        "UNREACHABLE at the 0.5 default: the trigger needs 0.75, and the 'near miss' is "
        "SPEC 18.2's own worked example at the real default",
    ))

    return out


def ladder_profile(cfg: AppConfig) -> list[tuple[float, float]]:
    """The drawdown ladder sampled either side of every threshold (SPEC 18.5).

    Sampled rather than asserted from the table, because what matters is the function the
    code computes, not the pairs the config declares -- an off-by-one in the comparison
    would leave the table looking correct.
    """
    peak = 100_000.0
    points: list[float] = [0.0]
    for threshold, _ in cfg.risk.dd_ladder:
        points += [threshold - 0.01, threshold, threshold + 0.01]
    points.append(cfg.risk.equity_dd_kill_pct)
    return [
        (dd, drawdown_multiplier(cfg, peak_equity=peak, equity=peak * (1 - dd / 100.0)))
        for dd in sorted(set(points))
    ]


# ------------------------------------------------------- the stop-model bake-off


@dataclass
class StopProposals:
    """One setup's four stop prices, and the exogenous anchor they are measured from."""

    setup_id: str
    atr: float
    break_close: float
    direction: Direction
    entry_price: float
    stops: dict[str, float] = field(default_factory=dict)

    def offset(self, model: StopModel | str) -> float | None:
        """ATR-normalised distance from the break bar's close.

        **Anchored on the break close, which no stop model produced.** D-012 section 3a:
        centring on the per-observation mean across the variables being compared pins the
        average pairwise correlation at ``-1/(k-1)`` -- a number about the centring, not
        about the models. The anchor has to be exogenous.
        """
        p = self.stops.get(StopModel(model).value)
        if p is None or self.atr <= 0:
            return None
        return (self.break_close - p) / self.atr


def stop_agreement(
    rows: Sequence[StopProposals],
    models: Sequence[StopModel],
    tolerance_atr: float = 0.05,
) -> dict[tuple[str, str], tuple[int, float, float]]:
    """Pairwise agreement: ``(n, exact_fraction, within_tolerance_fraction)``.

    Both fractions, because D-012 section 2 found that exact agreement *understates*
    redundancy -- two definitions that pick different bars at almost the same price are
    economically one definition and arithmetically two. Here the same thing happens
    between S1 and S2: they are identical whenever no bar in the setup window went below
    the sweep extreme, and a hair apart when one did.
    """
    out: dict[tuple[str, str], tuple[int, float, float]] = {}
    for a, b in combinations(models, 2):
        pairs = [
            (r.stops.get(a.value), r.stops.get(b.value), r.atr)
            for r in rows
            if r.stops.get(a.value) is not None and r.stops.get(b.value) is not None
        ]
        if not pairs:
            out[(a.value, b.value)] = (0, float("nan"), float("nan"))
            continue
        exact = sum(1 for x, y, _ in pairs if x == y)
        close = sum(1 for x, y, at in pairs if at > 0 and abs(x - y) / at <= tolerance_atr)
        n = len(pairs)
        out[(a.value, b.value)] = (n, exact / n, close / n)
    return out


def stop_correlations(
    rows: Sequence[StopProposals], models: Sequence[StopModel]
) -> tuple[np.ndarray, int]:
    """Listwise-complete correlation matrix of ATR-normalised stop offsets, and its ``n``.

    Listwise, matching ``ob_study.price_correlations`` and for the same reason: a
    pairwise-complete matrix need not be positive semi-definite, and ``effective_tests``
    decomposes its variance -- negative eigenvalues there would produce a number that
    looks reasonable and means nothing.
    """
    complete = [r for r in rows if all(r.offset(m) is not None for m in models)]
    k = len(models)
    if len(complete) < 3:
        return np.full((k, k), np.nan), len(complete)
    m = np.asarray([[r.offset(d) for r in complete] for d in models], dtype=np.float64)
    if np.allclose(m.std(axis=1), 0):
        return np.full((k, k), np.nan), len(complete)
    with np.errstate(invalid="ignore", divide="ignore"):
        corr = np.corrcoef(m)
    return corr, len(complete)


def stop_effective_tests(
    rows: Sequence[StopProposals], models: Sequence[StopModel]
) -> tuple[float, int]:
    """``M_eff`` for the stop models, by the same Galwey estimator D-012 settled on."""
    corr, n = stop_correlations(rows, models)
    return effective_tests(corr), n


# ------------------------------------------------------ the minimum viable account


@dataclass(frozen=True)
class AccountRow:
    equity: float
    n: int
    accepted: int
    below_min: int
    under_risk: int
    median_lots: float

    @property
    def acceptance(self) -> float:
        return self.accepted / self.n if self.n else float("nan")


def account_sweep(
    cfg: AppConfig,
    sl_distances: Sequence[float],
    equities: Sequence[float],
    *,
    symbol: str = "EURUSD",
    risk_pct: float | None = None,
) -> list[AccountRow]:
    """How much of the setup stream each account size can actually size (SPEC 18.2).

    The rejections here are not a property of the strategy: the same setup at the same
    stop distance is tradable on one account and not on another. That makes this the one
    place in the risk layer where a *reported* result depends on a number
    (``account.starting_equity``) chosen for reporting -- so it is swept rather than
    quoted, and the report gives the whole curve.
    """
    pct = cfg.risk.pct_per_trade if risk_pct is None else risk_pct
    rows: list[AccountRow] = []
    for eq in equities:
        results = [
            size_for_setup(cfg, symbol=symbol, equity=eq, risk_pct=pct, sl_distance=d)
            for d in sl_distances
        ]
        lots = [r.lots for r in results if r.ok]
        rows.append(
            AccountRow(
                equity=eq,
                n=len(results),
                accepted=sum(1 for r in results if r.ok),
                below_min=sum(
                    1 for r in results if r.reason is RiskReject.SIZE_BELOW_MIN
                ),
                under_risk=sum(
                    1 for r in results if r.reason is RiskReject.SIZE_UNDER_RISK
                ),
                median_lots=float(np.median(lots)) if lots else float("nan"),
            )
        )
    return rows


def minimum_viable_equity(rows: Sequence[AccountRow], target: float = 0.95) -> float:
    """The smallest swept equity whose acceptance rate reaches ``target``.

    ``nan`` when no swept value does, which is a real answer rather than a missing one.
    """
    for row in sorted(rows, key=lambda r: r.equity):
        if row.acceptance >= target:
            return row.equity
    return float("nan")
