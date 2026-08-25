# Project State — pick-up point for a new session

Last updated: 2026-08-25, after Phase 9 and the H5 marginal-value study.

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
| **Phases complete** | 1, 5, 6, 7, 8, 9 |
| **Tests** | 309, all passing |
| **Commits** | 7, on `master` |
| **Python** | 3.14 in `.venv`; deps pinned in `requirements.txt` |

```bash
.venv/Scripts/python.exe -m pytest tests/          # 309 tests, ~20s
.venv/Scripts/python.exe scripts/phase9_report.py  # the Phase 9 funnel gate
.venv/Scripts/python.exe scripts/marginal_value_report.py  # the H5 study
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

**Phase 5 was built before 2–4 deliberately**: Monthly/Weekly/Daily analysis is the *same*
engine instantiated on other bar series (SPEC 7.1), so building it once at H4 makes 2–4
mostly configuration.

### Not started

Phases 2–4 (Monthly/Weekly/Daily bias), 10–17 (FVG lifecycle, order blocks, setup
assembly, entries, risk, backtest, charts, paper, live).

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

## 4. Module map

```
bot/config/     schema.py (every parameter, with FROZEN/ABLATION/TUNABLE in its
                description), loader.py (layering + config_hash), defaults.yaml
bot/data/       calendar.py, resample.py, quality.py, ingest.py, synthetic.py
bot/core/       bars.py, indicators.py, sessions.py, swings.py, structure.py,
                liquidity.py, sweeps.py, displacement.py, fvg.py, mss.py
bot/research/   sweep_study.py (H2), displacement_study.py (SPEC 10.6),
                funnel.py (SPEC 11.7), marginal_value.py (H5)
scripts/        build_dataset.py, phase{1,5,6,7,8,9}_report.py,
                marginal_value_report.py, regen_golden.py
tests/          309 tests + tests/golden/structure_h4.json
```

`bot/core/` is pure: no I/O, no clock, no broker. That is what makes the causality tests
cheap to run.

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

D-004 through D-010 record corrections and findings from each phase's implementation.

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

---

## 7. Four statistical lessons already learned the hard way

All four are the same mistake at different scales, and all four are now pinned by tests.

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

**A statistic not compared against what noise alone would produce is not a finding.**

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

`bot/data/synthetic.py` says so in its own docstring and is never used to produce a
strategy result.

---

## 9. What to do next

Phase 9 was the decision point and it did not stop the project. The H5 study that
followed it is built, validated and **open** — it cannot be answered on synthetic data,
and one of its findings is that part of it may not be answerable on real data either
(§3a). So the design stands and the next phases are the ones that turn an event into a
trade.

### Phase 10 → 13 → 14, building toward a backtest

FVG lifecycle (SPEC 12.2 — touch, PARTIAL, MITIGATED, INVALIDATED, EXPIRED, plus 12.3's
selection rule), order blocks, setup assembly, entries, risk, then the engine. The
alternative ordering — run the H5 marginal-value test first — has been taken, which is
why it is no longer listed here.

### Still blocking real results

**Q1 (broker/account currency) and Q2 (M1 or tick history).** Answered in principle by
D-003 — raw-spread ECN, Dukascopy tick + broker M1 — but no data has been downloaded and
no broker chosen. Until then:

- Phase 1's broker-candle reconciliation stays BLOCKED.
- Phase 9's gate stays PASS-on-projection.
- H5 stays open: on a random walk the true MSS vs CHoCH-not-MSS difference is zero by
  construction, so the study can only ever validate its own instrument.
- Every study runs on synthetic data and can only validate instruments, not measure edge.

**This is still the single highest-value action in the project.** Six engines and two
studies are built, and none of their numbers mean anything about markets yet.

### When real data lands, run these in this order

1. **`scripts/phase9_report.py`** — the funnel on real bars. The gate's PASS is currently
   a projection from a synthetic conversion rate; this replaces it with a measurement,
   and the ABLATION sensitivity in §3 means the verdict could genuinely go either way.
2. **`scripts/marginal_value_report.py`** — H5, for real. The instrument is validated and
   the power arithmetic re-computes itself from the real return variance, which is the
   number most likely to move.
3. Re-measure the condition-bindingness ranking (D-008 §4, D-009 §7) before trusting the
   TUNABLE/ABLATION split.

### Before Phase 14

Namespace ids (§6 item 10) — required before trades from different runs are pooled.

### When Phases 2–4 land

`bot/core/mss.py` takes the MTF gate as an injected predicate
(`gate: (Direction, bar) -> bool`), currently the always-pass control — which is
`bias.gate_mode = none`, the variant SPEC 7.5 says MUST be run anyway. Dropping a real
gate in needs no change to the engine, **and it can only reduce the MSS count**: every
Phase 9 number is an upper bound. Re-run `scripts/phase9_report.py` afterwards; the gate
passed with 152 against a floor of 120, so a gate rejecting more than a fifth of setups
puts the development set back under it — and would push H5 further out of reach at the
same time.

### After Phase 12, revisit H5

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
