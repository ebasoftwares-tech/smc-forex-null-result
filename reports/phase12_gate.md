# Phase 12 Gate Report

**Entry models and fill resolution (SPEC 15).**

Generated 2026-08-30T14:57:34+00:00

- `config_hash` `7f393e8ced4bf193f993f056380bffff2826fbeb71ba2a583d99e54f19b18c49`
- `dataset_hash` `2a2bb0293052ae31bd2be73cfd53df25f6032f4db94da906543889e447d03ed9`
- Data: **real bars** -- 10 symbols, 2019-2022 (40 symbol-years), H4 with the **real M1 path**, source `histdata`, `bid` side, tzdata `2026.3`
- Split (PRE_REGISTRATION 4.1, Amendment 1): in-sample **[2019, 2020, 2021, 2022]**, out-of-sample [2023, 2024], holdout [2025]
- **1,616 displaced CHoCH setups** (441 of them MSS), 64,238 H4 bars

The M1 path here is the **vendor's own M1**, and the H4 bars were resampled from
it by this project's own resampler, so the two fill resolutions still describe
one series — but now a real one, with weekend gaps, holiday gaps and news gaps
that the generated fixture had none of. That difference is the whole point of
re-running this phase, and it is measured below rather than asserted.

## Gate checks

| Check | Result | Detail |
|---|---|---|
| Test suite green | PASS | 662 passed in 108.07s (0:01:48) |
| All five models arm (gate) | PASS | A 1616, B 1610, C 1545, D 1493, E 1613 |
| Model A arms every setup and fills every order | PASS | the only 100% model, which is what makes it the baseline (SPEC 15.5) |
| Limit models do not always fill | PASS | coverage differs by model, so per-SETUP expectancy is the only valid comparison |
| Fill logic verified against M1 (gate) | PASS | 0 disagreements over 7,877 armed orders |
| The continuity branch is exercised | PASS | 238 bars touched both the entry and the stop |
| Model A never uses the close that triggered it | PASS | SPEC 15.3 -- pinned by test; the lookahead it prevents is worth a mean 0.0156 ATR per bar here (43,360 non-zero gaps) |
| Every reject reason is named | PASS | SPEC 15.7: invalidate and say why, never fall back |

## The five models (gate, first half)

| Model | Order | Armed | Rejected | Fill rate | Median bars to fill | Risk distance (ATR) |
|---|---|---:|---|---:|---:|---:|
| **A — market on MSS** | MARKET | 1,616 | — | 100.0% | 1 | 2.83 |
| **B — retracement of the leg** | LIMIT | 1,610 | `PRICE_THROUGH_STOP` 6 | 42.4% | 4 | 1.56 |
| **C — FVG** | LIMIT | 1,545 | `PRICE_THROUGH_STOP` 71 | 33.9% | 4 | 1.24 |
| **D — order block** | LIMIT | 1,493 | `PRICE_THROUGH_STOP` 92, `NO_OB_AVAILABLE` 31 | 30.7% | 5 | 1.07 |
| **E — 50% of the leg** | LIMIT | 1,613 | `PRICE_THROUGH_STOP` 3 | 46.5% | 3 | 1.68 |

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

## The opposing-sweep cancel makes every limit model nearly unusable

| Model | Fill rate without cancel_if 2 | With it |
|---|---:|---:|
| A — market on MSS | 100.0% | 100.0% |
| B — retracement of the leg | 42.4% | 9.4% |
| C — FVG | 33.9% | 7.2% |
| D — order block | 30.7% | 6.2% |
| E — 50% of the leg | 46.5% | 10.2% |

The data carries **28,012 confirmed sweeps over 64,238 H4 bars**, or
0.44 per bar. Over a 6-bar expiry
window an opposing sweep is close to certain, so SPEC 15.1's cancel_if clause 2
cancels most limit orders before they can fill. Model A is untouched
because a market order is resolved on the next bar and never waits.

**The synthetic report called this a fixture property and predicted it would not
survive. It survived.** Its exact words were that *"a random walk with up to 40
active liquidity levels produces sweeps at a rate no real market sustains; the
same clause on real bars will cost something quite different"*. Real bars:

| | fixture | real bars |
|---|---:|---:|
| confirmed sweeps per H4 bar | 0.47 | **0.44** |

**The sweep rate is the same to within 7%.** The liquidity model produces roughly
one confirmed sweep every two H4 bars on real FX majors, exactly as it did on
noise, so the mechanism behind the cancel is not a fixture artefact.

What *did* change is the size of the damage, and not enough to rescue the models:
limit fill rates run 6-10% with the clause against 30-46% without it, where the
fixture read 2-3% against 33-41%. Four to five times better and still a filter
that discards nine of every ten limit orders it is given.

**That makes cancel_if clause 2 a live design question rather than a fixture
note.** It is FROZEN, so nothing is changed here; but a clause that removes ~90%
of every limit model's population is deciding the entry-model bake-off by itself,
and SPEC 15.5's per-setup comparison cannot see past it. Both columns are
reported so the two effects stay separable, and the left one is used everywhere
else in this report.

## Fill logic verified against M1 (gate, second half)

- Armed orders resolved both ways: **7,877**
- Disagreements between the bar-level rule and the M1 replay: **0**
- Bars that touched both the entry and the stop: **238**
- Bars that opened beyond the stop (a true gap): **0**

**The two agree everywhere, and getting there required fixing the bar-level rule.**

A limit sits at `p` with its stop at `s` beyond it, and price approaches from the
far side. Any continuous path that reaches `s` must pass `p` first, so a bar
touching both is **not ambiguous** — the entry filled. The first version of this
module treated it as a coin flip and resolved it "pessimistically" by cancelling,
which produced 238 false cancels and disagreed with the M1 path on every
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

## The two effects the fixture measured as exactly zero

`STATE.md` §8 listed these as *"first in line on real bars"*, because the
synthetic series opens every bar exactly at the previous close and both effects
are gap effects. Real bars have gaps:

- **43,360 non-zero close-to-open gaps** in 64,228 bar transitions (67.5%), against 0 on the fixture.

**1. SPEC 15.3's lookahead now has a magnitude.** Filling model A at the close
that triggered it, rather than the next bar's open, gains exactly the
close-to-open move. In ATR:

| | ATR |
|---|---:|
| mean over **all** transitions | **0.0156** |
| median of non-zero gaps | 0.0049 |
| 95th percentile | 0.0353 |
| largest | 3.9978 |

The mean is the number that matters, because the lookahead would be taken on
every trade: **0.0156 ATR per entry**, free, in the direction the
trade wants. Against a stop of roughly 1-2 ATR that is on the order of a few
percent of R per trade — smaller than SPEC 15.3's *"10-30% of headline return"*
but unambiguously non-zero, and it accrues to **every** entry rather than to the
tail. The rule was load-bearing on a fixture that could not demonstrate it; it
is load-bearing and demonstrable now.

**2. Gap-past-the-stop cancels still fire zero times — and this one did *not*
come true.** `STATE.md` §8 expected this branch to come alive with real bars,
on the reasoning that it needs a price discontinuity and real data has them.
It has them, and they are far too small:

- median non-zero gap **0.0049 ATR**, 95th percentile 0.0353 ATR
- a stop sits **1-2 ATR** away

Gapping *past a stop* needs a discontinuity two orders of magnitude larger
than the typical one. The largest single gap in the sample is 4.00 ATR,
so it is not impossible — merely rare enough that four years across ten majors
produced no instance where it also beat the entry price to the stop.

**`cancel_if` clause 1 and the `backtest.intrabar_mode` branch therefore remain
exercised only by constructed tests.** Unlike Phase 10's `INVALIDATED`, which
did come alive on real bars, this one stays a guard nothing in the data
reaches — the pattern D-014 §8 and D-017 named, now confirmed to survive the
move to real data. It should not be removed on that basis: the H4 gap that
clears a stop is a tail event, and tail events are what the guard is for.

## Where limit orders end up

| Model | FILLED | EXPIRED | CANCELLED | PENDING |
|---|---:|---:|---:|---:|
| A — market on MSS | 1,616 | 0 | 0 | 0 |
| B — retracement of the leg | 683 | 927 | 0 | 0 |
| C — FVG | 523 | 1,022 | 0 | 0 |
| D — order block | 459 | 1,034 | 0 | 0 |
| E — 50% of the leg | 750 | 863 | 0 | 0 |

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
2. **That the fill rates generalise beyond these four years.** They are measured
   in-sample. Notably they barely moved from the fixture — 30-46% against
   33-41% without the cancel — which was *not* what the synthetic report
   expected, and is worth more scepticism than a confirmed prediction would be.
3. **Shadow trades (SPEC 15.6).** The would-have-been outcome of an expired order
   needs `exit.max_bars_in_trade` and a stop/target policy. Deferred to Phase 14,
   and worth doing: *"did we miss the good ones?"* is per-model and unanswerable
   without them.
4. **That M1 is the true intrabar path.** This is the vendor's M1, not a
   generated one, so the gate's second half is now a real check and it passes —
   but M1 is still a sampling of the tape. Within-minute order is unknown, and
   `backtest.intrabar_mode = m1_path` inherits that limit. Tick data (Q2's other
   half) is what would close it, and no spread series exists yet at all.

## Verdict: PASS

Five models arm on the same setup stream with their prices pinned by arithmetic,
and the bar-level fill rule agrees with the M1 replay on every armed order — after
a correction the M1 comparison is what surfaced.
