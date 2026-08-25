from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bot.config.loader import load_config
from bot.data.synthetic import fixture_year, generate

UTC = timezone.utc


@pytest.fixture(scope="session")
def cfg():
    """The shipped defaults, including DECISION D-001 (UTC day boundary)."""
    c, _ = load_config()
    return c


@pytest.fixture(scope="session")
def cfg_hash():
    _, h = load_config()
    return h


@pytest.fixture(scope="session")
def ny_cfg():
    """The New-York-anchor ablation.  Exercises the 23h/25h day paths."""
    c, _ = load_config(overrides={"tf": {"day_boundary_tz": "America/New_York"}})
    return c


@pytest.fixture(scope="session")
def m15_year(cfg):
    """The DST fixture year required by the Phase 1 gate."""
    return fixture_year(cfg, year=2026, timeframe="M15")


@pytest.fixture(scope="session")
def m15_quarter(cfg):
    return generate(
        "EURUSD",
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 3, 31, tzinfo=UTC),
        cfg,
        timeframe="M15",
    )


@pytest.fixture(scope="session")
def m1_month(cfg):
    """A month of M1 bars -- the real ingest timeframe."""
    return generate(
        "EURUSD",
        datetime(2026, 2, 1, tzinfo=UTC),
        datetime(2026, 2, 28, tzinfo=UTC),
        cfg,
        timeframe="M1",
    )
