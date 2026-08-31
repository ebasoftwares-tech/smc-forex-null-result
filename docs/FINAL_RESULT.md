# Final Result — a documented null

**Status: terminal. The project concluded on 2026-08-31 (D-030).**

This document is the deliverable. It states the question, the answer, which link in the
chain fails, the measured evidence for each claim, which specification assumptions the data
did not support, and what a successor project would have to pre-register.

It is written to `BACKTEST_PROTOCOL.md` §10.2 and `PRE_REGISTRATION.md` §7.5, which define
what the failure case must contain. Everything here is regenerable from `scripts/` against
the dataset named in §8; nothing in it is a summary of a summary.

---

## 1. The question, and the answer

> Does the sequence *liquidity → sweep → CHoCH/MSS → displacement → entry* produce a
> positive expectancy on FX majors that survives out-of-sample testing, transaction costs
> and multiple-testing correction — **and if not, which link fails?**

**No, and the link that carries what little signal there is, is the CHoCH/displacement
step — which mostly avoids a bad entry rather than finding a good one.**

The evidence, in one table. Every row is in-sample (2019-2022), on ten FX majors:

| | Result | Source |
|---|---|---|
| Phase 9 funnel gate | **FAILS** on its development-set half — 97 MSS against a floor of 120 | D-020 |
| H2 — confirmed sweeps carry directional information | **FALSIFIED.** `EQUIVALENT` at every horizon, on 28,004 sweeps | D-031 |
| H3 — real liquidity levels matter | **FALSIFIED.** A randomly placed level book performs the same | D-028 §2 |
| H4 — the sequence matters | **Split.** The CHoCH step contributes; the sweep and the ordering do not | D-028 §3 |
| H5 — displacement filtering adds value | **EQUIVALENT** at h=1 and h=4; underpowered at h=12 | D-024 |
| FVG standalone directional edge | **EQUIVALENT** at every horizon, on 7,800 touches | D-023 |
| §10.1's deciding falsification row | **NOT MET** — 3 of 5 controls beaten in net R, 1 of 5 in gross | D-028 §1 |
| In-sample expectancy | **−0.19 R**, CI spanning zero, 102 trades | D-027 |
| Ablation matrix, 34 variants | Every default stands; nothing survives Benjamini-Hochberg at q = 0.10 | D-029 |

---

## 2. What verdict this is, formally

**No verdict was rendered under `PRE_REGISTRATION.md` §7.** All three of its outcomes —
PASS (§7.2), FAIL (§7.3), INCONCLUSIVE (§7.4) — are defined over the *out-of-sample* split,
and no out-of-sample evaluation was performed. The pre-registration's own §9 budget is
unspent: 2023-2024 and 2025 have never been read.

The project stopped before that point, deliberately (D-030). The reasoning is that a
strategy which fails §10.1's deciding row in-sample, whose own expectancy is not
distinguishable from zero, and whose component hypotheses came back negative wherever they
could be answered at all (§3.1), does not have an out-of-sample question worth the budget —
a pass would confirm an in-sample result that already fails.

If a §7 label is insisted upon, the one that applies is **INCONCLUSIVE by its first clause**
— fewer than 200 out-of-sample trades — and that clause is satisfied for want of looking,
not for want of an effect. It is recorded that way rather than as FAIL because §7.4 exists
precisely to stop this project claiming knowledge its sample does not contain.

**What is *not* inconclusive is the component evidence.** H3's interval sits inside the
±0.10 R margin declared before any arm ran, and the H2, FVG and H5 studies are `EQUIVALENT`
against a ±0.25 ATR margin declared the same way. `EQUIVALENT` is the only verdict in the
three-way scheme that licenses the word "no". Those are answers, in-sample, on real bars.

H2 is the strongest of them and worth separating: it resolves at **every** horizon on
28,004 events, against the 1,592 the widest horizon needed. Nothing in this project is
better powered, and nothing else here comes with an 18× surplus over its own requirement.

---

## 3. §10.2 item 1 — which component fails

### 3.1 The component hypotheses, which protocol §1.2 calls the actual deliverable

*"These are the deliverable even if H1 fails, because brief §33 asks which link breaks, not
whether the whole chain pays."* Their status at the close of the project:

| Id | Hypothesis | Status | Source |
|---|---|---|---|
| H2 | Confirmed sweeps carry directional information | **FALSIFIED.** `EQUIVALENT` at all four horizons, every interval inside the ±0.25 ATR margin, on 28,004 sweeps against matched controls | D-031 |
| H3 | Real liquidity levels matter | **FALSIFIED.** `EQUIVALENT` inside the ±0.10 R margin | D-028 §2 |
| H4 | The *sequence* matters | **SPLIT, and too coarse a hypothesis for what the data says.** The CHoCH requirement contributes; the sweep requirement and the ordering do not | D-028 §3 |
| H5 | Displacement filtering adds value | **FALSIFIED at h=1 and h=4**; underpowered at h=12 | D-024 |
| H6 | MTF bias filtering adds value | **BLOCKED, and always was.** The MTF gate is an injected always-pass predicate, which *is* `gate_mode = none` — the baseline already runs the control arm and there is no treatment arm. Needs Phases 2-4 and a new pre-registration | pre-reg §1.2 |
| H7 | Retracement entries beat market entries | **NOT SUPPORTED.** Against the shipped default (model C), models A, B and E are all `EQUIVALENT`; model D is the only `DIFFERENT` row and it is *worse* (−0.0203 R). Nothing survives BH correction | D-029 |

H6 is worth one extra line, because it silently conditions every count in this project:
since the gate always passes, **every MSS count reported anywhere here is an upper bound**,
and a real bias gate could only reduce it.

### 3.2 Which link fails

| Link in the chain | Verdict | Evidence |
|---|---|---|
| Liquidity identification | **Contributes nothing** | `shuffled_liquidity` is `EQUIVALENT` to the baseline in both currencies |
| The sweep requirement | **Contributes nothing**, on two independent measurements | `choch_only` — drop the sweep entirely — is `EQUIVALENT`, and nominally the highest gross expectancy of any arm (D-028); and the sweeps themselves carry no directional information, measured directly on 28,004 of them (H2, D-031) |
| The ordering of the sequence | **Contributes nothing in gross R** | `reversed_order` is `EQUIVALENT` in gross; its net-R "win" is stop geometry (§4.2) |
| The CHoCH / MSS step | **Contributes — the only link that does** | `sweep_only` is beaten in both currencies |
| — but read against the floor | it recovers from a bad entry rather than finding signal | entering at the sweep (−0.021) is **worse than random timing** (−0.010); the full sequence (+0.003) is level with random timing |
| Displacement filtering | **No measurable value** at h=1 and h=4 | D-024 |
| The FVG concept, standalone | **No directional edge** at any horizon | D-023 |
| The funnel itself | **Too thin to iterate on** | 97 development-set MSS against a floor of 120 |

The one-sentence version: **the only component with a demonstrable contribution earns it by
undoing the damage done by the component that precedes it, and the finished chain performs
the same as entering at a random time.**

---

## 4. §10.2 item 2 — why, with the measured evidence

### 4.1 The falsification suite (protocol §6.3/§6.4)

In-sample, 10 symbols × 2019-2022, 64,228 H4 bars, entry model A, baseline 1,616 setups.
`reports/falsification.md`, D-028.

| Arm | Tests | Gross E/setup | Net Δ | Gross verdict | Net verdict | Median SL (ATR) |
|---|---|---:|---:|---|---|---:|
| `baseline` | — | +0.003 | — | — | — | 2.20 |
| `shuffled_liquidity` | H3 | −0.000 | +0.004 | `EQUIVALENT` | `EQUIVALENT` | 2.12 |
| `sweep_only` | H4 | −0.021 | +0.064 | **DIFFERENT** | **DIFFERENT** | 0.88 |
| `choch_only` | H4 | +0.019 | −0.013 | `EQUIVALENT` | `EQUIVALENT` | 2.14 |
| `reversed_order` | H4 | −0.021 | +0.063 | `EQUIVALENT` | **DIFFERENT** | 0.85 |
| `random_time` | floor | −0.010 | +0.047 | `EQUIVALENT` | **DIFFERENT** | 0.95 |

The requirement is **every** control, in **both** currencies, each by a CI excluding zero.
The result is 3 of 5 in net R and 1 of 5 in gross. Only `sweep_only` clears in both.

H3's own interval is **+0.003 R per setup, CI [−0.010, +0.017]**, entirely inside the
±0.10 R margin, on 43,965 shuffled-arm setups against the baseline's 1,616. Power is not
the limitation.

### 4.2 Judging in both currencies is what prevented a false pass

`reversed_order` and `random_time` are `DIFFERENT` in net R and `EQUIVALENT` in gross.
Their median stops are 0.85 and 0.95 ATR against the baseline's 2.20 — a fixed spread costs
roughly twice as much per R against a stop half as wide, so an arm that enters earlier is
cost-inflated by geometry rather than beaten on signal.

D-016 §1 found this on synthetic data, where the true difference is zero by construction
and the baseline still cleared the net-R bar by +0.125 R, CI [0.019, 0.229]. The
pre-registration therefore fixed, in advance, that the row is judged in **both** currencies
(§7.1). On real bars **two of the three net-R wins turned out to be exactly that geometry.**

**Had that rule not been fixed beforehand, this project would have reported a partial
success it did not have.** This is the single most consequential thing the pre-registration
did.

### 4.3 The primary metric

`reports/phase14_gate.md`, D-027. In-sample, real M1 fill path.

| | Limits off | Limits on |
|---|---:|---:|
| Trades | **102** | 57 |
| Win rate | 30.4% | 33.3% |
| **Expectancy (R)** | **−0.1869** | −0.1306 |
| Total R | −19.06 | −7.44 |

Expectancy CI **[−0.423, +0.064] R** i.i.d., **[−0.382, +0.016] R** stationary block. Read
the block row; trades are not independent. **Both span zero** — neither evidence of edge
nor evidence against it, and 102 trades against protocol §5.1's floor of 200 means no
headline claim is made from this number in either direction.

The trade count is structural, not a matter of waiting for more history:

| Symbol | Trades |
|---|---:|
| AUDUSD | 38 |
| NZDUSD | 32 |
| EURUSD | 19 |
| GBPUSD | 13 |
| the other six | **0** |

Six symbols cannot be sized at all — every symbol whose quote currency is not the account
currency, blocked by SPEC 18.2's missing-FX-rate rule while Q1 is open (D-026). At this
funnel rate the four sizeable symbols would need roughly eight more years to reach 200; the
other six need a conversion series and nothing else.

### 4.4 The funnel (protocol §4.3, Phase 9)

`reports/phase9_gate.md`, D-020. Reported before any performance figure, as the protocol
requires — it says whether the strategy exists in enough quantity to be measured at all.

| Mode | Sweep→MSS | MSS / symbol-year | Universe (4y × 10) | Dev set (4y × 3) | Gate |
|---|---:|---:|---|---|---|
| `major` | 1.59% | 9.2 | **368** — clears ≥300 | **97** — misses ≥120 | **FAIL** |
| `micro` | 0.24% | 1.4 | 55 — misses | 16 — misses | **FAIL** |

The development set is the thin end of the universe by construction: EURUSD converts at
1.11%, the lowest of all ten majors, and the four best converters are all outside it.
Confirmed-sweep counts are nearly flat across the universe (2,138-2,399), so this is a
difference in conversion, not in raw material. **No registered parameter value rescues it** —
`choch.max_reference_distance_atr` spans the verdict, but its registered range
{2.0, 3.0, 4.0} yields 41 / 97 / 113 against a floor of 120.

### 4.5 The component studies

**H2 — do confirmed sweeps carry directional information** (`reports/phase7_gate.md`,
D-031), margin ±0.25 ATR, controls matched on session slot and volatility tercile. This is
SPEC 9.7's study, deliberately independent of the strategy: no CHoCH, no displacement, no
entry model, no stops:

| h | n sweep | n control | diff (ATR) | 95% CI | needed for the margin | Verdict |
|---:|---:|---:|---:|---|---:|---|
| +1 | 28,004 | 28,003 | −0.0085 | [−0.0211, +0.0038] | 150 | **EQUIVALENT** |
| +3 | 27,973 | 27,999 | −0.0192 | [−0.0397, +0.0010] | 383 | **EQUIVALENT** |
| +6 | 27,964 | 27,988 | −0.0149 | [−0.0441, +0.0145] | 782 | **EQUIVALENT** |
| +12 | 27,931 | 27,961 | −0.0427 | [−0.0851, +0.0003] | 1,592 | **EQUIVALENT** |

The widest interval spans 0.085 ATR against a 0.5 ATR-wide acceptance band. Pooled by
concatenating raw returns across all ten symbols, never by averaging per-symbol effect
sizes — no single symbol's interval comes near the margin, which is why the pooled sample
exists.

**H5 — displacement filtering** (`reports/marginal_value.md`, D-024), margin ±0.25 ATR:

| h | n MSS | diff (ATR) | 95% CI | MDE | Verdict |
|---:|---:|---:|---|---:|---|
| +1 | 326 | −0.026 | [−0.117, +0.064] | 0.134 | **EQUIVALENT** |
| +4 | 326 | +0.017 | [−0.134, +0.168] | 0.232 | **EQUIVALENT** |
| +12 | 325 | −0.142 | [−0.417, +0.143] | 0.428 | UNDERPOWERED |

**FVG standalone** (`reports/phase10_gate.md`, D-023), same margin, control matched on
session slot and ATR tercile:

| h | n touch | diff (ATR) | 95% CI | MDE | Verdict |
|---:|---:|---:|---|---:|---|
| 1 | 7,800 | +0.0111 | [−0.0126, +0.0347] | 0.034 | **EQUIVALENT** |
| 3 | 7,796 | +0.0307 | [−0.0080, +0.0685] | 0.055 | **EQUIVALENT** |
| 6 | 7,793 | +0.0342 | [−0.0194, +0.0885] | 0.079 | **EQUIVALENT** |
| 12 | 7,788 | +0.0251 | [−0.0535, +0.1048] | 0.112 | **EQUIVALENT** |

The positive control detects an injected +0.05 ATR — a fifth of the margin — and the study
saw nothing. Note what this does *not* condemn: the strategy never claims a gap predicts
direction (`disp.require_fvg` uses one as structural evidence that displacement occurred,
SPEC 10.2; entry model C uses one as a price to bid at). Those uses are Phase 12's to
evaluate, and this result neither clears nor condemns them.

### 4.6 The ablation matrix (protocol §6.5)

`reports/ablation.md`, D-029. Baseline 1,616 setups, 267 filled, E/setup −0.0118 R. Three
of 34 rows clear the raw rule; **none survives Benjamini-Hochberg at q = 0.10**:

| Row | Delta (net R) | 95% CI | p | BH q |
|---|---:|---|---:|---:|
| `tp.model = T4` | +0.0188 | [0.0044, 0.0340] | 0.003 | **0.102** |
| `entry.model = D` | −0.0203 | [−0.0401, −0.0013] | 0.018 | 0.306 |
| `manage.be_trigger_r = 1.5` | −0.0023 | [−0.0055, −0.0000] | 0.082 | 0.467 |

T4 misses by 0.002 — one try out of 34, on a single dataset, at a raw p of 0.003 that looks
compelling in isolation. The correction is applied and the default stands. **Nothing here
licenses switching to T4**: the delta is measured against a baseline whose own expectancy is
not distinguishable from zero.

Two structural findings survive the change of dataset because they are facts about the
codebase rather than the market: **6 of 34 variants are still INERT** — no trade lives long
enough for a time stop or a trail to bind, which is a fact about the exit policy — and three
of §6.5's components are unimplemented, so **D-002 has never been tested against its own
named counterfactual**.

---

## 5. §10.2 item 3 — which assumptions were unsupported

Mapped to the specification section that made each one.

| Assumption | Where it is stated | What the data says |
|---|---|---|
| Identified liquidity levels are where price is drawn, and identifying them correctly matters | SPEC §8 (esp. 8.1, 8.3, 8.6, 8.8) | **Unsupported.** A shuffled level book performs the same (H3, `EQUIVALENT`) |
| A sweep of liquidity is the event that starts a setup | SPEC §9; invariant 2 | **Unsupported, and measured directly.** A confirmed sweep does not move the next 1-12 H4 bars in its own direction (H2, `EQUIVALENT` on 28,004 events). Removing the requirement also costs nothing measurable, and entering *at* the sweep is worse than random timing |
| The *order* liquidity → sweep → CHoCH carries information | SPEC §14, §20; invariant 2 | **Unsupported in gross R.** Reversing it is `EQUIVALENT` |
| Displacement filtering separates good MSS from bad | SPEC §10, and SPEC 6.9's H5 | **Unsupported** at h=1 and h=4 |
| An unmitigated FVG marks a directional imbalance | SPEC §12 | **Unsupported** at every horizon — though the strategy does not use it this way (§4.5) |
| The funnel yields enough events to iterate on | SPEC §11, §27; protocol §4.3 | **Unsupported on the development set** (97 vs 120), and no registered parameter reaches it |
| Four order-block definitions are meaningfully distinct | SPEC §13 | **Unsupported at the shipped configuration** — B, C and D are identical to the default, because nothing consumes an order block (D-029 §2) |
| Every liquidity tier deserves H4 confirmation | D-002, over SPEC §8.6 / §11 | **Untested.** `liq.tier_confirmation_tf` was never implemented, so the counterfactual D-002 named cannot be run |
| Every symbol can carry a sized trade | SPEC §18.2 | **False in practice.** 6 of 10 cannot, for want of an FX conversion series |

D-002 deserves its own line, because it is the assumption most likely to explain the whole
result: requiring H4 confirmation for *every* tier puts a minimum of eight hours between
sweep and MSS. **What was tested is a session-to-session swing model, not the intraday
reversal the source material describes.** That was a deliberate, documented choice made
before any data was seen — and it was never tested against its alternative.

---

## 6. §10.2 item 4 — what to test next, as a new pre-registration

Nothing here is scheduled. Under D-030 §5, each of these is a **new pre-registration that
must supersede D-030 explicitly** — not an amendment, and not a continuation. Everything in
this document then becomes prior work, reported as such.

Ordered by what the evidence actually points at:

1. **A lower confirmation timeframe throughout.** The single assumption most likely to be
   responsible (§5, D-002). It is a different strategy, not a tuned one, and needs its own
   registration and its own funnel gate.
2. **A mean-reversion reformulation with the SMC framing dropped.** Protocol §6.3 names this
   as the consequence of exactly the verdict H3 returned. It is now supported by an
   `EQUIVALENT` result on real data rather than by a fixture-guaranteed null.
3. **Resolve Q1 — the broker and FX conversion series — before anything else is measured.**
   It caps the in-sample book at 102 trades against a floor of 200, blocks the
   cross-sectional criterion outright, and leaves every cost figure a declared default. No
   successor reaches a headline claim without it.
4. **Re-choose or re-state the development set.** Three separate studies resolved on the
   pooled universe and failed to resolve on the three development symbols (rule 78). A
   successor either iterates on a set that can answer, or stops claiming the development set
   answers anything. Note that swapping it *after* seeing these results selects a split by
   its outcome, so it must be registered in advance and justified on conversion rate, not on
   result.
5. **Re-derive `tp.min_target_rank` from an observed distribution.** It was raised 2.0 → 5.0
   and selected by its outcome on the synthetic fixture (D-019 §6) — the basis §10.2
   prohibits. Until it is re-registered as a percentile stated in advance, no T2 figure is
   comparable with T1 or T4 for any purpose.
6. **A different asset class where session structure is sharper** — protocol §10.2's own
   suggestion (index futures). The furthest from what exists here, and the least supported by
   anything measured.

Two things a successor must not do, both prohibited by §10.2 on any outcome: report the best
in-sample configuration as the result (T4 at q = 0.102 is the specific temptation), and widen
the TUNABLE grid or promote a FROZEN parameter to reach the funnel gate
(`choch.max_reference_distance_atr` at 6.0 is the specific temptation — it reaches 120
exactly, and it is outside the registered range).

---

## 7. What this result does and does not claim

**It does not claim that SMC concepts cannot work.** It claims that *this specification* of
them — these frozen defaults, this entry/stop/target configuration, H4 confirmation for
every tier — produces no measurable edge on ten FX majors over 2019-2022.

**It does not claim confirmation out of sample.** 2023-2024 and 2025 were never read and
stay unspent. The result is in-sample and is reported as such.

**It does not claim the measurement was as strong as it could be.** Two known limits: only
4 of 10 symbols could be sized (D-026), so the book is 102 trades against a floor of 200;
and the development set could not resolve any of three separate studies (rule 78).

**It does not claim the engine is wrong.** Every phase gate passed, both controls in every
study passed, the M1 fill path agreed with the bar-level rule on 7,877 armed orders (D-025),
and every parallelised study was verified against its serial equivalent. **The instrument
works; the strategy does not.**

**Two specific things remain unmeasured**, and are named rather than buried:

- **Three detector-level gates were never re-run on real bars.** Phases 5, 6 and 8
  (`reports/phase5_gate.md`, `phase6_gate.md`, `phase8_gate.md`) are still the synthetic
  fixture, and `reports/phase1_gate.md` is against a superseded `dataset_hash` (`9f8736c4`),
  not the one every result above uses. What those gates establish is that the detectors are
  deterministic, causal and self-consistent — properties of the code, which hold on any
  input — and the detectors were then exercised on real bars throughout Phases 9-14 and
  every study here. **Phase 7 was the one that mattered and it is now real** (D-031): it
  carried the H2 forward-return study, which is a measurement rather than a gate, and
  leaving it on the fixture was the difference between an answered and an unanswered
  component hypothesis.
- **One cell in the falsification report is unverified** (D-028 §6): the claim that
  `choch.max_reference_distance_atr` "rejects nothing" is a fixture measurement carried into
  a real-bars report, and D-020 found that same parameter binding differently on real data.
- **Two of the three execution effects the fixture measured as exactly zero** — the S4 stop's
  movement at fill, and SPEC 17.5's intrabar ambiguity — were never re-measured on real bars.
  All three remain pinned by constructed tests.

---

## 8. Reproduction

Everything needed to check or contest this result is in the repository.

| | |
|---|---|
| **Pre-registration** | `docs/PRE_REGISTRATION.md` at v1.1 + Amendment 1, blob `646ddfb6db70d7964051680cb86ad468546a49b9` — committed before any arm ran |
| **Dataset** | 10 symbols × 2019-2025 M1, `dataset_hash 2a2bb0293052ae31bd2be73cfd53df25f6032f4db94da906543889e447d03ed9`, source HistData, bid side, tzdata 2026.3, day boundary UTC 00:00 |
| **Symbols** | AUDUSD, EURGBP, EURJPY, EURUSD, GBPJPY, GBPUSD, NZDUSD, USDCAD, USDCHF, USDJPY |
| **Splits** | in-sample 2019-2022 (every result here) · out-of-sample 2023-2024 (**unspent**) · holdout 2025 (**unspent**) |
| **Declared `M`** | 9,600 (pre-registration §5); measured `M_eff` 1.68 for the OB bake-off (D-022), 1.32 for the stop models (D-026) |
| **Margins** | ±0.10 R for the falsification and primary comparisons, ±0.25 ATR for the forward-return studies — both declared before any result was read |
| **Tests** | 666, green |
| **Decision log** | `docs/DECISIONS.md` D-001 … D-030, every correction and finding in order |

```bash
.venv/Scripts/python.exe -m pytest tests/                   # 666 tests, ~130s
.venv/Scripts/python.exe scripts/phase9_report.py           # the funnel gate             ~9 min
.venv/Scripts/python.exe scripts/phase14_report.py          # the backtest                ~9 min
.venv/Scripts/python.exe scripts/marginal_value_report.py   # H5
.venv/Scripts/python.exe scripts/phase10_report.py          # the FVG edge test
.venv/Scripts/python.exe scripts/phase7_report.py           # H2, the sweep study     ~12 min
.venv/Scripts/python.exe scripts/falsification_report.py    # protocol 6.3/6.4   --workers 5, ~52 min
.venv/Scripts/python.exe scripts/ablation_report.py         # protocol 6.5       --workers 5, ~50 min
```

Every script keeps `--synthetic`, so the original instrument-validation runs still reproduce.
`bot/data/synthetic.py` is never used to produce a strategy result.

**A reader who disagrees with this conclusion has the pre-registration, the code, the data
hash and the reports, and can check it.**

---

## 9. Change control

Reopening this requires a **new pre-registration**, not an amendment — specifically: revising
a component in the light of §3's table, spending out-of-sample budget, or re-running any
study with a changed parameter. Each supersedes D-030 and must say so.

The pre-registration itself is untouched and stays valid. Its own rule — nothing may be
changed after the first out-of-sample evaluation — is not engaged, because there has not been
one.
