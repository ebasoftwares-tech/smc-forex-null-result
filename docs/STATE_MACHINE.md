# Signal State Machine — SMC Bot v1.0

Companion to `SMC_STRATEGY_SPECIFICATION_v1.0.md`. Section references in the form §n refer to
that document.

---

## §1. Two machines, not one

| Machine | Instances | Lifetime |
|---|---|---|
| **Setup machine** | One per candidate setup (§14.1) | From sweep confirmation to a terminal state |
| **Session machine** | One per symbol per trading day | From day boundary to day boundary; owns the risk gates and the halt states |

A common mistake is to model the strategy as one global state machine. It is not: several
setups on the same symbol can be at different stages at once (a sweep of the previous-day low
while a sweep of the Asian high is still waiting for its CHoCH). Concurrency caps (§14.4)
limit how many, but the count is never one by design.

The engine itself has no state beyond the collection of live setups plus the deterministic
engines (structure, liquidity, sessions), all of which are pure functions of the bar history.

---

## §2. States

### 2.1 Setup machine

| State | Meaning | Terminal? |
|---|---|---|
| `WAITING_FOR_LIQUIDITY` | Conceptual idle state of the engine. **No Setup object exists in this state** — it is the absence of a setup | — |
| `LIQUIDITY_IDENTIFIED` | An in-play, ranked level exists and is being watched. Held by the Liquidity Engine, not by a Setup object | — |
| `LIQUIDITY_SWEPT` | A sweep confirmed (§9). Setup object created. **No order may exist** | No |
| `WAITING_FOR_DISPLACEMENT` | CHoCH reference selected; watching for a displacement leg | No |
| `DISPLACEMENT_CONFIRMED` | A qualifying leg exists but the reference is not yet broken | No |
| `WAITING_FOR_CHOCH` | Reference selected; watching for the break. Coexists with the two states above — see §2.2. Under D-002 the confirmation timeframe is H4 for every tier, so a setup sits here for a minimum of one H4 bar and typically several | No |
| `CHOCH_CONFIRMED` | MSS confirmed (§11.5). Entry model, SL, TP and size are being computed and gated | No |
| `WAITING_FOR_ENTRY` | Order armed (limit) or about to be sent (market). Cancel conditions active | No |
| `ENTRY_CONFIRMED` | Broker acknowledged the fill; internal position record created | No |
| `TRADE_OPEN` | Position live and managed (§17) | No |
| `TRADE_CLOSED` | Position closed; record written | **Yes** |
| `SETUP_INVALIDATED` | Terminated pre-trade for a reason in §19; forward return recorded | **Yes** |

### 2.2 On the two "waiting" states

The brief lists `WAITING_FOR_DISPLACEMENT` and `WAITING_FOR_CHOCH` as sequential. In practice
displacement and the structural break are evaluated **on the same bar close** and may occur in
either order, so the implementation carries them as one state with two boolean flags:

```
state = AWAITING_MSS
  flags: displacement_ok : bool     # set by §10 on the leg ending at the current bar
         break_ok        : bool     # set by §6.3 against the CHoCH reference
MSS ⟺ displacement_ok ∧ break_ok ∧ all §11.5 clauses
```

The two named states are retained for **reporting and charting** — a setup is reported as
`DISPLACEMENT_CONFIRMED` when `displacement_ok ∧ ¬break_ok`, and `WAITING_FOR_CHOCH` when
`¬displacement_ok`. This keeps the brief's vocabulary in the logs and the charts without
implying an ordering the market does not respect.

Note the flag lifetime: `displacement_ok` is **recomputed per bar**, not latched. A leg that
displaced three bars ago and has since stalled does not qualify a break today, because §10.1
fixes the leg to the bars ending at the break bar.

### 2.3 Session machine

| State | Meaning |
|---|---|
| `MARKET_CLOSED` | Outside the trading week |
| `ACTIVE` | Normal operation |
| `HALTED_DAILY_LOSS` | `risk.max_daily_loss_pct` breached; auto-clears at the next day boundary |
| `HALTED_WEEKLY_LOSS` | Auto-clears at the next week boundary |
| `HALTED_MONTHLY_LOSS` | **Manual re-enable required** |
| `HALTED_CONSECUTIVE` | Auto-clears after `risk.consecutive_loss_pause_hours` |
| `HALTED_DATA` | Stale or suspect data; clears when data resumes |
| `KILLED` | Kill switch (§18.6). Manual re-enable only |

In every `HALTED_*` state and in `KILLED`: **no new setups may be armed or filled**; existing
setups may continue to progress through their analysis states and are invalidated at the entry
gate with `RISK_LIMIT_*`; **open positions continue to be managed normally**. Abandoning
management of an open position because a loss limit was hit would leave live risk unattended,
which is the opposite of what a loss limit is for.

---

## §3. Transition table (setup machine)

Guards are evaluated in the listed order; the first failing guard supplies the reason, but
**all** guards are evaluated and recorded (§14.1).

| # | From | Event | Guards | Action | To |
|---|---|---|---|---|---|
| T1 | *(none)* | `SWEEP_CONFIRMED` (§9) | level ACTIVE; direction has capacity (§14.4); not `DATA_SUSPECT` | Create Setup; snapshot bias + session; link cluster | `LIQUIDITY_SWEPT` |
| T2 | `LIQUIDITY_SWEPT` | same-bar close processed | CHoCH reference found (§11.1); within `max_reference_distance_atr` | Store reference; set deadline `= sweep_bar + choch.max_bars_after_sweep` | `WAITING_FOR_CHOCH` |
| T3 | `LIQUIDITY_SWEPT` | same-bar close processed | reference **not** found | Record reason | `SETUP_INVALIDATED(NO_CHOCH_REFERENCE \| REFERENCE_TOO_FAR)` |
| T4 | `WAITING_FOR_CHOCH` | confirmation-TF bar close | `displacement_ok ∧ ¬break_ok` | Set flag; store leg metrics | `DISPLACEMENT_CONFIRMED` |
| T5 | `DISPLACEMENT_CONFIRMED` | confirmation-TF bar close | `¬displacement_ok` (leg no longer qualifies) | Clear flag | `WAITING_FOR_CHOCH` |
| T6 | `WAITING_FOR_CHOCH` / `DISPLACEMENT_CONFIRMED` | confirmation-TF bar close | **all** §11.5 clauses | Emit `MSS_CONFIRMED`; emit `CHOCH_CONFIRMED` | `CHOCH_CONFIRMED` |
| T7 | `WAITING_FOR_CHOCH` / `DISPLACEMENT_CONFIRMED` | confirmation-TF bar close | `break_ok ∧ ¬displacement_ok` | Emit `CHOCH_CONFIRMED` **event only** (not an MSS); setup does **not** advance | *(unchanged)* |
| T8 | `WAITING_FOR_CHOCH` / `DISPLACEMENT_CONFIRMED` | confirmation-TF bar close | deadline passed | Record forward return | `SETUP_INVALIDATED(CHOCH_TIMEOUT)` |
| T9 | any pre-trade | confirmation-TF bar close | new extreme beyond sweep extreme + tolerance | — | `SETUP_INVALIDATED(NEW_EXTREME)` |
| T10 | any pre-trade | `SWEEP_CONFIRMED` opposite direction | — | — | `SETUP_INVALIDATED(OPPOSING_SWEEP)` |
| T11 | `CHOCH_CONFIRMED` | immediately | entry model can arm; SL valid (§16.3); `rr ≥ tp.min_rr`; size valid (§18.2); bias gate; session filter; spread; all risk limits | Build `EntryPlan`; place order | `WAITING_FOR_ENTRY` |
| T12 | `CHOCH_CONFIRMED` | immediately | any guard in T11 fails | Record **every** failed guard | `SETUP_INVALIDATED(<first reason>)` |
| T13 | `WAITING_FOR_ENTRY` | fill event | spread still within cap at fill | Create position record | `ENTRY_CONFIRMED` |
| T14 | `WAITING_FOR_ENTRY` | price reaches planned SL | — | Cancel order | `SETUP_INVALIDATED(SL_BEFORE_ENTRY)` |
| T15 | `WAITING_FOR_ENTRY` | `expires_at` reached | — | Cancel order; compute shadow trade (§15.6) | `SETUP_INVALIDATED(ENTRY_EXPIRED)` |
| T16 | `WAITING_FOR_ENTRY` | bias gate flips | `entry.cancel_on_bias_flip` | Cancel order | `SETUP_INVALIDATED(BIAS_FLIP)` |
| T17 | `WAITING_FOR_ENTRY` | kill switch / risk halt | — | Cancel order | `SETUP_INVALIDATED(RISK_LIMIT_* \| KILL_SWITCH)` |
| T18 | `ENTRY_CONFIRMED` | broker stop + target acknowledged | both present broker-side | — | `TRADE_OPEN` |
| T19 | `TRADE_OPEN` | management events (§17.3) | — | Partials, breakeven, trail | `TRADE_OPEN` |
| T20 | `TRADE_OPEN` | SL / TP / time stop / weekend / manual | — | Compute R, MAE/MFE, costs; write record; levels → `CONSUMED` | `TRADE_CLOSED` |

### 3.1 Illegal transitions — absent by construction

These pairs have **no row** in the table. The implementation's transition function MUST raise
on an attempt rather than silently ignore it, and a test MUST assert each raises:

| Attempted | Why it is forbidden |
|---|---|
| `LIQUIDITY_SWEPT` → `WAITING_FOR_ENTRY` | Entry immediately after a sweep. The core prohibition of the whole strategy |
| `LIQUIDITY_SWEPT` → `ENTRY_CONFIRMED` | As above |
| `DISPLACEMENT_CONFIRMED` → `WAITING_FOR_ENTRY` | Displacement without a structural break is not an MSS |
| `WAITING_FOR_CHOCH` → `TRADE_OPEN` | Skips every gate |
| `SETUP_INVALIDATED` → anything | Terminal. A setup is never revived; a new opportunity is a new object |
| `TRADE_CLOSED` → anything | Terminal |
| any → `TRADE_OPEN` without `ENTRY_CONFIRMED` | A position must never exist without a fill record |

### 3.2 Timeouts, in one place

| State | Timeout | On expiry |
|---|---|---|
| `LIQUIDITY_SWEPT` | Resolved on the same bar (T2/T3) | — |
| `WAITING_FOR_CHOCH` / `DISPLACEMENT_CONFIRMED` | `choch.max_bars_after_sweep` (12) | `CHOCH_TIMEOUT` |
| `CHOCH_CONFIRMED` | Same bar (T11/T12) | — |
| `WAITING_FOR_ENTRY` | `entry.pending_expiry_bars` (6) | `ENTRY_EXPIRED` |
| `TRADE_OPEN` | `exit.max_bars_in_trade` (30) | `TIME_STOP` |
| Sweep window (pre-setup) | `sweep.max_confirmation_bars` (3) | `SWEEP_FAILED_NO_RECLAIM` |

Every non-terminal state has a bounded lifetime. There is no state a setup can occupy
indefinitely, which is what makes the live system's setup population self-limiting without a
sweeper process.

---

## §4. Event ordering within one bar close

The order matters and is fixed. Same bar, same timeframe, always:

```
1  Ingest and close the bar; construct any higher-TF bars that completed
2  Update ATR
3  Session engine: close sessions that ended; publish their levels
4  Period rollovers (day / week / month) → cached HTF analyses (§2.5)
5  Structure engine: confirm swings; normalise; emit BOS / CHoCH / INTERNAL_LIQUIDITY_GRAB
6  Liquidity engine: create new levels; merge; age; expire; invalidate
7  Sweep engine: update open windows; emit SWEEP_CONFIRMED / SWEEP_FAILED
8  FVG and OB engines: create; update mitigation
9  Setup machine: for each live setup, in creation order, evaluate transitions
10 New setups from step 7's confirmations   ← after existing setups, never before
11 Risk layer: evaluate limits; update session machine state
12 Order layer: place / cancel / modify
13 Flush events to the log
```

Two orderings that are load-bearing:

- **Step 5 before step 6.** A swing must be confirmed before it can become a liquidity level,
  or the level's `confirmed_at` would precede its source's.
- **Step 9 before step 10.** Existing setups get the current bar before new ones are created
  from it, so a setup created by this bar's sweep cannot also be advanced by this bar — which
  is what enforces `sweep.same_bar_choch_allowed = false` structurally rather than by a check.

Step 13 last: the log is written after the bar is fully processed, as one atomic record, so a
crash cannot leave a half-processed bar in the log.

---

## §5. Reconstruction and crash recovery

State is **not** persisted as state. On restart:

1. Replay `events.jsonl` to rebuild live setups and the session machine.
2. Recompute all engines from bar history.
3. Assert the replayed state equals the recomputed state (this is the §25.2 test running in
   production).
4. Reconcile against the broker: every open broker position must have an internal record and
   vice versa; every open position must have a broker-side stop.
5. Any mismatch → `HALTED_DATA` plus alert. **Never** guess, never adopt the broker's view
   silently, never adopt the log's view silently.

Step 3 is the reason state is recomputed rather than checkpointed: a checkpoint can preserve a
corrupt state indefinitely, whereas a recomputation that disagrees with the log localises the
fault immediately.

---

## §6. Instrumentation

Every transition emits:

```
{ ts_utc, setup_id, symbol, from_state, to_state, trigger_event, bar_time, bar_tf,
  guards: [{name, passed, value, threshold}], reason?, config_hash }
```

The `guards` array records **every** guard with its measured value and threshold, whether or
not it passed. This is what makes the rejection analysis of §21.3 possible: without the
measured values, the log says a setup failed `SL_TOO_WIDE` but not by how much, and the
question "how many rejections were marginal?" — the question that tells you whether a
threshold is well-placed — becomes unanswerable.
