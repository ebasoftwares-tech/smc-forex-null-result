# Parameter Registry — SMC Bot v1.0

Companion to `SMC_STRATEGY_SPECIFICATION_v1.0.md`.

---

## §1. The classification, and why it is part of the specification

This system has ~140 parameters. Optimised jointly, that is an unbounded search space and any
resulting equity curve is a description of the past, not a prediction. The defence is not
discipline; it is classification, fixed in advance:

| Class | Count | Rule |
|---|---|---|
| **FROZEN** | 96 | Set by a documented reasoning decision. **Never optimised, never swept.** Changing one produces a different, unregistered strategy whose results must be reported as such |
| **ABLATION** | 36 | Toggled **one at a time** against the baseline to measure that component's contribution. On/off or a small enumerated set. Never optimised jointly, never selected by best result |
| **TUNABLE** | **8** | May be optimised, but only in-sample, only over the declared grid, and only under the plateau requirement and the multiple-testing correction of `BACKTEST_PROTOCOL.md` §5 |

**The distinction between ABLATION and TUNABLE matters.** An ablation asks "does this component
help?" and its answer is reported whatever it is. A tune asks "what value is best?" and every
such question consumes statistical power. Eight is already generous: with 8 parameters over the
declared grid the configuration count is 6,912, which is why the OOS budget ledger
(`BACKTEST_PROTOCOL.md` §7) exists.

**Total in-sample configuration count MUST be declared before optimisation begins** and carried
into the deflated Sharpe calculation. A parameter promoted from FROZEN to TUNABLE mid-project
invalidates every prior significance claim.

---

## §2. The 8 TUNABLE parameters

These, and only these, may be optimised.

| Parameter | Default | Grid | Spec | Why this one |
|---|---|---|---|---|
| `sweep.max_penetration_atr` | 1.00 | 0.5, 0.75, 1.0, 1.5, 2.0 | §9.2 | The boundary between "sweep" and "breakout". The strategy's most consequential single number |
| `sweep.max_confirmation_bars` | 3 | 1, 2, 3, 5 | §9.2 | How long a reclaim may take. Interacts with wick/close filters — must be swept jointly with them, never one at a time |
| `disp.min_leg_atr` | 1.5 | 1.0, 1.25, 1.5, 2.0, 2.5 | §10.1 | How much movement counts as displacement |
| `choch.max_bars_after_sweep` | 12 | 4, 8, 12, 18, 24 | §11.4 | The patience window between sweep and MSS |
| `entry.pending_expiry_bars` | 6 | 3, 6, 9, 12 | §15.1 | How long a limit order waits. Directly trades fill rate against entry quality |
| `tp.r_multiple` | 2.0 | 1.5, 2.0, 2.5, 3.0 | §17.1 | Required by the brief's §18 |
| `risk.pct_per_trade` | 0.35% | 0.10–0.50% | §18.3 | Bounded by the brief. Scales the equity curve, not the edge — reported, never "optimised" for return |
| `bias.min_score` | 2 | 0, 1, 2, 3, 4 | §7.5 | How much MTF agreement is required. `0` is equivalent to the `none` control |

Grid size: 5 × 4 × 5 × 5 × 4 × 4 × (risk excluded from the grid — it is a scaling choice, not an
edge parameter) × 5 = **8,000** configurations at most; **6,912** after removing dominated
combinations. Every one of them is a test, and the correction in `BACKTEST_PROTOCOL.md` §5.6
uses that number.

---

## §3. Full registry

Classification key: **F** = FROZEN, **A** = ABLATION, **T** = TUNABLE.

### 3.1 Data and timeframes (§1, §2)

| Parameter | Default | Class | Notes |
|---|---|---|---|
| `data.ingest_timeframe` | M1 | F | M5 acceptable; H1 degrades sessions (§3.6) |
| `data.max_gap_bars` | 3 | F | Above this → `DATA_SUSPECT` |
| `data.spike_filter_atr` | 10.0 | F | Single-bar range above this is quarantined for review, never auto-corrected |
| `tf.day_boundary` | **`UTC 00:00`** | **A** | **D-001.** Alternative `America/New_York 00:00`. Changes every H4 bar; two full parallel runs, not a sweep |
| `tf.sunday_handling` | `merge_into_monday` | **A** | D-001 §2.6.1. Alternative `standalone_incomplete` |
| `tf.stub_merge_threshold` | 0.25 | F | Coverage below which the week's first D1 bucket merges forward |
| `tf.min_bar_coverage_warn` | 0.50 | F | Below this a bar is tagged `low_coverage` and reported separately |
| `week.open_utc` | Sun 21:00 | F | Verified against data at ingest |
| `week.close_utc` | Fri 21:00 | F | |
| `atr.period` | 14 | F | Wilder. `ATR_ref(i) = ATR(i−1)` is a rule, not a parameter |
| `symbols` | 10 majors/crosses | F | Cross-sectional robustness needs a fixed universe |

### 3.2 Sessions (§3)

| Parameter | Default | Class | Notes |
|---|---|---|---|
| `session.asia` | Asia/Tokyo 09:00–18:00 | F | |
| `session.london` | Europe/London 08:00–16:30 | F | |
| `session.new_york` | America/New_York 08:00–17:00 | F | |
| `session.asia_range` | America/New_York 20:00–00:00 | F | Liquidity only |
| `session.london_kz` | America/New_York 02:00–05:00 | **A** | Killzone filter on/off |
| `session.ny_kz` | America/New_York 07:00–10:00 | **A** | |
| `session.source_tf` | M15 | F | H1 only in degraded mode |
| `session.min_bar_coverage` | 0.60 | F | Below → INCOMPLETE |
| `session_liquidity.use_running_extreme` | false | **A** | true permits intra-session sweeps (§3.5) |
| `filter.allowed_execution_sessions` | all | **A** | Per-session on/off; required by brief §20 |

### 3.3 Swings (§5)

| Parameter | Default | Class |
|---|---|---|
| `swing.fractal_n.MN1 / W1 / D1 / H4 / H1 / M15` | 1 / 1 / 2 / 2 / 3 / 5 | **A** (H4 only: {1,2,3,4}) |
| `swing.tie_rule` | leftmost | F |
| `swing.price_source` | wick | **A** (alt: body) |
| `choch.micro_fractal_n` | 1 | **A** |

### 3.4 Structure (§6)

| Parameter | Default | Class | Notes |
|---|---|---|---|
| `structure.break_confirmation` | close | F | `wick` available for ablation, expected to be much worse |
| `structure.min_break_penetration_atr` | 0.00 | **A** | {0, 0.05, 0.10, 0.15}. Registered in v1.0 as `break.min_penetration_atr`; renamed because `break` is a Python keyword and cannot be a config group |
| `structure.on_wick_below_protected` | keep | F | `reset` destroys the CHoCH signal |
| `structure.min_bars_between_flips` | 2 | F | Whipsaw guard |
| `structure.protected_on_bos` | most_recent_low | **A** | **D-005.** `ratchet_only` is the alternative. Resolves the SPEC 6.4 / 6.9 contradiction; changes how far price must travel to produce a CHoCH |
| `swing.min_history` | 36/104/250/500/1000/2000 | F | SPEC 5.3. Reported, not enforced |

### 3.5 Bias (§7)

| Parameter | Default | Class | Notes |
|---|---|---|---|
| `bias.method` | structure | **A** | alts: premium_discount, close_vs_open, ema |
| `bias.max_event_age.MN1/W1/D1/H4` | 6 / 8 / 10 / 20 | F | |
| `bias.weights` | 1,1,1,1 | F | Equal by deliberate refusal to invent a weighting |
| `bias.gate_mode` | score | **A** | none / htf_only / daily_h4 / score / strict. **`none` MUST be run** |
| `bias.min_score` | 2 | **T** | |
| `bias.counter_monthly_action` | block | **A** | block / derisk / allow |
| `bias.equilibrium_band` | 0.05 | F | premium_discount method only |
| `bias.ema_period` | 20 | F | ema method only |

### 3.6 Liquidity (§8)

| Parameter | Default | Class | Notes |
|---|---|---|---|
| `liq.enable_source.*` | 8 of 9 on; `RANGE_*` off | **A** | One switch per source (§8.3) |
| `eq.min_touches` | 2 | **A** | {2, 3} |
| `eq.tolerance_atr` | 0.10 | **A** | {0.05, 0.10, 0.20} |
| `eq.min_separation_bars` | 3 | F | |
| `eq.max_span_bars` | 50 | F | |
| `eq.cluster_price` | extreme | F | mean under-reports the sweep requirement |
| `range.window_bars` / `max_height_atr` / `max_breakout_bars` | 20 / 2.0 / 3 | F | Source disabled by default |
| `liq.merge_tolerance_atr` | 0.10 | F | |
| `liq.max_distance_atr` | 5.0 | **A** | In-play filter |
| `liq.invalidate_closes` | 2 | F | |
| `liq.invalidate_buffer_atr` | 0.25 | F | |
| `liq.max_age_bars.tier1/2/3` | 90 / 30 / 5 (D1) | F | |
| `liq.max_active_levels` | 40 | F | |
| `liq.rank_weights` | tier 3/2/1, strength 0.5, recency 1.0, bias 1.0 | F | Ordering only; tuning an ordering function is efficient overfitting |
| `liq.tier_confirmation_tf` | **t1 H4, t2 H4, t3 H4** | **A** | **D-002.** The `t3 H1` tier map is now the primary ablation. Tier still governs ranking and expiry |

### 3.7 Sweeps (§9)

| Parameter | Default | Class |
|---|---|---|
| `sweep.max_confirmation_bars` | 3 | **T** |
| `sweep.min_penetration_atr` | 0.05 | F |
| `sweep.max_penetration_atr` | 1.00 | **T** |
| `sweep.reclaim_buffer_atr` | 0.00 | **A** {0, 0.05, 0.10} |
| `sweep.min_wick_ratio` | 0.00 | **A** {0, 0.3, 0.5} |
| `sweep.min_close_position` | 0.00 | **A** {0, 0.5, 0.66} |
| `sweep.require_prior_level_age_bars` | 3 | F — **swing-derived sources only**, §9.2.1 |
| `sweep.same_bar_choch_allowed` | false | F — the "WAIT" is structural |

### 3.8 Displacement (§10)

| Parameter | Default | Class |
|---|---|---|
| `disp.mode` | leg | **A** (leg / bar / either) |
| `disp.min_leg_atr` | 1.5 | **T** |
| `disp.min_body_ratio` | 0.50 | **A** {0.4, 0.5, 0.6} |
| `disp.min_directional_bars` | 1 | F |
| `disp.max_leg_bars` | 3 | **A** {2, 3, 5} |
| `disp.require_fvg` | true | **A** |
| `disp.min_range_atr` | 1.5 | F (bar mode only) |

### 3.9 CHoCH / MSS (§11)

| Parameter | Default | Class |
|---|---|---|
| `choch.reference_mode` | major | **A** (major / micro — two separate pre-registered strategies) |
| `choch.max_reference_lookback` | 30 | F |
| `choch.max_reference_distance_atr` | 3.0 | **A** {2.0, 3.0, 4.0} |
| `choch.max_bars_after_sweep` | 12 | **T** |
| `choch.min_bars_after_sweep` | 1 | F |
| `invalidate.new_extreme_atr` | 0.10 | F |
| `execution.confirmation_timeframe_override` | auto (= H4 everywhere under D-002) | **A** (auto / H4 / H1 / M15) |

### 3.10 FVG (§12)

| Parameter | Default | Class |
|---|---|---|
| `fvg.min_size_atr` | 0.10 | **A** {0.05, 0.10, 0.20} |
| `fvg.min_size_pips` | 0.5 | F |
| `fvg.mitigation_mode` | ce | **A** (touch / ce / full) |
| `fvg.invalidate_buffer_atr` | 0.00 | F |
| `fvg.max_age_bars` | 30 | F |
| `fvg.exclude_weekend_gaps` | true | F |
| `fvg.merge_overlapping` | false | **A** |
| `fvg.selection` | first | **A** (first / largest / nearest) |

### 3.11 Order Blocks (§13)

| Parameter | Default | Class |
|---|---|---|
| `ob.definition` | last_opposing (OB-A) | **A** (A / B / C / D — four pre-registered variants) |
| `ob.zone_mode` | full_range | **A** (full_range / body / wick_to_open) |
| `ob.max_lookback_bars` | 10 | F |
| `ob.max_distance_atr` | 3.0 | F |
| `ob.max_age_bars` | 30 | F |
| `ob.invalidate_closes` | 1 | F |

### 3.12 Setup and entry (§14, §15)

| Parameter | Default | Class |
|---|---|---|
| `setup.max_active_per_symbol` | 2 | F |
| `setup.max_active_per_direction` | 1 | F |
| `setup.max_armed_orders` | 1 | F |
| `entry.model` | C | **A** (A–E, five pre-registered variants) |
| `entry.retrace_pct` | 0.50 | **A** {0.382, 0.5, 0.618} — model B only |
| `entry.fvg_entry_point` | ce | **A** (proximal / ce / distal) |
| `entry.ob_entry_point` | proximal | **A** (proximal / ce / distal) |
| `entry.pending_expiry_bars` | 6 | **T** |
| `entry.cancel_on_bias_flip` | true | **A** |
| `entry.fallback_model` | none | F — a fallback chain makes per-model statistics uninterpretable |

### 3.13 Stops (§16)

| Parameter | Default | Class |
|---|---|---|
| `sl.model` | sweep_extreme (S1) | **A** (S1–S4) |
| `sl.buffer_atr` | 0.10 | **A** {0.05, 0.10, 0.20} |
| `sl.buffer_spread_mult` | 2.0 | F |
| `sl.atr_multiple` | 1.5 | F (S4 only) |
| `risk.max_sl_atr` | 2.5 | F |
| `risk.max_sl_pips` | 60 / 90 JPY | F |
| `risk.min_sl_pips` | 8 / 12 JPY | F |

### 3.14 Targets and management (§17)

| Parameter | Default | Class |
|---|---|---|
| `tp.model` | fixed_r (T1) | **A** (T1–T4) |
| `tp.r_multiple` | 2.0 | **T** |
| `tp.min_rr` | 1.5 | **A** {1.0, 1.5, 2.0} |
| `tp.below_min_rr_action` | skip | F |
| `tp.min_target_rank` | 2.0 | F (T2 only) |
| `tp.target_buffer_atr` | 0.15 | F |
| `tp.ladder` | 50%@1R, 25%@2R, 25%@liq | F (T3 only) |
| `manage.be_trigger_r` | 0.0 (off) | **A** {off, 1.0, 1.5} |
| `manage.be_offset_atr` | 0.05 | F |
| `manage.trail_mode` | none | **A** (none / structure / atr) |
| `manage.trail_atr_mult` | 2.0 | F |
| `manage.trail_start_r` | 1.0 | F |
| `exit.max_bars_in_trade` | 30 | **A** {15, 30, 60, off} |
| `exit.close_before_weekend` | true | **A** |
| `exit.weekend_close_utc` | Fri 19:00 | F |
| `exit.close_before_high_impact_news` | false | F — prohibited until reproducible in backtest (§17.4) |

### 3.15 Risk (§18)

| Parameter | Default | Class |
|---|---|---|
| `risk.pct_per_trade` | 0.35% | **T** (bounded 0.10–0.50) |
| `risk.counter_monthly_multiplier` | 0.5 | F |
| `risk.max_total_open_risk_pct` | 1.5% | F |
| `risk.max_daily_loss_pct` | 2.0% | F |
| `risk.max_weekly_loss_pct` | 4.0% | F |
| `risk.max_monthly_loss_pct` | 8.0% | F |
| `risk.max_consecutive_losses` | 5 | F |
| `risk.consecutive_loss_pause_hours` | 24 | F |
| `risk.max_open_positions` | 3 | F |
| `risk.max_positions_per_symbol` | 1 | F |
| `risk.max_correlated_positions` | 2 | F |
| `risk.correlation_threshold` | 0.70 | F |
| `risk.correlation_window_days` | 60 | F |
| `risk.max_spread_pips` | 2.0 / 3.5 JPY | F |
| `risk.max_spread_pct_of_sl` | 10% | F |
| `risk.equity_dd_kill_pct` | 10% | F |
| `risk.dd_ladder` | 5/8/10% → ×0.75/0.50/kill | F — **monotone non-increasing, asserted by test** |
| `risk.min_realised_fraction` | 0.5 | F |

### 3.16 Execution and costs (§26)

| Parameter | Default | Class |
|---|---|---|
| `exec.latency_ms` | 250 | F |
| `slip.entry_pips` / `entry_atr_mult` | 0.2 / 0.02 | F |
| `slip.stop_pips` / `stop_atr_mult` | 0.5 / 0.05 | F |
| `cost.commission_per_lot_per_side` | 3.5 | F (account-specific) |
| `cost.spread_model` | measured | F (fallback: session constants) |
| `cost.multiplier` | 1.0 | **A** {1.0, 1.5, 2.0} — the mandatory cost-sensitivity run |
| `backtest.intrabar_mode` | m1_path | F (`pessimistic` when M1 absent; `ohlc_heuristic` prohibited) |
| `backtest.limit_fill_buffer_pips` | 0.2 | F |
| `ops.max_data_staleness_sec` | 300 | F |
| `ops.max_broker_errors` | 5/hour | F |
| `ops.order_retries` | 2 | F |
| `watchdog.timeout_sec` | 300 | F |
| `watchdog.on_timeout` | cancel | F |
| `watchdog.emergency_sl_atr` | 3.0 | F |
| `analysis.forward_bars` | 12 | F (rejection-log counterfactual) |

---

## §4. Decisions applied

| Id | Date | Parameter(s) | Value | Source |
|---|---|---|---|---|
| **D-001** | 2026-08-25 | `tf.day_boundary` | `UTC 00:00` (was `America/New_York 00:00`) | Q3 |
| **D-001a** | 2026-08-25 | `tf.sunday_handling`, `tf.stub_merge_threshold`, `tf.min_bar_coverage_warn` | new, added to fix the stub-bar defect D-001 exposed | §2.6 |
| **D-002** | 2026-08-25 | `liq.tier_confirmation_tf` | `{1:H4, 2:H4, 3:H4}` (was `3:H1`) | Q7 |
| **D-002a** | 2026-08-25 | `sweep.require_prior_level_age_bars` | domain narrowed to swing-derived sources; value unchanged at 3 | §9.2.1 |

D-001a and D-002a are **corrections**, not preferences: each fixes a rule that was wrong as
written. Neither changes a tunable value, and neither alters the TUNABLE count, which remains 8
and therefore leaves `M = 6,912` intact for the multiple-testing correction.

## §5. Change control

1. A parameter's **class** may not change during a study. Promoting FROZEN → TUNABLE
   invalidates every prior significance claim in that study.
2. Changing a **FROZEN default** starts a new study with a new pre-registration.
3. Every result carries its `config_hash`; a report without one is not a result.
4. `defaults.yaml` is version-controlled and reviewed. The current defaults were set by the
   reasoning recorded in the specification sections cited above — not by a backtest, and not
   by a preference.
