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

    @field_validator("symbols")
    @classmethod
    def _upper_unique(cls, v: list[str]) -> list[str]:
        up = [s.upper() for s in v]
        if len(set(up)) != len(up):
            raise ValueError("duplicate symbols")
        if not up:
            raise ValueError("at least one symbol is required")
        return up
