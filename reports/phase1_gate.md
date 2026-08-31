# Phase 1 Gate Report

Generated 2026-08-25T14:38:43+00:00

- `config_hash`  `f1edd96dfeb459dfaae41db5eff8cf5b6c4c90885f17c420bec4fc837e004fc5`
- `dataset_hash` `9f8736c421b5d7b2db40836d014cd42b27816bf4a233d2cf3166e2c4f77c2b1b`
- tzdata `2026.3` — decides every historical DST transition, so it is
  part of the dataset identity, not an environment detail
- day boundary **UTC 00:00** (DECISION D-001)
- session source **M15**, price side **bid**

## Gate checks

| Check | Result | Detail |
|---|---|---|
| Test suite green | PASS | 126 passed in 14.94s |
| UTC trading day is always 24h (D-001) | PASS | [24.0] |
| H4 grid fixed at 00/04/08/12/16/20 UTC | PASS | [0, 4, 8, 12, 16, 20] |
| No Sunday stub D1 bar survives (D-001a) | PASS | 0 short D1 bars |
| Sunday stubs merged into Monday | PASS | 52 merged bars |
| Overlap is 3.5h / 4.5h only | PASS | {3.5: 241, 4.5: 20} |
| Widened overlap == DST desync date | PASS | 1:1 match |
| Source structurally clean | PASS | no duplicates/non-monotonic/invalid OHLC |
| No week-anchor violations | PASS | 51 weeks checked |
| No unexplained data gaps | PASS | 0 suspect |
| Every session window well formed | PASS | 1304 instances |

| Broker-candle reconciliation | **BLOCKED** | No broker connected — Q1/Q2. See below |

## Series built

| Timeframe | Bars | First | Last |
|---|---:|---|---|
| M1 | 375,840 | 2026-01-01T00:00:00+00:00 | 2027-01-01T00:00:00+00:00 |
| M15 | 25,056 | 2026-01-01T00:00:00+00:00 | 2027-01-01T00:00:00+00:00 |
| H1 | 6,264 | 2026-01-01T00:00:00+00:00 | 2027-01-01T00:00:00+00:00 |
| H4 | 1,618 | 2026-01-01T00:00:00+00:00 | 2027-01-01T00:00:00+00:00 |
| D1 | 261 | 2026-01-01T00:00:00+00:00 | 2027-01-01T00:00:00+00:00 |
| W1 | 52 | 2025-12-28T21:00:00+00:00 | 2026-12-26T00:00:00+00:00 |
| MN1 | 12 | 2026-01-01T00:00:00+00:00 | 2027-01-01T00:00:00+00:00 |

## Sessions

| Session | Instances | Closed | Incomplete | Forming |
|---|---:|---:|---:|---:|
| ASIA | 261 | 261 | 0 | 0 |
| ASIA_RANGE | 260 | 260 | 0 | 0 |
| LONDON | 261 | 261 | 0 | 0 |
| NEW_YORK | 261 | 261 | 0 | 0 |
| OVERLAP | 261 | 261 | 0 | 0 |

London/New York overlap durations: {3.5: 241, 4.5: 20} (20 desync days).

## What this report does NOT establish

The fixture is a **random walk**, not a market. It exercises the calendar, the
bucket arithmetic, the merge rule and the session engine — everything whose
correctness is a property of *time*, not of price. It says nothing about whether
any strategy works, and no strategy result may ever be produced from it.

**The broker-reconciliation half of the gate is genuinely blocked**, not skipped:
it compares our resampled H4/D1 against a broker's own candles to establish the
known, explained difference between them, and that requires a chosen broker (Q1)
and downloaded history (Q2). Until it is done, the *shape* of our timeframes is
verified but their agreement with a live venue is not.

## Verdict: PASS (2 of 3 gate items; third blocked)
