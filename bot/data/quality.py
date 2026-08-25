"""Data quality: validation, gaps, DATA_SUSPECT regions, week-anchor verification.

SPEC 1.5 and 3.4.  The governing idea is that data defects are **detected from the
data**, never from a hard-coded calendar: broker holiday behaviour varies between
brokers and between years, and a wrong holiday calendar silently deletes real trades.

Nothing here repairs anything.  A defect is recorded, the affected region is flagged,
and setups formed there are excluded from headline statistics and reported separately.
Silent repair is how a dataset stops describing the market it came from.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np

from bot.config.schema import AppConfig
from bot.core.bars import TIMEFRAMES, BarSeries, from_epoch_s
from bot.core.indicators import atr_ref
from bot.data.calendar import UTC, DayBoundary, session_windows, week_start_utc


@dataclass
class Gap:
    start_utc: datetime
    end_utc: datetime
    missing_bars: int
    is_weekend: bool


@dataclass
class WeekAnchor:
    week_start_utc: datetime
    observed_open_utc: datetime
    observed_close_utc: datetime
    open_deviation_hours: float
    close_deviation_hours: float
    within_tolerance: bool


@dataclass
class SessionCoverage:
    session_name: str
    expected: int
    built: int
    closed: int
    incomplete: int
    forming: int
    absent: int  # window existed in the calendar but held no bars at all


@dataclass
class QualityReport:
    symbol: str
    timeframe: str
    n_bars: int
    first_bar_utc: datetime | None
    last_bar_utc: datetime | None
    duplicate_timestamps: int
    non_monotonic: int
    invalid_ohlc: int
    non_positive_prices: int
    gaps: list[Gap] = field(default_factory=list)
    suspect_gaps: list[Gap] = field(default_factory=list)
    spikes: list[datetime] = field(default_factory=list)
    week_anchors: list[WeekAnchor] = field(default_factory=list)
    week_anchor_violations: int = 0
    session_coverage: list[SessionCoverage] = field(default_factory=list)
    suspect_bar_count: int = 0

    @property
    def is_clean(self) -> bool:
        """Clean means *structurally sound*, not defect-free.

        Duplicates, non-monotonic timestamps and invalid OHLC are corruption and make
        the dataset unusable.  Gaps, spikes and thin sessions are facts about the
        market and the broker; they are flagged and carried, not treated as failures.
        """
        return (
            self.duplicate_timestamps == 0
            and self.non_monotonic == 0
            and self.invalid_ohlc == 0
            and self.non_positive_prices == 0
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return _jsonable(d)


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    return obj


# ------------------------------------------------------------------- structural


def _structural_checks(series: BarSeries) -> dict[str, int]:
    t = series.open_time
    dup = int(len(t) - len(np.unique(t))) if len(t) else 0
    non_mono = int(np.sum(np.diff(t) <= 0)) if len(t) > 1 else 0
    h, l, o, c = series.high, series.low, series.open, series.close
    invalid = int(
        np.sum((h < l) | (h < np.maximum(o, c) - 1e-12) | (l > np.minimum(o, c) + 1e-12))
    )
    nonpos = int(np.sum((o <= 0) | (h <= 0) | (l <= 0) | (c <= 0)))
    return {
        "duplicate_timestamps": dup,
        "non_monotonic": non_mono,
        "invalid_ohlc": invalid,
        "non_positive_prices": nonpos,
    }


# ------------------------------------------------------------------------- gaps


def find_gaps(series: BarSeries, cfg: AppConfig) -> tuple[list[Gap], list[Gap]]:
    """All gaps, and the subset that exceeds ``data.max_gap_bars`` and is not a weekend.

    The weekend is not a defect.  A gap is classed as a weekend gap when a trading-week
    open falls strictly inside it, which is data-driven and therefore correct for any
    broker's week schedule (SPEC 1.5).
    """
    if series.n < 2:
        return [], []
    step = int(TIMEFRAMES[series.timeframe].total_seconds())
    t = series.open_time
    d = np.diff(t)
    idx = np.flatnonzero(d > step)
    if len(idx) == 0:
        return [], []

    weeks = _week_opens(series, cfg)
    gaps: list[Gap] = []
    for i in idx:
        lo, hi = int(t[i]), int(t[i + 1])
        # The week open coincides exactly with the first bar after a weekend gap,
        # so the upper bound must be inclusive.  With `< hi` every weekend gap was
        # classified as a data defect.
        weekend = bool(np.any((weeks > lo) & (weeks <= hi)))
        gaps.append(
            Gap(
                start_utc=from_epoch_s(lo + step),
                end_utc=from_epoch_s(hi),
                missing_bars=int((hi - lo) // step) - 1,
                is_weekend=weekend,
            )
        )
    suspect = [g for g in gaps if not g.is_weekend and g.missing_bars > cfg.data.max_gap_bars]
    return gaps, suspect


def _week_opens(series: BarSeries, cfg: AppConfig) -> np.ndarray:
    first = from_epoch_s(series.open_time[0])
    last = from_epoch_s(series.open_time[-1])
    stamps: list[int] = []
    w = week_start_utc(first, cfg.week) - timedelta(days=7)
    while w <= last + timedelta(days=7):
        stamps.append(int(w.timestamp()))
        w += timedelta(days=7)
    return np.asarray(stamps, dtype=np.int64)


def mark_suspect(series: BarSeries, suspect: list[Gap], cfg: AppConfig) -> BarSeries:
    """Set ``data_suspect`` on bars within ``data.max_gap_bars`` of a suspect gap.

    The region either side of the gap is flagged, not only the gap itself: a swing or
    a sweep whose defining window straddles missing data is as unreliable as one
    inside it.
    """
    if series.n == 0 or not suspect:
        return series
    step = int(TIMEFRAMES[series.timeframe].total_seconds())
    pad = cfg.data.max_gap_bars * step
    flag = series.flag("data_suspect").copy()
    for g in suspect:
        lo = int(g.start_utc.timestamp()) - pad
        hi = int(g.end_utc.timestamp()) + pad
        flag |= (series.open_time >= lo) & (series.open_time <= hi)
    flags = dict(series.flags)
    flags["data_suspect"] = flag
    from dataclasses import replace

    return replace(series, flags=flags)


# ----------------------------------------------------------------------- spikes


def find_spikes(series: BarSeries, cfg: AppConfig) -> list[datetime]:
    """Bars whose range exceeds ``data.spike_filter_atr`` x ATR.

    Quarantined for review, never auto-corrected: on FX a 10-ATR bar is usually a bad
    tick, but it is sometimes a central bank, and only a human can tell the difference.
    """
    atr = atr_ref(series, cfg.atr.period)
    rng = series.high - series.low
    with np.errstate(invalid="ignore"):
        hit = np.flatnonzero(np.isfinite(atr) & (atr > 0) & (rng > cfg.data.spike_filter_atr * atr))
    return [from_epoch_s(series.open_time[i]) for i in hit]


# ----------------------------------------------------------- week-anchor check


def check_week_anchors(series: BarSeries, cfg: AppConfig) -> list[WeekAnchor]:
    """Compare each week's observed first/last bar against the configured anchors.

    SPEC 1.5 requires the week edges to be *measured*, not assumed: they are
    broker-specific, and a dataset whose week opens an hour early has a different
    Sunday stub, which changes every Monday D1 bar (SPEC 2.6.1).
    """
    if series.n == 0:
        return []
    weeks = _week_opens(series, cfg)
    t = series.open_time
    data_first, data_last = int(t[0]), int(series.close_time[-1])
    out: list[WeekAnchor] = []
    for i in range(len(weeks) - 1):
        lo, hi = weeks[i], weeks[i + 1]
        sel = (t >= lo) & (t < hi)
        if not np.any(sel):
            continue
        ws = from_epoch_s(int(lo))
        expected_close = ws + timedelta(days=(cfg.week.close_dow - ws.weekday()) % 7 or 7)
        expected_close = expected_close.replace(
            hour=cfg.week.close_time.hour, minute=cfg.week.close_time.minute
        )
        # A week clipped by the start or end of the dataset is not a broker anomaly.
        # Counting it as one would put a permanent violation in every report.
        if int(lo) < data_first or int(expected_close.timestamp()) > data_last:
            continue
        first = from_epoch_s(t[sel][0])
        # Compare against the CLOSE of the week's final bar: an M15 bar opening at
        # 20:45 closes exactly on a 21:00 anchor, and measuring its open reports a
        # spurious 15-minute deviation on every week.
        last = from_epoch_s(int(series.close_time[sel][-1]))
        open_dev = (first - ws).total_seconds() / 3600.0
        close_dev = (expected_close - last).total_seconds() / 3600.0
        out.append(
            WeekAnchor(
                week_start_utc=ws,
                observed_open_utc=first,
                observed_close_utc=last,
                open_deviation_hours=round(open_dev, 3),
                close_deviation_hours=round(close_dev, 3),
                within_tolerance=abs(open_dev) <= cfg.week.anchor_tolerance_hours
                and abs(close_dev) <= cfg.week.anchor_tolerance_hours,
            )
        )
    return out


# ------------------------------------------------------------ session coverage


def session_coverage(series: BarSeries, cfg: AppConfig) -> list[SessionCoverage]:
    """Expected vs built session occurrences, including the ones that hold no bars.

    A session that is entirely absent -- a full public holiday -- produces no
    ``SessionInstance`` at all, because an instance with no prices would be a lie.
    Counting the absence here is what keeps it visible: without this, a broker that
    silently drops a holiday looks identical to one that trades through it.
    """
    from bot.core.sessions import build_sessions, SessionStatus

    if series.n == 0:
        return []
    boundary = DayBoundary(cfg.tf.day_boundary_tz, cfg.tf.day_boundary_time)
    first = from_epoch_s(series.open_time[0]).date()
    last = from_epoch_s(series.open_time[-1]).date()

    built = build_sessions(series, cfg)
    by_name: dict[str, list] = {}
    for s in built:
        by_name.setdefault(s.session_name, []).append(s)

    out: list[SessionCoverage] = []
    for spec in cfg.session.windows:
        if not spec.enabled:
            continue
        expected = len(session_windows(spec, boundary, first, last))
        got = by_name.get(spec.name, [])
        out.append(
            SessionCoverage(
                session_name=spec.name,
                expected=expected,
                built=len(got),
                closed=sum(1 for s in got if s.status is SessionStatus.CLOSED),
                incomplete=sum(1 for s in got if s.status is SessionStatus.INCOMPLETE),
                forming=sum(1 for s in got if s.status is SessionStatus.FORMING),
                absent=max(0, expected - len(got)),
            )
        )
    return out


# --------------------------------------------------------------------- report


def analyse(series: BarSeries, cfg: AppConfig, *, with_sessions: bool = True) -> tuple[BarSeries, QualityReport]:
    """Full quality pass.  Returns the series with ``data_suspect`` set, and the report."""
    checks = _structural_checks(series)
    gaps, suspect = find_gaps(series, cfg)
    flagged = mark_suspect(series, suspect, cfg)
    anchors = check_week_anchors(series, cfg)
    cov = session_coverage(series, cfg) if with_sessions and series.timeframe == cfg.session.source_tf else []

    report = QualityReport(
        symbol=series.symbol,
        timeframe=series.timeframe,
        n_bars=series.n,
        first_bar_utc=from_epoch_s(series.open_time[0]) if series.n else None,
        last_bar_utc=from_epoch_s(series.close_time[-1]) if series.n else None,
        gaps=[g for g in gaps if not g.is_weekend],
        suspect_gaps=suspect,
        spikes=find_spikes(series, cfg),
        week_anchors=anchors,
        week_anchor_violations=sum(1 for a in anchors if not a.within_tolerance),
        session_coverage=cov,
        suspect_bar_count=int(flagged.flag("data_suspect").sum()),
        **checks,
    )
    return flagged, report
