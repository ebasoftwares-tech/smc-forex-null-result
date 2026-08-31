# SMC Forex Bot — a pre-registered study, and its negative result

**Status: CONCLUDED, 2026-08-31. The result is a documented null.**

This repository asked one question and answered it:

> Does the sequence *liquidity → sweep → change of character → displacement → entry*
> produce a positive expectancy on FX majors that survives out-of-sample testing,
> transaction costs, and multiple-testing correction — and if not, which link in that
> chain fails?

**No. And the only link that measurably contributes is the CHoCH/displacement step, which
mostly recovers from a bad entry rather than finding a good one.** The full sequence
performs the same as entering at a random time.

> ### → **[docs/FINAL_RESULT.md](docs/FINAL_RESULT.md)** — the deliverable: the answer, the
> evidence, the unsupported assumptions, and what a successor would have to pre-register.

The objective was never a profitable bot. It was a defensible answer, and a negative one was
declared an accepted outcome before any data was seen (`BACKTEST_PROTOCOL.md` §10.2, D-003).
**There is no runnable bot here, by design rather than omission** — no live loop, no broker
adapter, no order placement. Phases 15-17 were never started, and under D-030 they are not
going to be.

## The result in one table

Every row is in-sample (2019-2022), on ten FX majors, real bars:

| | Result |
|---|---|
| Phase 9 funnel gate | **FAILS** on its development-set half — 97 MSS against a floor of 120 |
| H2 — confirmed sweeps carry directional information | **FALSIFIED.** `EQUIVALENT` at every horizon, on 28,004 sweeps |
| H3 — real liquidity levels matter | **FALSIFIED.** A randomly placed level book performs the same |
| H4 — the sequence matters | **Split.** The CHoCH step contributes; the sweep and the ordering do not |
| H5 — displacement filtering adds value | **EQUIVALENT** at h=1 and h=4 |
| FVG standalone directional edge | **EQUIVALENT** at every horizon, on 7,800 touches |
| §10.1's deciding falsification row | **NOT MET** — 3 of 5 controls beaten in net R, 1 of 5 in gross |
| In-sample expectancy | **−0.19 R**, CI spanning zero, 102 trades |
| Ablation matrix, 34 variants | Every default stands; nothing survives Benjamini-Hochberg at q = 0.10 |

**The out-of-sample budget is unspent.** 2023-2024 and 2025 have never been read, so the
result is in-sample and is reported as such — see `docs/FINAL_RESULT.md` §2 for why the
project stopped rather than spending it.

## Why this is a strong null rather than a weak one

- **`EQUIVALENT`, not `UNDERPOWERED`.** H3's interval sits *inside* the ±0.10 R margin — the
  project's own threshold for a tradable edge, fixed before any arm ran. That is evidence of
  absence, not absence of evidence, and the three-way verdict exists to keep the two apart.
- **The margin, the grid, `M` and the decision rules were committed in advance** (D-018,
  amended once mechanically at D-021). Nothing was chosen after seeing a result.
- **The one criterion that could have produced a false pass was closed in advance.** D-016 §1
  found on synthetic data that net R can be won on stop-width geometry alone, so the
  pre-registration required the falsification row to be judged in **both** gross and net R.
  On real bars, two of the three net-R "wins" turned out to be exactly that geometry. **Had
  that rule not been fixed beforehand, this project would have reported a partial success it
  did not have.**

## Read in this order

| # | Document | What it settles |
|---|---|---|
| 1 | [docs/FINAL_RESULT.md](docs/FINAL_RESULT.md) | **The result.** The answer, which link fails, the measured evidence, the unsupported assumptions mapped to spec sections, and what to test next as a new pre-registration. |
| 2 | [docs/STATE.md](docs/STATE.md) | Where the project is, what is decided, what must not be re-derived. Start here if you are picking the codebase up. |
| 3 | [docs/PRE_REGISTRATION.md](docs/PRE_REGISTRATION.md) | The hypotheses, thresholds, splits, TUNABLE grid, `M`, and decision rule — **committed before the first run**, blob `646ddfb6db70`. This is what makes the result checkable. |
| 4 | [docs/DECISIONS.md](docs/DECISIONS.md) | **The decision log**, D-001 … D-031. Every correction and finding in order. D-030 is the terminal decision; D-031 closes the last open component hypothesis after it. |
| 5 | [docs/SMC_STRATEGY_SPECIFICATION_v1.0.md](docs/SMC_STRATEGY_SPECIFICATION_v1.0.md) | **The spec.** Every concept normally described visually — "price sweeps liquidity", "displacement", "change of character" — reduced to an arithmetic rule over OHLC bars, with a confirmation time, a parameter set, an invalidation condition, and a test that proves it never uses information from the future. |
| 6 | [docs/BACKTEST_PROTOCOL.md](docs/BACKTEST_PROTOCOL.md) | The scientific protocol: falsification suite, walk-forward design, Monte Carlo, multiple-testing correction, the OOS budget ledger, and §10.2 — what the failure case must deliver. |
| 7 | [docs/PARAMETERS.md](docs/PARAMETERS.md) | The parameter registry — 140 parameters, each **FROZEN**, **ABLATION-ONLY** or **TUNABLE**. Only 8 are tunable. This classification is the project's main defence against overfitting. |
| 8 | [docs/STATE_MACHINE.md](docs/STATE_MACHINE.md) · [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [docs/OPEN_QUESTIONS.md](docs/OPEN_QUESTIONS.md) | The per-setup and global state machines; the technology decision and module boundaries; the 17 questions and their answers. |

## What was built

Phases 1 and 5-14. Each ends in a gate report that states what it does **not** establish.

| Phase | Scope | Gate report | Result |
|---|---|---|---|
| 1 | Data ingest, UTC timeframe construction, session engine | `reports/phase1_gate.md` | 11/11; broker reconciliation BLOCKED on Q1 |
| 5 | H4 swings + market structure (BOS / CHoCH) | `reports/phase5_gate.md` | 7/7 |
| 6 | Liquidity engine — sources, lifecycle, merge, rank | `reports/phase6_gate.md` | 8/8 |
| 7 | Sweep detection + forward-return study (H2) | `reports/phase7_gate.md` | 8/8 — **H2 falsified**, `EQUIVALENT` on 28,004 sweeps (D-031) |
| 8 | Displacement + FVG detection | `reports/phase8_gate.md` | 8/8 |
| 9 | CHoCH reference selection, MSS confirmation, **the funnel** | `reports/phase9_gate.md` | 9/10 — **the development-set half FAILS** (D-020) |
| 10 | FVG lifecycle, selection, standalone edge test | `reports/phase10_gate.md` | 10/10 — **no standalone FVG edge** (D-023) |
| 11 | Order Block bake-off — four definitions | `reports/phase11_gate.md` | 10/10 — four variants are worth **1.68 tests** (D-022) |
| 12 | Entry engine — five models, M1 fill resolution | `reports/phase12_gate.md` | 8/8 — the lookahead has a magnitude, two predictions failed (D-025) |
| 13 | Risk — stops S1-S4, the RR gate, sizing, limits | `reports/phase13_gate.md` | 8/8 — **6 of 10 symbols cannot be sized at all** (D-026) |
| 14 | Backtest engine — exits, costs, metrics, Monte Carlo | `reports/phase14_gate.md` | 10/10 — 102 trades, expectancy −0.19 R (D-027) |
| — | Falsification suite (protocol §6.3/§6.4) | `reports/falsification.md` | **§10.1's deciding row is not met** (D-028) |
| — | Ablation matrix (protocol §6.5) | `reports/ablation.md` | 34 variants, every default stands (D-029) |
| — | H5 study — MSS vs CHoCH-not-MSS (SPEC 6.9) | `reports/marginal_value.md` | **EQUIVALENT** at h=1 and h=4 (D-024) |

**Everything that produces a measurement is on real bars.** Phases 5, 6 and 8 are still the
synthetic fixture and Phase 1 is against a superseded `dataset_hash` — they establish that
the detectors are deterministic, causal and self-consistent, which holds on any input, and
those detectors were then exercised on real bars throughout Phases 9-14. Phase 7 was the one
gate below Phase 9 that carried a *measurement* rather than a check, and it is now real too
(D-031).

Phase 5 was built before 2-4 deliberately: Monthly/Weekly/Daily analysis is the *same* engine
instantiated on other bar series (SPEC 7.1). **Phases 2-4 and 15-17 were never started.**

## Reproducing it

10 symbols × 2019-2025 M1 from HistData, `dataset_hash 2a2bb029…`, bid side, day boundary
UTC 00:00. Every script keeps `--synthetic`, so the original instrument-validation runs still
reproduce; `bot/data/synthetic.py` is never used to produce a strategy result.

```bash
.venv/Scripts/python.exe -m pytest tests/                   # 666 tests, ~130s
.venv/Scripts/python.exe scripts/phase9_report.py           # the funnel gate             ~9 min
.venv/Scripts/python.exe scripts/phase14_report.py          # the backtest                ~9 min
.venv/Scripts/python.exe scripts/falsification_report.py    # protocol 6.3/6.4   --workers 5, ~52 min
.venv/Scripts/python.exe scripts/ablation_report.py         # protocol 6.5       --workers 5, ~50 min
.venv/Scripts/python.exe scripts/marginal_value_report.py   # the H5 study
.venv/Scripts/python.exe scripts/phase7_report.py           # H2, the sweep study     ~12 min
```

## Non-negotiable invariants

These held everywhere in the specification, and any implementation that breaks one is wrong:

1. **Causality.** Every value used in a decision at time `T` is derivable from bars whose
   close time is `≤ T`. Enforced by an automated replay test, not by review (spec §25).
2. **The sequence.** No entry may be taken between a sweep and a confirmed CHoCH/MSS. The
   state machine makes the illegal transition unrepresentable. *(The data says this sequence
   carries no information — but the engine enforced it faithfully, which is what makes the
   measurement worth anything.)*
3. **Anti-martingale.** Position size is a pure function of (equity, risk %, stop distance).
   Risk % may decrease after losses, never increase. Asserted by unit test.
4. **One source of truth for logic.** The live path and the backtest path execute the same
   strategy module. Anything re-implemented for speed must be proven equivalent by a
   golden-file test.
5. **Charts are rendered from the event log**, never by re-running the engine — so a chart
   can only ever show what the bot actually saw at the time.

## Decisions in force

| Id | Decision | Consequence to keep in mind |
|---|---|---|
| **D-030** | **The null is accepted — the terminal decision** | The strategy is not carried forward. Anything that reopens it is a **new pre-registration**, not an amendment, and must supersede D-030 explicitly |
| **D-002** | **H4 confirmation for every liquidity tier** | Minimum 8 hours from sweep to MSS. This is a **session-to-session swing model**, not the intraday London reversal the source material describes. Do not report it as the latter — and note it was never tested against its own counterfactual |
| **D-001** | Day boundary = **UTC 00:00** | H4 grid fixed at 00/04/08/12/16/20 UTC year-round. NY anchor is now an ablation |
| D-003 | Remaining 15 questions took the recommended defaults | Entry model A is the pre-registered baseline; M1 intrabar resolution is available and mandatory |
