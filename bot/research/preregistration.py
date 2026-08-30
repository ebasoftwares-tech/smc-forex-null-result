"""The pre-registration, as data (`BACKTEST_PROTOCOL.md` section 1).

`docs/PRE_REGISTRATION.md` is the document; this module is the half of it a test can
check. Everything here exists because a pre-registration is worthless if it can drift:
the numbers that discount our own best result must be **computed from the configuration
that actually runs**, not typed into prose and then diverge from it.

**The configuration count `M` is the reason this module exists.** It feeds the Deflated
Sharpe Ratio and section 5.6's expected-maximum-Sharpe-under-the-null, so every
significance claim in the project is scaled by it. `PARAMETERS.md` section 2 states it
three ways and none of them can be reproduced from the schema that runs:

| | `PARAMETERS.md` | The schema |
|---|---|---|
| TUNABLE parameters | 8, including `bias.min_score` | **7** -- there is no `bias` section at all (Phases 2-4 unbuilt) |
| `disp.min_leg_atr` grid | 5 values | **6** -- the schema includes `0 (off)` |
| Stated product | `5 x 4 x 5 x 5 x 4 x 4 x 5 = 8,000` | that product is **40,000**; 8,000 is the product *without* the trailing `x 5` for bias |
| Declared `M` | **6,912**, "after removing dominated combinations" | no rule for "dominated" is stated anywhere, so it cannot be recomputed |

So `M` is declared here instead, as the **full Cartesian product of the grids the schema
itself declares**, and pinned by a test that parses those grids back out of the field
descriptions. Three reasons for taking the full product rather than a reduced one:

1. **It is reproducible.** A number nobody can recompute is not a pre-registration.
2. **It is conservative.** `M` exists to discount the best in-sample result; a larger `M`
   discounts harder, and erring toward a harsher correction is the only direction that
   cannot flatter the outcome.
3. **"Dominated" is a judgement about results.** Deciding which configurations could not
   have won requires knowing something about how they perform, which is exactly the
   knowledge a pre-registration is written before having.

`risk.pct_per_trade` is TUNABLE and is **excluded from the grid, provably rather than by
convention**: SPEC 18.1 makes `position_size` a pure function of `(equity, risk_pct,
sl_distance)`, so risk percent scales PnL and **cannot move R** -- and R-expectancy is the
primary metric. Sweeping it would be sweeping a parameter that cannot change the number
being tested.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Mapping

#: The grids that may be optimised, in-sample only, under section 5.5's plateau
#: requirement and section 5.6's correction.  Parsed back out of the schema by
#: ``tests/test_preregistration.py``, so a divergence fails a test rather than sitting in
#: two documents disagreeing.
TUNABLE_GRID: Mapping[str, tuple] = {
    "sweep.max_confirmation_bars": (1, 2, 3, 5),
    "sweep.max_penetration_atr": (0.5, 0.75, 1.0, 1.5, 2.0),
    "disp.min_leg_atr": (0.0, 1.0, 1.25, 1.5, 2.0, 2.5),
    "choch.max_bars_after_sweep": (4, 8, 12, 18, 24),
    "entry.pending_expiry_bars": (3, 6, 9, 12),
    "tp.r_multiple": (1.5, 2.0, 2.5, 3.0),
}

#: TUNABLE parameters deliberately outside the grid, each with the reason. A TUNABLE the
#: schema declares and this module neither grids nor excludes is a test failure.
EXCLUDED: Mapping[str, str] = {
    "risk.pct_per_trade": (
        "Scales PnL, cannot move R. SPEC 18.1 makes position_size a pure function of "
        "(equity, risk_pct, sl_distance), and R-expectancy is the primary metric, so no "
        "value of this parameter can change the number under test. Reported at the "
        "default and varied only for the drawdown figures section 10.1 states in percent."
    ),
}

#: Not in the schema, and therefore not in `M` yet. Named so that landing the bias engine
#: is visibly a re-registration rather than a quiet widening of the grid.
DEFERRED: Mapping[str, str] = {
    "bias.min_score": (
        "PARAMETERS.md section 2 lists this as the eighth TUNABLE with a grid of "
        "{0, 1, 2, 3, 4}. No `bias` section exists in the schema: Phases 2-4 are unbuilt "
        "and `mss.py` takes the MTF gate as an always-pass predicate, which IS "
        "`gate_mode = none`. When the bias engine lands, M is multiplied by 5 and this "
        "pre-registration is superseded -- see the amendment rule."
    ),
}


def configuration_count(grid: Mapping[str, tuple] = TUNABLE_GRID) -> int:
    """`M` -- the full Cartesian product. No removals, by the reasoning above."""
    return math.prod(len(v) for v in grid.values())


#: The number carried into every Deflated Sharpe Ratio and every section 5.6 correction.
M = configuration_count()

#: What `M` becomes once the bias engine exists, stated in advance so the increase cannot
#: be presented later as a detail.
M_WITH_BIAS = M * 5


def parse_schema_grid(description: str) -> tuple[str, ...] | None:
    """The `TUNABLE {a, b, c}` grid out of a schema field description, or None.

    Deliberately literal: it reads what the running configuration says about itself,
    which is the only text that cannot drift from the code.
    """
    m = re.search(r"TUNABLE \{([^}]*)\}", description)
    if not m:
        return None
    return tuple(part.strip() for part in m.group(1).split(","))


# ------------------------------------------------------------------ the splits


@dataclass(frozen=True)
class SplitRule:
    """Section 2.1's splits, as a **rule** rather than as dates.

    The protocol's table is written *"assuming 2019-01 -> 2025-12 available"*, and no data
    has been acquired (Q1/Q2). Fixing literal dates now would either invent a range or
    leave the pre-registration's fourth item blank until data arrives -- and a
    pre-registration completed after the data is in hand is not one.

    A rule is as binding as a date and can be committed today: applied to whatever history
    is acquired it yields exactly one answer, and the answer does not depend on anything
    anyone has seen. The concrete dates are stamped mechanically at acquisition and
    committed as an amendment **before the first run**.
    """

    in_sample_years: float = 4.0
    out_of_sample_years: float = 2.0
    #: Everything after IS + OOS. Touched exactly once, at the end (section 7).
    holdout: str = "remainder"
    #: Section 2 requires these regimes in the acquired period, whatever its endpoints.
    required_regimes: tuple[str, ...] = (
        "2020 (volatility shock)",
        "2022 (trending)",
        "at least one extended range regime",
    )
    #: The IS block is the EARLIEST span, so OOS and holdout are strictly forward in time.
    ordering: str = "chronological: IS earliest, then OOS, then holdout"


SPLITS = SplitRule()

#: Section 2.1's cross-sectional split, already fixed by the protocol.
DEV_SYMBOLS: tuple[str, ...] = ("EURUSD", "GBPUSD", "USDJPY")
VALIDATION_SYMBOLS: tuple[str, ...] = (
    "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "EURJPY", "GBPJPY", "EURGBP",
)


# ------------------------------------------------------- acceptance and decision


@dataclass(frozen=True)
class Criterion:
    """One row of section 10.1's go/no-go table."""

    name: str
    threshold: str
    #: False when the project cannot currently evaluate it, with the reason in `note`.
    evaluable: bool = True
    note: str = ""


#: Section 10.1, verbatim in substance, with the two rows this project cannot yet score
#: marked rather than dropped (`STATE.md` section 10: never quietly skipped).
GO_NO_GO: tuple[Criterion, ...] = (
    Criterion("Trades (OOS)", ">= 200"),
    Criterion("Expectancy", ">= +0.10 R, 5th-percentile bootstrap bound > 0"),
    Criterion("Profit factor", ">= 1.20"),
    Criterion("Max drawdown", "<= 20% of equity at risk.pct_per_trade"),
    Criterion("Walk-forward efficiency", ">= 0.50"),
    Criterion("Profitable OOS windows", ">= 60%"),
    Criterion("Cross-sectional", ">= 6 of 10 symbols with positive expectancy, same parameters"),
    Criterion("Deflated Sharpe", f"> 0 at M = {M}"),
    Criterion(
        "Falsification suite",
        "Full model beats every 6.3/6.4 control in **both** gross and net R, "
        "each by a margin whose CI excludes zero",
        note="The 'both currencies' requirement is this pre-registration's decision on "
             "D-016 section 1 -- see the document.",
    ),
    Criterion("Plateau", "Every TUNABLE parameter sits in a plateau (section 5.5)"),
    Criterion("Paper trading", ">= 60 days, >= 95% entry-signal agreement with a backtest"),
)

#: The primary metric, named once so nothing else can quietly become it.
PRIMARY_METRIC = "expectancy in R per trade, net of costs, at cost.multiplier = 1.5"
PRIMARY_THRESHOLD_R = 0.10

#: Section 5.1's floor, and the reason INCONCLUSIVE is a distinct verdict from FAIL.
MIN_OOS_TRADES = 200
