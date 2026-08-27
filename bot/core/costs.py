"""Transaction costs and slippage (SPEC 26).

Four costs, and the interesting thing about them is that three are asymmetric in a
direction a careless implementation gets wrong.

**Slippage is always adverse, and stops slip more than limits.** SPEC 26 gives entries
0.2 pips + 0.02 ATR and stops 0.5 pips + 0.05 ATR, and says why the two differ:
*"stops fill worse than limits; modelling them symmetrically is a systematic optimism."*
A stop is a market order fired into the move that triggered it; a limit is filled by
someone else's market order arriving at your price.

**A limit's fill price is not slipped at all.** A limit fills at its price or better, never
worse — that is what a limit is. What it pays instead is the spread: a buy fills at the
ask. Slipping a limit adversely would be modelling an impossibility, and doing it "to be
conservative" is the same error D-013 section 1 records for the entry/stop ordering.

**The spread is charged once, at entry, as the difference between bid and ask.** A long
enters at the ask and exits at the bid, so one full spread is paid over the round trip.
Charging it at both ends would double-count it.

**`cost.multiplier` is not a fudge factor.** BACKTEST_PROTOCOL 3.3 makes {1.0, 1.5, 2.0}
mandatory for every headline result: *"a strategy whose expectancy is destroyed at 1.5x is
not deployable: broker spreads vary by more than that, and so do the same broker's spreads
across the day and across years."*

**Swap is zero and says so.** SPEC 26 takes it from the broker's table, which needs Q1.
``cost.swap_pips_per_day`` is empty by default, which produces exactly zero rather than an
invented financing cost, and every report that uses this module states it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from bot.config.schema import AppConfig, SymbolSpec
from bot.core.displacement import Direction


def _family(table: dict[str, float], symbol: str, default: float = 0.0) -> float:
    s = symbol.upper()
    if s in table:
        return table[s]
    if s.endswith("JPY") and "JPY" in table:
        return table["JPY"]
    return table.get("default", default)


def spread_at(
    cfg: AppConfig, *, symbol: str, spec: SymbolSpec, session: str | None
) -> float:
    """SPEC 26's session-scaled constant, in price units.

    The `measured` model needs a tick spread series (Q2). Until then the fallback runs and
    the 3.3 sensitivity sweep is what bounds the error it introduces -- which is the
    honest treatment of a cost nobody has measured, rather than a number nobody has
    justified.

    London and New York are the active sessions; everything else pays the quiet spread.
    """
    active = session in ("LONDON", "NEW_YORK")
    table = cfg.cost.spread_pips_active if active else cfg.cost.spread_pips_quiet
    return _family(table, symbol) * spec.pip_size * cfg.cost.multiplier


def entry_slippage(cfg: AppConfig, *, spec: SymbolSpec, atr_value: float) -> float:
    """SPEC 26, market entries only.  Always adverse; a limit is never slipped."""
    return (
        cfg.slip.entry_pips * spec.pip_size + cfg.slip.entry_atr_mult * atr_value
    ) * cfg.cost.multiplier


def stop_slippage(cfg: AppConfig, *, spec: SymbolSpec, atr_value: float) -> float:
    """SPEC 26, stop exits only.  Larger than the entry figure, deliberately."""
    return (
        cfg.slip.stop_pips * spec.pip_size + cfg.slip.stop_atr_mult * atr_value
    ) * cfg.cost.multiplier


def commission(cfg: AppConfig, *, lots: float) -> float:
    """SPEC 26, per lot per side, so a round turn is twice this."""
    return 2.0 * cfg.cost.commission_per_lot_per_side * lots * cfg.cost.multiplier


def swap(
    cfg: AppConfig, *, symbol: str, lots: float, nights: int, direction: Direction
) -> float:
    """SPEC 26.  Zero until a broker table exists (Q1), and zero is the honest value.

    The sign convention is that a positive table entry is a cost. Triple swap on
    `swap_3day_weekday` is not modelled because there is no table to triple.
    """
    rate = _family(cfg.cost.swap_pips_per_day, symbol)
    if rate == 0.0:
        return 0.0
    return rate * lots * nights * cfg.cost.multiplier


@dataclass(frozen=True)
class TradeCosts:
    """What one round trip actually cost, in account currency and in R."""

    spread: float
    commission: float
    swap: float
    slippage: float

    @property
    def total(self) -> float:
        return self.spread + self.commission + self.swap + self.slippage


def fill_price_with_costs(
    price: float,
    *,
    direction: Direction,
    spread: float,
    slippage: float = 0.0,
    is_entry: bool,
) -> float:
    """Move a price to what it actually transacts at.

    A long buys at the ask (price + spread) and sells at the bid (price). A short is the
    mirror. Slippage is then applied **adversely in both cases**, which for an exit means
    a stop fills lower for a long and higher for a short.
    """
    bullish = direction is Direction.BULLISH
    if is_entry:
        base = price + spread if bullish else price - spread
        return base + slippage if bullish else base - slippage
    return price - slippage if bullish else price + slippage


def nights_held(opened_at: datetime, closed_at: datetime) -> int:
    """Rollovers crossed, for the swap charge.  Calendar days, not 24-hour periods."""
    return max(0, (closed_at.date() - opened_at.date()).days)
