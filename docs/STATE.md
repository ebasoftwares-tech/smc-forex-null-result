# Project State — pick-up point for a new session

Last updated: 2026-08-25, after Phase 9.

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
| **Tests** | 275, all passing |
| **Commits** | 6, one per phase, on `master` |
| **Python** | 3.14 in `.venv`; deps pinned in `requirements.txt` |

```bash
.venv/Scripts/python.exe -m pytest tests/          # 275 tests, ~13s
.venv/Scripts/python.exe scripts/phase9_report.py  # regenerate the latest gate report
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

## 4. Module map

```
bot/config/     schema.py (every parameter, with FROZEN/ABLATION/TUNABLE in its
                description), loader.py (layering + config_hash), defaults.yaml
bot/data/       calendar.py, resample.py, quality.py, ingest.py, synthetic.py
bot/core/       bars.py, indicators.py, sessions.py, swings.py, structure.py,
                liquidity.py, sweeps.py, displacement.py, fvg.py, mss.py
bot/research/   sweep_study.py (H2), displacement_study.py (SPEC 10.6),
                funnel.py (SPEC 11.7)
scripts/        build_dataset.py, phase{1,5,6,7,8,9}_report.py, regen_golden.py
tests/          275 tests + tests/golden/structure_h4.json
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

D-004 through D-009 record corrections and findings from each phase's implementation.

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

---

## 7. Three statistical lessons already learned the hard way

All three are the same mistake at different scales, and all three are now pinned by tests.

- **Phase 7:** 3 of 20 year×horizon significance tests fired on a random walk (≈1
  expected). The multiple-testing problem on data whose true effect is zero *by
  construction*. This is the concrete argument for `BACKTEST_PROTOCOL.md` §5.6.
- **Phase 8:** the "natural break" detector reported STRUCTURED because one histogram bin
  rose +11 counts against a Poisson SD of 18 — 0.6σ. Now requires 2σ.
- **Phase 9:** two of the eight TUNABLE parameters have now been measured as *not* the
  parameter that decides the outcome, while an ABLATION parameter spans the gate verdict.
  A classification written down in advance is a hypothesis about which knob matters, and
  it is being falsified twice.

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

`bot/data/synthetic.py` says so in its own docstring and is never used to produce a
strategy result.

---

## 9. What to do next

Phase 9 was the decision point and it did not stop the project, so the design stands and
the next phases are the ones that turn an event into a trade.

### The choice to make first

Two orderings are defensible and they answer different questions:

- **Phase 10 → 13 → 14 (build toward a backtest).** FVG lifecycle, order blocks, setup
  assembly, entries, risk, then the engine. Fastest route to an equity curve.
- **SPEC 6.9's marginal-value test first.** Forward returns for (a) all CHoCH, (b) MSS
  only, (c) CHoCH-not-MSS. The population is already retained (477 events on the fixture)
  and `bot/research/sweep_study.py` is the template — it is a small study, not a phase.

**The second is worth doing first, and on real data it is close to decisive**: if MSS and
CHoCH-not-MSS are statistically indistinguishable, the sweep-plus-displacement requirement
adds nothing, which is the central claim of the whole methodology, and `BACKTEST_PROTOCOL.md`
§6.2 says that is a headline finding rather than a reason to re-tune. Building five more
phases before asking it risks answering it after the entry engine has been written around
the assumption.

On synthetic data it can only return "no difference", so it is worth doing **when Q1/Q2
land**, not before.

### Still blocking real results

**Q1 (broker/account currency) and Q2 (M1 or tick history).** Answered in principle by
D-003 — raw-spread ECN, Dukascopy tick + broker M1 — but no data has been downloaded and
no broker chosen. Until then:

- Phase 1's broker-candle reconciliation stays BLOCKED.
- Phase 9's gate stays PASS-on-projection.
- Every study runs on synthetic data and can only validate instruments, not measure edge.

**This is now the single highest-value action in the project.** Five engines are built and
none of their numbers mean anything about markets yet.

### Before Phase 14

Namespace ids (item 10 above) and re-measure the condition-bindingness ranking on real
bars (D-008 §4, D-009 §7) before trusting the TUNABLE/ABLATION split.

### When Phases 2–4 land

`bot/core/mss.py` takes the MTF gate as an injected predicate
(`gate: (Direction, bar) -> bool`), currently the always-pass control — which is
`bias.gate_mode = none`, the variant SPEC 7.5 says MUST be run anyway. Dropping a real
gate in needs no change to the engine, **and it can only reduce the MSS count**: every
Phase 9 number is an upper bound. Re-run `scripts/phase9_report.py` afterwards; the gate
was passed with 152 against a floor of 120, so a gate that rejects more than a fifth of
setups puts the development set back under it.

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
