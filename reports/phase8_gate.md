# Phase 8 Gate Report

Generated 2026-08-25T17:21:18+00:00

- `config_hash` `4b909422c33e0a0ca2b0c6022a6ad16e81153fd0397cdd8cdf84fa402cc5c7b5`
- Fixture: 3 synthetic years (2024–2026), EURUSD
- **2,323 confirmed sweeps → 27,760 candidate displacement legs**

## Gate checks

| Check | Result | Detail |
|---|---|---|
| Test suite green | PASS | 227 passed in 8.93s |
| Threshold distribution reported | PASS | 27,760 candidate legs |
| Rejection rate per setting reported | PASS | 6 settings |
| Off setting rejects nothing | PASS | SPEC 10.4 |
| Rejection is monotone in the threshold | PASS | sanity |
| The filter actually rejects | PASS | pass rate 5.4% |
| FVGs detected in both directions | PASS | {'BULLISH': 341, 'BEARISH': 366} |
| Joint FVG ablation reported | PASS | SPEC 10.6 |

## Threshold distribution (gate item 1)

Distribution of `net / ATR` across every candidate leg following a confirmed sweep.

| Percentile | net/ATR |
|---|---:|
| p5 | 0.090 |
| p25 | 0.367 |
| p50 | 0.724 |
| p75 | 1.222 |
| p90 | 1.748 |
| p95 | 2.042 |
| p99 | 2.574 |

| Bucket | Legs | Share |
|---|---:|---:|
| 0.00–0.25 | 4,616 | 16.6% |
| 0.25–0.50 | 5,111 | 18.4% |
| 0.50–0.75 | 4,589 | 16.5% |
| 0.75–1.00 | 3,844 | 13.8% |
| 1.00–1.25 | 2,940 | 10.6% |
| 1.25–1.50 | 2,284 | 8.2% |
| 1.50–2.00 | 2,804 | 10.1%  ← default cut |
| 2.00–2.50 | 1,200 | 4.3% |
| 2.50+ | 372 | 1.3% |

**Verdict on the default: ARBITRARY — the 1.5 cut rejects 84% of legs, but the density decays smoothly through it with no shoulder or gap. Nothing in the data marks 1.5 as special; it is a choice, not a discovery, which is why it is TUNABLE under a plateau requirement (BACKTEST_PROTOCOL 5.5)**

This is the question SPEC 10.6 asks and it deserves a straight answer. The density
decays monotonically from zero; there is no shoulder, gap or local minimum near 1.5.
1.25 rejects 76%, 1.5 rejects 84%, 2.0 rejects 95% — a smooth progression with
nothing to distinguish the middle value. **1.5 is a choice, not a discovery.** That
is precisely why it is TUNABLE under a plateau requirement rather than FROZEN: the
data cannot justify it, so out-of-sample stability has to.

*(The first version of this check reported STRUCTURED, because a single histogram
bin rose by +11 counts against a Poisson standard deviation of 18 — 0.6 sigma. The
detector now requires a rise of 2 sigma, and `tests/test_displacement_study.py` pins
both that noise does not qualify and that a genuinely bimodal distribution does.)*

## Rejection rate per setting (gate item 2)

| `disp.min_leg_atr` | Legs rejected on `net` alone |
|---|---:|
| 0 | 0.0% |
| 1 | 65.4% |
| 1.25 | 76.0% |
| 1.5 | 84.2%  ← default |
| 2 | 94.3% |
| 2.5 | 98.7% |

## Which condition actually does the rejecting

| Condition | Legs it rejects |
|---|---:|
| BODY_RATIO | 90.8% |
| NET_TOO_SMALL | 84.2% |
| NO_FVG | 83.8% |
| DIRECTIONAL_BARS | 13.7% |

Overall pass rate: **5.38%** of candidate legs.

Counted independently, so they overlap and do not sum to the failure rate — which
is the point. **`BODY_RATIO` rejects more legs than `NET_TOO_SMALL` does.** SPEC 9.2
calls `max_penetration_atr` "the parameter most likely to matter" for sweeps and
SPEC 10 gives `min_leg_atr` the TUNABLE slot for displacement — but on this fixture
the binding constraint is the body/range ratio, which is only ABLATION.

Read that carefully before acting on it: a random walk has no sustained directional
drives, so body ratios are low **by construction**. Real displacement legs should
carry much higher body ratios, and the ranking may invert. What this establishes is
that the relative bindingness of the five conditions must be **re-measured on real
bars before the TUNABLE/ABLATION split is trusted**, not that the split is wrong.

## Joint ablation: `min_leg_atr` x `require_fvg` (SPEC 10.6)

| `min_leg_atr` | FVG off | FVG on | Cost of the FVG rule |
|---|---:|---:|---:|
| 0 | 8.7% | 5.7% | −3.1% |
| 1 | 8.6% | 5.7% | −2.9% |
| 1.25 | 8.2% | 5.6% | −2.6% |
| 1.5 | 7.3% | 5.1% | −2.2% |
| 2 | 4.2% | 3.3% | −1.0% |
| 2.5 | 1.4% | 1.2% | −0.2% |

SPEC 10.2 argues the FVG requirement is not an extra filter but *the same condition
expressed structurally*. The table supports that: its marginal cost shrinks as the
net threshold tightens — 3.6 points at 0 ATR, 0.3 points at 2.5 ATR — because a leg
large enough to clear a strict net threshold has usually already left a gap. They
must therefore be ablated **jointly**; testing them one at a time would credit each
with the other's work.

## FVG detection

| Direction | Count |
|---|---:|
| BEARISH | 366 |
| BULLISH | 341 |

Detection only. The SPEC 12.2 lifecycle — touch, PARTIAL, MITIGATED, INVALIDATED,
EXPIRED — and the 12.3 selection rule are Phase 10. Detection landed here because
`disp.require_fvg` defaults to **true**, and shipping Phase 8 with that switched off
would have made every rejection rate above describe a different filter than the one
that runs.

## What this report does NOT establish

Nothing about whether displacement predicts anything. This measures the *filter*:
how often it rejects, which condition binds, and whether the threshold is justified
by the data. Whether a displaced leg is worth trading is Phase 9's funnel and, after
that, the ablation suite on real bars.

## Verdict: PASS
