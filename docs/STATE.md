# Project State — pick-up point for a new session

Last updated: 2026-08-30, after real data landed (10 symbols, 2019-2025) and the Phase 9
gate was re-run on it as a measurement rather than a projection (D-020).

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

**Both halves of that question are now answered, and the answer to the first is no**
(D-030). The chain does not produce a measurable edge; the link that carries what little
signal there is, is the CHoCH/displacement step, and it mostly avoids a bad entry rather
than finding one. §9 has the decision; D-028 has the per-link detail.

---

## 2. Current status

| | |
|---|---|
| **Phases complete** | 1, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14 |
| **Studies complete** | H5 (SPEC 6.9), the falsification suite (protocol 6.3/6.4), the ablation matrix (6.5) |
| **Pre-registration** | **COMMITTED at v1.1 + Amendment 1** — `docs/PRE_REGISTRATION.md`, blob `646ddfb6db70` (D-018, re-registered by D-019, amended by D-021). **All seven of protocol §1's items are now fixed**: item 4's literal dates were stamped from §4.1's own rule on 2026-08-30 |
| **Data** | **Real bars in.** 10 symbols × 2019-2025 M1, `dataset_hash 2a2bb029`, source histdata, bid side, tzdata 2026.3. Splits: in-sample 2019-2022, OOS 2023-2024, holdout 2025. **Q2 answered; Q1 (broker) still open** |
| **Tests** | 662, all passing |
| **Commits** | 19, on `master` |
| **Python** | 3.14 in `.venv`; deps pinned in `requirements.txt` |

```bash
.venv/Scripts/python.exe -m pytest tests/          # 662 tests, ~107s
.venv/Scripts/python.exe scripts/phase9_report.py  # the Phase 9 funnel gate, REAL bars (~9 min)
.venv/Scripts/python.exe scripts/phase9_report.py --synthetic  # ... the original fixture
.venv/Scripts/python.exe scripts/phase12_report.py  # the Phase 12 entry engine
.venv/Scripts/python.exe scripts/phase13_report.py  # the Phase 13 risk layer
.venv/Scripts/python.exe scripts/phase14_report.py  # the Phase 14 backtest engine
.venv/Scripts/python.exe scripts/marginal_value_report.py  # the H5 study
.venv/Scripts/python.exe scripts/falsification_report.py   # protocol 6.3/6.4, ~11 min
.venv/Scripts/python.exe scripts/ablation_report.py        # protocol 6.5, ~20 min
```

### Phases built

| Phase | Scope | Gate report | Result |
|---|---|---|---|
| 1 | Data ingest, UTC timeframe construction, session engine | `reports/phase1_gate.md` | 11/11; broker reconciliation **BLOCKED** on Q1 (Q2 answered — real bars ingested, and a resampler bug only they could find) |
| 5 | H4 swing detection + market structure (BOS/CHoCH) | `reports/phase5_gate.md` | 7/7 |
| 6 | Liquidity engine — sources, lifecycle, merge, rank | `reports/phase6_gate.md` | 8/8; sweep-rate half deferred to 7 |
| 7 | Sweep detection + forward-return study (H2) | `reports/phase7_gate.md` | 7/7; closed Phase 6's deferred half |
| 8 | Displacement + FVG detection | `reports/phase8_gate.md` | 8/8 |
| 9 | CHoCH reference selection, MSS confirmation, **the funnel** | `reports/phase9_gate.md` | **9/10 on real bars — the development-set half of the gate FAILS**, see §3 and D-020 |
| — | **H5 study**: MSS vs CHoCH-not-MSS (SPEC 6.9, run out of order) | `reports/marginal_value.md` | 8/8 — instrument validated, **H5 open** |
| 10 | FVG lifecycle, selection, standalone edge test | `reports/phase10_gate.md` | 10/10 **on real bars** — **EQUIVALENT: no standalone FVG edge**, the project's first meaningful null, see D-023 |
| 11 | Order Block bake-off — four definitions, agreement matrix | `reports/phase11_gate.md` | 10/10 **on real bars** — four variants are worth **1.68 tests**, see D-022 |
| 12 | Entry engine — five models, fill resolution against M1 | `reports/phase12_gate.md` | 8/8 **on real bars, real M1** — the lookahead has a magnitude and two predictions failed, see D-025 |
| 13 | Risk — stops S1–S4, the RR gate, sizing, limits | `reports/phase13_gate.md` | 8/8 **on real bars** — **6 of 10 symbols cannot be sized at all** (no FX rate), see D-026 |
| 14 | Backtest engine — exits, costs, metrics, Monte Carlo | `reports/phase14_gate.md` | 10/10 **on real bars** — 102 trades, expectancy −0.19 R, CI spans zero, see D-027 |
| — | **The falsification suite**: shuffled liquidity, sweep-only, CHoCH-only, reversed-order, random-time (protocol 6.3/6.4) | `reports/falsification.md` | Built and validated — **and section 10.1's own acceptance row turns out to be satisfiable by stop width**, see D-016 |
| — | **The ablation matrix** (protocol 6.5) | `reports/ablation.md` | 34 variants over 19 components — **5 of the 19 cannot be toggled at all**, and 7 variants change nothing, see D-017 |

**Phase 5 was built before 2–4 deliberately**: Monthly/Weekly/Daily analysis is the *same*
engine instantiated on other bar series (SPEC 7.1), so building it once at H4 makes 2–4
mostly configuration.

### Not started

Phases 2–4 (Monthly/Weekly/Daily bias), 15–17 (charts, paper, live).

**All of `BACKTEST_PROTOCOL.md` §6 is now built** — §6.1/§6.2 as `sweep_study.py` and
`marginal_value.py`, §6.3/§6.4 as the falsification suite (§3d), §6.5 as the ablation
matrix (§3e). Every *result* in them is still meaningless on a fixture whose true effect
is zero by construction; what the two most recent ones produced instead were findings
about the protocol's own acceptance criterion and about which components exist at all.

---

## 3. The Phase 9 result, which is the project's decision point

Everything before Phase 9 produced candidates. Phase 9 asks the question the design rests
on — how many tradable events does the sequence actually produce — and **since 2026-08-30 the
answer is a measurement on real bars rather than a projection**:

| Mode | Sweep→MSS | MSS / symbol-year | Universe (4y × 10) | Dev set (4y × 3) | Gate |
|---|---:|---:|---|---|---|
| `major` | **1.59%** | 9.2 | **368** — clears ≥300 | **97** — misses ≥120 | **FAIL** |
| `micro` | 0.24% | 1.4 | 55 — misses | 16 — misses | **FAIL** |

Five things to carry forward, and the first is the whole result:

1. **The gate fails on its development-set half, which is the half that exists to catch
   this.** 368 across the universe clears ≥300 with room; 97 on EURUSD/GBPUSD/USDJPY misses
   ≥120. D-002 added that second half because *"a universe-wide count can hide a development
   set too thin to iterate on"*, and hiding it is exactly what the pooled number does here.
2. **The development set is the thin end of the universe by construction, not by luck.**
   EURUSD converts at 1.11%, the lowest of all ten majors; the four best — NZDUSD, EURGBP,
   AUDUSD, USDCAD — are all cross-set. Confirmed-sweep counts are nearly flat across the
   universe (2,138-2,399), so this is a difference in conversion, not in raw material.
   Adding symbols moves only the half that already passes, and swapping the development set
   selects a split by its outcome (D-020 §2).
3. **No registered parameter value rescues it.** `choch.max_reference_distance_atr` spans
   the verdict again, but its registered range {2.0, 3.0, 4.0} yields 41 / 97 / 113 against
   a floor of 120. Only 6.0 — outside the range — reaches it, and exactly. On synthetic the
   PASS was conditional on this parameter; on real bars the FAIL cannot be undone by any
   move that is actually available.
4. **The projection overstated the universe by 38% and the development set by 57%.** That is
   the size of error to expect from every other synthetic-fixture number in this file.
5. **D-009's specification contradiction now decides a gate verdict.** 87 of 368 MSS fail
   SPEC 6.6's leg clause — the one SPEC 11.5 omits while calling itself complete — so under
   the 6.6 reading the universe is 281 and *both* halves fail. It was cost-free on synthetic
   and is not now. Resolve it before quoting any Phase 10+ figure.

**What the failure does not settle is what to do about it.** SPEC §9 states the consequence
as *"the design is reconsidered before any entry code is written"*, and the entry code exists
— Phases 10-14 were built while the gate stood on a projection. D-020 §8 sets out the four
options and deliberately takes none of them.

`micro` remains a pre-registered null (SPEC 11.1), not a parameter that came out badly, and
§10.2 still forbids tuning it until it passes.

---

## 3a. The H5 study, answered at the short horizons and out of reach at the long one

`reports/marginal_value.md`, **run on real bars 2026-08-30 (D-024)**. H5 — "displacement
filtering adds value" — is **answered in the negative at h=1 and h=4, and unanswerable at
h=12**:

| h | n MSS | diff (ATR) | 95% CI | MDE | Verdict |
|---:|---:|---:|---|---:|---|
| +1 | 326 | −0.026 | [−0.117, +0.064] | 0.134 | **EQUIVALENT** |
| +4 | 326 | +0.017 | [−0.134, +0.168] | 0.232 | **EQUIVALENT** |
| +12 | 325 | −0.142 | [−0.417, +0.143] | 0.428 | UNDERPOWERED |

The pooled verdict is the weakest horizon's — **UNDERPOWERED** — and that is correct
rather than pessimistic: averaging a resolved answer together with an unresolved one is
the thing the three-way verdict exists to prevent. But two of three horizons *did*
resolve, and at those **MSS and CHoCH-not-MSS forward returns differ by less than 0.25
ATR**. That is H5 falsified at short horizons, on real market data.

**The power arithmetic squeezed from both ends.** The synthetic projection is on the left:

| h | needed (synth → real) | universe (427 → **326**) | dev set (128 → **88**) |
|---:|---|---|---|
| +1 | 58 → **93** | yes | **no** |
| +4 | 222 → **281** | yes | **no** |
| +12 | 804 → **951** | **no** | **no** |

Requirements rose and the population fell. **The development set cannot answer H5 at any
horizon** — 88 MSS against the 93 that h=1 alone needs. That is the third consecutive
study in which the pooled number resolves and the three symbols development iterates on do
not (D-020's gate, D-022's OB touches, this).

**Widening the margin is now closed.** The synthetic run listed 0.5 ATR as a live option
and said in advance that it was defensible before the data and an indefensible reaction
after it. The data has been seen; §10.2 binds. What remains for h=12 is §6.5's ablation
delta, which measures the same component through the full system rather than through
forward returns.

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

## 3d. The falsification suite — §10.1's deciding row, and it is not met

`reports/falsification.md`, **run on real bars 2026-08-30 (D-028)**. §10.1 requires the
full model to beat **every** §6.3/§6.4 control, in **both** gross and net R, each by a CI
excluding zero. It beats **3 of 5 in net R and 1 of 5 in gross**:

| Arm | tests | gross E/setup | net Δ vs base | gross verdict | median SL (ATR) |
|---|---|---:|---:|---|---:|
| `baseline` | — | +0.003 | — | — | 2.20 |
| `shuffled_liquidity` | H3 | −0.000 | +0.004 | `EQUIVALENT` | 2.12 |
| `sweep_only` | H4 | −0.021 | +0.064 | **DIFFERENT** | 0.88 |
| `choch_only` | H4 | +0.019 | −0.013 | `EQUIVALENT` | 2.14 |
| `reversed_order` | H4 | −0.021 | +0.063 | `EQUIVALENT` | 0.85 |
| `random_time` | floor | −0.010 | +0.047 | `EQUIVALENT` | 0.95 |

**H3 is falsified.** A randomly-placed level book performs the same: +0.003 gross, CI
[−0.010, +0.017], inside the declared ±0.10 R margin. `EQUIVALENT` is the verdict that
licenses "contributes nothing" — not absence of evidence, but evidence of absence at the
margin the project itself declared tradable.

**The only component that survives is the CHoCH step**, and it survives in a weaker sense
than it looks: `sweep_only` is beaten in both currencies, but at −0.021 gross it is *worse
than the random-time floor* (−0.010), and the baseline (+0.003) is `EQUIVALENT` to that
floor. **Waiting for the CHoCH mostly recovers from a bad entry rather than finding
signal.**

**D-016 §1 is confirmed on real bars.** The two arms that flip between currencies —
`reversed_order` and `random_time` — are exactly the two with much tighter stops (0.85 and
0.95 ATR against 2.20). A fixed spread costs more per R against a tighter stop. The
pre-registration fixed "judge in both" *before* any of this ran, and that is what stops
the net column reading as three wins.

The original §10.1 text, for the record:

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

## 3e. The ablation matrix, and the components that cannot be toggled

`reports/ablation.md`. Section 6.5 names **nineteen** components to toggle one at a time.
The matrix's real output is how many of them can be toggled at all, because that answer
does not depend on the fixture:

| Status | Meaning | Count |
|---|---|---|
| `PAIRED` | Same `Market`, only `run()` differs; compared setup by setup | 21 variants |
| `UNPAIRED` | The toggle changes the pipeline, so the Market is rebuilt | 13 variants |
| `BLOCKED` | Specified, engine unbuilt (Phases 2-4) | 2 rows |
| `ABSENT` | Named in 6.5, **exists nowhere in the codebase** | 3 rows |

**Every default stands.** One row of 34 excluded zero (`sl.model = S3`, p = 0.004) and
Benjamini-Hochberg took it to q = 0.153, which does not survive q = 0.10. Its gross delta
agrees in sign, so it is not D-016's cost confound — it is one try out of 34, which is
what §5.6 exists to catch.

### Four things that are true whatever data arrives

1. **`session filter`, `killzone filter` and `liq.tier_confirmation_tf` are not
   implemented.** The third is the one §6.5 calls **"the D-002 counterfactual"** — the
   alternative to the decision that makes this a session-to-session swing model (§5). It
   is declared in the schema, marked ABLATION, and read by no module. **D-002 cannot
   currently be tested against its own alternative.** A test greps the package so
   implementing any of the three fails rather than leaving the matrix claiming ABSENT.
2. **`ob.definition` was hardcoded to OB-A in the engine** (`definition=` a literal), so
   the four SPEC 13.2 variants were unreachable end to end and Phase 11's bake-off had no
   counterpart in `run()`. Fixed, default byte-identical. **Still inert at the shipped
   defaults**: entry model C reads an FVG and stop S1 reads the sweep extreme, so neither
   consumes an order block. Only entry D or SL S3 makes it observable.
3. ~~**A fifth default that cannot fire.**~~ **RETRACTED — D-019.** T2 armed nothing
   because the engine never passed the liquidity book to the target gate and because
   `_opposing_side` was inverted, not because of `tp.min_target_rank`. Setting the rank to
   0 changed nothing, which is the measurement that should have preceded naming a cause.
   Both bugs fixed; T2 now arms, and its real constraint is SPEC 17.2's RR gate. **T3
   remains structurally dead** (D-014 item 4), so §6.5's "each TP model" row is T1, T2 and
   T4.
4. **"One component at a time" is not achievable where components share objects.**
   `disp.mode = bar` fills 2 trades because `leg` mode confirms displacement *by finding
   an FVG* and entry model C enters on that FVG — so the row measures the entry model.
   Same for `require_fvg`, and for the OB rows. All three need a second axis. D-016 §2 is
   the same shape: the shipped default is the awkward one to measure against, twice now.

### INERT is not "no measurable effect"

§6.5's rule — *"a CI spanning zero is 'no measurable effect' and its default stands"* — is
followed literally, and it cannot distinguish the two things that produce a delta of
0.0000. **7 of 34 variants changed the outcome of zero setups.** For those the sentence
says the component was tested and did not matter, when it was never reached. The time stop
is the clearest: **15, 30, 60 and off are all the same run**, because no trade lives long
enough for any horizon to bind.

So the matrix reports `INERT` and `NO_TRADES` as statuses **outranking any verdict**, with
no delta and no CI. This is D-014 §8 / D-016 §5's "a guard nothing reaches" one level out:
there a rule no test exercised, here a rule no *data* exercises.

### Two more worth carrying

- **Median MDE is 0.076 R paired against 0.181 R unpaired, a factor of 2.4.** An unpaired delta cannot
  separate *changed outcomes* from *changed what we traded* — a filter removing half the
  setups shifts expectancy by selecting a population, and reading that as its value is how
  a filter that only reduced sample size gets recorded as one that improved the edge.
- **`sweep.max_penetration_atr` rejects 460 sweeps at its default and moves 3 setups.**
  Raising it to 2.0 admits 66 more confirmed sweeps and 3 more setups; above 2.0 nothing
  changes at all. Its rejections reappear as `ACCEPTED_THROUGH` as it loosens (156 → 700
  → 1,015), the near-substitution SPEC 9.2 warns about. *"This filter does nothing"* and
  *"this filter changes nothing"* are different statements, and a one-at-a-time delta
  reports only the second. Likewise `disp.min_leg_atr = 0` admits **nothing**, so
  `require_fvg` and `min_body_ratio` already imply the threshold — which makes
  `min_leg_atr`, a TUNABLE carrying §5.5's plateau requirement, unmeasurable
  one-at-a-time.

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
                falsification.py (protocol 6.3/6.4 -- the five controls),
                ablation.py (protocol 6.5 -- the matrix),
                preregistration.py (protocol 1 -- the grid, M, the splits)
scripts/        build_dataset.py, phase{1,5,6,7,8,9,10,11,12,13,14}_report.py,
                marginal_value_report.py, falsification_report.py,
                ablation_report.py, regen_golden.py
tests/          662 tests + tests/golden/structure_h4.json
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

D-004 through D-029 record corrections and findings from each phase's implementation.
**D-028 is the one that matters most**: it is the study §10.1 says decides the question,
and it did not pass. **Every study and phase gate in the project has now been run on
real bars.**
**D-020 is the one to read first**: it is the only entry written against real bars, and
it is the one that turned Phase 9's PASS into a FAIL.

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
| 25 | **The four OB definitions are worth `M_eff` = 1.68 independent tests, not 4** — measured on real bars, in-sample (D-022). The synthetic fixture said 1.77, so this is one of the very few numbers in the project that transferred. Use it in the multiple-testing correction. Same-bar agreement *understates* redundancy badly: OB-A and OB-D never pick the same bar and still correlate 0.925 on entry price (D-012 §2, D-022 §2). |
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
| 54 | **A delta of exactly 0.0000 is two different findings.** INERT (the toggle changed no trade) is not "no measurable effect" (it fired and did not matter), and §6.5's rule cannot tell them apart. 7 of 34 ablation variants are inert; the time stop is identical at 15, 30, 60 and off (D-017 §4). |
| 55 | **Three §6.5 components are not implemented**: session filter, killzone filter, and `liq.tier_confirmation_tf` — the last being §6.5's own named **D-002 counterfactual**, declared ABLATION and read by nothing (D-017 §1). |
| 56 | **The block length is a calendar span, not an observation count.** §5.3 says ~20 trading days, and arms differ in trade density, so each gets its own (D-017 §7). |
| 57 | **A paired ablation uses a sign-flip permutation, never the pooled two-sample test**, which discards the pairing — the power it was for. Median MDE 0.076 R paired against 0.181 R unpaired (D-017 §6/§7). |
| 58 | **`ob.definition` reaches the engine only via `cfg`; it was a hardcoded literal for four phases**, so OB-B/C/D had never run end to end. Fixed, and still inert at the shipped defaults because entry C and stop S1 consume no order block (D-017 §2). |
| 59 | **`tp.min_target_rank = 2.0` makes T2 arm nothing** — a fifth default that cannot fire, on top of D-014's four, and the only target model that aims at a liquidity level (D-017 §3). |
| 60 | **`M = 9,600`, not `PARAMETERS.md`'s 6,912 or 8,000.** It is computed from the schema's own grids by `preregistration.py` and pinned by a test that parses them back out of the field descriptions. `M` scales the Deflated Sharpe and §5.6's null, so a wrong one mis-corrects every claim (D-018 §1). |
| 61 | **The pre-registration is committed and its blob hash is in §2.** Changing a threshold, a grid, `M`, or a decision rule in it is **not an amendment** — it is a new pre-registration, and every result under the old one is reported as such (D-018). Exactly one change has qualified as a mere amendment: stamping §4.1's literal split dates, which applied a rule rather than choosing anything (D-021). |
| 62 | **`INCONCLUSIVE` is a verdict, not a soft FAIL**: fewer than 200 OOS trades, or an MDE exceeding the +0.10 R being tested for. A study that could not have seen the effect has failed to look, not failed to find (D-018 §4). |
| 63 | **A rejection reason names the gate that refused, not the reason it refused.** T2's `NO_TARGET_AVAILABLE` was read as "no level clears the rank filter"; the filter was never reached, because the engine passed an empty book. Setting the parameter to 0 and seeing nothing change is the one-line check (D-019 §1). |
| 64 | **The finished `LiquidityBook` cannot be read causally at all.** SPEC 8.8's merge rewrites a survivor's `price`, `tier` and `strength` **in place**. Use `book.active_at(bar)` / `ranks_at(bar)`, never a finished level. Third instance of D-009 §4 / D-011 §3 (D-019 §4). |
| 65 | **A long targets BUY_SIDE liquidity, a short SELL_SIDE.** Inverted for the project's life, with six tests asserting the inversion; SPEC 17.1's worked example targets a PDH (BUY_SIDE) for a BUY LIMIT (D-019 §3). |
| 66 | **`tp.min_target_rank = 5.0` was SELECTED BY ITS OUTCOME** on the synthetic fixture, by instruction (D-019 §6). It carries no evidence of being right. Re-derive it on real bars before any T2 comparison means anything. |
| 67 | **The Phase 9 gate FAILS on real bars, on its development-set half** — 368 across the universe (clears ≥300) against 97 on the three development symbols (misses ≥120). A pooled count hides it, which is the exact failure D-002 added that half to expose (D-020 §1). |
| 68 | **EURUSD is the worst sweep→MSS converter of all ten majors** (1.11% against NZDUSD's 2.02%), so the development set is the thin end of the universe by construction. Adding symbols cannot fix the failing half; swapping the development set selects a split by its outcome and empties the cross-sectional test (D-020 §2). |
| 69 | **No registered value of `choch.max_reference_distance_atr` clears the development gate** — {2.0, 3.0, 4.0} give 41 / 97 / 113 against 120, and only the unregistered 6.0 reaches it, exactly. On synthetic this parameter spanned the verdict; on real bars the rescue is not available (D-020 §3). |
| 70 | **SPEC 6.6 versus 11.5 now decides a gate verdict.** 87 of 368 MSS fail the leg clause 11.5 omits, so adopting 6.6 takes the universe to 281 and both halves fail. D-009 priced it as cost-free on synthetic data; it is not cost-free now (D-020 §4). |
| 71 | **The OB edge study is answerable on the universe and not on the development set.** 492 OB-A touches in-sample, of which 135 are on the three development symbols against the 155 that h=1 alone needs. h=6 and h=12 are out of reach even universe-wide. Same shape as D-020: the pooled number passes and the set iteration happens on does not (D-022 §3). |
| 72 | **The null calibration is calibrated on real bars and was not on synthetic** — 5.6% over 3,000 shuffles, 1.6 sigma, CI containing alpha, because the pooled sample is 492 touches rather than a few dozen. The bootstrap's under-coverage was a small-sample artefact, so real-bar UNDERPOWERED verdicts are trustworthy rather than understated (D-022 §4). |
| 73 | **There is no standalone FVG edge, measured on real bars** — EQUIVALENT at h=1, 3, 6 and 12 over 7,800 touches, every interval inside the declared +/-0.25 ATR. This is the project's **first null that is about the market rather than the fixture**. It does *not* say `disp.require_fvg` or entry model C are worthless: those use a gap as displacement evidence and as a price to bid at, neither of which is what was tested (D-023). |
| 74 | **A report whose prose was written for the fixture will state a real-data result backwards.** Phase 10's first real run printed "on a random walk the true effect is zero by construction" underneath a genuine market null, and printed "INVALIDATED is zero on this fixture" directly under a paragraph reporting 19 of them. Grep every generated report for `random walk` and `fixture` after pointing a script at real data (D-023 §5). |
| 75 | **Components of this strategy are not equally measurable, and the two the design rests on are the hardest.** Over the same in-sample split: 7,800 FVG touches, 492 OB touches, 368 MSS events. The FVG concept gets ~21x the sample the MSS chain does, so a confident null about FVGs and an underpowered shrug about MSS are what the data supports, not a statement about which component matters (D-023 §3). |
| 76 | **H5 is answered at h=1 and h=4 and unanswerable at h=12** — EQUIVALENT on both short horizons over 326 MSS events, so displacement filtering does not separate MSS from CHoCH-not-MSS forward returns by as much as 0.25 ATR. The pooled verdict reads UNDERPOWERED because it takes the weakest horizon, which is correct and hides the answer; read the per-horizon table (D-024). |
| 77 | **Widening H5's 0.25 ATR margin is closed, permanently.** It was listed as a live option *before* the data with the note that it would be an indefensible reaction afterwards. The data has been seen. Anyone reaching for 0.5 ATR to make h=12 answerable is doing the thing §10.2 prohibits (D-024 §4). |
| 78 | **Three studies now say the same thing about the development set: it answers nothing.** Phase 9's gate failed on it (97 MSS vs 120), the OB study cannot resolve any horizon on it (135 touches vs 155), and H5 cannot either (88 MSS vs 93). Every one of those pooled counterparts passes. Iterating on three symbols is the design decision this keeps colliding with (D-020, D-022 §3, D-024 §3). |
| 79 | **`cancel_if` clause 2 is NOT a fixture artefact, and it decides the entry bake-off by itself.** The synthetic report predicted the sweep rate behind it was one *"no real market sustains"*; real bars give **0.44 confirmed sweeps per H4 bar against 0.47** on the fixture. Limit fill rates run 6-10% with the clause against 30-46% without it. A FROZEN clause discarding ~90% of every limit model's population is not a background condition (D-025 §3). |
| 80 | **SPEC 15.3's lookahead is worth 0.0156 ATR per entry on real bars**, from 43,360 non-zero close-to-open gaps in 64,228 transitions. Small against the spec's *"10-30% of headline return"* but taken on **every** trade, and exactly 0.0000 on the fixture. The rule is now demonstrable, not just load-bearing (D-025 §2). |
| 81 | **The gap-past-the-stop branch stays unexercised even on real data** — 0 firings over 40 symbol-years. Real H4 gaps are ~0.005 ATR median against a stop 1-2 ATR away. Unlike Phase 10's `INVALIDATED`, moving to real data did **not** bring this guard alive, and that is not grounds to remove it: the gap that clears a stop is precisely the tail event it exists for (D-025 §4). |
| 82 | **Only 4 of the 10 symbols can be sized at all, and it is not the account size.** Every symbol whose QUOTE currency is not the account currency is blocked by SPEC 18.2's missing-FX-rate rule (Q1 open): USDJPY, EURJPY, GBPJPY, USDCAD, USDCHF, EURGBP. The pre-registration's cross-sectional criterion (≥ 6 of 10 symbols) is **unevaluable** until a rate series exists (D-026 §1). |
| 83 | **`trade.evaluate` reports a missing FX rate as `SIZE_BELOW_MIN`**, so the rejection log names lot granularity for a failure that is nothing of the kind — and it produced exactly the wrong diagnosis ("the account is too small") before the sizing call was run directly. D-019 §1 recurring. SPEC 19 has no code for it, so adding one is a specification change (D-026 §2). |
| 84 | **S4's 40-pip ceiling does bind on real bars** — 12% of setups overall, 47% of GBPUSD's, 1% of EURGBP's, and the ceiling is **60 pips for JPY pairs** because `max_sl_pips` is {default: 60, JPY: 90}. S4 is a partially available model whose availability depends on the symbol; D-014 §3's open question, answered (D-026 §3). |
| 85 | **An account sweep must not be fed the setups that already sized.** Its denominator would be fixed by the one number it varies. Feed it every setup whose stop cleared the SPEC 16.3 caps (D-026 §4). |
| 86 | **The whole in-sample book is 102 trades on four symbols**, against protocol 5.1's floor of 200 for a headline claim. The shortfall is **structural, not a matter of more history**: six symbols cannot be sized for want of an FX rate (D-026), so reaching 200 in-sample needs a conversion series rather than more years (D-027 §2). |
| 87 | **In-sample expectancy is −0.19 R with a CI spanning zero** ([−0.38, +0.02] block bootstrap, 102 trades). Negative point estimate, interval reaching positive, sample too small to separate them — neither evidence of edge nor evidence against, and **not** a result to quote either way (D-027 §1). |
| 88 | **Phase 14 must build one market at a time.** It runs ~19 variants over each, so the original built all markets first; ten symbols × four years of M1 is 1.04 GB measured. `run` costs ~0.0s against `build_market`'s ~44s, so inverting the loops is free (D-027 §4). |
| 89 | **§10.1's deciding row is NOT met on real bars** — the baseline beats 3 of 5 controls in net R and 1 of 5 in gross, against a requirement of 5 of 5 in both. Only `sweep_only` is beaten in both currencies (D-028 §1). |
| 90 | **H3 is falsified: a shuffled level book performs the same as the real one.** +0.003 gross, CI [−0.010, +0.017], inside the declared ±0.10 R. `EQUIVALENT` is evidence of absence at the project's own tradable-edge margin, not absence of evidence (D-028 §2). |
| 91 | **The baseline does not separate from the random-time floor in gross R**, and `sweep_only` is *worse* than that floor. So the CHoCH step's measurable value is largely recovery from a bad entry rather than signal (D-028 §3). |
| 92 | **Judging §10.1 in both currencies is what prevented a false pass.** The two arms that flip — `reversed_order`, `random_time` — have stops of 0.85 and 0.95 ATR against the baseline's 2.20. D-016 §1 predicted this on synthetic data; real bars confirm it, and the pre-registration had already closed the question (D-028 §4). |
| 93 | **Every ablation default stands on real bars.** Three of 34 rows clear §6.5's raw rule and none survives Benjamini-Hochberg at q = 0.10 — `tp.model = T4` closest at **q = 0.102**, which is the tightest miss in the project and exactly what §5.6 exists for (D-029 §1). |
| 94 | **The INERT rows did NOT come alive on real bars**, contrary to D-017's prediction that longer-lasting trades would make the time stop, break-even and trailing bite. `exit.max_bars_in_trade` at 15 / 30 / 60 / off remain byte-identical; 6 of 34 variants are still INERT plus T3 producing no trades (D-029 §2). |
| 95 | **A protocol study's prose must be re-swept every time its data changes.** Six reports in a row shipped a first draft that stated its own result backwards, and the ablation matrix costs ~50 minutes per regeneration — so sweep for `random walk`, `fixture` and `synthetic` BEFORE the run, not after it (D-029 §4). |

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

- **The ablation matrix:** the mutation discipline caught its own tests being thin.
  Nine of the first nineteen mutations survived, and six were one gap — the tests
  exercised `stats` directly and nothing checked that `evaluate` *called* the block
  bootstrap or the paired test, so swapping either for its weaker sibling changed only
  numbers no test asserted on. Fixed with spies on the wiring. **Testing a primitive is
  not testing that the caller uses it.**

- **T2 (D-019):** the same shape again, and this time in a *diagnosis* rather than a
  statistic. `NO_TARGET_AVAILABLE` on every setup was read as "no level clears the rank
  filter" and written up as a finding — a fifth unreachable default — when the filter was
  never reached at all, because the engine passed an empty book. **The check was one line:
  set the parameter to 0 and see whether anything changes.** Nothing did. A rejection
  reason names the gate that refused, not the reason it refused.

**A statistic not compared against what noise alone would produce is not a finding.**
**A statistic compared in a unit the two arms do not share is not one either.**
**A delta of zero is not a finding until you know whether the thing ever fired.**
**And a rejection reason is not a diagnosis until you have moved the thing you blamed.**

Every study module carries a **positive control** as well as a null result. A study that
can only ever say "no edge" would pass the random-walk fixture and be worthless.
`tests/test_mss.py::test_positive_control_a_clean_setup_confirms` is Phase 9's.

**And: mutation-test the tests.** Phase 9's reference-selection fix passed the entire
suite when reverted; only deliberately breaking the code and watching for a red test
caught it. Four mutations were run against the new suite and all four are now caught.

---

## 8. The honest limitation

**Every phase gate has now been re-run on real bars.** Real bars landed on 2026-08-30;
**Phases 9-14** (§3 D-020, D-023, D-022, D-025, D-026, D-027) and **the H5 study**
(§3a, D-024) are all measured on them. What is left on the fixture is **the
falsification suite and the ablation matrix**. Where the text below still describes
the fixture, it describes `--synthetic`, which every report still reproduces:

- Sweep counts, rates, distributions, rejection rates and now the MSS funnel are
  properties of *the detectors meeting noise*. They prove the engines are deterministic,
  causal and self-consistent.
- The forward-return study correctly finds **nothing**, at every horizon. Had it found an
  edge, the study would be broken.
- **H2 is neither supported nor refuted.** It cannot be, on this data.
- **Phase 9 has left this list.** Its gate is a measurement now, and it FAILS (§3). The
  projection it used to rest on overstated the universe by 38% and the development set by
  57% — the size of error to expect from the rest. What survives unchanged is the
  correlation caveat: the ten majors are heavily correlated, so the effective sample is
  smaller than the count, and the gate is stated in counts.
- **H5 has left this list too.** Run on real bars it answers its own question at h=1
  and h=4 (EQUIVALENT, no difference) and cannot at h=12 (§3a, D-024). Its power
  arithmetic — the output the fixture run said would transfer — did transfer, in the
  sense that its *shape* held while both its terms moved against us.
- **Phase 10 has left this list, and it is the one that produced an answer.** Both of
  its predictions were checked on real bars (D-023): it *was* answerable at every
  horizon — 7,800 touches, all four intervals inside the margin, verdict **EQUIVALENT**
  — and its fill-rate curve *half* fell, early but not at 30 bars. **A resolved null on
  real data is the first result in this project that is about the market rather than
  about the fixture.**

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
- **Of the three execution effects the fixture measured as exactly 0.0000, real bars
  moved one.** SPEC 15.3's lookahead is now **0.0156 ATR per entry** (D-025). The
  gap-past-the-stop branch is still 0 — real H4 close-to-open gaps run ~0.005 ATR
  against a stop 1-2 ATR away, so the discontinuity needed is two orders of magnitude
  larger than the typical one. The S4 stop's movement at fill (Phase 13) and SPEC
  17.5's intrabar ambiguity (Phase 14) are **still unmeasured** — those reports have
  not been re-run. All remain pinned by constructed tests.

- **The falsification suite's five arms are the sharpest case in the project.** Their
  whole purpose is to answer "does this component contribute?", and this fixture answers
  *no* for every one of them **by construction**, including the real ones. That makes them
  the one place where the guaranteed null is also the publishable conclusion — §6.3 invites
  the reader to act on it (*"rebuilt as a mean-reversion model and the SMC framing
  dropped"*). Nothing in `reports/falsification.md` licenses that, and the report says so
  before it says anything else. What the run does establish is the instrument, plus one
  finding about the *acceptance criterion* that is independent of the data (§3d).

- **The ablation matrix splits the same three ways Phase 13 did**, and the split is
  worth keeping in mind when reading it. *Structural*: the five components that cannot be
  toggled, the OB wiring, the component coupling — true on any data. *Arithmetic*: T2 and
  T3 arming nothing. *Measurements*: every delta, every CI, and every INERT row — real
  bars trend, so trades last longer and the time stop, break-even and trailing may all
  start to bite.

`bot/data/synthetic.py` says so in its own docstring and is never used to produce a
strategy result.

---

## 9. The decision, and what it closed

**Everything measurable was measured, and the project reached its terminal decision on
2026-08-31: the null is accepted (D-030).** Every phase gate and every protocol study runs
on real bars (§8); nothing was left blocked on a run, on data, or on more code. What
follows records the evidence, the fork that was open, and which branch was taken.

### What the evidence says, in one place

| | Result | Where |
|---|---|---|
| Phase 9 gate | **FAILS** — 97 dev-set MSS against 120 | D-020 |
| H3, real liquidity levels matter | **FALSIFIED** — a shuffled level book performs the same | D-028 §2 |
| H4, the sequence matters | **Split** — the CHoCH step contributes, the sweep and the ordering do not | D-028 §3 |
| H5, displacement filtering adds value | **EQUIVALENT** at h=1 and h=4 | D-024 |
| FVG standalone edge | **EQUIVALENT** at every horizon | D-023 |
| §10.1's deciding falsification row | **NOT MET** — 3 of 5 in net R, 1 of 5 in gross | D-028 §1 |
| In-sample expectancy | **−0.19 R**, CI spans zero, 102 trades | D-027 |
| Ablation matrix | Every default stands; nothing survives BH at q = 0.10 | D-029 |

Two structural facts sit underneath all of it: **only 4 of 10 symbols can be sized** for
want of an FX conversion series (D-026), and **the development set answers nothing** —
three separate studies could not resolve on it while their pooled counterparts could
(rule 78).

### The fork — **resolved: A, on 2026-08-31 (D-030)**

**A — Accept the null and publish it. TAKEN.** `BACKTEST_PROTOCOL.md` §10.2 and D-003
(Q17) both say a documented null is an accepted deliverable, and this one is unusually
well evidenced: an `EQUIVALENT` verdict on H3 is evidence of absence at the project's own
declared tradable-edge margin, not absence of evidence. **Nothing further is built.** The
strategy is not carried forward; the deliverable is the negative result and the evidence
for it. See D-030 for the decision, what it does and does not claim, and the change
control that governs reopening it.

The two options **not** taken are kept below, because D-030 §5 makes either of them a new
pre-registration rather than a continuation, and a future reader should see what was
declined.

**B — Diagnose, revise, and re-register.** The per-arm detail says *where* the chain
fails, which is more than a bare null: liquidity identification contributes nothing, the
sweep requirement contributes nothing, and the CHoCH step contributes mostly by avoiding a
bad entry (`sweep_only` is worse than the random-time floor; the baseline is level with
it). A revision aimed at that is legitimate — but changing a component after seeing these
results is a **new pre-registration** (§10, D-018), and everything above becomes prior
work reported as such.

**C — Spend out-of-sample budget.** One evaluation on 2023-2024 under protocol §7. Worth
it only if a pass would change what you do; note that what it would confirm is an
in-sample result that already fails §10.1. **The budget is spent by looking**, so this is
not a free check.

**A was taken (D-030).** B and C remain available only as new pre-registrations, and
each must supersede D-030 explicitly.

### If B or C is ever taken, this is the order

**Not started, and not to be started under D-030.** Kept because it is the dependency order, and because knowing what was *not* built is part of the record.

1. **Unblock Q1 — broker and FX conversion rates.** It now blocks three separate things:
   the swap table, the cross-sectional criterion, and the primary metric's sample size.
   Six symbols cannot carry a sized trade without a quote→account rate series (D-026).
2. **Build `events.jsonl` (SPEC 21.1).** The engine holds trades and rejections in memory
   and the reports read them there, which inverts the specification — the log is meant to
   be the primary artefact. Tolerable while one process does both; a hard blocker for
   Phase 16, which reconciles a live log against a backtest.
3. **Phase 15 — visualisation.** Gate: 20 trades reviewed by eye, and *any* chart-versus-log
   disagreement is a blocker. Cheap, and the step most likely to surface an engine defect
   the statistics cannot see.
4. **The execution layer, none of which exists.** A broker adapter; a live loop with state
   persisted across restarts; the kill switch actually watching `ops.kill_switch_file`
   rather than being a pure predicate in `risk.py`; staleness detection
   (`max_data_staleness_sec`); reconciliation of broker fills against the engine's.
5. **Phase 16 — paper trading.** ≥60 days, ≥95% entry agreement and ≥90% on fills against
   a same-period backtest. A divergence beyond that is a defect, not variance.
6. **Phase 17 — live at `risk.pct_per_trade = 0.10%`**, and only if 16 passed *and* the
   pre-registered OOS criteria were met (SPEC §27).

Steps 4-6 are the bulk of the remaining work and none of it is written.

### There is no runnable bot, and that is by design not omission

`bot/` contains `config`, `core`, `data`, `backtest` and `research`. There is no live
loop, no broker adapter, no order placement and no scheduler; `scripts/` builds the
dataset or writes a report and does nothing else. Phases 15-17 were never started. What
`scripts/phase14_report.py` runs is a backtest of the in-sample split — not a trading
system, and not a thing that can be pointed at an account.

### The whole of `BACKTEST_PROTOCOL.md` §6 is built

§6.1 and §6.2 were already `sweep_study.py` and `marginal_value.py`. §6.3 and §6.4 are the
falsification suite (§3d, D-016) — five arms through the unmodified `run()`, 30 tests,
18/18 mutations. §6.5 is the ablation matrix (§3e, D-017) — 34 variants over 19
components, 25 tests. Neither produced an interpretable verdict, and neither was expected
to; both produced findings about the protocol and the codebase instead.

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

**§6.5's ablation matrix is built too** (§3e, D-017). It shares this suite's comparison
machinery — `falsification.Arm`, the declared margin, the three-way verdict — and inherits
D-016 §1 directly, reporting every row in both currencies.

**Everything in `BACKTEST_PROTOCOL.md` §6 is now built.** What remains in the protocol —
walk-forward (§8), the OOS budget ledger (§7), the pre-registration (§1), and §5.5's
plateau requirement — is a procedure over real splits and cannot start earlier. §5.5 is
worth naming: a plateau needs a metric that varies meaningfully across a TUNABLE grid, and
on a random walk it varies only by noise, so it has never been run at all.

### Still blocking real results

**Q2 is answered.** `data/parquet` holds 10 symbols × 2019-2025 M1 from HistData,
`dataset_hash 2a2bb029`, and Phase 9 has been re-run against it (§3, D-020). **Q1 (broker
and account currency) is still open**, and it now blocks a smaller and more specific set
of things than it used to:

- Phase 1's broker-candle reconciliation stays BLOCKED — although protocol §2's two-source
  check did pass against Dukascopy on a sample day during ingest.
- The spread and swap tables stay declared rather than measured: `cost.swap_pips_per_day`
  is empty, so every cost figure is a default and the reports say so.
- **Q1 now blocks the primary metric itself**, which was not known until D-026: six of
  the ten symbols cannot be sized without a quote→account conversion series, so the
  in-sample book is four symbols and 102 trades against protocol §5.1's floor of 200.
  Reaching that floor needs the rate series, not more history (D-027 §2).
- It also blocks the **cross-sectional criterion** — *"≥ 6 of 10 symbols with positive
  expectancy"* (pre-registration §3) — which is one of §10.1's go/no-go rows and is
  currently unevaluable.

**H2-H5 are no longer open for want of data.** Every study has been re-run on real bars:
H3 is falsified, H4 is split, H5 is answered at the short horizons, and H2's instrument
was never the blocker. See §9's fork — the highest-value action is now a decision, not a
run.

### Now that real data has landed, run these in this order

1. ~~**`scripts/phase9_report.py`**~~ — **DONE, 2026-08-30 (D-020).** The verdict went the
   way §3's ABLATION sensitivity warned it might: universe PASS, development set FAIL.
   Re-run after any change to the funnel; it now takes ~9 minutes.
2. ~~**`scripts/phase11_report.py`**~~ — **DONE, 2026-08-30 (D-022).** `M_eff` = **1.68**
   on real bars against the fixture's 1.77 — it transferred, which almost nothing else
   here has. Use 1.68 in every correction. Takes ~9 minutes.
3. ~~**`scripts/phase10_report.py`** and **`scripts/marginal_value_report.py`**~~ —
   **BOTH DONE, 2026-08-30 (D-023, D-024).** No standalone FVG edge (EQUIVALENT at
   every horizon, 7,800 touches); H5 EQUIVALENT at h=1 and h=4, UNDERPOWERED at h=12.
   Two of the project's component hypotheses now have real-data answers.
4. ~~**`scripts/phase12_report.py`**~~ — **DONE, 2026-08-30 (D-025).** SPEC 15.3's
   lookahead measures **0.0156 ATR per entry** (was exactly 0.0000). The
   gap-past-the-stop branch **still fires zero times** — real H4 gaps are ~0.005 ATR
   against a 1-2 ATR stop — so that prediction failed. So did the claim that the
   opposing-sweep cancel was a fixture artefact.
5. ~~**`scripts/phase13_report.py`**~~ — **DONE, 2026-08-30 (D-026).** `M_eff` = 1.32
   (was 1.36). S4's ceiling **does** bind, on 12% of setups and 47% of GBPUSD's. The
   minimum viable account is still USD 2,000. And **6 of 10 symbols cannot be sized at
   all** for want of an FX rate. Superseded text below, kept for the questions it named:
5. ~~**`scripts/phase13_report.py`** — recompute `M_eff` for the stop models, and settle the
   three questions the fixture's 17.4-pip ATR cannot: whether real ATR clears S4's 40-pip
   ceiling, which of the two upper stop caps actually binds, and what the minimum viable
   account really is at the real stop-distance scale.
6. **`scripts/phase14_report.py`** — the whole chain on real bars, and specifically the
   three effects this fixture measures as exactly zero: SPEC 15.3's lookahead, the S4
   stop's movement at fill, and SPEC 17.5's intrabar ambiguity. Also the rejection table,
   which is the only place a filter can be shown to be destroying edge.
7. ~~**`scripts/falsification_report.py`**~~ — **DONE, 2026-08-30 (D-028).** §10.1's
   deciding row is **not met**: 3 of 5 in net R, 1 of 5 in gross. H3 falsified. The
   currency question was settled in advance by the pre-registration, which is the only
   reason the net column does not read as a pass. ~52 min at `--workers 5`.
8. ~~**`scripts/ablation_report.py`**~~ — **DONE, 2026-08-31 (D-029).** Every default
   stands; no row survives BH at q = 0.10, T4 closest at q = 0.102. **The prediction
   in this line was wrong**: 6 of 34 are still INERT and the time stop, break-even and
   trailing did *not* come alive on real bars. ~50 min at `--workers 5`.
9. Re-measure the condition-bindingness ranking (D-008 §4, D-009 §7) before trusting the
   TUNABLE/ABLATION split.

### Before the first real backtest

**The pre-registration is written and committed** — `docs/PRE_REGISTRATION.md`, D-018. It
closes D-016 §1 (the falsification row is judged in **both** gross and net R), declares
**`M = 9,600`** against `PARAMETERS.md`'s unreproducible 6,912, fixes the splits as a rule
rather than as invented dates, and defines `INCONCLUSIVE` as a verdict distinct from FAIL.
What is left:

1. **Stamp item 4's literal dates** from §4.1's rule at data acquisition, and commit them
   as an amendment **before the first run**. It changes no threshold, no grid and no
   decision rule — it is the one mechanical step the document schedules for itself.
2. **Re-derive `tp.min_target_rank` on real bars.** It was raised 2.0 → 5.0 in v1.1 and
   **selected by its outcome on the synthetic fixture** (D-019 §6), which is the basis
   §10.2 prohibits. It carries no evidence of being right. The honest treatment on real
   data is to re-register it from the observed rank distribution — a percentile stated in
   advance — rather than inherit this one. Until then, **no T2 figure is comparable with
   T1 or T4 for any purpose.** T3 is separately still dead (`tp.min_rr`) and remains a
   named new-registration trigger.
3. **Build `events.jsonl`** (SPEC 21.1). The engine currently holds trades and rejections
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
