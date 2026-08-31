# Phase 11 Gate Report

**Order Block definition bake-off (SPEC 13.8).**

Generated 2026-08-30T13:22:33+00:00

- `config_hash` `7f393e8ced4bf193f993f056380bffff2826fbeb71ba2a583d99e54f19b18c49`
- `dataset_hash` `2a2bb0293052ae31bd2be73cfd53df25f6032f4db94da906543889e447d03ed9`
- Data: **real bars** -- 10 symbols, 2019-2022 (40 symbol-years), H4, source `histdata`, `bid` side, tzdata `2026.3`
- Split (PRE_REGISTRATION 4.1, Amendment 1): in-sample **[2019, 2020, 2021, 2022]**, out-of-sample [2023, 2024], holdout [2025]
- **6,764 CHoCH setups, 1,616 of which displaced** and can carry an OB

SPEC 13.1 opens by admitting the problem this phase exists to settle:

> *"'The last opposing candle before a move that breaks structure' is the standard
> formulation and it is under-specified in three places... Different choices produce
> zones tens of pips apart, which for a stop-based strategy is the difference between
> a win and a loss."*

So the deliverable is a comparison between four rules, not one rule's performance.

## Gate checks

| Check | Result | Detail |
|---|---|---|
| Test suite green | PASS | 662 passed in 107.16s (0:01:47) |
| All four definitions run as pre-registered variants (gate) | PASS | OB-A 1585, OB-B 1554, OB-C 1584, OB-D 398 |
| Agreement matrix reported (gate) | PASS | 6 pairs |
| Effective number of independent tests computed | PASS | M_eff = 1.68 against a nominal 4 (n = 384) |
| M_eff is below the nominal count | PASS | variants are not independent -- see the matrix |
| Rejection reasons enumerated per definition (SPEC 13.7) | PASS | the NO_OB_AVAILABLE rate is a quality signal, not a defect |
| Standalone edge test run per definition | PASS | 4 definitions x 4 horizons |
| Positive control detects an injected effect | PASS | 1.0 ATR shift -> DIFFERENT |
| Null calibration lands near alpha | PASS | 5.6% over 3,000 shuffles, CI [4.9%, 6.5%] (1.6 sigma from alpha 5%) |
| Zone-mode ablation reported | PASS | SPEC 13.3 / 13.8 |

## The headline: how many independent tests the four variants are worth

**M_eff = 1.68 against a nominal 4** (Galwey's estimator on the
correlation of proposed entry offsets, listwise n = 384).

The synthetic fixture reported **1.77**, and D-012 flagged that
number as a property of how the definitions behave on *that* fixture rather than a
constant. Recomputed on real bars it is **1.68**, a change of
5%. **Use the real-bar value in
every correction from here.**

SPEC 13.8 asks for the agreement matrix for one stated reason: *"near-identical
variants must not be counted as independent tests when applying the multiple-testing
correction."* That is a statistical instruction, so this report answers it with a
number rather than a table of percentages to eyeball.

Correcting as though these were 4 independent tests would over-correct by
a factor of 2.4. **Use 1.68, not 4.**

### The two agreement measures, side by side

| Pair | n | Same bar |
|---|---:|---:|
| OB-A vs OB-B | 1544 | 69.0% |
| OB-A vs OB-C | 1569 | 23.2% |
| OB-A vs OB-D | 393 | 0.0% |
| OB-B vs OB-C | 1539 | 30.7% |
| OB-B vs OB-D | 388 | 0.0% |
| OB-C vs OB-D | 393 | 0.0% |

Entry-offset correlation (ATR from the break bar's close):

| | OB-A | OB-B | OB-C | OB-D |
|---|---:|---:|---:|---:|
| **OB-A** | 1.000 | 0.985 | 0.970 | 0.925 |
| **OB-B** | 0.985 | 1.000 | 0.967 | 0.925 |
| **OB-C** | 0.970 | 0.967 | 1.000 | 0.926 |
| **OB-D** | 0.925 | 0.925 | 0.926 | 1.000 |

**Read those two tables together.** SPEC 13.6's heuristic — *"if OB-A and OB-C select
the same bar 80% of the time, they are not two hypotheses"* — is the right instinct
with the wrong instrument, and the two columns are what shows it. What a trade
consumes is the entry *price*, and two rules that differ by a fraction of an ATR are
one hypothesis however different their reasoning looks.

Restricted to OB-A/B/C, M_eff = **1.36** against a nominal 3 (n = 1530).

### Why Galwey and not Li & Ji

Li & Ji (2005) is the more commonly cited estimator and would report **2.00**
here. It is not used, for a reason specific to this study: it sums
`I(lambda >= 1) + frac(lambda)`, which is **discontinuous at integer eigenvalues**.
Four perfectly correlated variants give eigenvalues `[4, 0, 0, 0]` and it
analytically returns 1 — but it never sees an exact 4. `eigvalsh` on a matrix of
ones returns 3.999999999999999, `floor` drops from 4 to 3, and the estimate jumps
to ~2. It is wrong by a whole test on the most redundant input possible, from
floating-point noise alone. Galwey's `(sum sqrt(lambda))^2 / sum lambda` is
continuous and exact at every anchor. Both are pinned by tests.

## Hit rate and why each definition declines (SPEC 13.7)

*"The frequency of this is a quality signal for the definition"* — so the reasons
are enumerated rather than collapsed into a count of failures.

| Definition | Blocks | Hit rate | Rejections |
|---|---:|---:|---|
| OB-A `last_opposing` | 1,585 | 23.4% | `NO_DISPLACEMENT` 5148, `OB_ABOVE_REFERENCE` 17, `TOO_FAR` 14 |
| OB-B `last_down_close_before_break` | 1,554 | 23.0% | `NO_DISPLACEMENT` 5148, `OB_ABOVE_REFERENCE` 50, `TOO_FAR` 12 |
| OB-C `extreme_origin` | 1,584 | 23.4% | `NO_DISPLACEMENT` 5148, `OB_ABOVE_REFERENCE` 19, `TOO_FAR` 13 |
| OB-D `breaker` | 398 | 5.9% | `NO_DISPLACEMENT` 5148, `NO_FAILED_MOVE` 593, `OB_ABOVE_REFERENCE` 463 |

`NO_DISPLACEMENT` dominates every row and is the same number for all four: it is
SPEC 13.4's constraint 1, applied before any definition-specific search. **That
constraint is what stops OB-A degenerating into "the last red candle"**, which on
any chart is never more than a few bars away and therefore always exists. Without
it every definition would report a near-100% hit rate and the bake-off would
measure nothing.

**SPEC 13.2 describes OB-D in one line and leaves more open than it closes.** A, B
and C all key off the displacement leg of the setup in hand; D points at a
*different* structural event — *"the last opposing bar of the failed move"* —
without saying which swing, how far back, or what "broken downward" means for a
level that is broken upward by definition. The reading implemented is documented at
`order_blocks._ob_d` and recorded in D-012 as a **flagged ambiguity rather than a
resolved one**. Its hit rate is a property of that reading, not of the breaker
concept.

## Standalone edge test, per definition (SPEC 13.8)

| Definition | Blocks | Touches | h=1 diff | h=1 CI | h=1 MDE | Verdict |
|---|---:|---:|---:|---|---:|---|
| OB-A | 1,585 | 492 | +0.0050 | [-0.0913, +0.0972] | 0.140 | **UNDERPOWERED** |
| OB-B | 1,554 | 479 | -0.0073 | [-0.1114, +0.0949] | 0.149 | **UNDERPOWERED** |
| OB-C | 1,584 | 536 | -0.0126 | [-0.1039, +0.0800] | 0.133 | **UNDERPOWERED** |
| OB-D | 398 | 132 | -0.1456 | [-0.3616, +0.0531] | 0.291 | **UNDERPOWERED** |

Verdicts are three-way (`stats.Verdict`): `UNDERPOWERED` is **not** `EQUIVALENT`.
Only an interval sitting inside the declared +/-0.25 ATR
margin licenses "this definition contributes nothing".

### Answerability, measured rather than projected

OB-A yields **492 touch events** across the 40
in-sample symbol-years (12 per symbol-year), of which
**135** are on the three development symbols.

| h | touches needed | dev set has | universe has | answerable? |
|---:|---:|---:|---:|---|
| 1 | 155 | 135 (no) | 492 | yes |
| 3 | 343 | 135 (no) | 492 | yes |
| 6 | 784 | 135 (no) | 492 | **no** |
| 12 | 1,652 | 135 (no) | 492 | **no** |

The development-set column is the one to read. Phase 9 measured the funnel that
feeds this study and its development-set half **failed its gate** (D-020), so a
study answerable across the universe but not on the three symbols development
actually happens on is answerable only in a sense that cannot be iterated against.

## Fill rate and time to fill

| Definition | fill@5 | fill@30 | median bars to fill |
|---|---:|---:|---:|
| OB-A | 15.2% | 50.4% | 9 |
| OB-B | 14.2% | 52.0% | 9 |
| OB-C | 10.6% | 47.9% | 10 |
| OB-D | 25.2% | 48.2% | 5 |

SPEC 13.7 makes this a headline statistic rather than a detail: *"a model with a
20% fill rate has a fifth of the sample size and cannot be compared naively against
model A."* Whatever share of OB-A's blocks go unfilled within 30 bars is the share
of setups entry model D discards before any comparison starts.

## Zone-mode ablation (SPEC 13.3)

| `ob.zone_mode` | Blocks | Mean zone height | fill@5 | fill@30 |
|---|---:|---:|---:|---:|
| `full_range`  ← default | 1,585 | 0.09101 | 15.2% | 50.4% |
| `body` | 1,599 | 0.04557 | 15.3% | 51.4% |
| `wick_to_open` | 1,599 | 0.07071 | 13.5% | 49.4% |

The mode changes the zone's height and therefore how easily price reaches its
midpoint, so fill rate moves with it. It does not change which bar was chosen, so
it is orthogonal to the definition bake-off above and the two ablate independently.

## Controls

### Positive control

Injected shifts on OB-A at h=1, where the sample's MDE is 0.140 ATR:

| Injected effect | Detected |
|---:|---|
| +0.00 ATR | no |
| +0.25 ATR | yes |
| +0.50 ATR | yes |
| +1.00 ATR | yes |

End to end with a 1.0 ATR shift the study reports **DIFFERENT**.

### Null calibration

- False-positive rate over 3,000 label shuffles: **5.6%** against alpha of 5%
- 95% Wilson interval on that rate: **[4.9%, 6.5%]** — it contains alpha: yes
- Deviation: **1.6 sigma**

**Both the interval and the trial count are the result of getting this wrong first.**
Earlier drafts ran 300-400 shuffles, where the standard error on the rate is about
1.1 points — the same size as the deviation being looked for. Three draws of this
very calibration read 4.8%, 8.0% and 5.5%, and the 8.0% one was written up as
evidence of a miscalibrated interval. It was a noisy draw. Every calibration in the
project now runs 3,000 shuffles and quotes its Wilson interval. See D-012 §4.

**Calibrated** — the deviation is inside what 3,000 shuffles resolve.

## What this report does NOT establish

**Which definition is best.** That needs performance, and performance needs the
entry engine, the risk layer and the backtest — Phases 12 to 14. What this
establishes is the prerequisite SPEC 13.8 asks for: how many independent tests the
comparison actually represents, so that the eventual performance numbers can be
corrected honestly rather than by counting variants.

Specifically not established:

1. **That `M_eff` is stable across splits.** It is recomputed here on the in-sample
   years only. Nothing says it holds on the out-of-sample or holdout years, and it
   should not be recomputed there to find out — that spends out-of-sample budget on
   a nuisance parameter (protocol §7).
2. **Anything about edge.** See the verdict column; an UNDERPOWERED result is a
   statement about the sample, not about the market.
3. **That OB-D's hit rate reflects the breaker concept.** It reflects one reading of
   a one-line specification (D-012 §1).
4. **That these touch counts survive the Phase 9 result.** D-020's development-set
   gate failed, and this study draws from the same funnel.

## Verdict: PASS

The bake-off ran, all four definitions are implemented as pre-registered variants,
and the agreement matrix has been converted into the number it exists to produce:
**1.68 effective tests, not 4**.
