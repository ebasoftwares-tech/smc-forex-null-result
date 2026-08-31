# Phase 5 Gate Report

Generated 2026-08-25T14:37:27+00:00

- `config_hash` `f1edd96dfeb459dfaae41db5eff8cf5b6c4c90885f17c420bec4fc837e004fc5`
- Fixture: synthetic year 2026, M15 -> H4, 1618 bars
- `swing.fractal_n[H4]` = 2, tie rule `leftmost`, price source `wick`
- `structure.break_confirmation` = `close`, `protected_on_bos` = `most_recent_low`

## Gate checks

| Check | Result | Detail |
|---|---|---|
| Test suite green | PASS | 126 passed in 16.73s |
| Golden file present and matching | PASS | tests/golden/structure_h4.json |
| Replay / prefix stability (60 cut points) | PASS | no emitted event revised |
| Incremental == batch | PASS | 188 events |
| Swing sequence strictly alternates | PASS | 364 swings |
| Confirmation lag is exactly N bars | PASS | N=2 |
| No level produces two break events | PASS | one break per level |

## Populations

Swings: **364** (224 per 1000 bars) — {'HIGH': 182, 'LOW': 182}

| Label | Count |
|---|---:|
| HH | 83 |
| HL | 87 |
| LH | 98 |
| LL | 94 |
| UNDEFINED | 2 |

Normalisation amendments: APPEND 364, REPLACE 21, REJECT 34.

| Structure event | Count |
|---|---:|
| BOS | 83 |
| CHOCH | 68 |
| INTERNAL_LIQUIDITY_GRAB | 37 |
| TREND_INITIALISED | 0 |

Final trend: **BEARISH**. H4 min-history floor met: **True** (1618 of 500 bars).

## Findings

**SPEC 6.2's label-based trend initialisation is nearly unreachable.** Across twelve
fixture years it fired **2** times; the first-break path resolved `UNDEFINED`
**10** times. Both define the trend within the first ~20 bars, so the choice
affects at most the first event of a dataset — a warm-up artefact, not a strategy
behaviour. Recorded so its rarity is not later mistaken for a bug (D-005).

**MSS is deliberately absent.** SPEC 6.6 defines it as CHoCH + sweep context +
displacement; neither exists before Phases 7 and 8. This engine emits the unfiltered
superset, which is what makes the marginal value of those filters measurable later.

## What this report does NOT establish

The fixture is a random walk. These counts prove the engine is deterministic, causal
and self-consistent; they say nothing about whether the structure it finds is
tradeable, and no strategy result may be produced from this data.

## Verdict: PASS
