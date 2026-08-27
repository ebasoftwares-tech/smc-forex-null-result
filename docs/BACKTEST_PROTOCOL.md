# Backtest and Validation Protocol — SMC Bot v1.0

Companion to `SMC_STRATEGY_SPECIFICATION_v1.0.md`. This document exists because the project's
stated objective (brief §33) is to find out whether this methodology has an edge, not to
produce a curve that looks like one. Everything here is designed so that a null result is a
clean, reportable outcome rather than a prompt to keep tuning.

---

## §1. Pre-registration

**Before the first strategy backtest is run**, a pre-registration file is committed and its
hash recorded. It states:

1. The hypothesis, in falsifiable form.
2. The primary metric and its acceptance threshold.
3. The secondary metrics and their thresholds.
4. The in-sample and out-of-sample date ranges.
5. The complete TUNABLE grid and therefore the configuration count `M`.
6. The ablation list.
7. The decision rule — what constitutes pass, fail, and inconclusive.

### 1.1 The primary hypothesis

> **H1.** On FX majors over the out-of-sample period, a strategy executing
> `liquidity → sweep → MSS (CHoCH + displacement) → entry` produces expectancy per trade
> significantly greater than zero after realistic costs, with a lower bootstrap confidence
> bound above zero after correction for the number of configurations evaluated.
>
> **H0.** Expectancy is not distinguishable from zero after costs and correction.

### 1.2 Component hypotheses (each independently falsifiable)

| Id | Hypothesis | Falsified by |
|---|---|---|
| H2 | Confirmed sweeps carry directional information | §6.1 forward-return study shows no difference vs matched controls |
| H3 | Real liquidity levels matter | §6.3 shuffled-liquidity control performs the same |
| H4 | The *sequence* matters | §6.4 sequence-scramble controls perform the same |
| H5 | Displacement filtering adds value | MSS and CHoCH-not-MSS forward returns are indistinguishable |
| H6 | MTF bias filtering adds value | `gate_mode = none` performs the same or better |
| H7 | Retracement entries beat market entries | Per-**setup** expectancy of models B–E does not exceed model A |

**These are the deliverable.** Even if H1 fails, H2–H7 answer "which component fails, and why"
— which is what brief §33 asks for.

---

## §2. Data

| Item | Requirement |
|---|---|
| Period | **Minimum 3 years, target 5–7.** Must include 2020 (volatility shock), 2022 (trending), and at least one extended range regime |
| Sunday hours | The Sun 21:00–24:00 UTC block MUST be present in the raw data. Some archives drop it. Under D-001 it merges into Monday's D1 bar (spec §2.6.1), so a source that omits it silently changes every Monday D1 high/low. Verified at ingest, per week |
| Resolution | M1 preferred (required for `intrabar_mode = m1_path`); M5 acceptable; H1 degrades sessions and intrabar resolution |
| Sources | Two independent sources, reconciled. Broker history for the live-matching set, plus one archive (Dukascopy / TrueFX / HistData) as the research set |
| Reconciliation | Per symbol per day: bar counts, high/low agreement within a tolerance, and a report of every disagreement above it. Systematic differences must be understood before results are trusted, because they land exactly on the wicks this strategy reads |
| Rates | Quote→account FX rate series for every non-account-currency quote (§18.2) |
| Spread | Historical spread series where available; otherwise the session-constant fallback with the sensitivity run of §3.3 |
| Manifest | Every file hashed; `dataset_hash` stamped on every run |

### 2.1 Splits

| Split | Period (assuming 2019-01 → 2025-12 available) | Use |
|---|---|---|
| **In-sample (IS)** | 2019-01 → 2022-12 (4y) | Development, TUNABLE optimisation, all ablations |
| **Out-of-sample (OOS)** | 2023-01 → 2024-12 (2y) | **Touched under the budget of §7 only** |
| **Holdout** | 2025-01 → present | Touched **once**, at the end, immediately before the go/no-go decision |

Symbols are also split: EURUSD, GBPUSD, USDJPY are the development set; the other seven are a
**cross-sectional out-of-sample**. A strategy tuned on three pairs that works on seven unseen
pairs with the same parameters has passed a test that no amount of time-series validation can
substitute for — and it is the cheapest strong evidence available here, because the parameters
are all ATR-normalised (§1.6) precisely so that this transfer is meaningful.

---

## §3. Execution realism

Full cost model in specification §26. What this protocol adds:

### 3.1 Fill discipline

- Market orders: next-bar open, or the M1 price at `signal_time + latency`. Never the signal
  bar's close.
- Limit orders: require `backtest.limit_fill_buffer_pips` of penetration beyond the level.
- Stops: fill at the worse of the level and the next available price; gaps fill at the gap.
- Partial fills modelled where the broker reports them; partially-filled trades are excluded
  from headline expectancy and reported separately (their risk was not the planned risk).

### 3.2 Intrabar resolution

`m1_path` mandatory when M1 exists. Where it does not, `pessimistic` is mandatory and the
**M1-availability delta** must be reported: re-run any period where both are possible under
both modes, and publish the difference. That number is the error bar on every result produced
without M1, and quoting results without it is quoting a result without its uncertainty.

### 3.3 Cost sensitivity — mandatory, not optional

Every headline result is reported at `cost.multiplier` ∈ {1.0, 1.5, 2.0}. A strategy whose
expectancy is destroyed at 1.5× is not deployable: broker spreads vary by more than that, and
so do the same broker's spreads across the day and across years.

---

## §4. Metrics and breakdowns

### 4.1 Headline metrics

Total trades · win rate · **expectancy in R** (primary) · profit factor · net return ·
CAGR · **max drawdown (equity and R)** · drawdown duration · MAR · Sharpe (daily equity,
annualised √252) · Sortino · Ulcer index · average win R / average loss R · largest win/loss ·
max consecutive wins/losses · average duration · time in market · exposure-adjusted return ·
Kelly fraction (**reported only, never used for sizing**).

**Expectancy in R is primary.** Net return conflates edge with position sizing and with the
compounding path; R-expectancy is the property of the strategy itself.

### 4.2 Required breakdowns (brief §26)

Symbol · session (sweep / MSS / entry) · day of week · hour of day · month · year ·
Monthly/Weekly/Daily/H4 bias · alignment label · liquidity source · liquidity tier · liquidity
side · sweep type (single-bar vs multi-bar) · penetration decile · CHoCH reference mode ·
bars-sweep-to-MSS · displacement decile · entry model · SL model · TP model · planned RR ·
volatility regime (ATR tercile) · `dst_desync` · `data_suspect`.

Each is a DuckDB `group_by` on `trades.parquet` (§21.2). **Every breakdown cell reports its
n**, and any cell with `n < 30` is labelled *insufficient sample* and MUST NOT be described as
a finding. `n < 100` may be described as *suggestive* only.

#### 4.2.1 The sweep-session x entry-session matrix — required, not optional

Added by decision D-002 (spec §0.4a). A 4x4 table (Asia / London / NY / other) of trade count
and expectancy, cross-tabulating `sweep_session` against `entry_session`, plus the distribution
of `bars_sweep_to_mss`.

Under H4-only confirmation the minimum sweep-to-MSS distance is two H4 bars, so a sweep during
London can rarely be entered during London. This table is what makes that visible instead of
implicit, and it is the evidence that decides the `liq.tier_confirmation_tf` ablation. If the
diagonal (sweep and entry in the same session) is nearly empty, the strategy being tested is
not the one the brief's §6 example describes, and the report must say so in those words.

### 4.3 The funnel — reported before any performance figure

```
levels created → levels in play → sweeps triggered → sweeps confirmed
  → CHoCH reference found → CHoCH events → MSS events
  → setups passing gates → orders armed → orders filled → trades closed
```

With conversion rates at each step, per symbol per year. The funnel is the first thing to
read: it says whether the strategy exists in sufficient quantity to be measured, and where the
population is being lost. A 30% drop at one step that was expected to be 90% is a bug
long before it is a finding.

### 4.4 Per-setup vs per-trade expectancy

Models B–E do not always fill. Comparing per-trade win rates across models is invalid.
Every model comparison reports **three** numbers:

```
fill_rate           filled setups / qualified setups
E_trade             mean R over filled trades
E_setup             (fill_rate × E_trade)          ← the comparable quantity
```

`E_setup` is the number that answers "which model would I rather run?", because it charges a
model for the opportunities it declines to take.

**It is not sufficient on its own, because `qualified setups` differs by model (D-015 §6).** SPEC 16.3's stop cap rejects model A on 60% of setups -- and on the *strongest-displacement* ones specifically, since for a market entry a strong displacement is a wide stop. Dividing by each model's own qualified count scores model A on its easy 40%. A fourth column,

```
E_all_setups        (total R) / (every MSS setup in the stream)
```

uses the one denominator every model shares, and is the only figure comparing them over a single population.

---

## §5. Statistical protocol

### 5.1 Minimum samples

| Claim | Minimum |
|---|---|
| Headline strategy result | 200 trades across the universe |
| Per-symbol claim | 60 |
| Any subgroup finding | 100 (30–99: *suggestive*; <30: not reportable) |
| Ablation delta | 150 in each arm |

### 5.2 Confidence intervals

Bootstrap, 10,000 resamples, BCa intervals, on expectancy and profit factor. Reported for
every headline number and every ablation delta.

### 5.3 Trade independence

Trades are **not** independent: same-day trades share regime, and correlated symbols share
direction. Plain i.i.d. bootstrap therefore understates uncertainty. Rules:

- **Stationary block bootstrap** (mean block length ≈ 20 trading days) for anything conditioned
  on a slow-moving variable — bias, regime, session.
- Cluster by trading day for portfolio-level metrics.
- The effective sample size `n_eff` is computed and reported alongside `n`.

### 5.4 Power, and the Monthly-bias case

Before any subgroup claim, report the **minimum detectable effect** at 80% power for that
subgroup's `n_eff`. For Monthly bias (§7.7) `n_eff` is the number of independent monthly
regimes — realistically 6–12 over five years — so the minimum detectable effect is enormous and
the honest conclusion will almost certainly be "no measurable effect at this sample size."

Stating that in advance is what stops a noisy positive delta from being reported as a finding.

### 5.5 Plateau requirement (parameter sensitivity)

For every TUNABLE parameter, plot the metric across the grid. Acceptance:

```
The chosen value must sit inside a plateau: performance at v−1 step and v+1 step must each be
≥ 70% of the performance at v, and the plateau must contain ≥ 3 adjacent grid points that all
beat the no-filter baseline.
```

A single peak surrounded by poor values is **flagged OVERFIT** and the parameter reverts to its
FROZEN default. This is the operational form of the brief's rule that a strategy which only
works at very specific parameters must be flagged.

### 5.6 Multiple-testing correction

With `M` configurations evaluated (declared in the pre-registration, §2 of `PARAMETERS.md`
computes `M = 6,912`):

- **Deflated Sharpe Ratio** (Bailey & López de Prado) using `M`, the trade-return skew and
  kurtosis, and the sample length. Reported next to every raw Sharpe.
- **Benjamini–Hochberg** at `q = 0.10` across all subgroup and ablation p-values.
- The **expected maximum Sharpe under the null** for `M` trials is computed and printed at the
  top of every optimisation report, so the best in-sample result is always read against what
  pure noise would have produced with the same number of tries. On a 4-year sample with
  M ≈ 7,000, that number is not small, and seeing it first changes how the winner is read.

---

## §6. The falsification suite

The most informative runs in the project. All are executed **in-sample**; they cost no OOS
budget.

### 6.1 Sweep information content (tests H2)

Forward returns at +1/+3/+6/+12 confirmation-TF bars after every `SWEEP_CONFIRMED`, against a
matched control sample of bars with the same session, symbol and ATR-percentile but no sweep.
Report the effect size with CI. **No difference falsifies the strategy's premise**, regardless
of what the full system's equity curve looks like.

### 6.2 CHoCH vs MSS (tests H5)

Three populations: all CHoCH; MSS; CHoCH-not-MSS. Compare forward returns and, where a
hypothetical trade can be constructed, R-expectancy. If MSS and CHoCH-not-MSS are
indistinguishable, the sweep-plus-displacement requirement is decoration.

### 6.3 Shuffled-liquidity control (tests H3)

Re-run the entire strategy with real liquidity levels replaced by synthetic levels drawn to
match the real distribution of (distance from price, age, count per day) but placed at random
prices. Everything downstream — sweep detection, CHoCH, displacement, entries, risk — is
unchanged.

If the shuffled version performs as well, then **liquidity identification contributes nothing**
and whatever edge exists is in the reversal-after-extension machinery. That is a legitimate,
useful finding: it means the system should be rebuilt as a mean-reversion model and the SMC
framing dropped. Run with 20 random seeds and report the distribution, not one draw.

### 6.4 Sequence-scramble controls (tests H4)

| Control | Construction |
|---|---|
| **Sweep-only** | Enter on sweep confirmation, no CHoCH requirement. Same SL/TP |
| **CHoCH-only** | Enter on every MSS-shaped structure break with displacement, no prior sweep required |
| **Reversed order** | Require CHoCH *then* a sweep (a sequence that should be meaningless) |
| **Random-time** | Enter at random times matched to the real trade distribution over session and volatility, with the same SL/TP geometry |

The brief's central claim is that the *sequence* `liquidity → sweep → CHoCH` is what works. If
sweep-only or CHoCH-only matches the full model, the sequence adds nothing and the extra
machinery is only reducing sample size. The random-time control establishes the floor: the
level any SL/TP geometry achieves on this data with no signal at all.

### 6.5 Ablation matrix

One component toggled at a time against the baseline, each reported as a delta with a block-
bootstrap CI: MTF gate · counter-monthly rule · each sweep filter · displacement requirement ·
FVG requirement · session filter · killzone filter · break-even · trailing · time stop ·
weekend exit · each entry model · each SL model · each TP model · each OB definition ·
`reference_mode` · tier→confirmation-TF mapping (**H4-only baseline vs the `t3: H1` tier map** — the D-002 counterfactual) · `day_boundary` (**UTC baseline vs the NY anchor** — the D-001 counterfactual) · `tf.sunday_handling`.

**A component whose delta CI spans zero is reported as "no measurable effect" and its default
stands.** It is not kept because it looked slightly positive, and not removed because it looked
slightly negative — both are noise-chasing in opposite directions.

---

## §7. The out-of-sample budget ledger

The mechanism that makes the rest of this document enforceable rather than aspirational.

```
OOS_BUDGET = 10 evaluations for the entire project.
```

- Every OOS run is appended to `oos_evaluations.log` (append-only, committed, timestamped)
  **before** it executes: `run_id`, `config_hash`, the reason, and the pre-committed
  expectation.
- The holdout split gets **exactly one** evaluation, at the end.
- When the budget is exhausted, the project reports whatever the OOS results were. Additional
  OOS runs are permitted only after a new pre-registration that explicitly declares the
  previous OOS period as now in-sample — which is the honest accounting for what it is.

Rationale: out-of-sample data stops being out-of-sample the moment it influences a decision.
Ten looks is already enough to overfit if each look is followed by an adjustment; the ledger
makes each look visible and countable, which is what discipline actually requires.

---

## §8. Walk-forward analysis

| Setting | Value |
|---|---|
| Mode | Both **rolling** (fixed window) and **anchored** (expanding) |
| IS window | 24 months |
| OOS window | 6 months |
| Step | 6 months |
| Windows | ~8 over 2019–2024 |
| Optimised in each window | The 8 TUNABLE parameters only |

Reported per window: chosen parameters, IS metric, OOS metric, IS/OOS degradation ratio.

Acceptance:

```
Walk-forward efficiency  = mean(OOS expectancy) / mean(IS expectancy)   ≥ 0.50
Profitable OOS windows                                                  ≥ 60%
Parameter stability: chosen value moves ≤ 1 grid step between adjacent windows for ≥ 70%
                     of (parameter, window) pairs
```

**The parameter-stability criterion is the most diagnostic of the three.** A strategy whose
optimal `disp.min_leg_atr` jumps from 1.0 to 2.5 and back between adjacent windows has no
stable optimum; the optimiser is fitting noise, and its average OOS performance is luck
regardless of level.

---

## §9. Monte Carlo

| Test | Method | Acceptance |
|---|---|---|
| Trade-order shuffle | 10,000 permutations of the realised trade sequence | 95th-percentile max drawdown within the risk tolerance; ruin probability at `risk.pct_per_trade` < 1% |
| Bootstrap resample | 10,000 resamples with replacement | 5th-percentile expectancy > 0 |
| Randomised costs | Spread and slippage drawn from measured distributions | Median expectancy > 0 |
| Randomised execution | Skip 10% of trades at random, 1,000 runs | 5th-percentile net return > 0 — tests whether the result depends on a handful of trades |
| Random entry timing | Entry shifted ±1 bar | Expectancy degradation < 40% |

The skip-10% test deserves emphasis: a strategy whose entire profit comes from three trades
will fail it, and no other test in this suite reliably catches that.

**Its stated acceptance does not always reach that target, and needs a companion (D-015 §4).** "5th-percentile net return > 0" is a **sign** test while concentration is a **drop**: on a sequence of 57 losers and 3 large winners, dropping 10% of 60 trades rarely removes all three and the sign survives. Report alongside it the **share of total R held by the best k trades** (above 1.0 means the rest of the book loses money) and the **degradation** of the skip test's own 5th percentile against the unskipped return. Both are undefined on a losing book and must return no verdict there rather than a negative share that trivially passes.

---

## §10. Acceptance criteria (pre-registered — fill in before the first run)

### 10.1 Go/no-go for live trading

**All** must hold on out-of-sample data at `cost.multiplier = 1.5`:

| Criterion | Threshold |
|---|---|
| Trades (OOS) | ≥ 200 |
| Expectancy | ≥ **+0.10 R**, with 5th-percentile bootstrap bound > 0 |
| Profit factor | ≥ 1.20 |
| Max drawdown | ≤ 20% of equity at `risk.pct_per_trade` |
| Walk-forward efficiency | ≥ 0.50 |
| Profitable OOS windows | ≥ 60% |
| Cross-sectional | ≥ 6 of 10 symbols with positive expectancy, same parameters |
| Deflated Sharpe | > 0 at `M` = declared configuration count |
| Falsification suite | Full model beats **every** control in §6.3 and §6.4 by a margin whose CI excludes zero |
| Plateau | Every TUNABLE parameter sits in a plateau (§5.5) |
| Paper trading | ≥ 60 days, ≥ 95% entry-signal agreement with a same-period backtest |

The last falsification row is the one that matters most and is the one most likely to fail. A
strategy that beats a null model but not a sweep-only control has not demonstrated the thing it
claims to demonstrate — it has demonstrated that *some* part of it works.

### 10.2 If the criteria are not met

Prohibited: widening the TUNABLE grid; promoting FROZEN parameters; extending the OOS budget;
"just trying" a filter that was not pre-registered; reporting the best in-sample configuration
as the result.

Required — the deliverable in the failure case, which brief §33 asks for explicitly:

1. **Which component fails**, located by the funnel (§4.3) and the falsification suite (§6).
2. **Why**, with the measured evidence: e.g. "confirmed sweeps show no forward edge on FX
   majors at H4 (effect size 0.01R, CI [−0.04, +0.06], n = 4,120)".
3. **Which assumptions were unsupported**, mapped to the specification section that made them.
4. **What to test next**, as a new pre-registration — for example: a lower confirmation
   timeframe throughout; session liquidity only; a mean-reversion reformulation if §6.3 shows
   liquidity identification adds nothing; or a different asset class where session structure is
   sharper (index futures).

A negative result documented to this standard is a genuine, valuable outcome of the project. A
positive result obtained by breaking §10.2 is worth less than nothing, because it will be
traded.
