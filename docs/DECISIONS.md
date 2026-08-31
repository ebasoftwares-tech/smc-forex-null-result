# Decision Log — SMC Bot

Append-only. Every entry records what was decided, by whom, what it changed, and what it cost.
This file is an input to the pre-registration (`BACKTEST_PROTOCOL.md` §1): a result is only
interpretable against the decisions that were in force when it was produced.

---

## D-001 — Day boundary is UTC 00:00

| | |
|---|---|
| **Date** | 2026-08-25 |
| **Decided by** | Elie (answer to Q3) |
| **Status** | ACTIVE |
| **Parameter** | `tf.day_boundary = UTC 00:00` (was `America/New_York 00:00`) |
| **Spec** | §2.2, §2.3, §2.6 |

**Decision.** The trading day, and therefore the H4 / D1 / W1 / MN1 bucket edges, are anchored
to UTC midnight. The New York-midnight anchor is demoted from default to ablation.

**What it buys.** A boundary that never moves. The H4 grid is fixed at 00/04/08/12/16/20 UTC
year-round, so bar edges are identical across every data source and broker, and a result
computed on the Dukascopy research set is directly comparable with one computed on broker
history. It also removes the tz database from the resampling path entirely — sessions still
need it, bucket construction no longer does.

**What it costs.** The strategy's source literature defines "daily open" and "previous day
high/low" at NY midnight. Under UTC those levels sit 4–5 hours earlier, so `PDH`/`PDL` — heavily
used liquidity sources — are measured over a different 24-hour window than the SMC material
assumes. If the NY-anchored levels carry information that the UTC-anchored ones do not, this
decision discards it. The `day_boundary` ablation is the measurement that would show that, and
it is a full parallel run rather than a parameter sweep.

**Also: the London and New York opens now fall at different points inside an H4 bar in summer
and winter**, because a fixed UTC grid meets a session that moves with DST. This is not a
defect, but it is why `dst_desync` is recorded on every trade and reported separately.

### D-001a — Sunday stub-bar merge (correction, arising from D-001)

The market opens Sunday 21:00 UTC and the UTC day boundary is Monday 00:00, so a naive
implementation emits a **3-hour "Sunday" D1 bar** whose high and low become Monday's
`PDH`/`PDL`. A three-hour opening range standing in for "the previous day" is wrong data, and
it would be wrong every week.

**Fix:** `tf.sunday_handling = merge_into_monday` (§2.6.1). The week's first D1 bucket merges
forward when its coverage is below `tf.stub_merge_threshold` (0.25), so Monday's D1 bar spans
Sun 21:00 → Mon 24:00 UTC and carries the Sunday-gap region.

**This defect existed under the NY anchor too** — as an 8-hour stub, which is worse in the
specific sense that it is large enough to look plausible and would likely have survived review.
It was latent in v1.0 of the spec and is now fixed for both anchors. Two supporting parameters
were added: `tf.stub_merge_threshold` and `tf.min_bar_coverage_warn`. Neither is tunable.

---

## D-002 — H4 confirmation for every liquidity tier

| | |
|---|---|
| **Date** | 2026-08-25 |
| **Decided by** | Elie (answer to Q7) |
| **Status** | ACTIVE |
| **Parameter** | `liq.tier_confirmation_tf = {1: H4, 2: H4, 3: H4}` (was `3: H1`) |
| **Spec** | §0.4(a), §8.6, §11.2 |

**Decision.** Sweeps and CHoCH/MSS confirm on H4 for every liquidity source, including
session-derived levels. The tier map giving session liquidity H1 confirmation is retained as
the primary ablation.

**What it buys.** One timeframe for the whole signal path. The setup timeframe, the
confirmation timeframe, the ATR normalisation and the trade management are all H4, which
removes an entire class of cross-timeframe alignment bug and makes the causality test (§25.2)
strictly simpler. It is also what the brief asked for.

**What it costs, precisely.** `sweep.same_bar_choch_allowed = false`, so the minimum distance
from sweep confirmation to MSS confirmation is **two H4 bars = 8 hours**.

> **The flagship setup changes character.** "London sweeps the Asian low and reverses during
> London" is not reachable on H4. What is reachable is "London sweeps the Asian low, and the
> structure shift confirms in New York or later." That is a coherent, tradeable model — it is
> a session-to-session swing model — but it is **not** the model the brief's §6 example
> depicts, and it must never be reported as if it were.

Two further costs, both accepted:

1. **A thinner funnel.** Fewer confirmable events per unit time. The Phase 9 gate (§27) was
   therefore tightened to require ≥ 120 MSS events on the three development symbols in-sample,
   not just ≥ 300 universe-wide — a universe-wide count can hide a development set too thin to
   iterate on.
2. **The session filter loses most of its power.** Entry session is now largely determined by
   when the MSS bar happens to close rather than by a choice, so
   `filter.allowed_execution_sessions` is a much weaker instrument than it would be on H1.

**New required measurement.** The sweep-session × entry-session matrix
(`BACKTEST_PROTOCOL.md` §4.2.1). If its diagonal is nearly empty, that is the quantified cost
of this decision, and it is the evidence that settles the tier-map ablation.

### D-002a — Level-age rule applies to swing-derived sources only (correction, arising from D-002)

v1.0 §9.2 required every level to have "existed for ≥3 bars of its own TF" before it could be
swept. Under D-002 that is fatal:

> An `ASIA_RANGE` low confirms at ~05:00 UTC. Three H4 bars is 12 hours. The level would not
> become sweepable until 17:00 UTC — after London has closed. The setup the brief names as the
> flagship would never have fired once, in five years, on any symbol.

**Fix:** §9.2.1. The rule now applies only to swing-derived sources (`SWING_*`,
`PROTECTED_SWING`, `EQUAL_*`, `RANGE_*`), measured in bars of the timeframe the swing was
*detected* on. Period- and session-derived levels (`PREV_DAY/WEEK/MONTH_*`, `SESSION_*`) are
exempt, because they are by construction the extreme of a *completed* period — the price
action that formed them is already over when the level comes into existence, so the rule's
purpose (don't let an impulse sweep a level it just created) does not apply.

`sweep.require_prior_level_age_bars` stays FROZEN at 3. Its **domain** changed, not its value.

**This defect was also latent under the H1 tier map**, and there it was worse: three H1 bars
pushes the earliest sweepable moment to ~08:00 UTC, deleting the London open while leaving the
rest of the session intact. That failure mode presents as "the strategy just doesn't trade the
London open much" — the kind of thing that gets rationalised rather than found.

---

## D-003 — Remaining 15 questions take the recommended defaults

| | |
|---|---|
| **Date** | 2026-08-25 |
| **Decided by** | Elie |
| **Status** | ACTIVE |

| Q | Decision |
|---|---|
| Q1 | Raw-spread ECN account, USD or EUR; costs modelled as spread + $3.50/lot/side until the real account is known |
| Q2 | Dukascopy tick for research + broker M1 for the live-matching set, reconciled per protocol §2. `backtest.intrabar_mode = m1_path` is therefore available and mandatory |
| Q4 | Account size to be confirmed before Phase 13; the lot-granularity rejection rate is measured in the backtest either way |
| Q5 | The 10-symbol universe as specified; develop on 3, validate on 7 |
| Q6 | **Entry model A (market on MSS) is the pre-registered baseline.** B–E are challengers that must beat it on per-*setup* expectancy, not win rate |
| Q8 | Long and short, with direction as a first-class reporting dimension |
| Q9 | MQL5 watchdog built before Phase 17, not before Phase 16 |
| Q10 | Local machine, Parquet, results cached by `run_id` |
| Q11 | Round numbers deferred; revisit only after the shuffled-liquidity control (protocol §6.3) has run |
| Q12 | Windows VPS for live; desktop for research and paper |
| Q13 | No news filter in v1.0. If a historical calendar is obtained, news becomes a reporting dimension before it becomes a filter |
| Q14 | Telegram for alerts |
| Q15 | Daily human oversight assumed; the manual kill switch and manual monthly re-enable depend on it |
| Q16 | Checkpoints at Phase 4, Phase 9 (the funnel gate) and Phase 14 |
| Q17 | A documented null result is accepted in advance as a legitimate deliverable |

---

## Effect on the statistical budget

Neither decision nor either correction changes the TUNABLE set. It remains the same 8
parameters over the same grid, so `M = 6,912` stands and every figure in
`BACKTEST_PROTOCOL.md` §5.6 that depends on it is unaffected.

`tf.day_boundary` and `liq.tier_confirmation_tf` moved from "default" to "ablation" and from
"ablation" to "default" respectively — a relabelling within the ABLATION class, not a promotion
to TUNABLE. Both counterfactuals are now named ablation runs (protocol §6.5) rather than
assumptions.

---

## D-004 — Corrections found while implementing Phase 1

| | |
|---|---|
| **Date** | 2026-08-25 |
| **Status** | ACTIVE |
| **Trigger** | Phase 1 implementation and its test suite |

Five defects, four of them in v1.0 of the specification rather than in the code written
against it. Recorded here because a specification that quietly acquires fixes is no longer
the thing anyone signed off.

### 1. The London/New York overlap is 3.5h and 4.5h, not {3, 4, 5}h — SPEC 3.7 corrected

v1.0 asserted the overlap duration was "in {3h, 4h, 5h}". That was a guess. With London
08:00–16:30 and New York 08:00–17:00, the intersection is 13:00–16:30 UTC in winter and
12:00–15:30 in summer — **3.5h either way** — widening to 12:00–16:30 = **4.5h** only while
the US is on DST and the EU is not. Measured across the 2026 fixture year: 241 days at 3.5h,
20 at 4.5h, and all 20 are `dst_desync` dates. The spec's property test would have been
unfalsifiable-by-accident: it asserted a set the real values never belong to.

### 2. The level-age rule and the emission rule were both keyed on the wrong thing

Already recorded as D-002a. Restated here because implementation is what proved it:
`sweep.require_prior_level_age_bars` applied to session levels made the flagship setup
unreachable.

Separately, the resampler's "a bucket is closed once a later bar exists" rule was replaced
with "once its end instant has passed according to observed data". The two agree whenever the
source grid divides the bucket grid — which it always does here — but only the second makes
resampling **prefix-stable**, and prefix stability is the no-repaint property for a
resampler. `tests/test_causality.py` asserts it across 60 random truncation points per
timeframe, plus cut points concentrated on the four DST transition weekends.

### 3. Weekend gaps were classified as data defects (off-by-one)

The weekend-gap test was `week_open > last_bar AND week_open < first_bar_after`. The week
open coincides **exactly** with the first bar after the gap, so the second comparison was
always false and every weekend in the dataset was reported as a suspect gap. Fixed to `<=` in
both `quality.find_gaps` and `resample._internal_gap_mask`.

Worth noting how this presents: not as a crash, but as a data-quality report that flags
~52 defects a year and is therefore ignored.

### 4. Week-anchor deviation was measured against the bar's open

An M15 bar opening 20:45 closes exactly on the 21:00 week anchor. Measuring the open reported
a 15-minute deviation every week. Fixed to measure `close_time`, and dataset-edge weeks
(clipped by the start or end of the data) are now skipped rather than counted as broker
anomalies.

### 5. `datetime.fromtimestamp` raises on negative epochs on Windows

Reachable in ordinary use: `_week_opens` walks one week back from the first bar. Replaced
with epoch-offset arithmetic. A portability bug, not a defensive flourish — it crashed a test
outright.

### Also established, not a defect

Under a New-York anchor the 23h and 25h trading days are **always Sundays**, because
daylight-saving transitions always fall on a Sunday. A 25-hour day therefore never produces
its theoretical seventh H4 bucket on real FX data: there is no price action in the hours that
would fill it. The practical impact of the NY-anchor ablation's irregular days is confined to
the Sunday-open stub, which SPEC 2.6.1 already handles. This makes the D-001 ablation cheaper
to run than expected.

---

## D-005 — Corrections and one unresolved contradiction, found implementing Phase 5

| | |
|---|---|
| **Date** | 2026-08-25 |
| **Status** | ACTIVE |
| **Trigger** | Phase 5 implementation (swings + structure) and its test suite |

### 1. SPEC 6.4 and SPEC 6.9 contradict each other — now an explicit ablation

SPEC 6.4 says that when a BOS fires, `protected_low` "is updated to **the most recent
swing low confirmed at or before bar `i`**, and thereafter **ratchets upward only**".
SPEC 6.9 lists as an invariant that "`protected_low` is monotonically non-decreasing
within a bullish trend".

**These cannot both hold.** After an `INTERNAL_LIQUIDITY_GRAB` prints a swing low
*below* the protected low, the next BOS resets the protected level down to it under
6.4, and violates 6.9. The test asserting 6.9's invariant failed on real bar counts at
the first run, which is how this surfaced.

Resolved as `structure.protected_on_bos`, an **ABLATION** rather than a silent choice,
because it changes how far price must travel to produce a CHoCH and therefore the setup
count:

| Value | Behaviour |
|---|---|
| `most_recent_low` (**default**) | Follows 6.4 and standard SMC practice: the origin of the leg that broke structure becomes the new invalidation point |
| `ratchet_only` | Follows 6.9: the level may only ever move toward price |

SPEC 6.9's invariant is restated as "non-decreasing **between BOS events**", which is
what the default actually promises.

### 2. A break is an event, not a state — one BOS per level

SPEC 6.4 defines a BOS as `break_up(i, last_swing_high.price)`. Read literally, every
bar that closes beyond an already-broken level emits another BOS, because
`last_swing_high` does not change until the next swing confirms N bars later. On the
first run this produced **274 BOS events where 49 were real** — a single sustained move
firing on every bar.

A swing is now consumed when it is broken, and `INTERNAL_LIQUIDITY_GRAB` likewise fires
once per protected level. This matches the principle SPEC 8.9 already states for
liquidity ("a level's status is SWEPT only once") and is now applied to structure too.

### 3. Confirmation lag is N *bars*, not N x the bar duration

SPEC 5.2 wrote `confirmation_lag = N × D(TF)` unqualified. A swing formed on the last
H4 bar of Friday confirms on Monday: still exactly two bars, 52 hours of wall clock.
Clarified in the spec, because **every timeout measured "in bars" inherits this** —
`choch.max_bars_after_sweep = 12` is two trading days mid-week and four days over a
weekend.

### 4. Object ids are deterministic, not ULIDs

SPEC 1.7 specifies ULIDs. A ULID embeds wall-clock time, so a ULID-keyed event log
cannot be byte-identical across two runs of the same data — which SPEC 25.5 requires
and `test_golden_is_reproducible_within_a_run` checks. Ids are derived from
`(symbol, timeframe, kind, formed_index)` instead: deterministic, unique, and readable
in a log.

### Also established, not a defect

**SPEC 6.2's label-based trend initialisation is nearly unreachable.** It requires the
last high to be labelled HH and the last low HL simultaneously; in practice the first
structural break resolves `UNDEFINED` first. Measured across twelve fixture years:
label-based initialisation fired **2 times**, the first-break path **10 times**, and
in every case the trend was defined within the first 20 bars.

This is not worth fixing. Both paths define the trend early and the choice affects at
most the first event of a dataset — a warm-up artefact, not a strategy behaviour. It is
recorded so that nobody later mistakes the rule's rarity for a bug.

**MSS is deliberately absent from Phase 5.** SPEC 6.6 defines it as a CHoCH plus sweep
context plus displacement, and neither exists until Phases 7 and 8. The structure engine
emits the unfiltered superset, which is exactly what makes the marginal value of those
filters measurable later (SPEC 6.9, `BACKTEST_PROTOCOL.md` §6.2).

---

## D-006 — Corrections and three findings, from implementing Phase 6

| | |
|---|---|
| **Date** | 2026-08-25 |
| **Status** | ACTIVE |
| **Trigger** | Phase 6 implementation (liquidity engine) and its test suite |

### 1. Equal-cluster growth was re-dating its own confirmation (causality bug)

SPEC 8.5.1 says an equal-highs level is "confirmed at the `confirmed_at` of the last
constituent swing". Implemented literally, a cluster that grew from two touches to three
**moved its `confirmed_at` forward**, so a level that became knowable at bar 152 was reported
as knowable only once a swing at bar 200 had happened.

That is a lookahead: the level's admission time depended on a swing that had not occurred yet.
Caught by the prefix-stability test, which is exactly the shape of leak that test exists for.

**Fixed:** confirmation is stamped when the cluster *first* reaches `eq.min_touches` and never
moved. Later growth amends strength, price and `source_ids` — permitted by SPEC 1.2, which
allows amending a label but never retracting or re-dating a signal.

### 2. Merging needed a fixpoint, and is transitive by construction

Two related corrections:

* A single clustering pass does not satisfy its own post-condition. SPEC 8.8 moves the
  survivor to the *more extreme* price, which can push it inside the next cluster, leaving two
  active levels within the tolerance. Merging now runs to a fixpoint, so "no two active levels
  on a side within `tol`" is true at the end of every bar.
* Because of that same extreme-price rule, **merging is transitive**: a dense ladder collapses
  to its extremes even though its endpoints are far outside the tolerance. This is the
  specified rule working as written — the stops sit above the highest high, and one level is
  what a sweep must clear — not chain drift. A merged level's price is always some real
  constituent's price, never an invented one. Both properties are pinned by test.

The consequence is that **~65% of all levels created end as MERGED**. That is arithmetic: with
the book capped at 40 active levels inside a 5-ATR in-play band and a 0.1-ATR merge tolerance,
the mean gap between neighbours is smaller than the tolerance.

### 3. OVERLAP and killzones are not liquidity sources

`OVERLAP` is derived (London ∩ New York) and is not a configured session at all. Measured on
the fixture, **an overlap extreme coincides exactly with the London or New York extreme on 38
of 42 days (90%)** — it is a sub-window of two sessions already counted, not an independent
inference about resting orders. Including it inflated the population by ~20% and the merge
machinery then deleted it again.

Killzones are execution windows, not pools of orders.

**Fixed:** only sessions whose configured `role` contains `liquidity` contribute levels, which
excludes both without a special case.

### 4. Finding: `PROTECTED_SWING` is a strength annotation, not an independent source

SPEC 8.3 enumerates it as source 7 and the spec text calls it "arguably the highest-quality
level in the model". Measured: **95% of the levels it emits are the same swing, at the
identical price, that `SWING_*` has already emitted** — the protected low *is* a confirmed
swing low. They merge on the bar they are admitted, so the source's only lasting effect is
`+1` strength on whichever swing is currently protected.

That is defensible behaviour — the protected swing *should* outrank an ordinary one, and
strength is how this engine says so. But it is not what §8.3 implies, and it has a concrete
consequence for Phase 7: **`PROTECTED_SWING` will show a near-zero sweep rate**, because the
coincident `SWING_*` level survives the merge and anchors the sweep. Read as "this source does
not work", that would be wrong. Its 4% penetration rate against 30–68% for every other source
is the same artefact one step earlier.

### 5. Finding: `PREV_SESSION_EXTREME` (SPEC 8.3 source 9) is redundant and is not implemented

A tier-3 level lives for 5 D1 bars (SPEC 8.7), so yesterday's Asian high is *still an ACTIVE
`SESSION_HIGH`*. Emitting it again under a second name would double-count every level and
every sweep of it. The source is deliberately folded into `SESSION_*`.

### 6. Finding: the population is 61% session levels

SPEC 8.10 asks for the population report first and says why. `SESSION` emits two levels per
session per day, against two per *day* for `PREV_DAY` and two per *week* for `PREV_WEEK`, so
it is 61% of everything created and tier 3 is 61% of the book. **Any statistic computed over
undifferentiated levels is mostly a statement about session extremes.** Every downstream
report must break down by source; this is not a defect to fix but a shape to carry.

### Not in SPEC 8.7: two extra terminal statuses

`MERGED` and `PRUNED` are required by 8.8 (two levels become one) and 8.9 (levels beyond the
cap are dropped and never return) but are absent from the 8.7 lifecycle list. Recorded as
distinct statuses rather than deletions so the population report can account for every level
ever created.

---

## D-007 — Corrections and findings, from implementing Phase 7

| | |
|---|---|
| **Date** | 2026-08-25 |
| **Status** | ACTIVE |
| **Trigger** | Phase 7 implementation (sweep detection) and the forward-return study |

### 1. `GAPPED_THROUGH` must be tested before penetration depth

A bar that opens beyond a level and never traded at it satisfies `low < price` — so the
naive ordering classified it by penetration depth and reported `OVER_PENETRATION`, i.e. a
breakout. It is neither: there was no opportunity to sweep. The gap test now runs **before**
the penetration branch (SPEC 9.6).

The rule is aggressive by design — any bar entirely beyond a level whose predecessor was
entirely on the other side. In practice on H4 that is almost always the Sunday open, which is
the case the ruling exists for.

### 2. A negative wick ratio could silently reject a valid sweep

`wick_ratio` is `(min(open, close) − low) / range`. On a malformed bar (close outside
`[low, high]`) that is negative, and `negative < 0.0` is **true**, so the filter rejected the
sweep *even with the filter switched off at its 0.0 default*. Now clamped to `[0, 1]`, so a
filter that is off behaves as if it is off. Rejecting malformed bars remains the ingest
layer's job (SPEC 1.5, `quality.analyse`); this is only a guard against a switched-off filter
having an effect.

Found because a hand-built test fixture had `close` below `low`. Worth noting how it
presented: not as an error, but as one missing sweep among hundreds.

### 3. Sweep windows must survive their level being merged away

With ~65% of levels merging (D-006), a window keyed on a level that is absorbed mid-sweep
would be dropped, and the surviving level — at a near-identical price — would open a fresh
window with a new trigger bar, losing the original trigger and the running extreme.
`LiquidityLevel.merged_into` now records the survivor and the sweep engine follows the chain.

### 4. Finding: level and event ids are unique per **run**, not globally

Ids restart at `L000000` / `SW000001` every run. Across five fixture years, **206 level ids
collide**. Harmless while each run is analysed alone; actively wrong the moment Phase 14 pools
trades from several symbols or several walk-forward windows into one table — a pooled
uniqueness check on ids would fail, or worse, a join would silently mismatch.

SPEC 1.7 already specifies a ULID for exactly this reason. The sequential ids used in Phases
5–7 are a convenience and **must not survive into the trade log**. This is a Phase 14
prerequisite, recorded now so it is not discovered by a corrupted join later.

### 5. Finding: 3 of 20 significance tests fired on data known to contain nothing

The forward-return study run per-year across four horizons is 20 tests. On the random-walk
fixture, **three reported a confidence interval excluding zero** — right at what a 5%
false-positive rate predicts (expected 1, P(≥3) ≈ 8%).

This is the multiple-testing problem made concrete on data where the true effect is exactly
zero by construction. It is the clearest available argument for `BACKTEST_PROTOCOL.md` §5.6:
Benjamini–Hochberg across subgroup and ablation p-values, and a Deflated Sharpe Ratio against
the declared configuration count. With `M = 6,912` in the tunable grid, the expected maximum
result under the null is large, and reading any single row as an edge would be reporting
arithmetic.

### 6. The forward-return study needs a positive control, not just a null result

A study that always reports "no edge" passes the random-walk fixture while being useless. The
null result here is only meaningful because `tests/test_sweep_study.py` also pins that a
**planted** post-sweep drift is detected at +1 bar with a CI excluding zero, in both
directions, and that an inverted edge reports negative rather than zero.

**H2 is therefore neither supported nor refuted.** It cannot be, on synthetic data. Phase 7
proves the instrument works; the measurement needs real bars (Q1/Q2).

---

## D-008 — Findings from implementing Phase 8

| | |
|---|---|
| **Date** | 2026-08-25 |
| **Status** | ACTIVE |
| **Trigger** | Phase 8 implementation (displacement) and the threshold study |

### 1. FVG detection was pulled forward from Phase 10

`disp.require_fvg` defaults to **true** (SPEC 10.2), and FVG is nominally Phase 10. Shipping
Phase 8 with the flag switched off would have made every rejection rate in its gate report
describe a *different filter* than the one that actually runs.

`bot/core/fvg.py` therefore implements SPEC 12.1 **detection only**. The 12.2 lifecycle —
touch, PARTIAL, MITIGATED, INVALIDATED, EXPIRED — and the 12.3 selection rule remain Phase 10,
whose gate is the standalone edge test. The full `FvgConfig` is declared now so Phase 10 adds
code rather than changing `config_hash`.

### 2. The `1.5` displacement threshold is arbitrary, and the report says so

SPEC 10.6 asks the question directly: *"If the default 1.5 sits in the middle of a smooth
unimodal distribution, it is an arbitrary cut and should be reported as such rather than
defended."*

Measured over 27,760 candidate legs: the `net/ATR` density **decays monotonically from zero**
with no shoulder, gap or local minimum. 1.25 rejects 76%, 1.5 rejects 84%, 2.0 rejects 95% —
a smooth progression with nothing distinguishing the middle value.

**1.5 is a choice, not a discovery.** That is exactly why it is TUNABLE under a plateau
requirement rather than FROZEN: the data cannot justify it, so out-of-sample stability must.

### 3. The "natural break" detector fired on 0.6 sigma of Poisson noise

Its first version reported STRUCTURED — because one histogram bin rose by **+11 counts against
a standard deviation of 18**. A single bin-to-bin wobble was being reported as the data marking
the threshold out.

It now requires a rise exceeding **2× the Poisson noise** of the preceding bin, and the tests
pin both directions: noise must not qualify, and a genuinely bimodal distribution must.

This is the same failure mode as D-007 §5 one level up: a statistic that is not compared
against what noise alone would produce is not a finding.

### 4. Finding: `BODY_RATIO` binds harder than `NET_TOO_SMALL`

Rejection rates over the fixture, counted independently:

| Condition | Rejects |
|---|---:|
| `BODY_RATIO` | 90.5% |
| `NET_TOO_SMALL` | 84.0% |
| `NO_FVG` | 83.3% |
| `DIRECTIONAL_BARS` | 14.1% |

SPEC 10 gives `min_leg_atr` the TUNABLE slot; `min_body_ratio` is only ABLATION. On this
fixture the body/range ratio is the binding constraint.

**Do not act on this yet.** A random walk has no sustained directional drives, so body ratios
are low *by construction*; real displacement legs should carry much higher ones and the ranking
may invert. What it establishes is that the relative bindingness of the five conditions must be
**re-measured on real bars before the TUNABLE/ABLATION split is trusted**.

### 5. Finding: the FVG requirement's marginal cost shrinks as the net threshold tightens

Joint ablation (SPEC 10.6), pass rate:

| `min_leg_atr` | FVG off | FVG on | Cost |
|---|---:|---:|---:|
| 0.0 | 9.5% | 5.9% | −3.6 pts |
| 1.5 | 8.0% | 5.4% | −2.6 pts |
| 2.5 | 1.5% | 1.2% | −0.3 pts |

This is direct support for SPEC 10.2's claim that the FVG requirement is *the same condition
expressed structurally* rather than an extra filter: a leg large enough to clear a strict net
threshold has usually already left a gap. They must be ablated **jointly** — testing them one
at a time would credit each with the other's work.

### 6. A required field on a frozen dataclass breaks callers silently until the full suite runs

Adding `sweep_extreme_bar` to `SweepEvent` (Phase 9 needs it to place the displacement leg's
origin) broke nine Phase 7 tests that construct the event by hand. They were not caught by
running the sweep tests, only by the full suite.

Not a code defect, but a process note worth keeping: **run the whole suite after changing a
shared dataclass**, not the module that motivated the change.

---

## D-009 — Two specification contradictions, and findings, from implementing Phase 9

| | |
|---|---|
| **Date** | 2026-08-25 |
| **Status** | ACTIVE |
| **Trigger** | Phase 9 implementation (CHoCH reference selection, MSS confirmation, the funnel) |

### 1. SPEC 11.5 and SPEC 11.6 disagree about what invalidation means

11.5 lists "no new extreme below the sweep" and "no opposing confirmed sweep" as **clauses
evaluated at the break bar `b`**, over the interval `(s, b]`. 11.6 lists the same two as things
that **invalidate the setup** the moment they occur.

The readings differ for a real population: a setup that makes a new extreme and *then* breaks
its reference is, under 11.6, dead before the break and never recorded — and that is exactly
the population SPEC 6.9 requires in order to measure whether the sweep-and-displacement
requirement adds anything.

**Resolution: both conditions are tracked as sticky flags over `(s, b]` and read as clauses at
the break bar.** This satisfies 11.5 literally. They surface as *terminal outcomes* when no
break ever comes, which satisfies 11.6. Nothing is discarded either way.

On the fixture this keeps CHoCH events the strict-invalidation reading would have erased: the
NEW_EXTREME and OPPOSING_SWEEP clauses fire on 179 and 351 of the 477 CHoCH-not-MSS events
respectively (they overlap). Under the other reading, SPEC 6.9's marginal-value test would be
run on a population selected to exclude its most informative cases.

### 2. SPEC 6.6 carries a fourth MSS clause that SPEC 11.5 omits while calling itself complete

6.6 requires that *"the swept level lies beyond the extreme of the leg that produced the
CHoCH"*. 11.5 gives the MSS conditions under the heading **"MSS confirmation, complete"** and
does not include it.

**Resolution: 11.5 is operative** — it is the more specific section and the one claiming
completeness. The 6.6 clause is evaluated anyway and reported as a diagnostic, so the cost of
the other reading is a number rather than an argument: **3 of 38** major MSS events would
additionally be rejected by it. Too small to change the gate, which is the useful part — the
two readings agree on the decision Phase 9 exists to make, and the contradiction can be settled
on real data without re-opening this phase.

### 3. The WAIT and knowability are two different constraints, and only one was written down

SPEC 11.5 measures the window from the sweep **extreme** bar `s`. But a sweep is not knowable
until its **confirm** bar, up to `sweep.max_confirmation_bars` (3) later. Enforcing only
`b − s ≥ choch.min_bars_after_sweep` would admit a break judged against a sweep that had not
yet happened as far as any live engine was concerned.

Both floors are now applied:

    first_bar = max(s + choch.min_bars_after_sweep,
                    confirm_bar + (0 if sweep.same_bar_choch_allowed else 1))

This is a correction, not a preference, and it changes no registered value.

### 4. `SwingStore` answered "which swings existed at bar i" with less than a live engine had

SPEC 5.4 normalisation REPLACEs a swing when a more extreme same-kind swing confirms with no
opposite swing between, and the superseded object is then absent from the store — **including
from every earlier bar**. SPEC 11.1 selects the CHoCH reference from that set *as it stood at
the sweep bar*, so reading the finished store makes a swing vanish retroactively from a moment
at which it was live.

`SwingStore.history` (a `SwingSpan` per swing, carrying the half-open bar range over which it
was live) and `visible_at(bar, kind)` make the historical query exact. The direction of the
error matters and is why it survived four phases: the finished store is a **subset** of the
live view, so every rule reading it was conservative rather than lookahead.

It is also nearly self-correcting, which is worth recording so nobody re-derives it: the move
that supersedes a swing high has usually already *broken* it, and a broken level fails 11.1's
"unbroken since it formed" test regardless. The residual case is a sweep occurring before the
superseding swing forms — **4 of 2,323 fixture sweeps (0.17%)** select a different reference.

**The mutation test found this, not review.** Substituting the finished store passed the entire
suite; `test_a_reference_the_finished_store_no_longer_holds_is_still_selectable` now fails
against it.

### 5. Finding: the funnel converts 1.98% of sweeps into MSS — the number SPEC 11.7 named

SPEC 11.7, written before any of this existed:

> *"A funnel that converts 2% of sweeps into MSS will not produce a testable sample in five
> years, and that is a design finding to surface in Phase 9, before the entry engine is built."*

Measured, per cluster, right-censored candidates excluded: **1.98%**.

Scaled to the in-sample period (4 years × 10 symbols, `BACKTEST_PROTOCOL.md` §2.1) that is
**507 universe-wide and 152 on the development set** — clearing the gate's 300/120, but not
comfortably, and on a projection rather than a measurement. The gate is therefore recorded as
**PASS on projection, BLOCKED on measurement** until Q1/Q2 deliver real bars.

### 6. Finding: `micro` reference mode produces almost no MSS, and it is a pre-registered null

| Mode | MSS / symbol-year | Projected universe | Projected dev set |
|---|---:|---:|---:|
| `major` | 12.7 | 507 | 152 |
| `micro` | 1.0 | 40 | 12 |

Micro breaks the first pullback swing *after* the sweep, so its reference sits close to the
sweep extreme and the move reaching it is small — and a small move rarely clears a 1.5-ATR
displacement threshold. 709 of its 780 CHoCH events fail on displacement.

SPEC 11.1 predicted the two failure modes as "the move is over before confirmation" (major) and
"confirms on noise" (micro). What actually happens is that the displacement filter declines to
call the noise a confirmation at all.

**This is a null result on a pre-registered strategy variant, and is reported as one.**
`BACKTEST_PROTOCOL.md` §10.2 forbids tuning micro until it passes.

### 7. Finding: the TUNABLE window parameter is inert; the FROZEN floor binds

`choch.max_bars_after_sweep` is one of only eight TUNABLE parameters, and SPEC 11.2 treats it
as what makes this a multi-session model. Varying it over its registered range {4, 8, 12, 18,
24} moves the MSS count from 36 to 38 and then not at all: every MSS lands within 7 bars, with
the mass at **2** — the first admissible bar for most candidates.

**The floor is doing the work the ceiling is credited with.** This is the second instance of
the registered parameter classes not matching which parameter decides the outcome (D-008 §4 was
the first, `min_body_ratio` over `min_leg_atr`). Both were measured on a random walk; both must
be re-measured on real bars before the TUNABLE/ABLATION split is trusted.

It also qualifies D-002's reading of the timescale: the window *permits* two trading days from
sweep to MSS, but the observed median is **8 hours**. Multi-session by permission, same-day in
practice — against noise, at least.

### 8. Finding: an ABLATION parameter spans the gate verdict

`choch.max_reference_distance_atr`, registered ABLATION {2.0, 3.0, 4.0}:

| Value | MSS | Projected dev set | Gate |
|---|---:|---:|---|
| 2.0 | 14 | 56 | **FAIL** |
| 3.0 (default) | 38 | 152 | PASS |
| 4.0 | 42 | 168 | PASS |

`REFERENCE_TOO_FAR` rejects 588 of 1,916 decided candidates — more than any single MSS clause.
A parameter classified as a secondary question is one of the two largest terms in the funnel,
and the gate verdict is not robust to moving it inside its own registered range.

The default was fixed before the report ran and **stays fixed**; §10.2 forbids choosing it by
looking at the outcome. What this licenses is knowing the PASS is conditional — not moving the
parameter.

### 9. Finding: `OPPOSING_SWEEP` is a density effect, not a self-inflicted one

The clause fires on 351 of 515 major CHoCH events. The obvious suspicion — that the confirming
leg sweeps liquidity on its own way up and so disqualifies itself — is wrong: only **6**
opposing sweeps land on the break bar, and the rest spread evenly across the window.

It is level density. With up to 40 active levels and ~0.5 confirmed sweeps per H4 bar on this
fixture, a 12-bar window contains an opposing sweep more often than not. On real bars the sweep
rate differs and so will this clause's cost, so it is a fixture property rather than a design
finding — but it is the third-largest term in the funnel and needs re-measuring, not assuming.

### 10. The funnel changes units in the middle

Its first two stages count **levels**; the rest count **events**, and they are not nested — one
level can trigger several sweep events over its life (a rejected poke, then a real one), so
`sweeps_triggered` (3,859) legitimately exceeds `levels_swept_or_tested` (3,678).

Presented as one descending chain the funnel shows a rise in the middle and invites a hunt for
a bug that is not there. `LEVEL_STAGES` and `EVENT_STAGES` are kept separate, only the event
chain is asserted monotone, and the join is reported as the fan-out it is. Caught by the gate
report's own monotonicity check on its first run.

---

## D-010 — The H5 marginal-value study, run out of order

| | |
|---|---|
| **Date** | 2026-08-25 |
| **Status** | ACTIVE |
| **Trigger** | `bot/research/marginal_value.py` — SPEC 6.9 / `BACKTEST_PROTOCOL.md` §6.2 |

Run before Phase 10 rather than after Phase 14. The population it needs already existed
after Phase 9, and H5 is the hypothesis that decides whether the methodology's central
mechanism is real; answering it after five more phases had been built around the
assumption would have been expensive in exactly the way that matters.

**Outcome: the instrument is built and validated. H5 itself remains open**, and on
synthetic data it can only remain open — the true difference between MSS and
CHoCH-not-MSS is zero by construction on a random walk.

### 1. The verdict is three-way, because H5 is falsified by a *negative* result

Every other study in this project risks a false positive. This one risks the opposite,
and the opposite error is worse: a confidence interval spanning zero, written up as
"displacement is decoration", would retire the methodology's central claim on the
strength of a sample that could not resolve anything.

Falsifying H5 is an **equivalence** claim, and equivalence needs the interval to sit
inside a margin — not merely to contain zero. So:

| Verdict | Condition |
|---|---|
| `DIFFERENT` | CI excludes zero |
| `EQUIVALENT` | CI lies entirely inside +/-`EQUIVALENCE_MARGIN_ATR`. **The only verdict that licenses "decoration"** |
| `UNDERPOWERED` | CI spans zero *and* extends past the margin — the study cannot answer |

`MarginalValueStudy.headline()` refuses to describe an `UNDERPOWERED` study as a null
result, in those words, and a test pins the wording. A two-way verdict would have
reported this fixture as "no difference" at every horizon.

### 2. The equivalence margin is a declared judgement, fixed before any result was read

`EQUIVALENCE_MARGIN_ATR = 0.25`. The reasoning: displacement selects legs that moved at
least 1.5 ATR, and a selector that strong should shift the *subsequent* return by a
non-trivial fraction of an ATR if it carries information. A sixth of the threshold it
enforces is the line taken.

It is not derived from the data and could not be — choosing it afterwards would select
the verdict rather than measure it (§10.2). Every row also reports its own minimum
detectable effect and required sample size, so a reader applying a different margin needs
no re-run.

### 3. Bug found: half the sample was one forward return counted twice

The forward return is a function of `(break bar, direction)` and nothing else, so two
candidates sharing both contribute the *identical number*. The first version pooled raw
candidates, and on the fixture **640 CHoCH candidates collapse to 315 distinct
observations — 50.8% of the rows were redundant.** Every interval was about sqrt(2) too
narrow and every required-sample figure understated by two.

Worse: **15 bars carried both labels**, an MSS candidate and a CHoCH-not-MSS candidate
breaking together, putting one identical return into *both* groups and dragging their
means toward each other. That biases the study toward `EQUIVALENT` — the verdict that
declares the methodology decoration. A bug in the other direction would have been
tolerable.

It was producing a real false result: h=1 read `EQUIVALENT` before the fix and
`UNDERPOWERED` after.

`events_from()` now collapses to one event per `(bar, direction)`, resolving a mixed bar
to **MSS** — the same "best outcome represents the opportunity" rule `funnel.py` applies
to sweep clusters, and deliberately the stricter of the two: SPEC 9.4 keys on the
*sweep*, but two sweeps in different clusters can still break on one bar.

**Found by a number disagreeing across two reports, not by review.** The study projected
204 dev-set MSS where Phase 9's funnel projected 152; chasing the discrepancy found the
duplication. Two reports quoting the same quantity is worth the redundancy.

### 4. Finding: H5 is not answerable at the 12-bar horizon on the current design

MSS events needed to resolve +/-0.25 ATR, against what Phase 9 projects for the in-sample
period (4 years, 10 symbols; 3 of them the development set):

| h | MSS needed | Dev set (128) | Universe (427) |
|---:|---:|:--:|:--:|
| 1 | 58 | yes | yes |
| 4 | 222 | **no** | yes |
| 12 | 804 | **no** | **no** |

**At the 12-bar horizon the full in-sample universe is not enough**, whatever the
backtest shows. Required counts scale with the return variance, which grows with the
horizon, so the long horizons are far more expensive than they look.

This is the study's most durable output: it is a property of the return distribution and
the funnel's output rate, not of the fixture's realism, so it transfers to real data far
better than any effect size here does. Three ways out, all better decided now than after
Phase 14 — answer H5 at the short horizons only and say so; widen the margin (defensible
as a decision now, indefensible as a reaction later); or leave H5 open and rely on §6.5's
ablation delta, which measures the same component through the full system.

### 5. Both controls pass, and the positive control agrees with the power arithmetic

- **Positive control.** A study that could only ever say "no difference" would pass every
  null test in the file. An injected 0.8 ATR effect is detected end to end. The detection
  boundary across a grid of shifts falls **exactly where the MDE says it should** —
  detected at 0.5, not at 0.25, with an MDE of 0.336 — which is the internal consistency
  check that makes the required-sample table above worth acting on.
- **Null calibration.** Shuffling the MSS label makes the true effect exactly zero, so
  every `DIFFERENT` under a shuffle is a false positive by construction. Measured
  **7.8%** against alpha of 5%, which at 400 trials is **2.5 sigma** — genuinely
  anti-conservative, not noise.

  > **Corrected in D-012 §4.** That was a 400-shuffle draw, where the standard error on
  > the rate is ~1.1 points — the same size as the effect. Re-run at 3,000 shuffles the
  > figure is **5.90%, Wilson [5.11%, 6.80%]**: still above alpha, but far less than 7.8%.
  > The direction of this claim survives; the magnitude does not, and neither does
  > "not noise" as stated on 400 trials. Every calibration in the project now runs 3,000
  > shuffles and quotes its interval.

The percentile bootstrap under-covers with a few dozen heavy-tailed observations. Both
consequences point the same way: the intervals are too narrow, so the study is *more*
underpowered than its table shows, and a `DIFFERENT` verdict is over-eager — which for
H5 is the safe direction, since the error worth avoiding is falsely declaring the
methodology decoration. Left uncorrected for that reason, and because swapping the
interval method on synthetic data would be tuning the instrument against noise. `MDE` and
the required-sample figures come from the parametric SE and are unaffected.

**Report the sigma, not the rate.** This project has twice written up a sub-2-sigma
wobble as a finding before catching itself (D-007 §5, D-008 §3); the first draft of this
section did it a third time, calling a 0.5-sigma gap "mildly anti-conservative". The
report now computes the standard error and states the deviation in sigma.

### 6. Overlapping windows are reported, not assumed away

**33.7% of CHoCH events have a 12-bar forward window overlapping a neighbour's** (it read
67.3% before the duplicate fix — half the apparent "overlap" was duplicate rows at one
bar). Overlapping windows are not independent draws and narrow every interval.

Every comparison is therefore also run on a **non-overlapping subsample**, thinned
earliest-first so it is deterministic and seed-free. It fixes the independence and
destroys the sample size — at h=12 it leaves 7 MSS events. Neither version can answer H5
here, and reporting both is what makes that visible rather than letting the more
convenient one stand alone. A **stratified** sample (matched on the D-001 session slot
and ATR tercile, as `sweep_study.py` matches controls) is reported for the same reason.

### 7. R-expectancy is deferred, not skipped

§6.2 asks for forward returns *and* R-expectancy "where a hypothetical trade can be
constructed". It cannot be: stops (SPEC 16) and targets (SPEC 17) are Phase 12, so there
is no R. Inventing a stop distance to fill the gap would make the answer a property of
that invention. Reported as **DEFERRED**, and worth revisiting after Phase 12 — R-based
expectancy may resolve at a smaller sample than raw forward returns do, since a stop
truncates the left tail that drives the variance in the table above.

---

## D-011 — Two spec corrections and a shared-statistics extraction, from implementing Phase 10

| | |
|---|---|
| **Date** | 2026-08-25 |
| **Status** | ACTIVE |
| **Trigger** | Phase 10 implementation (FVG lifecycle, selection, standalone edge test) |

### 1. SPEC 12.1 labels the proximal and distal edges backwards

Proximal means the edge price reaches **first** on returning to the zone. A bullish gap
forms with price above it (`L_n > H_(n−2)`), so a return meets `L_n` = `zone_high` first.

§12.1's table says the proximal edge is `H_(n−2)` = `zone_low`. Two other places in the
same section say the opposite, and both describe *behaviour* rather than naming:

- §12.2's touch rule, `bullish: L ≤ zone_high` — the zone is entered when the low reaches
  `zone_high`, so that is the first edge.
- §12.4's worked example: *"Entry model C would place a buy limit at 1.08420 (proximal
  edge)"*, where the zone is `[1.08310, 1.08420]` and 1.08420 is `L_n` = `zone_high`.

**Resolution: §12.1's labels are corrected in place.** This is not cosmetic. Entry model
C places its limit at the proximal edge, so the inverted label makes every model-C entry
wait for a pullback to the *far* side of the gap — a systematically deeper fill, a
different stop distance, and a materially different fill rate. It would have shipped as a
wrong price in Phase 12 rather than as a visible error.

`Fvg.proximal` / `.distal` followed §12.1 and were inverted. **Two committed tests
encoded the inverted version** (`test_fvg_geometry_ce_and_edges`,
`test_bearish_fvg_mirrors`, both from Phase 8) and were corrected with a comment naming
this decision, so the flip is not mistaken for a regression later.

### 2. SPEC 12.2's touch rule made `INVALIDATED` unreachable

The rule is written one-sided — `bullish: L ≤ zone_high`, `bearish: H ≥ zone_low`. That
is correct whenever price returns to the gap from its own side, and wrong for the case
§12.5 explicitly describes:

> *"Price gaps over the entire zone without touching — zone is **not** mitigated (never
> touched) but is INVALIDATED if the close is beyond it."*

A bar that opens below a bullish zone satisfies `L ≤ zone_high` while never having traded
inside it. So under the one-sided rule a gap-over registers as a touch, which mitigates.

**And that made invalidation impossible, not merely rare.** For a bullish gap,
invalidation needs `close < zone_low`; `close ≥ low`, so `low < zone_low`; and `zone_low`
is at or past *every* mitigation target (`proximal`, `ce`, `distal` are all ≥ `zone_low`).
Mitigation is tested first, so it always won. Measured: **0 INVALIDATED across 707 gaps**
on the fixture, and structurally 0 on any input.

**Resolution: touch is range intersection** — `L ≤ zone_high ∧ H ≥ zone_low`. It agrees
with the one-sided rule everywhere the one-sided rule is right (a bar arriving from above
has `H ≥ zone_low` trivially) and differs only in §12.5's case, so it generalises rather
than replaces. Mitigation additionally requires the touch.

The cost of leaving it: **every gap-over counted as a fill**, inflating the fill-rate
curve that §12.6 asks for — Phase 10's own deliverable.

**The synthetic fixture cannot exercise this.** H4 bars from `bot/data/synthetic.py` are
continuous, and weekend-gap FVGs are excluded at creation
(`fvg.exclude_weekend_gaps`, default true), so INVALIDATED stays 0 in the gate report for
a legitimate reason. It is covered by a constructed test
(`test_a_gap_over_is_INVALIDATED_not_MITIGATED`) and by nothing else, which is exactly the
kind of transition that would otherwise reach real data untested.

### 3. Status is a function of time, and the object looks like it holds one

`Fvg.status` is a single mutable field, so the natural way to ask "was this gap available
at bar `i`?" is to read it — which returns the **end-of-run** value and would let a gap
mitigated at `i + 5` look unavailable at `i`. Lookahead, and invisible.

`status_at(bar)` is now the accessor callers use, backed by stored transition indices, and
`select_fvg` reads availability through it at the setup bar. §12.3's "`status =
UNMITIGATED`" is amended in place to say *as of the bar the setup confirms on*.

`track_fvgs` also works on **copies**. Detection output is shared with the displacement
engine (SPEC 10.2), and a tracker that mutated it in place would make the displacement
filter's behaviour depend on whether anyone had run the tracker first.

### 4. Shared statistics extracted to `bot/research/stats.py`

Phase 10's edge test would have been the **third** copy of the same percentile bootstrap
(`sweep_study.py`, `marginal_value.py`). Two copies of an interval method is how two
studies quietly start answering the same question differently, which matters more here
than in ordinary code because `BACKTEST_PROTOCOL.md` §6 needs the falsification suite to
be comparable to itself.

Moved: bootstrap CI, permutation p, MDE, required-n, Benjamini-Hochberg, tercile
stratification, `Group`, the three-way `Verdict`, and both controls
(`null_calibration`, `detects_effect`). Added `calibration_sigma`, because reporting a
false-positive rate without its standard error is what produced three sub-2-sigma
misreadings in this project already (D-007 §5, D-008 §3, D-010 §5).

**Verified by regenerating `reports/phase7_gate.md` and `reports/marginal_value.md`
before and after: byte-identical.** Call order into the shared RNG was preserved
deliberately, since a reordered bootstrap draw would move every interval without changing
any logic.

One deliberate interface change: `required_n` takes its margin explicitly rather than
defaulting. A shared default margin would let two studies inherit a number neither
declared, and each study's margin is a pre-registered judgement (§10.2).

### 5. The FVG concept is testable at a scale H5 is not

Phase 10's gate is SPEC 12.6's standalone edge test. On the fixture it returns
**UNDECIDED** — EQUIVALENT at +1 and +3, UNDERPOWERED at +6 and +12 — which on a random
walk is a working instrument reporting the absence it should.

The durable finding is the power comparison:

| Study | Events needed (longest horizon) | In-sample universe projects | Answerable? |
|---|---:|---:|---|
| H5 (MSS vs CHoCH-not-MSS) | ~800 | ~427 | **no** |
| FVG edge test | 1,306 | 7,613 | **yes, at every horizon** |

The FVG population is about **18×** larger, because every gap counts rather than only
those surviving the sweep-to-MSS funnel. Whatever real data says about FVGs, this study
will be able to hear it — which is not true of H5, and is worth knowing before either is
run for real.

Null calibration also lands much closer to nominal here (1.6 sigma, against H5's 2.5): the
percentile bootstrap under-covers with a few dozen heavy-tailed observations, and this
study has hundreds rather than dozens. Same method, different sample size, and the
calibration step is what makes the difference visible rather than assumed.

### 6. Finding: the mitigation mode moves availability, not whether price returned

Ablation over `fvg.mitigation_mode`:

| Mode | Mitigated | Expired | Touch events | h=1 diff |
|---|---:|---:|---:|---:|
| `touch` | 591 | 114 | 571 | +0.0136 |
| `ce` (default) | 551 | 153 | 571 | +0.0136 |
| `full` | 511 | 193 | 571 | +0.0136 |

The mitigated/expired split moves substantially — `touch` consumes a gap on any tag,
`full` needs a complete traverse — while the touch event count and the edge-test result
are **identical to the digit** across all three modes.

That is correct, and worth recording precisely because it looks like a bug. The first
touch happens at the same bar whatever the mode is; the mode only governs how long a gap
stays *available* to entry model C afterwards. So it has no bearing on whether price came
back, and the edge test — which anchors on the first touch — cannot move. Anyone reading
this ablation as evidence about the edge would be reading it wrong, and anyone reading the
identical columns as a copy-paste error would be too.

---

## D-012 — The Order Block bake-off, two statistical corrections, and one flagged ambiguity

| | |
|---|---|
| **Date** | 2026-08-25 |
| **Status** | ACTIVE |
| **Trigger** | Phase 11 implementation (Order Blocks, SPEC 13) |

Phase 11's deliverable is unlike every phase before it: SPEC 13.1 opens by admitting that
the standard Order Block formulation is under-specified in three places, and 13.2 offers
four candidate definitions. So the gate is a **comparison between rules**, and the
headline output is not a performance number but a count of how many independent
hypotheses those four rules actually represent.

### 1. OB-D is under-specified in a way A/B/C are not, and is flagged rather than resolved

OB-A, OB-B and OB-C all key off the displacement leg of the setup in hand and are fully
determined by SPEC 13.2's table. OB-D points at a **different structural event** in one
line — *"the last opposing bar of the failed move: the up-bar before a swing high that was
subsequently broken downward, now used as resistance-turned-support"* — without saying
which swing, how far back to look, or what "broken downward" means for a level that is
broken upward by definition.

The reading implemented (documented at `order_blocks._ob_d`): for a bullish setup, the
most recent confirmed swing **low** before the sweep whose price the sweep traded below —
the failed move, whose support gave way — and then the last bar before that swing formed
which closed in the direction of the failed move.

**Recorded as a flagged ambiguity, not a resolved one.** At least two other readings are
defensible. Its consequence is visible and reported rather than hidden: OB-D produces
**72 blocks against OB-A's 178** on the fixture, failing on `NO_FAILED_MOVE` and
`OB_ABOVE_REFERENCE` where the others do not. SPEC 13.7 makes that rate *"a quality signal
for the definition"*, so it is reported as one — but it is a signal about this reading,
not about the breaker concept, and the report says so.

### 2. Finding: four variants are worth 1.77 tests, and same-bar agreement hides it

SPEC 13.8 requires the agreement matrix for a stated reason: *"near-identical variants
must not be counted as independent tests when applying the multiple-testing correction."*
That is a statistical instruction, so the deliverable is a number.

**M_eff = 1.77 against a nominal 4** (Galwey's estimator on the correlation of proposed
entry offsets, listwise n = 71). Correcting as though these were four independent tests
would over-correct by a factor of 2.3.

The interesting part is that the spec's own suggested instrument does not detect this:

| | Same-bar agreement | Entry-offset correlation |
|---|---:|---:|
| OB-A vs OB-B | 79.4% | 0.976 |
| OB-A vs OB-C | 23.0% | 0.971 |
| OB-A vs OB-D | 0.0% | 0.872 |
| OB-C vs OB-D | 0.0% | 0.918 |

By same-bar agreement the definitions look largely independent — OB-D agrees with nobody,
ever. By entry price they are nearly redundant: every pair correlates above 0.87. **They
pick different bars that sit at almost the same price.**

SPEC 13.6's heuristic — *"if OB-A and OB-C select the same bar 80% of the time, they are
not two hypotheses"* — is the right instinct with the wrong instrument. On this fixture
same-bar agreement **understates** redundancy badly. What a trade consumes is the entry
price, and two rules differing by a fraction of an ATR are one hypothesis however
different their reasoning looks. Both measures are reported; the correlation is the one
that feeds `M_eff`.

### 3. Two statistical bugs in the agreement machinery, both caught by measurement

**(a) Centring the correlation on the per-setup mean is a compositional artifact.** The
first version removed each setup's price level by subtracting the mean *across the
definitions*. That forces the deviations to sum to zero and pins the average pairwise
correlation at exactly `-1/(k-1)`. It produced a matrix where OB-A and OB-B correlated at
**0.28 while agreeing on the same bar 79% of the time**, and where everything correlated
negatively with OB-D.

Caught by the internal contradiction, then confirmed directly: four *independent* random
variables, row-centred, show a mean pairwise correlation of −0.333, matching −1/(k−1)
exactly; the observed off-diagonals averaged −0.239, carrying the same signature.

Fixed by anchoring on the break bar's close in ATR units — **exogenous to the set of
definitions**, so adding or removing a variant cannot move the others.

**(b) Li & Ji's effective-test estimator is discontinuous exactly where this study
lives.** It sums `I(λ ≥ 1) + frac(λ)`. Four perfectly correlated variants give eigenvalues
`[4, 0, 0, 0]` and it analytically returns 1 — but it never sees an exact 4:
`numpy.linalg.eigvalsh` on a matrix of ones returns **3.999999999999999**, `floor` drops
from 4 to 3, and the estimate jumps to ~2. It is wrong by a whole test on the most
redundant input possible, from floating-point noise alone, before any sampling noise.

Near-identical variants are this study's entire subject, so the estimator would be least
stable precisely where it is needed. **Galwey's `(Σ√λ)² / Σλ` is used instead**:
continuous, and exact at every anchor (`[4,0,0,0] → 1`, `[2,2,0,0] → 2`, `[1,1,1,1] → 4`).
Li & Ji is kept in the module for reference and pinned by a test documenting the
discontinuity.

### 4. Correction to D-010 §5: the H5 null calibration was overstated

D-010 §5 reported the H5 study's false-positive rate as **7.8%, 2.5 sigma, "genuinely
anti-conservative, not noise"**. That was a 400-shuffle draw, where the standard error on
the rate is about 1.1 points — the same size as the effect being described.

Three draws of the *Phase 11* calibration on 300–400 trials read 4.8%, 8.0% and 5.5%. The
extremes are mutually compatible (their Wilson intervals overlap) yet disagree about
whether alpha is inside, which is exactly the question a calibration exists to answer.

Re-run at **3,000 shuffles**:

| Study | n treated | FPR | 95% Wilson | Contains alpha |
|---|---:|---:|---|---|
| H5 (MSS vs CHoCH-not-MSS) | 32 | 5.90% | [5.11%, 6.80%] | no |
| FVG edge test | 570 | 5.47% | [4.71%, 6.34%] | yes |
| OB edge test (OB-A) | 43 | 4.83% | [4.12%, 5.66%] | yes |

**The direction of D-010's claim survives and the magnitude does not.** H5 is mildly
anti-conservative — 5.9% against 5%, not 7.8% — and the practical consequence is
correspondingly smaller. Its conclusions do not change: the bias still runs in the
direction that protects an UNDERPOWERED verdict.

Two things changed as a result, project-wide:

1. **Every null calibration now runs 3,000 shuffles** and quotes its Wilson interval.
   `stats.calibration_interval` was added for this.
2. **`reports/marginal_value.md` and `reports/phase10_gate.md` were regenerated** with the
   corrected figures, and the H5 report now carries a note explaining the correction.

This is the fourth time in this project a sub-2-sigma or noisy statistic was written up as
a finding before being caught (D-007 §5, D-008 §3, D-010 §5, and this). The pattern is
consistent enough to be worth naming: **a rate is never quoted without its uncertainty**,
and a calibration is not evidence until its interval is narrower than the effect it is
supposed to detect.

### 5. Finding: every OB definition's edge test is underpowered, and by how much

All four definitions return `UNDERPOWERED` at every horizon — not "no edge", since the
intervals are far too wide to sit inside the declared ±0.25 ATR margin.

The cause is sample size, and it places the OB study precisely between the two already
run. OB-A yields **43 touch events across 3 symbol-years**, because a block is only
proposed at a CHoCH that displaced and only a quarter are ever touched:

| Study | Touch events (3 symbol-years) | Universe projects | Answerable? |
|---|---:|---:|---|
| H5 | 32 MSS | ~427 | no, at any long horizon |
| **OB edge test** | **43** | **~573** | h=1 and h=3 only |
| FVG edge test | 571 | ~7,613 | yes, every horizon |

Worth knowing before Phase 12 builds entry model D on top of it: the model can be built,
but the evidence that its zone means anything will be thin at the horizons that matter.

### 6. Finding: half of OB-A's blocks are never filled

Fill rate at 30 bars is **50.3%** for OB-A, 51.2% for OB-B, 45.6% for OB-C and 65.9% for
OB-D. SPEC 13.7 makes this a headline rather than a detail: *"a model with a 20% fill rate
has a fifth of the sample size and cannot be compared naively against model A."*

Entry model D will therefore discard about half the setups it is offered, before any of
the other four entry models have had their fill rates measured — which is a Phase 12
planning fact, and the reason fill rate is reported per definition here rather than
averaged.

---

## D-013 — The entry engine, and a "conservative" default that was neither

| | |
|---|---|
| **Date** | 2026-08-25 |
| **Status** | ACTIVE |
| **Trigger** | Phase 12 implementation (entry models and fill resolution, SPEC 15) |

### 1. The within-bar order of entry and stop is determined, not ambiguous

A limit sits at `p` with its stop at `s` beyond it, and price approaches from the far
side. **Any continuous path that reaches `s` must pass `p` first.** So a bar that touches
both did not pose a question: the entry filled.

The first version of this module treated such a bar as a coin flip the bar could not
settle, and resolved it "pessimistically" by cancelling the order. On the Phase 12 fixture
that produced **15 false cancels**, and the M1 replay disagreed with every one of them.

**It was wrong twice over, and the second way is the instructive one.** Cancelling is not
the pessimistic *outcome*: a fill that then stops out loses 1R, while a cancel loses
nothing. Reaching for "be conservative" without asking *conservative about what* produced
an answer that was both physically incorrect and less conservative than the truth. The
label did the reasoning instead of the reasoning.

`resolve_fill` now fills such a bar and records `touched_both` as a diagnostic rather than
an ambiguity. The bar-level rule and the M1 replay agree on **all 814 armed orders**.

### 2. `cancel_if` clause 1 needs a gap, and SPEC 15.1 already said so

Once §1 is right, the clause looks nearly unreachable — which prompted re-reading what it
is for. SPEC 15.1 states it plainly: *"Without this, a limit order can fill on the way back
up from a level that already invalidated the idea."*

That scenario requires price to reach the stop **without having filled on the way**, which
under continuity requires a **gap past both**. So the clause is not about within-bar
ordering at all; it is about a level that was blown through and then revisited. The
implementation now:

- cancels when a bar **opens beyond the stop** (a true gap), and
- consults M1 there, because a finer series may show the level was offered after all.

That is the only place `backtest.intrabar_mode` changes an answer. SPEC 15.1 and 15.4 are
amended in place to say so.

`ohlc_heuristic` is prohibited by SPEC 17.5 and is deliberately **not offered as a config
value**: an option that must never be selected should not be selectable.

### 3. The fixture cannot demonstrate SPEC 15.3's trap, at all

SPEC 15.3 calls filling model A at `C_b` *"a lookahead of one full bar"* worth 10-30% of
headline return on H4. Measured on this fixture, the advantage is **exactly 0.0000 ATR per
trade** — because `bot/data/synthetic.py` emits a continuous walk in which every bar opens
at the previous close, weekends included. There were **0 non-zero gaps in 4,857 bar
transitions**.

The whole magnitude of the trap lives in the close-to-open gap: spread, overnight, news.
The rule is correct and load-bearing, and the fixture simply has nothing to say about it.
Covered by `test_model_A_never_fills_at_the_close_that_triggered_it` instead.

The same continuity makes the gap-past-the-stop branch unreachable (**0 occurrences**), so
`cancel_if` clause 1 and the `intrabar_mode` branch are exercised by constructed tests and
nowhere else — the position Phase 10's `INVALIDATED` was in, for the same reason (D-011
§2). Both are first in line to re-measure when Q2 delivers real bars.

### 4. Finding: the opposing-sweep cancel makes every limit model unusable here

| Model | Fill rate without `cancel_if` 2 | With it |
|---|---:|---:|
| A — market | 100.0% | 100.0% |
| B — retracement | 39.4% | 1.8% |
| C — FVG | 33.1% | 1.9% |
| D — order block | 33.3% | 1.9% |
| E — 50% of the leg | 40.6% | 3.0% |

The fixture carries 2,298 confirmed sweeps over 4,860 H4 bars — 0.47 per bar — so over a
6-bar expiry window an opposing sweep is close to certain. Model A is untouched because a
market order never waits.

**This is D-009 §9 one level down and it is a fixture property, not a finding about the
models.** A random walk with up to 40 active liquidity levels produces sweeps at a rate no
real market sustains. Both columns are reported so the fixture effect stays separable from
the models, and the left column is used everywhere else in the Phase 12 report.

### 5. Finding: model A is the only 100% model, and that is the whole problem

Coverage across the five: 100%, 39%, 33%, 33%, 41%. SPEC 15.5 is explicit about the
consequence — *"a model that fills 35% of the time on the best-looking third of setups
will show a superior win rate and a worse total return"* — so any comparison must be on
expectancy **per setup**, never per trade.

That is a Phase 14 obligation, but the coverage numbers that make it obligatory are
measured now, and they are large enough that ignoring them would decide the bake-off on
its own.

### 6. Scope deliberately left out

- **Only stop model S1** (`sweep_extreme`). `cancel_if` clause 1 needs *a* planned stop
  before an order can be armed, so S1 is implemented; S2-S4, SPEC 16.2's full buffer and
  16.3's constraints are their own phase. The buffer here is the ATR term only — 16.2 also
  takes a spread multiple and the broker's stops level, neither of which exists until
  Q1/Q2, and inventing them would make every stop a property of the invention.
- **Shadow trades (SPEC 15.6)** need `exit.max_bars_in_trade` and a stop/target policy.
  Deferred to Phase 14, and worth doing rather than dropping: *"did we miss the good
  ones?"* is a per-model question and is unanswerable without them.
- **`bias_snapshot`** in SPEC 14.1's Setup object needs the SPEC 7 bias engine, which is
  Phases 2-4. `cancel_if` clause 3 is implemented and takes an injected list of flip bars,
  the same shape as the MSS engine's MTF gate (D-009).
- **The M1 half of the gate is verified against synthetic M1.** It agrees with its own H4
  by construction, so what it establishes is that the two code paths implement the same
  rule — not that either matches a broker. Q2 is what makes it a real check.

---

## D-014 — Four defaults that cannot fire, and one model whose stop moves at fill

| | |
|---|---|
| **Date** | 2026-08-27 |
| **Status** | ACTIVE |
| **Trigger** | Phase 13 implementation (risk management, SPEC 18, with SPEC 16 completed and SPEC 17.1/17.2 placed) |

Phase 13's gate is *"every limit exercised by scenario; sizing purity test passes"*, and
exercising every limit is what produced this entry. **Four of them cannot be reached by
any legal configuration.** Three are arithmetic facts about pairs of FROZEN defaults, and
one is a contradiction between two of them.

None has been changed. `BACKTEST_PROTOCOL.md` §10.2 forbids moving a parameter to make a
result appear, and that applies to a check as much as to a return: a default moved because
it never fires is still a default moved after seeing the outcome. Each is written up here
so the decision can be taken deliberately, and each is pinned by a test asserting the
unreachability rather than the behaviour — so if a future change makes one reachable, the
test that fails says why that matters.

### 1. T3 cannot pass its own gate at the default `min_rr`

SPEC 17.2 measures `rr = |tp_1 − entry| / sl_distance` and rejects below `tp.min_rr`,
default **1.5**. SPEC 17.1 defines T3's ladder as *"50% at 1R, 25% at 2R, 25% at T2's
opposing liquidity"*, so its `tp_1` — the **first** rung, which is what 17.2 measures — is
at **1R**.

`rr` is therefore 1.0 on every setup T3 will ever see, against a 1.5 floor. **T3 is
rejected always**, and not because of anything about the market: the ratio does not depend
on the setup at all, since both the target and the stop distance scale together.

It is reachable at exactly one of the three declared ablation values for `min_rr`
({1.0, 1.5, 2.0}) — the lowest. So the T1–T4 ablation, run as specified, tests T3 on
one third of its grid and nowhere else.

Three ways out, and the choice is a decision rather than an implementation detail:

- **Gate T3 on its final rung** rather than its first. Defensible — the ladder's *purpose*
  is the runner — but it means `tp_1` in 17.2 no longer means the same thing for every
  model, and a gate whose subject varies by model is a gate that cannot be compared across
  models.
- **Exempt T3 from the gate**, as T4 already is by construction (§6 below). This makes the
  ablation unpaired in one more place, which is the problem §6 is about.
- **Accept that T3 is only defined at `min_rr` ≤ 1.0** and say so in the ablation grid.
  The most honest of the three and the smallest change, but it means the headline
  `min_rr = 1.5` configuration has three target models, not four.

`tp.ladder_first_r` exists in the config (FROZEN, 1.0) so that the number 17.2 measures is
declared rather than buried in the placement code, and `targets.gate_is_reachable` computes
the answer from the configuration alone — because for T1 and T3 it is a property of the
configuration and not of the data.

### 2. `risk.max_total_open_risk_pct` is unreachable under every legal configuration

SPEC 18.4 caps total open risk across all positions at **1.5%** and rejects a new trade
that would breach it. SPEC 18.4 also caps concurrent positions at **3**, and SPEC 18.3
bounds `risk.pct_per_trade` to **[0.10%, 0.50%]** — a bound the brief sets and this
implementation now enforces at load time rather than documenting.

    3 positions × 0.50% = 1.50%

which is exactly the cap and therefore does not *breach* it. At the default 0.35% the
ceiling is **1.05%**. Nothing in the risk layer can push it higher: the drawdown ladder
only reduces `risk_pct` (§18.5) and `counter_monthly_multiplier` only reduces it (§18.3).

**`max_open_positions` binds first, always.** The exposure cap is implemented and
scenario-tested, and the scenario has to pass a `risk_pct` of 0.60% — outside the legal
band — to reach it at all. That is recorded in the scenario's own note rather than hidden,
because a battery in which one row is only reachable illegally should say so.

This is defence in depth against a future configuration rather than a bug. But SPEC 18.9
asks for every limit to be exercised, and a reader of that table is entitled to know which
row was exercised by a legal input and which by a constructed one.

### 3. `risk.min_realised_fraction` is provably dead at 0.5

SPEC 18.2 rejects a trade whose lot-rounded risk falls below `min_realised_fraction` ×
the intended risk, default **0.5**, and justifies it with a worked example: *"with
`min_lot = 0.01` and a 26-pip stop, a €2,000 account at 0.25% risk wants 0.019 lots, floors
to 0.01, and takes **half** the intended risk."*

It cannot fire, for any lot grid, ever. Let `k = floor(raw_lots / lot_step)`. Sizing
proceeds only when `lots = k × step ≥ min_lot`, so `k ≥ 1`, and by definition of the floor
`raw_lots < (k+1) × step`. Then

    realised / intended  =  k × step / raw_lots  >  k / (k+1)  ≥  1/2

The ratio is **strictly greater than one half**, so `realised < 0.5 × intended` is never
true. Flooring to a grid cannot lose more than half of what you asked for.

Confirmed rather than only derived: **0 fires in 400,000 randomised sizings**, with the
worst accepted fraction at **0.500081** — the infimum approached from above, exactly as
the bound predicts. And on SPEC 18.2's own example the realised fraction is **0.52**, which
the prose calls "half" and the threshold lets through. *The check does not catch the case
the specification wrote it for.*

The smallest threshold that would catch that example is anything above 0.52. Whether the
right value is 0.75, or whether the check should be expressed as "reject if `raw_lots`
would floor to `min_lot`" instead, is the decision. Left at 0.5 and pinned by
`test_min_realised_fraction_is_unreachable_at_its_default`, with a positive control at 0.75
so the check is known to work rather than only known to be silent.

### 4. S4's stop is downstream of the entry price, and nothing else's is

SPEC 16.1's table defines S1–S3 against structure — a sweep extreme, the lowest low of the
setup window, an order block's distal edge — and S4 as `entry_price ∓ atr_multiple ×
ATR_ref`. That one asymmetry has four consequences, none of which SPEC 16 mentions, because
16 treats the stop as fixed once planned.

**(a) `arm` must compute the entry price before the stop.** Phase 12 computed the stop
first, which was correct when S1 was the only model. Reordered; S1–S3 are unaffected
because their anchors do not depend on the price.

**(b) Under S4, `PRICE_THROUGH_STOP` is vacuous.** That guard rejects a limit sitting at or
beyond its own stop. Under S4 the stop is placed a fixed distance from the price by
construction, so the two can never cross. The check is correct and unreachable for one
model in four — which matters when reading a rejection table, because a zero there means
*impossible*, not *did not happen*. Pinned by a test, with an S1 positive control on the
same setup showing the guard does fire.

**(c) Under S4, `sl.buffer_atr` is inert.** SPEC 16.1 gives S4 no buffer term. So the
declared buffer ablation across {0.05, 0.10, 0.20} covers three of the four stop models,
and a run reporting "buffer ablation" alongside "stop-model ablation" is reporting a grid
with a hole in it.

**(d) With a MARKET order, the S4 stop must be re-derived at fill.** Model A's planned
price is a *placeholder* for `C_b`, the close SPEC 15.3 forbids using — the fill is next
bar's open, or the first M1 price after latency. So under S4+market the planned stop is
anchored to a price that was never obtainable. `trade.revalidate_at_fill` re-derives it,
which SPEC 16.5 independently requires the caps to be re-run at (*"Both checks are
required"*) for the unrelated reason that the spread moves.

On the Phase 13 fixture the movement measures **exactly zero**, because `synthetic.py`
emits a perfectly continuous walk and every bar opens at the previous close — the same
reason SPEC 15.3's own lookahead measured 0.0000 ATR in Phase 12 (D-013 §3). The mechanism
is pinned by constructed tests and is first in line to re-measure on real bars.

**And a fifth, which is arithmetic between two FROZEN defaults.** S4's stop is 1.5 ATR and
`risk.max_sl_pips` is 60 pips on a major, so **S4 is `SL_TOO_WIDE` on every setup once ATR
exceeds 40 pips** (60 on a JPY cross, against its 90-pip cap). Mirror image: `max_sl_atr`
is 2.5 and S4 is 1.5, so **under S4 the ATR cap can never fire** — the third unreachable
check in this entry, and the only one that is model-conditional.

Whether the ceiling binds is a measurement this fixture cannot make: its median H4 ATR is
**17.4 pips**, well under 40, so S4 arms on all 165 setups here. Whether a real EURUSD H4
series spends time above 40 pips of ATR decides whether S4 is a usable model or an
unavailable one, and it is on the list for the first run on real bars.

### 5. Which cap is doing the work is a measurement, not an assumption

Two pairs of FROZEN defaults each contain one cap that is decoration, and *which* one
depends on the data.

**Upper stop caps.** `max_sl_atr` (2.5) and `max_sl_pips` (60) cross at
`60 / 2.5 = 24 pips of ATR`. Below that ATR the ATR cap binds and the pip cap is
decoration; above it, the reverse. On the fixture, whose median H4 ATR is 17.4 pips, the
ATR cap binds on **100%** of setups. On a market with a 40-pip H4 ATR it would be the other
way round entirely. `stops.dominant_upper_cap` computes it so the report measures this
rather than assuming it.

**Spread caps.** `max_spread_pips` (2.0 majors / 3.5 JPY) and `max_spread_pct_of_sl` (10%)
cross at `absolute / 0.10` = **20 pips of stop** on a major and **35** on a JPY cross.
Against SPEC 16.3's legal stop ranges ([8, 60] and [12, 90]) the relative cap is the binding
one over the tightest **23%** and **29%** of each range — the tight-stop end, which is where
a spread does the most damage. Both are inert until Q2 delivers a spread series;
`risk.binding_spread_cap_pips` records which would bind.

### 6. T4 is exempt from the RR gate, so the T1–T4 ablation is not paired

SPEC 17.7 asks for *"paired T1–T4 variants on a shared setup stream"*. T4 (`structure_trail`)
has no fixed target at all — it exits on the first opposing CHoCH — so there is no `tp_1`
to divide by `sl_distance` and SPEC 17.2's gate cannot be applied to it.

**T4 therefore accepts setups that T1–T3 reject.** The streams are not shared, and the
difference is not random: it is exactly the setups whose nearest structural target sits
inside 1.5R, which is a systematically different population, not a smaller sample of the
same one.

This is not fixable by implementation — it is what "no fixed target" means. What it
requires is that any T1–T4 comparison state its populations, and that per-setup expectancy
(SPEC 15.5's rule for the *entry* models, for the same reason) be the unit of comparison
rather than per-trade. `TargetPlan.gate_applies` carries the flag so a downstream
comparison cannot lose it silently.

### 7. Four stop models are worth 1.36 tests, not 4

D-012 established for the four order-block definitions that agreement makes them worth
**1.77** independent tests, and that the multiple-testing correction has to use `M_eff`
rather than the nominal variant count. SPEC 16.6 asks for the same paired-variant treatment
of S1–S4, so the same arithmetic is now run for them:

**`M_eff` = 1.36 over 160 setups**, by the same Galwey estimator D-012 §3b settled on, over
the correlation of each model's ATR-normalised distance from the break bar's close.

Lower than the order blocks', and the pairwise table says why: S1 and S2 produce the
**identical price on 58.8%** of setups. S2 is the lowest low of a window that *starts* at
the sweep extreme, so it differs from S1 only when some bar in the setup window went lower
— and `invalidate.new_extreme_atr` caps how much lower it can go without killing the setup
outright. They are close to one model with a rounding difference.

Both an exact-agreement and a within-0.05-ATR column are reported, because D-012 §2 found
exact agreement *understates* redundancy: two definitions picking different anchors at
almost the same price are economically one and arithmetically two.

The correlation is anchored on the **break bar's close**, which no stop model produced —
D-012 §3a's rule, because centring on the per-observation mean across the variables being
compared pins the average pairwise correlation at `−1/(k−1)`, a number about the centring
rather than about the models.

`effective_tests`, `li_ji_effective_tests` and `_eigenvalues` moved from
`bot/research/ob_study.py` to `bot/research/stats.py` as part of this, following D-011 §3's
rule that shared primitives live in `stats`. They are re-exported from `ob_study` so the
Phase 11 report and its tests keep their original names.

### 8. A testing lesson: two guarantees, one reachable test

The drawdown ladder is protected twice — a config validator, so no configuration can
*express* a multiplier above 1.0, and a clamp in `drawdown_multiplier`, so no arithmetic
can *produce* one. Defence in depth, deliberately.

**A mutation deleting the clamp survived the entire suite.** Every test reaching
`drawdown_multiplier` builds its config through `load_config`, where the validator fires
first — so no reachable input could distinguish a clamped implementation from an unclamped
one. The clamp was untested precisely *because* the other guarantee worked.

Fixed with a test that bypasses the validator (`model_copy`, which does not re-validate) to
ask the second question directly. 15 of 15 mutations are now caught.

The generalisable form, and the reason it is written down: **when two mechanisms enforce
one rule, the outer one hides the inner one from every test that goes through the front
door.** This is the fifth instance in this project of a check that looked fine and measured
nothing (STATE.md §7), and the first where the cause was redundancy rather than imprecision.

### 9. Scope: why half of SPEC 17 is in this phase

SPEC 27 lists Phase 13 as "risk management" and SPEC 17 belongs to no phase explicitly.
The split taken here:

**In Phase 13** — SPEC 16 complete (S1–S4, the 16.2 buffer, the 16.3 caps), SPEC 17.1's
target *placement* and 17.2's minimum-RR gate, SPEC 18 entire.

**In Phase 14** — SPEC 17.3 (break-even, trailing), 17.4 (time and calendar exits), 17.5's
intrabar resolution *for exits*, and the execution of T3's ladder and T4's trail.

The line is SPEC 19's own: `RR_BELOW_MIN` fires in state CHOCH_CONFIRMED, alongside
`SL_TOO_WIDE`/`SL_TOO_TIGHT` (16.3) and `SIZE_BELOW_MIN`/`SIZE_ABOVE_MAX`/`SIZE_UNDER_RISK`
(18.2). Those three are the pre-trade rejections this phase's gate exists to exercise, and
implementing two of the three would have left the gate half met. Everything in 17 that
needs an *open* trade went to 14.

SPEC 16 had to come here regardless: sizing takes `sl_distance` as an input, so a sizing
test without the stop models tests arithmetic on a number nothing produced.

### 10. Also deliberately left out

- **`symbol.stops_level` is 0 points and `spread` is `None` everywhere.** SPEC 16.2's
  buffer takes the max of an ATR term, a spread multiple and the broker's stops level; the
  latter two need Q1 and Q2. Both are wired, scenario-tested with constructed values, and
  contribute nothing to any number in the Phase 13 report. `spread=None` *omits* the term
  rather than passing zero, so "no spread data" never reads as "the spread was zero".
- **The FX conversion is exercised only where it is the identity.** Every number here is
  EURUSD on a USD account, where `fx_rate(quote → account)` is 1. The conversion is
  implemented and a missing rate **raises** rather than defaulting to 1.0 — SPEC 18.2's
  "its absence blocks the inclusion of any symbol" as code, and the 40%-error case it warns
  about is a JPY-pair case that needs the rate series.
- **The correlation cap is scenario-tested on one symbol.** `correlation_clusters` builds
  signed single-linkage clusters and `_signed_cluster` implements SPEC 18.7's directional
  equivalence (long EURUSD and short USDCHF are one exposure), both pinned. What cluster
  membership actually looks like on the ten majors is a multi-symbol measurement.
- **`risk.counter_monthly_multiplier`** is declared and inert until the bias engine exists
  (Phases 2–4), the same position as `bias.gate_mode` in the MSS engine (D-009).
- **SPEC 18.8's live-only safety layer** — broker reconciliation, stop verification,
  watchdog heartbeat — is Phase 17. SPEC 17.4's rule that no live behaviour may exist that
  the backtest cannot reproduce is what keeps it out of the signal path, not out of scope.
- **The limits-on / limits-off comparison SPEC 18.9 asks for is reported and does not yet
  mean anything.** With no exit policy, the ledger fills to `max_open_positions` on the
  third setup of each year and rejects everything after it. The switch is verified to work
  and to leave the *strategy's* own rejections in place; the comparison it exists for needs
  an equity curve, and is Phase 14's.

---

## D-015 — The backtest engine, and four ways a free lunch gets into one

| | |
|---|---|
| **Date** | 2026-08-27 |
| **Status** | ACTIVE |
| **Trigger** | Phase 14 implementation (the backtest engine, BACKTEST_PROTOCOL in full) |

The gate is *"full protocol; replay + shifted-data tests green; cost sensitivity run"*, and
all three pass. What is worth recording is not that they pass but what building them turned
up: **four separate places where the engine was crediting a price the trade could never
have obtained**, each arrived at by a different route, and none of them visible in a diff.

They share a shape. In every case the code took a price that was *planned* and treated it as
a price that was *paid*. That is the same error D-013 section 1 recorded from the other
direction — reasoning from a label rather than from what the market could actually have
done — and it is evidently the characteristic bug of this layer.

### 1. SPEC 1.7 asks for a ULID and SPEC 25.1 forbids what a ULID is made of

A ULID is 48 bits of wall-clock milliseconds plus 80 bits of randomness. SPEC 25.1 requires
that the same data and the same `config_hash` produce byte-identical output — *"no
wall-clock reads, no unseeded randomness"* — and 25.4 prohibits `datetime.now()` anywhere in
the signal path. **A conforming ULID would break reproducibility on its own**: two runs over
identical data would produce different ids and `events.jsonl` would never be byte-identical.

Resolved by keeping the ULID's shape and ordering property and deriving both halves
deterministically (`bot/core/ids.py`): the timestamp is **the bar's** millisecond, and the
entropy is a BLAKE2b digest of the object's **natural key** rather than randomness.

**The problem it fixes was under-recorded by two orders of magnitude.** STATE.md section 6
item 10 said "206 collide across five fixture years". The real figure is **23,314 duplicates
out of 30,637 ids — 76%** — and it affected every object kind, including the ones already
namespaced by symbol and timeframe, because their sequence restarted with each run. Pooling
trades from two runs would have joined unrelated objects to each other. It is now 0.

Two things learned while getting there:

**A sequence number cannot be in the key.** The first version put the candidate `seq` in a
level's natural key. `seq` counts candidates built so far, which depends on how much history
the run was given — so a level's id changed under truncation, and because admission order
tie-breaks on the id, the book silently reordered. It broke
`test_admission_order_is_prefix_stable`, which is SPEC 25.2 doing exactly its job. Content
addressing is not a nicety here: it is what makes an id survive a different date range.

**Content addressing buys something a sequence cannot**, which is why it stayed after the
bug was understood: the same logical object gets the same id in every run. Two runs over
overlapping periods now agree about which level is which, which is the actual precondition
for pooling.

### 2. A documented finding whose mechanism was accidental

Changing the id format moved `PROTECTED_SWING`'s share of anchored sweeps from under 2% to
**3.3%**, breaking a test that pinned D-006's finding.

The finding was right and its cause was luck. A `PROTECTED_SWING` duplicates a `SWING_*` at
the identical price ~95% of the time, and the two share a tier *and* a confirmation bar — so
SPEC 8.8's stated tie-breaks (tier, then time) **both tie**, and the winner fell out of
whatever order the levels happened to sit in. That order came from the id, which came from
the order `build_candidates` happens to construct sources in. Nothing stated it as a rule and
no test pinned it; reordering that function would have changed it just as silently.

`_pick_survivor` now has an explicit third key, `_SOURCE_PRECEDENCE`, with the id as a fourth
so the order is total. The rule it encodes is the one D-006 was implicitly relying on: **a
primary structural object beats one that annotates it or is derived from it.** A protected
swing is a strength annotation on a swing (D-006, STATE.md section 6 item 6), so the swing is
the level.

The general lesson: *a behaviour that no rule states and no test pins is not a design, it is
a coincidence with a docstring.*

### 3. Shadow trades were entering at prices price never reached

SPEC 15.6: *"the would-have-been trade's forward outcome ... is computed and stored as a
shadow trade"*, so that "did we miss the good ones?" is answerable per entry model.

The first implementation simulated an entry at the planned limit price from the MSS bar,
**whether or not price ever traded there**. A bullish limit sits *below* the market, so every
shadow got a free discount. The signature was unmistakable once looked at: **38 take-profits
against 2 stops, mean +1.57R**, while the filled population sat at roughly zero.

A shadow trade is counterfactual on the **cancel**, never on the **fill**. The question is
"would this have been good had the gate not stopped it", not "would it have been good had
price gone somewhere it never went". The fix re-resolves the same order with the cancels
removed; if it would have filled, the shadow starts from that fill, and if it would still
never have filled there is **no shadow**, because there was no trade to miss. Shadows fell
from 54 to 11 and their exits now split across stops, targets and time.

Two smaller instances of the same confusion, found in the same pass:

**Fill rate was counting admitted trades.** SPEC 15.5's coverage is an *order* property —
filled orders over armed orders. Counting portfolio-admitted trades instead folds SPEC 18.4's
position cap into the model comparison, and the cap bites hardest on whichever model fills
most. Model A read **58%** that way, against the 100% Phase 12 measured.

**The bake-off runs with the limits off**, for the same reason. A comparison with the
portfolio engaged measures the cap, not the models.

### 4. Protocol section 9's most-emphasised test does not always reach its own target

*"The skip-10% test deserves emphasis: a strategy whose entire profit comes from three trades
will fail it, and no other test in this suite reliably catches that."*

Its stated acceptance is *"5th-percentile net return > 0"* — a **sign** test. Concentration
shows up as a **drop**. Measured on constructed sequences:

| sequence | top-3 share | p5 return | degradation | sign test |
|---|---:|---:|---:|---|
| diffuse edge, 300 trades | 26% | +7.9% | 37% | passes |
| carried by 3 of 300 | 571% | −1.2% | 158% | fails |
| carried by 1 of 40 | 190% | −1.3% | 189% | fails |
| **carried by 3 of 60** | **161%** | **+1.7%** | 49% | **passes** |

The gap is narrow and specific: dropping 10% of 60 trades removes 6, so it rarely removes
all three that carry the result, and the sign survives. Two companion statistics close it and
cost nothing — the **share of total R held by the best k trades** and the **degradation** of
the skip test's own 5th percentile against the unskipped return.

The test is not wrong and is not replaced. It is reported with a companion, and the
companion is what fails on the fourth row.

A related guard: **concentration is undefined on a losing book.** A share of a negative total
is a negative number that sails past a "< 1.0" threshold. The Phase 14 fixture reported
**−5.97 and "passed"** before this was caught. It now returns no verdict, which is the honest
output for a question about how a *profit* is distributed.

### 5. The entry bar is part of the trade

The exit walk started at `entry_bar + 1`. A trade that filled and then hit its stop **inside
the same bar** was therefore carried to the next bar's open instead. Every such loss was
delayed and some were missed outright.

**6% of trades on the Phase 14 fixture close on their entry bar** — 15 of 248 — and including
them moved measured expectancy by up to **0.04R per trade**. BACKTEST_PROTOCOL section 10.1's
go/no-go threshold is +0.10R, so the bug was worth 40% of the minimum acceptable edge, in the
flattering direction.

The two fill shapes are not symmetric on that bar and the code now distinguishes them:

- **A market order fills at the bar's open**, so the whole bar happens after the fill and
  both levels are checkable as on any later bar.
- **A limit fills inside the bar.** For a buy limit at `p` the fill is the *first* touch of
  `p`, so the bar's low — which is below `p` — necessarily came at or after it: **a stop below
  the entry was reached, by continuity.** That is D-013 section 1's argument applied to the
  other end of the trade. The bar's *high*, though, may have printed before the fill on the
  way down, so a target hit on the entry bar is **not** credited unless M1 resolves it.

That asymmetry is the honest reading rather than a cautious one: one direction is proved by
continuity and the other is not. The MFE on such a bar is clamped to the entry for the same
reason — an excursion the trade could not have had is not an excursion.

### 6. The entry models do not arm on the same setups, and the difference has a direction

SPEC 15.8 asks for the five models as *"separate pre-registered variants over identical
setups, from one shared setup stream, so the comparison is paired."* The stream is shared.
The **arming** is not:

| | setups | median displacement |
|---|---:|---:|
| Model A armed | 66 | 2.10 ATR |
| Model A rejected `SL_TOO_WIDE` | 99 | 2.57 ATR |
| all setups | 165 | 2.30 ATR |

Model A enters at the break price with its stop at the sweep extreme, so **its stop distance
*is* the displacement leg** — and SPEC 16.3's 2.5-ATR cap rejects it wherever that leg is
large. The other four enter on a retracement and carry a stop roughly a third as wide (median
1.51 ATR against 2.24).

So the cap does not thin model A at random. **It removes its strongest-displacement setups
specifically**, because for this model a strong displacement is a wide stop. Protocol section
4.4's `E_setup` does not fix it: `E_setup` divides by each model's *own* qualified count, so
model A is scored on its easy 40% and model B on nearly everything.

`compare_models` now also reports **`E_all_setups`**, dividing by the shared denominator —
every MSS setup, armed or not. A model that cannot arm takes no trade and earns nothing,
which is exactly what a per-setup figure exists to charge for. It is the only column in that
table comparing the five over one population.

This is D-014 section 6 (T4 exempt from the RR gate) recurring on the entry axis, and the two
together say something worth stating plainly: **"paired" is a property of the comparison, not
of the setup stream, and every gate between the stream and the trade can break it.**

### 7. SPEC 21.3's counterfactual, measured two wrong ways at once

*"Rejection record ... `forward_return_r` computed by simulating the planned trade over the
next `analysis.forward_bars` bars."* Implemented literally, it distorts twice:

- **From the planned entry price.** A bullish limit sits below the market, so measuring
  forward from it starts at a price the trade never paid. The fixture reported a median
  **+1.7R at a 92% win rate — on a random walk.**
- **In R against the planned risk.** Several rejection reasons are precisely that *the risk
  was wrong*: `SL_TOO_TIGHT` rejects a 0.37-pip stop, and dividing by it reported **+7.0R**.
  The denominator, not the market, is what that number measures.

The reference is now the **MSS bar's close** — the price at the moment the gate fired, shared
by every gate — and the normaliser is **ATR**, this project's normaliser everywhere else
(SPEC 1.6) and defined for every setup regardless of what the gate objected to.
`OPPOSING_SWEEP` then reads **+0.002 ATR at a 45% hit rate**, which is the correct answer on
a random walk and was invisible before.

### 8. One row of that table is a tautology and will be misread

`ENTRY_EXPIRED` still reads **+1.37 ATR at 76%** after the fix, and it is not a bug.

An order expires unfilled precisely when price never retraced to the limit — which for a
bullish setup means price went *up* and kept going. Measuring the forward move in the setup's
direction on that population selects for exactly that move. **The setups a limit misses are,
by construction, the ones that ran.**

It must never be read as "the expiry rule destroys edge". It is the mechanical cost of using
a limit at all, it is the quantity SPEC 15.6's shadow trades exist to price, and the correct
comparison is against what a *market* entry on the same setups would have paid — model A's
column in the bake-off — not against zero. Recorded here because the rejection table is the
most useful artefact the engine produces and this row is the one most able to mislead.

### 9. Two-pass, and why R being portfolio-free is structural

The engine is deliberately split: **pass one is geometry** (stop caps, RR gate, arming, fill,
exit path) and **pass two is the portfolio** (limits, sizing, equity). Everything pass one
produces — entry bar, exit bar, **R** — depends only on prices and configuration.

Protocol 4.1 makes R primary because *"net return conflates edge with position sizing and
with the compounding path; R-expectancy is the property of the strategy itself."* Computing
it in a pass that cannot see equity is how that claim is made true rather than asserted, and
`test_r_multiple_does_not_depend_on_equity` pins it.

**Pass one does not size, and an earlier version's doing so was a real leak.** SPEC 18.2's
rejections are functions of equity, so running them in the portfolio-free pass made its
population depend on a nominal account size chosen for convenience — change the constant and
the funnel changed. `evaluate(..., skip_sizing=True)` now keeps the two apart, and
`SIZE_BELOW_MIN` appears where it belongs, in pass two, where Phase 13's account sweep already
measured how much of the stream it removes.

**Entries are processed before exits within one bar.** A market entry fills at the bar's open
while an exit happens somewhere inside it, so opening first is what the prices support. It is
also the choice that does not invent capacity: freeing a position slot using a close whose
time within the bar is unknown would let a trade in that the limits should have refused.

### 10. A redundant guard is an untested guard, one phase later

D-014 section 8 recorded that a mutation deleting the drawdown ladder's clamp survived the
entire suite, because a config validator fired first and hid it. **The same thing happened
again in `manage_stop`**: each management branch clamped internally (`max(stop, be)`,
`max(stop, trail)`) *and* the function clamped at the end, so the final clamp was unreachable
and a mutation removing it changed nothing.

Fixed by restructuring rather than by adding a test: the branches now *propose* and one clamp
*decides*. The invariant has one enforcement point, that point is reachable, and a mutation
removing it fails.

Seventeen mutations were run against the Phase 14 suite. Six survived the first pass — the
two clamps above, two cost tests whose tolerances were wider than the effect they were
meant to detect, the pass-one equity leak, and the losing-book concentration guard. All
seventeen are now caught.

### 11. What Phase 14 deliberately did not build

- **The falsification suite (protocol section 6)** — shuffled liquidity (H3), sweep-only,
  CHoCH-only, reversed-order and random-time controls (H4). The protocol calls these *"the
  most informative runs in the project"* and they are the phase's largest omission. They are
  studies rather than engine, and every one of them asks a question that is meaningless on a
  fixture whose true effect is zero by construction: a shuffled-liquidity control that
  "performs the same" as the real thing proves nothing when neither performs at all. They
  need real bars, and they are the first thing to build after the data lands.
- **Walk-forward (section 8) and the OOS budget ledger (section 7).** Both are procedures
  over real splits. There is no out-of-sample period to spend budget on.
- **The pre-registration (section 1).** Due before the first *strategy* backtest, which this
  is not — a synthetic run validates an instrument. Writing it is the first action when data
  arrives and it must precede the first real run, because a pre-registration written after
  seeing a result is not one.
- **`events.jsonl` and the Parquet artefacts (SPEC 21.1).** The engine holds trades and
  rejections in memory and the report reads them there. The log is the primary artefact in
  the specification and `trades`/`rejections` are meant to be *derived* from it; that
  inversion is fine while one process produces and consumes both, and must be fixed before
  Phase 16's paper trading, which reconciles a live log against a backtest.
- **Partial fills** (SPEC 15.4, 24 item 9). Modelled nowhere; the engine fills whole or not
  at all. Needs broker behaviour to model, and the spec already says such trades are excluded
  from headline expectancy and reported separately.
- **The MTF bias gate.** `bias.gate_mode = none` throughout (Phases 2–4 unbuilt), so every
  count in the Phase 14 report is an upper bound: a real gate can only reduce it.

---

## D-016 — The falsification suite, and an acceptance criterion that geometry can satisfy

| | |
|---|---|
| **Date** | 2026-08-28 |
| **Status** | ACTIVE |
| **Trigger** | Building `BACKTEST_PROTOCOL.md` sections 6.3 and 6.4 (`bot/research/falsification.py`, `scripts/falsification_report.py`) |

D-015 item 11 listed this as Phase 14's largest omission and said it needed real bars. That
is still true of the *results*. It turned out not to be true of the *construction*: building
the arms surfaced a problem in section 10.1's acceptance criterion that has nothing to do
with what data it is run on, and would otherwise have been discovered on real bars only by
being acted on.

### 1. Section 10.1's falsification row can be cleared on stop width alone

The row is the one section 10.1 calls the most important and the most likely to fail:

> *"Full model beats **every** control in 6.3 and 6.4 by a margin whose CI excludes zero."*

On the synthetic fixture the baseline beats `sweep_only` by **+0.125 R per setup, CI
[0.019, 0.229]** — excluding zero, so the row is satisfied — **on a random walk, where the
true difference is zero by construction**. That should be impossible.

It is not a bug in the engine or in the arm. **R is a ratio, and the arms do not share its
denominator.**

| | baseline | `sweep_only` |
|---|---:|---:|
| median stop (ATR) | 2.24 | 0.96 |
| E/setup, gross R | −0.034 | −0.051 |
| cost, in R | +0.028 | +0.135 |
| E/setup, net R | −0.062 | −0.186 |

The gross delta is **+0.018 R, CI [−0.084, 0.123]** — containing zero, which is the correct
answer. The entire net-R gap is transaction cost. A control entering at the sweep
confirmation stops just beyond an extreme a bar or two old; the baseline waits for a CHoCH
and stops beyond an extreme up to twelve bars back. A fixed spread and commission against a
stop half as wide is twice the cost *per R* — and in net R that is indistinguishable from
signal.

**Three of the five arms are affected**, and the inflation tracks stop width exactly:

| Arm | median SL (ATR) | net delta | gross delta | inflation |
|---|---:|---:|---:|---:|
| `shuffled_liquidity` | 2.27 | −0.041 | −0.045 | 0.004 |
| `sweep_only` | 0.96 | +0.125 | +0.018 | **0.107** |
| `choch_only` | 2.23 | −0.045 | −0.056 | 0.011 |
| `reversed_order` | 0.96 | +0.092 | −0.012 | **0.104** |
| `random_time` | 1.18 | +0.080 | +0.001 | **0.078** |

`random_time` is the one that matters most, because it is the **floor**: a baseline that
beats random entry in net R has not thereby shown it has a signal, only that it waits longer
before committing.

**Every arm is reported in both currencies and no decision has been taken.** Section 10.2
forbids moving a criterion to make a result appear, and that applies to a criterion as much
as to a parameter. The three options, for the pre-registration to settle *before* real bars:

1. Read the row in gross R — a test of signal, losing the point that a strategy must pay its
   costs to be worth trading.
2. Keep net R and require **both**, treating a net-only win as not demonstrating the
   sequence. This is what the report does.
3. Match stop distance across arms — which changes what the controls are. A sweep-only arm
   with the baseline's stop is not "enter on sweep confirmation".

This is the same species as D-015's four free lunches and D-013 section 1: a number that
looks like a measurement of the market and is a measurement of the construction.

### 2. The shipped default entry model cannot run half the section 6.4 suite

At `entry.model = C` (the default), `sweep_only` and `reversed_order` arm **zero** orders —
100% `NO_FVG_AVAILABLE`. Structural, not a fixture artefact: both enter at the sweep
confirmation, so their displacement leg spans at most `sweep.max_confirmation_bars` and has
a **median length of 0 bars, maximum 2**. An FVG needs three. So section 10.1's row is
undefined at the shipped default — "beats every control" cannot be evaluated against an arm
with no trades.

The suite therefore runs at **model A**, which is also the only 100%-fill model (D-013
section 5), so an arm's trade count reflects its setup count rather than its FVG
availability. Pinned by a test, because the natural "fix" is to change the arm.

### 3. `choch_only` must not be built on `structure.py`'s CHoCH events

The obvious construction is wrong and fails invisibly. A structure `CHOCH` is a trend flip
through the **protected** level; SPEC 11.2's CHoCH — the one the baseline trades — is a break
of the **last unbroken swing** inside the sweep window. An arm built on the former differs
from the baseline in the definition of the thing under test, and its inevitable null then
reads as *"the sweep requirement only reduces sample size"* when what was measured was a
stricter break rule.

So the arm calls `MssEngine._major_reference` itself, private and all. `breaks_level`'s own
docstring already records that two copies of the break test in two modules is how SPEC 11.2's
"this test and no other" quietly stops holding; the same argument applies one level up, to
the *selection* of what gets broken.

**The two counts are too close for a size check to catch the error.** The wrong construction
gives 26 events against 82 baseline setups on one fixture year, and 43 against 41 on another
— larger on one and smaller on the other. The test asserts on *which bars fire* instead.

### 4. A control is a setup stream, not a second engine

Four of the five arms substitute `Market.setup_override`; the fifth substitutes the level
book through `analyse_sweeps(level_transform=...)`. Both seams are inert when unused, pinned
by tests, and the 578 pre-existing tests are unchanged.

The alternative — reimplementing the pipeline per arm — was rejected for a specific reason
rather than on style: an arm that re-stated the admission order, the merge fixpoint or the
fill discipline could differ from the baseline for a reason that is not the one being tested,
and **no amount of care would make that visible**, because the arm's output looks the same
either way. It is the one property that makes a control a control.

The shuffle holds count-per-day and age **exactly** rather than in distribution — same
`confirmed_at`, same `formed_at`, same side/source/tier/strength; only `price` moves — which
is stronger than section 6.3 asks and removes a class of confound: a difference between the
arms cannot be that one had more levels, or older ones. Prices are redrawn from the empirical
signed-distance-in-ATR pool **per side**; losing the sign or the side would test whether
levels are on the correct side of the market, an easier question the real book wins trivially.

### 5. Three guards nothing in the fixture reaches — the pattern, for the third time

D-014 section 8 and D-015's `manage_stop` both recorded a rule enforced somewhere no test
goes. Three of the first eighteen mutations here survived for the same reason:

| Guard | Why the fixture never reaches it |
|---|---|
| `placeholder_sweep`'s `trigger_bar` in the id key | No two legs in the fixture share an extreme, so no collision occurs — but `choch_only` scans every bar and two references broken three bars apart can share one. The cost is silent and doubled: `arm_from` credits one setup's R and scores its twin 0.0, and `run`'s `live` dict loses a position to an overwritten key |
| `_leg_extreme`'s direction | Inverting it still produces trades and still reports a null |
| `choch.max_reference_distance_atr` in `choch_only` | At the FROZEN default of 3.0 it rejects **nothing** — the widest reference on the fixture sits at 2.81 ATR, across 198 events in three years |

The third is different in kind from D-014's four unreachable defaults, and the difference
matters: those were **arithmetic impossibilities**, this is a **measurement**. It is an
ABLATION parameter over {2.0, 3.0, 4.0} and **at 2.0 it binds hard**, which is how the branch
is now tested. It also echoes STATE.md section 3 — the Phase 9 gate is not robust to this
same parameter — making this the second place where 3.0 sits just past where the fixture
reaches.

**18/18 mutations are caught.**

### 6. Three asymmetries between the arms that no construction removes

Recorded because each is a real limit on what the report can say, and each is easier to
discover here than in a result:

1. **`choch_only` and `random_time` have no sweep**, so their setups carry a placeholder
   `SweepEvent`. Every field that would be a measurement is NaN or an out-of-range sentinel
   (`level_tier = 0`), so a liquidity breakdown over those arms is loudly wrong rather than
   quietly plausible. `ControlSpec.has_liquidity` says which arms may be broken down that way.
2. **The leg origin is *searched* in the sweepless arms and *clamped* in the baseline.**
   D-009 section 11 records that the real path never looks for the leg origin. Without a
   sweep there is nothing to clamp to. **This favours the control** — a searched origin can
   only displace at least as much.
3. **Reversing the order moves the stop anchor** onto the event being entered on. No
   construction reverses the order and holds both the trigger and the anchor fixed; they are
   the same two events. `reversed_order` holds the SL/TP *models* constant, which is what
   section 6.4 asks, and not the distance — which is exactly what item 1 of this decision
   then bites on.

### 7. What is still not built

An **end-to-end positive control**: an injected edge surviving the whole chain from prices to
a `DIFFERENT` verdict. The positive control that exists covers the comparison layer, and the
per-arm tests cover each construction, but nothing demonstrates that a real conditional edge
in the *price series* comes out the other end. Building one needs a synthetic market with a
genuine SMC edge — injecting drift after each MSS changes the prices, which changes the
sweeps, which changes the MSS set. Recorded as a limitation rather than solved.

Section 6.5's ablation matrix remains unbuilt and is the natural next piece.

---

## D-017 — The ablation matrix, and five components that cannot be toggled

| | |
|---|---|
| **Date** | 2026-08-28 |
| **Status** | ACTIVE |
| **Trigger** | Building `BACKTEST_PROTOCOL.md` section 6.5 (`bot/research/ablation.py`, `scripts/ablation_report.py`) |

Section 6.5 names **nineteen** components to toggle one at a time. The matrix's first
output is an accounting of how many of them can be toggled at all, and it is the part that
does not depend on the fixture:

| Status | Meaning | Count |
|---|---|---|
| `PAIRED` | Same `Market`, only `run()` differs; compared setup by setup | 21 variants |
| `UNPAIRED` | The toggle changes the pipeline, so the Market is rebuilt and the populations differ | 13 variants |
| `BLOCKED` | Specified, but its engine is unbuilt (Phases 2-4) | 2 rows |
| `ABSENT` | Named in 6.5 and **exists nowhere in the codebase** | 3 rows |

### 1. Three components named by section 6.5 are not implemented

**`session filter` and `killzone filter`.** `SessionWindowConfig.role` admits `"killzone"`
and `defaults.yaml` defines LONDON_KZ and NY_KZ with `enabled: false`, but the only code
that reads `role` is `liquidity_session_names`, which selects liquidity *sources*. **No
module gates an entry on the session it fires in.** Enabling the killzone windows would add
two more session windows, not a filter.

**`liq.tier_confirmation_tf`** is the serious one. Section 6.5 names its `{'3': 'H1'}` value
**"the D-002 counterfactual"** — the alternative to the decision that makes this a
session-to-session swing model rather than the intraday one the SMC source material
describes (`STATE.md` §5). The field is declared in the schema, documented as ABLATION, and
**read by no module**: `analyse_sweeps` steps the H4 series for every tier by construction.
**D-002 cannot currently be tested against its own alternative.**

Pinned by a test that greps the package, so implementing any of the three fails the test
rather than silently leaving the matrix claiming ABSENT.

### 2. `ob.definition` was hardcoded in the engine, and is inert even once fixed

`_pass_one` passed `definition=ObDefinition.A_LAST_OPPOSING` as a **literal**, so
`cfg.ob.definition` was ignored: the four SPEC 13.2 variants — a documented ABLATION and the
entire subject of Phase 11's bake-off — were unreachable through the engine. Setting the
parameter changed nothing, and no run had ever used B, C or D end to end.

Fixed by passing `None`, which `propose` resolves from the config. **The default is
byte-identical** (a test asserts it), and the four now produce visibly different results —
at entry model D, 19/15/12/2 trades on one fixture year.

At the **shipped defaults they remain inert**, because entry model C reads an FVG and stop
model S1 reads the sweep extreme: neither consumes an order block. So SPEC 13.8's
requirement to report the agreement matrix alongside performance still cannot be exercised
from the default config, and Phase 11's `M_eff = 1.77` has no end-to-end counterpart.

### 3. A fifth default that cannot fire — **RETRACTED, see D-019**

> **This section named the wrong cause and is superseded by D-019.** T2 armed nothing
> because the engine never passed the liquidity book to the target gate, and because
> `_opposing_side` was inverted — not because of `tp.min_target_rank`. The one-line
> measurement that would have caught it (set the rank to 0 and see whether anything
> changes: nothing did) was not taken before the cause was named. The paragraph below is
> kept as written, because a retraction that edits away the original claim hides what the
> mistake was. **T3's half of it stands**; only the T2 half was wrong.

D-014 recorded four. **`tp.min_target_rank = 2.0` is a fifth**: T2 arms on **zero** setups,
`NO_TARGET_AVAILABLE` on every one, because no opposing liquidity level ever reaches rank
2.0. T3 also arms nothing, for the reason D-014 item 4 already gave.

T2 is the more consequential of the two. T3 is a ladder whose first rung is set below the RR
gate — an arithmetic mismatch between two parameters. T2 is **the only target model that
aims at a liquidity level**, which is the one place the strategy's own thesis about where
price is *going* would enter the exit rather than the entry. It has never produced a trade.

**Two of the four TP models therefore cannot be ablated at the shipped defaults**, so
section 6.5's "each TP model" row reduces to T1 vs T4.

### 4. INERT is not "no measurable effect", and section 6.5's rule cannot tell them apart

Section 6.5's decision rule is *"a component whose delta CI spans zero is reported as 'no
measurable effect' and its default stands"*. It is conservative in the right direction and
is followed literally. But **7 of 34 runnable variants changed the outcome of zero setups**,
and for those the sentence is wrong: it says the component was tested and did not matter,
when the component was never reached. Identical numbers, opposite conclusions.

The clearest case is the time stop: **`max_bars_in_trade` at 15, 30, 60 and off are all the
same run**, because no trade in the fixture lives long enough for any horizon to bind. A
reader given only "no measurable effect" would conclude the time stop is decoration; the
data says it was never reached. Break-even at 1.5R and structure trailing are the same —
neither trigger is ever hit.

So the matrix reports `INERT` (the toggle changed nothing) and `NO_TRADES` (the variant
armed nothing) as their own statuses, **outranking any statistical verdict**, and gives them
no delta and no CI. This is D-014 §8 and D-016 §5's "a guard nothing reaches" one level
out: there it was a rule no test exercised, here it is a rule no *data* exercises, and the
report is where it has to be visible.

### 5. "One component at a time" is not achievable where components share objects

Section 6.5's method assumes a component can be toggled in isolation. Three of its rows
cannot be, because the default entry model consumes something another component produces:

| Toggle | Coupled to | What happens |
|---|---|---|
| `disp.mode = bar` | `entry.model = C` | `leg` mode confirms displacement *by finding an FVG* (`require_fvg`), so model C always has one. `bar` mode (SPEC 10.3) produces none: **113 of 145 setups reject with `NO_FVG_AVAILABLE`** and the arm fills 2 trades. The row measures the entry model, not the displacement mode |
| `disp.require_fvg = False` | `entry.model = C` | The same mechanism from the other side: nearly doubles the setup count, fills the same number of trades |
| `ob.definition = B/C/D` | `entry.model = C`, `sl.model = S1` | Neither default consumes an order block, so all three are INERT |

All three need a second axis to be read at all. **A one-at-a-time matrix cannot express
that**, and reporting these rows as though it could would attribute the entry model's
dependency to the component being toggled. Same shape as D-016 §2, where two falsification
controls could not run at the default entry model either — the second time the shipped
default has turned out to be the awkward one to measure against.

### 6. Paired and unpaired are different measurements, and the gap is large

A `PAIRED` row runs two configurations over one setup stream and takes the difference per
setup, so what is bootstrapped is the variance of a *difference* and most market noise
cancels. An `UNPAIRED` row rebuilds the Market, so the arms are different populations and
all of that noise is back. On the fixture the median MDE is **0.076 R paired against
0.181 R unpaired — a factor of 2.4**.

It is not presentational. **An unpaired delta cannot separate "this component changed
outcomes" from "this component changed what we traded"** — a sweep filter removing half the
setups shifts expectancy by selecting a different population, and reading that as the
filter's value is how a filter that only reduced sample size gets recorded as one that
improved the edge.

### 6a. Two filters that are very active and change almost nothing

**`sweep.max_penetration_atr`**, over the three fixture years:

| cap | confirmed sweeps | setups | `OVER_PENETRATION` | `ACCEPTED_THROUGH` |
|---:|---:|---:|---:|---:|
| 0.5 | 1,713 | 127 | 1,722 | 156 |
| **1.0 (default)** | **2,298** | **165** | **460** | **700** |
| 2.0 | 2,364 | 168 | 5 | 1,015 |
| 10.0 | 2,364 | 168 | 0 | 1,017 |

At its default the cap rejects **460** sweeps. Raising it to 2.0 admits 66 more confirmed
sweeps and **three** more setups; above 2.0 nothing changes at all. So the filter is very
active at the sweep level and almost irrelevant at the setup level, because the sweeps it
admits are removed downstream — and because its rejections *reappear* as
`ACCEPTED_THROUGH` as it loosens (156 → 700 → 1,015), which is the near-substitution
SPEC 9.2 warns about in as many words.

**"This filter does nothing" and "this filter changes nothing" are different statements**,
and a one-at-a-time delta reports only the second. On the half-year fixture the tests use,
the 2.0 arm is byte-identical to the baseline; over three years it moves three setups.

**`disp.min_leg_atr = 0` admits nothing at all**, so `require_fvg` and `min_body_ratio`
already imply the magnitude threshold. SPEC 10.6 calls them partially redundant and ablates
them jointly for that reason; here the redundancy is total, which means `min_leg_atr` — a
TUNABLE carrying section 5.5's plateau requirement — is unmeasurable one-at-a-time.

### 7. Statistics

Section 6.5 requires a **block-bootstrap** CI, which `stats.py` had only for the one-sample
case. `bootstrap_diff_ci` gained `block_a`/`block_b`, and the stationary walk was extracted
to `_stationary_indices` so both share one implementation.

**The block length is derived from the calendar, not from an observation count.** Section
5.3 states it as *"mean block length ~ 20 trading days"*, and the arms do not share a trade
density: an arm producing four times as many setups over the same calendar needs four times
the block length to span the same twenty days. A single fixed count would resample one arm
over a materially different horizon from the other.

Paired rows use a **sign-flip** permutation test and the difference-series MDE, not the
pooled two-sample versions. The pooled test's null is *"these two samples came from one
distribution"*, which discards the pairing — precisely the power the pairing exists for.

### 8. The one measurable row, and what killed it

`sl.model = S3` reports a delta of +0.078 R with a CI of [0.007, 0.173] and **p = 0.004** —
the only one of 34 rows to exclude zero, on a random walk. **Benjamini-Hochberg at q = 0.10
takes it to q = 0.153 and it does not survive.** Its gross delta agrees in sign and size, so
it is not D-016 §1's cost confound; it is one try out of 34.

This is the project's recurring statistical lesson arriving where it was designed to be
caught rather than where it was designed to be missed: Phase 7 saw 3 of 20 tests fire on a
random walk, and section 5.6's correction exists for exactly this. **Every default stands.**

---

## D-018 — The pre-registration, and a configuration count that was wrong three ways

| | |
|---|---|
| **Date** | 2026-08-28 |
| **Status** | ACTIVE |
| **Trigger** | Writing `BACKTEST_PROTOCOL.md` §1's pre-registration (`docs/PRE_REGISTRATION.md`, `bot/research/preregistration.py`) |

§1 requires the pre-registration *"before the first strategy backtest"*. Nothing has
triggered it yet — every run so far has been on a random walk and validated an instrument
rather than measured a strategy — which is exactly why now is the only honest moment to
write it. **A pre-registration written after seeing a result is not one.**

Six of the seven items were already determined by existing documents. Writing them down
turned up one problem that would have corrupted every significance claim the project ever
makes, and forced three decisions that §1 requires be closed in advance.

### 1. `M` was wrong three ways, and `M` scales every significance claim

`M` — the configuration count — feeds the Deflated Sharpe Ratio and §5.6's expected-maximum-
Sharpe-under-the-null. It is the number that discounts the best in-sample result against
what pure noise would have produced with the same number of tries. `PARAMETERS.md` §2
states it three mutually inconsistent ways, and none can be reproduced from the schema:

| | `PARAMETERS.md` §2 | The schema that runs |
|---|---|---|
| TUNABLE parameters | 8, including `bias.min_score` | **7** — six gridded plus `risk.pct_per_trade`; there is **no `bias` section at all** |
| `disp.min_leg_atr` grid | 5 values | **6** — the schema includes `0 (off)` |
| Stated product | `5 × 4 × 5 × 5 × 4 × 4 × 5 = 8,000` | that product is **40,000**; 8,000 is the product *without* the trailing `× 5` |
| Declared `M` | **6,912**, "after removing dominated combinations" | **no rule for "dominated" is stated anywhere**, so it cannot be recomputed by anyone |

**`M = 9,600`**, the full Cartesian product of the grids the schema itself declares, for
three reasons:

1. **It is reproducible.** A number nobody can recompute is not a pre-registration.
2. **It is conservative.** A larger `M` discounts harder, which is the only direction that
   cannot flatter the outcome.
3. **"Dominated" is a judgement about results.** Deciding which configurations could not
   have won requires knowing how they perform — the knowledge a pre-registration is
   written before having.

**It is computed, not typed.** `bot/research/preregistration.py` holds the grid and
`tests/test_preregistration.py` parses the `TUNABLE {…}` declarations back out of the
schema field descriptions and fails if the two diverge — plus a test that the *document*
states the `M` the code computes. Writing a second document that could drift from the
schema would have reproduced the exact failure being corrected here.

`PARAMETERS.md` §2 and `BACKTEST_PROTOCOL.md` §5.6 are amended in place with pointers,
rather than silently rewritten, because their figures have been cited elsewhere.

**One grid point is genuinely disputable and is recorded as such.** `disp.min_leg_atr = 0`
is labelled `0 (off)` and is arguably an ablation rather than a tune; reading it that way
gives `M = 8,000`. It is counted, on the conservative principle above, and the alternative
is written down so that adopting it later is visibly a change to a declared number.

**`risk.pct_per_trade` is excluded provably rather than by convention.** SPEC 18.1 makes
`position_size` a pure function of `(equity, risk_pct, sl_distance)`, so risk percent scales
PnL and cannot move R — and R-expectancy is the primary metric. Sweeping it would sweep a
parameter that cannot change the number under test. Asserted against the real sizing
function, not quoted from the spec.

**`bias.min_score` is deferred, and its arrival is named as a re-registration trigger**: it
would take `M` to 48,000 and supersede every correction computed under 9,600.

### 2. Item 4 is fixed as a rule, because it cannot honestly be fixed as dates

No data has been acquired (Q1/Q2). §2.1's table is written *"assuming 2019-01 → 2025-12
available"*. Inventing dates would be worse than fixing none, and leaving the item blank
until the data arrives would mean completing the pre-registration **after** seeing the
sample — the one thing it exists to prevent.

So the split is a rule: **the earliest 4 years in-sample, the next 2 out-of-sample, the
remainder holdout**, chronological and non-negotiable, over a period that must contain 2020,
2022 and one extended range regime. A rule is as binding as a date and yields exactly one
answer. The literal dates are stamped mechanically at acquisition and committed as an
amendment that changes no threshold, no grid and no decision rule.

### 3. D-016 §1 is closed: the falsification row is judged in **both** currencies

D-016 left this open and §1 requires it closed before the first run.

> The full model must beat every §6.3/§6.4 control in **gross R *and* net R**, each by a
> margin whose CI excludes zero.

Neither alone is defensible. **Net R alone can be cleared on stop width** — proved on a
random walk, where the true difference is zero by construction and the baseline still
cleared the bar by +0.125 R (CI [0.019, 0.229]) because its median stop is 2.24 ATR against
`sweep_only`'s 0.96; in gross R the same comparison is +0.018, CI [−0.084, 0.123]. **Gross R
alone** ignores that a strategy has to pay its costs to be worth trading.

The two rejected alternatives — gross-only, and matching stop distance across arms — are
recorded in the document so that adopting either later is visibly a change. Matching stops
is the more tempting and the worse: a sweep-only arm with the baseline's stop is not "enter
on sweep confirmation", so it changes what the control *is*.

### 4. `INCONCLUSIVE` is defined as a verdict distinct from `FAIL`

§10.1 is binary — all eleven rows must hold — and §10.2 covers "not met", so without this
the case where the sample was too small to look gets filed as a failure to find an edge.

> **INCONCLUSIVE**: fewer than 200 OOS trades, **or** the primary metric's minimum
> detectable effect at 80% power exceeds the +0.10 R it is being tested against.

The second clause is the operative one. **A study that could not have detected the effect it
requires has not failed to find it — it has failed to look**, and reporting that as FAIL
claims knowledge the sample does not contain. This project has already learned the same
lesson three times (Phase 7's 3-of-20 false positives, Phase 8's 0.6σ "structure", H5's
`UNDERPOWERED`-is-not-`EQUIVALENT`); declaring it in advance is what stops it being
relitigated once a disappointing number is on the page.

### 5. What the pre-registration declares it cannot evaluate

Named in advance so that none of it can later be presented as a discovery: H6 (no bias
engine), the D-002 counterfactual (`liq.tier_confirmation_tf` read by no module), the
session and killzone filters (not implemented), T2 and T3 (arm no trades at the shipped
defaults), `ob.definition` (inert at the defaults), §5.5's plateaus (never run — a plateau
needs a metric that varies across the grid, and on a random walk it varies only by noise),
and the Phase 9 funnel gate (passes on a projection, not a measurement).

**No default was changed to make any of these evaluable.** §10.2 forbids moving a parameter
to make a result appear; moving one *before* any result exists is a different act, but it is
still a specification change and belongs in a registration of its own. All four are listed
as named new-registration triggers instead.

---

## D-019 — Two bugs behind T2, a retracted finding, and a default chosen by its outcome

| | |
|---|---|
| **Date** | 2026-08-28 |
| **Status** | ACTIVE |
| **Trigger** | Instruction: *"fix `tp.min_target_rank` so T2 can arm"* |
| **Supersedes** | D-017 §3, which named the wrong cause |

### 1. The parameter was not the blocker, and one line proved it

D-017 §3 reported `tp.min_target_rank = 2.0` as a fifth default that cannot fire, on top of
D-014's four, because T2 rejected all 73 setups with `NO_TARGET_AVAILABLE`. **That
diagnosis was wrong.** The rejection reason was read as the cause.

The measurement that settles it costs one line: set `min_target_rank` to **0** and see
what changes. Nothing does — the same 73 rejections — because the filter was never
reached.

> **A rejection reason names the gate that refused, not the reason it refused.**

### 2. Bug one: the engine never gave the gate a book to filter

`evaluate(..., levels=(), ranks=None)` defaults to empty, and neither `_pass_one` nor
`run` passed either. `select_target_level` was iterating an empty sequence and returning
`None` every time, so `min_target_rank` gated nothing at any value.

`Market` did not even carry the book: `build_market` computed it, kept `len(book.levels)`
as a funnel count, and discarded the rest.

### 3. Bug two: `_opposing_side` was inverted, and its own docstring said so

```python
"""A long targets sell-side liquidity above; a short targets buy-side below."""
return Side.SELL_SIDE if direction is Direction.BULLISH else Side.BUY_SIDE
```

`liquidity.Side` defines BUY_SIDE as the pool sitting **above** price, so *"sell-side
liquidity above"* names something that cannot exist. "Opposing" means opposing to the side
the setup **swept**: a bullish setup sweeps SELL_SIDE below and runs at the BUY_SIDE pool
above.

SPEC 17.1's worked example settles it — a BUY LIMIT off a swept `sweep_low`, targeting
*"nearest opposing liquidity **PDH** 1.17240"*, and `period_levels` assigns a previous-day
**high** to BUY_SIDE.

**Six tests asserted the inversion**, which is why nothing caught it. The give-away was
inside their own helper: every fixture level was built with `source=PREV_DAY_HIGH` and
labelled `SELL_SIDE`, disagreeing with the source it claimed to come from.

### 4. The book cannot be read causally, so it is snapshotted — the trap's third instance

Handing the gate the finished `LiquidityBook` would have been a lookahead bug. SPEC 8.8's
merge mutates a **surviving level in place**: `strength` gains the loser's, `tier` drops to
the lower of the two, and `price` moves to the cluster extreme — and termination rewrites
`status`. Asking a finished level what it looked like at bar 100 returns what it became by
bar 1,600.

This is D-009 §4's swing trap and D-011 §3's FVG trap **for the third time**, and the
resolution is the same shape: capture the state *at* the bar rather than reconstruct it.
`LevelSnapshot` is a frozen `(id, side, price, rank, tier, strength)` taken at the end of
`on_bar_close`, after merge and prune, so it is the settled end-of-bar view. Capped by SPEC
8.9's `max_active_levels = 40`, so it is tens of small objects per bar rather than a copy
of the book.

**Both passes gate against the arming bar's snapshot**, not the fill bar's. The target is
part of the plan formed at arming, and pass two re-runs the gate over that plan; reading a
later book there would let the two passes disagree about whether the same setup had a
target — a disagreement about a price nobody has paid.

The test asserts the snapshot **differs** from the finished book, because a test that
passed either way would prove nothing.

### 5. What T2 was actually blocked by

With the gate able to see the book, over three fixture years and 165 setups:

| | armed | trades | dominant rejection |
|---|---:|---:|---|
| Before | 0 | 0 | `NO_TARGET_AVAILABLE` × 165 |
| After, at `min_target_rank` = 2.0 | 5 | 0 | `RR_BELOW_MIN` × 130 |

So T2's real constraint is **SPEC 17.2's RR gate**: the nearest qualifying opposing level
is usually closer than `min_rr` = 1.5. That is the gate working exactly as specified —
`below_min_rr_action = skip` exists so that a structural target too close to justify the
risk is skipped rather than replaced by a fixed one.

`NO_TARGET_AVAILABLE` fell from 165 to 14.

### 6. The default was then raised 2.0 → 5.0, and it was selected by its outcome

**Recorded plainly because the basis is the one `BACKTEST_PROTOCOL.md` §10.2 prohibits.**

The choice was put explicitly, with three options and their bases, and the instruction was
to take 5.0 — the value at which T2 arms 48 of 165 setups and produces 10 trades, against
5 and 0 at the default. That selection criterion is *the outcome on the data*, and on a
random walk those 10 trades are noise. **The number carries no evidence that 5.0 is
right**, only that it is where this fixture's targets sit far enough away to clear the RR
gate.

Two things make it less bad than it sounds, and neither rescues it:

- **There is a separate, outcome-independent case that 2.0 was wrong.** `rank` =
  `tier_weight(1–3) + 0.5 × min(strength, 4) + recency(0–1)`, so it spans **[1.5, 6.0]**
  and measures a median of **4.86** over 107,882 level-bars. A threshold of 2.0 sits
  essentially at the floor and filtered almost nothing — it was mis-scaled against the
  function it gates, whatever the right replacement is.
- **No result depends on it yet.** No out-of-sample evaluation has occurred, so this is the
  cheapest possible moment to re-register; §10.2's prohibition bites hardest when a result
  is already on the page, and there is none.

The provenance is written into four places so the value cannot later be cited as reasoned:
the schema field description, `defaults.yaml`, the ablation matrix's own spec note, and
`PRE_REGISTRATION.md` §11's re-registration record.

**Blast radius is contained and was checked, not assumed.** `min_target_rank` is read only
by `select_target_level`, which only T2 reaches; the T1 baseline is byte-identical at 149
armed and 37 trades before and after. What changes is `config_hash`, for every run, which
is correct — `PARAMETERS.md` §5.3: *"every result carries its `config_hash`"*.

### 7. Change control

`PARAMETERS.md` §5.2: *"Changing a FROZEN default starts a new study with a new
pre-registration"*, and `PRE_REGISTRATION.md` §10 names this exact change as a
new-registration trigger. Honoured: the pre-registration is superseded at **v1.1**, with
the old blob hash recorded, the reason stated, and the provenance carried in full. Its own
rule that nothing may change after the first OOS evaluation is not engaged, because there
has not been one.
---

## D-020 — The Phase 9 gate on real bars: the universe passes, the development set does not

| | |
|---|---|
| **Date** | 2026-08-30 |
| **Decided by** | Elie (instruction: *"run phase9_report on the real data"*) |
| **Status** | ACTIVE |
| **Supersedes** | The PASS recorded in `STATE.md` §3 and in the gate report at `d2bcf76`, which was a projection |
| **Data** | `dataset_hash 2a2bb029…`, 10 symbols × 2019–2022 in-sample, H4, histdata, bid |
| **Report** | `reports/phase9_gate.md` |

Phase 9's gate had been passed on a **projection**: a sweep→MSS conversion rate measured on
one synthetic symbol, scaled to ten symbols and four years. Real bars replace the scaling
with a count.

### 1. The gate fails, and on the half that was added to catch exactly this

| | Synthetic projection | Real measurement | |
|---|---:|---:|---|
| Sweep → MSS conversion | 1.98% | **1.59%** | ×0.80 |
| MSS per symbol-year | 12.7 | **9.2** | ×0.72 |
| Universe MSS (≥ 300) | 507 | **368** | PASS |
| Development-set MSS (≥ 120) | 152 | **97** | **FAIL** |

The universe half clears with room. The development-set half misses by 23, and that half of
the gate is not decoration — SPEC §9's phase table records why it exists:

> *"The second half of the gate was added by D-002: H4-only confirmation thins the funnel,
> and a universe-wide count can hide a development set too thin to iterate on."*

**The failure mode is the one the clause was written to expose, arriving exactly as
described.** A pooled count of 368 does hide it; the gate's second half is what makes it
visible.

`micro` fails both halves decisively (55 universe, 16 development), which is the third
consecutive run in which the pre-registered `micro` variant produces a null. That remains a
result about a variant, not a parameter to be tuned (SPEC 11.1, §10.2).

### 2. The development set is the worst end of the universe, not an unlucky draw

| Symbol | Set | Confirmed sweeps | CHoCH | MSS | Sweep → MSS |
|---|---|---:|---:|---:|---:|
| NZDUSD | cross | 2,322 | 638 | 47 | 2.02% |
| EURGBP | cross | 2,399 | 581 | 45 | 1.88% |
| AUDUSD | cross | 2,259 | 602 | 43 | 1.90% |
| USDCAD | cross | 2,335 | 564 | 40 | 1.71% |
| GBPUSD | **dev** | 2,328 | 543 | 37 | 1.59% |
| USDJPY | **dev** | 2,138 | 532 | 34 | 1.59% |
| USDCHF | cross | 2,345 | 551 | 32 | 1.36% |
| EURJPY | cross | 2,317 | 543 | 32 | 1.38% |
| GBPJPY | cross | 2,327 | 581 | 32 | 1.38% |
| EURUSD | **dev** | 2,348 | 536 | 26 | 1.11% |

**EURUSD is the lowest converter of all ten symbols**, and the four best are all in the
cross-sectional set. The confirmed-sweep counts are nearly flat across the universe
(2,138–2,399), so this is not a difference in how much the funnel has to work with — it is a
difference in conversion.

That matters for what a remedy could look like. The development set is a *named* three
(protocol §2.1, pre-registration §4.2), chosen on liquidity grounds before any of this was
measured, and the gate's second half exists precisely because iteration happens on those
three. So:

- **Adding symbols cannot fix the failing half.** It moves the universe count, which already
  passes.
- **Swapping the development set to the three best converters would be selecting a split by
  its outcome**, which §10.2 prohibits — and it would also empty the cross-sectional test of
  meaning, since pre-registration §4.2's whole claim is transfer to seven *unseen* pairs at
  the same parameters.

### 3. Nothing inside the registered range reaches the threshold

`choch.max_reference_distance_atr`, registered ABLATION over {2.0, 3.0, 4.0}:

| Value | Universe MSS | Sweep → MSS | Development-set MSS |
|---|---:|---:|---:|
| 2.0 | 145 | 0.63% | 41 |
| 3.0 (default) | 368 | 1.59% | **97** |
| 4.0 | 446 | 1.93% | **113** |
| 6.0 (unregistered) | 466 | 2.02% | 120 |

On synthetic data this parameter **spanned the verdict** — 2.0 failed the development half,
3.0 and 4.0 passed — and `STATE.md` §3 recorded the PASS as conditional on it for that
reason. On real bars every registered value fails: 41, 97, 113 against a floor of 120. Only
6.0, outside the registered set, reaches the threshold, and it reaches it *exactly*.

> **The parameter that spans the verdict is registered ABLATION, and on real bars none of its
> registered values clears the gate.** The move that would rescue the PASS is not available
> even to someone willing to make it.

This is a stronger statement than the synthetic run could support, and it is why this FAIL is
not a near miss to be argued down. It is also the cleanest illustration in the project of why
§10.2 is written as a prohibition rather than a preference: a single out-of-range value lands
on the threshold to the unit.

### 4. D-009's specification contradiction stops being cost-free

SPEC 6.6 requires the swept level to lie beyond the extreme of the leg that produced the
CHoCH. SPEC 11.5 enumerates the MSS conditions, calls itself complete, and omits it. D-009
adopted 11.5 as operative and priced the other reading rather than arguing it — and on the
synthetic fixture the price was small enough to record that *"the two readings of the
specification agree on the decision Phase 9 exists to make"*.

They no longer agree:

| Reading | Universe MSS | vs 300 | Development-set MSS | vs 120 |
|---|---:|:--:|---:|:--:|
| SPEC 11.5 (operative) | 368 | PASS | 97 | FAIL |
| SPEC 6.6 additionally applied | **281** | **FAIL** | not broken out | — |

**87 of 368 MSS fail the 6.6 clause**, so adopting it takes the universe half under its floor
as well. Which section is operative now decides a gate verdict, not a footnote.

Two consequences. **It has to be resolved on its merits before any Phase 10+ figure is
quoted**, because every downstream population is this one. And the run did not break the 6.6
cost out by symbol set, so the development-set number under that reading is unmeasured —
worth adding when it is resolved, since it is the half already failing.

### 5. What did not change

**No parameter was moved.** §10.2 forbids choosing a value by looking at the outcome, and
that binds hardest exactly here: one unregistered value converts a FAIL into a PASS at
precisely the threshold. The defaults stand, the gate reads FAIL, and the report says so
before it says anything else.

**The pre-registration was not amended** — see §7.

### 6. The TUNABLE is inert on real bars too, so D-008 §4 survives contact with data

| `choch.max_bars_after_sweep` | Universe MSS | Development-set MSS |
|---|---:|---:|
| 4 | 315 | 75 |
| 8 | 368 | 97 |
| 12 (default) | 368 | 97 |
| 18 | 368 | 97 |
| 24 | 368 | 97 |

Median sweep-extreme-to-MSS distance is **2 bars**, the maximum observed is **8**, and **0.0%**
of MSS sit at the window edge. The window admits events; it does not manufacture them, and
above 8 it does nothing at all.

D-008 §4 recorded that the registered TUNABLE/ABLATION split did not match which parameter
actually decides the outcome, and flagged that the finding was measured on a random walk and
had to be re-measured on real bars before being trusted. **Re-measured, and it holds**: the
TUNABLE is inert over most of its grid while an ABLATION parameter spans the verdict. Both
observations that produced D-008 §4 now have real-data support, so the classification is
wrong on its own terms rather than as an artefact of the fixture.

This also qualifies D-002's timescale reading the same way the synthetic run did, and by a
wider margin: the window permits two trading days, the observed median is **8 hours**.

### 7. How it was run, and the one methodological choice inside it

`scripts/phase9_report.py` now reads `data/parquet` by default; `--synthetic` reproduces the
original fixture into `reports/phase9_gate_synthetic.md`, so the instrument stays runnable.

- **The split is derived from the rule, not typed in.** `acquired_years()` reads the manifest
  and `split()` applies pre-registration §4.1 — earliest four years in-sample, next two
  out-of-sample, remainder holdout — resolving to **2019–2022 / 2023–2024 / 2025**. Nothing
  outside the in-sample years was read.
- **Each symbol is one continuous four-year pass, not four yearly ones.** Yearly passes would
  restart the liquidity book, the structure state and every warm-up on 1 January — losing a
  level created in December and swept in January, and blinding the first weeks of each year.
  They would also put D1 under `swing.min_history` (250 bars against ~260 D1 bars in a year),
  leaving the swing engine cold for a meaningful share of every run. The calendar year
  survives as a reporting breakdown, taken from each sweep's own timestamp.
- **Suspect data is reported, not dropped** (SPEC 1.5): 517 of 23,116 decided opportunities
  (2.24%) and 6 of 368 MSS (1.63%) sit on a suspect bar. Excluding them leaves 362, still
  over 300 — so no gate verdict turns on the choice. This row could not exist before; the
  synthetic fixture has no gaps.
- 662 tests green.

**Item 4 of the pre-registration is now due.** §4.1's rule was applied to real history for the
first time by this run. The document schedules stamping the literal dates as an amendment
under §10 *before the first run*, and that has not been done here: it is a change to a
committed pre-registration, it changes no threshold and no grid, and it is a governance act
rather than a mechanical one.

### 8. What this entry does not decide

SPEC §9 states the gate's consequence as *"the design is reconsidered before any entry code is
written"*. **The entry code exists** — Phases 10 through 14 were built while the gate stood on
a projection — so the clause's trigger has fired after the event it was written to precede.
That is itself the cost of passing a gate on a projection, and it is recorded rather than
worked around.

What the reconsideration should conclude is not decided here. The options are visible and each
has a price:

1. **Accept a thinner development set** and carry the reduced power explicitly into every §6
   study's MDE — 97 events, against the H5 arithmetic in `STATE.md` §3a which already showed
   the 12-bar horizon out of reach at a larger count.
2. **Re-open D-002** (H4 confirmation for every tier), which is the design decision that
   thins the funnel. §6.5 names `liq.tier_confirmation_tf` as its counterfactual and D-017 §1
   found that parameter **declared and read by nothing**, so the alternative still cannot be
   measured. Testing it means implementing it first.
3. **Treat the universe count as the operative gate** and record the development half as a
   known, quantified deficiency carried into every later claim.
4. **A new pre-registration with a different development set** — which is selecting a split by
   its outcome unless justified on grounds independent of these numbers.

None is taken. What the run establishes is that the choice is now a real one, made against a
measurement instead of an extrapolation.
---

## D-021 — Pre-registration Amendment 1: the split dates are stamped

| | |
|---|---|
| **Date** | 2026-08-30 |
| **Decided by** | Elie (instruction: *"stamp the pre-registration dates"*) |
| **Status** | ACTIVE |
| **Class** | **Amendment** under `PRE_REGISTRATION.md` §10 — *not* a re-registration |
| **Blob** | `b9142a0fcb0960016162b1c18bb6fa60cfc4a6f5` → `646ddfb6db70d7964051680cb86ad468546a49b9` |

The last open item in `BACKTEST_PROTOCOL.md` §1. Item 4 — the literal date ranges — could not
be a literal when the pre-registration was written, because no data existed; it was fixed as
a **rule** instead, and D-018 argued at the time that a rule yielding exactly one answer is as
binding as a date. Real history arrived on 2026-08-30, so the rule was applied.

### 1. What it did, and why applying it required no judgement

Acquired history: 10 symbols of M1, earliest bar **2019-01-01T22:00Z**, latest
**2025-12-31T21:58Z** — seven calendar years. §4.1 says earliest four in-sample, next two
out-of-sample, remainder holdout:

| Split | Literal range (UTC) | Years |
|---|---|---|
| In-sample | 2019-01-01T00:00:00Z → 2022-12-31T23:59:59Z | 2019-2022 |
| Out-of-sample | 2023-01-01T00:00:00Z → 2024-12-31T23:59:59Z | 2023-2024 |
| Holdout | 2025-01-01T00:00:00Z → 2025-12-31T23:59:59Z | 2025 |

There is no second reading. That is the whole point of having written the rule first, and it
is why this is an amendment: it changes no threshold, no grid, no `M` and no decision rule,
which are the four things §10 names as making a change a *new registration* instead.

**Nothing is invalidated.** No out-of-sample evaluation has occurred, so §1's "nothing may be
changed after the first out-of-sample evaluation" is not engaged. The stamp was triggered by
the Phase 9 funnel run (D-020), which is the first thing in the project to apply the split to
real bars, and which read the in-sample years only.

### 2. The stamp is only operative because "the split" and "these year partitions" are the same thing

A bar belongs to the split holding the UTC calendar year of its `open_time`. That is how the
Parquet store is partitioned and how `ingest.read_series(years=…)` selects, so a run on one
split provably reads no bar from another. Recorded in §4.1 as a requirement on future
runners, not as an observation: a runner that filtered by timestamp inside a shared series,
or that resampled across a boundary, could satisfy the stamped dates on paper while reading
data it is not entitled to.

### 3. Two consequences the rule produced and nobody chose

Recorded rather than engineered around — engineering around either now would mean choosing a
split boundary with the data in hand, which is the exact thing §4.1 exists to prevent.

1. **The holdout is a single year**, against four in-sample and two out-of-sample. Seven
   years is what was acquired and the rule says *"everything after that"*. §3's ≥ 200-trade
   minimum is stated against the out-of-sample split, so it is not directly violated, but a
   one-year holdout carrying the final go/no-go is thin — and thinner still given Phase 9's
   measured rate (D-020). If it proves too thin, that is a finding about the **acquisition**,
   and the honest response is to acquire more history and re-register, not to redraw the line.
2. **The out-of-sample/holdout boundary falls mid-week; the in-sample/out-of-sample boundary
   does not.** 2022-12-31 is a Saturday, so the first out-of-sample week opens cleanly on
   Sunday 2023-01-01. 2024-12-31 is a **Tuesday**, so the week that opened Sunday 2024-12-29
   at 22:00 UTC is cut by the boundary and closes on Friday 2025-01-03. Because splits are
   selected as whole year partitions (§2 above), the week is **truncated, not leaked**: a
   position still open at the boundary is right-censored by the end of its series rather than
   resolved from data the run may not see. That is the safe direction, and it is worth knowing
   when reading trade counts at a split edge.

### 4. What is now closed, and what is not

`BACKTEST_PROTOCOL.md` §1 is **complete**: all seven items fixed, before the first strategy
backtest, as required. What that does not do is make the project's open questions smaller —
D-020's failed development-set gate is unaffected by any of this, and Q1 (broker, account
currency, the real spread and swap tables) is still open.
---

## D-022 — The OB bake-off on real bars: `M_eff` transferred, the answerability did not

| | |
|---|---|
| **Date** | 2026-08-30 |
| **Decided by** | Elie (instruction: *"run phase11_report on the real data"*) |
| **Status** | ACTIVE |
| **Updates** | D-012's `M_eff` = 1.77, which D-012 itself flagged as fixture-dependent |
| **Data** | `dataset_hash 2a2bb029…`, 10 symbols × 2019-2022 in-sample, H4 |
| **Report** | `reports/phase11_gate.md` — 10/10 checks PASS |

`STATE.md` §9's run order put this second, for a stated reason: `M_eff` is *"a property of
how the four behave on this fixture"* and *"every later correction depends on this number"*.
Recomputed on real bars, in-sample only.

### 1. The number transferred, which almost nothing else in this project has

| | Synthetic | Real bars |
|---|---:|---:|
| `M_eff` (four definitions) | 1.77 | **1.68** |
| `M_eff` (OB-A/B/C only) | 1.36 | **1.36** |
| Li & Ji, for contrast | ~2 | 2.00 |

A 5% move on the headline and none at all on the A/B/C subset. **Use 1.68.** Correcting as
though the four were independent over-corrects by 2.4×.

That is worth recording as a *contrast*, not just an update. D-020 had just found the Phase 9
projection overstating by 38% and 57%; the standing expectation after that was that
fixture-derived numbers do not survive. This one did, and the reason is visible in §2: `M_eff`
is a statement about how four rules relate **to each other**, and they are all reading the
same displacement leg. That relationship is structural, so it is much less sensitive to
whether the price series trends than a rate or a count is.

### 2. Real bars made the definitions *more* redundant, not less

Entry-offset correlations are **≥ 0.925 for every pair** (synthetic: > 0.87), while same-bar
agreement stays low and uneven:

| Pair | Same bar | Entry-offset correlation |
|---|---:|---:|
| OB-A vs OB-B | 69.0% | 0.985 |
| OB-B vs OB-C | 30.7% | 0.967 |
| OB-A vs OB-C | 23.2% | 0.970 |
| OB-A vs OB-D | **0.0%** | **0.925** |
| OB-B vs OB-D | 0.0% | 0.925 |
| OB-C vs OB-D | 0.0% | 0.926 |

**OB-D never once picks the same bar as any other definition, across 393 paired setups, and
still correlates 0.925 with all three on the price a trade would actually pay.** That is the
sharpest form the D-012 §2 finding has taken. SPEC 13.6's heuristic — *"if OB-A and OB-C
select the same bar 80% of the time, they are not two hypotheses"* — would license treating
OB-D as a fully independent fourth test. It is not one.

### 3. Answerability is the D-020 shape again

OB-A yields **492 touch events** across the 40 in-sample symbol-years, 12 per symbol-year, of
which **135** fall on the three development symbols.

| h | touches needed | universe (492) | development set (135) |
|---:|---:|---|---|
| 1 | 155 | yes | **no** |
| 3 | 343 | yes | **no** |
| 6 | 784 | **no** | **no** |
| 12 | 1,652 | **no** | **no** |

**The study is answerable at short horizons across the universe and at no horizon at all on
the development set** — 135 against the 155 that h=1 alone requires. This is D-020 recurring:
a pooled number that clears while the three symbols development actually iterates on do not,
and the two findings share a cause, because this study draws from the funnel Phase 9 measured.

Worth stating plainly: a definition bake-off that cannot be resolved on the development set is
a bake-off whose winner cannot be chosen without touching data reserved for validating the
choice.

### 4. The bootstrap's under-coverage was a small-sample artefact

The synthetic run reported the null calibration as **anti-conservative** and reasoned that the
percentile bootstrap under-covers with a few dozen heavy-tailed observations. On real bars,
with 492 pooled touches instead of a few dozen:

- false-positive rate **5.6%** over 3,000 shuffles against α = 5%
- Wilson interval **[4.9%, 6.5%]**, which contains α
- deviation **1.6 σ**

**Calibrated.** The earlier explanation is confirmed by the mechanism it predicted: raise the
sample and the coverage comes back. The practical consequence is that the UNDERPOWERED
verdicts in this report are trustworthy as stated, rather than "understated" the way the
synthetic ones had to be qualified.

The positive control also tightens: an injected **+0.25 ATR** is now detected (MDE 0.140 ATR
at h=1), where the fixture needed more.

### 5. Two things that transferred quietly, and one that did not

- **Half of OB-A's blocks are never filled within 30 bars** — 50.4% fill@30 on real bars
  against the fixture's roughly-half. Entry model D discards about half the setups it is
  offered, before any of the other four models have had their fill rates measured on real
  data. The fixture's warning about this was right.
- **`NO_DISPLACEMENT` is identical across all four definitions** (5,148 of 6,764 setups),
  because SPEC 13.4's constraint 1 is applied before any definition-specific search. That is
  what stops OB-A degenerating into "the last red candle".
- **OB-D's hit rate did not transfer and should not be read as one.** 5.9% against A/B/C's
  ~23%, driven by `NO_FAILED_MOVE` (593) and `OB_ABOVE_REFERENCE` (463) — rejections the
  others barely see. D-012 recorded OB-D's implementation as **a flagged reading of a one-line
  specification, not a resolved one**, and that has not changed. Its hit rate is a property of
  the reading.

### 6. What this does not establish

**Which definition is best** — that needs performance, which needs Phases 12-14. And
**nothing about `M_eff`'s stability across splits**: it is recomputed here on the in-sample
years only, and it must *not* be recomputed on the out-of-sample or holdout years to check,
because that spends out-of-sample budget on a nuisance parameter (protocol §7).
---

## D-023 — The FVG edge test on real bars: the project's first null that means something

| | |
|---|---|
| **Date** | 2026-08-30 |
| **Decided by** | Elie (instruction: *"run phase10_report on the real data"*) |
| **Status** | ACTIVE |
| **Data** | `dataset_hash 2a2bb029…`, 10 symbols × 2019-2022 in-sample, H4 |
| **Report** | `reports/phase10_gate.md` — 10/10 checks PASS; fixture retained at `reports/phase10_gate_synthetic.md` |

### 1. EQUIVALENT at every horizon, and what that licenses

| h | n touch | diff (ATR) | 95% CI | MDE | Verdict |
|---:|---:|---:|---|---:|---|
| 1 | 7,800 | +0.0111 | [-0.0126, +0.0347] | 0.034 | **EQUIVALENT** |
| 3 | 7,796 | +0.0307 | [-0.0080, +0.0685] | 0.055 | **EQUIVALENT** |
| 6 | 7,793 | +0.0342 | [-0.0194, +0.0885] | 0.079 | **EQUIVALENT** |
| 12 | 7,788 | +0.0251 | [-0.0535, +0.1048] | 0.112 | **EQUIVALENT** |

**Every previous null in this project was a null on a random walk**, where the true effect is
zero by construction and the result is the fixture speaking. This one is not. Ten real
symbols, 40 symbol-years, every interval inside the pre-declared ±0.25 ATR margin, and a
positive control that detects an injected **+0.05 ATR** — so the study could have seen an
effect a fifth the size of the margin and did not.

`EQUIVALENT` is the only verdict in `stats.Verdict` that licenses the word "no", and it is
earned here rather than defaulted into.

**What it licenses, exactly:** touching an unmitigated FVG does not move the next 1 to 12 H4
bars by as much as 0.25 ATR in the gap's own direction, against a control matched on session
slot and ATR tercile.

**What it does not license, and this matters more than the result:** the strategy does not use
FVGs that way. `disp.require_fvg` uses a gap as *evidence that displacement occurred* — SPEC
10.2 is explicit that it is the same condition expressed structurally, not an extra filter —
and entry model C uses one as a *price to bid at*. Neither is a claim that a gap predicts
direction. Both are Phase 12's to evaluate, and this result neither condemns nor clears them.

A reader who thinks a 0.10 ATR edge would be tradable should read the diff and CI columns
rather than the verdict: at h=3 the interval still reaches +0.069 ATR.

### 2. `INVALIDATED` became reachable, exactly as predicted, and D-011 §2 is why

The synthetic report listed `INVALIDATED` behaviour as something it explicitly could **not**
establish: the transition needs a true price discontinuity, a continuous random walk cannot
produce one, and it was covered by a single constructed test. Real bars have weekends and
holidays:

- **19 of 9,446 gaps end `INVALIDATED`** (0.2%), plus 2 `PARTIAL`, a status the fixture also
  never reached.

Two things follow. It is now exercised at scale rather than by one test — and **it is only
reachable at all because of D-011 §2's touch-rule fix**. Under SPEC 12.2's one-sided rule,
mitigation always won the race, so every one of these 19 would have been counted as a fill,
silently inflating the fill-rate curve that is this phase's own deliverable. A correction made
on a fixture that could not exercise it has now paid off on data that can.

### 3. The population asymmetry is the finding to carry forward

Over the **same in-sample split**:

| Study | Population | Verdict it can support |
|---|---:|---|
| FVG touch (this) | **7,800** | EQUIVALENT at every horizon |
| OB touch (D-022) | 492 | UNDERPOWERED everywhere; dev set answers nothing |
| MSS events (D-020) | 368 | gate failed on the development half |

**The FVG concept gets about 21× the sample the MSS chain does**, because every gap counts
while an MSS has to survive the whole sweep-to-CHoCH-to-displacement funnel.

That asymmetry has to be held in mind when reading these three results together. It would be
easy — and wrong — to conclude "FVGs don't work, and MSS is unproven". What the data actually
says is that **the components are not equally measurable, and the two the design rests on are
the two hardest to measure.** A confident null about the cheap component and an underpowered
shrug about the expensive one is a statement about sample sizes before it is a statement about
markets.

### 4. The fill-curve prediction was half right, in the half that matters less

The synthetic report predicted the curve would **fall** on real data — *"a random walk returns
to a local extreme readily; a trending market may not"*.

| | fixture | real bars | |
|---|---:|---:|---|
| fill within 1 bar | 29.8% | 23.1% | fell 22% |
| fill within 30 bars | 78.2% | 80.4% | **rose** |
| median bars to mitigation | 2 | 3 | |

Real bars fill **more slowly early** — the predicted effect — but the 30-bar rate did not
fall. Gaps are filled eventually at about the same rate; they take longer to get there.

The prediction was really about `fvg.max_age_bars` (default 30), and the distinction changes
what it implies: a 30-bar cap catches essentially the same share of gaps on real bars as on
the fixture, so **the cap does not need re-examining** — what changed is how much of the wait
happens inside it, which is entry model C's problem rather than the lifecycle's.

### 5. A report written for a fixture states a real-data result backwards

Worth recording as process, because this is the third script pointed at real data and the
first where the prose actually inverted the meaning.

The first real run of this report printed, directly beneath an EQUIVALENT verdict on real
market data:

> *"On a random walk the true effect is zero by construction, so finding nothing is what a
> working instrument does here."*

— which says the exact opposite of what the result means. It also printed *"`INVALIDATED` is
zero on this fixture"* in the paragraph immediately after one reporting 19 of them, and
described the study's own power as *"the output that survives the fixture being synthetic"*.

The numbers were right in every case; the sentences around them were written for a different
dataset. **Grep every generated report for `random walk` and `fixture` after pointing a script
at real data**, and make the prose branch on the source rather than assuming it. Phases 9 and
11 needed the same treatment and got it while adapting them; this one only surfaced because
the verdict itself changed.

One gate check needed the same care rather than a re-word. *"INVALIDATED covered by a
constructed test, not the fixture"* asserts `count == 0`, which is a true and useful statement
about the fixture and becomes a **failing check on real bars precisely when the prediction
comes true**. It now branches: unreachability on the fixture, a measurement on real bars. That
is not a loosened gate — it is the same question asked of data that can answer it.

### 6. What this does not establish

**That the result holds out of sample.** This is the in-sample split and it was not checked on
2023-2024 or 2025 — and should not be. A null needs no confirmation bought with out-of-sample
budget (protocol §7).

**That 19 `INVALIDATED` gaps characterise the path.** They prove it is reachable. The
`fvg.exclude_weekend_gaps` ablation is what would measure it.

**Anything about entry model C.** Selection is implemented and tested; what it is worth is
Phase 12.
---

## D-024 — H5 on real bars: answered at the short horizons, out of reach at the long one

| | |
|---|---|
| **Date** | 2026-08-30 |
| **Decided by** | Elie (instruction: *"run marginal_value_report on the real data"*) |
| **Status** | ACTIVE |
| **Supersedes** | D-010 §4's three-ways-out framing, two of which have now resolved |
| **Data** | `dataset_hash 2a2bb029…`, 10 symbols × 2019-2022 in-sample, H4, `reference_mode = major` |
| **Report** | `reports/marginal_value.md` — 8/8 checks PASS, pooled verdict UNDERPOWERED |

### 1. Two of three horizons resolved, and both say no

| h | n MSS | n not-MSS | diff (ATR) | 95% CI | MDE | Verdict |
|---:|---:|---:|---:|---|---:|---|
| 1 | 326 | 3,529 | −0.026 | [−0.117, +0.064] | 0.134 | **EQUIVALENT** |
| 4 | 326 | 3,526 | +0.017 | [−0.134, +0.168] | 0.232 | **EQUIVALENT** |
| 12 | 325 | 3,524 | −0.142 | [−0.417, +0.143] | 0.428 | UNDERPOWERED |

**The pooled verdict is UNDERPOWERED and it hides a real answer.** It takes the weakest
horizon, which is the correct behaviour — averaging a resolved result together with an
unresolved one is exactly what the three-way verdict exists to prevent — but the headline
should not be read as "H5 remains open" without qualification. At h=1 and h=4 the sample
resolves the pre-declared ±0.25 ATR margin and reports EQUIVALENT.

So: **displacement filtering does not separate MSS from CHoCH-not-MSS forward returns by as
much as 0.25 ATR over 1 to 4 H4 bars, on real market data.** H5 is falsified at those
horizons. Together with D-023's FVG null, two of the project's component hypotheses now have
real-data answers, and both are negative.

`UNDERPOWERED` at h=12 is **not** a null. It says the study could not look, and the power
table says why.

### 2. The power arithmetic squeezed from both ends

`STATE.md` §3a carried the synthetic projection as *"a planning fact that transfers to real
data"*. It transferred in shape and got worse in both terms:

| h | MSS needed (synth → real) | universe (427 → **326**) | dev set (128 → **88**) |
|---:|---|---|---|
| 1 | 58 → **93** | yes | **no** |
| 4 | 222 → **281** | yes | **no** |
| 12 | 804 → **951** | **no** | **no** |

Requirements rose by 15-60% because real forward-return variance is larger than a random
walk's, and the population fell because D-020's measured funnel produces fewer MSS than the
fixture projected. The prediction that h=12 would be out of reach was right, and by a wider
margin than expected: 326 against 951.

**The counting basis is worth restating**, since it explains why 368 (D-020) and 326 (here)
differ. Phase 9 deduplicates per SPEC 9.4 *cluster*; this study collapses to one observation
per `(break bar, direction)`, because a forward return is a function of exactly those two
things. The stricter rule is the right one for a return study and gives 8.2 MSS per
symbol-year against the funnel's 9.2.

### 3. The development set answers nothing, for the third study running

**88 MSS on EURUSD/GBPUSD/USDJPY, against the 93 that the *shortest* horizon needs.** So H5
is unanswerable on the development set at every horizon, while the universe answers two of
three.

That is now a pattern rather than an observation:

| Study | Pooled | Development set |
|---|---|---|
| Phase 9 gate (D-020) | 368 ≥ 300, passes | 97 < 120, **fails** |
| OB touches (D-022) | 492, h=1 and h=3 answerable | 135 < 155, **no horizon** |
| H5 (this) | 326, h=1 and h=4 answerable | 88 < 93, **no horizon** |

Three independent studies, one shape. **The design iterates on three symbols and none of its
questions can be answered on three symbols.** That is not a finding about any one study; it
is a finding about the development-set size, and it is the same underlying scarcity D-020
measured. Nothing here decides what to do about it — the options are still D-020 §8's — but
after three instances the cost of leaving it undecided is clearer.

### 4. Widening the margin is now closed, permanently

The synthetic run listed three ways out and said they were *"better decided now than after
Phase 14"*. Two have resolved themselves and one is closed:

1. **Answer H5 at the short horizons only** — this is what happened, without anyone choosing
   it. h=1 and h=4 resolve; the narrower claim is a real one.
2. **Widen the margin to 0.5 ATR** — **closed.** It divides every requirement by four and
   would make h=12 answerable. It was declared defensible *before* the data and an
   indefensible reaction after it, and the data has been seen. §10.2 binds. Recorded as
   closed rather than left on a list where someone could pick it up later.
3. **The §6.5 ablation delta** remains the route to h=12's question, measuring the same
   component through the full system rather than through forward returns.

### 5. Controls, and the overlap diagnostic

Both controls pass and are worth quoting because the verdict depends on them: null
calibration **5.2%** over 3,000 label shuffles with a Wilson interval of [4.5%, 6.1%]
containing α — calibrated, the same result D-022 §4 and D-023 got once samples grew — and
the positive control detects an injected +0.8 ATR.

**31.9% of events have a contaminated 12-bar window.** The non-overlapping subsample is
reported alongside for that reason and reaches the same verdicts, so overlap is not what is
holding h=12 back; sample size is.

### 6. Applying D-023 §5's own rule caught six more passages

D-023 §5 recorded the rule *"grep every generated report for `random walk` and `fixture`
after pointing a script at real data"*. Applied here immediately, it found six surviving
passages — including the power section describing itself as *"the one output that does not
depend on the data being synthetic"* — plus a table headed **"Per-year stability"** whose
rows were symbols, with the prose beneath it reading *"the sign flips between years"*.

The rule earned its keep on the first script it was applied to. This one had the most
fixture-shaped prose in the project, because its entire framing was "instrument validated,
H5 open pending real data" — a sentence that becomes false the moment the data arrives.
---

## D-025 — Phase 12 on real bars: one effect appeared, two predictions failed

| | |
|---|---|
| **Date** | 2026-08-30 |
| **Decided by** | Elie (instruction: *"run phase12_report on the real data"*) |
| **Status** | ACTIVE |
| **Data** | `dataset_hash 2a2bb029…`, 10 symbols × 2019-2022 in-sample, H4 **with the vendor's real M1** |
| **Report** | `reports/phase12_gate.md` — 8/8 checks PASS; fixture retained at `reports/phase12_gate_synthetic.md` |

### 1. The gate's second half is now a real check, and it passes

*"Fill logic verified against M1"* meant something weaker on the fixture: the M1 path was
generated and the H4 bars resampled from it, so the two agreed **by construction**. This run
uses HistData's own M1 — 1.45M bars per symbol — and the bar-level rule still agrees with the
M1 replay on **0 disagreements over 7,877 armed orders**.

That is the first time the phase's own gate has been tested rather than asserted.

### 2. SPEC 15.3's lookahead has a magnitude for the first time

| | fixture | real bars |
|---|---:|---:|
| non-zero close-to-open gaps | **0** of 4,859 | **43,360** of 64,228 (67.5%) |
| mean gap over all transitions | 0.0000 ATR | **0.0156 ATR** |
| median non-zero gap | — | 0.0049 ATR |
| 95th percentile | — | 0.0353 ATR |
| largest | — | 3.9978 ATR |

Filling model A at the close that triggered it, rather than the next bar's open, gains
exactly the close-to-open move. On the fixture that is 0.0000 by construction; here it is
**0.0156 ATR on every entry**, free, in the direction the trade wants.

Against a stop of 1-2 ATR that is a few percent of R per trade — **materially less than SPEC
15.3's "10-30% of headline return"**, and worth recording as a number the spec over-states on
this data rather than quietly adopting the spec's figure. But it accrues to *every* entry
rather than to a tail, and it is unambiguously non-zero. The rule was load-bearing on data
that could not demonstrate it; it is load-bearing and demonstrated now.

### 3. The opposing-sweep cancel is not a fixture artefact — the prediction was wrong

The synthetic report said this in as many words:

> *"A random walk with up to 40 active liquidity levels produces sweeps at a rate no real
> market sustains; the same clause on real bars will cost something quite different."*

| | fixture | real bars |
|---|---:|---:|
| confirmed sweeps per H4 bar | 0.47 | **0.44** |
| fill rate, limit models, **without** `cancel_if` 2 | 33-41% | **30-46%** |
| fill rate, limit models, **with** it | 2-3% | **6-10%** |

**The sweep rate is the same to within 7%.** The liquidity model produces roughly one
confirmed sweep every two H4 bars on real FX majors, exactly as it did on noise, so the
mechanism behind the cancel is not an artefact of the fixture. The damage is four to five
times smaller and still severe.

**This promotes `cancel_if` clause 2 from a fixture note to a live design question.** It is
FROZEN and nothing was changed. But a clause that discards roughly nine of every ten limit
orders is deciding the entry-model bake-off by itself, and SPEC 15.5's per-setup comparison
cannot see past it — model A is untouched because a market order never waits, so the
comparison is between one model at full population and four at a tenth of theirs. Both
columns are reported so the effects stay separable.

A second prediction failed quietly in the same table: the fixture's fill rates **did**
transfer, 30-46% against 33-41% without the cancel, where the synthetic report expected real
retracement behaviour to differ.

### 4. The gap-past-the-stop branch is still dead, and that one is mine

`STATE.md` §8 expected this branch to come alive on real bars, on the reasoning that it needs
a price discontinuity and real data has them. It has them — 43,360 of them — and the branch
fires **0 times over 40 symbol-years**.

The arithmetic is the whole explanation: gapping *past a stop* needs a discontinuity 1-2 ATR
wide, and the median non-zero gap is **0.0049 ATR**, two orders of magnitude smaller. The
largest single gap in the sample is 3.9978 ATR, so it is not impossible — merely rare enough
that four years across ten majors produced no instance where a gap also beat the entry price
to the stop.

**Unlike Phase 10's `INVALIDATED`, which did come alive (D-023 §2), this guard stays
exercised only by constructed tests.** That is D-014 §8's "a guard nothing reaches" pattern
surviving the move to real data. It is **not** grounds to remove it: the H4 gap that clears a
stop is exactly the tail event the guard exists for, and a guard justified by tail events
cannot be justified by their frequency.

**This one is worth recording as a process failure as well as a result.** My first adaptation
of this report printed *"Gap-past-the-stop cancels fire 0 times… They are live on real data"*
— an assertion contradicted by the number in the same sentence. That is precisely the failure
D-023 §5 recorded two commits earlier, this time in prose written *for* the real-data branch
rather than inherited from the fixture. The rule needs strengthening: **it is not enough to
grep for fixture language; every claim in a report that a thing now happens must be
conditioned on the count of that thing.** Both branches are now written from `gap_bars`
itself.

### 5. What this does not establish

**Anything about returns** — no trade is closed here and nothing is sized. **That M1 is the
true intrabar path**: it is a sampling of the tape, within-minute order is unknown, and
`backtest.intrabar_mode = m1_path` inherits that limit. Tick data would close it and no
spread series exists yet at all. **That the fill rates generalise beyond these four
in-sample years** — and their close agreement with the fixture deserves more scepticism than
a confirmed prediction would, given two other predictions in the same report failed.
---

## D-026 — Phase 13 on real bars: six symbols cannot be sized, and the log blamed the wrong thing

| | |
|---|---|
| **Date** | 2026-08-30 |
| **Decided by** | Elie (instruction: *"run phase13_report on the real data"*) |
| **Status** | ACTIVE |
| **Answers** | D-014 §3 (does S4's ceiling bind?), and `STATE.md` §9's three open Phase 13 questions |
| **Data** | `dataset_hash 2a2bb029…`, 10 symbols × 2019-2022 in-sample, H4 |
| **Report** | `reports/phase13_gate.md` — 8/8 checks PASS |

### 1. Only four of the ten symbols can carry a sized trade

| | |
|---|---|
| Sizeable | AUDUSD, EURUSD, GBPUSD, NZDUSD |
| **Blocked** | USDJPY, EURJPY, GBPJPY, USDCAD, USDCHF, EURGBP |

**Every blocked symbol is one whose quote currency is not the account currency.** Sizing
converts a stop distance into money at the quote→USD rate; SPEC 18.2 says the absence of
that series *"blocks the inclusion of any symbol"* rather than defaulting to 1.0; and there
is no series, because **Q1 is still open**. The four that size are the USD-quoted pairs,
where the rate is 1 by identity.

This is the rule working exactly as written. It is not a defect, and it is not a function of
`account.starting_equity` — the blocked symbols fail at every equity.

**What it costs is larger than this phase.** The pre-registration's cross-sectional
criterion — *"≥ 6 of 10 symbols with positive expectancy, same parameters"* (§3) — is
**unevaluable**, because six of the ten cannot produce a sized trade at all. That criterion
is one of §10.1's go/no-go rows. Q1 was previously understood to block the live-matching set
and the swap table (`STATE.md` §9); it also blocks a headline acceptance criterion, and that
was not known before this run.

### 2. The rejection log named the wrong cause, and it fooled me first

`trade.evaluate` catches `MissingConversionRate` and returns
`RiskReject.SIZE_BELOW_MIN`:

```python
except MissingConversionRate as exc:
    return Decision(Stage.SIZING, RiskReject.SIZE_BELOW_MIN.value, None, at, str(exc))
```

So all six blocked symbols report a **lot-granularity** failure they never had. Reading that
table gives the wrong diagnosis — *"USD 35 of risk cannot buy a 0.01 lot, the account is too
small"* — and therefore the wrong fix, which is to raise `starting_equity`. That fix would
change nothing.

**That is precisely the diagnosis I reached and reported before checking**, and it survived
until `account_sweep` — which calls `size_for_setup` directly and does not relabel — raised
the exception in the open. **D-019 §1 recurring, in the same codebase, four decisions
later**: *a rejection reason names the gate that refused, not the reason it refused.* The
one-line check there was "set the parameter to 0 and see if anything changes"; the one-line
check here is "call the sizing function directly and read the exception".

**Not fixed here.** SPEC 19's catalogue has no code for a missing conversion rate, so adding
one is a specification change rather than an implementation detail, and this run was asked to
measure rather than to alter the rejection vocabulary. The recommended fix is a new SPEC 19
reason (`MISSING_CONVERSION_RATE`) surfaced at `Stage.SIZING`, which would have made this
visible in the first table anyone read.

### 3. S4's ceiling binds, and it is per symbol

D-014 §3 recorded `max_sl_pips` as unreachable under S4 *on the fixture* and named the real
question: does a real H4 ATR spend time above 40 pips?

| Symbol | median H4 ATR (pips) | ceiling | above it |
|---|---:|---:|---:|
| GBPUSD | 38.9 | 40 | **47%** |
| GBPJPY | 42.9 | 60 | 16% |
| USDCAD | 28.7 | 40 | 16% |
| EURJPY | 28.9 | 60 | 10% |
| AUDUSD | 24.3 | 40 | 9% |
| EURUSD | 25.4 | 40 | 9% |
| USDCHF | 22.1 | 40 | 8% |
| USDJPY | 22.1 | 60 | 4% |
| NZDUSD | 23.4 | 40 | 3% |
| EURGBP | 19.5 | 40 | 1% |

**12% overall, and the spread across symbols is the finding.** S4 is neither an unavailable
model nor a universally usable one; it is available in proportion to the symbol's volatility.
GBPUSD loses nearly half its setups to it, EURGBP almost none.

Two things this exposed that the one-symbol fixture could not:

- **The ceiling is 60 pips of ATR for JPY pairs, not 40**, because `max_sl_pips` is
  `{default: 60, JPY: 90}`. The first real run computed one ceiling for the whole universe
  and misreported all three JPY pairs.
- The median real H4 ATR is **26.5 pips** against the fixture's 17.4, and the median accepted
  stop **31.5 pips** against 23.6 — stops roughly a third wider.

### 4. The account sweep's denominator was circular

SPEC 18.2's sweep asks *how much of the setup stream each account size can size*. It was fed
only the distances of setups that **had already sized successfully at the default equity**,
so its population was defined by the one number it exists to vary. On a one-symbol fixture
that was close to harmless; on ten symbols it reported "100% sizeable at USD 2,000" over a
stream from which six symbols had silently vanished.

Fixed by capturing every setup whose stop **cleared the SPEC 16.3 caps** — stage `SIZING` or
later — so the stream is defined by the stop rules. `Decision.plan` is `None` on a sizing
rejection, so the distance comes from the armed plan; `EntryPlan.risk_distance` and
`StopCheck.sl_distance` are both `abs(entry_price - stop)`, verified in `stops.check_stop`
rather than assumed.

**The answer did not move**: USD 2,000 still sizes 95% of the stream, on stops a third wider
than the fixture's. That is worth knowing precisely because the correction could have moved
it and did not.

### 5. `M_eff` transferred again

**1.32** over 1,528 setups, against the fixture's 1.36 — the second `M_eff` in the project to
survive contact with real data nearly unchanged (D-022's order-block figure went 1.77 → 1.68).
Use 1.32, not 4, in any S1–S4 correction.

### 6. What this does not establish

**Anything about the portfolio limits.** Nothing closes a trade until Phase 14, so the ledger
fills to `max_open_positions` and the limits-on/limits-off comparison still measures the
absence of exits (`STATE.md` rule 40, unchanged).

**The correlation cap's realised effect.** The universe is ten symbols now, so cluster
membership is finally measurable — but not here, for the same reason: it needs closed trades.
Phase 14.

**That `M_eff` holds outside this split.** Recomputed in-sample, and it should not be
recomputed out of sample to check (protocol §7).
---

## D-027 — Phase 14 on real bars: 102 trades, and the count is structural

| | |
|---|---|
| **Date** | 2026-08-30 |
| **Decided by** | Elie (instruction: *"run phase14_report on the real data"*) |
| **Status** | ACTIVE |
| **Data** | `dataset_hash 2a2bb029…`, 10 symbols × 2019-2022 in-sample, H4 with the real M1 path |
| **Report** | `reports/phase14_gate.md` — 10/10 checks PASS |

### 1. The headline, and the only honest reading of it

| | Limits off | Limits on |
|---|---:|---:|
| Trades | **102** | 57 |
| Win rate | 30.4% | 33.3% |
| **Expectancy (R)** | **−0.1869** | −0.1306 |
| Total R | −19.06 | −7.44 |

Expectancy CI: **[−0.423, +0.064] R** i.i.d., **[−0.382, +0.016] R** stationary block. Read
the block row — trades are not independent and the i.i.d. resample understates uncertainty.

**Both intervals span zero.** On the fixture that was a tautology; here it is a result, and
the reading is exactly this: the point estimate is negative, the interval reaches into
positive territory, and 102 trades cannot separate the two. **Neither evidence of edge nor
evidence against it**, and not a number to quote in either direction.

`n = 102` against `BACKTEST_PROTOCOL.md` §5.1's floor of **200 for a headline claim**, so no
headline claim is made. This is also the in-sample split, where a positive result would carry
no weight anyway.

### 2. The trade count is structural, and that is the finding

| Symbol | trades |
|---|---:|
| AUDUSD | 38 |
| NZDUSD | 32 |
| EURUSD | 19 |
| GBPUSD | 13 |
| the other six | **0** |

**Reaching 200 trades in-sample is not a matter of waiting for more history.** The book is
four symbols because the other six cannot be sized at all — every symbol whose quote currency
is not the account currency, blocked by SPEC 18.2's missing-FX-rate rule while Q1 is open
(D-026 §1). At this funnel rate the four sizeable symbols would need roughly eight more years
to reach 200; the other six would need a conversion series and nothing else.

So **Q1 now blocks the primary metric's sample size**, not merely the live-matching set. That
is the third distinct thing Q1 has been found to block in two days, after the swap table and
the cross-sectional criterion.

### 3. A claim this report inherited was already false

The funnel narrative said armed-to-filled is *"the opposing-sweep cancel, which D-013 §4
measured as a fixture property: a random walk with up to 40 active levels produces sweeps at
a rate no real market sustains"*. **D-025 §3 falsified that**: 0.44 confirmed sweeps per H4
bar on real data against the fixture's 0.47. The cancel removes most limit orders here for
the same reason it did on the fixture, and `cancel_if` clause 2 remains a FROZEN clause
deciding the entry bake-off by itself.

Corrected in the report. **Four further passages in its closing section are still
fixture-framed** — including a repeat of the same "no real market sustains" claim — and are
left for a follow-up rather than committed unverified, since checking a prose change here
costs a full nine-minute run.

### 4. One market at a time, for a measured reason

The report runs ~19 variants over every market and the original built them all up front. Ten
symbols × four years of M1 is **1.04 GB** measured, and `run` costs ~0.0s against
`build_market`'s ~44s — so inverting the loops (build a market, run every variant on it, drop
it) bounds memory at one symbol and costs nothing. Results are unchanged: `Pooled` concatenates
per-market results in the same order.

### 5. What this does not establish

**Anything about out-of-sample performance.** This is the in-sample split; 2023-2024 and 2025
were not read.

**That the Monte Carlo FAILs mean anything beyond the expectancy CI.** Protocol §9's suite asks
whether an edge survives perturbation. There is no demonstrable edge here to perturb, so the
verdicts carry no information the headline interval did not already give.

**That 102 trades support any per-model, per-session or per-symbol breakdown.** Every such cell
in the report is smaller still, and the report marks the 30-99 band as suggestive only.
---

## D-028 — The falsification suite on real bars: §10.1's deciding row is not met

| | |
|---|---|
| **Date** | 2026-08-30 |
| **Decided by** | Elie (instruction: *"run the falsification suite on the real data"*) |
| **Status** | ACTIVE |
| **Answers** | H3 (falsified), H4 (partially) |
| **Data** | `dataset_hash 2a2bb029…`, 10 symbols × 2019-2022 in-sample, 64,228 H4 bars, entry model A |
| **Report** | `reports/falsification.md` |

`BACKTEST_PROTOCOL.md` §10.1 names this row as the one that decides the question, and says
in advance it is the one most likely to fail:

> *"A strategy that beats a null model but not a sweep-only control has not demonstrated
> the thing it claims to demonstrate."*

### 1. The row is not met

The requirement is **every** control, in **both** currencies, each by a CI excluding zero.

| Arm | tests | gross E/setup | net Δ | gross verdict | net verdict | median SL |
|---|---|---:|---:|---|---|---:|
| `baseline` | — | +0.003 | — | — | — | 2.20 |
| `shuffled_liquidity` | H3 | −0.000 | +0.004 | `EQUIVALENT` | `EQUIVALENT` | 2.12 |
| `sweep_only` | H4 | −0.021 | +0.064 | **DIFFERENT** | **DIFFERENT** | 0.88 |
| `choch_only` | H4 | +0.019 | −0.013 | `EQUIVALENT` | `EQUIVALENT` | 2.14 |
| `reversed_order` | H4 | −0.021 | +0.063 | `EQUIVALENT` | **DIFFERENT** | 0.85 |
| `random_time` | floor | −0.010 | +0.047 | `EQUIVALENT` | **DIFFERENT** | 0.95 |

**3 of 5 in net R, 1 of 5 in gross.** Only `sweep_only` clears in both.

### 2. H3 is falsified

A **randomly placed level book performs the same as the real one**: +0.003 R per setup
gross, CI [−0.010, +0.017] — sitting entirely inside the ±0.10 R margin declared before any
arm ran.

`EQUIVALENT` is the verdict the three-way scheme exists to distinguish from `UNDERPOWERED`,
and it is the only one that licenses the word "no". This is **evidence of absence at the
project's own threshold for a tradable edge**, not absence of evidence. The arm had 43,965
setups against the baseline's 1,616, so power is not the limitation.

§6.3 states the consequence it invites: *"rebuilt as a mean-reversion model and the SMC
framing dropped"*. **This entry does not draw that conclusion** — see §5 — but it is the
first time in the project that the data, rather than the fixture, has put it on the table.

### 3. The one surviving component survives weakly

`sweep_only` is beaten in both currencies, which is a real result: **waiting for the CHoCH
after a sweep is better than entering at the sweep.** Read against the floor, though:

| | gross E/setup |
|---|---:|
| `sweep_only` (enter at the sweep) | **−0.021** |
| `random_time` (matched random entry) | −0.010 |
| `baseline` (the full sequence) | +0.003 |

Entering at the sweep is **worse than entering at random**, and the baseline is
`EQUIVALENT` to random. So the CHoCH step's measurable contribution is mostly **recovery
from a bad entry rather than the discovery of signal** — it stops the strategy doing
something actively harmful and returns it to the floor.

`choch_only` says the mirror image: dropping the sweep requirement entirely costs nothing
measurable (−0.013, `EQUIVALENT`), and its gross expectancy (+0.019) is nominally the
highest of any arm.

### 4. Judging in both currencies is what prevented a false pass

`reversed_order` and `random_time` are `DIFFERENT` in net R and `EQUIVALENT` in gross. Their
median stops are **0.85 and 0.95 ATR against the baseline's 2.20**: a fixed spread costs
roughly twice as much per R against a stop half as wide, so an arm that enters earlier is
cost-inflated by geometry rather than beaten on signal.

D-016 §1 found this on synthetic data and could not tell whether it would matter on real
bars. It does: **two of the three net-R "wins" are geometry.** The pre-registration closed
the question in advance by requiring both currencies (§3, closing D-016 §1), which is the
only reason this reads as 1 of 5 rather than 3 of 5. **A criterion settled before the run is
what makes the difference between a null result and a false positive here** — precisely what
§10.2 exists to protect.

### 5. What this does NOT decide

**It is in-sample.** 2023-2024 and 2025 were not read. A result this consequential should be
confirmed out of sample before it is acted on, and doing that spends §7 budget — a decision
to take deliberately, not as a reflex.

**The baseline's own expectancy is not distinguishable from zero** (D-027: −0.19 R over 102
trades, CI spanning zero). Every delta here is between two arms neither of which has
demonstrated an edge, so "the baseline beats X" means "is less bad than X".

**H4 is not settled as a whole.** Its three arms disagree: the CHoCH requirement contributes
(`sweep_only` beaten), the sweep requirement does not (`choch_only` equivalent), and the
ordering does not (`reversed_order` equivalent in gross). "The sequence matters" is too
coarse a hypothesis for what the data says.

**No redesign is decided here.** §6.3's invitation to drop the SMC framing is now supported
by an `EQUIVALENT` verdict on H3 rather than by a fixture-guaranteed null, which is a
genuine change in status — but acting on it is a design decision, and the honest next step
is the out-of-sample confirmation above rather than a rebuild.

### 6. One residual, unverified

The "three guards nothing reaches" table still asserts that
`choch.max_reference_distance_atr` *"rejects nothing — the widest reference sits at about
2.8 ATR"*. That is a fixture measurement carried into a real-bars report, and it may be
false: D-020 found this same parameter binding differently on real data. Re-checking it
costs another ~52-minute run and was not done. **Treat that one cell as unverified.**

### 7. Runtime

~52 minutes at `--workers 5`, against ~2.4 hours serial: §6.3's shuffled arm calls the full
`build_market` once per seed (20 seeds × 10 symbols = 200 builds at ~44s). Every
(symbol, seed) build is independent, seeded and deterministic, so distributing them changes
the wall clock and nothing else — verified before use against a serial run on one symbol ×
2 seeds (identical but for a pytest timing string) and two symbols × 1 seed
(byte-identical). `--workers 1` forces the serial path.
---

## D-029 — The ablation matrix on real bars: every default stands, and a prediction fails

| | |
|---|---|
| **Date** | 2026-08-31 |
| **Decided by** | Elie (instruction: *"run the ablation matrix on the real data"*) |
| **Status** | ACTIVE |
| **Data** | `dataset_hash 2a2bb029…`, 10 symbols × 2019-2022 in-sample, 64,228 H4 bars, 12,350 trading days |
| **Report** | `reports/ablation.md` — 34 runnable variants over §6.5's 19 components |

Baseline: **1,616 setups, 267 filled, E/setup −0.0118 R.**

### 1. Every default stands, and the closest call is a miss by 0.002

Three rows clear §6.5's raw rule (a CI excluding zero). None survives Benjamini-Hochberg
across the matrix at q = 0.10:

| Row | delta (net R) | 95% CI | p | BH q |
|---|---:|---|---:|---:|
| `tp.model = T4` | +0.0188 | [0.0044, 0.0340] | 0.003 | **0.102** |
| `entry.model = D` | −0.0203 | [−0.0401, −0.0013] | 0.018 | 0.306 |
| `manage.be_trigger_r = 1.5` | −0.0023 | [−0.0055, −0.0000] | 0.082 | 0.467 |

**T4 misses by 0.002.** That is the tightest call in the project and precisely the
situation §5.6 exists for: one try out of 34 on a single dataset, at a raw p of 0.003 that
looks compelling in isolation. The correction is applied and the default stands.

Worth stating plainly because the number is tempting: **nothing here licenses switching to
T4.** The delta is measured against a baseline whose own expectancy is not distinguishable
from zero (D-027), so it is a difference between two things neither of which has been shown
to work.

### 2. D-017's prediction about the INERT rows is falsified

`STATE.md` §9 item 8 said, in as many words:

> *"Seven variants change nothing on this fixture and most of them are candidates to come
> alive on real bars: the time stop, break-even and trailing all depend on trades lasting
> longer than a random walk's do."*

They did not.

- **6 of 34 variants are still INERT** — they changed the outcome of zero setups — plus
  **T3 still produces no trades at all**.
- `exit.max_bars_in_trade` at **15, 30, 60 and off are still identical runs**. No trade in
  the sample lives long enough for any horizon to bind, on real FX majors as on noise.
- `manage.trail_mode = structure` is still INERT.

The reasoning behind the prediction was sound and the conclusion was wrong: real markets do
trend, but this strategy's trades do not last long enough for a time stop or a trail to
reach them. That is a fact about the exit policy, not about the market.

**The order-block definitions are still inert too** (B, C and D all identical to the
default), and *that* prediction was right: D-017 §2 said they would stay inert at the
shipped configuration because entry model C reads an FVG and stop S1 reads the sweep
extreme, so nothing consumes an order block. Confirmed.

### 3. The structural findings are unchanged, which is the point of separating them

§6.5's three unimplemented components — session filter, killzone filter and
`liq.tier_confirmation_tf` — are still unimplemented, so **D-002 still cannot be tested
against its own named counterfactual**. That was true on the fixture and is true now,
because it is a fact about the codebase rather than about the data. Splitting the matrix's
output into *structural* / *arithmetic* / *measurement* (D-017) is what makes that legible
across a change of dataset.

### 4. The prose problem, priced

This is the **sixth consecutive report** whose first real-data draft stated its own result
backwards — here, *"every delta below is measured on a random walk, where the true effect
of every component is zero by construction"*, printed above 34 real-data deltas.

What makes this one different is the cost: the matrix takes **~50 minutes** to regenerate,
so the usual "run it, read it, fix it, re-run" loop is expensive. The rule that follows is
**sweep for `random walk`, `fixture` and `synthetic` *before* launching a long run, not
after** — the sweep is free and the re-run is not. Six patches went in as one pass for that
reason.

Two further sources of delay in this run, recorded so the estimates improve:

- The build was predicted at ~25 minutes and took **10,166s (2.8 hours)**, because the
  serial-vs-parallel equivalence check was run concurrently on the same cores. The clean
  re-run took ~50 minutes. **Do not run two build-heavy jobs at once** — they also both
  write `reports/ablation.md`, which is a correctness hazard, not just a speed one.
- The equivalence check itself had to be run from a copy of the script with its output
  redirected, for exactly that reason.

### 5. The parallelisation is verified

`7ed2630` committed the parallel runner flagged as unverified, with the serial-vs-parallel
numeric comparison explicitly deferred. **That check has now been run and passed**: one
symbol, `--workers 1` against `--workers 2`, reports identical across all 34 variants but
for the pytest timing string. The flag on that commit is discharged.

### 6. What this does not establish

**Anything out of sample.** In-sample only, on the four symbols that can be sized.

**That an INERT row is a row that does not matter.** It is a row nothing in this data
reached. §6.5's rule cannot tell those apart, which is why the matrix reports `INERT` and
`NO_TRADES` as statuses outranking any verdict (D-017 §4).

**That T4 is worse than the default**, or better. q = 0.102 is a miss, and a miss is not
evidence for the null.
---

## D-030 — The null is accepted: the project concludes with a documented negative result

| | |
|---|---|
| **Date** | 2026-08-31 |
| **Decided by** | Elie (instruction: *"accept the null"*) |
| **Status** | ACTIVE — **this is the project's terminal decision** |
| **Closes** | `STATE.md` §9's fork, option A |
| **Rests on** | D-020, D-022 … D-029, and the pre-registration committed before any of them |

### 1. The decision

**The strategy is not carried forward.** No execution layer is built, no out-of-sample
budget is spent, and no component is revised. The deliverable is the negative result and
the evidence for it.

This is not abandonment and it is not a failure of the project. `STATE.md` §1 states the
objective in full:

> *"Does the sequence liquidity → sweep → CHoCH/MSS → displacement → entry produce a
> positive expectancy on FX majors that survives out-of-sample testing, transaction costs
> and multiple-testing correction — **and if not, which link fails?**"*

Both halves are now answered. The first is *no*. The second — which link — is answered in
more detail than a bare null would give, and that detail is the substance of the
deliverable.

### 2. What the answer is

| Link in the chain | Verdict | Source |
|---|---|---|
| Liquidity identification | **Contributes nothing.** A randomly placed level book performs the same | D-028 §2 |
| The sweep requirement | **Contributes nothing.** Dropping it costs nothing measurable | D-028 §3 |
| The ordering of the sequence | **Contributes nothing** in gross R | D-028 §1 |
| The CHoCH / displacement step | **Contributes**, and is the only link that does | D-028 §3 |
| — but | it mostly recovers from a bad entry rather than finding signal: entering at the sweep is *worse* than random timing, and the full sequence is level with random timing | D-028 §3 |
| Displacement filtering (H5) | **Adds no measurable value** at h=1 and h=4 | D-024 |
| The FVG concept standalone | **No directional edge** at any horizon | D-023 |

And the headline the protocol asks for: **§10.1's deciding falsification row is not met**
— the full model beats 3 of 5 controls in net R and 1 of 5 in gross, against a
requirement of all five in both (D-028 §1). In-sample expectancy is −0.19 R with a
confidence interval spanning zero (D-027).

### 3. Why this is a strong null rather than a weak one

Three properties distinguish it from "we looked and found nothing":

- **`EQUIVALENT`, not `UNDERPOWERED`.** H3's interval sits *inside* the ±0.10 R margin —
  the project's own declared threshold for a tradable edge, fixed in the pre-registration
  before any arm ran. That is evidence of absence, not absence of evidence, and the
  three-way verdict exists precisely to keep the two apart.
- **The margin, the grid, `M` and the decision rules were all committed in advance**
  (D-018, amended once mechanically at D-021). Nothing was chosen after seeing a result.
- **The one criterion that could have produced a false pass was closed in advance.**
  D-016 §1 found on synthetic data that net R can be won on stop-width geometry alone;
  the pre-registration therefore required §10.1 to be judged in **both** currencies. On
  real bars two of the three net-R "wins" turned out to be exactly that (D-028 §4).
  **Had that rule not been fixed beforehand, this project would have reported a partial
  success it did not have.**

### 4. What this decision does *not* claim

**Not that SMC concepts cannot work.** It claims that *this specification* of them —
frozen defaults, this entry/stop/target configuration, H4 confirmation for every tier
(D-002) — does not produce a measurable edge on ten FX majors over 2019-2022.

**Not that the result is confirmed out of sample.** 2023-2024 and 2025 were never read.
Under option B or C they would be the next step; under A they stay unspent, and the
result is reported as in-sample.

**Not that the measurement was as strong as it could be.** Two known limits, both
recorded: only **4 of 10 symbols could be sized** for want of an FX conversion series
(D-026), so the book is 102 trades against protocol §5.1's floor of 200; and the
development set could not resolve any of three separate studies (rule 78).

**Not that the engine is wrong.** Every gate passed, both controls in every study passed,
the M1 fill path agreed with the bar-level rule on 7,877 armed orders (D-025), and the
parallelised studies were verified against their serial equivalents. The instrument works;
the strategy does not.

### 5. Change control

**Anything that reopens this is a new pre-registration, not an amendment.** Specifically:
revising a component in the light of §2's table, spending out-of-sample budget, or
re-running any study with a changed parameter. Each supersedes this entry and must say so.

The pre-registration itself is untouched and stays valid: its own rule — *nothing may be
changed after the first out-of-sample evaluation* — is not engaged, because there has not
been one.

### 6. What is preserved

Everything needed to reproduce or contest the result:

- `docs/PRE_REGISTRATION.md` at v1.1 + Amendment 1, with its blob hash in `STATE.md` §2.
- `docs/DECISIONS.md` D-001 … D-030, every correction and finding in order.
- The dataset (`dataset_hash 2a2bb029…`, 10 symbols × 2019-2025 M1) and the manifest that
  identifies it.
- Every report under `reports/`, regenerable from `scripts/*_report.py`; each script keeps
  `--synthetic` so the original instrument-validation runs still reproduce.
- 662 tests, green.

A reader who disagrees with the conclusion has the pre-registration, the code, the data
hash and the reports, and can check it.
