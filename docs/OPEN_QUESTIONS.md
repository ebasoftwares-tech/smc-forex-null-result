# Open Questions — ALL ANSWERED 2026-08-25

> **Status: CLOSED.** Elie answered "defaults are fine except Q3 (use UTC) and Q7 (H4 only)".
> The decisions and their consequences are recorded in [DECISIONS.md](DECISIONS.md); the spec
> has been updated. This file is retained as the record of what was asked and why.
>
> | | Answer | Record |
> |---|---|---|
> | **Q3** | `tf.day_boundary = UTC 00:00` — **against the recommendation** | D-001, D-001a |
> | **Q7** | H4 confirmation for every tier — **against the recommendation** | D-002, D-002a |
> | All others | Recommended defaults accepted | D-003 |
>
> Each answer against the recommendation exposed a latent defect in v1.0 of the spec (the
> Sunday stub D1 bar; the level-age rule). Both are fixed. Both were present under the
> recommended defaults too — the answers surfaced them rather than caused them.

Each item lists a **recommended default** so the whole set can be resolved with "defaults are
fine" plus corrections. Items marked **BLOCKING** change the architecture or the data pipeline,
not just a constant.

---

## Data and platform

**Q1 — Broker, account currency, and account type. (BLOCKING)**
Which broker and MT5 server? Account currency? Raw-spread-plus-commission or
standard-spread? This determines pip-value conversion (§18.2), the cost model (§26), symbol
specifications, and the swap table. Every backtest cost figure is broker-specific and cannot
be finalised without it.
*Recommendation:* a raw-spread ECN account in USD or EUR; costs modelled as spread +
$3.5/lot/side until the real account is known.

**Q2 — M1 or tick history availability, 3–5 years. (BLOCKING)**
Without M1 data the backtest cannot resolve intrabar path and must use the pessimistic
assumption (§17.5), which biases every result by an unquantified amount. Options: broker M1
export (usually 1–3 years, broker-specific), Dukascopy tick (free, complete, needs a
downloader), or paid (Tickstory / TickData).
*Recommendation:* Dukascopy tick for research, broker M1 for the live-matching set, reconciled
per §2 of the protocol. Confirm whether the download and storage (~20–60 GB) is acceptable.

**Q3 — Day boundary: New York midnight or UTC midnight? (BLOCKING)**
This determines where every H4, D1 and W1 bar is cut, and therefore every swing, level, sweep
and CHoCH in the system (§2.2).
*Recommendation:* NY midnight as default (it is the convention the methodology comes from),
with UTC as a full parallel ablation run rather than a parameter sweep.
**ANSWERED: UTC.** The NY anchor becomes the ablation. See D-001.

**Q10 — Data storage and compute budget.**
A full walk-forward across 10 symbols and the declared grid is on the order of 10⁴ backtests.
On one desktop that is hours to days per full sweep.
*Recommendation:* local machine, Parquet, results cached by `run_id`. Cloud only if the
walk-forward becomes the bottleneck.

---

## Strategy scope

**Q4 — Account size and target risk.**
Drives lot granularity (§18.2's `min_realised_fraction` rejection): below roughly €5,000, a
0.35% risk on a 25-pip stop rounds badly and a meaningful share of setups will be rejected for
under-risk.
*Recommendation:* state the intended live account size now so the rejection rate can be
measured in the backtest rather than discovered live.

**Q5 — Symbol universe.**
Confirm the 10 in §1.4. Adding metals or indices means a separate parameter study, not a
larger list.
*Recommendation:* the 10 as specified; develop on 3, validate on 7.

**Q7 — May session (tier-3) liquidity confirm its CHoCH on H1, or is H4-only absolute?
(BLOCKING)**
This is the §0.4(a) tension. H4-only is closer to the brief's letter and will produce
substantially fewer setups — possibly too few to test (see the Phase 9 gate). The tier map
lets session liquidity confirm on H1 while keeping H4 as the setup timeframe for everything
else.
*Recommendation:* the tier map as default, with an "all-H4" run as a mandatory ablation, so
the decision is made on the funnel numbers rather than in advance.
**ANSWERED: H4 only.** The tier map becomes the ablation. See D-002 — and read its
"what it costs" section, because the flagship setup changes character.

**Q6 — Which entry model is the primary?**
Five models (§15.2) is five strategies. Testing all five is right; **shipping** all five is not.
*Recommendation:* pre-register model A (market on MSS) as the baseline — it has a 100% fill
rate and therefore the largest sample and the cleanest statistics — and treat B–E as
challengers that must beat A on per-**setup** expectancy (§4.4), not on win rate.

**Q8 — Directional restriction?**
Long-only, short-only, or both? Both is assumed. Some FX pairs have a persistent carry drift
that shows up as an asymmetry in results.
*Recommendation:* both, with the long/short breakdown reported as a first-class dimension.

**Q11 — Round numbers as a liquidity source (v1.1)?**
Deliberately excluded from v1.0 (§8.3) to keep the level population bounded. It is the single
most plausible addition.
*Recommendation:* defer. Revisit only after the §6.3 shuffled-liquidity control has been run —
if that control shows liquidity identification adds nothing, adding a source is pointless.

---

## Execution and operations

**Q9 — MQL5 watchdog: build it, or accept the risk?**
~200 lines (`ARCHITECTURE.md` §5.3), protecting against a Python crash with an open position.
*Recommendation:* build it before Phase 17, not before Phase 16 — paper trading does not need
it and it would delay the phase that produces the most information.

**Q12 — Hosting: desktop or VPS?**
The H4/H1 cadence means downtime of minutes is tolerable, but the MT5 terminal must be running
at every bar close, including overnight.
*Recommendation:* a Windows VPS near the broker's servers for live; the desktop is fine for
research and paper.

**Q13 — Economic calendar feed for news filtering?**
`exit.close_before_high_impact_news` is **off** in v1.0 because §17.4 prohibits live behaviour
the backtest cannot reproduce. Enabling it requires a historical calendar covering the whole
backtest period (ForexFactory scrape, or a paid API).
*Recommendation:* keep off for v1.0. If a historical calendar is obtained, add news as a
*reporting dimension* first (do trades around releases behave differently?) before adding it
as a filter.

**Q14 — Alerting channel.**
Kill switch, halts, reconciliation mismatches and daily summaries need somewhere to go.
*Recommendation:* Telegram bot — simplest reliable option, works on mobile.

**Q15 — Who operates it, and what is the expected intervention model?**
The kill switch has a manual trigger and the monthly loss limit requires manual re-enable
(§18.4). Both assume somebody is watching daily.
*Recommendation:* confirm this, or the limits should be reconsidered to be fully automatic.

---

## Process

**Q16 — Timeline expectations.**
Phases 1–14 are substantial: the data pipeline and the engines alone are the bulk of it, and
the backtest protocol (walk-forward, falsification suite, Monte Carlo) is comparable again.
This is not a weekend project, and compressing it mostly means dropping the validation, which
is the part that determines whether the answer means anything.
*Recommendation:* agree checkpoints at Phase 4 (data and bias engines verified), Phase 9 (the
funnel gate — the project's real decision point), and Phase 14 (full protocol results).

**Q17 — What happens if the answer is "no edge"?**
Worth agreeing now, while it is hypothetical. The protocol (§10.2) prohibits tuning until it
passes, and requires a documented failure analysis instead.
*Recommendation:* accept that outcome up front as a legitimate deliverable. The single most
common failure in this kind of project is deciding after the fact that the rules do not apply
to this particular result.

---

## Answering

Answered 2026-08-25. Phase 1 is unblocked pending sign-off on the updated specification.
