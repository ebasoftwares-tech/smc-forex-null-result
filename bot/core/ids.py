"""Object identity (SPEC 1.7), made deterministic.

SPEC 1.7 asks for a **ULID**: *"monotonic by creation time, globally unique"*. A real ULID
is 48 bits of wall-clock milliseconds plus 80 bits of randomness, and **both halves are
forbidden here**. SPEC 25.1 requires that the same data and the same ``config_hash``
produce byte-identical output — *"no wall-clock reads, no unseeded randomness"* — and 25.4
prohibits ``datetime.now()`` anywhere in the signal path, because *"time comes from the bar
being processed, so the same code runs identically in backtest and live"*.

A standard ULID would break reproducibility on its own: two runs over identical data would
produce different ids, and `events.jsonl` would never be byte-identical. See D-015 §1.

So the ids here keep the ULID's **shape and ordering property** and derive both halves
deterministically:

* **Timestamp (10 chars, 48 bits)** — the millisecond of the bar that made the object
  knowable, not the wall clock. Lexicographic order is still chronological order, which is
  the property ULIDs exist for.
* **Entropy (16 chars, 80 bits)** — a BLAKE2b digest of the object's **natural key**, not
  randomness. This is content addressing, and it buys the thing STATE.md's pooling
  requirement actually needs: *the same logical object gets the same id in every run*, and
  two different objects cannot collide even when they are created at the same bar.

A sequence number was the obvious alternative and is worse. Two runs over different date
ranges number the same object differently, so pooling their trades would give one object two
ids — and, worse, could give two different objects the *same* id, since a collision only
needs the same slot in the same bar. Sequence numbers are still accepted as a tiebreaker for
objects whose natural keys genuinely coincide.

**The problem this fixes is large and was under-recorded.** Over five fixture years the old
scheme produced 30,637 ids of which only 7,323 were distinct: **23,314 duplicates, 76% of
the total**, and it affected every object kind — including the ones already namespaced by
symbol and timeframe, because their sequence restarted with each run. STATE.md recorded 206.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Iterable

#: Crockford base32: no I, L, O or U, so an id cannot be misread aloud or in a log.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_TIME_CHARS = 10
_ENTROPY_CHARS = 16
#: 48 bits, the ULID timestamp width.  Overflows in the year 10889.
_TIME_MAX = (1 << 48) - 1


def _encode(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(out))


def _timestamp_ms(at: datetime | int | float) -> int:
    """Milliseconds for the id's time half, from a bar's clock only.

    Negative epochs are clamped to 0 rather than wrapped. A pre-1970 bar is not a thing this
    system trades, and a wrapped timestamp would silently sort a 1969 object after a 2026 one
    — which is exactly the property the format exists to provide.
    """
    if isinstance(at, datetime):
        ms = int(at.timestamp() * 1000)
    else:
        ms = int(at * 1000)
    return max(0, min(ms, _TIME_MAX))


def _entropy(kind: str, symbol: str, timeframe: str, key: Iterable[object]) -> int:
    """80 bits of BLAKE2b over the natural key.  Deterministic, never random.

    Floats are formatted with ``.10g`` rather than ``repr`` so that a price which arrives as
    a numpy scalar in one path and a Python float in another hashes identically — an id that
    depends on which code path built the object is not an identity.
    """
    parts = [kind, symbol, timeframe]
    for k in key:
        if isinstance(k, float):
            parts.append(f"{k:.10g}")
        elif isinstance(k, datetime):
            parts.append(k.isoformat())
        else:
            parts.append(str(k))
    digest = hashlib.blake2b("\x1f".join(parts).encode("utf-8"), digest_size=10).digest()
    return int.from_bytes(digest, "big")


def object_id(
    kind: str,
    *,
    symbol: str,
    timeframe: str,
    at: datetime | int | float,
    key: Iterable[object] = (),
) -> str:
    """A deterministic 26-character ULID for one derived object.

    ``at`` is the bar time at which the object became knowable — ``confirmed_at`` in SPEC
    1.7's vocabulary, never the wall clock. ``key`` is the object's natural key: whatever
    tuple makes two of these the same thing. Pass **times and prices, never bar indices** —
    an index is relative to where the run started, so an index-keyed id changes when the
    date range does, which defeats the whole purpose.
    """
    ts = _encode(_timestamp_ms(at), _TIME_CHARS)
    ent = _encode(_entropy(kind, symbol, timeframe, key), _ENTROPY_CHARS)
    return ts + ent


def is_object_id(value: str) -> bool:
    """Whether a string is one of ours.  Used by tests, not by the signal path."""
    return (
        len(value) == _TIME_CHARS + _ENTROPY_CHARS
        and all(c in _ALPHABET for c in value)
    )
