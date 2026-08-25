"""Ingest, storage and the dataset manifest.

Bars live in Parquet, partitioned ``symbol/timeframe/year``.  There is no database:
a single-machine research project scanning full history is exactly the workload
columnar files are best at, and a server is one more thing that can be in a different
state than the results claim (ARCHITECTURE.md section 1.4).

The manifest is the point of this module.  A result is identified by
``(config_hash, dataset_hash, code_commit)`` and a run missing one of the three is not
admissible (SPEC 25.5).  ``dataset_hash`` is computed here, and it deliberately
includes the **tzdata version**: the IANA database decides every historical DST
transition and therefore every session boundary in the backtest, so two runs on
different tzdata releases are not strictly comparable.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from bot.config.loader import tzdata_version
from bot.config.schema import AppConfig
from bot.core.bars import FLAG_FIELDS, BarSeries, build_series, from_epoch_s
from bot.data import quality
from bot.data.resample import DERIVED_TIMEFRAMES, resample

_PRICE_COLS = ("open", "high", "low", "close", "volume")


class IngestError(ValueError):
    pass


# ------------------------------------------------------------------------- CSV


def read_csv(
    path: Path,
    symbol: str,
    timeframe: str,
    *,
    column_map: Mapping[str, str] | None = None,
    timestamp_format: str | None = None,
    timestamp_is_utc: bool = True,
) -> BarSeries:
    """Read a CSV export into a :class:`BarSeries`.

    ``timestamp_is_utc`` is not a convenience flag -- it is a claim about the file that
    must be verified against the provider's documentation.  Most broker exports are in
    *server* time, not UTC, and reading one as UTC shifts every bar by the broker's
    offset, which moves every session and every H4 boundary in the system.  Passing
    ``False`` is refused rather than silently converted: the correct fix is to record
    the source timezone in the manifest and convert at export time.
    """
    import csv

    if not timestamp_is_utc:
        raise IngestError(
            "non-UTC source timestamps must be converted before ingest and the source "
            "timezone recorded in the manifest (SPEC 1.1)"
        )
    cmap = {
        "timestamp": "timestamp",
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "volume": "volume",
        **(column_map or {}),
    }
    ts: list[int] = []
    cols: dict[str, list[float]] = {c: [] for c in _PRICE_COLS}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            raw = row[cmap["timestamp"]]
            dt = (
                datetime.strptime(raw, timestamp_format)
                if timestamp_format
                else datetime.fromisoformat(raw)
            )
            if dt.tzinfo is None:
                from datetime import timezone

                dt = dt.replace(tzinfo=timezone.utc)
            ts.append(int(dt.timestamp()))
            for c in _PRICE_COLS:
                key = cmap[c]
                cols[c].append(float(row[key]) if key in row and row[key] != "" else 0.0)

    from bot.core.bars import TIMEFRAMES

    step = int(TIMEFRAMES[timeframe].total_seconds())
    t = np.asarray(ts, dtype=np.int64)
    order = np.argsort(t, kind="stable")
    t = t[order]
    arrs = {c: np.asarray(cols[c], dtype=np.float64)[order] for c in _PRICE_COLS}
    return build_series(
        symbol, timeframe, t, t + step, arrs["open"], arrs["high"], arrs["low"], arrs["close"], arrs["volume"]
    )


# --------------------------------------------------------------------- Parquet


def _schema() -> pa.Schema:
    fields = [
        pa.field("open_time", pa.int64()),
        pa.field("close_time", pa.int64()),
        pa.field("open", pa.float64()),
        pa.field("high", pa.float64()),
        pa.field("low", pa.float64()),
        pa.field("close", pa.float64()),
        pa.field("volume", pa.float64()),
        pa.field("bar_count", pa.int32()),
        pa.field("expected_bars", pa.int32()),
    ]
    fields += [pa.field(name, pa.bool_()) for name in FLAG_FIELDS]
    return pa.schema(fields)


def _to_table(series: BarSeries) -> pa.Table:
    data = {
        "open_time": series.open_time,
        "close_time": series.close_time,
        "open": series.open,
        "high": series.high,
        "low": series.low,
        "close": series.close,
        "volume": series.volume,
        "bar_count": series.bar_count,
        "expected_bars": series.expected_bars,
    }
    for name in FLAG_FIELDS:
        data[name] = series.flag(name)
    return pa.Table.from_pydict(data, schema=_schema())


def _partition_dir(root: Path, symbol: str, timeframe: str, year: int) -> Path:
    return root / f"symbol={symbol}" / f"timeframe={timeframe}" / f"year={year}"


def write_series(series: BarSeries, root: Path) -> list[Path]:
    """Write one series, partitioned by year of ``open_time``.  Overwrites in place."""
    if series.n == 0:
        return []
    years = np.array([from_epoch_s(x).year for x in series.open_time])
    written: list[Path] = []
    for y in np.unique(years):
        sel = years == y
        part = _partition_dir(root, series.symbol, series.timeframe, int(y))
        part.mkdir(parents=True, exist_ok=True)
        path = part / "bars.parquet"
        sub = _to_table(series).filter(pa.array(sel))
        pq.write_table(sub, path, compression="zstd")
        written.append(path)
    return written


def read_series(
    root: Path, symbol: str, timeframe: str, *, years: Iterable[int] | None = None
) -> BarSeries:
    base = root / f"symbol={symbol}" / f"timeframe={timeframe}"
    if not base.exists():
        raise IngestError(f"no data for {symbol} {timeframe} under {root}")
    paths = sorted(base.glob("year=*/bars.parquet"))
    if years is not None:
        keep = {int(y) for y in years}
        paths = [p for p in paths if int(p.parent.name.split("=")[1]) in keep]
    if not paths:
        raise IngestError(f"no partitions matched for {symbol} {timeframe}")
    table = pa.concat_tables([pq.read_table(p) for p in paths])
    d = table.to_pydict()
    return BarSeries(
        symbol=symbol,
        timeframe=timeframe,
        open_time=np.asarray(d["open_time"], dtype=np.int64),
        close_time=np.asarray(d["close_time"], dtype=np.int64),
        open=np.asarray(d["open"], dtype=np.float64),
        high=np.asarray(d["high"], dtype=np.float64),
        low=np.asarray(d["low"], dtype=np.float64),
        close=np.asarray(d["close"], dtype=np.float64),
        volume=np.asarray(d["volume"], dtype=np.float64),
        bar_count=np.asarray(d["bar_count"], dtype=np.int32),
        expected_bars=np.asarray(d["expected_bars"], dtype=np.int32),
        flags={name: np.asarray(d[name], dtype=bool) for name in FLAG_FIELDS},
    )


# -------------------------------------------------------------------- manifest


@dataclass
class SeriesEntry:
    symbol: str
    timeframe: str
    n_bars: int
    first_bar_utc: str | None
    last_bar_utc: str | None
    content_hash: str
    quality: dict[str, Any]


@dataclass
class DatasetManifest:
    created_utc: str
    config_hash: str
    tzdata_version: str
    ingest_timeframe: str
    session_source_tf: str
    day_boundary: str
    price_side: str
    source: str
    series: list[SeriesEntry] = field(default_factory=list)
    dataset_hash: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @staticmethod
    def load(path: Path) -> "DatasetManifest":
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["series"] = [SeriesEntry(**s) for s in raw["series"]]
        return DatasetManifest(**raw)


def content_hash(series: BarSeries) -> str:
    """Hash of the bar content itself, independent of file layout or compression."""
    h = hashlib.sha256()
    h.update(f"{series.symbol}|{series.timeframe}|{series.n}".encode())
    for arr in (series.open_time, series.close_time):
        h.update(np.ascontiguousarray(arr, dtype=np.int64).tobytes())
    for arr in (series.open, series.high, series.low, series.close, series.volume):
        h.update(np.ascontiguousarray(arr, dtype=np.float64).tobytes())
    return h.hexdigest()


def build_dataset(
    sources: Mapping[str, BarSeries],
    cfg: AppConfig,
    cfg_hash: str,
    root: Path,
    *,
    source_label: str = "synthetic",
    price_side: str = "bid",
    timeframes: tuple[str, ...] = DERIVED_TIMEFRAMES,
) -> DatasetManifest:
    """Validate, resample, flag and persist every symbol; return the manifest.

    Order matters: quality analysis runs on the **ingest** series first so that
    ``data_suspect`` is established before resampling, and the flag then propagates
    upward -- a higher-timeframe bar built partly from suspect data is itself suspect.
    """
    root.mkdir(parents=True, exist_ok=True)
    entries: list[SeriesEntry] = []

    for symbol, raw in sources.items():
        if raw.timeframe != cfg.data.ingest_timeframe:
            raise IngestError(
                f"{symbol}: source is {raw.timeframe}, config expects {cfg.data.ingest_timeframe}"
            )
        flagged, rep = quality.analyse(raw, cfg, with_sessions=False)
        if not rep.is_clean:
            raise IngestError(
                f"{symbol}: source data is structurally invalid "
                f"(duplicates={rep.duplicate_timestamps}, non_monotonic={rep.non_monotonic}, "
                f"invalid_ohlc={rep.invalid_ohlc}, non_positive={rep.non_positive_prices})"
            )
        write_series(flagged, root)
        entries.append(
            SeriesEntry(
                symbol=symbol,
                timeframe=flagged.timeframe,
                n_bars=flagged.n,
                first_bar_utc=rep.first_bar_utc.isoformat() if rep.first_bar_utc else None,
                last_bar_utc=rep.last_bar_utc.isoformat() if rep.last_bar_utc else None,
                content_hash=content_hash(flagged),
                quality=rep.to_dict(),
            )
        )

        for tf in timeframes:
            derived = resample(flagged, tf, cfg)
            derived = _propagate_suspect(flagged, derived)
            write_series(derived, root)
            with_sessions = tf == cfg.session.source_tf
            _, drep = quality.analyse(derived, cfg, with_sessions=with_sessions)
            entries.append(
                SeriesEntry(
                    symbol=symbol,
                    timeframe=tf,
                    n_bars=derived.n,
                    first_bar_utc=drep.first_bar_utc.isoformat() if drep.first_bar_utc else None,
                    last_bar_utc=drep.last_bar_utc.isoformat() if drep.last_bar_utc else None,
                    content_hash=content_hash(derived),
                    quality=drep.to_dict(),
                )
            )

    boundary = f"{cfg.tf.day_boundary_tz} {cfg.tf.day_boundary_time.isoformat(timespec='minutes')}"
    manifest = DatasetManifest(
        created_utc=datetime.now().astimezone().isoformat(),
        config_hash=cfg_hash,
        tzdata_version=tzdata_version(),
        ingest_timeframe=cfg.data.ingest_timeframe,
        session_source_tf=cfg.session.source_tf,
        day_boundary=boundary,
        price_side=price_side,
        source=source_label,
        series=sorted(entries, key=lambda e: (e.symbol, e.timeframe)),
    )
    h = hashlib.sha256()
    for e in manifest.series:
        h.update(f"{e.symbol}|{e.timeframe}|{e.content_hash}".encode())
    h.update(manifest.tzdata_version.encode())
    h.update(boundary.encode())
    manifest.dataset_hash = h.hexdigest()
    (root / "manifest.json").write_text(manifest.to_json(), encoding="utf-8")
    return manifest


def _propagate_suspect(source: BarSeries, derived: BarSeries) -> BarSeries:
    """Carry ``data_suspect`` up from the ingest series to a derived bar.

    A derived bar is suspect if **any** constituent source bar was.  Setups whose
    formation window intersects a suspect region are excluded from headline statistics
    (SPEC 1.5), and that exclusion only works if the flag survives resampling.
    """
    if derived.n == 0 or source.n == 0:
        return derived
    src_flag = source.flag("data_suspect")
    if not src_flag.any():
        return derived
    lo = np.searchsorted(source.open_time, derived.open_time, side="left")
    hi = np.searchsorted(source.open_time, derived.close_time, side="left")
    cum = np.concatenate(([0], np.cumsum(src_flag.astype(np.int64))))
    hits = cum[hi] - cum[lo]
    flags = dict(derived.flags)
    flags["data_suspect"] = derived.flag("data_suspect") | (hits > 0)
    from dataclasses import replace

    return replace(derived, flags=flags)
