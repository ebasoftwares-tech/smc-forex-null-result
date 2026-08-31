# Phase 10 Gate Report

**FVG lifecycle (SPEC 12.2), selection (12.3), and the standalone edge test (12.6).**

Generated 2026-08-30T14:04:11+00:00

- `config_hash` `7f393e8ced4bf193f993f056380bffff2826fbeb71ba2a583d99e54f19b18c49`
- `dataset_hash` `2a2bb0293052ae31bd2be73cfd53df25f6032f4db94da906543889e447d03ed9`
- Data: **real bars** -- 10 symbols, 2019-2022 (40 symbol-years), H4, source `histdata`, `bid` side, tzdata `2026.3`
- Split (PRE_REGISTRATION 4.1, Amendment 1): in-sample **[2019, 2020, 2021, 2022]**, out-of-sample [2023, 2024], holdout [2025]
- **9,446 gaps, 7,800 first-touch events**
- Equivalence margin: **+/-0.25 ATR**, declared before any result was read

## Gate checks

| Check | Result | Detail |
|---|---|---|
| Test suite green | PASS | 662 passed in 107.17s (0:01:47) |
| Standalone edge test run (gate) | PASS | 4 horizons, 7,800 touch events vs 7,800 matched controls |
| Positive control detects an injected effect | PASS | 0.5 ATR shift -> DIFFERENT |
| Null calibration lands near alpha | PASS | 5.6% over 3,000 shuffles (1.5 sigma from alpha 5%) |
| Lifecycle reaches MITIGATED and EXPIRED | PASS | {'EXPIRED': 1824, 'INVALIDATED': 19, 'MITIGATED': 7592, 'PARTIAL': 2, 'UNMITIGATED': 9} |
| INVALIDATED measured on real bars (SPEC 12.5) | PASS | 19 gaps ended INVALIDATED |
| Fill curve is monotone | PASS | 23.1% at 1 bar -> 80.4% at 30 |
| Both directions populated | PASS | {'BULLISH': 4772, 'BEARISH': 4674} |
| Mitigation-mode ablation reported | PASS | SPEC 12.6 |
| Verdict distinguishes 'no edge' from 'no power' | PASS | EQUIVALENT |

## The gate: standalone edge test — EQUIVALENT

> NO EDGE -- touching an unmitigated FVG is indistinguishable from a matched control, within the declared margin

SPEC 12.6 asks for the return after touching an unmitigated FVG in the direction
of the gap, against a matched control. The point is the **independence**: if price
returning into a gap carries no directional information, then `disp.require_fvg`
is filtering setups on a coin flip, and knowing that before the entry engine is
built localises the failure to the concept rather than the machinery around it.

Controls are drawn from the same (session slot, ATR tercile) cell with no touch,
and carry the direction of the gap they match — a signed return against a signed
baseline, not against zero.

| h | n touch | n control | touch mean | control mean | diff | 95% CI | p (BH) | MDE | Verdict |
|---:|---:|---:|---:|---:|---:|---|---:|---:|---|
| 1 | 7,800 | 7,800 | +0.0094 | -0.0017 | +0.0111 | [-0.0126, +0.0347] | 0.485 | 0.034 | **EQUIVALENT** |
| 3 | 7,796 | 7,797 | +0.0390 | +0.0083 | +0.0307 | [-0.0080, +0.0685] | 0.450 | 0.055 | **EQUIVALENT** |
| 6 | 7,793 | 7,796 | +0.0511 | +0.0170 | +0.0342 | [-0.0194, +0.0885] | 0.450 | 0.079 | **EQUIVALENT** |
| 12 | 7,788 | 7,795 | +0.0346 | +0.0095 | +0.0251 | [-0.0535, +0.1048] | 0.533 | 0.112 | **EQUIVALENT** |

All figures in ATR units. Benjamini-Hochberg across the four horizons at q = 0.10:
four horizons on one population is four chances to find something, and Phase 7 is
this project's standing evidence that those chances get taken.

**At +1, +3, +6, +12 the study resolves the margin and finds no edge.** Those intervals
sit entirely inside +/-0.25 ATR, which is the only result that licenses the
word "no" — an interval merely containing zero would be absence of evidence.

**This is a null result on real market data, and it is the first one in the
project that means anything.** Every earlier study reported a null on a random
walk, where the true effect is zero by construction and a null is the fixture
speaking. Here the fixture is 10 real symbols over 40 symbol-years,
the intervals resolve the declared margin at every horizon, and the controls
below show the instrument would have found an effect of 0.05 ATR if one existed.

What it licenses is narrow and worth stating exactly: **touching an unmitigated
FVG does not move the next 12 H4 bars by as much as
0.25 ATR, in the gap's own direction, against a control
matched on session slot and ATR tercile.** It does not say an FVG is worthless
inside the strategy: `disp.require_fvg` uses a gap as evidence that displacement
happened, and entry model C uses one as a *location* to bid at. Neither claim is
the claim tested here, and both are Phase 12's to answer.

It does mean the concept carries no standalone directional information, which is
the thing SPEC 12.6 wrote this test to find out.

## Power, and how this compares to H5

Measured on the in-sample split. The MDE column above is what makes the verdict
readable: an EQUIVALENT result is only worth anything if the study could have
seen an effect, and at h=1 it resolves to 0.034 ATR against a 0.25 ATR margin.

| h | touches needed for +/-0.25 ATR | dev set has 2,398 | universe has 7,800 |
|---:|---:|:--:|:--:|
| 1 | 144 | yes | yes |
| 3 | 374 | yes | yes |
| 6 | 771 | yes | yes |
| 12 | 1,557 | yes | yes |

At 195 touch events per symbol-year, the in-sample
period **contains 7,800** across the universe and
**2,398** on the development set. Those are counts, not projections:
all 40 symbol-years the gate names have been read.

**This is the sharpest contrast in the project, and real bars widened it.**
D-020 measured **368 MSS events** over the same in-sample
period — the population H5 and the whole sweep-to-MSS chain have to work with.
This study has about **21x** that, because
every gap counts rather than only those surviving the funnel.

That asymmetry is worth holding onto when reading D-020's failed gate: the
components of this strategy are **not** equally measurable, and the two that
are hardest to measure are the two the design rests on.

## Lifecycle (SPEC 12.2)

| Terminal status | Gaps | Share |
|---|---:|---:|
| `EXPIRED` | 1,824 | 19.3% |
| `INVALIDATED` | 19 | 0.2% |
| `MITIGATED` | 7,592 | 80.4% |
| `PARTIAL` | 2 | 0.0% |
| `UNMITIGATED` | 9 | 0.1% |

**`INVALIDATED` fires 19 times on real bars, and
the synthetic report predicted exactly that.** It requires a true price
discontinuity — SPEC 12.5's gap-over — which a continuous random walk cannot
produce and a real weekend or holiday can. The transition was covered by one
constructed test precisely because the fixture could not reach it; it is now
exercised at scale, and the D-011 §2 touch-rule fix is what makes it reachable
at all — under SPEC 12.2's one-sided rule every one of these would have been
counted as a fill.

That transition was **unreachable entirely** until Phase 10 generalised the touch
rule — see "Two spec corrections" below.

Median bars from confirmation to mitigation: **3.0** (n = 7,592).

### Fill-rate curve (SPEC 12.6)

| Within k bars | Mitigated |
|---:|---:|
| 1 | 23.1% |
| 2 | 34.8% |
| 3 | 42.6% |
| 5 | 55.4% |
| 10 | 66.2% |
| 20 | 76.0% |
| 30 | 80.4% |

Gaps whose k-bar window runs past the end of the series are excluded from that
horizon rather than counted unfilled — right-censoring them would make the curve
sag at the long end purely because of where the data stops.

**The synthetic report predicted this curve would fall on real data, and it was
half right — in the half that matters less.**

| | fixture | real bars | |
|---|---:|---:|---|
| fill within 1 bar | 29.8% | 23.1% | fell |
| fill within 30 bars | 78.2% | 80.4% | rose |
| median bars to mitigation | 2 | 3.0 | |

The reasoning behind the prediction was that *"a random walk returns to a local
extreme readily; a trending market may not"*. Real bars fill **more slowly early**
— the 1-bar rate falls by 22% and the median
moves from 2 bars to 3.0 — which is the predicted effect. But the
**30-bar rate did not fall**; it rose slightly. Gaps get filled eventually at about
the same rate, they just take longer to get there.

That distinction has a consequence the prediction did not anticipate. What the
prediction was really about is `fvg.max_age_bars` (default 30), and the number
that decides whether that cap is well set is the **shape** of the curve, not its
endpoint. A cap at 30 bars catches essentially the same share of gaps on real
bars as on the fixture; what changed is how much of the wait happens inside it.

## Ablation: `fvg.mitigation_mode` (SPEC 12.2 / 12.6)

The mode decides when a gap stops being available to entry model C, so it changes
both the population and the edge test that reads it.

| Mode | Mitigated | Expired | Touch events | h=1 diff | h=1 verdict |
|---|---:|---:|---:|---:|---|
| `touch` | 8,140 | 1,281 | 7,800 | +0.0111 | EQUIVALENT |
| `ce`  ← default | 7,592 | 1,824 | 7,800 | +0.0111 | EQUIVALENT |
| `full` | 7,056 | 2,355 | 7,800 | +0.0111 | EQUIVALENT |

`touch` consumes a gap on any tag and `full` requires a complete traverse, so the
mitigated count falls and the expired count rises as the mode loosens.

**The touch-event count and the edge-test result are identical to the digit across
all three modes, and that is correct.** The first touch happens at the same bar
whatever the mode is; the mode only governs how long a gap stays *available* to
entry model C afterwards. So it cannot move a study that anchors on the first
touch. Reading these columns as evidence about the edge would be wrong, and reading
them as a copy-paste error would be too.

## Controls

### Positive control

Injected shifts at h=1, where the sample's own MDE is 0.034 ATR:

| Injected effect | Detected |
|---:|---|
| +0.00 ATR | no |
| +0.05 ATR | yes |
| +0.10 ATR | yes |
| +0.25 ATR | yes |
| +0.50 ATR | yes |

The detection boundary falls where the MDE says it should. That agreement is the
internal consistency check that makes the power table above worth acting on — if
the interval and the arithmetic disagreed, one of them would be wrong and the
required-sample figures would be fiction.

End to end with a 0.5 ATR shift the whole study reports **DIFFERENT**.

### Null calibration

- False-positive rate over 3,000 label shuffles: **5.6%** against alpha of 5%
- 95% Wilson interval: **[4.8%, 6.5%]** — contains alpha: yes
- Deviation: **1.5 sigma**

**Calibrated**, and now on enough shuffles to say so. An earlier version of this
report ran 400 and quoted the point estimate alone; at that trial count the
standard error is about 1.1 points, which is the same size as the deviation
being looked for. See D-012 §4.

This study has 7,800 observations where the H5 study had dozens,
and the percentile bootstrap under-covers with a few dozen heavy-tailed values.
D-022 found the same thing on the order-block study: raise the sample and the
coverage comes back. That is now confirmed twice, on independent populations.

## Do bigger gaps behave differently?

| Size tercile | n | Mean forward return at h=1 |
|---|---:|---:|
| small | 264 | +0.0648 |
| medium | 263 | -0.0116 |
| large | 264 | +0.0130 |

Reported because "bigger gaps matter more" is the natural next claim after a null,
and it is cheaper to check now than to re-open the study later. **Read it as a
breakdown, not as evidence**: three cells on one population is three more chances,
and none of these is corrected for that.

It matters more here than it did on the fixture, because the headline is now a
resolved null on real data and a subgroup that looks different is exactly the
result someone would want to rescue it with. Anything found here needs its own
pre-registered test on data this study has not touched, not a paragraph.

## Two spec corrections found while implementing this

Both are recorded in D-011 and amended into the spec in place.

**1. SPEC 12.1 labels the proximal and distal edges backwards.** A bullish gap
forms with price above it, so a return meets `zone_high` (= L_n) first. 12.1's
table says the proximal edge is `H_(n-2)` = `zone_low`; 12.2's touch rule and
12.4's worked example (*"buy limit at 1.08420 (proximal edge)"*, where 1.08420 is
L_n) both say the opposite. Two of three places agree, and they are the two that
describe behaviour rather than naming. **Entry model C places its limit at the
proximal edge**, so the label decides whether a model-C entry waits for a shallow
pullback or a deep one — it would have shipped as a systematically wrong fill price.
Two committed tests encoded the inverted version and were corrected.

**2. SPEC 12.2's touch rule made `INVALIDATED` unreachable.** The rule is written
one-sided — bullish `L <= zone_high` — which is right when price returns from the
gap's own side and wrong for the case 12.5 describes: a bar that opens below the
whole zone satisfies it while never having traded inside. Because a bullish close
below `zone_low` implies a low below `zone_low`, which is at or past *every*
mitigation target, mitigation always won the race and the gap-over case could never
fire. Touch is now range intersection, which agrees with 12.2 everywhere 12.2 is
right and differs only in 12.5's case. **Every gap-over would otherwise have been
counted as a fill**, inflating the fill-rate curve that is this phase's own
deliverable.

## What this report does NOT establish

**That FVGs are useless.** The verdict is EQUIVALENT on *one* claim — that a
touch carries standalone directional information over 1 to 12 H4 bars — and the
strategy does not use gaps that way. `disp.require_fvg` treats a gap as evidence
that displacement occurred, and entry model C treats one as a price to bid at.
Both are Phase 12's to evaluate.

Specifically not established:

1. **That the result holds out of sample.** This is the in-sample split. It was
   not checked on 2023-2024 or 2025, and it should not be: a null needs no
   confirmation bought with out-of-sample budget (protocol §7).
2. **That the size breakdown means anything.** Three cells, uncorrected — and see
   the warning under that table.
3. **That `INVALIDATED` is now well exercised.** 19 of 9,446 gaps is enough to
   prove the path is reachable and not enough to characterise it.
   `fvg.exclude_weekend_gaps` switched off is the ablation that would.
4. **Anything about entry model C's fill rate.** Selection is implemented and
   tested; what it is worth is Phase 12.
5. **That the margin is the right margin.** +/-0.25 ATR was declared in advance
   and EQUIVALENT means the interval sits inside it. A reader who thinks a
   0.10 ATR edge would be tradable should read the diff and CI columns, not the
   verdict — at h=3 the interval reaches +0.069 ATR.

## Verdict: PASS

The gate is the standalone edge test, and it ran on real bars with both controls
passing and 7,800 touch events — enough to resolve the declared
margin at every horizon rather than report an honest inability to tell. The
answer it returns is **no standalone directional edge**, and unlike every
previous null in this project that is a statement about the market rather than
about the fixture.
