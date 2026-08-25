"""Data quality (SPEC 1.5), ATR (SPEC 1.6), and dataset persistence."""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pytest

from bot.config.loader import config_hash, load_config
from bot.core.bars import build_series
from bot.core.indicators import atr_ref, true_range, wilder_atr
from bot.data import ingest, quality
from bot.data.calendar import UTC
from bot.data.resample import resample
from bot.data.synthetic import generate


@pytest.fixture(scope="module")
def defective(cfg):
    """A month with one holiday and one intraday outage."""
    return generate(
        "EURUSD",
        datetime(2026, 3, 1, tzinfo=UTC),
        datetime(2026, 3, 31, tzinfo=UTC),
        cfg,
        timeframe="M15",
        holidays=[date(2026, 3, 17)],
        drop_ranges=[(datetime(2026, 3, 11, 9, 0, tzinfo=UTC), datetime(2026, 3, 11, 11, 0, tzinfo=UTC))],
    )


# ------------------------------------------------------------------------ ATR


def test_true_range_matches_the_definition():
    o = np.array([1.0, 1.1, 1.2])
    h = np.array([1.2, 1.3, 1.25])
    l = np.array([0.9, 1.05, 1.0])
    c = np.array([1.1, 1.2, 1.05])
    tr = true_range(h, l, c)
    assert tr[0] == pytest.approx(0.3)  # first bar falls back to its own range
    assert tr[1] == pytest.approx(max(1.3 - 1.05, abs(1.3 - 1.1), abs(1.05 - 1.1)))
    assert tr[2] == pytest.approx(max(1.25 - 1.0, abs(1.25 - 1.2), abs(1.0 - 1.2)))


def test_wilder_atr_seeds_and_smooths_correctly():
    n, period = 40, 14
    h = np.linspace(1.10, 1.14, n)
    l = h - 0.001
    c = (h + l) / 2
    s = build_series("X", "H4", np.arange(n) * 14400, np.arange(1, n + 1) * 14400, c, h, l, c, np.ones(n))
    atr = wilder_atr(s, period)
    assert np.all(np.isnan(atr[: period - 1]))
    tr = true_range(h, l, c)
    assert atr[period - 1] == pytest.approx(tr[:period].mean())
    assert atr[period] == pytest.approx(((period - 1) * atr[period - 1] + tr[period]) / period)


def test_atr_ref_is_the_previous_bars_value(cfg, m15_quarter):
    """SPEC 1.6: a bar must never raise the threshold it is tested against."""
    atr = wilder_atr(m15_quarter, cfg.atr.period)
    ref = atr_ref(m15_quarter, cfg.atr.period)
    assert np.isnan(ref[0])
    assert np.allclose(ref[1:], atr[:-1], equal_nan=True)


# -------------------------------------------------------------------- quality


def test_weekend_gaps_are_not_defects(cfg, m15_quarter):
    """The week open coincides exactly with the first bar after the gap.

    An exclusive upper bound classified every weekend as a data defect -- found during
    Phase 1 implementation, and the reason this test exists.
    """
    gaps, suspect = quality.find_gaps(m15_quarter, cfg)
    assert gaps, "a quarter of FX data must contain weekend gaps"
    assert all(g.is_weekend for g in gaps)
    assert not suspect, [(g.start_utc, g.missing_bars) for g in suspect]

    # And the report drops them entirely rather than listing them as defects.
    _, rep = quality.analyse(m15_quarter, cfg)
    assert not rep.gaps
    assert not rep.suspect_gaps


def test_real_defects_are_found_and_classified(cfg, defective):
    flagged, rep = quality.analyse(defective, cfg)
    assert rep.is_clean  # structurally sound
    assert len(rep.suspect_gaps) == 2
    kinds = sorted(g.missing_bars for g in rep.suspect_gaps)
    assert kinds == [8, 96]  # the 2h outage and the full holiday
    assert rep.suspect_bar_count > 0
    assert flagged.flag("data_suspect").sum() == rep.suspect_bar_count


def test_structural_corruption_is_detected(cfg):
    n = 10
    t = np.arange(n, dtype=np.int64) * 900
    good = np.full(n, 1.10)
    s = build_series("X", "M15", t, t + 900, good, good + 0.001, good - 0.001, good, np.ones(n))
    _, rep = quality.analyse(s, cfg, with_sessions=False)
    assert rep.is_clean

    bad_high = (good + 0.001).copy()
    bad_high[3] = 1.05  # high below the low
    s2 = build_series("X", "M15", t, t + 900, good, bad_high, good - 0.001, good, np.ones(n))
    _, rep2 = quality.analyse(s2, cfg, with_sessions=False)
    assert not rep2.is_clean
    assert rep2.invalid_ohlc >= 1


def test_week_anchors_are_measured_against_bar_close(cfg, m15_quarter):
    """An M15 bar opening 20:45 closes exactly on a 21:00 anchor.

    Measuring its open reported a spurious 15-minute deviation on every week.
    """
    _, rep = quality.analyse(m15_quarter, cfg)
    assert rep.week_anchors
    assert rep.week_anchor_violations == 0
    assert all(a.close_deviation_hours == 0.0 for a in rep.week_anchors)
    assert all(a.open_deviation_hours == 0.0 for a in rep.week_anchors)


def test_session_coverage_counts_the_holiday_as_absent(cfg, defective):
    _, rep = quality.analyse(defective, cfg)
    ldn = next(c for c in rep.session_coverage if c.session_name == "LONDON")
    assert ldn.absent >= 1
    assert ldn.expected == ldn.built + ldn.absent


def test_spike_filter_flags_an_implausible_bar(cfg, m15_quarter):
    assert not quality.find_spikes(m15_quarter, cfg)
    s = m15_quarter
    high = s.high.copy()
    high[2000] += 0.05  # ~50x a normal M15 range
    spiked = build_series(
        s.symbol, s.timeframe, s.open_time, s.close_time, s.open, high, s.low, s.close, s.volume
    )
    spikes = quality.find_spikes(spiked, cfg)
    assert len(spikes) == 1
    assert int(spikes[0].timestamp()) == int(s.open_time[2000])


def test_suspect_flag_propagates_upward(cfg, defective):
    """A derived bar built partly from suspect data is itself suspect."""
    flagged, rep = quality.analyse(defective, cfg)
    h4 = ingest._propagate_suspect(flagged, resample(flagged, "H4", cfg))
    assert h4.flag("data_suspect").any()
    for i in range(h4.n):
        seg = flagged.slice_between(int(h4.open_time[i]), int(h4.close_time[i]))
        assert h4.flag("data_suspect")[i] == bool(seg.flag("data_suspect").any())


# --------------------------------------------------------------------- ingest


def test_parquet_round_trip_is_lossless(cfg, cfg_hash, tmp_path, m15_quarter):
    ingest.write_series(m15_quarter, tmp_path)
    back = ingest.read_series(tmp_path, "EURUSD", "M15")
    assert back.n == m15_quarter.n
    for name in ("open_time", "close_time", "open", "high", "low", "close", "volume"):
        assert np.array_equal(getattr(back, name), getattr(m15_quarter, name)), name
    assert ingest.content_hash(back) == ingest.content_hash(m15_quarter)


def test_build_dataset_writes_every_timeframe_and_a_manifest(cfg, cfg_hash, tmp_path, m1_month):
    manifest = ingest.build_dataset({"EURUSD": m1_month}, cfg, cfg_hash, tmp_path)
    tfs = {e.timeframe for e in manifest.series}
    assert tfs == {"M1", "M15", "H1", "H4", "D1", "W1", "MN1"}
    assert manifest.config_hash == cfg_hash
    assert manifest.tzdata_version
    assert manifest.day_boundary == "UTC 00:00"
    assert manifest.price_side == "bid"
    assert len(manifest.dataset_hash) == 64
    assert (tmp_path / "manifest.json").exists()

    reloaded = ingest.DatasetManifest.load(tmp_path / "manifest.json")
    assert reloaded.dataset_hash == manifest.dataset_hash


def test_dataset_hash_is_deterministic_and_content_sensitive(cfg, cfg_hash, tmp_path, m1_month):
    a = ingest.build_dataset({"EURUSD": m1_month}, cfg, cfg_hash, tmp_path / "a")
    b = ingest.build_dataset({"EURUSD": m1_month}, cfg, cfg_hash, tmp_path / "b")
    assert a.dataset_hash == b.dataset_hash

    mutated = build_series(
        m1_month.symbol,
        m1_month.timeframe,
        m1_month.open_time,
        m1_month.close_time,
        m1_month.open,
        m1_month.high + 1e-6,
        m1_month.low,
        m1_month.close,
        m1_month.volume,
    )
    c = ingest.build_dataset({"EURUSD": mutated}, cfg, cfg_hash, tmp_path / "c")
    assert c.dataset_hash != a.dataset_hash


def test_ingest_refuses_structurally_invalid_source(cfg, cfg_hash, tmp_path):
    n = 20
    t = np.arange(n, dtype=np.int64) * 60
    v = np.full(n, 1.10)
    high = (v + 0.001).copy()
    high[5] = 1.0  # high below low
    bad = build_series("X", "M1", t, t + 60, v, high, v - 0.001, v, np.ones(n))
    with pytest.raises(ingest.IngestError, match="structurally invalid"):
        ingest.build_dataset({"X": bad}, cfg, cfg_hash, tmp_path)


def test_non_utc_source_is_refused(tmp_path):
    with pytest.raises(ingest.IngestError, match="non-UTC"):
        ingest.read_csv(tmp_path / "x.csv", "EURUSD", "M1", timestamp_is_utc=False)


# --------------------------------------------------------------------- config


def test_config_hash_is_deterministic_and_sensitive():
    a, ha = load_config()
    b, hb = load_config()
    assert ha == hb == config_hash(a) == config_hash(b)
    _, hc = load_config(overrides={"tf": {"day_boundary_tz": "America/New_York"}})
    assert hc != ha


def test_unknown_parameter_is_a_load_error():
    with pytest.raises(Exception):
        load_config(overrides={"tf": {"nonsense": 1}})


def test_config_is_immutable(cfg):
    with pytest.raises(Exception):
        cfg.atr.period = 20
