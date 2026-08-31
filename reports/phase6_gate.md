# Phase 6 Gate Report

Generated 2026-08-25T16:13:39+00:00

- `config_hash` `30332e499a079286e7615f7862a6de281b225607242c591efd723cfed0db0204`
- Fixture: 3 independent synthetic years (2024, 2025, 2026), EURUSD, M15 source
- **10,277 levels** created across the three years

## Gate checks

| Check | Result | Detail |
|---|---|---|
| Test suite green | PASS | 160 passed in 7.30s |
| No level from a forming period (SPEC 8.4) | PASS | 0 violations |
| Every enabled source produced levels | PASS | 7/7 families |
| RANGE source off by default | PASS | SPEC 8.5.2 ABLATION-ONLY |
| OVERLAP / killzones excluded | PASS | derived + execution windows |
| PREV_MONTH never expires by age | PASS | SPEC 8.7 |
| Active cap never exceeded | PASS | cap 40 |
| Every terminal level is timestamped | PASS | population report can account for all |
| Sweep-rate report by source | **DEFERRED** | Sweep detection is Phase 7. Penetration rate below is its denominator |

## Population by source (SPEC 8.10)

| Source | Levels | Share | Tier |
|---|---:|---:|---:|
| SESSION_HIGH | 3,133 | 30.5% | 3 |
| SESSION_LOW | 3,133 | 30.5% | 3 |
| PREV_DAY_HIGH | 784 |  7.6% | 2 |
| PREV_DAY_LOW | 784 |  7.6% | 2 |
| PROTECTED_SWING | 650 |  6.3% | 2 |
| SWING_HIGH | 628 |  6.1% | 2 |
| SWING_LOW | 626 |  6.1% | 2 |
| PREV_WEEK_HIGH | 156 |  1.5% | 1 |
| PREV_WEEK_LOW | 156 |  1.5% | 1 |
| EQUAL_LOWS | 84 |  0.8% | 1 |
| EQUAL_HIGHS | 71 |  0.7% | 1 |
| PREV_MONTH_HIGH | 36 |  0.4% | 1 |
| PREV_MONTH_LOW | 36 |  0.4% | 1 |

| Family | Levels | Share |
|---|---:|---:|
| SESSION | 6,266 | 61.0% |
| PREV_DAY | 1,568 | 15.3% |
| SWING | 1,254 | 12.2% |
| PROTECTED_SWING | 650 |  6.3% |
| PREV_WEEK | 312 |  3.0% |
| EQUAL | 155 |  1.5% |
| PREV_MONTH | 72 |  0.7% |

Tier split: tier 1 = 724 (7.0%), tier 2 = 3,287 (32.0%), tier 3 = 6,266 (61.0%)

**SPEC 8.10 asks for exactly this table first, and it says why.** A source producing
an order of magnitude more levels than the others dominates the trade population by
construction. Here **SESSION is 61% of everything created** — it emits two
levels per session per day against two per *day* for PREV_DAY and two per *week* for
PREV_WEEK. Any statistic computed over undifferentiated levels is therefore mostly a
statement about session extremes. Every downstream report must break down by source.

## Lifecycle by source

| Source | Active | Invalidated | Expired | Merged | Pruned | Penetrated |
|---|---:|---:|---:|---:|---:|---:|
| SESSION_HIGH | 10 | 753 | 196 | 2166 | 8 | 47% |
| SESSION_LOW | 7 | 676 | 245 | 2189 | 16 | 45% |
| PREV_DAY_HIGH | 11 | 256 | 21 | 496 | 0 | 41% |
| PREV_DAY_LOW | 11 | 265 | 49 | 459 | 0 | 41% |
| PROTECTED_SWING | 0 | 19 | 4 | 627 | 0 | 4% |
| SWING_HIGH | 12 | 163 | 15 | 438 | 0 | 32% |
| SWING_LOW | 7 | 148 | 26 | 445 | 0 | 30% |
| PREV_WEEK_HIGH | 10 | 80 | 7 | 59 | 0 | 55% |
| PREV_WEEK_LOW | 11 | 66 | 13 | 66 | 0 | 46% |
| EQUAL_LOWS | 8 | 54 | 8 | 14 | 0 | 67% |
| EQUAL_HIGHS | 2 | 45 | 4 | 20 | 0 | 68% |
| PREV_MONTH_HIGH | 2 | 3 | 0 | 31 | 0 | 11% |
| PREV_MONTH_LOW | 3 | 4 | 0 | 29 | 0 | 11% |

`Penetrated` is the share of levels price traded through at least once while they
were active. **It is not the sweep rate**: a sweep is a penetration *followed by a
reclaim within a bounded window* (SPEC 9.1), and the reclaim half arrives in Phase 7.
Penetration is its denominator, so a source with a near-zero penetration rate cannot
produce sweeps and a source at ~100% is not identifying a barrier at all.

## Finding: PROTECTED_SWING is a strength annotation, not an independent source

SPEC 8.3 enumerates `PROTECTED_SWING` as source 7, and the spec text calls it
"arguably the highest-quality level in the model". Measured here, **95% of the
levels it emits are the *same swing*, at the identical price, that `SWING_*` has
already emitted** — the protected low *is* a confirmed swing low. They therefore
merge on the bar they are admitted, and the source's only lasting effect is `+1`
strength on whichever swing is currently protected.

That is arguably the right behaviour: the protected swing *should* rank above an
ordinary one, and strength is how this engine expresses that. But it is not what
§8.3 implies, and it has a concrete consequence for Phase 7: **`PROTECTED_SWING`
will show a near-zero sweep rate**, because the coincident `SWING_*` level is the
one that survives the merge and anchors the sweep. Read as "this source does not
work", that would be wrong. Its 4% penetration rate in the table above is the same
artefact seen one step earlier. See D-006.

## Merging

**68% of all levels created end as MERGED.** That is arithmetic, not a
defect. With the active book capped at 40 levels inside a 5-ATR in-play band and a
merge tolerance of 0.1 ATR, the mean gap between neighbouring levels is smaller than
the tolerance, so most levels have a near neighbour on arrival.

Two properties are pinned by test rather than assumed:

1. Merging runs to a **fixpoint**, so at the end of every bar no two active levels on
   a side sit within the tolerance. One pass is not enough, because SPEC 8.8 moves the
   survivor to the *more extreme* price, which can push it into the next cluster.
2. Merging is therefore **transitive**: a dense ladder collapses to its extremes even
   though its endpoints are far outside the tolerance. This is the specified rule
   working as written — the stops sit above the highest high — and a merged level's
   price is always some real constituent's price, never an invented one.

## What this report does NOT establish

The fixture is a random walk. These counts prove the engine is deterministic, causal
and self-consistent, and they establish the population shape every later statistic
must be read against. They say nothing about whether these levels are where stops
actually rest, and no strategy result may be produced from this data.

The single test that would answer that question is the **shuffled-liquidity control**
(`BACKTEST_PROTOCOL.md` §6.3): re-run the whole strategy with these levels replaced by
random prices matching the same distance-from-price distribution. It needs Phases 7–14
to be runnable, and it is the most informative test in the suite.

## Verdict: PASS (population half; sweep half deferred to Phase 7)
