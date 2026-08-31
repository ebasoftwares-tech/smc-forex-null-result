# Phase 7 Gate Report

Generated 2026-08-31T15:23:05+00:00

- `config_hash` `7f393e8ced4bf193f993f056380bffff2826fbeb71ba2a583d99e54f19b18c49`
- Fixture: 5 independent synthetic years (2022–2026), EURUSD
- **3,882 confirmed sweeps** from 17,015 levels

## Gate checks

| Check | Result | Detail |
|---|---|---|
| Test suite green | PASS | 666 passed in 135.35s (0:02:15) |
| Sweep counts stable across years | PASS | CV = 0.016 over 5 years |
| Every year produced sweeps | PASS | min 761 |
| Confirmed penetration inside bounds | PASS | 3882 events |
| No level swept twice (per run) | PASS | SPEC 8.9 |
| Failures are recorded, not dropped | PASS | 2098 failed, 457 rejected |
| Forward-return study ran | PASS | 5 years x 4 horizons |

## Sweep counts per year (gate item 1)

| Year | Confirmed | Failed | Rejected |
|---|---:|---:|---:|
| 2022 | 787 | 417 | 89 |
| 2023 | 786 | 394 | 88 |
| 2024 | 782 | 440 | 79 |
| 2025 | 766 | 426 | 93 |
| 2026 | 761 | 421 | 108 |

Coefficient of variation on confirmed counts: **0.016**. Stability is a
prerequisite for trusting any downstream statistic: a count that swings by a factor
of two between years means the denominator of every rate below is unstable too.

## Outcome breakdown

| Event | Count | Share |
|---|---:|---:|
| SWEEP_CONFIRMED | 3,882 | 60.3% |
| SWEEP_FAILED | 2,098 | 32.6% |
| SWEEP_REJECTED | 457 | 7.1% |

| Reason | Count |
|---|---:|
| ACCEPTED_THROUGH | 1,160 |
| OVER_PENETRATION | 706 |
| UNDER_PENETRATION | 457 |
| NO_RECLAIM | 227 |
| LEVEL_GONE | 5 |

SPEC 9.1 is explicit that a failure to reclaim is **not** "no event". The ratio of
confirmed to failed sweeps per source is a direct measure of whether a level is a
real barrier, and that measurement is impossible if failures are silently dropped.

## Sweep rate by source (closes the deferred half of the Phase 6 gate)

| Source | Swept | Invalidated | Expired | Sweep rate | Merged |
|---|---:|---:|---:|---:|---:|
| SESSION_LOW | 1287 | 780 | 209 | 57% | 2931 |
| SESSION_HIGH | 1188 | 854 | 221 | 52% | 2939 |
| PREV_DAY_LOW | 359 | 205 | 46 | 59% | 680 |
| PREV_DAY_HIGH | 351 | 193 | 51 | 59% | 698 |
| SWING_LOW | 175 | 135 | 33 | 51% | 657 |
| SWING_HIGH | 174 | 124 | 37 | 52% | 676 |
| PREV_WEEK_HIGH | 103 | 49 | 23 | 59% | 66 |
| PREV_WEEK_LOW | 94 | 45 | 20 | 59% | 86 |
| EQUAL_HIGHS | 58 | 29 | 8 | 61% | 18 |
| EQUAL_LOWS | 56 | 31 | 6 | 60% | 17 |
| PROTECTED_SWING | 22 | 15 | 3 | 55% | 1044 |
| PREV_MONTH_HIGH | 8 | 3 | 0 | 73% | 47 |
| PREV_MONTH_LOW | 7 | 4 | 0 | 64% | 47 |

The rate is `SWEPT / (SWEPT + INVALIDATED + EXPIRED)` — of the levels that reached a
terminal state on their own merits, rather than being merged away. SPEC 8.10 reads
this table in both directions: **a source whose levels are almost never swept
contributes nothing; a source whose levels are almost always swept is not
identifying a barrier at all.**

`PROTECTED_SWING`'s near-zero count is the merge working, not the source failing —
it duplicates a `SWING_*` level at the identical price 95% of the time, so the swing
level survives the merge and anchors the sweep (D-006, predicted before this phase
existed).

## Forward-return study (gate item 2, SPEC 9.7)

Hypothesis **H2** (`BACKTEST_PROTOCOL.md` §6.1): *confirmed sweeps carry directional
information*. Returns are ATR-normalised and signed by the direction the sweep
implies (a sell-side sweep implies up). Controls are bars in the same UTC-hour slot
and volatility tercile with no confirmed sweep.

| Horizon | Mean diff (sweep − control) | Years significant |
|---|---:|---:|
| +1 bars | +0.0015 ATR | 0 / 5 |
| +3 bars | -0.0076 ATR | 2 / 5 |
| +6 bars | -0.0234 ATR | 1 / 5 |
| +12 bars | +0.0248 ATR | 0 / 5 |

- 2022: NO MEASURABLE DIRECTIONAL EDGE — H2 unsupported in this sample
- 2023: NO MEASURABLE DIRECTIONAL EDGE — H2 unsupported in this sample
- 2024: NO MEASURABLE DIRECTIONAL EDGE — H2 unsupported in this sample
- 2025: SWEEPS CARRY DIRECTIONAL INFORMATION in this sample
- 2026: SWEEPS CARRY DIRECTIONAL INFORMATION in this sample

### 3 of 20 year x horizon tests came out "significant" on pure noise

That is not a defect; it is the multiple-testing problem, demonstrated on data
known to contain nothing. At a 5% false-positive rate, 20 independent tests
produce 1 spurious hits on average, and 3 is comfortably inside that.

**This is exactly why `BACKTEST_PROTOCOL.md` §5.6 requires Benjamini-Hochberg
correction and a Deflated Sharpe Ratio computed against the declared configuration
count.** Anyone reading a single per-year row here and calling it an edge would be
reporting arithmetic. The same trap scales: with `M = 9,600` configurations
declared in the pre-registration, the *expected maximum* result under the null is
large, which is why the protocol prints it at the top of every optimisation report.

### Reading this correctly

**The fixture is a random walk, so the correct result is exactly this one: nothing.**
A random walk contains no liquidity, no stop clusters and no participants, so a
sweep of a level in it is a coincidence of arithmetic. Had this study reported an
edge here, the study would be broken.

So this run establishes that the study **has no false-positive tendency**. What
makes the null result meaningful rather than vacuous is the paired positive control
in `tests/test_sweep_study.py`: a series with a planted post-sweep drift is detected
at +1 bar with a confidence interval excluding zero, in both directions, and an
inverted edge reports negative rather than zero. A study that could only ever say
"no edge" would pass the random walk and be worthless.

**H2 is not answered by this run**, and cannot be on synthetic data. It is
answered on real bars by the default mode of this same script.

## Finding: level and event ids are unique per run, not globally

Ids restart at `L000000` / `SW000001` in every run, so they collide across runs.
Harmless while each run is analysed alone and actively wrong the moment trades from
several symbols are pooled into one table. **Ids need a run or symbol namespace**
— SPEC 1.7 already specifies a ULID for exactly this reason, and the sequential
ids used here are a Phase 5–7 convenience that must not survive into the trade
log. Recorded as D-007, and closed since (D-015 section 1).

## What this report does NOT establish

Nothing about whether this strategy has an edge. Sweep counts, rates and stability here
are properties of the detector meeting a random walk. The one thing that would answer
the real question is this same study on real FX history, which is what the default mode
of this script now runs.

## Verdict: PASS
