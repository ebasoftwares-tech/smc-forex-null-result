"""Position sizing and the risk limits (SPEC 18).

The module is split in two along the line SPEC 18.1 draws, and the split is the point:

* **``position_size`` is a pure function of ``(equity, risk_pct, sl_distance)`` plus the
  instrument's quantisation.** It is given no trade history, no PnL, no streak counter and
  no ledger, so martingale, loss-recovery sizing and averaging down are not "discouraged"
  -- they are unimplementable. SPEC 18.1 puts it plainly: withholding the information is
  *"the only reliable way to prevent them"*.
* **``RiskLedger`` is where the state lives**, and everything it can do to a trade is
  *reject it* or *reduce* ``risk_pct``. ``risk_multiplier`` is clamped to 1.0 and the
  ladder is validated monotone at config-load time, so no configuration can express an
  increase and no code path can apply one.

Three findings from implementing SPEC 18 are recorded in D-014 and referenced from the
checks they belong to.

**``min_realised_fraction`` is provably dead at its default (D-014 section 3).** SPEC 18.2
rejects when the lot-rounded risk falls below half the intended risk. Let ``k =
floor(raw_lots / lot_step) >= 1``; then ``lots = k x step`` and ``raw_lots < (k+1) x
step``, so ``realised / intended = k x step / raw_lots > k / (k+1) >= 1/2``. The ratio is
*strictly greater than one half for every lot grid and every stop distance*, so the check
at 0.5 can never fire -- 0 fires in 400,000 randomised sizings, with the worst accepted
fraction at 0.500081. It does not fire on SPEC 18.2's own worked example either: a
EUR2,000 account at 0.25% with a 26-pip stop wants 0.0192 lots and gets 0.01, a fraction
of **0.52**, which the prose describes as taking "half the intended risk" and the
threshold lets through. Implemented as specified and left alone: moving a FROZEN default
to make a check fire is a decision, not an implementation detail.

**``max_total_open_risk_pct`` is unreachable under every legal configuration (D-014
section 2).** ``max_open_positions`` is 3 and ``pct_per_trade`` is bounded above at 0.50%,
so total open risk tops out at exactly 1.50% -- the cap itself, which "would breach"
does not reach. Everywhere below the top of the tunable band it is strictly lower. The
position count always binds first. The check is implemented and scenario-tested, and the
scenario has to step outside the legal band to reach it.

**The loss limits cannot be measured on this phase's fixture.** Daily, weekly, monthly and
consecutive-loss limits are all defined on *closed* PnL, and nothing closes a trade until
the exit policy exists in Phase 14. That is precisely why SPEC 27's gate for this phase
says "every limit exercised by **scenario**" rather than "by fixture": the scenarios are
the evidence here, and how often each limit actually binds is a Phase 14 measurement.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Iterable, Mapping, Sequence

import numpy as np

from bot.config.schema import AppConfig, SymbolSpec
from bot.core.displacement import Direction
from bot.core.stops import symbol_spec
from bot.data.calendar import DayBoundary, week_start_utc


class RiskReject(str, Enum):
    """SPEC 19 items 18-20, plus the kill switch (item 25)."""

    SIZE_BELOW_MIN = "SIZE_BELOW_MIN"
    SIZE_ABOVE_MAX = "SIZE_ABOVE_MAX"
    SIZE_UNDER_RISK = "SIZE_UNDER_RISK"
    SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"
    RISK_LIMIT_DAILY = "RISK_LIMIT_DAILY"
    RISK_LIMIT_WEEKLY = "RISK_LIMIT_WEEKLY"
    RISK_LIMIT_MONTHLY = "RISK_LIMIT_MONTHLY"
    RISK_LIMIT_CONSECUTIVE = "RISK_LIMIT_CONSECUTIVE"
    RISK_LIMIT_EXPOSURE = "RISK_LIMIT_EXPOSURE"
    RISK_LIMIT_POSITIONS = "RISK_LIMIT_POSITIONS"
    RISK_LIMIT_SYMBOL = "RISK_LIMIT_SYMBOL"
    RISK_LIMIT_CORRELATION = "RISK_LIMIT_CORRELATION"
    KILL_SWITCH = "KILL_SWITCH"


class MissingConversionRate(LookupError):
    """SPEC 18.2: the absence of a conversion series **blocks** a symbol.

    Raised rather than defaulted. A silent 1.0 is the exact bug SPEC 18.2 describes --
    "a backtest that treats every symbol as if it had a fixed $10/pip is wrong by up to
    40% on JPY pairs over a five-year window" -- and it is invisible in the output,
    because every number it produces looks like a number.
    """


# --------------------------------------------------------------- sizing (pure)


def value_per_price_unit_per_lot(
    spec: SymbolSpec,
    account_ccy: str,
    *,
    quote_to_account: float | None = None,
) -> float:
    """SPEC 18.2: ``contract_size x fx_rate(quote_ccy -> account_ccy)``.

    For EURUSD on a USD account the quote currency *is* the account currency, the rate is
    1 by identity and no series is needed. For anything else the rate must be supplied;
    its absence raises. That is SPEC 18.2's rule stated as code: the conversion series is
    part of the dataset and its absence blocks the symbol.
    """
    if spec.quote_ccy == account_ccy.upper():
        return spec.contract_size
    if quote_to_account is None:
        raise MissingConversionRate(
            f"{spec.quote_ccy}->{account_ccy.upper()} rate required to size "
            f"{spec.base_ccy}{spec.quote_ccy} (SPEC 18.2)"
        )
    return spec.contract_size * quote_to_account


@dataclass(frozen=True)
class SizingResult:
    lots: float
    intended_risk: float
    realised_risk: float
    raw_lots: float
    value_per_unit: float
    reason: RiskReject | None = None

    @property
    def ok(self) -> bool:
        return self.reason is None

    @property
    def realised_fraction(self) -> float:
        return self.realised_risk / self.intended_risk if self.intended_risk > 0 else 0.0


def position_size(
    equity: float,
    risk_pct: float,
    sl_distance: float,
    *,
    spec: SymbolSpec,
    value_per_unit: float,
    min_realised_fraction: float,
) -> SizingResult:
    """SPEC 18.2, and SPEC 18.1's purity invariant made structural.

    **The parameter list is the invariant.** There is no history argument, no ledger, no
    streak counter and no way to add one without changing every call site, which is what
    ``test_sizing_is_a_pure_function_of_its_declared_inputs`` asserts by introspection
    rather than by behaviour -- a behavioural test can only show that the function did not
    use history on the inputs it was given.

    ``risk_pct`` is a percent (0.35 means 0.35%), matching SPEC 18.3's table. Any
    reduction the risk layer applies has already been applied by the caller: this function
    cannot tell an unmodified 0.35 from a laddered 0.35, and must not be able to.
    """
    intended = equity * risk_pct / 100.0
    if sl_distance <= 0 or value_per_unit <= 0 or intended <= 0:
        return SizingResult(0.0, max(intended, 0.0), 0.0, 0.0, value_per_unit,
                            RiskReject.SIZE_BELOW_MIN)

    raw = intended / (sl_distance * value_per_unit)
    # Floor, never round: rounding up takes more risk than the caller asked for, which is
    # the one direction SPEC 18.1 does not permit.  The 1e-9 guard is for binary
    # representation only -- 0.03 / 0.01 is 2.9999999999999996 in IEEE 754, and without it
    # every exact multiple of the lot step would quantise one step low.
    steps = math.floor(raw / spec.lot_step + 1e-9)
    lots = round(steps * spec.lot_step, 10)
    realised = lots * sl_distance * value_per_unit

    if lots < spec.min_lot:
        return SizingResult(lots, intended, realised, raw, value_per_unit,
                            RiskReject.SIZE_BELOW_MIN)
    if lots > spec.max_lot:
        return SizingResult(lots, intended, realised, raw, value_per_unit,
                            RiskReject.SIZE_ABOVE_MAX)
    if realised < min_realised_fraction * intended:
        # Unreachable at the default 0.5 -- see the module docstring and D-014 section 3.
        return SizingResult(lots, intended, realised, raw, value_per_unit,
                            RiskReject.SIZE_UNDER_RISK)
    return SizingResult(lots, intended, realised, raw, value_per_unit, None)


def size_for_setup(
    cfg: AppConfig,
    *,
    symbol: str,
    equity: float,
    risk_pct: float,
    sl_distance: float,
    quote_to_account: float | None = None,
) -> SizingResult:
    """Config-aware wrapper.  Still pure -- it reads configuration, never state."""
    spec = symbol_spec(cfg, symbol)
    return position_size(
        equity,
        risk_pct,
        sl_distance,
        spec=spec,
        value_per_unit=value_per_price_unit_per_lot(
            spec, cfg.account.currency, quote_to_account=quote_to_account
        ),
        min_realised_fraction=cfg.risk.min_realised_fraction,
    )


# ------------------------------------------------------- the drawdown ladder


def drawdown_multiplier(cfg: AppConfig, *, peak_equity: float, equity: float) -> float:
    """SPEC 18.5.  Monotone non-increasing in drawdown, and never above 1.0.

    Clamped here as well as validated at config load, because these are two different
    guarantees: the validator says no *configuration* can express an increase, and the
    clamp says no *arithmetic* can produce one. SPEC 18.1 calls this the anti-martingale
    invariant expressed at portfolio level.

    Note what the invariant does and does not say. The multiplier is monotone in
    **drawdown**, not in time: recovery genuinely restores it, so a sequence of wins after
    a loss raises risk back toward -- never above -- 1.00. A test that asserted
    monotonicity in time would be asserting something SPEC 18.5 explicitly denies.
    """
    if peak_equity <= 0:
        return 1.0
    dd = (peak_equity - equity) / peak_equity * 100.0
    mult = 1.0
    for threshold, m in cfg.risk.dd_ladder:
        if dd >= threshold:
            mult = m
    return min(mult, 1.0)


def kill_switch_breached(cfg: AppConfig, *, peak_equity: float, equity: float) -> bool:
    """SPEC 18.4/18.6's equity kill switch.  Measured on equity **including floating**."""
    if peak_equity <= 0:
        return False
    return (peak_equity - equity) / peak_equity * 100.0 >= cfg.risk.equity_dd_kill_pct


# --------------------------------------------------------------- the risk book


@dataclass(frozen=True)
class OpenPosition:
    """What the ledger needs to know about a live trade.  Deliberately not much."""

    setup_id: str
    symbol: str
    direction: Direction
    risk_pct: float
    opened_at: datetime


@dataclass(frozen=True)
class ClosedTrade:
    symbol: str
    closed_at: datetime
    pnl: float  # account currency, signed


@dataclass
class RiskState:
    """Everything the limits are evaluated against, in one place so it can be inspected."""

    equity: float
    peak_equity: float
    open_positions: list[OpenPosition] = field(default_factory=list)
    closed: list[ClosedTrade] = field(default_factory=list)
    consecutive_losses: int = 0
    paused_until: datetime | None = None
    manual_halt: bool = False
    monthly_halt: bool = False
    consecutive_lock_weeks: dict[date, int] = field(default_factory=dict)


def risk_day(cfg: AppConfig, at: datetime) -> date:
    """SPEC 18.4: the risk day is ``tf.day_boundary``'s day, not the calendar day.

    Stated in the specification as a consistency requirement rather than a nicety -- a
    "daily" loss limit and a "daily" bias have to refer to the same day, or a limit can
    halt trading for a day the bias engine thinks is still yesterday.
    """
    return DayBoundary(cfg.tf.day_boundary_tz, cfg.tf.day_boundary_time).trading_date(at)


def risk_week(cfg: AppConfig, at: datetime) -> datetime:
    """The trading week containing ``at`` (SPEC 1.5's week, Sunday 21:00 UTC by default).

    The FX week rather than the ISO week: a weekly loss limit that reset on Monday 00:00
    would leave Sunday's opening hours attached to the week that just ended.
    """
    return week_start_utc(at, cfg.week)


def risk_month(cfg: AppConfig, at: datetime) -> tuple[int, int]:
    d = risk_day(cfg, at)
    return d.year, d.month


class RiskLedger:
    """SPEC 18.4-18.7.  Stateful, and able only to reject or to reduce.

    Every method that could conceivably raise risk is absent by construction rather than
    guarded: there is no ``increase_risk``, no ``recover_losses``, and ``risk_multiplier``
    returns ``min(ladder, 1.0)``.
    """

    def __init__(
        self,
        cfg: AppConfig,
        *,
        equity: float | None = None,
        clusters: Mapping[str, str] | None = None,
    ) -> None:
        self.cfg = cfg
        eq = cfg.account.starting_equity if equity is None else equity
        self.state = RiskState(equity=eq, peak_equity=eq)
        #: symbol -> cluster id (SPEC 18.7).  Injected: correlation is measured from a
        #: daily-return panel that the ledger has no business owning.
        self.clusters = dict(clusters or {})

    # -- equity -------------------------------------------------------------

    def mark_equity(self, equity: float) -> None:
        """Update equity, **including floating PnL**, and the running peak.

        The peak only ever rises, which is what makes ``drawdown_multiplier`` monotone in
        drawdown rather than in whatever the equity did most recently.
        """
        self.state.equity = equity
        self.state.peak_equity = max(self.state.peak_equity, equity)

    def record_close(self, trade: ClosedTrade) -> None:
        self.state.closed.append(trade)
        if trade.pnl < 0:
            self.state.consecutive_losses += 1
            if self.state.consecutive_losses >= self.cfg.risk.max_consecutive_losses:
                self.state.paused_until = trade.closed_at + timedelta(
                    hours=self.cfg.risk.consecutive_loss_pause_hours
                )
                wk = risk_week(self.cfg, trade.closed_at).date()
                self.state.consecutive_lock_weeks[wk] = (
                    self.state.consecutive_lock_weeks.get(wk, 0) + 1
                )
                self.state.consecutive_losses = 0
        else:
            self.state.consecutive_losses = 0

    def open(self, position: OpenPosition) -> None:
        self.state.open_positions.append(position)

    def close(self, setup_id: str) -> None:
        self.state.open_positions = [
            p for p in self.state.open_positions if p.setup_id != setup_id
        ]

    # -- accounting ---------------------------------------------------------

    def _closed_pnl(self, predicate) -> float:
        return sum(t.pnl for t in self.state.closed if predicate(t))

    def daily_pnl(self, at: datetime) -> float:
        d = risk_day(self.cfg, at)
        return self._closed_pnl(lambda t: risk_day(self.cfg, t.closed_at) == d)

    def weekly_pnl(self, at: datetime) -> float:
        w = risk_week(self.cfg, at)
        return self._closed_pnl(lambda t: risk_week(self.cfg, t.closed_at) == w)

    def monthly_pnl(self, at: datetime) -> float:
        m = risk_month(self.cfg, at)
        return self._closed_pnl(lambda t: risk_month(self.cfg, t.closed_at) == m)

    def open_risk_pct(self) -> float:
        return sum(p.risk_pct for p in self.state.open_positions)

    def risk_multiplier(self) -> float:
        """SPEC 18.5.  The only thing in this class that touches ``risk_pct``."""
        return drawdown_multiplier(
            self.cfg, peak_equity=self.state.peak_equity, equity=self.state.equity
        )

    def effective_risk_pct(self, base_pct: float | None = None) -> float:
        base = self.cfg.risk.pct_per_trade if base_pct is None else base_pct
        return base * self.risk_multiplier()

    # -- the kill switch ----------------------------------------------------

    def kill_switch(self, at: datetime | None = None) -> bool:
        """SPEC 18.6.  Any one trigger is sufficient.

        ``max_consecutive_losses`` exceeded **twice in one week** is a kill-switch trigger
        distinct from the pause the first breach causes -- the pause is a timeout, this is
        a halt requiring manual re-enable.
        """
        if self.state.manual_halt:
            return True
        if kill_switch_breached(
            self.cfg, peak_equity=self.state.peak_equity, equity=self.state.equity
        ):
            return True
        return any(n >= 2 for n in self.state.consecutive_lock_weeks.values())

    def trip(self) -> None:
        """The manual trigger (SPEC 18.6): a file, a button, or a person."""
        self.state.manual_halt = True

    # -- the gate -----------------------------------------------------------

    def check(
        self,
        at: datetime,
        *,
        symbol: str,
        direction: Direction,
        risk_pct: float,
        spread: float | None = None,
        sl_distance: float | None = None,
    ) -> RiskReject | None:
        """Every SPEC 18.4 limit, in one place.  ``None`` means the trade may proceed.

        Ordered kill switch first, then the halts, then exposure, then the per-trade
        spread gates -- most-terminal to least, so a rejection log reports the reason that
        would still apply if the others were fixed.
        """
        cfg = self.cfg
        eq = self.state.equity

        if self.kill_switch(at):
            return RiskReject.KILL_SWITCH
        if self.state.monthly_halt:
            return RiskReject.RISK_LIMIT_MONTHLY
        if self.state.paused_until is not None and at < self.state.paused_until:
            return RiskReject.RISK_LIMIT_CONSECUTIVE

        # Loss limits are on CLOSED PnL (SPEC 18.4).  A limit expressed as a positive
        # percentage is breached by a loss at least that large, hence the sign flip.
        if eq > 0:
            if -self.daily_pnl(at) >= cfg.risk.max_daily_loss_pct / 100.0 * eq:
                return RiskReject.RISK_LIMIT_DAILY
            if -self.weekly_pnl(at) >= cfg.risk.max_weekly_loss_pct / 100.0 * eq:
                return RiskReject.RISK_LIMIT_WEEKLY
            if -self.monthly_pnl(at) >= cfg.risk.max_monthly_loss_pct / 100.0 * eq:
                self.state.monthly_halt = True  # requires manual re-enable (SPEC 18.4)
                return RiskReject.RISK_LIMIT_MONTHLY

        if len(self.state.open_positions) >= cfg.risk.max_open_positions:
            return RiskReject.RISK_LIMIT_POSITIONS
        same = sum(1 for p in self.state.open_positions if p.symbol == symbol)
        if same >= cfg.risk.max_positions_per_symbol:
            return RiskReject.RISK_LIMIT_SYMBOL
        if self._cluster_count(symbol, direction) >= cfg.risk.max_correlated_positions:
            return RiskReject.RISK_LIMIT_CORRELATION
        if self.open_risk_pct() + risk_pct > cfg.risk.max_total_open_risk_pct:
            # Unreachable within the tunable band -- see D-014 section 2.
            return RiskReject.RISK_LIMIT_EXPOSURE

        if spread is not None:
            spec = symbol_spec(cfg, symbol)
            cap_pips = _spread_cap_pips(cfg, symbol)
            if spread / spec.pip_size > cap_pips:
                return RiskReject.SPREAD_TOO_WIDE
            if sl_distance is not None and sl_distance > 0:
                if spread / sl_distance * 100.0 > cfg.risk.max_spread_pct_of_sl:
                    return RiskReject.SPREAD_TOO_WIDE
        return None

    def _cluster_count(self, symbol: str, direction: Direction) -> int:
        """SPEC 18.7: **directionally equivalent** exposure counts toward the cluster.

        Long EURUSD and short USDCHF are the same position, so a cluster is counted in
        signed units: a position whose correlation-adjusted direction matches the proposed
        one counts, and one opposing it does not. Cluster membership carries the sign of
        the correlation, which is why ``clusters`` maps a symbol to a signed cluster id
        (``"USD+"`` / ``"USD-"``) rather than to a bare name.
        """
        cluster = self.clusters.get(symbol.upper())
        if cluster is None:
            return 0
        want = _signed_cluster(cluster, direction)
        return sum(
            1
            for p in self.state.open_positions
            if self.clusters.get(p.symbol.upper()) is not None
            and _signed_cluster(self.clusters[p.symbol.upper()], p.direction) == want
        )


def _signed_cluster(cluster: str, direction: Direction) -> str:
    """Collapse (cluster, direction) into the exposure it actually represents."""
    base, sign = cluster[:-1], cluster[-1]
    flip = sign == "-"
    bullish = direction is Direction.BULLISH
    net = bullish != flip
    return f"{base}{'+' if net else '-'}"


def _spread_cap_pips(cfg: AppConfig, symbol: str) -> float:
    s = symbol.upper()
    table = cfg.risk.max_spread_pips
    if s in table:
        return table[s]
    if s.endswith("JPY") and "JPY" in table:
        return table["JPY"]
    return table["default"]


def binding_spread_cap_pips(cfg: AppConfig, symbol: str, sl_distance_pips: float) -> str:
    """Which of SPEC 18.4's two spread caps is tighter at this stop distance.

    The absolute cap is a constant and the relative cap is a percentage of the stop, so
    they cross at ``max_spread_pips / (max_spread_pct_of_sl / 100)`` pips of stop: 20 pips
    on a major, 35 on a JPY cross. Below the crossover the relative cap binds and the
    absolute one is decoration; above it, the reverse. Against SPEC 16.3's legal stop
    range that puts the relative cap in charge of the tightest **23%** of a major's range
    (8-20 of 8-60 pips) and **29%** of a JPY cross's (12-35 of 12-90) -- the tight-stop
    end, which is exactly where a spread does the most damage. See D-014 section 5.
    """
    absolute = _spread_cap_pips(cfg, symbol)
    crossover = absolute / (cfg.risk.max_spread_pct_of_sl / 100.0)
    return "max_spread_pct_of_sl" if sl_distance_pips < crossover else "max_spread_pips"


# ------------------------------------------------------------ correlation (18.7)


def correlation_clusters(
    returns: Mapping[str, Sequence[float]], cfg: AppConfig
) -> dict[str, str]:
    """SPEC 18.7: group symbols whose ``|rho|`` reaches the threshold, with a sign.

    Single-linkage over the thresholded correlation graph. A cluster's sign is resolved by
    walking the graph from its first member and flipping at every negative edge, so
    ``EURUSD`` and ``USDCHF`` -- reliably near ``-0.9`` -- land in one cluster with
    opposite signs, and ``_signed_cluster`` then treats long EURUSD and short USDCHF as
    the same exposure, which is what SPEC 18.7 means by "directionally equivalent".

    Single-linkage can chain (A~B, B~C, A!~C all in one cluster) and that is the intended
    conservatism here: the cap exists to stop three correlated bets being taken as three
    independent ones, and a chained cluster errs toward fewer positions.
    """
    syms = sorted(returns)
    if len(syms) < 2:
        return {}
    m = np.asarray([np.asarray(returns[s], dtype=np.float64) for s in syms])
    if m.shape[1] < 2 or np.allclose(m.std(axis=1), 0):
        return {}
    with np.errstate(invalid="ignore", divide="ignore"):
        corr = np.corrcoef(m)

    adjacency: dict[int, list[tuple[int, float]]] = {i: [] for i in range(len(syms))}
    for i in range(len(syms)):
        for j in range(i + 1, len(syms)):
            r = corr[i, j]
            if np.isfinite(r) and abs(r) >= cfg.risk.correlation_threshold:
                adjacency[i].append((j, r))
                adjacency[j].append((i, r))

    out: dict[str, str] = {}
    seen: set[int] = set()
    for root in range(len(syms)):
        if root in seen or not adjacency[root]:
            continue
        stack = [(root, 1)]
        members: list[tuple[int, int]] = []
        while stack:
            node, sign = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            members.append((node, sign))
            for nxt, r in adjacency[node]:
                if nxt not in seen:
                    stack.append((nxt, sign if r > 0 else -sign))
        name = syms[root]
        for node, sign in members:
            out[syms[node]] = f"{name}{'+' if sign > 0 else '-'}"
    return out


def realised_risk_distribution(results: Iterable[SizingResult]) -> dict[str, float]:
    """SPEC 18.9's reporting requirement, as numbers a report can assert on.

    *"It MUST be a spike at ``risk.pct_per_trade`` with a lower tail only from lot
    rounding. Any mass above the nominal value is a sizing bug."* ``max_fraction`` is the
    assertion: it must not exceed 1.0, and the flooring in ``position_size`` is what
    guarantees it.
    """
    fracs = [r.realised_fraction for r in results if r.ok]
    if not fracs:
        return {"n": 0.0}
    arr = np.asarray(fracs, dtype=np.float64)
    return {
        "n": float(arr.size),
        "min_fraction": float(arr.min()),
        "median_fraction": float(np.median(arr)),
        "max_fraction": float(arr.max()),
        "p05_fraction": float(np.percentile(arr, 5)),
        "above_nominal": float((arr > 1.0 + 1e-12).sum()),
    }
