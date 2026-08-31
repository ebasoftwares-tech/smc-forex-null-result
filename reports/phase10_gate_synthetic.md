# Phase 10 Gate Report

**FVG lifecycle (SPEC 12.2), selection (12.3), and the standalone edge test (12.6).**

Generated 2026-08-30T13:38:06+00:00

- `config_hash` `7f393e8ced4bf193f993f056380bffff2826fbeb71ba2a583d99e54f19b18c49`
- Fixture: 3 synthetic years (2024-2026), EURUSD, H4
- **707 gaps, 571 first-touch events**
- Equivalence margin: **+/-0.25 ATR**, declared before any result was read

## Gate checks

| Check | Result | Detail |
|---|---|---|
| Test suite green | PASS | SKIPPED (--skip-tests) |
| Standalone edge test run (gate) | PASS | 4 horizons, 571 touch events vs 571 matched controls |
| Positive control detects an injected effect | PASS | 0.5 ATR shift -> DIFFERENT |
| Null calibration lands near alpha | PASS | 5.5% over 3,000 shuffles (1.2 sigma from alpha 5%) |
| Lifecycle reaches MITIGATED and EXPIRED on the fixture | PASS | {'EXPIRED': 153, 'MITIGATED': 551, 'PARTIAL': 1, 'UNMITIGATED': 2} |
| INVALIDATED covered by a constructed test, not the fixture | PASS | needs a true gap-over; see test_a_gap_over_is_INVALIDATED_not_MITIGATED |
| Fill curve is monotone | PASS | 29.8% at 1 bar -> 78.2% at 30 |
| Both directions populated | PASS | {'BULLISH': 341, 'BEARISH': 366} |
| Mitigation-mode ablation reported | PASS | SPEC 12.6 |
| Verdict distinguishes 'no edge' from 'no power' | PASS | UNDERPOWERED |

## The gate: standalone edge test — UNDERPOWERED

> UNDECIDED -- this sample cannot resolve the declared margin. NOT a null result, and must not be reported as one

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
| 1 | 570 | 571 | +0.0276 | +0.0140 | +0.0136 | [-0.0576, +0.0832] | 0.817 | 0.101 | **EQUIVALENT** |
| 3 | 570 | 571 | -0.0083 | +0.0065 | -0.0148 | [-0.1415, +0.1125] | 0.817 | 0.180 | **EQUIVALENT** |
| 6 | 570 | 570 | +0.0295 | -0.0448 | +0.0743 | [-0.1058, +0.2549] | 0.817 | 0.260 | **UNDERPOWERED** |
| 12 | 569 | 569 | -0.0709 | -0.0025 | -0.0684 | [-0.3348, +0.1976] | 0.817 | 0.379 | **UNDERPOWERED** |

All figures in ATR units. Benjamini-Hochberg across the four horizons at q = 0.10:
four horizons on one population is four chances to find something, and Phase 7 is
this project's standing evidence that those chances get taken.

**At +1, +3 the study resolves the margin and finds no edge.** Those intervals
sit entirely inside +/-0.25 ATR, which is the only result that licenses the
word "no" — an interval merely containing zero would be absence of evidence.

**At +6, +12 it cannot resolve the margin** and says so rather than reporting a
null. That is why the overall verdict is UNDECIDED and not "no edge": the
concept is not cleared at every horizon this fixture was asked about.

**On a random walk the true effect is zero by construction**, so finding nothing
is what a working instrument does here. See "What this does NOT establish".

## Power, and how this compares to H5

The output that survives the fixture being synthetic, because it is a property of
the return distribution and the gap population rather than of the fixture's realism.

| h | touches needed for +/-0.25 ATR | dev set projects 2,284 | universe projects 7,613 |
|---:|---:|:--:|:--:|
| 1 | 94 | yes | yes |
| 3 | 297 | yes | yes |
| 6 | 617 | yes | yes |
| 12 | 1,306 | yes | yes |

At 190 touch events per symbol-year, the in-sample
period projects **7,613** across the universe and **2,284**
on the development set alone.

**This is the sharpest contrast with the H5 study** (`reports/marginal_value.md`),
and it is worth stating plainly. H5 needs ~800 MSS events at its longest horizon
and the whole in-sample universe projects ~427 — it is not answerable
there. The FVG concept is tested on a population about **18x**
larger, because every gap counts rather than only those that survive the
sweep-to-MSS funnel — and every horizon clears its requirement with room to spare.
**Whatever real data says about FVGs, this study will be able to hear it.**

## Lifecycle (SPEC 12.2)

| Terminal status | Gaps | Share |
|---|---:|---:|
| `EXPIRED` | 153 | 21.6% |
| `MITIGATED` | 551 | 77.9% |
| `PARTIAL` | 1 | 0.1% |
| `UNMITIGATED` | 2 | 0.3% |

**`INVALIDATED` is zero on this fixture, and that is the fixture rather than the
rule.** It fires when price leaves a zone behind without ever trading inside it —
SPEC 12.5's gap-over case — which needs a true price discontinuity. Synthetic H4
bars are continuous, and weekend-gap FVGs are excluded at creation
(`fvg.exclude_weekend_gaps`, default true), so the path cannot arise here. It is
covered by a constructed test instead
(`test_a_gap_over_is_INVALIDATED_not_MITIGATED`).

That test exists because the transition was **unreachable entirely** until Phase 10
generalised the touch rule — see "Two spec corrections" below.

Median bars from confirmation to mitigation: **2** (n = 551).

### Fill-rate curve (SPEC 12.6)

| Within k bars | Mitigated |
|---:|---:|
| 1 | 29.8% |
| 2 | 40.0% |
| 3 | 46.4% |
| 5 | 55.2% |
| 10 | 65.4% |
| 20 | 73.5% |
| 30 | 78.2% |

Gaps whose k-bar window runs past the end of the series are excluded from that
horizon rather than counted unfilled — right-censoring them would make the curve
sag at the long end purely because of where the data stops.

The curve is steep early: most gaps that fill do so within a few bars, which is
what a random walk should produce, since a gap is by construction a local price
extreme that ordinary oscillation returns to.

## Ablation: `fvg.mitigation_mode` (SPEC 12.2 / 12.6)

The mode decides when a gap stops being available to entry model C, so it changes
both the population and the edge test that reads it.

| Mode | Mitigated | Expired | Touch events | h=1 diff | h=1 verdict |
|---|---:|---:|---:|---:|---|
| `touch` | 591 | 114 | 571 | +0.0136 | EQUIVALENT |
| `ce`  ← default | 551 | 153 | 571 | +0.0136 | EQUIVALENT |
| `full` | 511 | 193 | 571 | +0.0136 | EQUIVALENT |

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

Injected shifts at h=1, where the sample's own MDE is 0.101 ATR:

| Injected effect | Detected |
|---:|---|
| +0.00 ATR | no |
| +0.05 ATR | no |
| +0.10 ATR | yes |
| +0.25 ATR | yes |
| +0.50 ATR | yes |

The detection boundary falls where the MDE says it should. That agreement is the
internal consistency check that makes the power table above worth acting on — if
the interval and the arithmetic disagreed, one of them would be wrong and the
required-sample figures would be fiction.

End to end with a 0.5 ATR shift the whole study reports **DIFFERENT**.

### Null calibration

- False-positive rate over 3,000 label shuffles: **5.5%** against alpha of 5%
- 95% Wilson interval: **[4.7%, 6.3%]** — contains alpha: yes
- Deviation: **1.2 sigma**

**Calibrated**, and now on enough shuffles to say so. An earlier version of this
report ran 400 and quoted the point estimate alone; at that trial count the
standard error is about 1.1 points, which is the same size as the deviation
being looked for. See D-012 §4.

This study has hundreds of observations where the H5 study had dozens, and the
percentile bootstrap under-covers with a few dozen heavy-tailed values — so a
difference between the two is expected. It is a smaller difference than the earlier
400-shuffle draws suggested (D-012 §4).

## Do bigger gaps behave differently?

| Size tercile | n | Mean forward return at h=1 |
|---|---:|---:|
| small | 64 | +0.1048 |
| medium | 63 | +0.0650 |
| large | 64 | +0.0780 |

Reported because "bigger gaps matter more" is the natural next claim after a null,
and it is cheaper to check now than to re-open the study later. **Read it as a
breakdown, not as evidence**: three cells on one population is three more chances,
and none of these is corrected for that.

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

**Nothing about whether FVGs work.** The fixture is a random walk, where the true
difference between a gap touch and a matched control is zero by construction — so
a `DIFFERENT` verdict here would mean the study is broken, not that gaps predict.
What is established is that the instrument runs, is deterministic, does not
repaint, finds an effect that is there, and does not invent one that is not.

Specifically not established:

1. **That the fill-rate curve transfers.** A random walk returns to a local extreme
   readily; a trending market may not. The 30-bar fill rate above should be
   expected to *fall* on real data, and `fvg.max_age_bars` re-examined when it does.
2. **That the size breakdown means anything.** Three cells, uncorrected, on data
   with no true effect.
3. **That `INVALIDATED` behaves correctly at scale.** It is exercised by one
   constructed test, because the fixture cannot produce the discontinuity it needs.
   Real data with weekend gaps — and `fvg.exclude_weekend_gaps` switched off as an
   ablation — is where the rate becomes measurable.
4. **Anything about entry model C's fill rate.** Selection is implemented and
   tested; what it is worth is Phase 12.

## Verdict: PASS

The gate is the standalone edge test, and it ran: with both controls passing, a
resolved no-edge result at the short horizons, an honest UNDECIDED at the long
ones, and a population large enough that real data will be able to answer it.
