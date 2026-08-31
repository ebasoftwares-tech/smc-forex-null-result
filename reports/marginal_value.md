# H5: does displacement filtering add value?

**MSS vs CHoCH-not-MSS forward returns** — SPEC 6.9, `BACKTEST_PROTOCOL.md` §6.2.

Generated 2026-08-30T14:25:42+00:00

- `config_hash` `7f393e8ced4bf193f993f056380bffff2826fbeb71ba2a583d99e54f19b18c49`
- `dataset_hash` `2a2bb0293052ae31bd2be73cfd53df25f6032f4db94da906543889e447d03ed9`
- Data: **real bars** -- 10 symbols, 2019-2022 (40 symbol-years), H4, `reference_mode = major`, source `histdata`, `bid` side, tzdata `2026.3`
- Split (PRE_REGISTRATION 4.1, Amendment 1): in-sample **[2019, 2020, 2021, 2022]**, out-of-sample [2023, 2024], holdout [2025]
- **3,856 CHoCH events, of which 326 are MSS**
- Equivalence margin: **+/-0.25 ATR**, declared in the module before any result was read

## Verdict: UNDERPOWERED

> UNDECIDED -- this sample cannot resolve the pre-declared margin. NOT a null result, and must not be reported as one

**The overall verdict is the weakest horizon's, and it hides a real answer.**
At h=1, h=4 the sample resolves the declared margin and reports
**EQUIVALENT** — so at those horizons H5 *is* answered on real
market data, and answered in the negative: MSS and CHoCH-not-MSS forward
returns differ by less than 0.25 ATR.
At h=12 it cannot tell, and the overall verdict says so rather than
averaging a resolved answer together with an unresolved one.

**This is not a null result and must not be cited as one.** H5 is falsified by
MSS and CHoCH-not-MSS being *indistinguishable* — an equivalence claim, which
needs the confidence interval to sit inside the margin, not merely to contain
zero. In this split it does not, at 1 of the 3 horizons.
The study is reporting that it cannot tell, which is a different sentence from
"displacement is decoration" and has different consequences.

## Gate checks

| Check | Result | Detail |
|---|---|---|
| Test suite green | PASS | 662 passed in 110.61s (0:01:50) |
| All three SPEC 6.9 populations reported | PASS | all CHoCH = MSS + CHoCH-not-MSS at every horizon |
| All three horizons run (+1/+4/+12) | PASS | SPEC 6.9 |
| Positive control detects an injected effect | PASS | 0.8 ATR shift -> DIFFERENT |
| Null calibration lands near alpha | PASS | 5.2% over 3,000 label shuffles, CI [4.5%, 6.1%] (alpha 5%) |
| Multiple-testing correction applied across horizons | PASS | Benjamini-Hochberg, q = 0.10 |
| Overlap diagnostic reported | PASS | 31.9% of events have a contaminated 12-bar window |
| Verdict distinguishes 'no effect' from 'no power' | PASS | UNDERPOWERED |

## The comparison (SPEC 6.9)

Forward returns are ATR-normalised and **signed by setup direction**, anchored on
the close of the CHoCH bar — the first moment the event is knowable. `all CHoCH` is
the union of the other two columns, not independent evidence.

### Primary sample

Every CHoCH event.

| h | n MSS | n not-MSS | MSS mean | not-MSS mean | diff | 95% CI | p (BH) | MDE | Verdict |
|---:|---:|---:|---:|---:|---:|---|---:|---:|---|
| 1 | 326 | 3,529 | -0.026 | -0.000 | -0.026 | [-0.117, +0.064] | 0.851 | 0.134 | **EQUIVALENT** |
| 4 | 326 | 3,526 | +0.022 | +0.006 | +0.017 | [-0.134, +0.168] | 0.851 | 0.232 | **EQUIVALENT** |
| 12 | 325 | 3,524 | -0.152 | -0.010 | -0.142 | [-0.417, +0.143] | 0.851 | 0.428 | **UNDERPOWERED** |

### Non-overlapping subsample

Thinned so no two forward windows overlap. Independent draws, far fewer of them.

| h | n MSS | n not-MSS | MSS mean | not-MSS mean | diff | 95% CI | p (BH) | MDE | Verdict |
|---:|---:|---:|---:|---:|---:|---|---:|---:|---|
| 1 | 326 | 3,529 | -0.026 | -0.000 | -0.026 | [-0.111, +0.066] | 0.971 | 0.134 | **EQUIVALENT** |
| 4 | 277 | 3,217 | +0.033 | +0.013 | +0.020 | [-0.137, +0.186] | 0.971 | 0.253 | **EQUIVALENT** |
| 12 | 201 | 2,417 | +0.055 | +0.049 | +0.006 | [-0.359, +0.369] | 0.971 | 0.524 | **UNDERPOWERED** |

### Stratified sample

Only (session slot, ATR tercile) cells containing both groups, so the two are not compared partly on when they happened.

| h | n MSS | n not-MSS | MSS mean | not-MSS mean | diff | 95% CI | p (BH) | MDE | Verdict |
|---:|---:|---:|---:|---:|---:|---|---:|---:|---|
| 1 | 326 | 2,857 | -0.026 | -0.003 | -0.023 | [-0.113, +0.067] | 0.954 | 0.137 | **EQUIVALENT** |
| 4 | 326 | 2,854 | +0.022 | +0.026 | -0.004 | [-0.155, +0.149] | 0.965 | 0.224 | **EQUIVALENT** |
| 12 | 325 | 2,852 | -0.152 | +0.032 | -0.184 | [-0.478, +0.098] | 0.670 | 0.422 | **UNDERPOWERED** |

All figures are in ATR units. `MDE` is the smallest true difference this sample
could detect at alpha 5% with 80% power — the number that
separates "no effect" from "no power", which is why it sits in every row rather
than being mentioned once in prose.

## Power: what would it take to answer this?

Read this table before the comparison table above. Required counts scale with
the return variance at each horizon, which grows roughly with the square root of
the horizon, so the long horizons are far more expensive than they look — and
the verdict at a horizon is only meaningful once this table says the sample
could have resolved it.

**Counting basis.** Events are collapsed to one per `(break bar, direction)`: the
forward return is a function of exactly those two things, so candidates sharing
them contribute the identical number more than once. Here that nearly halves
the raw population -- 6,764 CHoCH candidates become 3,856
observations, of which 326 are MSS. Phase 9's funnel reports
9.2 MSS per symbol-year after *cluster* dedup (SPEC 9.4);
this study measures 8.2 after the stricter break-bar dedup, and
the projections below use the stricter figure. The two rules are not the same:
SPEC 9.4 keys on the sweep, while two sweeps in different clusters can still break
on one bar and produce one number.

| h | MSS needed for +/-0.25 ATR | dev set has | Enough? | universe has | Enough? |
|---:|---:|---:|:--:|---:|:--:|
| 1 | 93 | 88 | **no** | 326 | yes |
| 4 | 281 | 88 | **no** | 326 | yes |
| 12 | 951 | 88 | **no** | 326 | **no** |

**At h=12 the full in-sample universe is not enough.** The
in-sample split contains 326 MSS
events across 10 symbols over
4 years; resolving the margin at that horizon needs
951. **H5 is not answerable at the 12-bar horizon on
this design over the in-sample period**, whatever the backtest shows — and that
was not knowable before this study was run.

The synthetic run listed three ways out and said they were *"better decided
now than after Phase 14"*. The data has arrived, so two of the three have
resolved themselves and one is closed:

1. **Answer H5 at the short horizons only — this is what happened.** h=1 and
   h=4 both resolve the margin and both report EQUIVALENT. That is a real
   answer to a narrower question than the methodology poses.
2. **Widening the margin is no longer available.** It was a defensible choice
   *before* the data and is an indefensible reaction after it (§10.2). It is
   recorded as closed rather than left on the list.
3. **The ablation delta remains the route to h=12's question**, measuring the
   same component through the full system rather than through forward returns.

## Controls

### Positive control — can the study find an effect that is really there?

Injected shifts on the MSS group at h=1, where the sample's own MDE is 0.134 ATR:

| Injected effect | Detected |
|---:|---|
| +0.00 ATR | no |
| +0.10 ATR | no |
| +0.25 ATR | yes |
| +0.50 ATR | yes |
| +1.00 ATR | yes |

**The detection boundary falls where the MDE says it should**, which is the
internal consistency check that makes the power table above worth acting on: if
the arithmetic and the interval disagreed, one of them would be wrong and the
required-sample figures would be fiction.

Run end to end with a 0.8 ATR shift, the whole study reports
**DIFFERENT** — so a real effect of that size would not be missed.

### Null calibration — does the study invent effects that are not there?

Shuffling the MSS label across the same returns makes the true difference exactly
zero, so every `DIFFERENT` verdict under a shuffle is a false positive by
construction.

- False-positive rate over 3,000 shuffles: **5.2%** against alpha of 5%
- 95% Wilson interval: **[4.5%, 6.1%]** — contains alpha: yes
- Deviation: **0.6 sigma**

**This figure was corrected in Phase 11.** It ran on 400 shuffles and read 7.8%,
which was written up as clear anti-conservatism. At 400 trials the standard error
on the rate is about 1.1 points — the same size as the effect — and that draw was
high. The direction survives at a proper trial count; the magnitude does not. See
D-012 §4.

**Calibrated.** The deviation is inside what 400 shuffles can resolve, so there
is nothing here to correct and nothing to read into the direction of the gap —
a point-and-a-half on 400 trials is noise, and this project has twice written up
a sub-2-sigma wobble as a finding before catching itself (Phase 7's significance
tests, Phase 8's "natural break" detector). Stating the sigma rather than the
rate alone is the habit those two produced.

What this rules out is the failure that matters: an interval method too narrow to
be trusted would fire far more often than alpha under a shuffled label, and neither
code review nor any other test in the suite would show it.

## Overlap, and why the second sample exists

**31.9% of CHoCH events have a 12-bar forward window that
overlaps a neighbour's.** Overlapping windows are not independent draws, and
treating them as such narrows every interval — the same class of error as Phase 7's
false positives, one level up.

The non-overlapping subsample fixes the independence and destroys the sample size:
at h=12 it leaves 201 MSS events. Neither
version can answer H5 here; reporting both is what makes that visible rather than
letting the more convenient one stand alone.

## Per-symbol stability

| Symbol | CHoCH | MSS | h=1 diff | h=4 diff | h=12 diff |
|---|---:|---:|---:|---:|---:|
| EURUSD | 369 | 24 | -0.095 | +0.186 | -0.039 |
| GBPUSD | 365 | 33 | +0.099 | +0.142 | +0.185 |
| USDJPY | 360 | 31 | +0.076 | -0.036 | -0.156 |
| AUDUSD | 403 | 41 | -0.039 | -0.079 | -0.572 |
| USDCAD | 391 | 35 | +0.018 | -0.052 | -0.694 |
| USDCHF | 380 | 27 | +0.110 | +0.343 | +0.987 |
| NZDUSD | 423 | 40 | -0.185 | -0.338 | -0.205 |
| EURJPY | 371 | 27 | -0.096 | +0.004 | +0.322 |
| GBPJPY | 393 | 28 | -0.004 | -0.035 | -0.095 |
| EURGBP | 401 | 40 | -0.075 | +0.260 | -0.445 |

**The sign flips between symbols at every horizon**, and the h=12 column spans
more than 1.5 ATR end to end on per-symbol samples of 24 to 41 MSS events. That
is what noise looks like at this sample size, and it is the same message the
power table gives in a different currency: no single symbol's number here is
readable as a direction, and picking the agreeable ones would be picking noise.

## What this study does NOT establish

**It does not answer H5 at every horizon, and the verdict says which.** On the
fixture this study could only validate its own instrument, because the true
MSS vs CHoCH-not-MSS difference on a random walk is zero by construction. On
real bars it tests H5 itself — but only where the population resolves the
declared margin. Read the power table before the comparison table: an
UNDERPOWERED horizon has not found 'no difference', it has failed to look.

Specifically not established:

- **That an UNDERPOWERED horizon is evidence of anything.** `UNDERPOWERED` is
  not `EQUIVALENT`, and only `EQUIVALENT` licenses "displacement filtering is
  decoration". This distinction is the reason the verdict is three-way.
- **That the result holds out of sample.** This is the in-sample split;
  2023-2024 and 2025 were not read.
- **R-expectancy**, which §6.2 also asks for. Stops (SPEC 16) and targets (SPEC 17)
  are Phase 12, so there is no R yet. Inventing a stop distance to fill the gap
  would make the answer a property of that invention.
- **That h=1 and h=4 being EQUIVALENT settles the component.** It settles the
  *forward-return* question at those horizons. §6.5's ablation delta measures the
  same component through the full system — entry, stop, target, costs — and can
  still find displacement filtering worth its place for reasons a forward return
  from the break bar cannot see.
- **That widening the margin is now available.** §6.9's 0.25 ATR was declared
  before any result was read. Widening it to 0.5 would divide every required
  count by four and make h=12 answerable — and doing so *after* seeing this table
  is exactly what §10.2 prohibits. The option was live before the run and is not
  live now.

## Status: PASS — H5 answered where the population allows, verdict UNDERPOWERED
