# Phase 13 Gate Report

**Risk management (SPEC 18), with the SPEC 16 stop models and the SPEC 17.2 RR
gate that sizing depends on.**

Generated 2026-08-30T15:43:48+00:00

- `config_hash` `7f393e8ced4bf193f993f056380bffff2826fbeb71ba2a583d99e54f19b18c49`
- `dataset_hash` `2a2bb0293052ae31bd2be73cfd53df25f6032f4db94da906543889e447d03ed9`
- Data: **real bars** -- 10 symbols, 2019-2022 (40 symbol-years), H4, source `histdata`, `bid` side, tzdata `2026.3`
- Split (PRE_REGISTRATION 4.1, Amendment 1): in-sample **[2019, 2020, 2021, 2022]**, out-of-sample [2023, 2024], holdout [2025]
- **Each symbol is sized under its own `symbol_spec`** — a JPY pair's pip is 0.01 against 0.0001, and `risk.max_sl_pips` is {default: 60, JPY: 90}
- **1,616 displaced CHoCH setups** over 64,238 H4 bars
- Account: USD 10,000 at 0.35% per trade

## Scope

Phase 13 completes SPEC 16 (all four stop models, the full 16.2 buffer, the 16.3
caps), implements SPEC 17.1/17.2's target **placement** and minimum-RR gate, and
implements SPEC 18 in full. It does **not** implement SPEC 17.3-17.5 — break-even,
trailing, time and calendar exits — or the execution of T3's ladder and T4's trail.
Those need an open trade and land with the exit policy in Phase 14.

The RR gate is here rather than there because `RR_BELOW_MIN` fires in
CHOCH_CONFIRMED (SPEC 19 item 16), the same state as the 16.3 stop caps and the
18.2 sizing rejections. Those three are what this gate exercises, and implementing
two of the three would leave it half met.

## Gate checks

| Check | Result | Detail |
|---|---|---|
| Test suite green | PASS | 662 passed in 107.94s (0:01:47) |
| Every limit exercised by scenario (gate) | PASS | 18/18 scenarios fire on their trigger and not on their near miss |
| Sizing purity test passes (gate) | PASS | 6 passed, 25 deselected in 0.52s |
| Realised risk never exceeds nominal | PASS | SPEC 18.9: max realised fraction 0.9998 over 533 sized setups |
| The drawdown ladder is monotone and never above 1.0 | PASS | SPEC 18.5 / 18.1, and no configuration can express the violation |
| The chain produces sized trades, not only rejections | PASS | 533 of 1,616 setups clear every pre-trade check under S1 |
| Every rejection is a named SPEC 19 reason | PASS | SPEC 19: 'nothing exits a setup silently' |
| Four stop models are worth fewer than four tests | PASS | M_eff 1.32 over 1,528 setups — use this, not 4, in the multiple-testing correction |

## Every limit exercised by scenario (gate, first half)

Each row needs **both** columns. A limit that fires on its trigger has been shown
to fire; a limit that also declines to fire one step below it has been shown to
fire *because of* the trigger. Without the second column, a check that rejected
everything would pass this table.

| Limit | Trigger fires | Near miss | Scenario |
|---|---|---|---|
| `max_daily_loss_pct` | `RISK_LIMIT_DAILY` | — | one day's closed losses at the cap; the near miss is 0.1pp under it |
| `max_weekly_loss_pct` | `RISK_LIMIT_WEEKLY` | — | 3 x 1.5% across one trading week; every day under the daily cap |
| `max_monthly_loss_pct` | `RISK_LIMIT_MONTHLY` | — | 9 x 1.0% across three weeks of one month; every week and day under their caps |
| `max_consecutive_losses` | `RISK_LIMIT_CONSECUTIVE` | — | 5 losses in a row pauses for 24h; the near miss is one short |
| `consecutive_loss_pause_hours` | — | `RISK_LIMIT_CONSECUTIVE` | the pause lifts by itself; the 'near miss' column is the same ledger during it |
| `max_open_positions` | `RISK_LIMIT_POSITIONS` | — | 3 concurrent positions |
| `max_positions_per_symbol` | `RISK_LIMIT_SYMBOL` | — | the near miss holds the same count in a different symbol |
| `max_correlated_positions` | `RISK_LIMIT_CORRELATION` | — | 2 in one cluster, under the position cap |
| `correlated (directional equivalence)` | `RISK_LIMIT_CORRELATION` | — | long EURUSD + short USDCHF is one exposure; long USDCHF is the other side |
| `max_total_open_risk_pct` | `RISK_LIMIT_EXPOSURE` | — | UNREACHABLE legally: the near miss is the largest legal third trade (0.50%), which lands on exactly the cap; the trigger needs 0.60%, outside [0.10, 0.50] |
| `max_spread_pips` | `SPREAD_TOO_WIDE` | — | absolute cap 2.0 pips, measured against a wide (60-pip) stop so the relative cap cannot be what fires |
| `max_spread_pct_of_sl` | `SPREAD_TOO_WIDE` | — | 1.5 pips is inside the 2.0-pip absolute cap either way; only the stop changes |
| `equity_dd_kill_pct` | `KILL_SWITCH` | — | measured on equity INCLUDING floating PnL, unlike the loss limits |
| `consecutive streak twice in a week` | `KILL_SWITCH` | `RISK_LIMIT_CONSECUTIVE` | SPEC 18.6: twice in one week is a halt, not the timeout one streak causes |
| `manual kill switch` | `KILL_SWITCH` | — | SPEC 18.6's file trigger, so the person watching the screen can use it |
| `min_lot` | `SIZE_BELOW_MIN` | — | a $200 account at 0.10% with a 60-pip stop wants 0.0033 lots |
| `max_lot` | `SIZE_ABOVE_MAX` | — | a $1bn account clears the 100-lot ceiling |
| `min_realised_fraction` | `SIZE_UNDER_RISK` | — | UNREACHABLE at the 0.5 default: the trigger needs 0.75, and the 'near miss' is SPEC 18.2's own worked example at the real default |

**These are scenarios and not measurements, and the gate's own wording is why.**
Every loss limit in SPEC 18.4 is defined on *closed* PnL, and nothing closes a
trade until the exit policy exists in Phase 14. How often each limit actually
binds is a Phase 14 number; that each one binds correctly is this one.

### The drawdown ladder (SPEC 18.5)

| Drawdown | Multiplier |
|---:|---:|
| 0.00% | x1.00 |
| 4.99% | x1.00 |
| 5.00% | x0.75 |
| 5.01% | x0.75 |
| 7.99% | x0.75 |
| 8.00% | x0.50 |
| 8.01% | x0.50 |
| 10.00% | x0.50 |

Monotone non-increasing in drawdown, and never above 1.00 — SPEC 18.1's
anti-martingale invariant at portfolio level. It is **not** monotone in *time*:
SPEC 18.5 restores the multiplier as drawdown falls, so a test asserting
monotonicity in time would assert the opposite of the specification.

Two independent guarantees hold it: a config validator, so no configuration can
*express* an increase, and a clamp, so no arithmetic can *produce* one. The
validator fires first, which meant a mutation deleting the clamp survived the
entire suite until a test bypassed the validator to reach it.

## Sizing purity (gate, second half)

SPEC 18.1 makes martingale, loss-recovery sizing and averaging down
*unimplementable* rather than discouraged, by withholding the information they
need. `position_size` takes:

```
position_size(equity, risk_pct, sl_distance, *, spec, value_per_unit,
              min_realised_fraction)
```

No history, no PnL, no streak counter, no ledger. Asserted three ways, because one
is not enough: by **introspection** on the signature (a behavioural test can only
show the function did not use history on the inputs it happened to get), by
**construction** (a ledger carrying 50 closed losses produces identical lots), and
by **invariant** (the ladder can only reduce).

### The realised risk-per-trade distribution (SPEC 18.9)

*"It MUST be a spike at `risk.pct_per_trade` with a lower tail only from lot
rounding. Any mass above the nominal value is a sizing bug."*

| | |
|---|---:|
| Sized setups | 533 |
| Median realised / intended | 0.9630 |
| 5th percentile | 0.8947 |
| Minimum | 0.8489 |
| **Maximum** | **0.9998** |
| Above nominal | 0 |

The maximum is the assertion: flooring to the lot step is what makes it
impossible to exceed 1.0, and rounding instead would put mass above nominal on
roughly half of all trades.

## The four stop models on the setup stream

| Model | Armed | Accepted | Rejected at | Top reason |
|---|---:|---:|---|---|
| **S1 — sweep extreme** | 1,616 | 533 | SIZING 800, STOP 212, ARM 71 | `SIZE_BELOW_MIN` 800 |
| **S2 — structural swing** | 1,616 | 539 | SIZING 825, STOP 252 | `SIZE_BELOW_MIN` 825 |
| **S3 — order block** | 1,616 | 589 | SIZING 861, STOP 135, ARM 31 | `SIZE_BELOW_MIN` 861 |
| **S4 — ATR multiple** | 1,616 | 565 | SIZING 863, STOP 188 | `SIZE_BELOW_MIN` 863 |

Entry model C (FVG) throughout, so the four rows differ only in their stop.

### S4 has a hard ATR ceiling, and it is not the ATR cap

S4's stop is `atr_multiple` x ATR = 1.5 ATR by construction, and
`max_sl_pips` is 60 pips (90 for JPY pairs). The two cross at
**40 pips of ATR** (60 for JPY): above that, S4 is
`SL_TOO_WIDE` on every setup, whatever the setup looks like.

- H4 ATR: median **26.5 pips**, and
  **12% of setups sit above their own symbol's ceiling** — so the
  ceiling **does** bind, on roughly one setup in 9
- S4 accepts 565 of 1,616 setups end to end
- Mirror image: `max_sl_atr` is 2.5 and S4 is 1.5, so **under S4 the ATR cap can never fire** — that half is
  arithmetic between two constants and holds on any data

**The fixture could not answer this and real bars do.** The synthetic walk's
median H4 ATR was 17.4 pips against a 40-pip threshold, so the ceiling never
came near binding; the real median is 26.5 pips
and 12% of setups clear their symbol's ceiling outright.

| Symbol | median H4 ATR (pips) | ceiling | above it |
|---|---:|---:|---:|
| AUDUSD | 24.3 | 40 | 9% |
| EURGBP | 19.5 | 40 | 1% |
| EURJPY | 28.9 | 60 | 10% |
| EURUSD | 25.4 | 40 | 9% |
| GBPJPY | 42.9 | 60 | 16% |
| GBPUSD | 38.9 | 40 | 47% |
| NZDUSD | 23.4 | 40 | 3% |
| USDCAD | 28.7 | 40 | 16% |
| USDCHF | 22.1 | 40 | 8% |
| USDJPY | 22.1 | 60 | 4% |

**So S4 is a partially available model rather than an unavailable one or a
universally usable one**, and which it is depends on the symbol. That is the
question D-014 §3 left open, answered.

Two FROZEN defaults that were each reasonable alone. Reported, not changed: SPEC
16.3's caps and `sl.atr_multiple` are both frozen, and moving one to make the other
reachable is a decision rather than an implementation detail.

### Which upper cap does the work (SPEC 16.3)

`max_sl_atr` (2.5) and `max_sl_pips` (60) are both FROZEN, and only one of them can
ever be the one that rejects. They cross at 24 pips of ATR.

| Binding cap | Setups |
|---|---:|
| `max_sl_pips` | 830 (51%) |
| `max_sl_atr` | 786 (49%) |

Accepted stop distances under S1: median **31.5 pips** (1.20 ATR), range 8.3-59.8 pips.

## Four stop models are worth fewer than four tests

**M_eff = 1.32** over 1,528 setups with all four models available.

This is D-012's finding again, one layer down: the Phase 11 bake-off measured four
order-block definitions at 1.77 effective tests rather than 4, and SPEC 16.6 asks
for the same paired-variant treatment of S1-S4. The number below is what a
multiple-testing correction over the stop models must use.

| Pair | n | Identical price | Within 0.05 ATR |
|---|---:|---:|---:|
| sweep_extreme / structural_swing | 1,545 | 61.2% | 62.5% |
| sweep_extreme / order_block | 1,528 | 14.6% | 20.2% |
| sweep_extreme / atr | 1,545 | 0.0% | 6.9% |
| structural_swing / order_block | 1,585 | 22.8% | 27.1% |
| structural_swing / atr | 1,616 | 0.0% | 8.2% |
| order_block / atr | 1,585 | 0.0% | 4.5% |

**Both columns, because exact agreement understates redundancy** (D-012 §2). S1
anchors on the sweep extreme and S2 on the lowest low of a window that *starts* at
the sweep extreme, so they are the same number unless some bar went lower — and
when one did, they are economically one model and arithmetically two.

The correlation is computed on each model's ATR-normalised distance from the
**break bar's close**, which no stop model produced. Centring on the per-observation
mean across the models being compared would pin the average pairwise correlation at
`-1/(k-1)` — a number about the centring, not about the models (D-012 §3a).

## Where setups die (SPEC 19)

Under S1, limits off. The chain runs cheapest-and-most-structural first, so a
rejection names a property of *this setup* before it names a property of the book
it happened to arrive into: `SL_TOO_WIDE` would never have been fine,
`RISK_LIMIT_POSITIONS` would have been fine tomorrow.

| Reason | Count |
|---|---:|
| `SIZE_BELOW_MIN` | 800 |
| `SL_TOO_WIDE` | 146 |
| `PRICE_THROUGH_STOP` | 71 |
| `SL_TOO_TIGHT` | 66 |

### Limits on versus limits off (SPEC 18.9)

| Outcome | Limits off | Limits on |
|---|---:|---:|
| ACCEPTED | 533 | 4 |
| LIMITS | 0 | 529 |
| SIZING | 800 | 800 |
| STOP | 212 | 212 |

**The limits-on column is dominated by `RISK_LIMIT_POSITIONS`, and that is an
artefact of this phase, not a result.** Nothing closes a trade until Phase 14, so
the ledger fills to `max_open_positions` and stays there for the rest of the year.
The comparison SPEC 18.9 actually asks for — *"a strategy that is only profitable
with a daily loss limit engaged is a strategy with a fragility the limit is
hiding"* — needs an equity curve, and is a Phase 14 deliverable. What this column
does establish is that the switch works and that turning the limits off leaves the
*strategy's* own rejections in place rather than turning them off too.

## The smallest account that can trade this

SPEC 18.2's lot-granularity rejections are a function of equity: the same setup at
the same stop distance is tradable on one account and not on another. That makes
this the one number in the risk layer that depends on a value chosen for reporting,
so it is swept rather than quoted. Measured against the 1,333
stop distances whose stop cleared the SPEC 16.3 caps, each sized under its own symbol's spec and the counts pooled afterwards.

**The population is the cap-passing setups, not the ones that sized.** Feeding it
the accepted setups instead would fix the denominator at one equity — the very
number the sweep varies — and report a curve that cannot fall below the default.

Restricted to the 4 sizeable symbols; the 6 blocked ones cannot be swept at any equity.

### 6 of the 10 symbols cannot be sized at all, and it is not the account size

| Symbol | quote | reached sizing | sized | why not |
|---|---|---:|---:|---|
| AUDUSD | USD | 150 | 150 | — |
| EURGBP | GBP | 156 | 0 | **no GBP->USD rate** (SPEC 18.2) |
| EURJPY | JPY | 130 | 0 | **no JPY->USD rate** (SPEC 18.2) |
| EURUSD | USD | 120 | 120 | — |
| GBPJPY | JPY | 122 | 0 | **no JPY->USD rate** (SPEC 18.2) |
| GBPUSD | USD | 86 | 86 | — |
| NZDUSD | USD | 177 | 177 | — |
| USDCAD | CAD | 133 | 0 | **no CAD->USD rate** (SPEC 18.2) |
| USDCHF | CHF | 136 | 0 | **no CHF->USD rate** (SPEC 18.2) |
| USDJPY | JPY | 123 | 0 | **no JPY->USD rate** (SPEC 18.2) |

**Every blocked symbol is one whose QUOTE currency is not the account
currency.** Sizing needs the quote->USD rate to convert a
stop distance into money, SPEC 18.2 says its absence *blocks the inclusion of
any symbol*, and there is no rate series — Q1 is still open. The four that size
are exactly the USD-quoted pairs, where the rate is 1 by
identity. This is the rule working as written; it is not a defect and not a
function of `starting_equity`.

**It does not look like that in the rejection log, and that is a defect.**
`trade.evaluate` catches `MissingConversionRate` and returns
`RiskReject.SIZE_BELOW_MIN`, so all 6 symbols report a lot-granularity failure
they did not have. Reading that table without running the sizing call directly
leads to the wrong diagnosis — *"the account is too small"* — and to the wrong
fix. **This is D-019 §1 recurring**: a rejection reason names the gate that
refused, not the reason it refused. SPEC 19's catalogue has no code for a
missing conversion rate, so adding one is a specification change and is left
alone here rather than made silently.

**What it costs now**: the pre-registration's cross-sectional criterion — *"≥ 6
of 10 symbols with positive expectancy, same parameters"* (§3) — is
**unevaluable** until Q1 supplies a rate series, because only 4 symbols can
carry a sized trade. Everything below is measured on those four.

| Equity | Sizeable | `SIZE_BELOW_MIN` | `SIZE_UNDER_RISK` | Median lots |
|---:|---:|---:|---:|---:|
| USD 500 | 13% | 462 | 0 | 0.01 |
| USD 1,000 | 61% | 209 | 0 | 0.01 |
| USD 2,000 | 100% | 0 | 0 | 0.02 |
| USD 5,000 | 100% | 0 | 0 | 0.06 |
| USD 10,000 | 100% | 0 | 0 | 0.11 |
| USD 25,000 | 100% | 0 | 0 | 0.28 |
| USD 50,000 | 100% | 0 | 0 | 0.56 |
| USD 100,000 | 100% | 0 | 0 | 1.12 |

**Smallest swept account sizing 95% of this stream: USD 2,000.**

Per symbol, since each is sized under its own spec:

| Symbol | smallest account sizing 95% |
|---|---:|
| AUDUSD | USD 2,000 |
| EURUSD | USD 2,000 |
| GBPUSD | USD 2,000 |
| NZDUSD | USD 2,000 |

**That figure is a function of the stop distances**, whose median here is
31.5 pips against the fixture's 23.6 — so the
stops widened by roughly a third and the answer did not move. The scale table
below is kept because it says how far it *would* have to move:

| Stop distances | Median stop | Smallest account sizing 95% |
|---:|---:|---:|
| x1 | 31.5 pips | above the sweep |
| x1.5 | 47.2 pips | above the sweep |
| x2 | 63.0 pips | above the sweep |
| x3 | 94.5 pips | above the sweep |

The *shape* of that relationship is what transfers — it is a property of the lot
grid and of SPEC 18.2's arithmetic, not of returns. The row that applies is
whichever one matches the real ATR distribution, and that is not known yet.

Note the `SIZE_UNDER_RISK` column: it is zero at every equity and every scale, and
provably so — see the dead-limits table below.

## Three limits that cannot fire, and one model that cannot arm

Implementing SPEC 18 exactly as written turned up four defaults that are
unreachable rather than merely unused. None has been changed: they are FROZEN or
ABLATION parameters, and `BACKTEST_PROTOCOL.md` §10.2 forbids moving one to make a
result appear. Each needs an explicit decision. See D-014.

| # | What | Why it cannot fire |
|---|---|---|
| 1 | `risk.min_realised_fraction` = 0.5 | `lots = k x step` with `raw < (k+1) x step` gives a realised fraction above `k/(k+1) >= 1/2` for **every** lot grid. 0 fires in 400,000 randomised sizings; worst accepted fraction 0.500081. It does not catch SPEC 18.2's own worked example, which lands at 0.52 |
| 2 | `risk.max_total_open_risk_pct` = 1.5% | `max_open_positions` (3) x the top of the tunable band (0.50%) is exactly 1.5%, which does not breach 1.5%. At the default 0.35% the ceiling is 1.05%. The position count always binds first |
| 3 | `risk.max_sl_atr` = 2.5, **under S4 only** | S4's stop is 1.5 ATR by construction, so it can never reach 2.5 ATR. Under S1-S3 the cap is live |
| 4 | `tp.model = partial_ladder` (T3) | T3's `tp_1` is the ladder's first rung at **1R**, and `tp.min_rr` is 1.5. `rr` is 1.0 on every setup, so T3 is rejected always. It passes at exactly one of the three declared ablation values (1.0) |

A fifth is not a dead limit but the same species of finding: **T4 is exempt from
the RR gate**, because it has no fixed target to measure. So T4 accepts setups
T1-T3 reject, and SPEC 17.7's *"paired T1-T4 variants on a shared setup stream"*
does not describe four shared streams. Any comparison has to say so.

| Target model | Gate reachable at the default `min_rr` = 1.5 |
|---|---|
| fixed_r | yes — `r_multiple` 2.0 >= 1.5 |
| opposing_liquidity | depends on where the liquidity is |
| partial_ladder | **no** — 1R against a 1.5 floor |
| structure_trail | n/a — exempt, which is the finding |

## The stop moves at fill, under exactly one model

S1-S3 anchor the stop to structure — a sweep extreme, a swing low, an order block
edge — so the fill price cannot move it. **S4 anchors on the entry price**, and for
a MARKET order the planned entry price is a placeholder for `C_b`, which SPEC 15.3
forbids using because the fill is next bar's open.

Three consequences, all of them in the code and none of them in SPEC 16, which
treats the stop as fixed once planned:

1. `arm` must compute the entry price **before** the stop. Phase 12 computed the
   stop first, correctly, because only S1 existed.
2. Under S4 a limit can never be `PRICE_THROUGH_STOP` — the stop is placed a fixed
   distance from the price by construction. A zero in that rejection column means
   *impossible*, not *did not happen*.
3. The stop must be re-derived at fill, which SPEC 16.5 already requires the caps
   to be re-run at (*"Both checks are required"*) for the unrelated reason that
   the spread moves.

**The effect is no longer zero, and its size is Phase 12's number.** How far the
S4 stop moves between arming and filling is exactly the close-to-open gap, which
D-025 measured on this same data at a **mean 0.0156 ATR** (median non-zero 0.0049,
95th percentile 0.0353, largest 3.9978). On the fixture it was 0.0000 by
construction.

**This report does not measure it itself** — the pre-trade chain never resolves a
fill, so the number above is cited from Phase 12 rather than recomputed here. What
it means for S4 is that the stop, and therefore the risk denominator every R is
divided by, is set from a price the plan did not know. SPEC 16.5 already requires
the caps to be re-run at fill for the unrelated reason that the spread moves; that
re-run is now load-bearing for a second reason.

## What this report does NOT establish

1. **That any limit binds at a useful rate.** Every loss limit is defined on closed
   PnL and nothing closes here. The scenarios prove correctness; Phase 14 measures
   incidence.
2. **Anything about returns.** No trade is opened, closed, or costed. Sizing without
   an exit produces lots, not PnL.
3. **That the stop-distance distribution transfers.** It is what the detectors
   produce meeting noise. The account sweep transfers better than most things here
   because it depends on the distribution's *shape*, but the shape will move.
4. **The spread limits.** `sl.buffer_spread_mult`, `risk.max_spread_pips` and
   `risk.max_spread_pct_of_sl` are implemented, scenario-tested, and **inert** —
   there is no spread series until Q2. Same for `symbol.stops_level`, which is 0
   points because 0 is the only value that cannot invent a rejection (Q1).
5. **The FX conversion on anything but EURUSD.** SPEC 18.2's conversion is
   implemented and its absence **blocks** a symbol rather than defaulting to 1.0 —
   but every number here is EURUSD on a USD account, where the rate is 1 by
   identity. The 40%-error case SPEC 18.2 warns about is a JPY-pair case and needs
   the rate series.
6. **The correlation cap's realised effect.** `correlation_clusters` is
   implemented and scenario-tested including SPEC 18.7's directional
   equivalence. The universe is now ten symbols, so cluster membership *is*
   measurable — but not here: nothing closes a trade until Phase 14, so the
   ledger fills to `max_open_positions` and stops being informative (see the
   limits caveat above). It is a Phase 14 measurement, not a blocked one.
7. **That `M_eff` holds outside this split.** Recomputed here on real in-sample
   bars, and it should not be recomputed out of sample to check — that spends
   budget on a nuisance parameter (protocol §7).

## Verdict: PASS

Every SPEC 18.4 limit fires on its trigger and declines on its near miss
(18/18); sizing is a pure function of its declared inputs,
asserted on the signature rather than on behaviour; the realised risk distribution
has no mass above nominal; and the four stop models are worth
1.32 tests rather than 4.

Four FROZEN or ABLATION defaults were found to be unreachable rather than unused,
and none was changed. That is the phase's substantive output.
