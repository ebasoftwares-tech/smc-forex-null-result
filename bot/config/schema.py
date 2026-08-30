"""Configuration schema.

Every parameter in ``PARAMETERS.md`` is declared here with its default and its
class (FROZEN / ABLATION / TUNABLE) recorded in the field description.  Nothing in
the codebase may use a magic number that is not declared in this file
(SPEC section 0.3).

``extra="forbid"`` everywhere is deliberate: a mistyped parameter name that is
silently ignored means the run tested the default while the report claims
otherwise (ARCHITECTURE section 6.1).

Phase 1 declares the ``data``, ``tf``, ``week``, ``session``, ``atr`` and ``symbols``
groups.  Later phases append groups; they never edit an existing default without a
``DECISIONS.md`` entry.
"""

from __future__ import annotations

from datetime import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_WEEKDAYS = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}


class Frozen(BaseModel):
    """Base for every config model: immutable, no unknown keys, validated on assign."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)


def _parse_time(v: object) -> time:
    if isinstance(v, time):
        return v
    if isinstance(v, str):
        parts = v.split(":")
        if len(parts) == 2:
            return time(int(parts[0]), int(parts[1]))
        if len(parts) == 3:
            return time(int(parts[0]), int(parts[1]), int(parts[2]))
    raise ValueError(f"expected HH:MM time string, got {v!r}")


class DataConfig(Frozen):
    ingest_timeframe: Literal["M1", "M5"] = Field(
        "M1", description="FROZEN. SPEC 2.1 - higher timeframes are built, never fetched."
    )
    max_gap_bars: int = Field(
        3, ge=1, description="FROZEN. SPEC 1.5 - gaps beyond this mark DATA_SUSPECT."
    )
    spike_filter_atr: float = Field(
        10.0, gt=0, description="FROZEN. SPEC 1.5 - single-bar range above this is quarantined."
    )


class TimeframeConfig(Frozen):
    day_boundary_tz: str = Field(
        "UTC", description="ABLATION. DECISION D-001. Alternative: America/New_York."
    )
    day_boundary_time: time = Field(
        time(0, 0), description="ABLATION. DECISION D-001, with day_boundary_tz."
    )
    sunday_handling: Literal["merge_into_monday", "standalone_incomplete"] = Field(
        "merge_into_monday", description="ABLATION. DECISION D-001a, SPEC 2.6.1."
    )
    stub_merge_threshold: float = Field(
        0.25, ge=0.0, le=1.0, description="FROZEN. SPEC 2.6.1 coverage below which a week's first D1 bucket merges forward."
    )
    min_bar_coverage_warn: float = Field(
        0.50, ge=0.0, le=1.0, description="FROZEN. SPEC 2.6.2 - below this a bar is tagged low_coverage."
    )

    _v_time = field_validator("day_boundary_time", mode="before")(staticmethod(_parse_time))


class WeekConfig(Frozen):
    open_day: str = Field("Sun", description="FROZEN. SPEC 1.5 trading week open, UTC anchored.")
    open_time: time = Field(time(21, 0), description="FROZEN. SPEC 1.5.")
    close_day: str = Field("Fri", description="FROZEN. SPEC 1.5.")
    close_time: time = Field(time(21, 0), description="FROZEN. SPEC 1.5.")
    anchor_tolerance_hours: float = Field(
        1.0, gt=0, description="FROZEN. SPEC 1.5 - observed week edge may deviate by at most this."
    )

    _v_ot = field_validator("open_time", "close_time", mode="before")(staticmethod(_parse_time))

    @field_validator("open_day", "close_day")
    @classmethod
    def _known_day(cls, v: str) -> str:
        if v not in _WEEKDAYS:
            raise ValueError(f"unknown weekday {v!r}; expected one of {sorted(_WEEKDAYS)}")
        return v

    @property
    def open_dow(self) -> int:
        return _WEEKDAYS[self.open_day]

    @property
    def close_dow(self) -> int:
        return _WEEKDAYS[self.close_day]


class SessionWindowConfig(Frozen):
    """One session definition, anchored to its financial centre's local time.

    SPEC 3.1: a session is NEVER stored as a fixed UTC offset.  ``tz`` plus a local
    start/end is what makes daylight saving correct for free.
    """

    name: str
    tz: str
    start: time
    end: time
    days: list[str] = Field(description="Local weekdays on which the session STARTS.")
    role: Literal["liquidity", "execution", "liquidity+execution", "killzone"] = "liquidity"
    enabled: bool = True

    _v_se = field_validator("start", "end", mode="before")(staticmethod(_parse_time))

    @field_validator("days")
    @classmethod
    def _known_days(cls, v: list[str]) -> list[str]:
        bad = [d for d in v if d not in _WEEKDAYS]
        if bad:
            raise ValueError(f"unknown weekday(s) {bad}; expected from {sorted(_WEEKDAYS)}")
        if not v:
            raise ValueError("a session must run on at least one weekday")
        return v

    @property
    def dows(self) -> tuple[int, ...]:
        return tuple(_WEEKDAYS[d] for d in self.days)

    @property
    def spans_midnight(self) -> bool:
        return self.end <= self.start


class SessionConfig(Frozen):
    source_tf: Literal["M15", "H1"] = Field(
        "M15", description="FROZEN. SPEC 3.6 - H4 bars cannot produce session levels. H1 is the degraded mode."
    )
    min_bar_coverage: float = Field(
        0.60, ge=0.0, le=1.0, description="FROZEN. SPEC 3.4 - below this a session is INCOMPLETE."
    )
    windows: list[SessionWindowConfig]

    @model_validator(mode="after")
    def _unique_names(self) -> "SessionConfig":
        names = [w.name for w in self.windows]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"duplicate session names: {sorted(dupes)}")
        return self

    def window(self, name: str) -> SessionWindowConfig:
        for w in self.windows:
            if w.name == name:
                return w
        raise KeyError(f"no session window named {name!r}")


class AtrConfig(Frozen):
    period: int = Field(14, ge=2, description="FROZEN. SPEC 1.6 Wilder ATR.")


class SwingConfig(Frozen):
    """SPEC 5.  Fractal half-width per timeframe, plus the two tie-break rules."""

    fractal_n: dict[str, int] = Field(
        default_factory=lambda: {"MN1": 1, "W1": 1, "D1": 2, "H4": 2, "H1": 3, "M15": 5},
        description="ABLATION (H4 only: 1-4). SPEC 5.3. Confirmation lag is N bars.",
    )
    tie_rule: Literal["leftmost", "rightmost"] = Field(
        "leftmost",
        description="FROZEN. SPEC 5.1 - which bar of an equal-price plateau is the swing.",
    )
    price_source: Literal["wick", "body"] = Field(
        "wick", description="ABLATION. SPEC 5.1 - structure read on wicks or on closes."
    )
    min_history: dict[str, int] = Field(
        default_factory=lambda: {"MN1": 36, "W1": 104, "D1": 250, "H4": 500, "H1": 1000, "M15": 2000},
        description="FROZEN. SPEC 5.3 - bars required before a timeframe's structure is usable.",
    )

    @field_validator("fractal_n", "min_history")
    @classmethod
    def _positive(cls, v: dict[str, int]) -> dict[str, int]:
        bad = {k: n for k, n in v.items() if n < 1}
        if bad:
            raise ValueError(f"fractal/history values must be >= 1: {bad}")
        return v

    def n_for(self, timeframe: str) -> int:
        if timeframe not in self.fractal_n:
            raise ValueError(f"no fractal_n configured for {timeframe!r}")
        return self.fractal_n[timeframe]


class StructureConfig(Frozen):
    """SPEC 6.  Break confirmation and the protected-swing rules."""

    break_confirmation: Literal["close", "wick"] = Field(
        "close",
        description=(
            "FROZEN. SPEC 6.3. 'wick' is available for ablation and is expected to be much "
            "worse: a wick break of a level is exactly what a liquidity sweep of it looks "
            "like, so accepting one makes the system trade the pattern it exists to fade."
        ),
    )
    min_break_penetration_atr: float = Field(
        0.0,
        ge=0.0,
        description=(
            "ABLATION {0, 0.05, 0.10, 0.15}. SPEC 6.3. Registered in PARAMETERS.md as "
            "`break.min_penetration_atr`; renamed here because `break` is a Python keyword "
            "and cannot be a config group."
        ),
    )
    on_wick_below_protected: Literal["keep", "reset"] = Field(
        "keep",
        description=(
            "FROZEN. SPEC 6.4. 'reset' would move the protected level down on every wick "
            "and destroy the CHoCH signal."
        ),
    )
    min_bars_between_flips: int = Field(
        2, ge=1, description="FROZEN. SPEC 6.8 whipsaw guard."
    )
    protected_on_bos: Literal["most_recent_low", "ratchet_only"] = Field(
        "most_recent_low",
        description=(
            "ABLATION. Resolves a contradiction inside SPEC 6 (see D-005). 6.4 says the "
            "protected level is reset to the most recent confirmed opposite swing when a "
            "BOS fires; 6.9 asserts it is monotonically non-decreasing within a bullish "
            "trend. After a liquidity grab prints a lower low, those disagree. "
            "'most_recent_low' follows 6.4 and standard SMC practice -- the origin of the "
            "leg that broke structure becomes the new invalidation point. 'ratchet_only' "
            "follows 6.9 and never lets the level move away from price. The choice changes "
            "how far price must travel to produce a CHoCH, and therefore the setup count."
        ),
    )


class EqualLevelsConfig(Frozen):
    """SPEC 8.5.1 -- equal highs / equal lows."""

    min_touches: int = Field(2, ge=2, description="ABLATION {2, 3}. SPEC 8.5.1.")
    tolerance_atr: float = Field(
        0.10, gt=0, description="ABLATION {0.05, 0.10, 0.20}. SPEC 8.5.1."
    )
    min_separation_bars: int = Field(
        3,
        ge=1,
        description=(
            "FROZEN. SPEC 8.5.1. Two extremes on adjacent bars are one extreme, not two "
            "touches; without this a single rounded top counts as an equal-highs cluster."
        ),
    )
    max_span_bars: int = Field(50, ge=2, description="FROZEN. SPEC 8.5.1.")
    cluster_price: Literal["extreme", "mean"] = Field(
        "extreme",
        description=(
            "FROZEN. SPEC 8.5.1. The sweep must clear every stop resting beyond the "
            "cluster, so the extreme is the level that matters; the mean would report a "
            "sweep while part of the cluster is still untouched."
        ),
    )


class RangeConfig(Frozen):
    """SPEC 8.5.2 -- consolidation ranges.  Source disabled by default."""

    window_bars: int = Field(20, ge=4, description="FROZEN. SPEC 8.5.2.")
    max_height_atr: float = Field(2.0, gt=0, description="FROZEN. SPEC 8.5.2.")
    max_breakout_bars: int = Field(3, ge=0, description="FROZEN. SPEC 8.5.2.")


class LiquidityConfig(Frozen):
    """SPEC 8 -- the liquidity engine."""

    enabled_sources: list[str] = Field(
        default_factory=lambda: [
            "PREV_DAY",
            "PREV_WEEK",
            "PREV_MONTH",
            "SESSION",
            "SWING",
            "EQUAL",
            "PROTECTED_SWING",
        ],
        description=(
            "ABLATION, one switch per source family (SPEC 8.3). RANGE is omitted by "
            "default: SPEC 8.5.2 calls it the least well-founded source in the "
            "enumeration and marks it ABLATION-ONLY."
        ),
    )
    swing_timeframes: list[str] = Field(
        default_factory=lambda: ["H4", "D1"],
        description="FROZEN. SPEC 8.6 -- SWING_* on D1 is tier 1, on H4 tier 2.",
    )
    merge_tolerance_atr: float = Field(0.10, gt=0, description="FROZEN. SPEC 8.8.")
    max_distance_atr: float = Field(
        5.0, gt=0, description="ABLATION. SPEC 8.8 in-play filter."
    )
    invalidate_closes: int = Field(
        2, ge=1, description="FROZEN. SPEC 8.7 -- the swept/accepted-through distinction."
    )
    invalidate_buffer_atr: float = Field(0.25, ge=0, description="FROZEN. SPEC 8.7.")
    max_age_d1_bars: dict[str, int] = Field(
        default_factory=lambda: {"1": 90, "2": 30, "3": 5},
        description=(
            "FROZEN. SPEC 8.7, per tier, in D1 bars. PREV_MONTH_* never ages out; it is "
            "replaced monthly."
        ),
    )
    max_active_levels: int = Field(40, ge=1, description="FROZEN. SPEC 8.9 prune cap.")
    rank_tier_weight: dict[str, float] = Field(
        default_factory=lambda: {"1": 3.0, "2": 2.0, "3": 1.0},
        description=(
            "FROZEN. SPEC 8.8. These weights order candidates, they do not decide "
            "trades, and tuning an ordering function is a very efficient way to overfit "
            "without appearing to."
        ),
    )
    rank_strength_weight: float = Field(0.5, description="FROZEN. SPEC 8.8.")
    rank_recency_weight: float = Field(1.0, description="FROZEN. SPEC 8.8.")
    rank_bias_weight: float = Field(
        1.0,
        description=(
            "FROZEN. SPEC 8.8. Multiplies `bias_alignment`, which is always 0 until the "
            "bias engine exists (Phase 2-4/7); the term is wired but inert."
        ),
    )
    tier_confirmation_tf: dict[str, str] = Field(
        default_factory=lambda: {"1": "H4", "2": "H4", "3": "H4"},
        description=(
            "ABLATION. DECISION D-002 set every tier to H4; `{'3': 'H1'}` is the "
            "primary ablation."
        ),
    )


class SweepConfig(Frozen):
    """SPEC 9 -- liquidity sweep detection.

    Note the interaction SPEC 9.2 warns about: ``min_wick_ratio``,
    ``min_close_position`` and ``max_confirmation_bars`` are near-substitutes.  With
    ``max_confirmation_bars = 1`` the wick ratio is nearly implied.  Testing them one at
    a time will produce three "significant" parameters that are one effect.
    """

    max_confirmation_bars: int = Field(
        3,
        ge=1,
        le=5,
        description=(
            "TUNABLE {1, 2, 3, 5}. SPEC 9.2. 1 = a pure single-bar rejection wick; "
            "larger allows a two-bar poke-and-reclaim."
        ),
    )
    min_penetration_atr: float = Field(
        0.05,
        ge=0.0,
        description=(
            "FROZEN. SPEC 9.2. Excludes a sub-pip nick, which is usually a spread "
            "artefact rather than a stop run."
        ),
    )
    max_penetration_atr: float = Field(
        1.00,
        gt=0.0,
        description=(
            "TUNABLE {0.5, 0.75, 1.0, 1.5, 2.0}. SPEC 9.2. Above this the move is a "
            "breakout, not a sweep. This is the parameter that separates the two "
            "regimes and the one most likely to matter."
        ),
    )
    reclaim_buffer_atr: float = Field(
        0.0, ge=0.0, description="ABLATION {0, 0.05, 0.10}. SPEC 9.2."
    )
    min_wick_ratio: float = Field(
        0.0,
        ge=0.0,
        le=1.0,
        description='ABLATION {0, 0.3, 0.5}. SPEC 9.2, classic "long wick rejection".',
    )
    min_close_position: float = Field(
        0.0, ge=0.0, le=1.0, description="ABLATION {0, 0.5, 0.66}. SPEC 9.2."
    )
    require_prior_level_age_bars: int = Field(
        3,
        ge=0,
        description=(
            "FROZEN. SPEC 9.2.1 -- applies to SWING-DERIVED sources only, measured in "
            "bars of the timeframe the swing was detected on. Applying it to session "
            "levels made the flagship setup unreachable (D-002a)."
        ),
    )
    same_bar_choch_allowed: bool = Field(
        False,
        description=(
            "FROZEN. SPEC 9.6. The WAIT step is structural, not advisory: a bar that "
            "sweeps may not also confirm the CHoCH. Consumed by Phase 9; declared here "
            "because the registry name is `sweep.same_bar_choch_allowed`."
        ),
    )


class FvgConfig(Frozen):
    """SPEC 12.  Detection lands in Phase 8 (displacement needs it); the lifecycle
    fields are declared here so Phase 10 adds code rather than changing ``config_hash``."""

    min_size_atr: float = Field(
        0.10, ge=0.0, description="ABLATION {0.05, 0.10, 0.20}. SPEC 12.1."
    )
    min_size_pips: float = Field(
        0.5, ge=0.0, description="FROZEN. SPEC 12.1 -- a spread guard."
    )
    exclude_weekend_gaps: bool = Field(
        True,
        description=(
            "FROZEN. SPEC 12.5. An unfillable price region is not an imbalance anyone "
            "will trade back into."
        ),
    )
    mitigation_mode: Literal["touch", "ce", "full"] = Field(
        "ce", description="ABLATION. SPEC 12.2. [Phase 10]"
    )
    invalidate_buffer_atr: float = Field(0.0, ge=0.0, description="FROZEN. SPEC 12.2. [Phase 10]")
    max_age_bars: int = Field(30, ge=1, description="FROZEN. SPEC 12.2. [Phase 10]")
    merge_overlapping: bool = Field(False, description="ABLATION. SPEC 12.5. [Phase 10]")
    selection: Literal["first", "largest", "nearest"] = Field(
        "first", description="ABLATION. SPEC 12.3. [Phase 10]"
    )


class DisplacementConfig(Frozen):
    """SPEC 10.  Evaluated over a LEG, not a single bar: a two-bar drive and a one-bar
    drive of the same magnitude are the same event."""

    mode: Literal["leg", "bar", "either"] = Field(
        "leg", description="ABLATION. SPEC 10.3 -- `bar` is the classic formulation."
    )
    min_leg_atr: float = Field(
        1.5,
        ge=0.0,
        description=(
            "TUNABLE {0 (off), 1.0, 1.25, 1.5, 2.0, 2.5}. SPEC 10.1. Requires a PLATEAU, "
            "not a peak (BACKTEST_PROTOCOL 5.5)."
        ),
    )
    min_body_ratio: float = Field(
        0.50, ge=0.0, le=1.0, description="ABLATION {0.4, 0.5, 0.6}. SPEC 10.1."
    )
    min_directional_bars: int = Field(1, ge=0, description="FROZEN. SPEC 10.1.")
    max_leg_bars: int = Field(3, ge=1, description="ABLATION {2, 3, 5}. SPEC 10.1.")
    require_fvg: bool = Field(
        True,
        description=(
            "ABLATION. SPEC 10.2 -- not an extra condition layered on top but the same "
            "condition expressed structurally, and it yields an object entry model C "
            "can use. Partially redundant with min_leg_atr, so they ablate jointly."
        ),
    )
    min_range_atr: float = Field(
        1.5, ge=0.0, description="FROZEN. SPEC 10.3, `bar` mode only."
    )


class ChochConfig(Frozen):
    """SPEC 11 -- CHoCH reference selection and the sweep-to-MSS window.

    ``reference_mode`` is the one field here that is not a parameter in the ordinary
    sense.  SPEC 11.1 is explicit that ``major`` and ``micro`` are **two different
    strategies**, both pre-registered: they break different levels, at different times,
    with different stop distances and opposite failure modes.  Reporting a sweep over
    the two as though it were tuning would be a multiple-testing violation dressed up
    as a parameter choice (BACKTEST_PROTOCOL 5.6).
    """

    reference_mode: Literal["major", "micro"] = Field(
        "major",
        description=(
            "ABLATION -- and specifically two separately pre-registered strategy "
            "variants, not a sweep. SPEC 11.1. 'major' breaks the last unbroken swing "
            "high before the sweep; 'micro' breaks the first pullback high after it."
        ),
    )
    max_reference_lookback: int = Field(
        30,
        ge=1,
        description=(
            "FROZEN. SPEC 11.1. Bars back from the sweep extreme within which a major "
            "reference must have formed."
        ),
    )
    max_reference_distance_atr: float = Field(
        3.0,
        gt=0.0,
        description=(
            "ABLATION {2.0, 3.0, 4.0}. SPEC 11.1. A reference so far from the sweep "
            "that the stop would be untradeable is rejected up front as "
            "REFERENCE_TOO_FAR rather than surviving to be rejected by the risk layer "
            "-- the distinction matters for the rejection log, which is what the "
            "counterfactual analysis reads."
        ),
    )
    max_bars_after_sweep: int = Field(
        12,
        ge=4,
        le=24,
        description=(
            "TUNABLE {4, 8, 12, 18, 24}. SPEC 11.4. 12 H4 bars is two trading days. "
            "Under D-002 this is the parameter that makes the model a multi-session "
            "swing model rather than the intraday one the source material describes."
        ),
    )
    min_bars_after_sweep: int = Field(
        1,
        ge=0,
        description=(
            "FROZEN. SPEC 11.4 / 9.6 -- the WAIT. Measured from the sweep EXTREME bar, "
            "while knowability is measured from the sweep CONFIRM bar; both bind. See "
            "D-009."
        ),
    )
    micro_fractal_n: int = Field(
        1,
        ge=1,
        description=(
            "ABLATION. SPEC 11.1, `micro` mode only. Registered in PARAMETERS.md as "
            "`choch.micro_fractal_n` under the swing group."
        ),
    )


class InvalidateConfig(Frozen):
    """SPEC 11.6 / 19 -- setup invalidation tolerances."""

    new_extreme_atr: float = Field(
        0.10,
        ge=0.0,
        description=(
            "FROZEN. SPEC 11.5 clause 5. A small tolerance for a one-tick undercut of "
            "the sweep extreme. Beyond it the sweep FAILED -- the level was accepted "
            "through -- and any later break in the setup direction is a bounce inside "
            "the prevailing trend, not an MSS."
        ),
    )


class ObConfig(Frozen):
    """SPEC 13 -- Order Blocks.

    ``definition`` is the field this whole section exists for.  SPEC 13.1 opens by
    stating that "the last opposing candle before a move that breaks structure" is
    under-specified in three separate places, and that different choices produce zones
    tens of pips apart -- which for a stop-based strategy is the difference between a win
    and a loss.  So the four candidates are **four pre-registered variants**, not a knob,
    and SPEC 13.8 requires the agreement matrix to be reported alongside performance
    precisely so that near-identical variants are not counted as independent tests.
    """

    definition: Literal[
        "last_opposing", "last_down_close_before_break", "extreme_origin", "breaker"
    ] = Field(
        "last_opposing",
        description=(
            "ABLATION -- four separately pre-registered variants (OB-A/B/C/D), not a "
            "sweep. SPEC 13.2. Report the agreement matrix with any comparison between "
            "them (SPEC 13.8): variants that pick the same bar are not two hypotheses."
        ),
    )
    zone_mode: Literal["full_range", "body", "wick_to_open"] = Field(
        "full_range",
        description="ABLATION {full_range, body, wick_to_open}. SPEC 13.3.",
    )
    max_lookback_bars: int = Field(
        10,
        ge=1,
        description=(
            "FROZEN. SPEC 13.2. Bounds the search before the displacement leg. Without "
            "it OB-A degenerates into 'the last red candle', which always exists."
        ),
    )
    max_distance_atr: float = Field(
        3.0,
        gt=0.0,
        description="FROZEN. SPEC 13.4 constraint 4 -- an untradeable stop is rejected up front.",
    )
    max_age_bars: int = Field(30, ge=1, description="FROZEN. SPEC 13.4 constraint 5.")
    invalidate_closes: int = Field(
        1,
        ge=1,
        description=(
            "FROZEN. SPEC 13.5. A bullish OB whose distal edge is closed through is "
            "invalid: the orders it represented have been run."
        ),
    )


class SetupConfig(Frozen):
    """SPEC 14.4 -- concurrency caps.

    When a new setup would exceed a cap the **lower-ranked** one is invalidated as
    SUPERSEDED and logged with both ids, so the discarded alternative's outcome is still
    available to the counterfactual study.
    """

    max_active_per_symbol: int = Field(2, ge=1, description="FROZEN. SPEC 14.4.")
    max_active_per_direction: int = Field(1, ge=1, description="FROZEN. SPEC 14.4.")
    max_armed_orders: int = Field(1, ge=1, description="FROZEN. SPEC 14.4.")


class EntryConfig(Frozen):
    """SPEC 15.  Five models, run as five pre-registered variants over one shared setup
    stream (SPEC 15.8) so the comparison is **paired** -- "model C beats model A" is then
    a statement about the same setups rather than about two different populations."""

    model: Literal["A", "B", "C", "D", "E"] = Field(
        "C",
        description=(
            "ABLATION -- five separately pre-registered variants. SPEC 15.2. Compare on "
            "expectancy per SETUP, never per trade: models B-E do not always fill, and a "
            "model that fills on the best-looking third of setups shows a better win rate "
            "and a worse total return (SPEC 15.5)."
        ),
    )
    retrace_pct: float = Field(
        0.50,
        gt=0.0,
        lt=1.0,
        description="ABLATION {0.382, 0.5, 0.618}. SPEC 15.2, model B only.",
    )
    fvg_entry_point: Literal["proximal", "ce", "distal"] = Field(
        "ce", description="ABLATION. SPEC 15.2, model C only."
    )
    ob_entry_point: Literal["proximal", "ce", "distal"] = Field(
        "proximal", description="ABLATION. SPEC 15.2, model D only."
    )
    pending_expiry_bars: int = Field(
        6,
        ge=1,
        description=(
            "TUNABLE {3, 6, 9, 12}. SPEC 15.1. Directly trades fill rate against entry "
            "quality, which is why it is one of only eight tunable parameters."
        ),
    )
    cancel_on_bias_flip: bool = Field(
        True, description="ABLATION. SPEC 15.1 cancel_if 3."
    )
    fallback_model: Literal["none", "A", "B", "C", "D", "E"] = Field(
        "none",
        description=(
            "FROZEN at 'none'. SPEC 15.7: a fallback chain silently mixes populations and "
            "makes per-model statistics uninterpretable."
        ),
    )


class SlConfig(Frozen):
    """SPEC 16.  **Only S1 is implemented in Phase 12**, because ``cancel_if`` clause 1
    (SPEC 15.1) needs a planned stop price before an order can be armed at all. The other
    models, the full SPEC 16.2 buffer and the 16.3 constraints belong to their own phase.

    The buffer here is the ATR term only. SPEC 16.2 takes the max of that, a spread
    multiple and the broker's stops level -- neither of which exists until Q1/Q2 deliver a
    broker and real spread data, and inventing them would make the stop a property of the
    invention.
    """

    model: Literal["sweep_extreme", "structural_swing", "order_block", "atr"] = Field(
        "sweep_extreme",
        description=(
            "ABLATION S1-S4. SPEC 16.1. S1 is the default because the sweep extreme is "
            "the price at which the setup's premise is falsified: below it, the 'sweep' "
            "was a breakout. [Phase 12 implements S1 only.]"
        ),
    )
    buffer_atr: float = Field(
        0.10, ge=0.0, description="ABLATION {0.05, 0.10, 0.20}. SPEC 16.2."
    )
    buffer_spread_mult: float = Field(
        2.0, ge=0.0, description="FROZEN. SPEC 16.2 -- inert until real spread data (Q1/Q2)."
    )
    atr_multiple: float = Field(
        1.5, gt=0.0, description="FROZEN. SPEC 16.1, S4 only. [Phase 13]"
    )


class ExecConfig(Frozen):
    """SPEC 26 -- execution realism."""

    latency_ms: int = Field(
        250,
        ge=0,
        description=(
            "FROZEN. SPEC 15.3. A market fill is the first price at or after "
            "close_time(b) + latency, never the close that triggered the signal."
        ),
    )


class BacktestConfig(Frozen):
    """SPEC 17.5 / 15.4 -- how a bar is resolved into fills."""

    intrabar_mode: Literal["m1_path", "pessimistic"] = Field(
        "m1_path",
        description=(
            "FROZEN. SPEC 17.5: m1_path is 'the only correct option' and 'pessimistic' is "
            "the fallback when M1 is absent. `ohlc_heuristic` is prohibited by the spec "
            "and is therefore not offered here -- an option that must never be selected "
            "should not be selectable."
        ),
    )
    limit_fill_buffer_pips: float = Field(
        0.2,
        ge=0.0,
        description=(
            "FROZEN. SPEC 15.4. A limit order price merely TOUCHES is not reliably filled "
            "-- the queue may never reach you. Assuming touch-fills is one of the largest "
            "silent optimisms in retail backtesting and it flatters models B-E "
            "specifically."
        ),
    )


class SymbolSpec(Frozen):
    """SPEC 1.4 instrument metadata.

    **These are declared defaults, not broker-measured values.** SPEC 1.4 says this table
    is "resolved once from the broker and cached in the dataset manifest", and no broker
    has been chosen (Q1). The values below are the standard FX retail configuration and
    are right in shape; ``stops_level_points`` in particular is a per-broker, per-symbol
    number that is 0 here only because 0 is the one value that cannot silently invent a
    rejection. Every report that uses them says so.
    """

    digits: int = Field(5, ge=0, le=8, description="FROZEN. SPEC 1.4.")
    contract_size: float = Field(
        100_000.0, gt=0, description="FROZEN. SPEC 1.4 -- the FX standard lot."
    )
    lot_step: float = Field(0.01, gt=0, description="FROZEN. SPEC 18.2 quantisation.")
    min_lot: float = Field(0.01, gt=0, description="FROZEN. SPEC 18.2.")
    max_lot: float = Field(100.0, gt=0, description="FROZEN. SPEC 18.2.")
    base_ccy: str = Field(
        "EUR", min_length=3, max_length=3, description="FROZEN. SPEC 18.2 conversion."
    )
    quote_ccy: str = Field(
        "USD", min_length=3, max_length=3, description="FROZEN. SPEC 18.2 conversion."
    )
    stops_level_points: int = Field(
        0,
        ge=0,
        description=(
            "FROZEN. SPEC 1.4 / 16.3 broker minimum stop distance, in points. 0 until a "
            "broker is chosen (Q1) -- the only value that cannot invent a rejection."
        ),
    )

    @property
    def point(self) -> float:
        return 10.0**-self.digits

    @property
    def pip_size(self) -> float:
        """SPEC 1.4: ``10 x point`` when digits is 3 or 5, else ``point``."""
        return self.point * 10.0 if self.digits in (3, 5) else self.point

    @property
    def stops_level(self) -> float:
        return self.stops_level_points * self.point

    @field_validator("base_ccy", "quote_ccy")
    @classmethod
    def _ccy_upper(cls, v: str) -> str:
        return v.upper()

    @model_validator(mode="after")
    def _lots_consistent(self) -> "SymbolSpec":
        if self.min_lot > self.max_lot:
            raise ValueError("min_lot exceeds max_lot")
        return self


def _fx_specs() -> dict[str, "SymbolSpec"]:
    """The v1.0 symbol universe (SPEC 1.4), all standard-configuration FX."""
    pairs = (
        "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD",
        "USDCHF", "NZDUSD", "EURJPY", "GBPJPY", "EURGBP",
    )
    return {
        p: SymbolSpec(digits=3 if p.endswith("JPY") else 5, base_ccy=p[:3], quote_ccy=p[3:])
        for p in pairs
    }


class AccountConfig(Frozen):
    """SPEC 18.2 -- the account the sizing arithmetic is denominated in."""

    currency: str = Field(
        "USD",
        min_length=3,
        max_length=3,
        description="FROZEN. DECISION D-003 (Q1): raw-spread ECN, USD or EUR.",
    )
    starting_equity: float = Field(
        10_000.0,
        gt=0,
        description=(
            "FROZEN for reporting. Equity scales the equity curve and not the edge -- but "
            "it decides which trades SPEC 18.2's lot-granularity rejections eliminate, "
            "which is why the Phase 13 report sweeps it rather than quoting one value."
        ),
    )

    @field_validator("currency")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()


class TpConfig(Frozen):
    """SPEC 17.1 / 17.2 -- target PLACEMENT and the minimum-RR gate.

    Phase 13 implements placement and the gate because ``RR_BELOW_MIN`` fires in
    CHOCH_CONFIRMED (SPEC 19 item 16), alongside the SPEC 16.3 stop caps and the SPEC
    18.2 sizing rejections -- the three co-located pre-trade rejections this phase's gate
    exists to exercise.

    **Management is deliberately not implemented here.** 17.3's break-even and trailing,
    17.4's time and calendar exits, and the *execution* of T3's ladder and T4's trail all
    need an open trade, and belong with the exit policy in Phase 14.
    """

    model: Literal[
        "fixed_r", "opposing_liquidity", "partial_ladder", "structure_trail"
    ] = Field(
        "fixed_r",
        description=(
            "ABLATION T1-T4. SPEC 17.1. [Phase 13 places T1/T2 and gates all four; T3/T4 "
            "execution needs an open trade.]"
        ),
    )
    r_multiple: float = Field(
        2.0, gt=0, description="TUNABLE {1.5, 2.0, 2.5, 3.0}. SPEC 17.1, T1 only."
    )
    min_rr: float = Field(1.5, ge=0, description="ABLATION {1.0, 1.5, 2.0}. SPEC 17.2.")
    below_min_rr_action: Literal["skip"] = Field(
        "skip",
        description=(
            "FROZEN. SPEC 17.2. `fixed_fallback` is named there as the alternative and "
            "rejected in the same paragraph -- it contaminates the T2 population with T1 "
            "trades -- so it is not offered as a value at all."
        ),
    )
    min_target_rank: float = Field(
        5.0,
        ge=0,
        description=(
            "FROZEN. SPEC 17.1, T2 only. **Selected by its outcome on synthetic data "
            "(D-019) -- read the provenance before citing this value.** Raised from 2.0 "
            "by explicit instruction, on the ground that T2 arms 48 of 165 setups at 5.0 "
            "against 5 at 2.0. That basis is what BACKTEST_PROTOCOL 10.2 prohibits, and "
            "the fixture is a random walk on which the 10 resulting trades are noise, so "
            "the number carries no evidence that 5.0 is right -- only that it is where "
            "this fixture's targets sit far enough away to clear the 1.5 RR gate. The "
            "old 2.0 was separately mis-scaled: rank spans [1.5, 6.0] with a median of "
            "4.86, so it filtered almost nothing. Re-derive on real bars."
        ),
    )
    target_buffer_atr: float = Field(
        0.15,
        ge=0,
        description=(
            "FROZEN. SPEC 17.1, T2 only -- the order sits in front of the level, not at it."
        ),
    )
    ladder_first_r: float = Field(
        1.0,
        gt=0,
        description=(
            "FROZEN. SPEC 17.1, T3 only: the first rung of the ladder, and therefore the "
            "`tp_1` the SPEC 17.2 gate measures. See DECISION D-014 section 1 -- at this "
            "value T3 cannot pass the default `min_rr`, and that is a specification "
            "contradiction rather than a tuning opportunity."
        ),
    )


class RiskConfig(Frozen):
    """SPEC 16.3's stop caps and SPEC 18 in full.

    Per-symbol caps are keyed by symbol with a ``JPY`` family fallback and a ``default``
    fallback, so a symbol added later inherits the right family rather than silently
    inheriting a major's pip counts.
    """

    # --- SPEC 16.3, the stop-distance caps ---
    max_sl_atr: float = Field(2.5, gt=0, description="FROZEN. SPEC 16.3.")
    max_sl_pips: dict[str, float] = Field(
        default_factory=lambda: {"default": 60.0, "JPY": 90.0},
        description="FROZEN. SPEC 16.3 -- 60 majors / 90 JPY crosses.",
    )
    min_sl_pips: dict[str, float] = Field(
        default_factory=lambda: {"default": 8.0, "JPY": 12.0},
        description="FROZEN. SPEC 16.3 -- 8 majors / 12 JPY crosses.",
    )

    # --- SPEC 18.3, risk per trade ---
    pct_per_trade: float = Field(
        0.35,
        ge=0.10,
        le=0.50,
        description=(
            "TUNABLE, bounded [0.10, 0.50] by the brief (SPEC 18.3). Percent, not a "
            "fraction. The bound is enforced here rather than merely documented: SPEC "
            "18.1's anti-martingale invariant is only as strong as the largest number the "
            "risk layer can be asked for."
        ),
    )
    counter_monthly_multiplier: float = Field(
        0.5,
        gt=0,
        le=1.0,
        description="FROZEN. SPEC 18.3 -- inert until the bias engine lands (Phases 2-4).",
    )
    max_total_open_risk_pct: float = Field(
        1.5,
        gt=0,
        description=(
            "FROZEN. SPEC 18.4. **Unreachable under every legal configuration** -- see "
            "DECISION D-014 section 2: max_open_positions x pct_per_trade reaches exactly "
            "this value at the very top of the tunable band and sits below it everywhere "
            "else, so max_open_positions always binds first."
        ),
    )

    # --- SPEC 18.4, the hard limits ---
    max_daily_loss_pct: float = Field(2.0, gt=0, description="FROZEN. SPEC 18.4, closed PnL.")
    max_weekly_loss_pct: float = Field(4.0, gt=0, description="FROZEN. SPEC 18.4, closed PnL.")
    max_monthly_loss_pct: float = Field(
        8.0, gt=0, description="FROZEN. SPEC 18.4 -- manual re-enable, unlike daily/weekly."
    )
    max_consecutive_losses: int = Field(5, ge=1, description="FROZEN. SPEC 18.4.")
    consecutive_loss_pause_hours: int = Field(24, ge=0, description="FROZEN. SPEC 18.4.")
    max_open_positions: int = Field(3, ge=1, description="FROZEN. SPEC 18.4.")
    max_positions_per_symbol: int = Field(1, ge=1, description="FROZEN. SPEC 18.4.")
    max_correlated_positions: int = Field(2, ge=1, description="FROZEN. SPEC 18.4 / 18.7.")
    correlation_threshold: float = Field(0.70, ge=0, le=1.0, description="FROZEN. SPEC 18.7.")
    correlation_window_days: int = Field(60, ge=2, description="FROZEN. SPEC 18.7.")
    max_spread_pips: dict[str, float] = Field(
        default_factory=lambda: {"default": 2.0, "JPY": 3.5},
        description="FROZEN. SPEC 18.4 -- inert until real spread data (Q1/Q2).",
    )
    max_spread_pct_of_sl: float = Field(
        10.0,
        gt=0,
        description=(
            "FROZEN. SPEC 18.4, percent. Binds instead of the absolute cap for every "
            "stop under 20 pips (majors) or 35 pips (JPY) -- the tightest 23% and 29% of "
            "each legal stop range. See DECISION D-014 section 5."
        ),
    )
    equity_dd_kill_pct: float = Field(
        10.0,
        gt=0,
        description=(
            "FROZEN. SPEC 18.4 / 18.6 -- measured on equity INCLUDING floating PnL, "
            "unlike the loss limits, which are closed-PnL only."
        ),
    )

    # --- SPEC 18.5, the drawdown ladder ---
    dd_ladder: list[tuple[float, float]] = Field(
        default_factory=lambda: [(5.0, 0.75), (8.0, 0.50)],
        description=(
            "FROZEN. SPEC 18.5, as (drawdown_pct_threshold, multiplier) pairs. Validated "
            "monotone non-increasing with no multiplier above 1.0 -- SPEC 18.1's "
            "anti-martingale invariant at portfolio level, enforced at load time so that "
            "no configuration can express its violation."
        ),
    )

    # --- SPEC 18.2, the sizing rejections ---
    min_realised_fraction: float = Field(
        0.5,
        gt=0,
        le=1.0,
        description=(
            "FROZEN. SPEC 18.2. **Provably unreachable at 0.5** on any lot grid -- "
            "flooring to a step can never lose more than half the intended risk -- so the "
            "check is dead at its own default, including on the worked example SPEC 18.2 "
            "uses to justify it. See DECISION D-014 section 3. Deliberately not changed "
            "here: that is a decision to be taken explicitly, not an implementation detail."
        ),
    )

    @field_validator("dd_ladder")
    @classmethod
    def _ladder_monotone(cls, v: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if not v:
            return v
        thresholds = [t for t, _ in v]
        mults = [m for _, m in v]
        if thresholds != sorted(thresholds) or len(set(thresholds)) != len(thresholds):
            raise ValueError("dd_ladder thresholds must be strictly increasing")
        if any(m > 1.0 for m in mults):
            raise ValueError("dd_ladder multiplier above 1.0 violates SPEC 18.1")
        if mults != sorted(mults, reverse=True):
            raise ValueError("dd_ladder multipliers must be non-increasing (SPEC 18.5)")
        if any(m <= 0.0 for m in mults):
            raise ValueError("dd_ladder multiplier must be positive")
        return v


class OpsConfig(Frozen):
    """SPEC 18.6 -- the non-market kill-switch triggers."""

    max_data_staleness_sec: int = Field(300, ge=1, description="FROZEN. SPEC 18.6.")
    max_broker_errors: int = Field(5, ge=1, description="FROZEN. SPEC 18.6, per hour.")
    kill_switch_file: str = Field(
        "KILL_SWITCH",
        description=(
            "FROZEN. SPEC 18.6 -- the manual file trigger exists because a kill switch "
            "that can only fire automatically cannot be used by the person watching the "
            "screen."
        ),
    )


class ManageConfig(Frozen):
    """SPEC 17.3 -- break-even and trailing, both ABLATION dimensions.

    **Break-even is off by default and the specification says why:** it *"reliably raises
    win rate and reliably lowers expectancy on most systems; enabling it by default would
    flatter the headline statistic that matters least."* The default is the honest one, not
    the flattering one.
    """

    be_trigger_r: float = Field(
        0.0,
        ge=0.0,
        description=(
            "ABLATION {off, 1.0, 1.5}. SPEC 17.3. 0 disables it. Off by default because it "
            "trades expectancy for win rate, and win rate is the statistic that matters "
            "least."
        ),
    )
    be_offset_atr: float = Field(
        0.05,
        ge=0.0,
        description=(
            "FROZEN. SPEC 17.3 -- the offset covers spread and commission, so break-even "
            "is actually break-even rather than a small loss."
        ),
    )
    trail_mode: Literal["none", "structure", "atr"] = Field(
        "none", description="ABLATION. SPEC 17.3."
    )
    trail_atr_mult: float = Field(2.0, gt=0, description="FROZEN. SPEC 17.3, atr mode only.")
    trail_start_r: float = Field(
        1.0, ge=0.0, description="FROZEN. SPEC 17.3 -- trailing does not begin before this."
    )


class ExitConfig(Frozen):
    """SPEC 17.4 -- time and calendar exits.

    The last field carries a rule about the whole system, not just about news: **any live
    behaviour the backtest cannot reproduce is prohibited.** Otherwise the live system and
    the tested system are different systems and the backtest stops describing what runs.
    """

    max_bars_in_trade: int = Field(
        30,
        ge=1,
        description="ABLATION {15, 30, 60, off}. SPEC 17.4 -- 30 H4 bars is about 5 days.",
    )
    close_before_weekend: bool = Field(
        True,
        description=(
            "ABLATION. SPEC 17.4 -- avoids the weekend gap and the triple swap. On by "
            "default, unlike break-even, because it removes a risk rather than shaping a "
            "statistic."
        ),
    )
    weekend_close_utc: time = Field(
        time(19, 0), description="FROZEN. SPEC 17.4, Friday."
    )
    weekend_close_day: str = Field("Fri", description="FROZEN. SPEC 17.4.")
    close_before_high_impact_news: bool = Field(
        False,
        description=(
            "FROZEN false. SPEC 17.4 -- needs a calendar feed (Q13), and a feature that "
            "cannot be reproduced in the backtest MUST NOT exist only in live."
        ),
    )

    _v_time = field_validator("weekend_close_utc", mode="before")(staticmethod(_parse_time))

    @field_validator("weekend_close_day")
    @classmethod
    def _dow(cls, v: str) -> str:
        if v not in _WEEKDAYS:
            raise ValueError(f"unknown weekday {v!r}")
        return v

    @property
    def weekend_close_dow(self) -> int:
        return _WEEKDAYS[self.weekend_close_day]


class SlipConfig(Frozen):
    """SPEC 26 -- slippage, always adverse and asymmetric between entries and stops.

    *"Stops fill worse than limits; modelling them symmetrically is a systematic
    optimism."* The two pairs of numbers are the specification's, and the asymmetry is the
    point of having two pairs.
    """

    entry_pips: float = Field(0.2, ge=0.0, description="FROZEN. SPEC 26.")
    entry_atr_mult: float = Field(0.02, ge=0.0, description="FROZEN. SPEC 26.")
    stop_pips: float = Field(0.5, ge=0.0, description="FROZEN. SPEC 26 -- larger than entry.")
    stop_atr_mult: float = Field(0.05, ge=0.0, description="FROZEN. SPEC 26 -- larger than entry.")


class CostConfig(Frozen):
    """SPEC 26 / BACKTEST_PROTOCOL 3.3.

    ``multiplier`` is the mandatory cost-sensitivity dimension: *"a strategy whose
    expectancy is destroyed at 1.5x is not deployable: broker spreads vary by more than
    that, and so do the same broker's spreads across the day and across years."*
    """

    commission_per_lot_per_side: float = Field(
        3.5, ge=0.0, description="FROZEN. SPEC 26 -- raw-spread account assumption."
    )
    spread_model: Literal["measured", "session_constant"] = Field(
        "session_constant",
        description=(
            "FROZEN. SPEC 26 prefers `measured`; there is no tick spread series until Q2, "
            "so the session-constant fallback is what actually runs and the 3.3 "
            "sensitivity run is what bounds the error."
        ),
    )
    spread_pips_active: dict[str, float] = Field(
        default_factory=lambda: {"default": 0.8, "JPY": 1.2},
        description="FROZEN. SPEC 26 -- London and New York.",
    )
    spread_pips_quiet: dict[str, float] = Field(
        default_factory=lambda: {"default": 1.6, "JPY": 2.4},
        description="FROZEN. SPEC 26 -- Asia and outside sessions, double the active figure.",
    )
    multiplier: float = Field(
        1.0,
        gt=0,
        description=(
            "ABLATION {1.0, 1.5, 2.0}. BACKTEST_PROTOCOL 3.3 -- the mandatory "
            "cost-sensitivity run. Every headline result is reported at all three."
        ),
    )
    swap_pips_per_day: dict[str, float] = Field(
        default_factory=dict,
        description=(
            "FROZEN, empty. SPEC 26 takes swap from the broker table (Q1). Empty means "
            "zero swap and the reports say so rather than inventing a financing cost."
        ),
    )


class AnalysisConfig(Frozen):
    """SPEC 19 / 21.3 -- the counterfactual horizon."""

    forward_bars: int = Field(
        12,
        ge=1,
        description=(
            "FROZEN. SPEC 19: every invalidation stores the forward return over this many "
            "bars, which is what turns the rejection log into a counterfactual dataset "
            "answerable without a second backtest run."
        ),
    )


class AppConfig(Frozen):
    """The fully resolved configuration.  Hashed to produce ``config_hash``."""

    symbols: list[str]
    data: DataConfig = DataConfig()
    tf: TimeframeConfig = TimeframeConfig()
    week: WeekConfig = WeekConfig()
    session: SessionConfig
    atr: AtrConfig = AtrConfig()
    swing: SwingConfig = SwingConfig()
    structure: StructureConfig = StructureConfig()
    liq: LiquidityConfig = LiquidityConfig()
    sweep: SweepConfig = SweepConfig()
    disp: DisplacementConfig = DisplacementConfig()
    choch: ChochConfig = ChochConfig()
    invalidate: InvalidateConfig = InvalidateConfig()
    ob: ObConfig = ObConfig()
    setup: SetupConfig = SetupConfig()
    entry: EntryConfig = EntryConfig()
    sl: SlConfig = SlConfig()
    tp: TpConfig = TpConfig()
    risk: RiskConfig = RiskConfig()
    account: AccountConfig = AccountConfig()
    ops: OpsConfig = OpsConfig()
    manage: ManageConfig = ManageConfig()
    exit: ExitConfig = ExitConfig()
    slip: SlipConfig = SlipConfig()
    cost: CostConfig = CostConfig()
    analysis: AnalysisConfig = AnalysisConfig()
    exec: ExecConfig = ExecConfig()
    backtest: BacktestConfig = BacktestConfig()
    fvg: FvgConfig = FvgConfig()
    eq: EqualLevelsConfig = EqualLevelsConfig()
    range: RangeConfig = RangeConfig()
    symbol_specs: dict[str, SymbolSpec] = Field(
        default_factory=_fx_specs,
        description="FROZEN. SPEC 1.4 -- declared defaults, not broker-measured (Q1).",
    )

    @field_validator("symbols")
    @classmethod
    def _upper_unique(cls, v: list[str]) -> list[str]:
        up = [s.upper() for s in v]
        if len(set(up)) != len(up):
            raise ValueError("duplicate symbols")
        if not up:
            raise ValueError("at least one symbol is required")
        return up
