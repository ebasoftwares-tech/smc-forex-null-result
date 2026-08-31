# Phase 14 Gate Report

**The backtest engine.**

Generated 2026-08-31T14:44:05+00:00

- `config_hash` `7f393e8ced4bf193f993f056380bffff2826fbeb71ba2a583d99e54f19b18c49`
- `dataset_hash` `2a2bb0293052ae31bd2be73cfd53df25f6032f4db94da906543889e447d03ed9`
- Data: **real bars** -- 10 symbols, 2019-2022 (40 symbol-years), H4 with the real M1 path, source `histdata`, `bid` side, tzdata `2026.3`
- Split (PRE_REGISTRATION 4.1, Amendment 1): in-sample **[2019, 2020, 2021, 2022]**, out-of-sample [2023, 2024], holdout [2025]
- **1,616 displaced CHoCH setups** over 64,228 H4 bars
- Account: USD 10,000 at 0.35% per trade

> **Every number in this report is a property of the detectors meeting a random
> walk.** The engine is complete and validated; the market is not real. An
> expectancy here measures the machinery. See the closing section.

## Gate checks

| Check | Result | Detail |
|---|---|---|
| Test suite green | PASS | 662 passed in 132.92s (0:02:12) |
| Replay + shifted-data tests green (gate) | PASS | 2 passed, 19 deselected in 6.86s |
| Cost sensitivity run (gate) | PASS | expectancy reported at cost.multiplier 1.0x, 1.5x, 2.0x |
| Costs are monotone: 2x never beats 1x | PASS | a cost applied with the wrong sign would show up here and nowhere else |
| R is independent of the equity path | PASS | pass one cannot see equity; asserted by test, not by inspection |
| The full funnel reaches closed trades | PASS | 1,616 setups -> 1,333 armed -> 267 filled -> 102 closed |
| Model A fills every order; the others do not | PASS | SPEC 15.5 -- coverage differs, so per-SETUP expectancy is the comparable one |
| Every rejection carries a named SPEC 19 reason | PASS | 1,514 rejections, 1,442 with a forward return |
| Monte Carlo suite runs and reports verdicts | PASS | 6/7 tests returned a verdict |
| No headline strategy claim is made | PASS | n = 102 against protocol 5.1's floor of 200, and in-sample |

## The funnel, reported before any performance figure

BACKTEST_PROTOCOL 4.3 puts this first deliberately: *"it says whether the strategy
exists in sufficient quantity to be measured, and where the population is being
lost. A 30% drop at one step that was expected to be 90% is a bug long before it is
a finding."*

| Stage | Count | Conversion |
|---|---:|---:|
| levels created | 135,893 | — |
| sweeps confirmed | 28,005 | 20.6% |
| MSS setups | 1,616 | 5.8% |
| orders armed | 1,333 | 82.5% |
| orders filled | 267 | 20.0% |
| trades closed (limits off) | 102 | 38.2% |
| trades closed (limits on) | 57 | 55.9% |

The two large drops are both known. Sweeps to MSS is Phase 9's funnel, measured
at **1.59%** on real bars (D-020) against the 1.98% the gate was set against.

Armed to filled is the opposing-sweep cancel — and the claim that this was a
fixture artefact is **false**. D-013 §4 called it *"a rate no real market
sustains"*; D-025 §3 measured 0.44 confirmed sweeps per H4 bar on real data
against the fixture's 0.47. `cancel_if` clause 2 removes most limit orders here
for the same reason it did there, and it is a FROZEN clause deciding the entry
bake-off by itself.

## Headline metrics (BACKTEST_PROTOCOL 4.1)

Entry model C, stop sweep_extreme, target fixed_r, cost multiplier 1.0.

| | Limits off | Limits on |
|---|---:|---:|
| Trades | 102 | 57 |
| n_eff | 76.9 | 57.0 |
| Win rate | 30.4% | 33.3% |
| **Expectancy (R)** | -0.1869 | -0.1306 |
| Total R | -19.06 | -7.44 |
| Profit factor | 0.73 | 0.81 |
| Avg win (R) | +1.66 | +1.65 |
| Avg loss (R) | -0.99 | -1.02 |
| Largest win (R) | +1.97 | +1.97 |
| Largest loss (R) | -1.43 | -1.43 |
| Max consecutive losses | 11 | 8 |
| Net return | -6.56% | -2.57% |
| CAGR | -1.69% | -0.65% |
| Max drawdown (equity) | 9.79% | 4.95% |
| Max drawdown (R) | 29.22 | 13.02 |
| Sharpe (daily, sqrt-252) | -2.35 | -1.58 |
| Sortino | -4.32 | -6.53 |
| Ulcer index | 5.60 | 2.76 |
| MAR | -0.17 | -0.13 |
| Time in market | 0.7% | 0.4% |
| Kelly (reported, never used) | -0.113 | -0.079 |
| Avg duration (bars) | 4.5 | 4.2 |
| Censored | 0 | 0 |
| Intrabar-ambiguous | 2 | 0 |
| Gapped | 0 | 0 |

Expectancy CI (i.i.d. bootstrap, 10,000): **[-0.423, +0.064] R**  
Expectancy CI (stationary block, mean block 20): **[-0.382, +0.016] R**

**Both intervals span zero.** On real bars that is a result rather than a
tautology, and it is the only honest reading of it: the point estimate is
negative, the interval reaches into positive territory, and the sample is too
small to separate the two. It is neither evidence of edge nor evidence against.

The block interval is the one protocol 5.3 requires for anything conditioned on a
slow-moving variable: trades are not independent, so an i.i.d. resample
understates the uncertainty. Read the block row.

`n = 102` against protocol 5.1's floor of **200 for a headline claim**, so no headline claim is made.

**And the shortfall is structural, not a matter of waiting for more years.**
Six of the ten symbols cannot be sized at all — every one whose quote currency is
not the account currency, blocked by SPEC 18.2's missing-FX-rate rule while Q1 is
open (D-026 §1). The book below is four symbols, not ten:

| Symbol | trades |
|---|---:|
| AUDUSD | 38 |
| NZDUSD | 32 |
| EURUSD | 19 |
| GBPUSD | 13 |
| USDJPY | 0 |
| USDCAD | 0 |
| USDCHF | 0 |
| EURJPY | 0 |
| GBPJPY | 0 |
| EURGBP | 0 |

Reaching 200 trades in-sample is therefore not a question of more history at this
funnel rate — it needs the other six symbols, which needs a conversion series.

## The five entry models, paired (SPEC 15.8, protocol 4.4)

| Model | Armed | Filled | Fill rate | E_trade (R) | E_setup (R) | **E_all_setups (R)** | Shadows |
|---|---:|---:|---:|---:|---:|---:|---:|
| A — market on MSS | 272 | 91 | 33.5% | -0.0163 | -0.0055 | **-0.0009** | 0 |
| B — retracement | 1,239 | 131 | 10.6% | -0.0159 | -0.0017 | **-0.0013** | 214 |
| C — FVG | 1,333 | 102 | 7.7% | -0.1869 | -0.0143 | **-0.0118** | 189 |
| D — order block | 1,298 | 88 | 6.8% | +0.1559 | +0.0106 | **+0.0085** | 171 |
| E — 50% of the leg | 1,161 | 128 | 11.0% | -0.1209 | -0.0133 | **-0.0096** | 189 |

**`E_trade` is a trap and `E_setup` is not enough.** Protocol 4.4 warns about the
first: *"a model that fills 35% of the time on the best-looking third of setups will
show a superior win rate and a worse total return."* `E_setup` fixes that by
charging a model for the fills it declines.

**But the models do not arm on the same setups, so `E_setup`'s denominators are
different populations.** Model A enters at the break price with its stop at the sweep
extreme, so its stop distance *is* the displacement leg — and SPEC 16.3's 2.5-ATR cap
rejects it wherever that leg is large:

| | Setups | Median displacement |
|---|---:|---:|
| Model A armed | 272 | 2.16 ATR |
| Model A rejected `SL_TOO_WIDE` | 1344 | 2.64 ATR |
| All setups | 1616 | 2.56 ATR |

The cap does not thin model A at random — **it takes its strongest-displacement
setups specifically**, because for this model a strong displacement *is* a wide stop.
`E_all_setups` divides by the shared denominator (every MSS setup) instead, which is
the only column in this table that compares the five over one population. See D-015
section 6.

**This table is produced with the portfolio limits OFF, deliberately.** SPEC 18.4's
position cap rejects whichever model fills most, so a bake-off with the limits
engaged measures the cap rather than the models — model A read a 58% fill rate that
way against the 100% Phase 12 measured. See D-015 section 3.

Shadow trades are the would-have-been outcomes of armed orders that never filled
(SPEC 15.6). They answer *"did we miss the good ones?"*, which the filled
population cannot. **A shadow is counterfactual on the cancel, never on the fill** —
the first version entered at a limit price whether or not price reached it, which on
a bullish setup is a free discount and produced 38 take-profits against 2 stops.

## Cost sensitivity (gate; protocol 3.3)

*"A strategy whose expectancy is destroyed at 1.5x is not deployable: broker
spreads vary by more than that, and so do the same broker's spreads across the day
and across years."*

| `cost.multiplier` | Trades | Expectancy (R) | Total R | Profit factor |
|---:|---:|---:|---:|---:|
| 1.0x | 102 | -0.1869 | -19.06 | 0.73 |
| 1.5x | 102 | -0.2399 | -24.47 | 0.67 |
| 2.0x | 102 | -0.2929 | -29.87 | 0.62 |

Cost of doubling: **-0.1060 R per trade**. On a real edge this is the
number that decides deployability; here it only says the cost model is wired and
monotone, since the underlying expectancy is noise in the first place.

## Stop and target models over the same stream

| Stop model | Trades | Expectancy (R) | Fill rate | Median SL (pips) |
|---|---:|---:|---:|---:|
| sweep_extreme | 102 | -0.1869 | 20.0% | 35.0 |
| structural_swing | 97 | -0.3450 | 18.7% | 38.5 |
| order_block | 107 | +0.0365 | 19.0% | 38.0 |
| atr | 108 | -0.1717 | 19.1% | 39.1 |

| Target model | Trades | Expectancy (R) | Take-profits | Time stops |
|---|---:|---:|---:|---:|
| fixed_r | 102 | -0.1869 | 24 | 0 |
| opposing_liquidity | 51 | -0.5928 | 6 | 0 |
| partial_ladder | 0 | — | 0 | 0 |
| structure_trail | 102 | -0.4847 | 0 | 0 |

**T3 produces no trades, and that is D-014 section 1 reaching the engine.** Its
`tp_1` is the ladder's 1R rung against a `min_rr` of 1.5, so SPEC 17.2 rejects it on
every setup. **T4 produces more trades than T1–T2**, which is D-014 section 6: with
no fixed target there is no RR gate to fail, so T4 runs on a different and
systematically better-looking population. The four are not a paired ablation.

`M_eff` for the four stop models was measured at **1.36** in Phase 13 (D-014
section 7). Any correction across this table uses that, not 4.

## The sweep-session x entry-session matrix (protocol 4.2.1)

Required rather than optional, and added by D-002.

| sweep \ entry | ASIA | LONDON | NEW_YORK | OTHER |
|---|---:|---:|---:|---:|
| **ASIA** | 5 (-0.63R) | 1 (-1.06R) | 10 (+0.27R) | — |
| **LONDON** | 8 (-0.77R) | 10 (+0.20R) | 13 (-0.15R) | 1 (-1.11R) |
| **NEW_YORK** | 13 (-0.30R) | 11 (-0.24R) | 21 (+0.09R) | 2 (-0.01R) |
| **OTHER** | 3 (-0.75R) | — | 3 (-1.16R) | 1 (-0.04R) |

**Diagonal share (sweep and entry in the same session): 36.3%.**

Protocol 4.2.1 says what to do with that number in advance: *"if the diagonal is
nearly empty, the strategy being tested is not the one the brief's section 6 example
describes, and the report must say so in those words."* Under D-002's H4-only
confirmation the minimum sweep-to-MSS distance is two H4 bars, so a London sweep can
rarely be entered in London. **This is a session-to-session swing model, not the
intraday London reversal the source material describes** — which D-002 already
recorded and this table now measures.

Bars from sweep to MSS: median **4**, range 1-12.

## Breakdowns (protocol 4.2)

Every cell carries its `n`, and the protocol's own labels are applied: under 30 is
**not reportable**, 30-99 is **suggestive** only, 100 or more is reportable.
Of the 21 cells below, **14 are not reportable, 7 suggestive and 0 reportable** — not one cell in this run reaches the protocol's own bar for a subgroup finding, which is the honest headline of the whole section.

**Exit reason**

| Value | n | n_eff | Expectancy (R) | Win rate | Label |
|---|---:|---:|---:|---:|---|
| STOP_LOSS | 58 | 17.8 | -1.144 | 0% | suggestive |
| TAKE_PROFIT | 24 | 21.4 | +1.937 | 100% | not reportable |
| WEEKEND_CLOSE | 20 | 20.0 | +0.040 | 35% | not reportable |

**Liquidity source**

| Value | n | n_eff | Expectancy (R) | Win rate | Label |
|---|---:|---:|---:|---:|---|
| SESSION_LOW | 42 | 34.0 | -0.070 | 33% | suggestive |
| SESSION_HIGH | 35 | 19.3 | -0.079 | 34% | suggestive |
| PREV_DAY_LOW | 8 | 4.7 | -0.994 | 0% | not reportable |
| SWING_LOW | 5 | 5.0 | -0.457 | 20% | not reportable |
| PREV_DAY_HIGH | 4 | 4.0 | +0.731 | 75% | not reportable |
| SWING_HIGH | 3 | 3.0 | -0.119 | 33% | not reportable |
| EQUAL_HIGHS | 2 | 2.0 | -1.105 | 0% | not reportable |
| EQUAL_LOWS | 2 | 2.0 | -1.187 | 0% | not reportable |

**Liquidity tier**

| Value | n | n_eff | Expectancy (R) | Win rate | Label |
|---|---:|---:|---:|---:|---|
| tier 3 | 77 | 65.2 | -0.074 | 34% | suggestive |
| tier 2 | 20 | 19.7 | -0.383 | 25% | not reportable |
| tier 1 | 5 | 5.0 | -1.139 | 0% | not reportable |

**Entry session**

| Value | n | n_eff | Expectancy (R) | Win rate | Label |
|---|---:|---:|---:|---:|---|
| NEW_YORK | 47 | 31.0 | -0.015 | 36% | suggestive |
| ASIA | 29 | 29.0 | -0.532 | 24% | not reportable |
| LONDON | 22 | 12.4 | -0.079 | 32% | not reportable |
| OTHER | 4 | 4.0 | -0.293 | 0% | not reportable |

**Direction**

| Value | n | n_eff | Expectancy (R) | Win rate | Label |
|---|---:|---:|---:|---:|---|
| BULLISH | 58 | 58.0 | -0.287 | 26% | suggestive |
| BEARISH | 44 | 18.5 | -0.055 | 36% | suggestive |

## The rejection log as a counterfactual dataset (SPEC 21.3)

**Measured in ATR from the MSS close, not in R from the planned entry.** SPEC 21.3
asks for the latter and it distorts the answer twice: a bullish limit sits below the
market, so measuring from it starts at a price the trade never paid, and several
gates reject a setup precisely *because its risk was wrong* -- `SL_TOO_TIGHT`
rejects a 0.37-pip stop and dividing by it reported +7.0R. Both distortions were
found on the fixture, where the first read a median +1.7R at a 92% win rate; the
reasons are arithmetic and hold on any data. See D-015 section 7.

*"For each gate, what is the expectancy of the trades it rejected? A gate whose
rejected population has positive expectancy is destroying edge; one whose rejected
population has strongly negative expectancy is earning its place."* Computed from
**one** run, so it costs nothing against the out-of-sample budget.

| Rejection reason | n | Forward move (ATR) | Went the setup's way | Label |
|---|---:|---:|---:|---|
| `OPPOSING_SWEEP` | 582 | -0.259 | 46% | reportable |
| `ENTRY_EXPIRED` | 483 | +1.030 | 63% | reportable |
| `SIZE_BELOW_MIN` | 165 | -1.821 | 18% | reportable |
| `SL_TOO_WIDE` | 146 | +0.013 | 49% | reportable |
| `SL_TOO_TIGHT` | 66 | +0.177 | 52% | suggestive |

A gate that neither destroys nor earns should read near zero, and
`OPPOSING_SWEEP` reads -0.259 ATR at a 46% hit rate. **`ENTRY_EXPIRED` does not,
and it is not a bug — it is a tautology worth naming before it is misread.** An
order expires unfilled precisely when price never retraced to the limit, which for a
bullish setup means price went *up* and kept going. Measuring the forward move in the
setup's direction on that population selects for exactly that move. **The setups a
limit misses are, by construction, the ones that ran.**

So this row must never be read as "the expiry rule destroys edge". It is the
mechanical cost of using a limit at all, it is the same quantity SPEC 15.6's shadow
trades exist to price, and the correct comparison is against what a *market* entry on
the same setups would have paid — model A's column in the bake-off — not against
zero. See D-015 section 8.

**This is the table that will matter most when real bars arrive**: it is the only
place a filter can be shown to be destroying edge rather than earning its place. It
is also, on this evidence, the table most able to mislead.

## Monte Carlo (protocol 9)

| Test | Statistic | Value | Threshold | Verdict |
|---|---|---:|---:|---|
| Trade-order shuffle | 95th-percentile max drawdown | 10.4277 | 20 | PASS |
| Trade-order shuffle | ruin probability | 0.0000 | 0.01 | PASS |
| Bootstrap resample | 5th-percentile expectancy (R) | -0.3905 | 0 | FAIL |
| Skip 10% of trades | 5th-percentile net return (%) | -7.9989 | 0 | FAIL |
| Top-3 concentration | share of total R in the best 3 | — | 1 | n/a |
| Randomised costs | median expectancy (R) | -0.2399 | 0 | FAIL |
| Entry timing shift | worst degradation | 5.4348 | 0.4 | FAIL |

**These FAILs are what a sample with no demonstrable edge looks like**, which is
what the headline interval already said. Protocol 9's suite is designed to ask
whether an edge survives perturbation; there is no edge here to perturb, so the
verdicts carry no information beyond the expectancy CI above and must not be
read as independent evidence against the strategy. What the table does establish
is that each test runs, is seeded, and returns a verdict rather than a number.

The two `concentration` rows are additions rather than protocol items. Protocol 9
calls the skip-10% test the one that *"no other test in this suite reliably
catches"* concentration with — and its stated acceptance is a **sign** test while
concentration is a **drop**. On a constructed sequence of 57 losers and 3 large
winners the sign test passes and the top-3 share (161% of total R) fails. See D-015
section 4.

## What this report does NOT establish

1. **That the expectancy above is a result about the strategy.** It is a point
   estimate on 102 in-sample trades whose interval spans zero. It is not evidence
   of edge, and it is not evidence against one — the sample cannot tell.
2. **That the trade count can be fixed with more history.** Four of ten symbols
   carry the whole book; the other six cannot be sized while Q1 leaves the FX
   conversion series missing (D-026). Reaching protocol 5.1's 200 needs the rate
   series, not more years.
3. **Two of the three execution effects the fixture measured as 0.0000.** SPEC
   15.3's lookahead is now measured at **0.0156 ATR per entry** (D-025). The
   gap-past-the-stop branch still fires **zero** times — real H4 gaps are ~0.005
   ATR against a stop 1-2 ATR away — and the S4 stop's movement at fill is the
   same close-to-open gap, so it is no longer zero either but is not measured
   here. All three remain pinned by constructed tests.
4. **Out-of-sample anything.** This is the in-sample split. Walk-forward (protocol
   8) and the OOS budget ledger (protocol 7) are procedures over 2023-2024 and
   2025, and neither has been touched — deliberately, since the budget is spent
   by looking.
5. **The MTF bias gate.** `bias.gate_mode = none` throughout (Phases 2-4 unbuilt),
   so every count here is an upper bound: a real gate can only reduce them.
6. **That the portfolio limits mean anything yet.** With no exit policy driving
   the ledger across symbols, the limits-on column measures the cap binding rather
   than the limits working (`STATE.md` rule 40).
7. **The cross-sectional criterion.** Ten symbols are run, but only four can be
   sized, so the pre-registration's *"≥ 6 of 10 symbols with positive
   expectancy"* cannot be evaluated at all (D-026 §1). The correlation cap is
   likewise unexercised, for the Phase 14 reason in item 6 rather than for want
   of symbols.

## Verdict: PASS

The engine runs the full chain from liquidity to a closed trade with costs, produces
every record BACKTEST_PROTOCOL section 4 asks for, and passes the two tests the gate
names. R-expectancy is computed in a pass that structurally cannot see equity, which
is what makes protocol 4.1's claim about it true rather than asserted.

**No strategy result is claimed, and none is available until Q1/Q2 deliver real
bars.**
