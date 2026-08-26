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
