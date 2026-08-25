# Project State — pick-up point for a new session

Last updated: 2026-08-25, after Phase 8.

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
| **Phases complete** | 1, 5, 6, 7, 8 |
| **Tests** | 227, all passing |
| **Commits** | 5, one per phase, on `master` |
| **Python** | 3.14 in `.venv`; deps pinned in `requirements.txt` |

```bash
.venv/Scripts/python.exe -m pytest tests/          # 227 tests, ~9s
.venv/Scripts/python.exe scripts/phase8_report.py  # regenerate the latest gate report
```

### Phases built

| Phase | Scope | Gate report | Result |
|---|---|---|---|
| 1 | Data ingest, UTC timeframe construction, session engine | `reports/phase1_gate.md` | 11/11; broker reconciliation **BLOCKED** on Q1/Q2 |
| 5 | H4 swing detection + market structure (BOS/CHoCH) | `reports/phase5_gate.md` | 7/7 |
| 6 | Liquidity engine — sources, lifecycle, merge, rank | `reports/phase6_gate.md` | 8/8; sweep-rate half deferred to 7 |
| 7 | Sweep detection + forward-return study (H2) | `reports/phase7_gate.md` | 7/7; closed Phase 6's deferred half |
| 8 | Displacement + FVG detection | `reports/phase8_gate.md` | 8/8 |

**Phase 5 was built before 2–4 deliberately**: Monthly/Weekly/Daily analysis is the *same*
engine instantiated on other bar series (SPEC 7.1), so building it once at H4 makes 2–4
mostly configuration.

### Not started

Phases 2–4 (Monthly/Weekly/Daily bias), 9–17 (CHoCH/MSS, FVG lifecycle, order blocks,
entries, risk, backtest, charts, paper, live).

---

## 3. Module map

```
bot/config/     schema.py (every parameter, with FROZEN/ABLATION/TUNABLE in its
                description), loader.py (layering + config_hash), defaults.yaml
bot/data/       calendar.py, resample.py, quality.py, ingest.py, synthetic.py
bot/core/       bars.py, indicators.py, sessions.py, swings.py, structure.py,
                liquidity.py, sweeps.py, displacement.py, fvg.py
bot/research/   sweep_study.py (H2), displacement_study.py (SPEC 10.6)
scripts/        build_dataset.py, phase{1,5,6,7,8}_report.py, regen_golden.py
tests/          227 tests + tests/golden/structure_h4.json
```

`bot/core/` is pure: no I/O, no clock, no broker. That is what makes the causality tests
cheap to run.

---

## 4. Decisions in force

Full reasoning in `DECISIONS.md`. The two that shape everything:

- **D-001 — day boundary is UTC 00:00.** H4 grid fixed at 00/04/08/12/16/20 UTC
  year-round. NY anchor is an ablation. Forced the Sunday stub-bar merge (D-001a).
- **D-002 — H4 confirmation for every liquidity tier.** Minimum **8 hours** from sweep to
  MSS. **This is a session-to-session swing model, not the intraday London reversal the
  SMC source material describes.** Never report it as the latter.

D-004 through D-008 record corrections and findings from each phase's implementation.

---

## 5. Things a new session must not re-derive

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

---

## 6. Two statistical lessons already learned the hard way

Both are the same mistake at different scales, and both are now pinned by tests.

- **Phase 7:** 3 of 20 year×horizon significance tests fired on a random walk (≈1
  expected). The multiple-testing problem on data whose true effect is zero *by
  construction*. This is the concrete argument for `BACKTEST_PROTOCOL.md` §5.6.
- **Phase 8:** the "natural break" detector reported STRUCTURED because one histogram bin
  rose +11 counts against a Poisson SD of 18 — 0.6σ. Now requires 2σ.

**A statistic not compared against what noise alone would produce is not a finding.**

Every study module carries a **positive control** as well as a null result. A study that
can only ever say "no edge" would pass the random-walk fixture and be worthless.

---

## 7. The honest limitation

**Every number produced so far comes from a synthetic random walk.** It has no liquidity,
no participants and no structure, so:

- Sweep counts, rates, distributions and rejection rates are properties of *the detectors
  meeting noise*. They prove the engines are deterministic, causal and self-consistent.
- The forward-return study correctly finds **nothing**, at every horizon. Had it found an
  edge, the study would be broken.
- **H2 is neither supported nor refuted.** It cannot be, on this data.

`bot/data/synthetic.py` says so in its own docstring and is never used to produce a
strategy result.

---

## 8. What to do next

### Immediate: Phase 9 — CHoCH/MSS (SPEC 11)

**This is the project's decision point.** Its gate is the funnel report:

> ≥ 300 MSS events across the universe **and ≥ 120 on the three development symbols**
> over the in-sample period, or the design is reconsidered before any entry code is
> written.

(The second half was added by D-002 — a universe-wide count can hide a development set
too thin to iterate on.)

Everything built so far feeds that one number. Phase 9 needs:

- CHoCH reference selection, `major` and `micro` modes (SPEC 11.1) — these are **two
  different strategies**, both pre-registered, not a parameter sweep.
- `choch.max_bars_after_sweep` (12) and the `min_bars_after_sweep` WAIT (1, FROZEN).
- MSS = CHoCH + sweep context + displacement + reference validity (SPEC 11.5), including
  the `NEW_EXTREME` clause that is easy to omit.
- The funnel report: levels → in-play → sweeps triggered → confirmed → reference found →
  CHoCH → MSS.

`bot/core/displacement.evaluate(series, sweep_extreme_bar, break_bar, direction, cfg,
fvgs, atr)` is the entry point Phase 9 calls.

### Still blocking real results

**Q1 (broker/account currency) and Q2 (M1 or tick history).** Answered in principle by
D-003 — raw-spread ECN, Dukascopy tick + broker M1 — but no data has been downloaded and
no broker chosen. Until then:

- Phase 1's broker-candle reconciliation stays BLOCKED.
- Every study runs on synthetic data and can only validate instruments, not measure edge.

### Before Phase 14

Namespace ids (item 10 above) and re-measure the condition-bindingness ranking on real
bars (D-008 §4) before trusting the TUNABLE/ABLATION split.

---

## 9. Working conventions

- One commit per phase, straight to `master`, with the findings summarised in the message.
- Every phase ends with a `scripts/phaseN_report.py` writing `reports/phaseN_gate.md`, and
  the report states what it does **not** establish.
- Corrections and findings go in `DECISIONS.md` as a new `D-00N` entry; spec text is
  amended in place with a note pointing at the decision.
- A gate item that cannot be evaluated is reported **BLOCKED** or **DEFERRED**, never
  quietly skipped.
