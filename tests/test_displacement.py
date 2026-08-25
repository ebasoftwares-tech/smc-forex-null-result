"""Displacement (SPEC 10) and FVG detection (SPEC 12.1)."""

from __future__ import annotations

import numpy as np
import pytest

from bot.config.loader import load_config
from bot.core.bars import build_series
from bot.core.displacement import (
    Direction,
    DisplacementReason,
    evaluate,
    evaluate_bar,
    evaluate_leg,
    leg_origin,
)
from bot.core.fvg import FvgDirection, FvgStatus, detect_fvgs, fvgs_in_leg
from bot.core.indicators import atr_ref
from bot.data.resample import resample

H4 = 14400
WARM = 20
MID = 1.07860
HALF = 0.00225  # true range 0.00450 -> ATR exactly 0.00450


def make(tail: list[tuple], warm: int = WARM, mid: float = MID, half: float = HALF):
    t = list(np.arange(warm) * H4) + [(warm + k) * H4 for k in range(len(tail))]
    O = [mid] * warm + [x[0] for x in tail]
    H = [mid + half] * warm + [x[1] for x in tail]
    L = [mid - half] * warm + [x[2] for x in tail]
    C = [mid] * warm + [x[3] for x in tail]
    ts = np.asarray(t, dtype=np.int64)
    return build_series(
        "EURUSD", "H4", ts, ts + H4, np.array(O), np.array(H), np.array(L), np.array(C),
        np.ones(len(ts)),
    )


BAR_A = (1.08240, 1.08310, 1.08150, 1.08290)
FAIL_B = (1.08290, 1.08760, 1.08270, 1.08720)
PASS_B = (1.08290, 1.08870, 1.08270, 1.08830)


# ------------------------------------------------------- SPEC 10.4 worked example


def test_spec_10_4_as_written_fails(cfg):
    """The spec's example is deliberately one that FAILS.

    "A threshold that never rejects anything is not a filter" -- SPEC 10.4.
    """
    s = make([BAR_A, FAIL_B])
    atr = atr_ref(s, cfg.atr.period)
    assert atr[WARM + 1] == pytest.approx(0.00450, abs=1e-9)

    r = evaluate_leg(s, WARM, WARM + 1, Direction.BULLISH, cfg, detect_fvgs(s, cfg), atr)
    assert r.net == pytest.approx(0.00570)
    assert r.net_atr == pytest.approx(1.27, abs=0.005)
    assert r.dir_bars == 2
    assert not r.confirmed
    assert r.failed_on is DisplacementReason.NET_TOO_SMALL


def test_spec_10_4_variant_confirms(cfg):
    s = make([BAR_A, PASS_B])
    atr = atr_ref(s, cfg.atr.period)
    r = evaluate_leg(s, WARM, WARM + 1, Direction.BULLISH, cfg, detect_fvgs(s, cfg), atr)
    assert r.net == pytest.approx(0.00680)
    assert r.net_atr == pytest.approx(1.51, abs=0.005)
    assert r.bodies == pytest.approx(0.00590)
    assert r.gross == pytest.approx(0.00760)
    assert r.body_ratio == pytest.approx(0.776, abs=0.001)
    assert r.dir_bars == 2
    assert r.fvg_count >= 1
    assert r.confirmed
    assert r.reasons == ()


# --------------------------------------------------------------- SPEC 10.1 rules


def test_leg_origin_is_clamped_to_the_sweep_extreme(cfg):
    """SPEC 10.5: the leg is never measured from before the sweep that defines it."""
    assert leg_origin(sweep_extreme_bar=10, break_bar=20, cfg=cfg) == 18  # max_leg_bars 3
    assert leg_origin(sweep_extreme_bar=19, break_bar=20, cfg=cfg) == 19  # clamped
    assert leg_origin(sweep_extreme_bar=20, break_bar=20, cfg=cfg) == 20


def test_the_sweep_extreme_clamps_the_origin_even_when_that_fails(cfg):
    """SPEC 10.5: 'No search over origins -- searching for the window that passes is how
    a filter becomes a formality.'

    Note which way the incentive runs: ``net`` is measured from the leg's lowest low, so
    a *longer* leg always has a larger net.  An origin search would therefore always
    reach for the longest allowed window and the filter would be strictly weaker.  What
    stops it is the clamp to the sweep extreme -- and this test pins the case where the
    clamp is what makes the leg fail.
    """
    s = make([BAR_A, PASS_B])
    atr = atr_ref(s, cfg.atr.period)
    fv = detect_fvgs(s, cfg)

    early = evaluate(s, sweep_extreme_bar=WARM, break_bar=WARM + 1,
                     direction=Direction.BULLISH, cfg=cfg, fvgs=fv, atr=atr)
    late = evaluate(s, sweep_extreme_bar=WARM + 1, break_bar=WARM + 1,
                    direction=Direction.BULLISH, cfg=cfg, fvgs=fv, atr=atr)

    assert early.leg_start == WARM and late.leg_start == WARM + 1
    assert early.net > late.net, "a longer leg reaches a lower low, so net is larger"
    assert early.confirmed
    assert not late.confirmed, "the clamp must be allowed to fail the leg"
    assert late.failed_on is DisplacementReason.NET_TOO_SMALL


def test_only_bars_closing_in_the_leg_direction_contribute_bodies(cfg):
    down = (1.08290, 1.08300, 1.08100, 1.08150)  # a down bar inside a bullish leg
    s = make([BAR_A, down, PASS_B])
    atr = atr_ref(s, cfg.atr.period)
    r = evaluate_leg(s, WARM, WARM + 2, Direction.BULLISH, cfg, detect_fvgs(s, cfg), atr)
    assert r.dir_bars == 2, "the down bar must not count as directional"
    expected = (1.08290 - 1.08240) + (1.08830 - 1.08290)
    assert r.bodies == pytest.approx(expected)


def test_an_all_doji_leg_fails_without_a_division_error(cfg):
    """SPEC 10.5: ``gross = 0`` makes bodies/gross undefined; guarded explicitly."""
    doji = (1.08000, 1.08000, 1.08000, 1.08000)
    s = make([doji, doji])
    r = evaluate_leg(s, WARM, WARM + 1, Direction.BULLISH, cfg, [], atr_ref(s, cfg.atr.period))
    assert r.gross == 0.0
    assert r.body_ratio == 0.0
    assert not r.confirmed
    assert DisplacementReason.BODY_RATIO in r.reasons


def test_every_failing_condition_is_reported_not_just_the_first(cfg):
    """The rejection-rate report needs to know WHICH condition rejects, how often."""
    weak = (1.08000, 1.08010, 1.07990, 1.07995)  # tiny, down-closing, no FVG
    s = make([weak, weak])
    r = evaluate_leg(s, WARM, WARM + 1, Direction.BULLISH, cfg, [], atr_ref(s, cfg.atr.period))
    assert not r.confirmed
    assert DisplacementReason.NET_TOO_SMALL in r.reasons
    assert DisplacementReason.DIRECTIONAL_BARS in r.reasons
    assert DisplacementReason.NO_FVG in r.reasons


def test_bearish_is_the_exact_mirror(cfg):
    a = (1.07600, 1.07700, 1.07540, 1.07560)
    b = (1.07560, 1.07580, 1.06980, 1.07020)
    s = make([a, b])
    atr = atr_ref(s, cfg.atr.period)
    r = evaluate_leg(s, WARM, WARM + 1, Direction.BEARISH, cfg, detect_fvgs(s, cfg), atr)
    assert r.net == pytest.approx(1.07700 - 1.07020)
    assert r.dir_bars == 2
    assert r.confirmed


def test_missing_atr_during_warmup_fails_closed(cfg):
    s = make([BAR_A, PASS_B])
    r = evaluate_leg(s, 1, 2, Direction.BULLISH, cfg, [], atr_ref(s, cfg.atr.period))
    assert not r.confirmed
    assert r.failed_on is DisplacementReason.NO_ATR


# ----------------------------------------------------------------- SPEC 10.3 bar


def test_bar_mode_is_the_classic_single_bar_test(cfg):
    s = make([BAR_A, PASS_B])
    atr = atr_ref(s, cfg.atr.period)
    r = evaluate_bar(s, WARM + 1, Direction.BULLISH, cfg, atr)
    assert r.mode == "bar"
    assert r.leg_bars == 1
    assert r.net == pytest.approx(0.00600)  # the bar's range
    assert r.net_atr == pytest.approx(0.00600 / 0.00450, abs=1e-6)


def test_bar_mode_requires_the_close_in_the_leg_direction(cfg):
    s = make([BAR_A, PASS_B])
    r = evaluate_bar(s, WARM + 1, Direction.BEARISH, cfg, atr_ref(s, cfg.atr.period))
    assert not r.confirmed
    assert DisplacementReason.DIRECTIONAL_BARS in r.reasons


def test_either_mode_falls_back_to_the_bar_test():
    c, _ = load_config(overrides={"disp": {"mode": "either", "require_fvg": False}})
    s = make([BAR_A, FAIL_B])
    atr = atr_ref(s, c.atr.period)
    leg = evaluate_leg(s, WARM, WARM + 1, Direction.BULLISH, c, [], atr)
    assert not leg.confirmed
    out = evaluate(s, WARM, WARM + 1, Direction.BULLISH, c, [], atr)
    assert out.mode == "bar"


# -------------------------------------------------------------- SPEC 12.1 FVGs


def test_fvg_is_defined_by_the_outer_two_bars_only(cfg):
    """SPEC 12.1: bar n-1 is not tested at all.

    An implementation that also tests the middle bar is computing a different object.
    """
    # Middle bar closes DOWN, yet the gap between bar n-2 and bar n is bullish.
    tail = [
        (1.08000, 1.08050, 1.07950, 1.08020),
        (1.08600, 1.08700, 1.08500, 1.08520),  # middle: down-closing
        (1.08800, 1.08900, 1.08300, 1.08850),
    ]
    s = make(tail)
    fvgs = detect_fvgs(s, cfg)
    bull = [f for f in fvgs if f.direction is FvgDirection.BULLISH and f.confirmed_index == WARM + 2]
    assert bull, "a bullish FVG must be found despite the bearish middle bar"
    f = bull[0]
    assert f.zone_low == pytest.approx(1.08050)  # H of bar n-2
    assert f.zone_high == pytest.approx(1.08300)  # L of bar n
    assert f.formed_index == WARM + 1
    assert f.confirmed_index == WARM + 2


def test_fvg_geometry_ce_and_edges(cfg):
    tail = [
        (1.08000, 1.08050, 1.07950, 1.08020),
        (1.08600, 1.08700, 1.08500, 1.08650),
        (1.08800, 1.08900, 1.08300, 1.08850),
    ]
    s = make(tail)
    f = [x for x in detect_fvgs(s, cfg) if x.direction is FvgDirection.BULLISH][-1]
    assert f.size == pytest.approx(f.zone_high - f.zone_low)
    assert f.ce == pytest.approx((f.zone_low + f.zone_high) / 2)
    assert f.proximal == f.zone_low and f.distal == f.zone_high
    assert f.status is FvgStatus.UNMITIGATED


def test_bearish_fvg_mirrors(cfg):
    tail = [
        (1.07800, 1.07850, 1.07750, 1.07800),
        (1.07300, 1.07400, 1.07200, 1.07250),
        (1.07100, 1.07500, 1.07000, 1.07050),
    ]
    s = make(tail)
    bears = [f for f in detect_fvgs(s, cfg) if f.direction is FvgDirection.BEARISH]
    assert bears
    f = bears[-1]
    assert f.zone_low == pytest.approx(1.07500)
    assert f.zone_high == pytest.approx(1.07750)
    assert f.proximal == f.zone_high and f.distal == f.zone_low


def test_undersized_fvgs_are_not_created(cfg):
    """Filtered at creation, so they never appear in a population count."""
    tail = [
        (1.08000, 1.08050, 1.07950, 1.08020),
        (1.08055, 1.08070, 1.08050, 1.08060),
        (1.08060, 1.08090, 1.08052, 1.08080),  # gap of 0.00002 -- far under 0.10 ATR
    ]
    s = make(tail)
    assert not [f for f in detect_fvgs(s, cfg) if f.confirmed_index == WARM + 2]


def test_weekend_gap_fvgs_are_excluded_by_default(cfg, m15_quarter):
    """SPEC 12.5: an unfillable price region is not an imbalance anyone trades back into."""
    h4 = resample(m15_quarter, "H4", cfg)
    kept = detect_fvgs(h4, cfg)
    assert kept
    assert not any(f.spans_gap for f in kept)

    c, _ = load_config(overrides={"fvg": {"exclude_weekend_gaps": False}})
    with_gaps = detect_fvgs(h4, c)
    assert len(with_gaps) >= len(kept)


def test_fvg_membership_is_by_confirmation_bar(cfg):
    """SPEC 10.1 / 10.4: the gap's first bar may precede the leg.

    Requiring all three bars inside the leg would make an FVG impossible on any leg
    shorter than three bars, and SPEC 10.4's own two-bar example relies on the earlier
    bar.
    """
    s = make([BAR_A, PASS_B])
    fvgs = detect_fvgs(s, cfg)
    inleg = fvgs_in_leg(fvgs, WARM, WARM + 1, FvgDirection.BULLISH)
    assert inleg
    assert any(f.confirmed_index == WARM + 1 for f in inleg)
    # At least one of them starts before the leg -- that is the point.
    assert any(f.confirmed_index - 2 < WARM for f in inleg)


def test_fvg_detection_is_prefix_stable(cfg, m15_quarter):
    """SPEC 25.2.  A gap is knowable at bar n and never before."""
    h4 = resample(m15_quarter, "H4", cfg)
    full = detect_fvgs(h4, cfg)
    for frac in (0.4, 0.7):
        k = int(h4.n * frac)
        part = detect_fvgs(h4.head(k), cfg)
        assert [f.confirmed_index for f in part] == [
            f.confirmed_index for f in full if f.confirmed_index < k
        ]


def test_require_fvg_can_be_switched_off(cfg):
    c, _ = load_config(overrides={"disp": {"require_fvg": False}})
    s = make([BAR_A, PASS_B])
    atr = atr_ref(s, c.atr.period)
    r = evaluate_leg(s, WARM, WARM + 1, Direction.BULLISH, c, [], atr)
    assert r.confirmed
    assert DisplacementReason.NO_FVG not in r.reasons
