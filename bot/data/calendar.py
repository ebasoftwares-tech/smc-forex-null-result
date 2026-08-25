"""Trading calendar: day boundaries, trading weeks, session windows, DST.

This module owns every conversion between wall-clock local time and UTC.  Nothing
else in the codebase may call ``astimezone`` or touch ``zoneinfo`` (SPEC 3.1).

The rule that shapes the whole module: **a boundary is expressed as (IANA timezone,
local time) and resolved per calendar day through the tz database.**  A fixed UTC
offset is wrong for half the year and wrong in a way that still produces
plausible-looking results.

Under DECISION D-001 the day boundary is ``("UTC", 00:00)``, which makes the day
resolution trivial -- but the general path is used anyway, so the NY-anchor ablation
exercises the same code rather than a second implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from bot.config.schema import SessionWindowConfig, WeekConfig

UTC = timezone.utc
_DAY = timedelta(days=1)


class BoundaryResolutionError(ValueError):
    """A local wall-clock time that does not exist, or is ambiguous, on some date."""


@dataclass(frozen=True)
class DayBoundary:
    """The instant a trading day starts, as a local time in a named zone.

    ``tz_name="UTC"`` / ``at=00:00`` is DECISION D-001.
    """

    tz_name: str
    at: time

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.tz_name)

    def boundary_utc(self, d: date) -> datetime:
        """UTC instant at which trading date ``d`` begins.

        Round-trips the result to catch a local time that does not exist on ``d``
        (spring-forward) or is ambiguous (fall-back).  Python would silently shift
        such a time; here it is an error, because a boundary that quietly moves is a
        boundary that produces two different backtests from the same data.
        """
        tz = self.tz
        local = datetime.combine(d, self.at, tzinfo=tz)
        as_utc = local.astimezone(UTC)
        back = as_utc.astimezone(tz)
        if back.date() != d or back.time() != self.at:
            raise BoundaryResolutionError(
                f"local time {self.at} does not exist on {d} in {self.tz_name} "
                f"(resolved to {back.isoformat()})"
            )
        # Ambiguity check: fold=1 naming a different instant means the wall clock
        # occurs twice on this date.
        if local.replace(fold=1).astimezone(UTC) != as_utc:
            raise BoundaryResolutionError(
                f"local time {self.at} is ambiguous on {d} in {self.tz_name}"
            )
        return as_utc

    def trading_date(self, ts: datetime) -> date:
        """The trading date containing UTC instant ``ts``."""
        local = _require_utc(ts).astimezone(self.tz)
        d = local.date()
        return d if local.time() >= self.at else d - _DAY

    def boundaries_utc(self, start: date, end: date) -> list[datetime]:
        """Boundary instants for every date in ``[start, end]`` inclusive, ascending."""
        out: list[datetime] = []
        d = start
        while d <= end:
            out.append(self.boundary_utc(d))
            d += _DAY
        return out

    def day_length_hours(self, d: date) -> float:
        """Length of trading date ``d`` in hours.  23 or 25 on a DST transition day."""
        return (self.boundary_utc(d + _DAY) - self.boundary_utc(d)).total_seconds() / 3600.0


def _require_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        raise ValueError("naive datetime; all timestamps in this system are UTC-aware")
    return ts.astimezone(UTC)


# --------------------------------------------------------------------------- weeks


def week_start_utc(ts: datetime, week: WeekConfig) -> datetime:
    """The most recent trading-week open at or before ``ts``.

    The week opens Sunday 21:00 UTC by default, so Sunday's opening hours group with
    the week that follows them, not the calendar week that contains them.  Every
    Sunday-stub decision (SPEC 2.6.1) depends on this grouping.
    """
    ts = _require_utc(ts)
    d = ts.date()
    for back in range(8):
        cand_date = d - timedelta(days=back)
        if cand_date.weekday() != week.open_dow:
            continue
        cand = datetime.combine(cand_date, week.open_time, tzinfo=UTC)
        if cand <= ts:
            return cand
    raise AssertionError("unreachable: a week open exists within any 8-day window")


def week_close_utc(week_start: datetime, week: WeekConfig) -> datetime:
    """The configured close of the week that opened at ``week_start``."""
    days_ahead = (week.close_dow - week_start.weekday()) % 7
    if days_ahead == 0 and week.close_time <= week_start.timetz().replace(tzinfo=None):
        days_ahead = 7
    return datetime.combine(
        week_start.date() + timedelta(days=days_ahead), week.close_time, tzinfo=UTC
    )


# ------------------------------------------------------------------------ sessions


@dataclass(frozen=True)
class SessionWindow:
    """One concrete occurrence of a session, resolved to UTC."""

    name: str
    trading_date: date
    start_utc: datetime
    end_utc: datetime
    tz_name: str

    @property
    def duration_hours(self) -> float:
        return (self.end_utc - self.start_utc).total_seconds() / 3600.0


def session_windows(
    spec: SessionWindowConfig,
    boundary: DayBoundary,
    start: date,
    end: date,
) -> list[SessionWindow]:
    """Every occurrence of ``spec`` whose start-date falls in ``[start, end]``.

    ``spec.days`` names the local weekdays the session *starts* on.  A session whose
    local end is at or before its local start runs past midnight and finishes on the
    following local day -- ``ASIA_RANGE`` (20:00 to 00:00 New York) is the case this
    exists for.

    Attribution follows SPEC 3.2: a session belongs to the trading day containing its
    **final instant**, not its first.  So the Asian range that closes at 05:00 UTC on
    Tuesday is Tuesday's Asian range, available to Tuesday's London session.
    """
    tz = ZoneInfo(spec.tz)
    out: list[SessionWindow] = []
    d = start
    while d <= end:
        if d.weekday() in spec.dows:
            local_start = datetime.combine(d, spec.start, tzinfo=tz)
            end_date = d + _DAY if spec.spans_midnight else d
            local_end = datetime.combine(end_date, spec.end, tzinfo=tz)
            s_utc = local_start.astimezone(UTC)
            e_utc = local_end.astimezone(UTC)
            if e_utc <= s_utc:  # pragma: no cover - guarded by spans_midnight
                raise ValueError(f"session {spec.name} on {d}: end is not after start")
            out.append(
                SessionWindow(
                    name=spec.name,
                    trading_date=boundary.trading_date(e_utc - timedelta(microseconds=1)),
                    start_utc=s_utc,
                    end_utc=e_utc,
                    tz_name=spec.tz,
                )
            )
        d += _DAY
    return out


def overlap_windows(
    a: list[SessionWindow], b: list[SessionWindow], name: str = "OVERLAP"
) -> list[SessionWindow]:
    """Intersection of two session series, matched on trading date.

    The London/New York overlap is 5h for most of the year and 4h during the DST
    desynchronisation weeks (SPEC 3.3).  It is computed, never assumed.
    """
    by_date = {w.trading_date: w for w in b}
    out: list[SessionWindow] = []
    for wa in a:
        wb = by_date.get(wa.trading_date)
        if wb is None:
            continue
        s = max(wa.start_utc, wb.start_utc)
        e = min(wa.end_utc, wb.end_utc)
        if e > s:
            out.append(
                SessionWindow(
                    name=name,
                    trading_date=wa.trading_date,
                    start_utc=s,
                    end_utc=e,
                    tz_name="UTC",
                )
            )
    return out


# ------------------------------------------------------------------------------ DST

_LONDON = "Europe/London"
_NEW_YORK = "America/New_York"
_NORMAL_LONDON_NY_OFFSET_HOURS = 5.0


def london_ny_offset_hours(d: date) -> float:
    """Hours between London local midnight and New York local midnight on ``d``.

    5.0 for most of the year.  4.0 during the two windows a year when the EU and US
    daylight-saving dates disagree: roughly three weeks in March (US changes on the
    second Sunday, the EU on the last) and one week in late October (the EU changes
    first).
    """
    ldn = datetime.combine(d, time(0, 0), tzinfo=ZoneInfo(_LONDON)).astimezone(UTC)
    nyc = datetime.combine(d, time(0, 0), tzinfo=ZoneInfo(_NEW_YORK)).astimezone(UTC)
    return (nyc - ldn).total_seconds() / 3600.0


def is_dst_desync(d: date) -> bool:
    """True on a date where London and New York are not their usual 5 hours apart.

    Tagged on every trade.  Not to trade those weeks differently -- a strategy whose
    results depend materially on four weeks a year has found a calendar artefact, not
    an edge, and this flag is how that becomes visible (SPEC 3.3).
    """
    return london_ny_offset_hours(d) != _NORMAL_LONDON_NY_OFFSET_HOURS
