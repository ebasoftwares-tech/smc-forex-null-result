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
