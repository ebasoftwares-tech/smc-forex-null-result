# Phase 9 Gate Report

Generated 2026-08-30T10:49:22+00:00

- `config_hash` `7f393e8ced4bf193f993f056380bffff2826fbeb71ba2a583d99e54f19b18c49`
- `dataset_hash` `2a2bb0293052ae31bd2be73cfd53df25f6032f4db94da906543889e447d03ed9`
- Data: **real bars** -- 10 symbols, 2019-2022 (40 symbol-years), H4, source `histdata`, `bid` side, tzdata `2026.3`
- Split (PRE_REGISTRATION 4.1): in-sample **[2019, 2020, 2021, 2022]**, out-of-sample [2023, 2024], holdout [2025]
- Reference modes: **major** and **micro**, run as two pre-registered strategies (SPEC 11.1)

> **The gate is a measurement.** Every previous run of this report scaled a
> conversion rate measured on one synthetic symbol to a ten-symbol universe and
> compared the product against the gate -- a PASS on projection, recorded as one.
> Real bars are in, so the counts the gate asks for are counted. The projection
> is retained below only as the thing the measurement is read against.

## Gate checks

| Check | Result | Detail |
|---|---|---|
| Test suite green | PASS | 662 passed in 106.94s (0:01:46) |
| Funnel reported, every stage (SPEC 11.7) | PASS | 135,947 -> 48,287 -> 50,316 -> 23,118 -> 21,539 -> 5,671 -> 368 |
| Event funnel is monotone non-increasing | PASS | each event stage is a subset of the one before |
| Level stages are monotone, and separate | PASS | levels and events are different units -- see the funnel table |
| Both reference modes run as separate variants (SPEC 11.1) | PASS | major 368 MSS, micro 55 MSS |
| MSS is a subset of CHoCH | PASS | SPEC 6.6 |
| CHoCH-not-MSS population retained (SPEC 6.9) | PASS | 5,303 events kept for the marginal-value test |
| Median sweep->MSS is not at the window edge | PASS | median 2.0 bars, 0.0% at the edge |
| MEASURED universe MSS >= 300 (major) | PASS | 368 over 4y x 10 symbols |
| MEASURED development-set MSS >= 120 (major) | FAIL | 97 over 4y x 3 symbols |

## The measurement against the projection

The single reason this report was re-run. Left column is what the synthetic random
walk projected and the gate was passed on; right is what ten real symbols over four
real years actually produce.

| | Synthetic projection | Real measurement | |
|---|---:|---:|---|
| Sweep -> MSS conversion | 1.98% | **1.59%** | x0.80 |
| MSS per symbol-year | 12.7 | **9.2** | x0.72 |
| Universe MSS (>= 300) | 507 | **368** | PASS |
| Development set MSS (>= 120) | 152 | **97** | FAIL |

SPEC 11.7 named the number that would constitute a design finding before any of
this was built:

> *"A funnel that converts 2% of sweeps into MSS will not produce a testable
> sample in five years, and that is a design finding to surface in Phase 9,
> before the entry engine is built."*

The synthetic fixture measured 1.98% -- exactly on that line, which is why
the sensitivity tables were put in this report rather than deferred to the ablation
suite. Real bars measure **1.59%**.

## The funnel (gate item 1)

SPEC 9.4 counts several stacked levels swept by one bar as **one opportunity**. That
distinction matters more here than anywhere else in the project: the per-sweep column
triples correlated risk rather than sample size, and it is the per-cluster column that
answers the gate.

**The funnel changes units in the middle, and the table says where.** The first two
rows count *levels*; the rest count *events*. They are not nested -- one level can
trigger several sweep events over its life (a rejected poke, then a real one), which is
why `sweeps_triggered` exceeds `levels_swept_or_tested` rather than shrinking. Only the
event chain is a funnel in the strict sense, and only it is asserted to be monotone.

| Stage | Unit | Per sweep | Per cluster | Survives |
|---|---|---:|---:|---:|
| `levels_created` | level | 135,947 | 135,947 |  |
| `levels_swept_or_tested` | level | 48,287 | 48,287 | 35.5% |
| *(fan-out)* | | | | x1.04 |
| `sweeps_triggered` | event | 50,316 | 50,316 |  |
| `sweeps_confirmed` | event | 28,012 | 23,118 | 45.9% |
| `reference_found` | event | 26,158 | 21,539 | 93.2% |
| `choch` | event | 6,764 | 5,671 | 26.3% |
| `mss` | event | 441 | 368 | 6.5% |

**Sweep -> MSS conversion: 1.59%** (per cluster, right-censored candidates excluded).

## Per symbol (gate item 2)

The gate is a sum over this table, and the spread across it is what a single pooled
number hides.

| Symbol | Set | H4 bars | Confirmed sweeps | CHoCH | MSS | Sweep -> MSS |
|---|---|---:|---:|---:|---:|---:|
| NZDUSD | cross | 6,423 | 2,322 | 638 | **47** | 2.02% |
| EURGBP | cross | 6,423 | 2,399 | 581 | **45** | 1.88% |
| AUDUSD | cross | 6,423 | 2,259 | 602 | **43** | 1.90% |
| USDCAD | cross | 6,423 | 2,335 | 564 | **40** | 1.71% |
| GBPUSD | dev | 6,424 | 2,328 | 543 | **37** | 1.59% |
| USDJPY | dev | 6,426 | 2,138 | 532 | **34** | 1.59% |
| USDCHF | cross | 6,424 | 2,345 | 551 | **32** | 1.36% |
| EURJPY | cross | 6,424 | 2,317 | 543 | **32** | 1.38% |
| GBPJPY | cross | 6,424 | 2,327 | 581 | **32** | 1.38% |
| EURUSD | dev | 6,424 | 2,348 | 536 | **26** | 1.11% |
| **total** | | | 23,118 | 5,671 | **368** | 1.59% |

| Year | MSS (universe) |
|---|---:|
| 2019 | 101 |
| 2020 | 86 |
| 2021 | 97 |
| 2022 | 84 |

### Suspect data, reported and not dropped (SPEC 1.5)

Real bars carry gaps; the synthetic fixture had none, so this row could not exist
before. SPEC 1.5's rule for a level formed in a suspect region is that it is
*tagged and reported separately*, not excluded, and that is followed literally --
every headline number above includes these events.

- Decided sweep opportunities on a suspect bar: **517 of 23,116** (2.24%)
- MSS on a suspect bar: **6 of 368** (1.63%)
- Universe MSS excluding them: **362** (still passes the >= 300 gate)

## Against the gate (gate item 2)

In-sample is 4 years over 10 symbols, of which 3 are the
development set (EURUSD, GBPUSD, USDJPY) -- `BACKTEST_PROTOCOL.md` section 2.1 and
`PRE_REGISTRATION.md` section 4.2.

| Mode | MSS / symbol-year | Universe MSS | vs 300 | Development set MSS | vs 120 |
|---|---:|---:|:--:|---:|:--:|
| **major** | 9.2 | 368 | PASS | 97 | FAIL |
| **micro** | 1.4 | 55 | FAIL | 16 | FAIL |

## Where the funnel loses candidates

| Terminal outcome | major | micro |
|---|---:|---:|
| `REFERENCE_TOO_FAR` | 8,463 | 3,346 |
| `NEW_EXTREME` | 6,788 | 9,630 |
| `CHOCH_NOT_MSS` | 5,303 | 8,984 |
| `NO_CHOCH_REFERENCE` | 1,577 | 126 |
| `OPPOSING_SWEEP` | 600 | 942 |
| `MSS_CONFIRMED` | 368 | 55 |
| `CHOCH_TIMEOUT` | 17 | 33 |

## Which MSS clause binds (SPEC 11.5)

Counted independently, so they overlap and do not sum to the CHoCH-not-MSS total. The
`sole cause` column is the marginal one: how often a clause is the *only* thing
standing between a CHoCH and an MSS.

| Clause | major: fires | major: sole cause | micro: fires | micro: sole cause |
|---|---:|---:|---:|---:|
| `DISPLACEMENT` | 4,313 | 1,077 | 8,132 | 988 |
| `NEW_EXTREME` | 1,956 | 109 | 4,805 | 134 |
| `OPPOSING_SWEEP` | 3,813 | 436 | 6,751 | 241 |
| `MTF_GATE` | 0 | 0 | 0 | 0 |

`MTF_GATE` is zero because the SPEC 7 bias engine is Phase 2-4 and does not exist; the
gate is injected as the always-pass control, which is `bias.gate_mode = none` -- the
variant SPEC 7.5 says MUST be run regardless. **Every MSS count in this report is
therefore an upper bound**: a real gate can only remove events.

## Timing (SPEC 11.7)

- Median bars from sweep extreme to MSS: **2.0** (min 1, max 8)
- Share of MSS at the window edge: **0.0%**

SPEC 11.7 asks this to detect a window doing the structure's work.

| Level tier | Decided sweeps | MSS | Conversion |
|---|---:|---:|---:|
| 1 | 1,984 | 17 | 0.86% |
| 2 | 6,047 | 79 | 1.31% |
| 3 | 15,085 | 272 | 1.80% |

| Source | Decided sweeps | MSS | Conversion |
|---|---:|---:|---:|
| `SESSION_LOW` | 7,617 | 140 | 1.84% |
| `SESSION_HIGH` | 7,468 | 132 | 1.77% |
| `PREV_DAY_LOW` | 2,163 | 24 | 1.11% |
| `PREV_DAY_HIGH` | 2,091 | 28 | 1.34% |
| `SWING_LOW` | 925 | 8 | 0.86% |
| `SWING_HIGH` | 925 | 16 | 1.73% |
| `PREV_WEEK_LOW` | 573 | 7 | 1.22% |
| `PREV_WEEK_HIGH` | 553 | 4 | 0.72% |
| `EQUAL_HIGHS` | 276 | 2 | 0.72% |
| `EQUAL_LOWS` | 275 | 3 | 1.09% |
| `PROTECTED_SWING` | 150 | 4 | 2.67% |
| `PREV_MONTH_HIGH` | 57 | 0 | 0.00% |
| `PREV_MONTH_LOW` | 43 | 0 | 0.00% |

| H4 open hour (UTC) | Decided sweeps | MSS | Conversion |
|---|---:|---:|---:|
| 00:00 | 2,707 | 51 | 1.88% |
| 04:00 | 4,798 | 80 | 1.67% |
| 08:00 | 5,235 | 91 | 1.74% |
| 12:00 | 5,436 | 87 | 1.60% |
| 16:00 | 2,864 | 27 | 0.94% |
| 20:00 | 2,076 | 32 | 1.54% |

Under D-001 the H4 grid is fixed at 00/04/08/12/16/20 UTC year-round, so the open hour
*is* the session slot. Read these as sample sizes, not as edges: `BACKTEST_PROTOCOL.md`
section 5.6 exists because six cells this size will always show a spread, and nothing
here corrects for having looked at six of them.

## Sensitivity of the gate to two parameters

Both re-run the MSS engine only; the liquidity and sweep engines are untouched by
either parameter, so the comparison is against an identical sweep population.

### `choch.max_bars_after_sweep` (TUNABLE)

| Value | Universe MSS | Sweep->MSS | Development set MSS |
|---|---:|---:|---:|
| 4 | 315 | 1.36% | 75 |
| 8 | 368 | 1.59% | 97 |
| 12 | 368 | 1.59% | 97  <- default |
| 18 | 368 | 1.59% | 97 |
| 24 | 368 | 1.59% | 97 |

### `choch.max_reference_distance_atr` (ABLATION)

| Value | Universe MSS | Sweep->MSS | Development set MSS |
|---|---:|---:|---:|
| 2 | 145 | 0.63% | 41 |
| 3 | 368 | 1.59% | 97  <- default |
| 4 | 446 | 1.93% | 113 |
| 6 | 466 | 2.02% | 120 |

| Bars from sweep extreme to MSS | MSS |
|---|---:|
| 0 | 0 |
| 1 | 76 |
| 2 | 129 |
| 3 | 55 |
| 4 | 54 |
| 5 | 30 |
| 6 | 10 |
| 7 | 10 |
| 8 | 4 |
| 9 | 0 |
| 10 | 0 |

**Neither table licenses moving either parameter.** `BACKTEST_PROTOCOL.md` section 10.2
forbids choosing a parameter by looking at the outcome, and that binds hardest exactly
when a gate sits near its threshold. The defaults were fixed before this ran and stay
fixed; both are ablated with the rest, on the split they belong to.

## Per-month counts (SPEC 11.7)

| Month | Confirmed sweeps | CHoCH | MSS |
|---|---:|---:|---:|
| 2019-01 | 438 | 119 | 6 |
| 2019-02 | 428 | 91 | 8 |
| 2019-03 | 510 | 133 | 11 |
| 2019-04 | 474 | 116 | 7 |
| 2019-05 | 508 | 145 | 7 |
| 2019-06 | 476 | 119 | 4 |
| 2019-07 | 457 | 98 | 4 |
| 2019-08 | 449 | 103 | 4 |
| 2019-09 | 444 | 127 | 11 |
| 2019-10 | 544 | 131 | 14 |
| 2019-11 | 507 | 158 | 11 |
| 2019-12 | 436 | 131 | 14 |
| 2020-01 | 527 | 107 | 5 |
| 2020-02 | 428 | 82 | 1 |
| 2020-03 | 479 | 97 | 11 |
| 2020-04 | 523 | 137 | 8 |
| 2020-05 | 447 | 98 | 11 |
| 2020-06 | 484 | 129 | 12 |
| 2020-07 | 542 | 131 | 12 |
| 2020-08 | 469 | 98 | 5 |
| 2020-09 | 509 | 119 | 6 |
| 2020-10 | 514 | 132 | 4 |
| 2020-11 | 468 | 113 | 3 |
| 2020-12 | 493 | 109 | 8 |
| 2021-01 | 433 | 90 | 5 |
| 2021-02 | 424 | 101 | 5 |
| 2021-03 | 530 | 144 | 7 |
| 2021-04 | 482 | 122 | 14 |
| 2021-05 | 434 | 103 | 8 |
| 2021-06 | 474 | 122 | 11 |
| 2021-07 | 529 | 129 | 5 |
| 2021-08 | 501 | 122 | 5 |
| 2021-09 | 485 | 125 | 12 |
| 2021-10 | 467 | 135 | 13 |
| 2021-11 | 477 | 122 | 5 |
| 2021-12 | 522 | 134 | 7 |
| 2022-01 | 448 | 100 | 8 |
| 2022-02 | 433 | 120 | 14 |
| 2022-03 | 503 | 108 | 5 |
| 2022-04 | 481 | 118 | 4 |
| 2022-05 | 492 | 112 | 3 |
| 2022-06 | 512 | 119 | 6 |
| 2022-07 | 477 | 119 | 9 |
| 2022-08 | 511 | 130 | 5 |
| 2022-09 | 494 | 129 | 8 |
| 2022-10 | 464 | 111 | 8 |
| 2022-11 | 485 | 134 | 9 |
| 2022-12 | 504 | 99 | 5 |

## The clause SPEC 11.5 omits (D-009)

SPEC 6.6 requires the swept level to lie beyond the extreme of the leg that produced
the CHoCH; SPEC 11.5 lists the MSS conditions and calls itself *complete* without it.
11.5 is operative here -- it is the more specific section and the one claiming
completeness -- so the other reading is priced instead of argued:

- **87 of 368 major MSS events** would additionally be rejected by the SPEC 6.6 clause.

## What this report does NOT establish

**Nothing about whether an MSS is worth trading.** This is a count. It says the
sequence occurs, how often, and on which symbols -- and nothing at all about what
happens next, which is Phases 10-14 and the falsification suite.

Specifically **not** established:

1. **That the count is the count a strategy would get.** The MTF gate is not built
   and is injected as always-pass, so every number here is an upper bound (SPEC
   7.5). A gate rejecting a fifth of setups moves both gate rows.
2. **That the symbols are independent.** Ten majors sharing USD, EUR and JPY legs
   are heavily correlated; the effective sample is smaller than the count, and the
   gate is stated in counts. The per-symbol table is the honest form.
3. **That any of this is out-of-sample.** This is the in-sample split, which is what
   it is for. The out-of-sample and holdout years exist and were not read.
4. **That a passing count implies a testable *edge*.** Sample size is necessary and
   not sufficient; H2-H5 remain open and are answered by the studies, not here.

## Verdict: FAIL

