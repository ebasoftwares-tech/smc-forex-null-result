# Phase 7 Gate Report

Generated 2026-08-31T15:34:51+00:00

- `config_hash` `7f393e8ced4bf193f993f056380bffff2826fbeb71ba2a583d99e54f19b18c49`
- `dataset_hash` `2a2bb0293052ae31bd2be73cfd53df25f6032f4db94da906543889e447d03ed9`
- Data: **real bars** -- 10 symbols, 2019-2022 (40 symbol-years), H4 from M1, source `histdata`, `bid` side, tzdata `2026.3`
- Equivalence margin: **+/-0.25 ATR**, declared before any result was read
- **28,005 confirmed sweeps** from 135,893 levels

## Gate checks

| Check | Result | Detail |
|---|---|---|
| Test suite green | PASS | 666 passed in 130.37s (0:02:10) |
| Sweep counts stable across years | PASS | CV = 0.017 over 4 years |
| Every year produced sweeps | PASS | min 6838 |
| Confirmed penetration inside bounds | PASS | 28005 events |
| No level swept twice (per run) | PASS | SPEC 8.9 |
| Failures are recorded, not dropped | PASS | 19351 failed, 2950 rejected |
| Forward-return study ran | PASS | 10 symbols x 4 horizons |
| Forward-return study returned a verdict | PASS | pooled EQUIVALENT at a +/-0.25 ATR margin over 28,005 sweeps |

## Sweep counts per year (gate item 1)

| Year | Confirmed |
|---|---:|
| 2019 | 6838 |
| 2020 | 7096 |
| 2021 | 6990 |
| 2022 | 7081 |

Failed and rejected counts are not split by year: they belong to the level that
failed, whose creation and expiry can fall either side of a year boundary, and
attributing them to one would invent a precision the events do not carry. The
outcome breakdown below reports them over the whole window instead.

| Symbol | Confirmed | Failed | Rejected |
|---|---:|---:|---:|
| EURUSD | 2852 | 1975 | 250 |
| GBPUSD | 2849 | 1943 | 314 |
| USDJPY | 2596 | 1979 | 271 |
| AUDUSD | 2746 | 1890 | 308 |
| USDCAD | 2840 | 1921 | 308 |
| USDCHF | 2875 | 1930 | 289 |
| NZDUSD | 2796 | 1918 | 287 |
| EURJPY | 2797 | 1845 | 314 |
| GBPJPY | 2750 | 1944 | 310 |
| EURGBP | 2904 | 2006 | 299 |

Coefficient of variation on confirmed counts: **0.017**. Stability is a
prerequisite for trusting any downstream statistic: a count that swings by a factor
of two between years means the denominator of every rate below is unstable too.

## Outcome breakdown

| Event | Count | Share |
|---|---:|---:|
| SWEEP_CONFIRMED | 28,005 | 55.7% |
| SWEEP_FAILED | 19,351 | 38.5% |
| SWEEP_REJECTED | 2,950 | 5.9% |

| Reason | Count |
|---|---:|
| OVER_PENETRATION | 10,517 |
| ACCEPTED_THROUGH | 7,182 |
| UNDER_PENETRATION | 2,950 |
| NO_RECLAIM | 1,550 |
| GAPPED_THROUGH | 79 |
| LEVEL_GONE | 23 |

SPEC 9.1 is explicit that a failure to reclaim is **not** "no event". The ratio of
confirmed to failed sweeps per source is a direct measure of whether a level is a
real barrier, and that measurement is impossible if failures are silently dropped.

## Sweep rate by source (closes the deferred half of the Phase 6 gate)

| Source | Swept | Invalidated | Expired | Sweep rate | Merged |
|---|---:|---:|---:|---:|---:|
| SESSION_LOW | 8949 | 6951 | 1642 | 51% | 23942 |
| SESSION_HIGH | 8774 | 6879 | 1560 | 51% | 24267 |
| PREV_DAY_LOW | 2793 | 1931 | 341 | 55% | 5321 |
| PREV_DAY_HIGH | 2634 | 1846 | 280 | 55% | 5627 |
| SWING_HIGH | 1218 | 1125 | 231 | 47% | 5554 |
| SWING_LOW | 1187 | 1109 | 233 | 47% | 5594 |
| PREV_WEEK_LOW | 718 | 538 | 153 | 51% | 635 |
| PREV_WEEK_HIGH | 700 | 511 | 187 | 50% | 659 |
| EQUAL_LOWS | 367 | 304 | 51 | 51% | 158 |
| EQUAL_HIGHS | 357 | 317 | 44 | 50% | 149 |
| PROTECTED_SWING | 200 | 188 | 28 | 48% | 8466 |
| PREV_MONTH_HIGH | 64 | 38 | 0 | 63% | 360 |
| PREV_MONTH_LOW | 44 | 26 | 0 | 63% | 395 |

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

## The answer: H2 is EQUIVALENT

Pooled over every symbol by concatenating raw returns -- never by averaging the
per-symbol effect sizes, which would discard the sample sizes that decide
whether the question can be answered at all.

| Horizon | n sweep | n control | diff (ATR) | 95% CI | needed for the margin | Verdict |
|---|---:|---:|---:|---|---:|---|
| +1 bars | 28,004 | 28,003 | -0.0085 | [-0.0211, +0.0038] | 150 | **EQUIVALENT** |
| +3 bars | 27,973 | 27,999 | -0.0192 | [-0.0397, +0.0010] | 383 | **EQUIVALENT** |
| +6 bars | 27,964 | 27,988 | -0.0149 | [-0.0441, +0.0145] | 782 | **EQUIVALENT** |
| +12 bars | 27,931 | 27,961 | -0.0427 | [-0.0851, +0.0003] | 1,592 | **EQUIVALENT** |

At h=1, h=3, h=6, h=12 the interval lies entirely inside the declared +/-0.25 ATR
margin. That is the only verdict in the three-way scheme licensing the word "no":
confirmed sweeps do not move the next bars by as much as the margin, against controls
matched on session slot and volatility tercile.

**The pooled verdict is EQUIVALENT**, which is the weakest horizon's rather than an
average of them -- combining a resolved answer with an unresolved one is precisely what
the three-way verdict exists to prevent (D-024 states the same rule for H5).

### Per-symbol, for the record

| Symbol | confirmed sweeps | diff at h=1 (ATR) |
|---|---:|---:|
| EURUSD | 2,852 | -0.0191 |
| GBPUSD | 2,849 | -0.0133 |
| USDJPY | 2,596 | -0.0180 |
| AUDUSD | 2,746 | +0.0232 |
| USDCAD | 2,840 | -0.0149 |
| USDCHF | 2,875 | -0.0404 |
| NZDUSD | 2,796 | -0.0021 |
| EURJPY | 2,797 | -0.0328 |
| GBPJPY | 2,750 | +0.0376 |
| EURGBP | 2,904 | -0.0038 |

No single symbol answers H2 and none is meant to: the per-symbol intervals are each far
wider than the margin, which is the whole reason the pooled sample exists. Reading one
row here as a result is the error section 5.6 exists to prevent.

## Finding: level and event ids are unique per run, not globally

Ids restart at `L000000` / `SW000001` in every run, so they collide across runs.
Harmless while each run is analysed alone and actively wrong the moment trades from
several symbols are pooled into one table. **Ids need a run or symbol namespace**
— SPEC 1.7 already specifies a ULID for exactly this reason, and the sequential
ids used here are a Phase 5–7 convenience that must not survive into the trade
log. Recorded as D-007, and closed since (D-015 section 1).

## What this report does NOT establish

**That the strategy has an edge.** H2 is one link measured on its own, which is the
whole point of SPEC 9.7's design -- no CHoCH, no displacement, no entry model, no stops.
What the sequence built on top of these sweeps does is a different question, answered
separately and in the negative: the falsification suite finds a shuffled level book
performs the same (H3, D-028), and the full chain's in-sample expectancy is -0.19 R with
an interval spanning zero (D-027).

**Anything out of sample.** This is the in-sample split, 2019-2022. The out-of-sample
budget was never spent and stays unspent (D-030).

**That the level-source breakdown above supports a change.** Reading the best source out
of that table and keeping it is selection on the same data -- the error
`BACKTEST_PROTOCOL.md` section 5.6 exists to prevent, and section 10.2 forbids acting on
outright.

## Verdict: PASS
