"""Stop placement and the constraints that reject a setup (SPEC 16).

Phase 12 implemented model S1 alone, because ``cancel_if`` clause 1 needed *a* stop
before an order could be armed at all. This module completes SPEC 16: all four models,
the full 16.2 buffer, and the 16.3 caps.

**The caps reject; they never adjust.** SPEC 16.3 is emphatic and the reason is a bias
argument rather than a purity one: tightening a stop to fit a risk cap converts a
rejected setup into a low-quality trade with a structurally wrong stop, and it does so
*precisely on the widest, most volatile setups*. That is a systematic bias, not random
noise, and it is invisible in the results because the trades it creates look like
ordinary trades. So ``check_stop`` returns a rejection and nothing here can move a price.

**S4's stop is a function of the entry price, and the other three are not.** That single
asymmetry has three consequences the rest of the codebase has to respect, all recorded in
D-014 section 4:

1. ``arm`` must compute the entry price *before* the stop, not after.
2. Under S4 a limit order can never be ``PRICE_THROUGH_STOP``, because the stop is placed
   a fixed distance from the price by construction. That arm-time guard is not wrong, it
   is *vacuous* for one model in four, which is worth knowing before reading a rejection
   table that shows a zero.
3. Under S4 with a MARKET order the planned price is a placeholder for a price that is
   never obtainable (SPEC 15.3), so the stop must be re-derived from the fill. That is
   why ``planned_stop`` takes an ``entry_price`` and why the assembly re-runs the 16.3
   checks at fill -- which SPEC 16.5 requires independently ("Both checks are required").

**S4 also ignores the 16.2 buffer entirely.** SPEC 16.1's table defines it as
``entry_price - atr_multiple x ATR_ref`` with no buffer term, so ``sl.buffer_atr`` is
inert under S4. Implemented as written; reported so that a buffer ablation is not read as
covering all four models.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np

from bot.config.schema import AppConfig, SymbolSpec
from bot.core.bars import BarSeries
from bot.core.displacement import Direction
from bot.core.order_blocks import OrderBlock


class StopModel(str, Enum):
    """SPEC 16.1.  S5 is rejected by the specification and is not offered as a value."""

    S1_SWEEP_EXTREME = "sweep_extreme"
    S2_STRUCTURAL_SWING = "structural_swing"
    S3_ORDER_BLOCK = "order_block"
    S4_ATR = "atr"


class StopReject(str, Enum):
    """SPEC 16.3 and 16.5, as SPEC 19's catalogue names them."""

    SL_TOO_WIDE = "SL_TOO_WIDE"
    SL_TOO_TIGHT = "SL_TOO_TIGHT"
    SPREAD_EXCEEDS_STOP = "SPREAD_EXCEEDS_STOP"
    INVALID_GEOMETRY = "INVALID_GEOMETRY"
    NO_OB_AVAILABLE = "NO_OB_AVAILABLE"


@dataclass(frozen=True)
class StopCheck:
    """The outcome of SPEC 16.3, plus which term did the rejecting.

    ``binding`` names the term even when the setup is accepted, so a report can say which
    cap is doing the work rather than only which ones fired. That distinction is the whole
    content of D-014 section 5: two caps that both "pass" can still be one cap.
    """

    ok: bool
    reason: StopReject | None
    binding: str
    sl_distance: float
    sl_atr: float
    sl_pips: float

    def __bool__(self) -> bool:  # pragma: no cover - convenience only
        return self.ok


def symbol_spec(cfg: AppConfig, symbol: str) -> SymbolSpec:
    """The declared metadata for a symbol, or the standard FX default.

    A symbol with no entry does **not** raise: SPEC 1.4's table is broker-resolved and no
    broker exists yet (Q1), so a missing entry means "not yet measured", not "invalid".
    What must not happen is a *silent* default for something the arithmetic depends on,
    which is why ``value_per_price_unit_per_lot`` refuses a missing conversion rate rather
    than assuming 1.0 -- see ``bot.core.risk``.
    """
    spec = cfg.symbol_specs.get(symbol.upper())
    if spec is not None:
        return spec
    return SymbolSpec(
        digits=3 if symbol.upper().endswith("JPY") else 5,
        base_ccy=symbol[:3].upper(),
        quote_ccy=symbol[3:6].upper() if len(symbol) >= 6 else "USD",
    )


def _family_value(table: dict[str, float], symbol: str) -> float:
    """Per-symbol cap with a JPY-family fallback and a default fallback.

    Keyed this way so a symbol added later inherits the right family rather than silently
    inheriting a major's pip counts -- an 8-pip minimum stop on a JPY cross is a third of
    the intended one.
    """
    s = symbol.upper()
    if s in table:
        return table[s]
    if s.endswith("JPY") and "JPY" in table:
        return table["JPY"]
    return table["default"]


def stop_buffer(
    cfg: AppConfig,
    *,
    atr_value: float,
    spec: SymbolSpec,
    spread: float | None = None,
) -> float:
    """SPEC 16.2's three-term maximum.

    ``buffer = max(buffer_atr x ATR, buffer_spread_mult x spread, stops_level + 1 point)``

    **The spread term is not optional and it is currently inert.** SPEC 1.3: on a JPY
    cross at a news time two spreads can exceed 0.10 ATR, and a stop inside that band is
    hit by quote noise rather than by price. No spread series exists until Q2, so the term
    is present, wired, and contributes nothing -- ``spread=None`` omits it rather than
    passing a zero, so the difference between "no spread data" and "a spread of zero"
    stays visible at the call site.

    ``stops_level`` defaults to 0 points for the same reason: 0 is the only value that
    cannot invent a rejection out of a number nobody has measured.
    """
    terms = [cfg.sl.buffer_atr * atr_value, spec.stops_level + spec.point]
    if spread is not None:
        terms.append(cfg.sl.buffer_spread_mult * spread)
    return max(terms)


def planned_stop(
    cfg: AppConfig,
    *,
    direction: Direction,
    atr_value: float,
    sweep_extreme: float,
    model: StopModel | str | None = None,
    series: BarSeries | None = None,
    setup_start_bar: int | None = None,
    break_bar: int | None = None,
    order_block: OrderBlock | None = None,
    entry_price: float | None = None,
    spec: SymbolSpec | None = None,
    spread: float | None = None,
) -> float:
    """SPEC 16.1's four models, for a BUY; mirrored for a SELL.

    Raises ``ValueError`` when a model's own input is missing rather than falling back to
    another model. SPEC 15.7's rule for entries -- invalidate and say why, never fall back
    -- applies here for the same reason: a stop model that silently degrades to S1 makes
    the S1 population a mixture and the ablation uninterpretable. The one exception is
    S3, whose missing input is an ordinary business outcome (no order block was proposed)
    rather than a programming error, and which therefore has its own ``NO_OB_AVAILABLE``
    rejection in ``arm``.
    """
    m = StopModel(model or cfg.sl.model)
    spec = spec or SymbolSpec()
    bullish = direction is Direction.BULLISH

    if m is StopModel.S4_ATR:
        # No buffer term: SPEC 16.1 defines S4 purely from the entry price, so
        # sl.buffer_atr is inert here.  See the module docstring.
        if entry_price is None:
            raise ValueError("S4 needs the entry price (SPEC 16.1)")
        d = cfg.sl.atr_multiple * atr_value
        return entry_price - d if bullish else entry_price + d

    buffer = stop_buffer(cfg, atr_value=atr_value, spec=spec, spread=spread)

    if m is StopModel.S1_SWEEP_EXTREME:
        anchor = sweep_extreme
    elif m is StopModel.S2_STRUCTURAL_SWING:
        if series is None or setup_start_bar is None or break_bar is None:
            raise ValueError("S2 needs the setup window [s..b] (SPEC 16.1)")
        lo, hi = min(setup_start_bar, break_bar), max(setup_start_bar, break_bar)
        window = slice(lo, hi + 1)
        anchor = (
            float(series.low[window].min()) if bullish else float(series.high[window].max())
        )
    elif m is StopModel.S3_ORDER_BLOCK:
        if order_block is None:
            raise ValueError("S3 needs an order block (SPEC 16.1)")
        anchor = order_block.distal
    else:  # pragma: no cover - the enum is exhaustive
        raise ValueError(f"unknown stop model {m}")

    return anchor - buffer if bullish else anchor + buffer


def check_stop(
    cfg: AppConfig,
    *,
    symbol: str,
    direction: Direction,
    entry_price: float,
    stop: float,
    atr_value: float,
    spec: SymbolSpec | None = None,
    spread: float | None = None,
) -> StopCheck:
    """SPEC 16.3's caps, in the order the specification lists them.

    The order matters only for which reason is reported, and the specification's order is
    kept so the rejection log is comparable with it line by line.

    ``spread`` is checked here as well as at arm time because SPEC 16.5 requires both: a
    stop that cleared the caps when the order was armed can sit inside the spread by the
    time it fills, and only the second check sees that.
    """
    spec = spec or symbol_spec(cfg, symbol)
    pip = spec.pip_size

    # SPEC 16.5: impossible by construction, asserted rather than silently corrected.
    wrong_side = (
        stop >= entry_price if direction is Direction.BULLISH else stop <= entry_price
    )
    if wrong_side:
        return StopCheck(False, StopReject.INVALID_GEOMETRY, "geometry", 0.0, 0.0, 0.0)

    d = abs(entry_price - stop)
    d_atr = d / atr_value if atr_value > 0 else float("inf")
    d_pips = d / pip

    max_pips = _family_value(cfg.risk.max_sl_pips, symbol)
    min_pips = _family_value(cfg.risk.min_sl_pips, symbol)

    if d_atr > cfg.risk.max_sl_atr:
        return StopCheck(False, StopReject.SL_TOO_WIDE, "max_sl_atr", d, d_atr, d_pips)
    if d_pips > max_pips:
        return StopCheck(False, StopReject.SL_TOO_WIDE, "max_sl_pips", d, d_atr, d_pips)
    if d_pips < min_pips:
        return StopCheck(False, StopReject.SL_TOO_TIGHT, "min_sl_pips", d, d_atr, d_pips)
    if spec.stops_level > 0 and d < spec.stops_level:
        return StopCheck(False, StopReject.SL_TOO_TIGHT, "stops_level", d, d_atr, d_pips)
    if spread is not None and spread >= d:
        return StopCheck(False, StopReject.SPREAD_EXCEEDS_STOP, "spread", d, d_atr, d_pips)

    # Accepted.  ``binding`` names the tightest constraint -- the one that would fire
    # first if the stop widened or narrowed -- so a report can distinguish a cap that is
    # doing work from one that is decoration.
    headroom = {
        "max_sl_atr": cfg.risk.max_sl_atr - d_atr,
        "max_sl_pips": (max_pips - d_pips) / max(max_pips, 1e-12) * cfg.risk.max_sl_atr,
    }
    binding = min(headroom, key=lambda k: headroom[k])
    return StopCheck(True, None, binding, d, d_atr, d_pips)


def dominant_upper_cap(cfg: AppConfig, symbol: str, atr_value: float) -> str:
    """Which of the two upper caps binds first at this ATR (SPEC 16.3).

    ``max_sl_atr`` and ``max_sl_pips`` are both FROZEN and only one of them can ever be
    the one that rejects: the ATR cap is 2.5 x ATR and the pip cap is a constant, so the
    crossover sits at ``max_sl_pips / max_sl_atr`` pips of ATR. Below that ATR the pip cap
    is looser and the ATR cap binds; above it the pip cap binds and the ATR cap is
    decoration. Reported rather than assumed, because on H4 majors the ATR spends most of
    its life on one side of that line.
    """
    spec = symbol_spec(cfg, symbol)
    max_pips = _family_value(cfg.risk.max_sl_pips, symbol)
    atr_pips = atr_value / spec.pip_size if spec.pip_size > 0 else float("nan")
    if not np.isfinite(atr_pips) or atr_pips <= 0:
        return "undefined"
    crossover = max_pips / cfg.risk.max_sl_atr
    return "max_sl_atr" if atr_pips < crossover else "max_sl_pips"
