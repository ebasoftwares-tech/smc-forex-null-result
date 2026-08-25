# SMC Forex Bot — Specification Repository

**Status: PHASES 1, 5 and 6 COMPLETE.** 160 tests green.

| Phase | Scope | Gate report |
|---|---|---|
| 1 | Data, UTC timeframe construction, session engine | `reports/phase1_gate.md` (11/11; broker reconciliation blocked on Q1/Q2) |
| 5 | H4 swings + market structure (BOS / CHoCH) | `reports/phase5_gate.md` (7/7) |
| 6 | Liquidity engine — sources, lifecycle, merge, rank | `reports/phase6_gate.md` (8/8; sweep-rate half deferred to Phase 7) |

Phase 5 was built before 2–4 deliberately: Monthly/Weekly/Daily analysis is the *same*
engine instantiated on other bar series (SPEC 7.1), so building it once at H4 makes 2–4
mostly configuration. Phases 2–4 and 7–17 are not started.

```bash
.venv/Scripts/python.exe -m pytest tests/             # 160 tests
.venv/Scripts/python.exe scripts/phase1_report.py     # Phase 1 gate report
.venv/Scripts/python.exe scripts/phase6_report.py     # Phase 5 gate report
.venv/Scripts/python.exe scripts/regen_golden.py      # only when a structure change is intended
```

This repository currently contains one thing: a complete, deterministic, backtestable
definition of a Smart Money Concepts (SMC) Forex trading system. Every concept that is
normally described visually ("price sweeps liquidity", "displacement", "change of
character") is here reduced to an arithmetic rule over OHLC bars, with a confirmation
time, a parameter set, an invalidation condition, and a test that proves the rule never
uses information from the future.

## Read in this order

| # | Document | What it settles |
|---|---|---|
| 1 | [docs/SMC_STRATEGY_SPECIFICATION_v1.0.md](docs/SMC_STRATEGY_SPECIFICATION_v1.0.md) | **The spec.** All 20 required definitions: bar semantics, timeframes, sessions, swings, structure, BOS/CHoCH/MSS, liquidity, sweeps, displacement, FVG, order blocks, entries, stops, targets, risk, invalidation. Mathematical rule + pseudocode + example + edge cases + parameters + backtest method for each. |
| 2 | [docs/STATE_MACHINE.md](docs/STATE_MACHINE.md) | The per-setup and global state machines: every state, event, guard, action, timeout and invalidation transition, as a table an implementation can be checked against line by line. |
| 3 | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Technology decision (Python + MT5, with a small MQL5 watchdog) with the reasoning and the rejected alternatives; module boundaries; data contracts; storage; determinism controls. |
| 4 | [docs/PARAMETERS.md](docs/PARAMETERS.md) | The complete parameter registry — 140 parameters, each classified **FROZEN**, **ABLATION-ONLY**, or **TUNABLE**. Only 8 are tunable. This classification is the single most important defence against overfitting in the project. |
| 5 | [docs/BACKTEST_PROTOCOL.md](docs/BACKTEST_PROTOCOL.md) | The scientific protocol: pre-registered hypotheses and acceptance thresholds, the falsification suite (including shuffled-liquidity and sequence-scramble controls), walk-forward design, Monte Carlo, multiple-testing correction, and the out-of-sample evaluation budget ledger. |
| 6 | [docs/DECISIONS.md](docs/DECISIONS.md) | **The decision log.** What was decided, what it bought, what it cost, and the two latent spec defects the decisions exposed. Read D-002's "what it costs" before reading any backtest result. |
| 7 | [docs/OPEN_QUESTIONS.md](docs/OPEN_QUESTIONS.md) | The 17 questions, now all answered. Retained as the record of what was asked and why. |

## What this project is trying to find out

The objective is not a profitable bot. The objective is a defensible answer to:

> Does the sequence *liquidity → sweep → change of character → displacement → entry*
> produce a positive expectancy on FX majors that survives out-of-sample testing,
> transaction costs, and multiple-testing correction — and if not, which link in that
> chain fails?

The protocol is built so that a negative answer is a legitimate, publishable result of the
project, and so that the temptation to tune until the equity curve looks right is
structurally blocked rather than merely discouraged (see the OOS budget ledger in
`docs/BACKTEST_PROTOCOL.md` §7).

## Non-negotiable invariants

These hold everywhere in the specification and any implementation is wrong if it breaks them:

1. **Causality.** Every value used in a decision at time `T` is derivable from bars whose
   close time is `≤ T`. Enforced by an automated replay test, not by review (spec §25).
2. **The sequence.** No entry may be taken between a sweep and a confirmed CHoCH/MSS. The
   state machine makes the illegal transition unrepresentable.
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
| **D-001** | Day boundary = **UTC 00:00** | H4 grid fixed at 00/04/08/12/16/20 UTC year-round. NY anchor is now an ablation |
| **D-002** | **H4 confirmation for every liquidity tier** | Minimum 8 hours from sweep to MSS. This is a **session-to-session swing model**, not the intraday London reversal the source material describes. Do not report it as the latter |
| D-003 | Remaining 15 questions took the recommended defaults | Entry model A is the pre-registered baseline; M1 intrabar resolution is available and mandatory |
