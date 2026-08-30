# Pre-registration — SMC Bot v1.1

> **v1.1 supersedes v1.0 (blob `7f8d363a69538c277037d1c68bda527ea9a68871`).** A FROZEN
> default changed — `tp.min_target_rank`, 2.0 → 5.0 — which `PARAMETERS.md` §5.2 and §10
> of this document both classify as a **new pre-registration**, not an amendment. No
> out-of-sample evaluation had occurred, so no result is attached to v1.0 and nothing is
> invalidated. **The new value was selected by its outcome on synthetic data**; §11 records
> the provenance in full and it should be read before any T2 number is cited.

**Status: COMMITTED. All seven of `BACKTEST_PROTOCOL.md` §1's items are now fixed.** Six
were fixed when this document was written. The seventh — item 4, the literal date ranges —
was fixed as a *rule* that yields exactly one answer when applied to whatever history is
acquired, because at the time no data existed to apply it to. History was acquired on
2026-08-30 (Q2 answered) and the rule was applied to it mechanically: **Amendment 1**,
recorded in §4.1 and §10, stamped **before the first strategy backtest** exactly as this
document scheduled. It changed no threshold, no grid, no `M` and no decision rule.

**Nothing in this file may be changed after the first out-of-sample evaluation.** Its hash
is recorded in `STATE.md` §2 and in the commit that introduced it. Verify with:

```bash
git hash-object docs/PRE_REGISTRATION.md
```

**Why it is being written now, before the data.** `BACKTEST_PROTOCOL.md` §1 requires it
before the first *strategy* backtest. Every run so far has been on a synthetic random walk
and has validated an instrument rather than measured a strategy, so nothing has yet
triggered the requirement. Writing it now — while no real result exists to be tempted by —
is the only moment at which it can be written honestly. **A pre-registration written after
seeing a result is not one.**

---

## 1. The hypothesis, in falsifiable form

### 1.1 Primary

> **H1.** On FX majors over the out-of-sample period, a strategy executing
> `liquidity → sweep → MSS (CHoCH + displacement) → entry` produces expectancy per trade
> significantly greater than zero after realistic costs, with a lower bootstrap confidence
> bound above zero after correction for the number of configurations evaluated.
>
> **H0.** Expectancy is not distinguishable from zero after costs and correction.

### 1.2 Component hypotheses — the actual deliverable

`BACKTEST_PROTOCOL.md` §1.2 states these are the deliverable *"even if H1 fails"*, because
brief §33 asks which link breaks, not whether the whole chain pays.

| Id | Hypothesis | Falsified by | Instrument | Status |
|---|---|---|---|---|
| H2 | Confirmed sweeps carry directional information | §6.1 forward-return study shows no difference vs matched controls | `sweep_study.py` | built, open |
| H3 | Real liquidity levels matter | §6.3 shuffled-liquidity control performs the same | `falsification.py` | built, open |
| H4 | The *sequence* matters | §6.4 sweep-only / CHoCH-only / reversed-order controls perform the same | `falsification.py` | built, open |
| H5 | Displacement filtering adds value | MSS and CHoCH-not-MSS forward returns are indistinguishable | `marginal_value.py` | built, open |
| H6 | MTF bias filtering adds value | `gate_mode = none` performs the same or better | — | **BLOCKED**: Phases 2–4 unbuilt |
| H7 | Retracement entries beat market entries | Per-**setup** expectancy of models B–E does not exceed model A | `ablation.py` | built, open |

**H6 cannot be evaluated by this pre-registration.** The MTF gate is an injected
always-pass predicate, which *is* `gate_mode = none` — so the baseline already runs the
control arm and there is no treatment arm to compare it against. Every MSS count in the
project is therefore an upper bound, and H6 requires a new pre-registration once the bias
engine exists (§10).

### 1.3 The three-way reading, declared in advance

H3, H4, H5 and H7 are falsified by a **negative** result. A confidence interval that merely
contains zero is absence of evidence, so each is reported with the three-way verdict:

| Verdict | Condition | What it licenses |
|---|---|---|
| `DIFFERENT` | CI excludes zero | The component contributes |
| `EQUIVALENT` | CI lies entirely inside ±0.10 R | **Only this** licenses "contributes nothing" |
| `UNDERPOWERED` | CI spans zero *and* extends past the margin | The study cannot answer |

The equivalence margin is **0.10 R**, fixed here and not chosen freely: it is §10.1's own
expectancy threshold for trading this system live, so a difference smaller than the
project's declared boundary of a tradable edge cannot be a difference that matters. The
same constant is `falsification.EQUIVALENCE_MARGIN_R`, shared by both studies and pinned by
a test.

---

## 2. The primary metric and its acceptance threshold

**Primary metric: expectancy in R per trade, net of costs, at `cost.multiplier = 1.5`.**

| | |
|---|---|
| Threshold | **≥ +0.10 R**, with the 5th-percentile bootstrap bound **> 0** |
| Interval | Stationary block bootstrap, 10,000 resamples, mean block ≈ 20 trading days (§5.3) |
| Evaluated on | The out-of-sample split, under the §7 budget |

**Why R and not net return.** Net return conflates edge with position sizing and with the
compounding path (§4.1). R-expectancy is a property of the strategy, and the engine
computes it in a pass that structurally cannot see equity, so the claim is true by
construction rather than by assertion.

**Why 1.5× costs.** §3.3 makes cost sensitivity mandatory rather than optional. A strategy
that only clears the bar at modelled costs has no margin for the difference between a
backtest's spread assumption and a real fill.

### 2.1 The one place R is not sufficient, declared now

R is a **ratio**, and the arms of a comparison do not always share its denominator. An arm
that enters earlier has a tighter stop and therefore loses more of its R to a fixed spread
— which in net R is indistinguishable from a worse component. On the synthetic fixture this
was enough to satisfy §10.1's falsification row *on a random walk* (D-016 §1).

Every comparison between arms with different stop geometry is therefore reported in **both**
gross and net R, with the median stop width beside it. See §7.1 for the decision this forces.

---

## 3. Secondary metrics and their thresholds

§10.1's remaining rows, all of which must hold on out-of-sample data at
`cost.multiplier = 1.5`:

| Criterion | Threshold |
|---|---|
| Trades (OOS) | ≥ 200 |
| Profit factor | ≥ 1.20 |
| Max drawdown | ≤ 20% of equity at `risk.pct_per_trade` |
| Walk-forward efficiency | ≥ 0.50 |
| Profitable OOS windows | ≥ 60% |
| Parameter stability (§8) | Chosen value moves ≤ 1 grid step between adjacent windows for ≥ 70% of (parameter, window) pairs |
| Cross-sectional | ≥ 6 of 10 symbols with positive expectancy, **same parameters** |
| Deflated Sharpe | > 0 at `M = 9,600` (§5) |
| Falsification suite | Full model beats **every** §6.3/§6.4 control in **both** gross and net R, each by a margin whose CI excludes zero |
| Plateau (§5.5) | Every TUNABLE value sits inside a plateau: performance at v−1 and v+1 each ≥ 70% of performance at v, with ≥ 3 adjacent grid points beating the no-filter baseline |
| Paper trading | ≥ 60 days, ≥ 95% entry-signal agreement with a same-period backtest |

Reported alongside, never as acceptance criteria: win rate, CAGR, MAR, Sharpe, Sortino,
Ulcer, average win/loss R, streaks, duration, time in market, Kelly fraction (**reported
only, never used for sizing**).

**The funnel (§4.3) is reported before any performance figure**, per symbol per year. It
says whether the strategy exists in sufficient quantity to be measured at all. Phase 9's
gate — ≥ 300 MSS across the universe, ≥ 120 in the development set — currently **passes on
a projection from a synthetic conversion rate, not a measurement**, and replacing that
projection is the first thing real data does.

---

## 4. The splits

### 4.1 Time

*The paragraph and rule table below are the text as registered on 2026-08-28, when no data
existed, and are left verbatim. Amendment 1 at the end of this section stamps the dates the
rule yields.*

Item 4 of §1 is the only one that cannot be a literal today: no data has been acquired.
Fixing invented dates would be worse than fixing none, and leaving the item blank until the
data is in hand would mean completing the pre-registration after seeing the sample. **So it
is fixed as a rule**, which is as binding as a date and yields exactly one answer:

| Split | Rule | Use |
|---|---|---|
| **In-sample** | The **earliest 4 years** of acquired history | Development, TUNABLE optimisation, all ablations, the whole falsification suite |
| **Out-of-sample** | The **next 2 years** | Touched under the §7 budget only |
| **Holdout** | Everything after that | Touched **exactly once**, at the end, immediately before go/no-go |

Ordering is chronological and non-negotiable: in-sample is earliest, holdout is latest. Any
other arrangement leaks the future into the fit.

The acquired period **must** contain 2020 (volatility shock), 2022 (trending) and at least
one extended range regime (§2). A source that cannot supply those is rejected before the
splits are drawn, not accommodated by redrawing them.

**Amendment 1 (2026-08-30) stamped the literal dates.** The acquired history is 10
symbols of M1 from HistData (`dataset_hash 2a2bb029…`), earliest bar
**2019-01-01T22:00Z**, latest bar **2025-12-31T21:58Z** — seven calendar years, so the
rule above yields four, two and one:

| Split | Literal range (UTC) | Calendar years |
|---|---|---|
| **In-sample** | 2019-01-01T00:00:00Z → 2022-12-31T23:59:59Z | 2019, 2020, 2021, 2022 |
| **Out-of-sample** | 2023-01-01T00:00:00Z → 2024-12-31T23:59:59Z | 2023, 2024 |
| **Holdout** | 2025-01-01T00:00:00Z → 2025-12-31T23:59:59Z | 2025 |

A bar belongs to the split holding the UTC calendar year of its `open_time`. That is how
the Parquet store is partitioned and how `ingest.read_series(years=…)` selects, so a run
on one split reads no bar from another. **Any future runner must select the same way**;
the equivalence of "the split" and "these year partitions" is what makes the stamp
operative rather than decorative.

Both regimes §4.1 requires by name land **in-sample**: 2020 (volatility shock) and 2022
(trending). §10 records the two consequences the rule produced that nobody chose.

### 4.2 Symbols

Fixed by §2.1 and not a choice made here:

| Set | Symbols |
|---|---|
| Development | EURUSD, GBPUSD, USDJPY |
| **Cross-sectional out-of-sample** | AUDUSD, USDCAD, USDCHF, NZDUSD, EURJPY, GBPJPY, EURGBP |

A strategy tuned on three pairs that transfers to seven unseen pairs **at the same
parameters** has passed a test no amount of time-series validation substitutes for. The
parameters are ATR-normalised (SPEC 1.6) precisely so that this transfer is meaningful.

---

## 5. The TUNABLE grid, and `M`

**These six parameters may be optimised. Nothing else may.**

| Parameter | Default | Grid | Points |
|---|---|---|---:|
| `sweep.max_penetration_atr` | 1.00 | 0.5, 0.75, 1.0, 1.5, 2.0 | 5 |
| `sweep.max_confirmation_bars` | 3 | 1, 2, 3, 5 | 4 |
| `disp.min_leg_atr` | 1.5 | 0 (off), 1.0, 1.25, 1.5, 2.0, 2.5 | 6 |
| `choch.max_bars_after_sweep` | 12 | 4, 8, 12, 18, 24 | 5 |
| `entry.pending_expiry_bars` | 6 | 3, 6, 9, 12 | 4 |
| `tp.r_multiple` | 2.0 | 1.5, 2.0, 2.5, 3.0 | 4 |

```
M = 5 × 4 × 6 × 5 × 4 × 4 = 9,600
```

`M` is carried into the Deflated Sharpe Ratio and into §5.6's expected-maximum-Sharpe-under-
the-null, so **it scales every significance claim the project will make.** It is declared
in `bot/research/preregistration.py` and pinned by `tests/test_preregistration.py`, which
parses the grids back out of the schema field descriptions and fails if the two diverge.

### 5.1 This supersedes `PARAMETERS.md` §2, which is wrong three ways

Recorded rather than quietly corrected, because the superseded number has been cited
elsewhere in the project:

| | `PARAMETERS.md` §2 | The schema that runs |
|---|---|---|
| TUNABLE count | 8, including `bias.min_score` | **6 gridded + 1 excluded** — there is no `bias` section at all |
| `disp.min_leg_atr` | 5 values | **6** — the schema includes `0 (off)` |
| Stated product | `5 × 4 × 5 × 5 × 4 × 4 × 5 = 8,000` | that product is **40,000**; 8,000 is the product *without* the trailing `× 5` |
| Declared `M` | **6,912**, "after removing dominated combinations" | no rule for "dominated" is stated anywhere, so it cannot be recomputed |

**`M` is the full Cartesian product, with no removals**, for three reasons:

1. **It is reproducible.** A number nobody can recompute is not a pre-registration.
2. **It is conservative.** `M` exists to discount our own best in-sample result; a larger
   `M` discounts harder, and that is the only direction that cannot flatter the outcome.
3. **"Dominated" is a judgement about results.** Deciding which configurations could not
   have won requires knowing how they perform — exactly the knowledge a pre-registration is
   written before having.

### 5.1a The one grid point a reader might reasonably dispute

`disp.min_leg_atr = 0` is written in the schema as a TUNABLE grid point and labelled
`0 (off)`. It is arguably an *ablation* — "no displacement magnitude requirement" — rather
than a tune, and reading it that way gives `M = 8,000` instead of 9,600.

**It is counted as a grid point**, for the same reason the product is not reduced: the
schema is what runs and it declares six values, and the larger `M` corrects harder. The
alternative is recorded here so that adopting it later is visibly a change to a declared
number rather than a clarification.

Worth knowing when the ablation is read: on the synthetic fixture `min_leg_atr = 0` admits
**no additional setups at all**, because `disp.require_fvg` and `disp.min_body_ratio`
already imply the magnitude threshold (D-017 §6a). If that survives on real bars, this
parameter — a TUNABLE carrying §5.5's plateau requirement — cannot be plateaued
one-at-a-time, and §5.5's row is unevaluable for it.

### 5.2 `risk.pct_per_trade` is excluded, provably

It is TUNABLE and bounded to [0.10, 0.50] by the brief, and it is **not** in the grid.
SPEC 18.1 makes `position_size` a pure function of `(equity, risk_pct, sl_distance)`, so
risk percent scales PnL and **cannot move R** — and R-expectancy is the primary metric.
Sweeping it would sweep a parameter that cannot change the number under test. It is
reported at its default and varied only for the drawdown figures §10.1 states in percent.
Asserted against the real sizing function, not quoted from the spec.

### 5.3 `bias.min_score` is deferred, and its arrival is a re-registration

`PARAMETERS.md` counts it as the eighth TUNABLE with a grid of {0, 1, 2, 3, 4}. It does not
exist in the schema. When the bias engine lands, **`M` becomes 48,000** and every
correction computed under `M = 9,600` is superseded. Stating that here is what stops the
grid widening quietly later.

---

## 6. The ablation list

Fixed by `BACKTEST_PROTOCOL.md` §6.5 and implemented as `bot/research/ablation.py`: **34
runnable variants over the 19 components §6.5 names.** Each is toggled one at a time against
the shipped defaults and reported as a delta with a stationary block-bootstrap CI, in both
currencies, with Benjamini–Hochberg at q = 0.10 across every runnable row.

**§6.5's decision rule is adopted verbatim**: *a component whose delta CI spans zero is
reported as "no measurable effect" and its default stands* — not kept because it looked
slightly positive, not removed because it looked slightly negative.

### 6.1 Five of the nineteen cannot be toggled, and that is declared now rather than discovered later

| Component | Status | Why |
|---|---|---|
| MTF gate | **BLOCKED** | Phases 2–4 unbuilt; the baseline already runs the control arm |
| counter-monthly rule | **BLOCKED** | Same — no `bias` section exists |
| session filter | **ABSENT** | No execution-side session filter exists anywhere in the codebase |
| killzone filter | **ABSENT** | `role: killzone` is an accepted value read only by `liquidity_session_names`, which picks liquidity *sources* |
| tier → confirmation-TF map | **ABSENT** | `liq.tier_confirmation_tf` is declared, marked ABLATION, and **read by no module** |

The last is the serious one. §6.5 names it *"the D-002 counterfactual"* — the alternative to
the decision that makes this a session-to-session swing model rather than the intraday one
the source material describes. **D-002 cannot currently be tested against its own
alternative**, and this pre-registration does not pretend otherwise: the ablation is
reported as unavailable, and implementing it is a new registration (§10).

### 6.2 Two ablation arms produce no trades at the shipped defaults

| Arm | Cause |
|---|---|
| `tp.model = T3` | `RR_BELOW_MIN` on every setup: T3's `tp_1` is the 1R rung against `tp.min_rr = 1.5` (D-014 item 4) |

**T2 was on this list in v1.0 and is not on it now**, and the reason is a correction rather
than a parameter change: it armed nothing because the engine never passed the liquidity
book to the target gate, and because the target side was inverted. Both were bugs; both are
fixed (D-019). T2 now arms.

So §6.5's "each TP model" row is **T1, T2 and T4** — T3 alone remains structurally dead,
and no default was changed to rescue it. §10.2 forbids moving a parameter to make a result
appear; moving one *before* any result exists is a different act, but it is still a
specification change and belongs in a registration of its own.

### 6.3 Three components cannot be read one at a time

§6.5 assumes a component can be isolated. Three cannot, because the default entry model
consumes what another component produces: `disp.mode`, `disp.require_fvg` and
`ob.definition` are all coupled to `entry.model`. Each is reported **jointly with the entry
model**, on a second axis, and never as a one-at-a-time delta — which would attribute the
entry model's dependency to the component being toggled.

---

## 7. The decision rule

### 7.1 A decision this pre-registration takes: the falsification row is judged in both currencies

D-016 §1 left this open and §1 requires it closed before the first run.

> **The full model must beat every §6.3 and §6.4 control in gross R *and* in net R, each by
> a margin whose CI excludes zero.**

Neither currency alone is defensible. **Net R alone can be cleared on stop width** — proved
on a random walk, where the true difference is zero by construction and the baseline still
cleared the bar by **+0.125 R, CI [0.019, 0.229]**, because its median stop is 2.24 ATR
against `sweep_only`'s 0.96. In gross R the same comparison is +0.018, CI [−0.084, 0.123]
— containing zero, which is the correct answer. **Gross R
alone** ignores that a strategy has to pay its costs to be worth trading. Requiring both is
strictly the most conservative reading and is the only one that cannot be satisfied by
geometry.

The two rejected alternatives are recorded so that adopting one later is visibly a change:
reading the row in gross R only, or matching stop distance across arms (which changes what
the controls *are* — a sweep-only arm with the baseline's stop is not "enter on sweep
confirmation").

### 7.2 PASS

Every row in §3 holds on the out-of-sample split at `cost.multiplier = 1.5`, and the
holdout evaluation — the single one permitted — does not contradict them.

### 7.3 FAIL

≥ 200 OOS trades **and** at least one §3 row is violated, with enough power to say so.

### 7.4 INCONCLUSIVE — a distinct verdict, defined in advance

Declared explicitly because §10.1 is binary and would otherwise absorb this case into FAIL:

> Fewer than 200 OOS trades, **or** the primary metric's minimum detectable effect at 80%
> power exceeds the +0.10 R it is being tested against.

The second clause is the one that matters. **A study that could not have detected the effect
it requires has not failed to find it — it has failed to look.** Reporting that as FAIL
would claim knowledge the sample does not contain, and this project has already had to
learn the same lesson three times (Phase 7's 3-of-20 false positives, Phase 8's 0.6σ
"structure", and the H5 study's `UNDERPOWERED`-is-not-`EQUIVALENT`).

An INCONCLUSIVE verdict is written up under §10.2's deliverable, with the sample size that
*would* have resolved it.

### 7.5 On FAIL or INCONCLUSIVE, the deliverable is §10.2's, in full

1. **Which component fails**, located by the funnel and the falsification suite.
2. **Why**, with measured evidence and intervals.
3. **Which assumptions were unsupported**, mapped to the specification section that made them.
4. **What to test next**, as a new pre-registration.

**Prohibited on any outcome:** widening the TUNABLE grid; promoting a FROZEN parameter;
extending the OOS budget past 10 evaluations; trying a filter that was not pre-registered;
reporting the best in-sample configuration as the result.

> *"A negative result documented to this standard is a genuine, valuable outcome of the
> project. A positive result obtained by breaking §10.2 is worth less than nothing, because
> it will be traded."*

---

## 8. What this pre-registration knows it cannot currently evaluate

Listed so that none of it can later be presented as a discovery.

| | |
|---|---|
| H6 (MTF bias) | No bias engine. Every MSS count in the project is an upper bound |
| The D-002 counterfactual | `liq.tier_confirmation_tf` read by no module |
| Session and killzone filters | Not implemented |
| T3 target model | Arms no trades at the shipped defaults (D-014 item 4). T2 was listed here in v1.0 and no longer is — see §6.2 |
| `ob.definition` | Reaches the engine correctly but is inert at the shipped defaults — only entry D or SL S3 consumes an order block |
| §5.5 plateaus | Never run: a plateau needs a metric that varies across the grid, and on a random walk it varies only by noise |
| The Phase 9 funnel gate | Passes on a **projection**, not a measurement |

---

## 9. The out-of-sample budget

```
OOS_BUDGET = 10 evaluations for the entire project.
HOLDOUT    = exactly 1, at the end.
```

Every OOS run is appended to `oos_evaluations.log` — append-only, committed, timestamped —
**before it executes**, carrying `run_id`, `config_hash`, the reason, and the pre-committed
expectation. When the budget is exhausted the project reports whatever the OOS results were.

Further OOS runs are permitted only after a **new** pre-registration that explicitly
declares the previous OOS period as now in-sample, which is the honest accounting for what
it is.

---

## 10. Amendments

An amendment is a commit to this file. Each is appended below with its date, the reason, and
the hash of the superseded version. **Amendments that change a threshold, a grid, `M`, or a
decision rule are not amendments — they are a new pre-registration**, and every result
obtained under the old one is reported as such.

| # | Date | Change | Permitted because |
|---|---|---|---|
| 1 | 2026-08-30 | §4.1's literal date ranges stamped from the split rule | Mechanical application of a rule fixed in advance; changes no threshold, no grid, no `M` and no decision rule |

**The one amendment that was scheduled has been made** — see the record below. No further amendment is scheduled.

The following are known **new-registration** triggers, named in advance:

- The bias engine landing (H6 becomes evaluable; `M` → 48,000).
- Implementing the session filter, the killzone filter, or `liq.tier_confirmation_tf`.
- Changing `tp.min_rr` so that T3 can arm. (`tp.min_target_rank` was changed in v1.1 —
  see §11.)
- Any change to the falsification row's currency rule (§7.1).

### Amendment 1 — 2026-08-30 — §4.1's literal dates

| | |
|---|---|
| Superseded | v1.1, blob `b9142a0fcb0960016162b1c18bb6fa60cfc4a6f5` |
| Class | Scheduled amendment (§10) — **not** a re-registration |
| Results invalidated | **None.** No out-of-sample evaluation has occurred |
| Basis | §4.1's rule applied to the acquired history. No discretion was exercised |

The dates are in §4.1. Seven calendar years were acquired (2019-01-01T22:00Z to
2025-12-31T21:58Z), the rule says earliest four / next two / remainder, and that gives
**2019-2022 / 2023-2024 / 2025**. There is no second reading of it.

The stamp was triggered by the Phase 9 funnel run (D-020), which is the first thing to
apply the split to real bars. That run read the in-sample years only.

**Two consequences the rule produced and nobody chose.** Both are recorded rather than
engineered around, because engineering around either one now would mean choosing a split
boundary with the data in hand — which is the thing §4.1 exists to prevent.

1. **The holdout is a single year**, against four in-sample and two out-of-sample. The
   rule says *"everything after that"*, and seven years is what was acquired. It is not
   widened. §3's ≥ 200-trade minimum is stated against the out-of-sample split; the
   holdout is touched exactly once and is as large as the history makes it. If one year
   proves too thin to carry the final go/no-go, that is a finding about the acquisition,
   and the honest response is to acquire more history and re-register — not to redraw the
   line.
2. **The out-of-sample/holdout boundary falls mid-week; the in-sample/out-of-sample
   boundary does not.** 2022-12-31 is a Saturday, so the first out-of-sample trading week
   opens cleanly on Sunday 2023-01-01. But 2024-12-31 is a **Tuesday**, so the week that
   opened Sunday 2024-12-29 at 22:00 UTC is cut in half by the boundary and closes on
   Friday 2025-01-03. Because splits are selected as whole year partitions, an
   out-of-sample run reads no holdout bar: the week is **truncated, not leaked**, and a
   position still open at 2024-12-31T23:59Z is right-censored by the end of its series
   rather than resolved from data it is not entitled to see. That is the safe direction,
   and it is worth knowing when reading trade counts at a split edge.

---

## 11. Re-registration record

### v1.1 — 2026-08-28 — `tp.min_target_rank` 2.0 → 5.0

| | |
|---|---|
| Superseded | v1.0, blob `7f8d363a69538c277037d1c68bda527ea9a68871` |
| Class | FROZEN default (`PARAMETERS.md` §5.2 → new pre-registration) |
| Results invalidated | **None.** No OOS evaluation had occurred |
| Basis | **Selected by its outcome on synthetic data, by explicit instruction** |

**This is the basis §10.2 prohibits, and it is recorded rather than dressed up.** The value
was chosen because T2 arms 48 of 165 setups and produces 10 trades at 5.0, against 5 and 0
at 2.0. The fixture is a random walk, so those 10 trades are noise: **the number carries no
evidence that 5.0 is right**, only that it is where this fixture's targets sit far enough
away to clear the 1.5 RR gate.

There is a separate, outcome-independent case that **2.0 was wrong**, which stands on its
own and would survive on any data: `rank` = `tier_weight(1–3) + 0.5 × min(strength, 4) +
recency(0–1)` spans **[1.5, 6.0]** and measures a median of **4.86** across 107,882
level-bars, so a threshold of 2.0 sat essentially at the floor and filtered almost nothing.
It was mis-scaled against the function it gates. That argument justifies *changing* it; it
does not justify *this* value.

**What follows for reading results:** any T2 figure produced under v1.1 is conditional on a
parameter fitted to the synthetic fixture, and `tp.min_target_rank` must be re-derived on
real bars before T2 is compared with T1 or T4 for any purpose. The honest treatment on real
data is to re-register the value from the observed rank distribution — a percentile stated
in advance — rather than to inherit this one.

Provenance is carried in the schema field description, `defaults.yaml`, the ablation
matrix's spec note and D-019, so the value cannot be cited as reasoned by someone who has
not read this.
