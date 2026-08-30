"""Download Dukascopy M1 candles into the CSV shape ``ingest.read_csv`` expects.

    python scripts/fetch_dukascopy.py --probe                 # verify the format, 1 file
    python scripts/fetch_dukascopy.py --symbols EURUSD        # one symbol
    python scripts/fetch_dukascopy.py                         # every configured symbol

**Why Dukascopy and why M1 rather than ticks.** `BACKTEST_PROTOCOL.md` §2 wants M1 or
better, and `backtest.intrabar_mode = m1_path` -- the setting Q2 exists to enable -- needs
M1 bars, not ticks. Dukascopy publishes M1 candles directly, which turns Q2's "~20-60 GB"
tick estimate into a couple of hundred megabytes, and its timestamps are **UTC** so
``read_csv`` accepts them without the conversion every broker export needs.

Four things about the wire format, all of which are easy to get silently wrong:

1. **The month in the URL is 0-indexed.** ``/2019/00/01/`` is 1 January 2019. A 1-indexed
   reader is off by one month all year and still returns valid-looking data for eleven of
   them, which is the worst possible failure mode. ``--probe`` checks this against the
   timestamps inside the file rather than trusting it.
2. **The payload is raw LZMA** (``FORMAT_ALONE``), not ``.xz``. An empty body is a closed
   market, not an error.
3. **A candle record is 24 bytes, and its field order is not OHLC.** It is
   ``>5if`` = (second-of-day, **open, close, low, high**, volume). Reading it as OHLC
   swaps close and low, which still passes a naive "is it a number" check and produces
   bars whose high is below their open.
4. **Prices are integers scaled by the instrument's point factor** -- 1e5 for most pairs,
   1e3 for JPY quotes. Getting this wrong scales every price by 100 and every ATR with it.

**It is deliberately slow, and it caches every response.** The first version ran eight
concurrent workers and was throttled by Dukascopy within two minutes -- a URL that had
succeeded moments earlier started refusing connections, and it took about three minutes to
clear. This is a free public service and ~19,000 requests is a lot to ask of one; the
default is now serial with a delay. The cache is what makes that tolerable: every day's
payload is written to ``data/raw/_cache`` before it is decoded, so a re-run skips what it
already has and a throttle costs minutes rather than the whole download. It also means a
mistake in the record layout can be fixed by re-decoding rather than re-downloading.

Not in ``requirements.txt``: this is a build-time tool like the asset generators, and the
shipped bot never downloads anything.
"""

from __future__ import annotations

import argparse
import csv
import lzma
import struct
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.config.loader import load_config  # noqa: E402

UTC = timezone.utc
BASE = "https://datafeed.dukascopy.com/datafeed"
#: One record: second-of-day, open, close, low, high (scaled ints), volume (float).
_REC = struct.Struct(">5if")
_UA = "Mozilla/5.0 (research backtest dataset builder)"

DEFAULT_START = date(2019, 1, 1)
DEFAULT_END = date(2025, 12, 31)


def point_factor(symbol: str) -> float:
    """1e3 for a JPY quote, 1e5 otherwise. The quote currency decides, not the base."""
    return 1e3 if symbol.upper().endswith("JPY") else 1e5


def url_for(symbol: str, day: date) -> str:
    """Note ``day.month - 1``: Dukascopy months are 0-indexed."""
    return (
        f"{BASE}/{symbol.upper()}/{day.year:04d}/{day.month - 1:02d}/{day.day:02d}"
        f"/BID_candles_min_1.bi5"
    )


def fetch(url: str, *, retries: int = 6, timeout: float = 45.0) -> bytes:
    """Raw body, or ``b""`` for a day with no data. Raises only on real failure.

    The backoff is generous because the failure mode here is a throttle, not a flaky
    packet: once Dukascopy starts refusing, retrying quickly just extends the refusal.
    """
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 410):
                return b""  # no session that day; not an error
            last = exc
        except Exception as exc:  # noqa: BLE001 - network, retried below
            last = exc
        time.sleep(min(60.0, 3.0 * (2 ** attempt)))
    raise RuntimeError(f"failed after {retries} attempts: {url}") from last


def cached_fetch(symbol: str, day: date, cache: Path, delay: float) -> bytes:
    """``fetch`` behind a disk cache. A zero-byte file means "no session", and is cached
    too -- otherwise every weekend is re-requested on every resume."""
    path = cache / symbol.upper() / f"{day.isoformat()}.bi5"
    if path.exists():
        return path.read_bytes()
    payload = fetch(url_for(symbol, day))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    if delay:
        time.sleep(delay)
    return payload


def decode(payload: bytes, day: date, factor: float) -> list[tuple[int, float, float, float, float, float]]:
    """Decompress and unpack one day into ``(epoch_s, o, h, l, c, volume)`` rows."""
    if not payload:
        return []
    raw = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE).decompress(payload)
    midnight = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())
    out = []
    for sec, o, c, lo, hi, vol in _REC.iter_unpack(raw[: len(raw) - len(raw) % _REC.size]):
        out.append((
            midnight + sec,
            o / factor, hi / factor, lo / factor, c / factor, float(vol),
        ))
    return out


def _days(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() != 5:  # Saturday has no session; skip the request entirely
            yield d
        d += timedelta(days=1)


def probe(symbol: str = "EURUSD") -> int:
    """Verify the month indexing, the record layout and the price scale on one file."""
    day = date(2024, 3, 5)  # an ordinary Tuesday
    url = url_for(symbol, day)
    print(f"probing {url}")
    rows = decode(fetch(url), day, point_factor(symbol))
    if not rows:
        print("  EMPTY -- either the month indexing is wrong or the day had no session")
        return 1

    ok = True
    first, last = rows[0], rows[-1]
    print(f"  rows            {len(rows):,}")
    print(f"  first           {datetime.fromtimestamp(first[0], UTC):%Y-%m-%d %H:%M} "
          f"O={first[1]:.5f} H={first[2]:.5f} L={first[3]:.5f} C={first[4]:.5f}")
    print(f"  last            {datetime.fromtimestamp(last[0], UTC):%Y-%m-%d %H:%M}")

    # 1. The timestamps inside the file must land on the day we asked for. This is what
    #    catches a 1-indexed month, which otherwise returns a perfectly valid other month.
    got = datetime.fromtimestamp(first[0], UTC).date()
    if got != day:
        print(f"  FAIL  month indexing: asked {day}, file contains {got}")
        ok = False
    else:
        print(f"  month indexing  OK (0-indexed, {day} confirmed)")

    # 2. If the field order were OHLC rather than O/C/L/H, high would sit below open.
    bad = [r for r in rows if not (r[2] >= max(r[1], r[4]) and r[3] <= min(r[1], r[4]))]
    if bad:
        print(f"  FAIL  record layout: {len(bad):,} bars with high<max(o,c) or low>min(o,c)")
        ok = False
    else:
        print("  record layout   OK (>5if = sec, open, close, low, high, volume)")

    # 3. A price scale that is off by 100 is obvious against any plausible FX quote.
    mid = sorted(r[4] for r in rows)[len(rows) // 2]
    if not (0.3 < mid < 300.0):
        print(f"  FAIL  price scale: median close {mid} is not a plausible FX rate")
        ok = False
    else:
        print(f"  price scale     OK (median close {mid:.5f})")
    return 0 if ok else 1


def download_symbol(symbol: str, start: date, end: date, out_dir: Path,
                    workers: int, cache: Path, delay: float) -> tuple[int, int]:
    factor = point_factor(symbol)
    days = list(_days(start, end))
    rows: list[tuple] = []
    empty = 0

    def one(d: date):
        return d, cached_fetch(symbol, d, cache, delay)

    if workers <= 1:
        results = (one(d) for d in days)
    else:
        pool = ThreadPoolExecutor(max_workers=workers)
        results = pool.map(one, days)

    for n, (d, payload) in enumerate(results, 1):
        got = decode(payload, d, factor)
        if got:
            rows.extend(got)
        else:
            empty += 1
        if n % 250 == 0:
            print(f"    {symbol} {n:,}/{len(days):,} days, {len(rows):,} bars",
                  flush=True)

    rows.sort(key=lambda r: r[0])
    # A duplicate timestamp would make BarSeries refuse the file outright; Dukascopy
    # occasionally repeats a bar at a day boundary.
    deduped, seen = [], set()
    for r in rows:
        if r[0] not in seen:
            seen.add(r[0])
            deduped.append(r)

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{symbol.upper()}.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for ts, o, h, l, c, v in deduped:
            w.writerow([
                datetime.fromtimestamp(ts, UTC).isoformat(),
                f"{o:.5f}", f"{h:.5f}", f"{l:.5f}", f"{c:.5f}", f"{v:.2f}",
            ])
    return len(deduped), empty


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--probe", action="store_true", help="verify the format on one file")
    ap.add_argument("--symbols", nargs="*", default=None)
    ap.add_argument("--start", default=DEFAULT_START.isoformat())
    ap.add_argument("--end", default=DEFAULT_END.isoformat())
    ap.add_argument("--out", type=Path, default=Path("data/raw"))
    ap.add_argument("--workers", type=int, default=1,
                    help="keep this low; 8 got the first run throttled in two minutes")
    ap.add_argument("--delay", type=float, default=0.15,
                    help="seconds between requests, skipped for cache hits")
    ap.add_argument("--cache", type=Path, default=Path("data/raw/_cache"))
    args = ap.parse_args()

    if args.probe:
        return probe((args.symbols or ["EURUSD"])[0])

    cfg, _ = load_config()
    symbols = [s.upper() for s in (args.symbols or cfg.symbols)]
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    print(f"{len(symbols)} symbols, {start} -> {end}, {args.workers} worker(s), "
          f"{args.delay}s delay, cache {args.cache}")
    t0 = time.time()
    for sym in symbols:
        t = time.time()
        n, empty = download_symbol(
            sym, start, end, args.out, args.workers, args.cache, args.delay
        )
        print(f"  {sym}: {n:,} M1 bars, {empty:,} empty days, {time.time() - t:.0f}s",
              flush=True)
    print(f"\ntotal {time.time() - t0:.0f}s -> {args.out}")
    print("next: python scripts/build_dataset.py --raw data/raw --out data/parquet --tf M1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
