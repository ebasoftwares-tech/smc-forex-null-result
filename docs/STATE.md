# Project State — pick-up point for a new session

Last updated: 2026-08-28, after the falsification suite.

This is the orientation document. It says where the project is, what is decided, what is
deliberately not built yet, and what to do next. It does **not** repeat the specification
(`SMC_STRATEGY_SPECIFICATION_v1.0.md`) or the reasoning behind each finding
(`DECISIONS.md`) — read those for detail.

---

## 1. What this project is

A systematic SMC (Smart Money Concepts) Forex bot, built **specification-first**. The
objective is not a profitable bot; it is a defensible answer to:

> Does the sequence *liquidity → sweep → CHoCH/MSS → displacement → entry* produce a
> positive expectancy on FX majors that survives out-of-sample testing, transaction
> costs and multiple-testing correction — and if not, which link fails?

`BACKTEST_PROTOCOL.md` §10.2 forbids tuning until it passes. **A documented null result
is an accepted deliverable.** This has been agreed explicitly (D-003, Q17).

---

## 2. Current status

| | |
|---|---|
| **Phases complete** | 1, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14 |
| **Studies complete** | H5 (SPEC 6.9), **the falsification suite (protocol 6.3/6.4)** |
| **Tests** | 608, all passing |
| **Commits** | 14, on `master` |
| **Python** | 3.14 in `.venv`; deps pinned in `requirements.txt` |

```bash
.venv/Scripts/python.exe -m pytest tests/          # 608 tests, ~75s
.venv/Scripts/python.exe scripts/phase9_report.py  # the Phase 9 funnel gate
.venv/Scripts/python.exe scripts/phase12_report.py  # the Phase 12 entry engine
.venv/Scripts/python.exe scripts/phase13_report.py  # the Phase 13 risk layer
.venv/Scripts/python.exe scripts/phase14_report.py  # the Phase 14 backtest engine
.venv/Scripts/python.exe scripts/marginal_value_report.py  # the H5 study
.venv/Scripts/python.exe scripts/falsification_report.py   # protocol 6.3/6.4, ~11 min
```

### Phases built

| Phase | Scope | Gate report | Result |
|---|---|---|---|
| 1 | Data ingest, UTC timeframe construction, session engine | `reports/phase1_gate.md` | 11/11; broker reconciliation **BLOCKED** on Q1/Q2 |
| 5 | H4 swing detection + market structure (BOS/CHoCH) | `reports/phase5_gate.md` | 7/7 |
| 6 | Liquidity engine — sources, lifecycle, merge, rank | `reports/phase6_gate.md` | 8/8; sweep-rate half deferred to 7 |
| 7 | Sweep detection + forward-return study (H2) | `reports/phase7_gate.md` | 7/7; closed Phase 6's deferred half |
| 8 | Displacement + FVG detection | `reports/phase8_gate.md` | 8/8 |
| 9 | CHoCH reference selection, MSS confirmation, **the funnel** | `reports/phase9_gate.md` | 10/10 — but see §3, the gate passes on a *projection* |
| — | **H5 study**: MSS vs CHoCH-not-MSS (SPEC 6.9, run out of order) | `reports/marginal_value.md` | 8/8 — instrument validated, **H5 open** |
| 10 | FVG lifecycle, selection, standalone edge test | `reports/phase10_gate.md` | 10/10 — two spec corrections, see D-011 |
| 11 | Order Block bake-off — four definitions, agreement matrix | `reports/phase11_gate.md` | 10/10 — **four variants are worth 1.77 tests**, see D-012 |
| 12 | Entry engine — five models, fill resolution against M1 | `reports/phase12_gate.md` | 8/8 — a "conservative" fill default that was neither, see D-013 |
| 13 | Risk — stops S1–S4, the RR gate, sizing, limits | `reports/phase13_gate.md` | 8/8 — **four defaults that cannot fire**, see D-014 |
| 14 | Backtest engine — exits, costs, metrics, Monte Carlo | `reports/phase14_gate.md` | 10/10 — **four free lunches found and closed**, see D-015 |
| — | **The falsification suite**: shuffled liquidity, sweep-only, CHoCH-only, reversed-order, random-time (protocol 6.3/6.4) | `reports/falsification.md` | Built and validated — **and section 10.1's own acceptance row turns out to be satisfiable by stop width**, see D-016 |

**Phase 5 was built before 2–4 deliberately**: Monthly/Weekly/Daily analysis is the *same*
engine instantiated on other bar series (SPEC 7.1), so building it once at H4 makes 2–4
mostly configuration.

### Not started

Phases 2–4 (Monthly/Weekly/Daily bias), 15–17 (charts, paper, live).

**The falsification suite is now built** (§3d). What is *not* built from
`BACKTEST_PROTOCOL.md` §6 is **§6.5's ablation matrix**, which is the natural next piece.
Every arm's result is still meaningless on a fixture whose true effect is zero by
construction — but building it was not, which is the point of §3d.

---

## 3. The Phase 9 result, which is the project's decision point

Everything before Phase 9 produced candidates. Phase 9 asks the question the design rests
on — how many tradable events does the sequence actually produce — and the answer is:

| Mode | Sweep→MSS | MSS / symbol-year | Projected universe (4y × 10) | Projected dev set (4y × 3) | Gate |
|---|---:|---:|---:|---:|---|
| `major` | **1.98%** | 12.7 | 507 | 152 | **PASS** (≥300 / ≥120) |
| `micro` | 0.16% | 1.0 | 40 | 12 | **FAIL** |

Four things to carry forward, and the first is the most important:

1. **The gate passes on a projection, not a measurement.** No real bar has been ingested
   (Q1/Q2 still open). What is measured is the *conversion rate*; what is reported is that
   rate scaled to the stated universe. Recorded as **PASS on projection, BLOCKED on
   measurement**.
2. **1.98% is the number SPEC 11.7 named in advance** as the level at which the funnel
   becomes a design finding. The design clears its gate by sitting exactly on the line the
   specification drew, which is why the sensitivity tables are in the gate report.
3. **`micro` is a pre-registered null.** It is a separate strategy (SPEC 11.1), not a
   parameter that came out badly, and §10.2 forbids tuning it until it passes. Its
   displacement filter rejects 709 of 780 CHoCH events — small breaks close to the sweep
   extreme almost never clear 1.5 ATR.
4. **The gate is not robust to `choch.max_reference_distance_atr`**, an ABLATION parameter:
   2.0 fails the dev-set half, 3.0 and 4.0 pass. The default stays fixed. Knowing the PASS
   is conditional is the licensed conclusion; moving the parameter is not.

---

## 3a. The H5 study, and the number that outlives it

`reports/marginal_value.md`. **H5 — "displacement filtering adds value" — is open**, and
on a random walk it can only be: the true MSS vs CHoCH-not-MSS difference is zero by
construction, so a `DIFFERENT` verdict here would mean the study is broken.

What the run does establish is a planning fact that transfers to real data, because it is
a property of the return distribution and the funnel's output rate rather than of the
fixture's realism:

| Horizon | MSS needed to resolve +/-0.25 ATR | Dev set projects 128 | Universe projects 427 |
|---:|---:|:--:|:--:|
| +1 | 58 | yes | yes |
| +4 | 222 | **no** | yes |
| +12 | 804 | **no** | **no** |

**At the 12-bar horizon the full in-sample universe is not enough**, whatever the backtest
shows. Decide which way out to take *now* rather than after Phase 14 — answer H5 at the
short horizons only, widen the margin (a defensible decision in advance and an
indefensible reaction later), or leave H5 to §6.5's ablation delta. See D-010 §4.

Two things about the study itself that are easy to undo:

- **The verdict is three-way.** `UNDERPOWERED` is not `EQUIVALENT`. Only `EQUIVALENT` —
  the CI sitting *inside* the margin — licenses "the requirement is decoration". A
  two-way verdict would have reported this fixture as a null result on the methodology's
  central claim.
- **The equivalence margin is declared, not derived**, and fixed before any result was
  read. Every row carries its own MDE so a different margin needs no re-run.

---

## 3b. Phase 13, and the four defaults that cannot fire

The gate — *"every limit exercised by scenario; sizing purity test passes"* — is met:
18/18 scenarios fire on their trigger and decline on their near miss, and sizing is a pure
function of `(equity, risk_pct, sl_distance)` plus the lot grid, asserted on the signature
rather than on behaviour.

**Exercising every limit is what produced the phase's actual output.** Four defaults turn
out to be unreachable rather than merely unused, three of them arithmetic facts about pairs
of FROZEN parameters:

| | What | Why |
|---|---|---|
| 1 | `risk.min_realised_fraction` = 0.5 | Flooring to a lot grid cannot lose more than half, so the check can never fire. 0 fires in 400,000 sizings. It does not catch SPEC 18.2's own worked example, which lands at 0.52 |
| 2 | `risk.max_total_open_risk_pct` = 1.5% | 3 positions × the 0.50% band ceiling is *exactly* 1.5%, which does not breach 1.5%. `max_open_positions` always binds first |
| 3 | `risk.max_sl_atr` = 2.5, **under S4 only** | S4's stop is 1.5 ATR by construction. Its mirror: `max_sl_pips` rejects S4 outright above **40 pips of ATR** |
| 4 | T3 (`partial_ladder`) | Its `tp_1` is the 1R rung against `tp.min_rr` = 1.5, so §17.2 rejects it on every setup. Reachable at exactly one of the three ablation values |

**None was changed.** §10.2 forbids moving a parameter to make a result appear, and that
applies to a check as much as to a return. Each is pinned by a test asserting the
*unreachability*, so a future change that makes one live fails a test that says why.

A fifth, same species: **T4 is exempt from the RR gate** (no `tp_1` to measure), so it
accepts setups T1–T3 reject and SPEC 17.7's "paired T1–T4 variants on a shared setup
stream" does not describe four shared streams.

Three numbers worth carrying forward:

- **`M_eff` = 1.36 for the four stop models** (D-012's finding one layer down — S1 and S2
  produce the identical price on 58.8% of setups). Use it, not 4, in any S1–S4 correction.
- **The realised risk distribution is a spike with a lower tail only**: median 0.968 of
  nominal, maximum 0.9995, nothing above 1.0. That is SPEC 18.9's requirement met, and
  flooring rather than rounding is what meets it.
- **The smallest account that can size 95% of this stream is USD 2,000** — but the fixture's
  stops are narrow (median 23.6 pips, from a 17.4-pip median H4 ATR), so the report gives
  the number against a stop-scale factor rather than alone. The *shape* transfers; the row
  that applies depends on the real ATR distribution.

## 3c. Phase 14, and the four free lunches

The gate — *"full protocol; replay + shifted-data tests green; cost sensitivity run"* — is
met. What matters is what building it turned up: **four separate places where the engine
credited a price the trade could never have obtained.** They share a shape — a price that
was *planned* treated as a price that was *paid* — and it is evidently this layer's
characteristic bug.

| | What | Effect before the fix |
|---|---|---|
| 1 | **Shadow trades entered at limit prices price never reached.** A bullish limit sits below the market, so every shadow got a free discount | 38 take-profits against 2 stops, mean **+1.57R**, on a random walk |
| 2 | **The rejection log's forward return was measured from the planned entry, in R against the planned risk.** Both distort: the entry was never paid, and several gates reject a setup *because its risk was wrong* | median **+1.7R at 92%**; `SL_TOO_TIGHT` read **+7.0R** off a 0.37-pip denominator |
| 3 | **The exit walk skipped the entry bar**, so a trade that filled and stopped out inside one bar was carried to the next bar's open | **6% of trades** close on their entry bar; worth up to **0.04R per trade**, against a +0.10R go/no-go threshold |
| 4 | **Fill rate counted portfolio-admitted trades**, folding SPEC 18.4's position cap into the model bake-off — and the cap bites hardest on whichever model fills most | model A read **58%** against the 100% Phase 12 measured |

Three further findings that are not bugs:

- **The entry models do not arm on the same setups, and the difference has a direction.**
  Model A's stop distance *is* the displacement leg, so SPEC 16.3's 2.5-ATR cap rejects it
  on 99 of 165 setups — and the rejected ones have the **higher** median displacement (2.57
  ATR against 2.10). The cap removes its strongest setups specifically. `E_setup` does not
  repair this because it divides by each model's own count; `E_all_setups` (shared
  denominator) does. This is D-014 §6 recurring on the entry axis (D-015 §6).
- **`ENTRY_EXPIRED`'s rejection row is a tautology.** An order expires unfilled precisely
  when price never retraced — which for a bullish setup means it ran. The population selects
  for the move being measured. It reads +1.37 ATR and **must never be read as "the expiry
  rule destroys edge"** (D-015 §8).
- **Protocol §9's most-emphasised test does not always reach its own target.** Skip-10%'s
  acceptance is a *sign* test while concentration is a *drop*: 57 losers plus 3 large
  winners passes it. Two companions close the gap (D-015 §4).

And one recurrence worth its own line: **a redundant guard is an untested guard.** D-014 §8
recorded a clamp hidden by a validator; `manage_stop` had the identical problem one phase
later, with each branch clamping internally *and* a final clamp that no input could reach.
Fixed by restructuring so the branches propose and one clamp decides. 17 mutations were run;
6 survived the first pass, all 17 are caught now.

## 3d. The falsification suite, and the criterion it broke

`reports/falsification.md`. §10.1 has eleven go/no-go rows and says which one decides the
question:

> *"The last falsification row is the one that matters most and is the one most likely to
> fail. A strategy that beats a null model but not a sweep-only control has not
> demonstrated the thing it claims to demonstrate."*

All five arms are built — shuffled liquidity (H3), sweep-only, CHoCH-only, reversed-order
and random-time — and every one runs through the **unmodified** `run()`. Their *results*
are worthless on this fixture and always were going to be, for the reason D-015 gave when
it deferred them. **Building them was not.**

### The finding: §10.1's row can be cleared on stop width alone

On a random walk, where the true difference is zero by construction, the baseline beats
`sweep_only` by **+0.125 R/setup, CI [0.019, 0.229]** — excluding zero, so the row is
*satisfied*. That is not a bug. **R is a ratio and the arms do not share its denominator:**

| Arm | median SL (ATR) | net delta | gross delta | inflation |
|---|---:|---:|---:|---:|
| `shuffled_liquidity` | 2.27 | −0.041 | −0.045 | 0.004 |
| `sweep_only` | 0.96 | +0.125 | +0.018 | **0.107** |
| `choch_only` | 2.23 | −0.045 | −0.056 | 0.011 |
| `reversed_order` | 0.96 | +0.092 | −0.012 | **0.104** |
| `random_time` | 1.18 | +0.080 | +0.001 | **0.078** |

In **gross** R the baseline beats **0 of 5** and every CI contains zero, which is the
correct answer. In **net** R it beats 1 of 5. The whole gap is transaction cost: an arm
entering at the sweep stops just beyond an extreme a bar or two old, the baseline stops
beyond one up to twelve bars back, and a fixed spread against a stop half as wide is twice
the cost *per R*.

**Any control that enters earlier than the baseline is cost-inflated this way**, including
`random_time` — which matters most, because it is the floor. Both currencies are reported
and **no decision has been taken**: §10.2 forbids moving a criterion to make a result
appear, and that binds on a criterion as much as on a parameter. The three options are in
D-016 §1 and belong in the pre-registration, before real bars.

### Four more things to carry forward

1. **The suite cannot run at the configured default entry model.** At `entry.model = C`,
   `sweep_only` and `reversed_order` arm **zero** orders, 100% `NO_FVG_AVAILABLE`.
   Structural: both enter at the sweep confirmation, so their leg is **median 0 bars, max
   2**, and an FVG needs three. §10.1's row is undefined at the shipped default. The suite
   runs at **model A**, the only 100%-fill model.
2. **`choch_only` must not be built on `structure.py`'s CHoCH events** — a trend flip
   through the *protected* level is a stricter and different thing from SPEC 11.2's break
   of the *last unbroken swing*. It reuses `MssEngine._major_reference` itself. The two
   counts are too close for a size check to catch the error (26 vs 82 on one fixture year,
   43 vs 41 on another — larger on one, smaller on the other).
3. **Three guards nothing in the fixture reaches**, the same pattern D-014 §8 and D-015
   named. One is worth separating: `choch.max_reference_distance_atr` rejects **nothing**
   at its default of 3.0 (widest reference 2.81 ATR over 198 events) but **binds hard at
   2.0**. Unlike D-014's four unreachable defaults this is a *measurement*, not arithmetic
   — and it is the second place this same ABLATION parameter sits just past where the data
   reaches (§3, the Phase 9 gate).
4. **An end-to-end positive control does not exist and is hard.** The comparison layer has
   one and each arm's construction has one, but nothing shows a real conditional edge in
   the *price series* surviving the whole chain. Injecting drift after each MSS changes the
   prices, which changes the sweeps, which changes the MSS set.

---

## 4. Module map

```
bot/config/     schema.py (every parameter, with FROZEN/ABLATION/TUNABLE in its
                description), loader.py (layering + config_hash), defaults.yaml
bot/data/       calendar.py, resample.py, quality.py, ingest.py, synthetic.py
bot/core/       bars.py, indicators.py, sessions.py, swings.py, structure.py,
                liquidity.py, sweeps.py, displacement.py, fvg.py, mss.py,
                order_blocks.py, entries.py, stops.py, targets.py, risk.py,
                trade.py, ids.py, exits.py, costs.py
bot/backtest/   engine.py (two passes), metrics.py (protocol 4), montecarlo.py
                (protocol 9)
bot/research/   stats.py (shared primitives), sweep_study.py (H2),
                displacement_study.py (SPEC 10.6), funnel.py (SPEC 11.7),
                marginal_value.py (H5), fvg_study.py (SPEC 12.6),
                ob_study.py (SPEC 13.8), risk_study.py (SPEC 18.9),
                falsification.py (protocol 6.3/6.4 -- the five controls)
scripts/        build_dataset.py, phase{1,5,6,7,8,9,10,11,12,13,14}_report.py,
                marginal_value_report.py, falsification_report.py, regen_golden.py
tests/          608 tests + tests/golden/structure_h4.json
```

`bot/core/` is pure: no I/O, no clock, no broker. That is what makes the causality tests
cheap to run. `ARCHITECTURE.md` planned §16/§17/§18 for `strategy/` and `risk/`; they were
built in `core/` instead, following the precedent `entries.py` set, because all four
satisfy that same purity criterion (`RiskLedger` is stateful but takes its clock as a
parameter). The note is in `ARCHITECTURE.md` next to the map.

---

## 5. Decisions in force

Full reasoning in `DECISIONS.md`. The two that shape everything:

- **D-001 — day boundary is UTC 00:00.** H4 grid fixed at 00/04/08/12/16/20 UTC
  year-round. NY anchor is an ablation. Forced the Sunday stub-bar merge (D-001a).
- **D-002 — H4 confirmation for every liquidity tier.** Minimum **8 hours** from sweep to
  MSS. **This is a session-to-session swing model, not the intraday London reversal the
  SMC source material describes.** Never report it as the latter. *Qualified by D-009 §7:
  the window permits two days, but the measured median is 8 hours — the model is
  multi-session by permission and same-day in practice, at least against noise.*

D-004 through D-016 record corrections and findings from each phase's implementation.

---

## 6. Things a new session must not re-derive

Each of these cost real effort to find and is easy to undo by accident.

| # | |
|---|---|
| 1 | **`tzdata` is a hard dependency.** Windows has no system IANA database. Its version decides historical DST and is in the dataset manifest. |
| 2 | **`datetime.fromtimestamp` raises on negative epochs on Windows.** `bars.from_epoch_s` offsets from the epoch instead. |
| 3 | **The week open coincides exactly with the first bar after a weekend gap** — gap tests need `<=`, not `<`, or every weekend is flagged as a data defect. |
| 4 | **Resampling must be prefix-stable.** That is the no-repaint property; `tests/test_causality.py` asserts it across 60 truncations per timeframe. |
| 5 | **Merging runs to a fixpoint and is transitive** (SPEC 8.8 moves the survivor to the extreme price). ~65% of levels end MERGED — arithmetic, not a defect. |
| 6 | **`PROTECTED_SWING` duplicates `SWING_*` 95% of the time.** It is a strength annotation. Its near-zero sweep rate is the merge working, not the source failing. |
| 7 | **`PREV_SESSION_EXTREME` is redundant** with `SESSION_*` (5-day tier-3 expiry) and is not implemented. |
| 8 | **The population is 61% session levels.** Every downstream statistic must break down by source. |
| 9 | **`GAPPED_THROUGH` is tested before penetration depth** — a bar opening beyond a level satisfies `low < price` and would be misreported as a breakout. |
| 10 | **Level and event ids are unique per run, not globally.** 206 collide across five fixture years. **Must be namespaced (ULID, SPEC 1.7) before Phase 14 pools trades.** |
| 11 | **The displacement leg origin is never searched for.** Clamped to the sweep extreme, even when that makes the leg fail. |
| 12 | **FVG membership in a leg is by confirmation bar**, so the gap's first bar may precede the leg — SPEC 10.4's own example relies on it. |
| 13 | **Run the full suite after changing a shared dataclass.** Adding `sweep_extreme_bar` to `SweepEvent` broke nine Phase 7 tests that the sweep tests alone did not catch. |
| 14 | **`SwingStore.visible_at(bar)`, never `SwingStore.swings`, when asking what existed at a past bar.** Normalisation deletes a superseded swing from every earlier bar too. Conservative rather than lookahead, which is why it hid for four phases (D-009 §4). |
| 15 | **The sweep→MSS window has two floors**, the WAIT from the sweep *extreme* and knowability from the sweep *confirm* bar. Only the first is in the spec text (D-009 §3). |
| 16 | **The funnel changes units mid-chain**: stages 1–2 count levels, 3–7 count events, and `sweeps_triggered > levels_swept_or_tested` is correct, not a bug (D-009 §10). |
| 17 | **Stacked levels swept by one bar are ONE opportunity** (SPEC 9.4). Every Phase 9 headline number is deduplicated per cluster; the per-sweep MSS count is 51 against 38, and is not the gate. |
| 18 | **A forward return is a function of `(break bar, direction)` and nothing else.** Candidates sharing both contribute the identical number, so the H5 study collapses them — 640 raw CHoCH become 315 observations. Leaving them in made h=1 read EQUIVALENT when it is UNDERPOWERED (D-010 §3). |
| 19 | **`UNDERPOWERED` is not `EQUIVALENT`.** H5 is falsified by a *negative*, so only a CI sitting inside the declared margin licenses "decoration". A two-way verdict reports this fixture as a null result on the methodology's central claim. |
| 20 | **The H5 equivalence margin (0.25 ATR) is declared, not derived**, and fixed before any result was read. Changing it after seeing a verdict selects the answer (§10.2). |
| 21 | **`Fvg.proximal` is `zone_high` for a bullish gap** — the edge price reaches first. SPEC 12.1's table says the opposite and is wrong; 12.2 and 12.4 agree with the code. Entry model C's limit price depends on it (D-011 §1). |
| 22 | **FVG touch is range INTERSECTION, not SPEC 12.2's one-sided inequality.** One-sided made `INVALIDATED` structurally unreachable and counted every gap-over as a fill (D-011 §2). |
| 23 | **Use `Fvg.status_at(bar)`, never `Fvg.status`, to ask what was available at a past bar.** The field holds the end-of-run value; reading it is invisible lookahead (D-011 §3). |
| 24 | **`track_fvgs` returns copies.** Detection output is shared with the displacement engine, which must not depend on whether the tracker ran (D-011 §3). |
| 25 | **The four OB definitions are worth ~1.77 independent tests, not 4.** Use `M_eff` in the multiple-testing correction. Same-bar agreement *understates* redundancy — they pick different bars at almost the same price (D-012 §2). |
| 26 | **Never centre a correlation on the per-observation mean across the variables being compared.** It pins the average pairwise correlation at `-1/(k-1)`. Anchor on something exogenous (D-012 §3a). |
| 27 | **Galwey, not Li & Ji, for effective test counts.** Li & Ji is discontinuous at integer eigenvalues and returns ~2 for *perfectly* correlated variants because `eigvalsh` gives 3.999999999999999 (D-012 §3b). |
| 28 | **Null calibrations run 3,000 shuffles and quote a Wilson interval.** At 400 the standard error is ~1.1 points, the same size as the effect; three draws of one calibration read 4.8%, 8.0% and 5.5% (D-012 §4). |
| 29 | **A bar touching both the entry and the stop FILLS.** Continuity fixes the order — a limit is passed on the way to a stop beyond it. Cancelling it is physically wrong *and* not the pessimistic outcome, since a fill that stops out loses 1R and a cancel loses nothing (D-013 §1). |
| 30 | **`cancel_if` clause 1 needs a gap**, not a within-bar guess. It is about a level blown through and then revisited, which is the only place `backtest.intrabar_mode` changes an answer (D-013 §2). |
| 31 | **The synthetic fixture is perfectly continuous** — every bar opens at the previous close, weekends included. SPEC 15.3's lookahead trap therefore measures exactly 0.0000 ATR here, and gap-cancels never fire (D-013 §3). |
| 32 | **Model A is the only 100%-fill model.** Coverage runs 100/39/33/33/41%, so any model comparison must be per SETUP, never per trade (D-013 §5). |
| 33 | **The entry price is computed before the stop, and that ordering is required.** S4 defines the stop from the entry price while S1–S3 anchor on structure. Reverting it breaks only S4, silently (D-014 §4). |
| 34 | **A zero in S4's `PRICE_THROUGH_STOP` column means impossible, not "did not happen".** The stop is placed a fixed distance from the price, so the guard is vacuous for one model in four. Same for `max_sl_atr` under S4, and `sl.buffer_atr`, which S4 has no term for (D-014 §4). |
| 35 | **Four unreachable defaults, none of them changed** — `min_realised_fraction`, `max_total_open_risk_pct`, `max_sl_atr`-under-S4, and T3's RR gate. Each is pinned by a test asserting the unreachability. Do not "fix" one by moving the number; §10.2 (D-014 §1–§4). |
| 36 | **Which of two caps binds is a measurement, not a design choice.** `max_sl_atr`/`max_sl_pips` cross at 24 pips of ATR and `max_spread_pips`/`max_spread_pct_of_sl` at 20 (35 JPY) pips of stop. The fixture sits entirely on one side of the first; real bars may not (D-014 §5). |
| 37 | **`position_size` must never gain a parameter.** SPEC 18.1's invariant is the signature, and the test asserts the parameter set exactly. Adding a history-shaped argument is how martingale becomes implementable (D-014, `test_sizing_is_a_pure_function_of_its_declared_inputs`). |
| 38 | **Lots are floored with a `+1e-9` guard, not rounded.** Rounding puts mass above nominal risk on half of all trades. The epsilon is not cosmetic: `0.03 / 0.01` is 2.9999999999999996, and without it every exact multiple quantises one step low. |
| 39 | **A missing FX conversion rate raises; it never defaults to 1.0.** SPEC 18.2's "its absence blocks the inclusion of any symbol". Every Phase 13 number is EURUSD on a USD account, where the rate is 1 by identity, so the rule is tested and never exercised in anger. |
| 40 | **The limits-on/limits-off comparison does not mean anything yet.** With no exit policy the ledger fills to `max_open_positions` on the third setup and rejects the rest. It measures the absence of exits (D-014 §10). |
| 41 | **Ids are deterministic ULIDs keyed on content, not sequences.** SPEC 1.7 asks for a ULID and SPEC 25.1 forbids what one is made of. Putting a run-relative sequence back in a key breaks prefix-stability, because admission order tie-breaks on the id (D-015 §1). |
| 42 | **The old id scheme was 76% duplicates across five fixture years, not the 206 this file used to say.** 23,314 of 30,637. Now 0. |
| 43 | **`_SOURCE_PRECEDENCE` is load-bearing.** Tier and time both tie for the commonest merge in the book, so without it the survivor comes from the id format by accident — which is how D-006's finding was true for four phases (D-015 §2). |
| 44 | **A shadow trade is counterfactual on the CANCEL, never the FILL.** Re-resolve without the cancels; no fill means no shadow (D-015 §3). |
| 45 | **The entry bar is part of the exit walk, and the two fill shapes differ on it.** A market fill sees the whole bar; a limit's stop is proved by continuity but its target is not (D-015 §5). |
| 46 | **Pass one must not size.** SPEC 18.2's rejections are equity-dependent, so running them in the portfolio-free pass makes its population depend on an account size (D-015 §9). |
| 47 | **The rejection log's forward return is in ATR from the MSS close.** Not R from the planned entry — that reads +1.7R at 92% on a random walk (D-015 §7). |
| 48 | **Entries are processed before exits within one bar**, so a slot is never freed by a close whose time inside the bar is unknown. |
| 49 | **R is a ratio and the arms of a comparison rarely share its denominator.** An arm entering earlier has a tighter stop, so a fixed spread costs it more *per R* — enough to satisfy §10.1's falsification row on a random walk. Compare in **gross and net R both**; a net-only win is geometry (D-016 §1). |
| 50 | **The falsification suite cannot run at `entry.model = C`.** Sweep-only and reversed-order enter at the sweep confirmation, so their leg is median 0 bars and an FVG needs 3: both arm zero orders, on any data. It runs at model A (D-016 §2). |
| 51 | **`choch_only` is not `structure.py`'s CHOCH events.** A trend flip through the *protected* level is stricter and different from SPEC 11.2's break of the *last unbroken swing*. Build it on `MssEngine._major_reference`, and never check it by counting — the two counts cross between fixtures (D-016 §3). |
| 52 | **A control substitutes a setup stream (`Market.setup_override`) or a level book (`analyse_sweeps(level_transform=...)`), never a second engine.** An arm that re-stated the admission order or the fill discipline could differ from the baseline for a reason that is not under test, and nothing in its output would show it (D-016 §4). |
| 53 | **`Trade.setup_id` is the sweep id**, so two setups sharing one are scored as a trade plus a phantom 0.0 *and* collide in `run`'s `live` dict. The sweepless arms mint their own ids and must key on the trigger bar (D-016 §5). |

---

## 7. Statistical lessons already learned the hard way

The first seven are the same mistake at different scales. The last two are a different
mistake, and every one of them is now pinned by tests.

- **Phase 7:** 3 of 20 year×horizon significance tests fired on a random walk (≈1
  expected). The multiple-testing problem on data whose true effect is zero *by
  construction*. This is the concrete argument for `BACKTEST_PROTOCOL.md` §5.6.
- **Phase 8:** the "natural break" detector reported STRUCTURED because one histogram bin
  rose +11 counts against a Poisson SD of 18 — 0.6σ. Now requires 2σ.
- **Phase 9:** two of the eight TUNABLE parameters have now been measured as *not* the
  parameter that decides the outcome, while an ABLATION parameter spans the gate verdict.
  A classification written down in advance is a hypothesis about which knob matters, and
  it is being falsified twice.
- **The H5 study:** its first draft called a 0.5-sigma gap in the null calibration
  "mildly anti-conservative" — the same mistake a third time. The report now computes
  the standard error and states the deviation **in sigma**, which turned out to matter:
  after the duplicate-row fix the real deviation was 2.5 sigma and genuine.
  `stats.calibration_sigma` now exists so no study can report a rate without it.
- **Phase 11:** that sigma was itself computed from a 400-shuffle rate whose standard
  error is ~1.1 points — the same size as the effect. Three draws of one calibration read
  4.8%, 8.0% and 5.5%, and the 8.0% draw had already been written up as a finding in
  D-010 §5. Calibrations now run 3,000 shuffles and quote a Wilson interval, and D-010's
  figure was corrected (D-012 §4). **The lesson keeps arriving one level up: the fix for
  the last instance was itself measured too imprecisely to support what was said about
  it.**

- **Phase 13:** a mutation deleting the drawdown ladder's clamp survived the entire
  suite. Two mechanisms enforce one rule — a config validator so no configuration can
  *express* a multiplier above 1.0, and a clamp so no arithmetic can *produce* one — and
  every test reached the clamp through `load_config`, where the validator fires first. The
  clamp was untested **because** the other guarantee worked. Generalised: *when two
  mechanisms enforce one rule, the outer one hides the inner one from every test that goes
  through the front door.* Fixed by a test that bypasses the validator with `model_copy`;
  15 of 15 mutations are now caught.

- **Phase 14:** the same thing again, in `manage_stop`, one phase after D-014 §8 named
  it. Each management branch clamped internally *and* the function clamped at the end, so
  the final clamp was unreachable and a mutation deleting it changed nothing. Fixed by
  restructuring rather than by adding a test — the branches now propose and one clamp
  decides — because a guard that needs a special test to reach it is a guard in the wrong
  place. **Twice in two phases is a pattern, not an accident: check for it whenever a rule
  is enforced in more than one spot.**

- **The falsification suite:** the same lesson, moved off the *statistic* and onto the
  *unit*. Every earlier instance was a number not compared against noise; this one was
  compared against noise correctly and still separated, because the two arms did not share
  the denominator of the ratio they were compared in. A tighter stop costs more per R, and
  in net R that is indistinguishable from signal — enough to satisfy §10.1's own acceptance
  row on a random walk. **Check that a ratio's denominator is held fixed before reading a
  difference in it as an effect** (D-016 §1).

- **And the guard-nothing-reaches pattern arrived for the third time**, as D-015 predicted it
  would: three of the first eighteen mutations survived because the fixture never reaches the
  rule. One of them, `choch.max_reference_distance_atr`, is different from D-014's four
  unreachable defaults in a way worth keeping — those were arithmetic impossibilities, this
  is a *measurement* that binds at another of its own ABLATION values. **A guard that no test
  reaches through the front door needs a test that goes in the side door**, and if it has a
  configuration where it does fire, that configuration is the side door.

**A statistic not compared against what noise alone would produce is not a finding.**
**And a statistic compared in a unit the two arms do not share is not one either.**

Every study module carries a **positive control** as well as a null result. A study that
can only ever say "no edge" would pass the random-walk fixture and be worthless.
`tests/test_mss.py::test_positive_control_a_clean_setup_confirms` is Phase 9's.

**And: mutation-test the tests.** Phase 9's reference-selection fix passed the entire
suite when reverted; only deliberately breaking the code and watching for a red test
caught it. Four mutations were run against the new suite and all four are now caught.

---

## 8. The honest limitation

**Every number produced so far comes from a synthetic random walk.** It has no liquidity,
no participants and no structure, so:

- Sweep counts, rates, distributions, rejection rates and now the MSS funnel are
  properties of *the detectors meeting noise*. They prove the engines are deterministic,
  causal and self-consistent.
- The forward-return study correctly finds **nothing**, at every horizon. Had it found an
  edge, the study would be broken.
- **H2 is neither supported nor refuted.** It cannot be, on this data.
- **Phase 9's gate verdict is a projection built on that rate**, and the projection also
  assumes symbols are interchangeable and years stationary. Both are false; the ten majors
  are heavily correlated, so the effective sample is smaller than the count.
- **The H5 study validates its own instrument and nothing else.** A `DIFFERENT` verdict
  on this fixture would mean it is broken. Its power arithmetic is the one output that
  transfers (§3a).
- **Phase 10's FVG edge test is in the same position**, with one difference worth
  carrying: its population is ~18x larger, so unlike H5 it will be **answerable at every
  horizon on real data**. Its fill-rate curve is the number most likely to fall when real
  bars arrive — a random walk returns to a local extreme readily and a trending market
  may not.

- **Phase 13's numbers split cleanly into three kinds**, and only the first two are worth
  anything yet. *Arithmetic* (the four unreachable defaults, the cap crossovers, the
  purity invariant) holds on any data and is the phase's real output. *Instrument
  validation* (18/18 scenarios, the realised-risk distribution) says the machinery is
  correct. *Measurements* (`M_eff` = 1.36, 100% ATR-cap binding, the USD 2,000 minimum
  account, S4 arming on every setup) are fixture properties and several will move a lot —
  the fixture's median H4 ATR is 17.4 pips, which is what keeps S4 under its 40-pip
  ceiling and the pip cap out of play.

- **Phase 14 produced a complete equity curve and it means nothing.** Expectancy,
  profit factor, Sharpe, drawdown — all of it is the detectors meeting noise, and every
  confidence interval spans zero, which is the correct answer. The engine is validated;
  the market is not real. What the phase establishes is that the chain runs end to end,
  that R is computed in a pass which structurally cannot see equity, and that the two
  tests SPEC 25.2/25.3 name are green.
- **The fixture now fails to show a *third* execution effect.** It is perfectly continuous,
  so SPEC 15.3's lookahead (Phase 12), the S4 stop's movement at fill (Phase 13) and now
  **SPEC 17.5's intrabar stop-versus-target ambiguity — the protocol's "single largest
  backtest bias" — all measure exactly 0.0000 here**, the last of them because a bar
  spanning both a 1R stop and a 2R target needs a range of ~4.4 ATR. All three are pinned
  by constructed tests and all three are first in line on real bars.

- **The falsification suite's five arms are the sharpest case in the project.** Their
  whole purpose is to answer "does this component contribute?", and this fixture answers
  *no* for every one of them **by construction**, including the real ones. That makes them
  the one place where the guaranteed null is also the publishable conclusion — §6.3 invites
  the reader to act on it (*"rebuilt as a mean-reversion model and the SMC framing
  dropped"*). Nothing in `reports/falsification.md` licenses that, and the report says so
  before it says anything else. What the run does establish is the instrument, plus one
  finding about the *acceptance criterion* that is independent of the data (§3d).

`bot/data/synthetic.py` says so in its own docstring and is never used to produce a
strategy result.

---

## 9. What to do next

Phase 9 was the decision point and it did not stop the project. The H5 study that
followed it is built, validated and **open** — it cannot be answered on synthetic data,
and one of its findings is that part of it may not be answerable on real data either
(§3a). So the design stands and the next phases are the ones that turn an event into a
trade.

### The falsification suite is built; §6.5's ablation matrix is what remains

`BACKTEST_PROTOCOL.md` §6.3 and §6.4 are done (§3d, D-016). All five arms run through the
unmodified `run()`, 30 tests and 18/18 mutations pin them, and `scripts/falsification_report.py`
writes `reports/falsification.md` in about eleven minutes.

| Control | Tests | What a null verdict would mean |
|---|---|---|
| Shuffled liquidity (§6.3) | H3 | Liquidity identification contributes nothing — rebuild as mean-reversion and drop the SMC framing |
| Sweep-only (§6.4) | H4 | The CHoCH requirement only reduces sample size |
| CHoCH-only (§6.4) | H4 | The sweep requirement only reduces sample size |
| Reversed order (§6.4) | H4 | The *sequence* is not what works |
| Random-time (§6.4) | — | The floor: what this SL/TP geometry pays with no signal at all |

**None of those verdicts can be read yet** and none is read in the report: on a random walk
every arm's true effect is zero by construction, so a null is the fixture speaking. What the
run establishes is that the instrument works — and, unexpectedly, that **§10.1's acceptance
row does not**, in a way that has nothing to do with the data (§3d).

**§6.5's ablation matrix is now the largest unbuilt piece of the protocol**: one component
toggled at a time against the baseline, each a delta with a block-bootstrap CI. It shares
this suite's comparison machinery (`falsification.compare`, the declared margin, the
three-way verdict) and inherits D-016 §1 directly — **every ablation that changes the stop
distance is cost-confounded the same way**, and there are several (each SL model, the entry
models, `disp.max_leg_bars`).

Everything else in the protocol that remains — walk-forward (§8), the OOS budget ledger
(§7), the pre-registration (§1) — is a procedure over real splits and cannot start earlier.

### Still blocking real results

**Q1 (broker/account currency) and Q2 (M1 or tick history).** Answered in principle by
D-003 — raw-spread ECN, Dukascopy tick + broker M1 — but no data has been downloaded and
no broker chosen. Until then:

- Phase 1's broker-candle reconciliation stays BLOCKED.
- Phase 9's gate stays PASS-on-projection.
- H5 stays open: on a random walk the true MSS vs CHoCH-not-MSS difference is zero by
  construction, so the study can only ever validate its own instrument.
- **H3 and H4 stay open for the same reason** — the falsification suite is built and every
  arm's verdict is a property of the fixture, not of the market (§3d).
- Every study runs on synthetic data and can only validate instruments, not measure edge.

**This is still the single highest-value action in the project.** Six engines and three
studies are built, and none of their numbers mean anything about markets yet.

### When real data lands, run these in this order

1. **`scripts/phase9_report.py`** — the funnel on real bars. The gate's PASS is currently
   a projection from a synthetic conversion rate; this replaces it with a measurement,
   and the ABLATION sensitivity in §3 means the verdict could genuinely go either way.
2. **`scripts/phase11_report.py`** — recompute `M_eff` for the OB definitions. It is a
   property of how the four behave on *this* fixture; real bars have trends and gaps and
   the definitions may diverge more there. Every later correction depends on this number.
3. **`scripts/phase10_report.py`** and **`scripts/marginal_value_report.py`** — the two
   edge tests. Their instruments are validated and their power arithmetic recomputes
   itself from the real return variance, which is the number most likely to move.
4. **`scripts/phase12_report.py`** — the two things the continuous fixture cannot show:
   SPEC 15.3's lookahead (worth 10–30% of headline return per the spec, and exactly
   0.0000 ATR here) and the gap-past-the-stop branch. Both are pure gap effects. The S4
   stop's movement between arming and filling (D-014 §4) is a third, and measures 0.0000
   here for the same reason.
5. **`scripts/phase13_report.py`** — recompute `M_eff` for the stop models, and settle the
   three questions the fixture's 17.4-pip ATR cannot: whether real ATR clears S4's 40-pip
   ceiling, which of the two upper stop caps actually binds, and what the minimum viable
   account really is at the real stop-distance scale.
6. **`scripts/phase14_report.py`** — the whole chain on real bars, and specifically the
   three effects this fixture measures as exactly zero: SPEC 15.3's lookahead, the S4
   stop's movement at fill, and SPEC 17.5's intrabar ambiguity. Also the rejection table,
   which is the only place a filter can be shown to be destroying edge.
7. **`scripts/falsification_report.py`** — the §6.3/6.4 suite, which is the one that
   decides whether the project has demonstrated what it claims. Everything before it can
   pass while this fails. **Read the gross-R table before the net-R one** (D-016 §1), and
   settle which of the two §10.1 is to be judged in *before* running it, not after.
8. Re-measure the condition-bindingness ranking (D-008 §4, D-009 §7) before trusting the
   TUNABLE/ABLATION split.

### Before the first real backtest

1. **Settle D-016 §1** — whether §10.1's falsification row is judged in gross R, net R,
   or both. It has to be decided in the pre-registration, because after a result is seen
   the choice selects the answer (§10.2). The suite reports both and takes no position.
2. **Write the pre-registration** (`BACKTEST_PROTOCOL.md` §1) and commit its hash. It is
   due before the first *strategy* backtest — a synthetic run validates an instrument, so
   nothing so far has triggered it. Six of its seven items are already determined by
   existing documents; only the date ranges wait on the data. **A pre-registration written
   after seeing a result is not one.**
3. **Take the four D-014 decisions**, or the ablation grid has holes: T3 arms on no setup
   at the default `min_rr`, T4 runs on a different population from T1–T2, and two SPEC 18
   safety checks are inert.
4. **Build `events.jsonl`** (SPEC 21.1). The engine currently holds trades and rejections
   in memory and the report reads them there, which inverts the specification's
   relationship — the log is the primary artefact and the tables are derived from it. Fine
   while one process does both; a blocker for Phase 16, which reconciles a live log against
   a backtest.

Ids are namespaced (former §6 item 10, now done — D-015 §1).

### When Phases 2–4 land

`bot/core/mss.py` takes the MTF gate as an injected predicate
(`gate: (Direction, bar) -> bool`), currently the always-pass control — which is
`bias.gate_mode = none`, the variant SPEC 7.5 says MUST be run anyway. Dropping a real
gate in needs no change to the engine, **and it can only reduce the MSS count**: every
Phase 9 number is an upper bound. Re-run `scripts/phase9_report.py` afterwards; the gate
passed with 152 against a floor of 120, so a gate rejecting more than a fifth of setups
puts the development set back under it — and would push H5 further out of reach at the
same time.

### Revisit H5

R-expectancy is the half of `BACKTEST_PROTOCOL.md` §6.2 that could not be run: stops
(SPEC 16) and targets (SPEC 17) do not exist yet, so there is no R to measure. Worth
returning to, and not only for completeness — a stop truncates the left tail that drives
the forward-return variance, so an R-based comparison may resolve at a **smaller** sample
than §3a says raw forward returns need.

---

## 10. Working conventions

- One commit per phase, straight to `master`, with the findings summarised in the message.
- Every phase ends with a `scripts/phaseN_report.py` writing `reports/phaseN_gate.md`, and
  the report states what it does **not** establish.
- Corrections and findings go in `DECISIONS.md` as a new `D-00N` entry; spec text is
  amended in place with a note pointing at the decision.
- A gate item that cannot be evaluated is reported **BLOCKED** or **DEFERRED**, never
  quietly skipped.
- New rules get a **positive control** and a **mutation check**, not just a passing test.
