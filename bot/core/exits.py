"""Trade management and exit resolution (SPEC 17.3, 17.4, 17.5).

This is the half of SPEC 17 that Phase 13 deliberately left, because all of it needs an
open trade. It is also where the specification puts its own headline warning.

**The stop-versus-target ambiguity is real, and it is not the same problem D-013 solved.**
Phase 12 found that a bar touching both a limit entry and its stop is *not* ambiguous:
price approaches the entry from one side, so any continuous path reaching the stop passed
the entry first, and the "conservative" coin-flip was both wrong and less conservative
(D-013 section 1). **That argument does not transfer here.** An open trade sits *between*
its stop and its target, so from the entry price both are reachable first and continuity
rules out neither. SPEC 17.5 calls this *"the single largest backtest bias"* and it is
right: this is the genuine version of the problem Phase 12 had a fake version of.

``backtest.intrabar_mode`` decides it:

* ``m1_path`` — replay the M1 bars inside the H4 bar and resolve in true order. *"The only
  correct option."*
* ``pessimistic`` — the stop is assumed hit first, always. Mandatory when M1 is absent, and
  its cost must be quantified by re-running a period where M1 exists under both modes.
* ``ohlc_heuristic`` — prohibited by SPEC 17.5 and not offered as a config value.

**Gaps are resolved before the ambiguity is even asked about.** A bar that opens beyond the
stop filled at the open, worse than the stop, and the realised R may be −2.4R rather than
−1R (SPEC 16.5). A bar that opens beyond the target filled at the open, better. Neither is
ambiguous; both are simply worse or better than planned, and recording them at the planned
price is the optimism SPEC 26 warns about.

**Break-even is off by default and the specification says why.** It *"reliably raises win
rate and reliably lowers expectancy on most systems; enabling it by default would flatter
the headline statistic that matters least."*

**MAE and MFE are computed here, once, and reused.** SPEC 17.7 asks for them precisely so
that a target model is not re-optimised on the same trades that will report the result.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

import numpy as np

from bot.config.schema import AppConfig, SymbolSpec
from bot.core.bars import BarSeries, from_epoch_s
from bot.core.costs import stop_slippage
from bot.core.displacement import Direction


class ExitReason(str, Enum):
    """Every way a trade can end.  SPEC 17.4 / 19; nothing closes without one."""

    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    BREAK_EVEN = "BREAK_EVEN"
    TRAILING_STOP = "TRAILING_STOP"
    TIME_STOP = "TIME_STOP"
    WEEKEND_CLOSE = "WEEKEND_CLOSE"
    #: The series ran out while the trade was open.  Censored, not closed -- counting it as
    #: a flat exit would understate both tails.
    END_OF_DATA = "END_OF_DATA"


@dataclass(frozen=True)
class ExitOutcome:
    """One closed trade's price path, before costs are applied."""

    reason: ExitReason
    bar: int
    at: datetime
    price: float
    r_multiple: float
    bars_held: int
    mae_r: float
    mfe_r: float
    bars_to_mae: int
    bars_to_mfe: int
    #: The stop actually in force at exit, which is not the planned stop once break-even
    #: or trailing has moved it.
    final_stop: float
    stop_moved: bool = False
    #: True when the deciding bar contained both the stop and the target and neither had
    #: gapped.  The one case ``intrabar_mode`` decides; counted so the report can say how
    #: much of the result rests on it.
    intrabar_ambiguous: bool = False
    gapped: bool = False

    @property
    def censored(self) -> bool:
        return self.reason is ExitReason.END_OF_DATA


def _is_weekend_close(at: datetime, cfg: AppConfig) -> bool:
    """SPEC 17.4: close at ``exit.weekend_close_utc`` on ``exit.weekend_close_day``.

    Compared on the bar's **close** time, since a position is closed at the end of the bar
    that reaches the cutoff, not at the start of it.
    """
    return at.weekday() == cfg.exit.weekend_close_dow and at.timetz().replace(
        tzinfo=None
    ) >= cfg.exit.weekend_close_utc


def manage_stop(
    cfg: AppConfig,
    *,
    direction: Direction,
    entry_price: float,
    planned_stop: float,
    current_stop: float,
    best_r: float,
    extreme_price: float,
    atr_value: float,
) -> float:
    """SPEC 17.3.  Returns the stop after break-even and trailing, never looser.

    **Monotone by construction**: a stop may only move toward price, never away from it.
    That is not a stylistic choice — a stop that can widen is a stop that can turn a −1R
    loss into a −2R one after the fact, and it makes the R denominator a moving target.

    **The clamp is the only thing enforcing that, deliberately.** An earlier version also
    clamped inside each branch (``max(stop, be)``, ``max(stop, trail)``), which made the
    final clamp unreachable — and therefore untestable, since no input could distinguish
    an implementation with it from one without. That is D-014 section 8's lesson recurring:
    when two mechanisms enforce one rule, the outer one hides the inner one from every
    test. Here the branches propose freely and one clamp decides, so a mutation removing
    it fails a test.
    """
    bullish = direction is Direction.BULLISH
    risk = abs(entry_price - planned_stop)
    if risk <= 0:
        return current_stop

    proposals = [current_stop]

    # --- break-even (SPEC 17.3) -------------------------------------------
    if cfg.manage.be_trigger_r > 0 and best_r >= cfg.manage.be_trigger_r:
        offset = cfg.manage.be_offset_atr * atr_value
        proposals.append(entry_price + offset if bullish else entry_price - offset)

    # --- trailing (SPEC 17.3) ---------------------------------------------
    if cfg.manage.trail_mode == "atr" and best_r >= cfg.manage.trail_start_r:
        d = cfg.manage.trail_atr_mult * atr_value
        proposals.append(extreme_price - d if bullish else extreme_price + d)

    # The one enforcement: toward price, never away from it.
    return max(proposals) if bullish else min(proposals)


def _r(direction: Direction, entry: float, price: float, risk: float) -> float:
    if risk <= 0:
        return 0.0
    d = price - entry if direction is Direction.BULLISH else entry - price
    return d / risk


def _m1_first(
    series: BarSeries,
    m1: BarSeries,
    bar: int,
    *,
    direction: Direction,
    stop: float,
    target: float | None,
) -> str:
    """Which of stop/target the M1 path inside ``bar`` reached first.

    Returns ``"stop"``, ``"target"`` or ``"neither"``. An M1 series that does not cover
    the bar returns ``"neither"``, and the caller then falls back to ``pessimistic`` —
    never to a guess, and never to the convenient answer.
    """
    bullish = direction is Direction.BULLISH
    lo = int(series.open_time[bar])
    hi = int(series.close_time[bar])
    start = int(np.searchsorted(m1.open_time, lo, side="left"))
    end = int(np.searchsorted(m1.open_time, hi, side="left"))
    for k in range(start, end):
        low, high = float(m1.low[k]), float(m1.high[k])
        hit_stop = low <= stop if bullish else high >= stop
        hit_tp = (
            target is not None and (high >= target if bullish else low <= target)
        )
        if hit_stop and hit_tp:
            # Both inside one M1 bar.  One minute is the finest resolution the dataset
            # has, so this is genuinely unresolvable and takes the pessimistic reading.
            return "stop"
        if hit_stop:
            return "stop"
        if hit_tp:
            return "target"
    return "neither"


def resolve_exit(
    series: BarSeries,
    cfg: AppConfig,
    *,
    direction: Direction,
    entry_bar: int,
    entry_price: float,
    planned_stop: float,
    target: float | None,
    atr: np.ndarray,
    spec: SymbolSpec,
    m1: BarSeries | None = None,
    apply_slippage: bool = True,
    entry_at_bar_open: bool = False,
) -> ExitOutcome:
    """Walk the trade forward bar by bar and close it (SPEC 17.3-17.5).

    The per-bar order is fixed and each step is there for a reason:

    1. **Gap checks first.** A bar that opened beyond a level filled at the open. Asking
       the ambiguity question about such a bar would answer it at the planned price, which
       is exactly the optimism SPEC 26 forbids.
    2. **Stop and target inside the bar.** If both, ``intrabar_mode`` decides; if one,
       there is nothing to decide.
    3. **MAE/MFE update**, from the bar's extremes, before management moves anything.
    4. **Management last**, so a stop moved by *this* bar cannot also be hit by it. Moving
       the stop first would let break-even trigger and stop out on one bar, which is a
       lookahead of exactly one bar within the trade.

    ``apply_slippage`` exists so a shadow trade (SPEC 15.6) can be priced on the same path
    without charging costs to an equity curve it never touched.

    **The entry bar is part of the walk, and getting that wrong is a systematic optimism.**
    The first version started at ``entry_bar + 1``, so a trade that filled and then hit its
    stop inside the same bar was carried to the next bar's open instead -- every such loss
    was delayed, and some were missed entirely. See D-015 section 5.

    The two fill shapes are not symmetric on that bar, and ``entry_at_bar_open`` is which:

    * **A market order fills at the bar's open**, so the whole bar happens after the fill
      and both levels are checkable exactly as on any later bar.
    * **A limit fills somewhere inside the bar**, and only what came after the fill counts.
      For a buy limit at ``p`` the fill is the first touch of ``p``, so the bar's low --
      which is below ``p`` -- necessarily came at or after it: **a stop below the entry was
      reached, by continuity**, the same argument D-013 section 1 used for the entry itself.
      The bar's *high*, though, may have printed before the fill on the way down, so a
      target hit on the entry bar is genuinely unobservable from OHLC and is **not**
      credited unless M1 resolves it. That asymmetry is the honest reading rather than a
      cautious one: one direction is proved by continuity and the other is not.
    """
    bullish = direction is Direction.BULLISH
    risk = abs(entry_price - planned_stop)
    stop = planned_stop
    stop_moved = False
    mae_r = mfe_r = 0.0
    bars_to_mae = bars_to_mfe = 0
    best_r = 0.0
    extreme = entry_price
    last = series.n - 1
    horizon = min(entry_bar + cfg.exit.max_bars_in_trade, last)

    for i in range(entry_bar, horizon + 1):
        entry_bar_now = i == entry_bar
        a = float(atr[i]) if np.isfinite(atr[i]) else 0.0
        o, h, l = float(series.open[i]), float(series.high[i]), float(series.low[i])
        at = from_epoch_s(series.close_time[i])
        slip = stop_slippage(cfg, spec=spec, atr_value=a) if apply_slippage else 0.0

        # -- excursions, measured on the raw bar ---------------------------
        # On a limit's entry bar the favourable extreme may predate the fill, so it is
        # clamped to the entry: an MFE the trade could not have had is not an MFE.
        far = h if bullish else l
        if entry_bar_now and not entry_at_bar_open:
            # Clamp TOWARD the entry, not away from it: for a bullish limit the bar's
            # high may predate the fill, so the most favourable price the trade can be
            # credited with on this bar is the entry itself.
            far = min(far, entry_price) if bullish else max(far, entry_price)
        near = l if bullish else h
        r_far, r_near = _r(direction, entry_price, far, risk), _r(direction, entry_price, near, risk)
        if r_far > mfe_r:
            mfe_r, bars_to_mfe = r_far, i - entry_bar
        if r_near < mae_r:
            mae_r, bars_to_mae = r_near, i - entry_bar
        best_r = max(best_r, r_far)
        extreme = max(extreme, h) if bullish else min(extreme, l)

        reason_for_stop = (
            ExitReason.BREAK_EVEN
            if stop_moved and _r(direction, entry_price, stop, risk) >= 0
            else ExitReason.TRAILING_STOP if stop_moved else ExitReason.STOP_LOSS
        )

        # A gap is measured against the PREVIOUS bar's close, which the entry bar has no
        # meaningful relationship to -- the trade did not exist before it.
        target_visible = target is not None and (
            not entry_bar_now or entry_at_bar_open or m1 is not None
        )

        # -- 1. gaps -------------------------------------------------------
        gapped_stop = (not entry_bar_now) and (o <= stop if bullish else o >= stop)
        gapped_tp = (
            (not entry_bar_now)
            and target is not None
            and (o >= target if bullish else o <= target)
        )
        if gapped_stop:
            price = o - slip if bullish else o + slip
            return ExitOutcome(
                reason_for_stop, i, at, price, _r(direction, entry_price, price, risk),
                i - entry_bar, mae_r, mfe_r, bars_to_mae, bars_to_mfe, stop,
                stop_moved, False, True,
            )
        if gapped_tp:
            # A gap in our favour fills at the open, which is BETTER than the target.
            # Recording it at the target would be a systematic pessimism, and the
            # symmetric treatment is what makes the gap model honest in both directions.
            return ExitOutcome(
                ExitReason.TAKE_PROFIT, i, at, o, _r(direction, entry_price, o, risk),
                i - entry_bar, mae_r, mfe_r, bars_to_mae, bars_to_mfe, stop,
                stop_moved, False, True,
            )

        # -- 2. both inside the bar ---------------------------------------
        hit_stop = l <= stop if bullish else h >= stop
        hit_tp = target_visible and (h >= target if bullish else l <= target)
        if hit_stop and hit_tp:
            first = "stop"
            if cfg.backtest.intrabar_mode == "m1_path" and m1 is not None:
                got = _m1_first(series, m1, i, direction=direction, stop=stop, target=target)
                first = got if got != "neither" else "stop"
            if first == "stop":
                price = stop - slip if bullish else stop + slip
                return ExitOutcome(
                    reason_for_stop, i, at, price,
                    _r(direction, entry_price, price, risk), i - entry_bar,
                    mae_r, mfe_r, bars_to_mae, bars_to_mfe, stop, stop_moved, True, False,
                )
            return ExitOutcome(
                ExitReason.TAKE_PROFIT, i, at, target,
                _r(direction, entry_price, target, risk), i - entry_bar,
                mae_r, mfe_r, bars_to_mae, bars_to_mfe, stop, stop_moved, True, False,
            )
        if hit_stop:
            price = stop - slip if bullish else stop + slip
            return ExitOutcome(
                reason_for_stop, i, at, price, _r(direction, entry_price, price, risk),
                i - entry_bar, mae_r, mfe_r, bars_to_mae, bars_to_mfe, stop,
                stop_moved, False, False,
            )
        if hit_tp:
            return ExitOutcome(
                ExitReason.TAKE_PROFIT, i, at, target,
                _r(direction, entry_price, target, risk), i - entry_bar,
                mae_r, mfe_r, bars_to_mae, bars_to_mfe, stop, stop_moved, False, False,
            )

        # -- 3. calendar exit (SPEC 17.4) ---------------------------------
        if cfg.exit.close_before_weekend and _is_weekend_close(at, cfg):
            c = float(series.close[i])
            return ExitOutcome(
                ExitReason.WEEKEND_CLOSE, i, at, c, _r(direction, entry_price, c, risk),
                i - entry_bar, mae_r, mfe_r, bars_to_mae, bars_to_mfe, stop,
                stop_moved, False, False,
            )

        # -- 4. management, AFTER this bar has been resolved ---------------
        new_stop = manage_stop(
            cfg, direction=direction, entry_price=entry_price,
            planned_stop=planned_stop, current_stop=stop, best_r=best_r,
            extreme_price=extreme, atr_value=a,
        )
        if new_stop != stop:
            stop, stop_moved = new_stop, True

    # -- horizon ----------------------------------------------------------
    i = horizon
    at = from_epoch_s(series.close_time[i])
    c = float(series.close[i])
    if entry_bar + cfg.exit.max_bars_in_trade > last:
        # The data ended first.  Censored, not time-stopped: SPEC 17.4's TIME_STOP is a
        # decision the strategy made, and calling this one would put a decision in the
        # record that nobody took.
        return ExitOutcome(
            ExitReason.END_OF_DATA, i, at, c, _r(direction, entry_price, c, risk),
            i - entry_bar, mae_r, mfe_r, bars_to_mae, bars_to_mfe, stop, stop_moved,
        )
    return ExitOutcome(
        ExitReason.TIME_STOP, i, at, c, _r(direction, entry_price, c, risk),
        i - entry_bar, mae_r, mfe_r, bars_to_mae, bars_to_mfe, stop, stop_moved,
    )
