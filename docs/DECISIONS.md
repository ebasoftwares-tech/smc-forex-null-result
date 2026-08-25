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
