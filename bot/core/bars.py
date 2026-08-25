"""Bar series and the causality boundary.

SPEC 1.1 / 1.2.  A ``BarSeries`` is columnar (numpy arrays) and immutable.  The only
sanctioned way for an engine to read it is :meth:`BarSeries.as_of`, which returns the
prefix that had *closed* by a given instant.

Why bars carry an explicit ``close_time`` instead of ``open_time + duration``:
higher-timeframe buckets in this system are not all the same length.  A New York
anchored day is 23 or 25 hours across a DST transition, the week's first D1 bar
absorbs the Sunday stub (SPEC 2.6.1), and the H4 bars at the week edges are partial.
Deriving the close from a nominal duration would be wrong for every one of those, and
wrong in the direction of claiming a bar had closed before it actually had -- which is
lookahead.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Iterator, Mapping

import numpy as np

UTC = timezone.utc

TIMEFRAMES: Mapping[str, timedelta] = {
    "M1": timedelta(minutes=1),
    "M5": timedelta(minutes=5),
    "M15": timedelta(minutes=15),
    "H1": timedelta(hours=1),
    "H4": timedelta(hours=4),
    "D1": timedelta(days=1),
    "W1": timedelta(days=7),
    "MN1": timedelta(days=30),  # nominal only; MN1 close_time is always explicit
}

#: Boolean per-bar quality flags carried alongside OHLCV.
FLAG_FIELDS = (
    "partial_bar",
    "low_coverage",
    "spans_gap",
    "data_suspect",
    "merged_stub",
    "irregular_day",
)


def to_epoch_s(ts: datetime) -> int:
    if ts.tzinfo is None:
        raise ValueError("naive datetime; all timestamps in this system are UTC-aware")
    return int(ts.astimezone(UTC).timestamp())


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def from_epoch_s(v: int | np.integer) -> datetime:
    """Epoch seconds to an aware UTC datetime.

    Built by offsetting from the epoch rather than via ``datetime.fromtimestamp``,
    which raises OSError on Windows for any negative value.  Negative epochs are
    reachable in ordinary use -- ``_week_opens`` walks one week back from the first
    bar -- so this is a portability fix, not a defensive flourish.
    """
    return _EPOCH + timedelta(seconds=int(v))


@dataclass(frozen=True)
class BarSeries:
    """Immutable OHLCV series on one timeframe for one symbol.

    Timestamps are int64 **epoch seconds, UTC**.  Integer seconds are used rather than
    ``datetime64`` so that bucket arithmetic, hashing and Parquet round-tripping are
    all exact and free of timezone reinterpretation.
    """

    symbol: str
    timeframe: str
    open_time: np.ndarray  # int64 epoch seconds, ascending, unique
    close_time: np.ndarray  # int64 epoch seconds, > open_time elementwise
    open: np.ndarray  # float64
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    bar_count: np.ndarray  # int32 constituent source bars (1 for the ingest timeframe)
    expected_bars: np.ndarray  # int32 constituent source bars a full bucket would hold
    flags: Mapping[str, np.ndarray]  # name -> bool array, see FLAG_FIELDS

    # ------------------------------------------------------------------ construction

    def __post_init__(self) -> None:
        n = len(self.open_time)
        for name in ("close_time", "open", "high", "low", "close", "volume", "bar_count", "expected_bars"):
            if len(getattr(self, name)) != n:
                raise ValueError(f"{name} length {len(getattr(self, name))} != open_time length {n}")
        for name, arr in self.flags.items():
            if name not in FLAG_FIELDS:
                raise ValueError(f"unknown flag {name!r}; expected from {FLAG_FIELDS}")
            if len(arr) != n:
                raise ValueError(f"flag {name} length {len(arr)} != {n}")
        if n == 0:
            return
        if not np.all(np.diff(self.open_time) > 0):
            raise ValueError(f"{self.symbol} {self.timeframe}: open_time is not strictly ascending")
        if not np.all(self.close_time > self.open_time):
            raise ValueError(f"{self.symbol} {self.timeframe}: close_time must be after open_time")
        # Buckets must not overlap.  Equality is allowed: one bar's close is the next
        # bar's open on a contiguous grid.
        if not np.all(self.close_time[:-1] <= self.open_time[1:]):
            raise ValueError(f"{self.symbol} {self.timeframe}: overlapping bars")

    @property
    def n(self) -> int:
        return len(self.open_time)

    def __len__(self) -> int:
        return self.n

    @property
    def coverage(self) -> np.ndarray:
        with np.errstate(divide="ignore", invalid="ignore"):
            cov = np.where(self.expected_bars > 0, self.bar_count / self.expected_bars, 0.0)
        return cov.astype(np.float64)

    # -------------------------------------------------------------------- causality

    def as_of(self, ts: datetime | int) -> "BarSeries":
        """The bars that had **closed** at or before ``ts``.

        AXIOM C (SPEC 1.2).  This is the only accessor an engine may use.  The
        currently-forming bar is not visible, so a rule cannot read a price that had
        not printed yet, whatever the rule does with the arrays afterwards.
        """
        cutoff = ts if isinstance(ts, int) else to_epoch_s(ts)
        k = int(np.searchsorted(self.close_time, cutoff, side="right"))
        return self.head(k)

    def head(self, k: int) -> "BarSeries":
        k = max(0, min(int(k), self.n))
        if k == self.n:
            return self
        return replace(
            self,
            open_time=self.open_time[:k],
            close_time=self.close_time[:k],
            open=self.open[:k],
            high=self.high[:k],
            low=self.low[:k],
            close=self.close[:k],
            volume=self.volume[:k],
            bar_count=self.bar_count[:k],
            expected_bars=self.expected_bars[:k],
            flags={name: arr[:k] for name, arr in self.flags.items()},
        )

    def slice_between(self, start: datetime | int, end: datetime | int) -> "BarSeries":
        """Bars whose open_time lies in ``[start, end)``.  For reporting, not signals."""
        s = start if isinstance(start, int) else to_epoch_s(start)
        e = end if isinstance(end, int) else to_epoch_s(end)
        lo = int(np.searchsorted(self.open_time, s, side="left"))
        hi = int(np.searchsorted(self.open_time, e, side="left"))
        return replace(
            self,
            open_time=self.open_time[lo:hi],
            close_time=self.close_time[lo:hi],
            open=self.open[lo:hi],
            high=self.high[lo:hi],
            low=self.low[lo:hi],
            close=self.close[lo:hi],
            volume=self.volume[lo:hi],
            bar_count=self.bar_count[lo:hi],
            expected_bars=self.expected_bars[lo:hi],
            flags={name: arr[lo:hi] for name, arr in self.flags.items()},
        )

    # ------------------------------------------------------------------- convenience

    def open_dt(self, i: int) -> datetime:
        return from_epoch_s(self.open_time[i])

    def close_dt(self, i: int) -> datetime:
        return from_epoch_s(self.close_time[i])

    def flag(self, name: str) -> np.ndarray:
        if name not in FLAG_FIELDS:
            raise ValueError(f"unknown flag {name!r}")
        arr = self.flags.get(name)
        return arr if arr is not None else np.zeros(self.n, dtype=bool)

    def rows(self) -> Iterator[tuple]:
        for i in range(self.n):
            yield (
                self.open_dt(i),
                self.close_dt(i),
                self.open[i],
                self.high[i],
                self.low[i],
                self.close[i],
                self.volume[i],
            )

    def __repr__(self) -> str:  # pragma: no cover - diagnostics
        if self.n == 0:
            return f"<BarSeries {self.symbol} {self.timeframe} empty>"
        return (
            f"<BarSeries {self.symbol} {self.timeframe} n={self.n} "
            f"{self.open_dt(0).isoformat()} -> {self.close_dt(-1).isoformat()}>"
        )


def empty_flags(n: int) -> dict[str, np.ndarray]:
    return {name: np.zeros(n, dtype=bool) for name in FLAG_FIELDS}


def build_series(
    symbol: str,
    timeframe: str,
    open_time: np.ndarray,
    close_time: np.ndarray,
    o: np.ndarray,
    h: np.ndarray,
    l: np.ndarray,
    c: np.ndarray,
    v: np.ndarray,
    bar_count: np.ndarray | None = None,
    expected_bars: np.ndarray | None = None,
    flags: Mapping[str, np.ndarray] | None = None,
) -> BarSeries:
    n = len(open_time)
    return BarSeries(
        symbol=symbol,
        timeframe=timeframe,
        open_time=np.asarray(open_time, dtype=np.int64),
        close_time=np.asarray(close_time, dtype=np.int64),
        open=np.asarray(o, dtype=np.float64),
        high=np.asarray(h, dtype=np.float64),
        low=np.asarray(l, dtype=np.float64),
        close=np.asarray(c, dtype=np.float64),
        volume=np.asarray(v, dtype=np.float64),
        bar_count=np.asarray(
            bar_count if bar_count is not None else np.ones(n), dtype=np.int32
        ),
        expected_bars=np.asarray(
            expected_bars if expected_bars is not None else np.ones(n), dtype=np.int32
        ),
        flags={**empty_flags(n), **(dict(flags) if flags else {})},
    )
