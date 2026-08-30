"""Download HistData.com M1 bars into the CSV shape ``ingest.read_csv`` expects.

    python scripts/fetch_histdata.py --probe              # one symbol-year, format checks
    python scripts/fetch_histdata.py --symbols EURUSD     # one symbol, all years
    python scripts/fetch_histdata.py                      # every configured symbol

**Why this rather than Dukascopy.** Dukascopy serves M1 as one file per day, which is
~1,900 requests per symbol and ~19,000 for the universe; an eight-worker first attempt was
throttled inside two minutes and the block outlasted a seven-minute cooldown. HistData
serves a **whole year in one request**, so the same dataset is **70 requests** — 268x fewer.
That is the difference between politely asking a free service for a dataset and hammering
it.

**The timezone is the thing to get right, and the documentation is not sufficient.**
HistData's FAQ says the timestamps are *"Eastern Standard Time (EST) time-zone WITHOUT Day
Light Savings adjustments"*. ``read_csv`` refuses non-UTC input outright, so the conversion
has to be exact -- and a wrong constant shifts every bar by an hour for half the year,
moving every session boundary and every H4 bucket in the system.

So ``--probe`` measures the offset instead of trusting it, by finding the weekly open. The
FX week opens at 17:00 *New York local*, which is 22:00 UTC in winter and 21:00 UTC in
summer. Under a genuinely fixed UTC-5 stamp the weekly open would therefore appear at
17:00 in the file during winter and at **16:00** during summer. If it appears at 17:00 all
year, the stamps track New York DST and the correct conversion is
``ZoneInfo("America/New_York")`` rather than a constant.

Not in ``requirements.txt``: a build-time tool, like the asset generators.
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.config.loader import load_config  # noqa: E402

UTC = timezone.utc
NY = ZoneInfo("America/New_York")
PAGE = ("https://www.histdata.com/download-free-forex-historical-data/"
        "?/ascii/1-minute-bar-quotes/{pair}/{year}")
POST = "https://www.histdata.com/get.php"
_UA = {"User-Agent": "Mozilla/5.0 (research backtest dataset builder)"}

DEFAULT_START_YEAR = 2019
DEFAULT_END_YEAR = 2025


def fetch_year(symbol: str, year: int, cache: Path, delay: float = 1.0) -> bytes:
    """The year's zip, cached on disk. One page GET for the token, then one POST."""
    path = cache / f"{symbol.upper()}_{year}.zip"
    if path.exists():
        return path.read_bytes()

    page = PAGE.format(pair=symbol.lower(), year=year)
    html = urllib.request.urlopen(
        urllib.request.Request(page, headers=_UA), timeout=60
    ).read().decode("utf-8", "replace")
    m = re.search(r'name=["\']tk["\'][^>]*value=["\']([^"\']+)', html)
    if not m:
        raise RuntimeError(f"no download token on {page}")

    body = urllib.parse.urlencode({
        "tk": m.group(1), "date": str(year), "datemonth": str(year),
        "platform": "ASCII", "timeframe": "M1", "fxpair": symbol.upper(),
    }).encode()
    blob = urllib.request.urlopen(
        urllib.request.Request(POST, data=body, headers={**_UA, "Referer": page}),
        timeout=180,
    ).read()
    if not blob.startswith(b"PK"):
        raise RuntimeError(f"{symbol} {year}: response is not a zip ({len(blob)} bytes)")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)
    time.sleep(delay)
    return blob


def rows_from(blob: bytes) -> list[tuple[datetime, float, float, float, float, float]]:
    """Parse the zip's CSV into naive local-stamped rows, unconverted."""
    z = zipfile.ZipFile(io.BytesIO(blob))
    name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
    out = []
    for line in z.read(name).decode("ascii", "replace").splitlines():
        if not line:
            continue
        stamp, o, h, l, c, v = line.split(";")
        out.append((
            datetime.strptime(stamp, "%Y%m%d %H%M%S"),
            float(o), float(h), float(l), float(c), float(v),
        ))
    return out


def measure_offset(rows) -> tuple[Counter, Counter]:
    """File-time hour of the weekly open, split by whether NY was on DST.

    The discriminator described in the module docstring. Returns
    ``(winter_hours, summer_hours)`` over the weekly opens found in the data.
    """
    winter, summer = Counter(), Counter()
    for (a, *_), (b, *_) in zip(rows, rows[1:]):
        if (b - a) <= timedelta(hours=12):
            continue
        # `b` is the first bar of the new week, stamped in the file's own zone.
        dst = NY.dst(b.replace(tzinfo=None)) != timedelta(0)
        (summer if dst else winter)[b.hour] += 1
    return winter, summer


def to_utc(stamp: datetime, mode: str) -> datetime:
    if mode == "ny":
        return stamp.replace(tzinfo=NY).astimezone(UTC)
    return stamp.replace(tzinfo=timezone(timedelta(hours=-5))).astimezone(UTC)


def probe(symbol: str, year: int, cache: Path) -> int:
    blob = fetch_year(symbol, year, cache)
    rows = rows_from(blob)
    print(f"probing {symbol} {year}: {len(rows):,} M1 rows, zip {len(blob):,} bytes")

    ok = True
    bad = [r for r in rows if not (r[2] >= max(r[1], r[4]) and r[3] <= min(r[1], r[4]))]
    print(f"  OHLC sanity     {'OK' if not bad else f'FAIL {len(bad):,} bad bars'}")
    ok &= not bad

    winter, summer = measure_offset(rows)
    print(f"  weekly open, winter (NY on EST): {dict(winter)}")
    print(f"  weekly open, summer (NY on EDT): {dict(summer)}")
    if not winter or not summer:
        print("  INCONCLUSIVE: need both seasons in the sample")
        return 1

    w = winter.most_common(1)[0][0]
    s = summer.most_common(1)[0][0]
    if w == s == 17:
        print(f"  offset          NY LOCAL WITH DST -- weekly open is {w}:00 in both")
        print("                  seasons, so the stamps are NOT a fixed UTC-5. The FAQ's")
        print("                  'EST without DST' does not describe this file.")
        mode = "ny"
    elif w == 17 and s == 16:
        print(f"  offset          FIXED UTC-5 -- weekly open {w}:00 winter, {s}:00 summer,")
        print("                  which is what a constant offset produces. FAQ confirmed.")
        mode = "est"
    else:
        print(f"  FAIL            unexpected pattern: winter {w}:00, summer {s}:00")
        return 1

    # Whichever mode was inferred, the weekly open must land on the real UTC instant.
    for label, want in (("winter", 22), ("summer", 21)):
        src = winter if label == "winter" else summer
        hour = src.most_common(1)[0][0]
        sample = datetime(year, 1, 6, hour) if label == "winter" else datetime(year, 7, 7, hour)
        got = to_utc(sample, mode).hour
        flag = "OK" if got == want else "FAIL"
        if got != want:
            ok = False
        print(f"  {label} open -> {got:02d}:00 UTC (expect {want:02d}:00)  {flag}")

    print(f"\n  => convert with mode={mode!r}")
    return 0 if ok else 1


def build_symbol(symbol: str, years: range, cache: Path, out_dir: Path,
                 mode: str, delay: float) -> int:
    seen: set[int] = set()
    rows: list[tuple] = []
    for year in years:
        blob = fetch_year(symbol, year, cache, delay)
        for stamp, o, h, l, c, v in rows_from(blob):
            ts = int(to_utc(stamp, mode).timestamp())
            if ts in seen:
                continue  # the autumn DST fold repeats an hour of local stamps
            seen.add(ts)
            rows.append((ts, o, h, l, c, v))
        print(f"    {symbol} {year}: {len(rows):,} bars so far", flush=True)

    rows.sort(key=lambda r: r[0])
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / f"{symbol.upper()}.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for ts, o, h, l, c, v in rows:
            w.writerow([
                datetime.fromtimestamp(ts, UTC).isoformat(),
                f"{o:.5f}", f"{h:.5f}", f"{l:.5f}", f"{c:.5f}", f"{v:.2f}",
            ])
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--symbols", nargs="*", default=None)
    ap.add_argument("--start-year", type=int, default=DEFAULT_START_YEAR)
    ap.add_argument("--end-year", type=int, default=DEFAULT_END_YEAR)
    ap.add_argument("--out", type=Path, default=Path("data/raw"))
    ap.add_argument("--cache", type=Path, default=Path("data/raw/_histdata"))
    ap.add_argument("--mode", default="ny", choices=("ny", "est"))
    ap.add_argument("--delay", type=float, default=1.0)
    args = ap.parse_args()

    cfg, _ = load_config()
    symbols = [s.upper() for s in (args.symbols or cfg.symbols)]

    if args.probe:
        return probe(symbols[0], args.start_year, args.cache)

    years = range(args.start_year, args.end_year + 1)
    print(f"{len(symbols)} symbols x {len(years)} years = "
          f"{len(symbols) * len(years)} requests, mode={args.mode}")
    t0 = time.time()
    for sym in symbols:
        t = time.time()
        n = build_symbol(sym, years, args.cache, args.out, args.mode, args.delay)
        print(f"  {sym}: {n:,} M1 bars, {time.time() - t:.0f}s", flush=True)
    print(f"\ntotal {time.time() - t0:.0f}s -> {args.out}")
    print("next: python scripts/build_dataset.py --raw data/raw --out data/parquet --tf M1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
