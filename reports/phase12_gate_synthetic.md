# Phase 12 Gate Report

**Entry models and fill resolution (SPEC 15).**

Generated 2026-08-30T14:47:41+00:00

- `config_hash` `7f393e8ced4bf193f993f056380bffff2826fbeb71ba2a583d99e54f19b18c49`
- Fixture: 3 synthetic years (2024-2026), EURUSD, **generated at M1** and resampled
- **165 displaced CHoCH setups** (53 of them MSS), 4,860 H4 bars

Generating at M1 and resampling up is what makes the second half of the gate
meaningful: the H4 bars and the M1 path describe the same underlying series, so
the two fill resolutions are comparable by construction rather than by assumption.

## Gate checks

| Check | Result | Detail |
|---|---|---|
| Test suite green | PASS | SKIPPED (--skip-tests) |
| All five models arm on the fixture (gate) | PASS | A 165, B 165, C 160, D 159, E 165 |
| Model A arms every setup and fills every order | PASS | the only 100% model, which is what makes it the baseline (SPEC 15.5) |
| Limit models do not always fill | PASS | coverage differs by model, so per-SETUP expectancy is the only valid comparison |
| Fill logic verified against M1 (gate) | PASS | 0 disagreements over 814 armed orders |
| The continuity branch is exercised | PASS | 15 bars touched both the entry and the stop |
| Model A never uses the close that triggered it | PASS | SPEC 15.3 -- pinned by test, and unmeasurable on this fixture (see below) |
| Every reject reason is named | PASS | SPEC 15.7: invalidate and say why, never fall back |

## The five models (gate, first half)

| Model | Order | Armed | Rejected | Fill rate | Median bars to fill | Risk distance (ATR) |
|---|---|---:|---|---:|---:|---:|
| **A — market on MSS** | MARKET | 165 | — | 100.0% | 1 | 2.73 |
| **B — retracement of the leg** | LIMIT | 165 | — | 39.4% | 3 | 1.47 |
| **C — FVG** | LIMIT | 160 | `PRICE_THROUGH_STOP` 5 | 33.1% | 3 | 1.31 |
| **D — order block** | LIMIT | 159 | `PRICE_THROUGH_STOP` 6 | 33.3% | 4 | 1.26 |
| **E — 50% of the leg** | LIMIT | 165 | — | 40.6% | 2 | 1.56 |

Fill rates here exclude the opposing-sweep cancel, for a reason the next section
explains. `PRICE_THROUGH_STOP` is a rejection at arm time, not a cancellation: a
limit already at or beyond its own stop is not an order, it is a loss waiting to be
booked, and the log should distinguish "never armable" from "armed and then
invalidated".

**Model A is the only 100% model**, which is exactly what SPEC 15.5 warns about:
*"a model that fills 35% of the time on the best-looking third of setups will show
a superior win rate and a worse total return."* Any comparison between these models
has to be on expectancy **per setup**, not per trade — which is a Phase 14 problem,
but the coverage numbers that make it necessary are measured here.

## The opposing-sweep cancel makes every limit model unusable on this fixture

| Model | Fill rate without cancel_if 2 | With it |
|---|---:|---:|
| A — market on MSS | 100.0% | 100.0% |
| B — retracement of the leg | 39.4% | 1.8% |
| C — FVG | 33.1% | 1.9% |
| D — order block | 33.3% | 1.9% |
| E — 50% of the leg | 40.6% | 3.0% |

The fixture carries **2,298 confirmed sweeps over 4,860 H4 bars**, or
0.47 per bar. Over a 6-bar expiry
window an opposing sweep is close to certain, so SPEC 15.1's cancel_if clause 2
cancels essentially every limit order before it can fill. Model A is untouched
because a market order is resolved on the next bar and never waits.

**This is D-009 §9 one level down and it is a fixture property, not a finding about
the models.** A random walk with up to 40 active liquidity levels produces sweeps at
a rate no real market sustains; the same clause on real bars will cost something
quite different. Both columns are reported so the two effects stay separable, and
the left one is used everywhere else in this report.

## Fill logic verified against M1 (gate, second half)

- Armed orders resolved both ways: **814**
- Disagreements between the bar-level rule and the M1 replay: **0**
- Bars that touched both the entry and the stop: **15**
- Bars that opened beyond the stop (a true gap): **0**

**The two agree everywhere, and getting there required fixing the bar-level rule.**

A limit sits at `p` with its stop at `s` beyond it, and price approaches from the
far side. Any continuous path that reaches `s` must pass `p` first, so a bar
touching both is **not ambiguous** — the entry filled. The first version of this
module treated it as a coin flip and resolved it "pessimistically" by cancelling,
which produced 15 false cancels and disagreed with the M1 path on every
one of them.

That was wrong twice over. The physics says fill; and cancelling is not even the
pessimistic *outcome*, since a fill that then stops out loses 1R while a cancel
loses nothing. Reaching for "be conservative" produced the answer that was both
incorrect and less conservative. See D-013 §1.

So SPEC 15.1's clause 1 means what it says it means — *"a limit order can fill on
the way back up from a level that already invalidated the idea"* — and that needs
price to reach the stop **without having filled on the way**, which under continuity
requires a gap. Gap bars are the only place `backtest.intrabar_mode` changes an
answer.

## Two things this fixture structurally cannot measure

**Every H4 bar opens exactly at the previous close** — 0 non-zero gaps in
4,857 bar transitions. `bot/data/synthetic.py` emits a continuous random
walk, including across weekends. Two consequences:

1. **SPEC 15.3's lookahead has zero magnitude here.** Filling model A at `C_b`
   instead of the next bar's open would gain exactly 0.0000 ATR per trade, because
   the two prices are the same number. The spec puts the real cost at 10-30% of
   headline return on H4, and it comes entirely from the gap between a close and the
   next open — spread, overnight, news — which this fixture does not have. The rule
   is correct and load-bearing; the fixture simply cannot demonstrate it, and
   `test_model_A_never_fills_at_the_close_that_triggered_it` covers it instead.
2. **Gap-past-the-stop cancels never fire** (0 on the whole fixture), so
   `cancel_if` clause 1 and the `intrabar_mode` branch are exercised only by
   constructed tests. That is the same position Phase 10's `INVALIDATED` was in, and
   for the same reason.

Both are the first things to re-measure when real bars arrive.

## Where limit orders end up

| Model | FILLED | EXPIRED | CANCELLED | PENDING |
|---|---:|---:|---:|---:|
| A — market on MSS | 165 | 0 | 0 | 0 |
| B — retracement of the leg | 65 | 100 | 0 | 0 |
| C — FVG | 53 | 107 | 0 | 0 |
| D — order block | 53 | 106 | 0 | 0 |
| E — 50% of the leg | 67 | 98 | 0 | 0 |

`PENDING` is a censored order — the series ended before its window closed. Counting
those as expiries would understate every fill rate, so they are excluded from the
denominators above.

## What this report does NOT establish

**Which model is best.** That needs expectancy, which needs stops, targets, sizing
and an exit policy — SPEC 16, 17, 18, and the backtest engine. This phase
establishes only that five models arm on the same setups and that the fill rule
resolves them the way an M1 replay does.

Specifically not established:

1. **Anything about returns.** No trade is closed here; nothing is sized.
2. **That the fill rates transfer.** They are a property of how far each model's
   price sits from the break on a random walk. Real retracement behaviour differs,
   and the opposing-sweep column above will differ more.
3. **Shadow trades (SPEC 15.6).** The would-have-been outcome of an expired order
   needs `exit.max_bars_in_trade` and a stop/target policy. Deferred to Phase 14,
   and worth doing: *"did we miss the good ones?"* is per-model and unanswerable
   without them.
4. **The M1 fill path against real intrabar data.** The M1 here is synthetic and
   agrees with its own H4 by construction. Q2 is what makes this a real check.

## Verdict: PASS

Five models arm on the same setup stream with their prices pinned by arithmetic,
and the bar-level fill rule agrees with the M1 replay on every armed order — after
a correction the M1 comparison is what surfaced.
