"""`BACKTEST_PROTOCOL.md` sections 6.3 and 6.4 -- the controls the project is judged against.

Section 10.1's go/no-go table has eleven rows and says which one decides the question:

> *"The last falsification row is the one that matters most and is the one most likely to
> fail. A strategy that beats a null model but not a sweep-only control has not
> demonstrated the thing it claims to demonstrate -- it has demonstrated that some part
> of it works."*

So this module builds the *some part of it* arms. Each is the full strategy with one link
of `liquidity -> sweep -> CHoCH -> displacement -> entry` removed, replaced or reordered,
and everything downstream -- stops, targets, the RR gate, sizing, exits, costs -- left
alone.

**A control is a different setup stream over the same prices, not a different engine.**
Four of the five substitute ``Market.setup_override`` and are otherwise run by exactly the
``run()`` that produces the headline result; the fifth (6.3) substitutes the *level* book
through ``analyse_sweeps(level_transform=...)`` and then re-derives sweeps and MSS
normally. Nothing here reimplements a rule. That is deliberate and it is the only property
that makes a control a control: an arm that re-stated the admission order, the merge
fixpoint or the fill discipline could differ from the baseline for a reason that is not
the one being tested, and no amount of care in this file would show it.

---

**What this module cannot do on synthetic data, and it is the whole point.**

Every arm here asks *"does this component contribute?"*. On ``bot/data/synthetic.py`` -- a
random walk with no participants and no liquidity -- the true contribution of every
component is **zero by construction**, including the real ones. So the correct result on
this fixture is that no arm separates from the baseline, and that outcome is worth
precisely nothing as evidence about the strategy. It is worth something as evidence about
the instrument: an arm that *did* separate here would be a bug.

``STATE.md`` section 8 already says this for every study in the project. It binds harder
here, because a falsification suite is the one place where "we found no difference" is the
publishable answer, and this fixture guarantees that answer regardless of the truth.

**Two things follow, and both are load-bearing:**

1. **The verdict is three-way** (``stats.verdict_for``), never "the CI spans zero, so they
   are the same". H3 and H4 are falsified by a *negative* -- "the shuffled version
   performs as well", "sweep-only matches the full model" -- and a wide interval around
   zero is absence of evidence, not evidence of absence. Only ``EQUIVALENT``, the interval
   sitting *inside* the declared margin, licenses "this component contributes nothing".
   This is the H5 lesson (D-010 section 2, ``STATE.md`` section 6 item 19) and it applies
   here with more force, because 6.3 explicitly invites the reader to *act* on a null:
   *"rebuilt as a mean-reversion model and the SMC framing dropped"*.

2. **The margin is declared, not derived, and it is not a free choice.**
   ``EQUIVALENCE_MARGIN_R`` is **0.10 R**, which is section 10.1's own expectancy
   threshold for trading this system live. A difference smaller than the number the
   project already committed to as the boundary of a tradable edge cannot be a difference
   that matters, and tying it to an existing pre-registered figure is what stops it being
   chosen after the fact (section 10.2).

---

**Three asymmetries between the arms that are real, unavoidable, and must be read with the
numbers rather than discovered later.**

* **Two arms have no sweep at all** (``choch_only``, ``random_time``), so their setups
  carry a placeholder ``SweepEvent``. Its ``penetration_atr`` is NaN and its
  ``level_tier`` is 0 -- outside the real 1-3 range -- so a liquidity breakdown over those
  arms is loudly wrong rather than quietly plausible. ``ControlSpec.has_liquidity`` says
  which arms may be broken down that way, and the report obeys it.

* **The leg origin is *searched* in the sweepless arms and *clamped* in the baseline.**
  D-009 section 11 (``STATE.md`` section 6 item 11) records that the real path never looks
  for the leg origin: it clamps to the sweep extreme even when that makes the leg fail.
  Without a sweep there is nothing to clamp to, so ``_leg_extreme`` takes the actual
  extreme inside ``disp.max_leg_bars``. **This favours the control**: a searched origin
  can only produce a leg at least as displaced as a clamped one. Any arm that beats the
  baseline on displacement terms should be read against that before it is read as a
  finding.

* **Reversing the order moves the stop anchor, and it cannot not.** In the real sequence
  the stop sits beyond the *first* event's extreme (the sweep) and entry is at the second
  (the CHoCH). Reversed, the sweep is the second event, so the stop sits beyond the event
  being entered on. There is no construction that reverses the order and holds both the
  trigger and the anchor fixed -- they are the same two events. ``reversed_order``
  therefore holds the SL/TP *models* constant, which is what 6.4 asks, and not the
  distance.

---

**On sample sizes: the engine does not apply SPEC 9.4, and every arm inherits that.**
Three stacked levels swept by one bar produce three sweeps, hence three MSS candidates
sharing a ``choch_bar``, hence three near-identical trades -- ``STATE.md`` section 6 item
17 records that Phase 9's *headline* numbers deduplicate per cluster, but ``Market.setups``
does not, and neither does ``run()``. The controls deliberately do not deduplicate either,
because a control that did would be compared against a baseline that did not.
``distinct_opportunities`` is reported for every arm so the inflation is visible and
visibly shared.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

import numpy as np

from bot.backtest.engine import Market
from bot.config.schema import AppConfig
from bot.core.bars import BarSeries, from_epoch_s
from bot.core.displacement import Direction, DisplacementResult
from bot.core.displacement import evaluate as evaluate_displacement
from bot.core.ids import object_id
from bot.core.liquidity import LevelSource, LiquidityLevel, Side
from bot.core.mss import MssEngine, Outcome, ReferenceMode, SetupCandidate
from bot.core.structure import breaks_level
from bot.core.sweeps import SweepEvent, SweepEventType

#: Section 10.1's own expectancy threshold for going live, reused as the equivalence
#: margin so that "no difference" means "smaller than the difference this project already
#: declared to be the boundary of a tradable edge".  Declared before any arm was run.
EQUIVALENCE_MARGIN_R = 0.10

#: Section 6.3: *"Run with 20 random seeds and report the distribution, not one draw."*
#: Applied to ``random_time`` as well, which is a draw from a distribution for the same
#: reason.
SEEDS = tuple(range(20))

#: How many times ``random_time`` will retry a bar it has already drawn before giving up
#: on that setup.  Small: a bucket with no room left is a bucket the baseline barely uses.
_MAX_REDRAWS = 32


# --------------------------------------------------------------------- descriptors


@dataclass(frozen=True)
class ControlSpec:
    """What an arm is, what it falsifies, and what may honestly be said about it."""

    name: str
    protocol: str
    tests: str
    seeded: bool
    #: False when the arm's setups carry a placeholder sweep, so no liquidity-source,
    #: tier or penetration breakdown over it is a measurement.
    has_liquidity: bool
    #: What a null verdict on this arm would license, in the protocol's own words.
    null_means: str


BASELINE = ControlSpec(
    "baseline", "-", "-", False, True,
    "The full sequence.  Every other row is read against this one.",
)

CONTROLS: tuple[ControlSpec, ...] = (
    ControlSpec(
        "shuffled_liquidity", "6.3", "H3", True, True,
        "Liquidity identification contributes nothing; rebuild as mean-reversion and "
        "drop the SMC framing.",
    ),
    ControlSpec(
        "sweep_only", "6.4", "H4", False, True,
        "The CHoCH requirement only reduces sample size.",
    ),
    ControlSpec(
        "choch_only", "6.4", "H4", False, False,
        "The sweep requirement only reduces sample size.",
    ),
    ControlSpec(
        "reversed_order", "6.4", "H4", False, True,
        "The sequence is not what works -- only its ingredients.",
    ),
    ControlSpec(
        "random_time", "6.4", "floor", True, False,
        "The floor: what this SL/TP geometry pays with no signal at all.  Not a "
        "falsification target -- the baseline must beat it for anything else to matter.",
    ),
)

BY_NAME = {c.name: c for c in (BASELINE, *CONTROLS)}


# ------------------------------------------------------------------ small builders


def with_setups(market: Market, setups: Sequence[SetupCandidate]) -> Market:
    """The same market, viewed through a different setup stream."""
    return dataclasses.replace(market, setup_override=tuple(setups))


def _leg_extreme(
    series: BarSeries, b: int, direction: Direction, cfg: AppConfig
) -> tuple[int, float]:
    """The extreme of the leg into bar ``b``, within ``disp.max_leg_bars``.

    The sweepless arms' stand-in for a sweep extreme.  Bounded by the same window
    ``leg_origin`` clamps to, so ``evaluate_displacement`` measures the identical span it
    would have measured had a sweep put its extreme here.  See the module docstring on
    why this favours the control.
    """
    lo = max(0, b - cfg.disp.max_leg_bars + 1)
    if direction is Direction.BULLISH:
        seg = series.low[lo : b + 1]
        k = int(np.argmin(seg))
    else:
        seg = series.high[lo : b + 1]
        k = int(np.argmax(seg))
    return lo + k, float(seg[k])


def _trigger_reference(series: BarSeries, b: int) -> float:
    """The reference price for an arm that has no CHoCH reference: the trigger close.

    ``order_blocks.propose`` needs one for two SPEC 13.4/13.7 constraints -- how far the
    zone may sit from the level whose break defined the setup, and the rule that entering
    *beyond* that level is not a retracement.  Three arms here have no such level.

    The two obvious substitutes are both wrong.  ``None`` crashes: ``propose`` annotates
    the parameter ``float`` while the engine hands it ``SetupCandidate.reference_price``,
    which is ``float | None`` -- latent today only because ``Market.setups`` filters on
    ``is_choch`` and a CHoCH implies a reference was found (``mss.py``: a candidate with
    no reference never gets a ``choch_bar``).  The **swept level's** price is worse than
    a crash: for a bullish setup it sits *below* the market, so the "beyond" test would
    reject essentially every order block and model D would silently read as unavailable
    rather than as tested.

    The trigger bar's close keeps the constraint's meaning -- *do not enter beyond the
    price that declared the setup* -- with the geometry the baseline has, where the
    reference is a level just behind that same close.
    """
    return float(series.close[b])


def placeholder_sweep(
    *,
    symbol: str,
    timeframe: str,
    direction: Direction,
    extreme_bar: int,
    extreme_price: float,
    trigger_bar: int,
    at: datetime,
    control: str,
) -> SweepEvent:
    """A sweep-shaped record for an arm that has no sweep.

    Every field that would be a *measurement* is NaN or an out-of-range sentinel, so a
    breakdown that should not have been taken over this arm produces something visibly
    broken rather than a plausible number.  ``side`` and the two extreme fields are real:
    they are geometry the entry and stop models need, not claims about liquidity.
    """
    side = Side.SELL_SIDE if direction is Direction.BULLISH else Side.BUY_SIDE
    source = (
        LevelSource.SWING_LOW if direction is Direction.BULLISH else LevelSource.SWING_HIGH
    )
    return SweepEvent(
        # ``trigger_bar`` is in the key and nowhere else.  Without it two setups whose
        # legs happen to share an extreme collide on ``id``, and since ``Trade.setup_id``
        # *is* this id the damage is silent and in two places: ``arm_from`` would credit
        # one setup's R and score the other 0.0, and ``run``'s ``live`` dict would lose a
        # position to an overwritten key.
        id=object_id(
            "CTRL", symbol=symbol, timeframe=timeframe, at=at,
            key=(control, direction.value, extreme_bar, extreme_price, trigger_bar),
        ),
        symbol=symbol,
        timeframe=timeframe,
        type=SweepEventType.CONFIRMED,
        reason=None,
        side=side,
        level_id="",
        level_source=source,
        level_tier=0,  # outside the real 1-3 range, on purpose
        level_price=float("nan"),
        level_strength=0,
        trigger_bar=extreme_bar,
        confirm_bar=extreme_bar,
        at=at,
        sweep_extreme=extreme_price,
        sweep_extreme_bar=extreme_bar,
        penetration=float("nan"),
        penetration_atr=float("nan"),
        wick_ratio=float("nan"),
        close_position=float("nan"),
        confirmation_bars=0,
        single_bar_sweep=False,
    )


def make_setup(
    *,
    symbol: str,
    timeframe: str,
    direction: Direction,
    sweep: SweepEvent,
    extreme_bar: int,
    trigger_bar: int,
    at: datetime,
    displacement: DisplacementResult,
    control: str,
    reference_price: float | None = None,
) -> SetupCandidate:
    """A setup the engine will trade, triggered at ``trigger_bar``.

    ``outcome`` is ``MSS_CONFIRMED`` because that is what the field means to everything
    downstream -- *this candidate is tradable* -- not a claim that an MSS occurred.  The
    arm it came from is what says what it is, and no arm's output is ever pooled with
    another's.
    """
    return SetupCandidate(
        id=object_id(
            "SETUP", symbol=symbol, timeframe=timeframe, at=at,
            key=(control, direction.value, trigger_bar, sweep.id),
        ),
        symbol=symbol,
        timeframe=timeframe,
        direction=direction,
        reference_mode=ReferenceMode.MAJOR,
        sweep=sweep,
        sweep_extreme_bar=extreme_bar,
        window_first_bar=trigger_bar,
        window_last_bar=trigger_bar,
        outcome=Outcome.MSS_CONFIRMED,
        reference_price=reference_price,
        choch_bar=trigger_bar,
        choch_at=at,
        bars_sweep_to_choch=trigger_bar - extreme_bar,
        displacement=displacement,
    )


def _confirmed_sweeps(market: Market) -> list[SweepEvent]:
    """Every confirmed sweep, from the MSS candidate stream that holds one each.

    Deliberately *not* deduplicated per SPEC 9.4 -- see the module docstring.
    """
    return [c.sweep for c in market.mss.candidates]


def distinct_opportunities(setups: Sequence[SetupCandidate]) -> int:
    """SPEC 9.4's count: setups sharing a trigger bar and direction are one opportunity."""
    return len({(s.choch_bar, s.direction) for s in setups})


# ------------------------------------------------------------------ 6.4 controls


def sweep_only(cfg: AppConfig, market: Market) -> Market:
    """*"Enter on sweep confirmation, no CHoCH requirement. Same SL/TP."*  Tests H4.

    The sweep is real and so is its extreme; what is removed is everything between the
    sweep and the entry.  Displacement is **measured over the sweep-to-confirmation leg
    and recorded, but not required** -- requiring it would leave the CHoCH as the only
    difference from the baseline, and this arm exists to remove the whole waiting step.
    """
    h4, out = market.h4, []
    for sw in _confirmed_sweeps(market):
        b = sw.confirm_bar
        if b >= h4.n:
            continue
        direction = Direction.BULLISH if sw.side is Side.SELL_SIDE else Direction.BEARISH
        disp = evaluate_displacement(
            h4, sw.sweep_extreme_bar, b, direction, cfg, market.fvgs, market.atr
        )
        out.append(make_setup(
            symbol=market.symbol, timeframe=h4.timeframe, direction=direction, sweep=sw,
            extreme_bar=sw.sweep_extreme_bar, trigger_bar=b,
            at=from_epoch_s(h4.close_time[b]), displacement=disp, control="sweep_only",
            reference_price=_trigger_reference(h4, b),
        ))
    return with_setups(market, out)


def choch_only_setups(cfg: AppConfig, market: Market) -> list[SetupCandidate]:
    """*"Enter on every MSS-shaped structure break with displacement, no prior sweep."*

    Tests H4.  **The hard part of this control is what counts as a CHoCH**, and the
    obvious answer is the wrong one.

    ``structure.py`` emits its own ``CHOCH`` events, and reaching for those is the first
    thing anyone will try.  They are a *different and much stricter* thing than the break
    the baseline trades: a structure CHoCH is a trend flip through the **protected** level,
    while SPEC 11.2's CHoCH is a break of the **last unbroken swing** inside the sweep
    window.  On the 2024 fixture that is 26 events against the baseline's 82 setups from
    40 distinct opportunities -- so an arm built that way would differ from the baseline
    in the definition of the thing being tested, and its inevitable null would be read as
    "the sweep requirement only reduces sample size" when what it measured was a stricter
    break rule.

    So this reuses ``MssEngine._major_reference`` **itself**, private and all.  That is
    deliberate: SPEC 11.2 requires the reference break to use one test "and no other", and
    ``breaks_level``'s own docstring records that two copies of it in two modules is how
    that requirement quietly stops holding.  The same argument applies one level up to the
    *selection* of what gets broken.  What is removed here is the sweep and nothing else --
    the reference anchor becomes the leg extreme (asymmetry two in the module docstring),
    and the two clauses that are defined only relative to a sweep (new extreme, opposing
    sweep) cannot apply.

    A reference fires **once**.  Without a sweep window there is nothing to stop the next
    three bars each re-breaking the same swing and reporting three setups where the
    baseline would see one.
    """
    h4 = market.h4
    atr = market.atr
    engine = MssEngine(
        h4, cfg, [], swings=market.structure.swings, fvgs=market.fvgs, atr=atr
    )
    fired: set[tuple[str, str]] = set()
    out: list[SetupCandidate] = []

    for b in range(cfg.disp.max_leg_bars, h4.n):
        a = float(atr[b])
        if not np.isfinite(a) or a <= 0:
            continue
        for direction in (Direction.BULLISH, Direction.BEARISH):
            a_bar, a_price = _leg_extreme(h4, b, direction, cfg)
            ref = engine._major_reference(a_bar, direction)
            if ref is None:
                continue
            key = (direction.value, ref.id)
            if key in fired:
                continue
            if abs(ref.price - a_price) > cfg.choch.max_reference_distance_atr * a:
                continue  # SPEC 11.1's REFERENCE_TOO_FAR, applied as the baseline does
            if not breaks_level(
                h4, b, ref.price, up=direction is Direction.BULLISH, cfg=cfg, atr_value=a
            ):
                continue
            disp = evaluate_displacement(h4, a_bar, b, direction, cfg, market.fvgs, atr)
            if not disp.confirmed:
                continue
            fired.add(key)
            at = from_epoch_s(h4.close_time[b])
            out.append(make_setup(
                symbol=market.symbol, timeframe=h4.timeframe, direction=direction,
                sweep=placeholder_sweep(
                    symbol=market.symbol, timeframe=h4.timeframe, direction=direction,
                    extreme_bar=a_bar, extreme_price=a_price, trigger_bar=b, at=at,
                    control="choch_only",
                ),
                extreme_bar=a_bar, trigger_bar=b, at=at, displacement=disp,
                control="choch_only", reference_price=float(ref.price),
            ))
    return out


def choch_only(cfg: AppConfig, market: Market) -> Market:
    """``choch_only_setups`` as a market.  See it for the construction."""
    return with_setups(market, choch_only_setups(cfg, market))


def reversed_order(cfg: AppConfig, market: Market) -> Market:
    """*"Require CHoCH then a sweep (a sequence that should be meaningless)."*  Tests H4.

    Built as ``sweep_only`` filtered to sweeps preceded by a displaced CHoCH of the same
    direction inside the same window the baseline allows between its two events, so the
    two arms differ in the order of the pair and in nothing else that could be held fixed.
    Entry is on the second event, as in the baseline; the stop anchor moves with it, which
    is unavoidable (module docstring, asymmetry three).
    """
    h4 = market.h4
    lo_gap = cfg.choch.min_bars_after_sweep
    hi_gap = cfg.choch.max_bars_after_sweep

    prior: dict[Direction, list[int]] = {Direction.BULLISH: [], Direction.BEARISH: []}
    for s in choch_only_setups(cfg, market):
        prior[s.direction].append(s.choch_bar)

    out = []
    for sw in _confirmed_sweeps(market):
        b = sw.confirm_bar
        if b >= h4.n:
            continue
        direction = Direction.BULLISH if sw.side is Side.SELL_SIDE else Direction.BEARISH
        # A CHoCH of this direction inside [b - hi_gap, b - lo_gap].
        if not any(b - hi_gap <= cb <= b - lo_gap for cb in prior[direction]):
            continue
        disp = evaluate_displacement(
            h4, sw.sweep_extreme_bar, b, direction, cfg, market.fvgs, market.atr
        )
        out.append(make_setup(
            symbol=market.symbol, timeframe=h4.timeframe, direction=direction, sweep=sw,
            extreme_bar=sw.sweep_extreme_bar, trigger_bar=b,
            at=from_epoch_s(h4.close_time[b]), displacement=disp,
            control="reversed_order", reference_price=_trigger_reference(h4, b),
        ))
    return with_setups(market, out)


def random_time(cfg: AppConfig, market: Market, seed: int) -> Market:
    """*"Enter at random times matched to the real trade distribution over session and
    volatility, with the same SL/TP geometry."*  The floor.

    Matching is on **(session, ATR tercile)** and the direction mix, one draw per baseline
    setup, so the arm differs from the baseline in *when* it fires and in nothing about
    how often, in what conditions, or which way.  A floor drawn from all bars regardless
    of session would be beaten by the baseline for the trivial reason that the baseline
    does not trade the Asian range.
    """
    h4 = market.h4
    rng = np.random.default_rng(seed)
    base = market.setups
    if not base:
        return with_setups(market, [])

    atr = market.atr
    finite = np.isfinite(atr) & (atr > 0)
    if finite.any():
        lo, hi = (float(x) for x in np.quantile(atr[finite], [1 / 3, 2 / 3]))
    else:
        lo = hi = 0.0

    def bucket(i: int) -> tuple[str, int]:
        a = atr[i]
        t = 0 if not np.isfinite(a) else int(a > lo) + int(a > hi)
        return market.sessions_by_bar.get(i, "OTHER"), t

    # Only bars that could carry a setup at all: a leg behind them and a forward path.
    pool: dict[tuple[str, int], list[int]] = {}
    for i in range(cfg.disp.max_leg_bars, h4.n - 1):
        if finite[i]:
            pool.setdefault(bucket(i), []).append(i)

    # Drawn WITHOUT replacement per (bar, direction).  With replacement, two draws
    # landing on the same bar are the same setup twice: identical ids, hence an
    # overwritten key in ``run``'s ``live`` dict and a setup silently scored 0.0 in
    # ``arm_from``.  Pools run to hundreds of bars against ~80 draws, so refusing a
    # repeat costs almost nothing and removes the failure entirely.
    used: set[tuple[int, Direction]] = set()
    out = []
    for s in base:
        cand = pool.get(bucket(s.choch_bar))
        if not cand:
            continue
        direction = s.direction
        b = -1
        for _ in range(_MAX_REDRAWS):
            pick = int(cand[rng.integers(0, len(cand))])
            if (pick, direction) not in used:
                b = pick
                break
        if b < 0:
            continue
        used.add((b, direction))
        a_bar, a_price = _leg_extreme(h4, b, direction, cfg)
        disp = evaluate_displacement(h4, a_bar, b, direction, cfg, market.fvgs, market.atr)
        at = from_epoch_s(h4.close_time[b])
        out.append(make_setup(
            symbol=market.symbol, timeframe=h4.timeframe, direction=direction,
            sweep=placeholder_sweep(
                symbol=market.symbol, timeframe=h4.timeframe, direction=direction,
                extreme_bar=a_bar, extreme_price=a_price, trigger_bar=b, at=at,
                control=f"random_time:{seed}",
            ),
            extreme_bar=a_bar, trigger_bar=b, at=at, displacement=disp,
            control=f"random_time:{seed}", reference_price=_trigger_reference(h4, b),
        ))
    return with_setups(market, out)


# ------------------------------------------------------------------ 6.3 the shuffle


def shuffle_levels(
    candidates: Sequence[LiquidityLevel],
    h4: BarSeries,
    atr: np.ndarray,
    rng: np.random.Generator,
) -> list[LiquidityLevel]:
    """Section 6.3: *"synthetic levels drawn to match the real distribution of (distance
    from price, age, count per day) but placed at random prices."*

    **Everything about a level except where it is, is preserved.**  Same ``confirmed_at``
    and ``formed_at`` -- which is what holds age and count-per-day fixed exactly rather
    than in distribution -- same side, source, tier, timeframe and strength, so ranking,
    expiry and the tier map behave identically.  Only ``price`` moves.

    The new price is drawn from the empirical distribution of **signed distance in ATR
    from the close at admission, within the level's own side**.  Signed and per-side, both
    deliberately: a buy-side level is usually above price and a sell-side one below, and a
    control that lost that would not be testing liquidity identification, it would be
    testing whether levels are on the correct side of the market -- a much easier
    question, and one the real book would win for a trivial reason.

    The id is recomputed because price is part of a level's natural key (D-015 section 1).
    Leaving it would give two different levels the same id across the two arms, which is
    the exact failure that scheme exists to prevent.
    """
    n = h4.n
    close_t = h4.close_time

    def ref_of(lvl: LiquidityLevel) -> tuple[int, float, float]:
        i = int(np.searchsorted(close_t, lvl.confirmed_at.timestamp(), side="left"))
        i = min(max(i, 0), n - 1)
        a = float(atr[i]) if np.isfinite(atr[i]) else float("nan")
        return i, float(h4.close[i]), a

    # Pass one: the empirical signed-distance pool, per side.
    pool: dict[Side, list[float]] = {Side.BUY_SIDE: [], Side.SELL_SIDE: []}
    refs: dict[int, tuple[int, float, float]] = {}
    for k, lvl in enumerate(candidates):
        i, ref, a = ref_of(lvl)
        refs[k] = (i, ref, a)
        if np.isfinite(a) and a > 0:
            pool[lvl.side].append((lvl.price - ref) / a)

    arrays = {s: np.asarray(v, dtype=float) for s, v in pool.items()}

    # Pass two: redraw each price from its side's pool at its own bar's ATR.
    out: list[LiquidityLevel] = []
    for k, lvl in enumerate(candidates):
        _, ref, a = refs[k]
        draw = arrays[lvl.side]
        if draw.size == 0 or not np.isfinite(a) or a <= 0:
            # Nothing to draw from, or no scale to draw at.  Kept unmoved rather than
            # dropped: the count per day is part of what the control holds fixed.
            out.append(lvl)
            continue
        d = float(draw[rng.integers(0, draw.size)])
        # ``source_ids`` is copied rather than shared: the merge at liquidity.py:713
        # rebinds rather than mutates today, so sharing is safe *by accident*, and a
        # control that corrupted the arm it is compared against would be invisible.
        new = dataclasses.replace(
            lvl, price=float(ref + d * a), source_ids=list(lvl.source_ids)
        )
        new.id = object_id(
            "LV", symbol=lvl.symbol, timeframe=lvl.timeframe, at=lvl.confirmed_at,
            key=(lvl.source.value, lvl.side.value, new.price, lvl.formed_at),
        )
        out.append(new)
    return out


def shuffled_level_transform(h4: BarSeries, atr: np.ndarray, seed: int):
    """The callable ``build_market(level_transform=...)`` takes, bound to one seed."""
    rng = np.random.default_rng(seed)

    def transform(candidates: Sequence[LiquidityLevel]) -> list[LiquidityLevel]:
        return shuffle_levels(candidates, h4, atr, rng)

    return transform


def build_shuffled_market(cfg: AppConfig, m1: BarSeries, baseline: Market, seed: int) -> Market:
    """Section 6.3's arm: the whole pipeline again, over a level book placed at random.

    ``baseline`` supplies only the H4 series and ATR the shuffle measures distances
    against; the returned market is built from ``m1`` by the ordinary ``build_market``,
    so sweeps, structure, FVGs and MSS are all re-derived by the real engines from the
    shuffled book rather than patched afterwards.
    """
    from bot.backtest.engine import build_market

    return build_market(
        cfg, m1,
        level_transform=shuffled_level_transform(baseline.h4, baseline.atr, seed),
    )


# ------------------------------------------------------------------- comparison


@dataclass(frozen=True)
class Arm:
    """One control's outcome, in the units the comparison is made in.

    ``per_setup`` is the array everything statistical is computed from: one entry per
    setup, holding the trade's net R where the order filled and **0.0 where it did not**.
    That is BACKTEST_PROTOCOL section 4.4's per-setup expectancy with a shared
    denominator, and it is the only unit in which these arms are comparable at all --
    ``sweep_only`` offers 751 setups against the baseline's 82, and D-013 section 5 (a
    model that fills on the best-looking third of setups shows a better win rate and a
    worse total return) is the same trap one level up.  An unfilled order earns nothing
    and costs nothing, which is what the 0.0 says.
    """

    spec: ControlSpec
    seed: int | None
    n_setups: int
    distinct: int
    armed: int
    filled: int
    per_setup: np.ndarray
    per_trade: np.ndarray
    #: The same shared-denominator array in **cost-free R** (``Trade.r_multiple``), which
    #: is pure exit-versus-entry geometry over the planned risk.  Carried because R is a
    #: ratio and the denominator differs between arms -- see ``Comparison.gross_delta``.
    per_setup_gross: np.ndarray = dataclasses.field(
        default_factory=lambda: np.zeros(0)
    )
    #: Median stop in ATR, the quantity that makes the two R's diverge.
    median_sl_atr: float = float("nan")

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def expectancy_per_setup(self) -> float:
        return float(self.per_setup.mean()) if self.per_setup.size else float("nan")

    @property
    def expectancy_per_setup_gross(self) -> float:
        return (
            float(self.per_setup_gross.mean())
            if self.per_setup_gross.size
            else float("nan")
        )

    @property
    def cost_r_per_setup(self) -> float:
        """What costs take out of this arm's per-setup expectancy."""
        return self.expectancy_per_setup_gross - self.expectancy_per_setup

    @property
    def expectancy_per_trade(self) -> float:
        return float(self.per_trade.mean()) if self.per_trade.size else float("nan")

    @property
    def fill_rate(self) -> float:
        return self.filled / self.armed if self.armed else float("nan")

    @property
    def n_eff(self) -> float:
        from bot.research.stats import effective_sample_size

        return effective_sample_size(self.per_setup) if self.per_setup.size else 0.0


def arm_from(spec: ControlSpec, market: Market, result, seed: int | None = None) -> Arm:
    """Fold one ``run()`` over one arm's market into the comparison unit."""
    setups = market.setups
    net = {t.setup_id: t.r_net for t in result.trades}
    gross = {t.setup_id: t.r_multiple for t in result.trades}
    sl = np.array([t.sl_distance_atr for t in result.trades], dtype=float)
    return Arm(
        spec=spec,
        seed=seed,
        n_setups=len(setups),
        distinct=distinct_opportunities(setups),
        armed=int(result.funnel.get("orders_armed", 0)),
        filled=int(result.funnel.get("orders_filled", 0)),
        per_setup=np.array([net.get(s.sweep.id, 0.0) for s in setups], dtype=float),
        per_trade=np.array([t.r_net for t in result.trades], dtype=float),
        per_setup_gross=np.array(
            [gross.get(s.sweep.id, 0.0) for s in setups], dtype=float
        ),
        median_sl_atr=float(np.median(sl)) if sl.size else float("nan"),
    )


@dataclass(frozen=True)
class Comparison:
    """Baseline minus control, with the three-way reading and its power arithmetic.

    **Reported in two currencies, and the difference between them is this study's main
    finding.**  ``delta`` is in net R, which is what section 10.1's row means by
    expectancy.  ``gross_delta`` is the same comparison in cost-free R.

    R is a *ratio*, and the arms do not share its denominator: an arm entering at the
    sweep confirmation has a stop about 1 ATR wide where the baseline's is about 2.3, so
    a fixed spread and commission cost it more than twice as much **per R**.  That is a
    difference in geometry, not in signal, and in net R it is indistinguishable from one.
    On the synthetic fixture it is the *entire* gap between the baseline and
    ``sweep_only``: gross deltas sit at zero, as they must on a random walk, while the net
    delta clears section 10.1's bar.  See the report's finding 1.
    """

    control: str
    tests: str
    n_base: int
    n_ctrl: int
    base_e: float
    ctrl_e: float
    delta: float
    ci_low: float
    ci_high: float
    verdict: str
    mde: float
    need_n: float
    p_value: float
    base_e_gross: float = float("nan")
    ctrl_e_gross: float = float("nan")
    gross_delta: float = float("nan")
    gross_ci_low: float = float("nan")
    gross_ci_high: float = float("nan")
    gross_verdict: str = ""
    base_sl_atr: float = float("nan")
    ctrl_sl_atr: float = float("nan")

    @property
    def baseline_beats(self) -> bool:
        """Section 10.1's actual requirement: *"beats every control by a margin whose CI
        excludes zero"*.  Not merely a positive delta."""
        return bool(np.isfinite(self.ci_low) and self.ci_low > 0.0)

    @property
    def baseline_beats_gross(self) -> bool:
        """The same test on cost-free R -- the one that is about signal."""
        return bool(np.isfinite(self.gross_ci_low) and self.gross_ci_low > 0.0)

    @property
    def cost_explains_it(self) -> bool:
        """True when the arm clears section 10.1's bar in net R and not in gross R.

        The signature of a comparison won on stop width rather than on signal.
        """
        return self.baseline_beats and not self.baseline_beats_gross


def compare(
    baseline: Arm,
    control: Arm,
    *,
    rng: np.random.Generator,
    n_boot: int = 10_000,
    n_perm: int = 2_000,
    margin: float = EQUIVALENCE_MARGIN_R,
) -> Comparison:
    """Baseline vs one control on per-setup R.

    The delta is signed **baseline minus control**, so section 10.1's row reads as
    "positive, with a CI excluding zero" for every control -- including ``random_time``,
    which is the floor rather than a falsification target and where a *failure* to beat it
    is the more serious result.
    """
    from bot.research import stats

    a, b = baseline.per_setup, control.per_setup
    lo, hi = stats.bootstrap_diff_ci(a, b, n_boot, rng)
    ga, gb = baseline.per_setup_gross, control.per_setup_gross
    glo, ghi = stats.bootstrap_diff_ci(ga, gb, n_boot, rng)
    return Comparison(
        control=control.name,
        tests=control.spec.tests,
        n_base=len(a),
        n_ctrl=len(b),
        base_e=baseline.expectancy_per_setup,
        ctrl_e=control.expectancy_per_setup,
        delta=float(a.mean() - b.mean()) if a.size and b.size else float("nan"),
        ci_low=lo,
        ci_high=hi,
        verdict=stats.verdict_for(lo, hi, margin, len(a), len(b)).value,
        mde=stats.minimum_detectable_effect(a, b),
        need_n=stats.required_n(a, b, margin),
        p_value=stats.permutation_p(a, b, n_perm, rng),
        base_e_gross=baseline.expectancy_per_setup_gross,
        ctrl_e_gross=control.expectancy_per_setup_gross,
        gross_delta=(
            float(ga.mean() - gb.mean()) if ga.size and gb.size else float("nan")
        ),
        gross_ci_low=glo,
        gross_ci_high=ghi,
        gross_verdict=stats.verdict_for(glo, ghi, margin, len(ga), len(gb)).value,
        base_sl_atr=baseline.median_sl_atr,
        ctrl_sl_atr=control.median_sl_atr,
    )


def synthetic_arm(per_setup: np.ndarray, name: str = "synthetic") -> Arm:
    """An arm made from a raw per-setup array, for exercising the comparison layer.

    The suite's positive control needs a difference that is known to be real, which no
    arm built from this fixture can supply -- on a random walk every true effect is zero
    by construction, so a comparison that failed to separate two genuinely different
    populations would look exactly like a correct null.
    """
    x = np.asarray(per_setup, dtype=float)
    spec = ControlSpec(name, "-", "-", False, False, "Not a control.")
    return Arm(
        spec=spec, seed=None, n_setups=x.size, distinct=x.size,
        armed=x.size, filled=int(np.count_nonzero(x)),
        per_setup=x, per_trade=x[x != 0.0], per_setup_gross=x,
    )


def pooled(spec: ControlSpec, arms: Sequence[Arm]) -> Arm:
    """Several seeds of one control as a single arm.

    **Its ``n`` is not an independent sample size.**  Twenty seeds over the same price
    series are twenty correlated draws, so a CI computed from the pooled array is
    optimistic; the across-seed spread reported beside it is the honest uncertainty.
    Pooled anyway because section 6.3 asks for the control's expectancy, and one draw is
    a worse estimate of it than twenty.
    """
    return Arm(
        spec=spec,
        seed=None,
        n_setups=sum(a.n_setups for a in arms),
        distinct=sum(a.distinct for a in arms),
        armed=sum(a.armed for a in arms),
        filled=sum(a.filled for a in arms),
        per_setup=np.concatenate([a.per_setup for a in arms]) if arms else np.zeros(0),
        per_trade=np.concatenate([a.per_trade for a in arms]) if arms else np.zeros(0),
        per_setup_gross=(
            np.concatenate([a.per_setup_gross for a in arms]) if arms else np.zeros(0)
        ),
        median_sl_atr=(
            float(np.median([a.median_sl_atr for a in arms
                             if np.isfinite(a.median_sl_atr)]))
            if any(np.isfinite(a.median_sl_atr) for a in arms) else float("nan")
        ),
    )
