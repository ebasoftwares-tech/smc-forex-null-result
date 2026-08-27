"""Object identity (SPEC 1.7) — the prerequisite Phase 14 could not start without.

STATE.md section 6 item 10 recorded this as "206 collide across five fixture years". The
real figure was **23,314 of 30,637 ids, 76% of them**, and it affected every object kind
including the ones already namespaced by symbol and timeframe — their sequence restarted
with each run. Pooling trades from two runs would have joined unrelated objects.

The interesting constraint is that SPEC 1.7 asks for a ULID and SPEC 25.1 forbids what a
ULID is made of: wall-clock milliseconds and randomness. These tests pin the resolution —
ULID shape, bar clock, content-addressed entropy — and the two properties that resolution
has to keep: determinism, and prefix-stability under truncation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bot.core.fvg import detect_fvgs
from bot.core.ids import is_object_id, object_id
from bot.core.liquidity import LevelSource, _source_precedence, build_book
from bot.core.sessions import build_sessions
from bot.core.structure import analyse_structure
from bot.core.sweeps import analyse_sweeps
from bot.core.swings import detect_swings
from bot.data.resample import resample
from bot.data.synthetic import generate

UTC = timezone.utc
AT = datetime(2026, 3, 2, 12, tzinfo=UTC)


def _mk(**kw):
    base = dict(symbol="EURUSD", timeframe="H4", at=AT, key=("a", 1.0815))
    base.update(kw)
    return object_id("LV", **base)


# ------------------------------------------------------------------ the format


def test_an_id_is_a_26_character_crockford_ulid():
    got = _mk()
    assert len(got) == 26
    assert is_object_id(got)
    # Crockford omits I, L, O and U so an id cannot be misread in a log.
    assert not (set(got) & set("ILOU"))


def test_ids_sort_chronologically_by_the_bar_that_made_them_knowable():
    """The one property a ULID exists for, preserved."""
    earlier = _mk(at=AT)
    later = _mk(at=AT + timedelta(hours=4))
    assert earlier < later
    assert earlier[:10] != later[:10]


# --------------------------------------------------------------- determinism


def test_the_same_object_gets_the_same_id_every_time():
    """SPEC 25.1: 'the same data plus the same config_hash produces byte-identical' output.

    A real ULID cannot do this — it is wall-clock plus randomness, so two runs over
    identical data would differ. See D-015 section 1.
    """
    assert _mk() == _mk()


def test_no_wall_clock_reaches_the_id():
    """The mutation check for the previous test, and the reason it exists.

    If `datetime.now()` were anywhere in this path, two calls separated in time would
    differ. They are separated here by an actual sleep-free but ordered pair of calls, and
    by construction the only clock the function is given is the bar's.
    """
    import time

    a = _mk()
    time.sleep(0.01)
    b = _mk()
    assert a == b


def test_the_entropy_is_the_natural_key_not_a_sequence():
    """Different objects differ; the same object at a different position does not.

    A sequence number would fail the second half — and that is the whole reason the entropy
    is content-addressed. Two runs over different date ranges number the same object
    differently, so a sequence-keyed id gives one object two ids across runs, and can give
    two objects one id.
    """
    assert _mk(key=("a", 1.0815)) != _mk(key=("a", 1.0816))
    assert _mk(key=("a", 1.0815)) != _mk(key=("b", 1.0815))
    assert _mk(symbol="GBPUSD") != _mk(symbol="EURUSD")
    assert _mk(timeframe="D1") != _mk(timeframe="H4")
    assert object_id("LV", symbol="EURUSD", timeframe="H4", at=AT, key=("a",)) != (
        object_id("SW", symbol="EURUSD", timeframe="H4", at=AT, key=("a",))
    )


def test_a_float_hashes_the_same_however_it_arrived():
    """A numpy scalar and a Python float are the same price.

    An id that depended on which code path built the object would not be an identity.
    """
    import numpy as np

    assert _mk(key=("a", 1.0815)) == _mk(key=("a", np.float64(1.0815)))


def test_a_negative_epoch_is_clamped_not_wrapped():
    """A wrapped 48-bit timestamp would sort a 1969 object after a 2026 one."""
    ancient = object_id("LV", symbol="EURUSD", timeframe="H4", at=-10**9, key=("a",))
    assert is_object_id(ancient)
    assert ancient[:10] == "0" * 10
    assert ancient < _mk()


# ------------------------------------------------- the regression it was built for


@pytest.fixture(scope="module")
def three_years(cfg):
    out = []
    for k, year in enumerate((2024, 2025, 2026)):
        m1 = generate("EURUSD", datetime(year, 1, 1, tzinfo=UTC),
                      datetime(year, 3, 31, tzinfo=UTC), cfg, timeframe="M1", seed=41 + k)
        h4 = resample(m1, "H4", cfg)
        d1 = resample(m1, "D1", cfg)
        m15 = resample(m1, "M15", cfg)
        st = analyse_structure(h4, cfg)
        book, sw = analyse_sweeps(
            cfg=cfg, h4=h4, d1=d1, w1=resample(m1, "W1", cfg),
            mn1=resample(m1, "MN1", cfg), sessions=build_sessions(m15, cfg),
            h4_structure=st, d1_swings=detect_swings(d1, cfg),
        )
        out.append((book, st, sw, detect_fvgs(h4, cfg)))
    return out


def test_no_id_collides_across_runs(three_years):
    """The measurement that made this a Phase 14 prerequisite.

    Under the old scheme this pooled set was 76% duplicates. Trades from two runs could
    not be put in one table without joining unrelated objects to each other.
    """
    ids: list[str] = []
    for book, st, sw, fvgs in three_years:
        ids += [lv.id for lv in book.levels]
        ids += [s.id for s in st.swings.swings]
        ids += [e.id for e in st.events]
        ids += [e.id for e in sw.events]
        ids += [c.id for c in sw.clusters]
        ids += [f.id for f in fvgs]
    assert len(ids) > 4_000
    assert len(set(ids)) == len(ids)
    assert all(is_object_id(i) for i in ids)


def test_every_engine_emits_the_new_format(three_years):
    """A generator left on the old scheme would pass the collision test by luck."""
    book, st, sw, fvgs = three_years[0]
    for group in (
        [lv.id for lv in book.levels],
        [s.id for s in st.swings.swings],
        [e.id for e in st.events],
        [e.id for e in sw.events],
        [c.id for c in sw.clusters],
        [f.id for f in fvgs],
    ):
        assert group, "an engine produced nothing; the test would pass vacuously"
        assert all(is_object_id(i) for i in group)


def test_level_ids_are_prefix_stable_under_truncation(cfg, m15_quarter):
    """SPEC 25.2 applied to identity itself.

    An id keyed on anything run-relative changes when the run is truncated — and because
    admission order tie-breaks on the id, that silently reorders the book. The first
    version of this keyed on a candidate sequence number and did exactly that, breaking
    `test_admission_order_is_prefix_stable`. See D-015 section 1.
    """
    h4 = resample(m15_quarter, "H4", cfg)
    d1 = resample(m15_quarter, "D1", cfg)

    def ids_for(n: int) -> list[str]:
        sub = h4.head(n)
        book = build_book(cfg=cfg, h4=sub, d1=d1,
                          sessions=build_sessions(m15_quarter, cfg))
        return [lv.id for lv in book.levels]

    full = ids_for(h4.n)
    for frac in (0.4, 0.7):
        part = ids_for(int(h4.n * frac))
        assert part, frac
        assert set(part) <= set(full), frac


# ------------------------------------------------ the merge rule it flushed out


def test_a_swing_beats_the_protected_swing_that_annotates_it():
    """D-015 section 2.

    ``PROTECTED_SWING`` duplicates a ``SWING_*`` at the identical price ~95% of the time
    (D-006) and shares its tier and confirmation bar, so tier and time both tie. Until
    Phase 14 the winner fell out of the id format by accident; changing the format flipped
    it and moved ``PROTECTED_SWING``'s share of anchored sweeps from under 2% to 3.3%.

    The rule is now stated: a primary structural object beats one that annotates it.
    """
    assert _source_precedence(LevelSource.SWING_HIGH) < _source_precedence(
        LevelSource.PROTECTED_SWING
    )
    assert _source_precedence(LevelSource.SWING_LOW) < _source_precedence(
        LevelSource.PROTECTED_SWING
    )
    # ... and derived levels lose to the primaries they are derived from.
    assert _source_precedence(LevelSource.SWING_HIGH) < _source_precedence(
        LevelSource.EQUAL_HIGHS
    )
    assert _source_precedence(LevelSource.EQUAL_HIGHS) < _source_precedence(
        LevelSource.RANGE_HIGH
    )


def test_every_level_source_has_a_declared_precedence():
    """A source added without one would silently sort last and nobody would notice."""
    for source in LevelSource:
        assert _source_precedence(source) < 99, source
