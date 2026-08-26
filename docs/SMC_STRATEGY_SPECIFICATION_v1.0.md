# SMC STRATEGY SPECIFICATION v1.0

**Document status:** DRAFT FOR APPROVAL — implementation is blocked on sign-off.
**Scope:** FX majors and major crosses, H4 primary setup timeframe, swing-style holding
periods (hours to days).
**Companion documents:** `STATE_MACHINE.md`, `ARCHITECTURE.md`, `PARAMETERS.md`,
`BACKTEST_PROTOCOL.md`, `OPEN_QUESTIONS.md`.

---

## §0. How to read this document

### 0.1 Structure of every definition

Each of the 20 required concepts is specified with the same six-part structure:

| Part | Contains |
|---|---|
| **Rule** | The formal condition, in arithmetic over OHLC series. No adjectives. |
| **Pseudocode** | An implementable algorithm, including the confirmation time of the result. |
| **Parameters** | Names as they appear in `PARAMETERS.md`, with defaults and classification. |
| **Example** | A worked numeric case with real price levels. |
| **Edge cases** | The enumerated situations where the naive rule is ambiguous, and the ruling. |
| **Backtest** | How the component is measured and falsified independently of the whole system. |

### 0.2 Coverage map (requirement §32 → this document)

| Required definition | Section |
|---|---|
| 1. Monthly analysis | §7.5, §2.3 |
| 2. Weekly analysis | §7.5, §2.3 |
| 3. Daily analysis | §7.5, §2.3 |
| 4. H4 analysis | §7.5, §14 |
| 5. Sessions | §3 |
| 6. Swing High | §5 |
| 7. Swing Low | §5 |
| 8. Liquidity | §8 |
| 9. Liquidity Sweep | §9 |
| 10. Displacement | §10 |
| 11. BOS | §6.4 |
| 12. CHoCH | §6.5, §11 |
| 13. MSS | §6.6, §11 |
| 14. FVG | §12 |
| 15. Order Block | §13 |
| 16. Entry | §15 |
| 17. Stop Loss | §16 |
| 18. Take Profit | §17 |
| 19. Risk Management | §18 |
| 20. Setup invalidation | §19 |
| Architecture + state machine | `ARCHITECTURE.md`, `STATE_MACHINE.md` |

### 0.3 Language conventions

- **MUST / MUST NOT** — a rule an implementation is wrong if it violates.
- **SHOULD** — a default that may be changed only by changing a parameter, never in code.
- *Undefined* — a state the state machine forbids; reaching it is a bug, not a fallback.
- All parameters are written `group.name`. A parameter never appears as a literal in code.

### 0.4 Three specification-level warnings, stated once

**(a) The session-liquidity model and the H4 setup timeframe are in tension — RESOLVED by
decision D-002 in favour of H4-only, and the strategy changes character as a result.**
The London session is 8.5 hours — roughly two H4 bars. A model that requires an H4 sweep of
the Asian low *and* an H4 change of character after it has, at most, two bars in which to
resolve. Since `sweep.same_bar_choch_allowed = false` (§9.6), the minimum distance from sweep
confirmation to MSS confirmation is **two H4 bars = 8 hours**, and the practical median will
be longer.

**Therefore, under H4-only confirmation, the flagship setup is not "London sweeps the Asian
low and reverses during London". It is "London sweeps the Asian low, and the structure shift
confirms in New York or the following session."** That is a coherent and tradeable model — it
is closer to a session-to-session continuation model than to an intraday reversal one — but it
is *not* the model the brief's §6 example describes, and it must not be reported as if it
were. The measurement that settles whether this matters is the **sweep-session × entry-session
matrix** (§21.2, `BACKTEST_PROTOCOL.md` §4.2), which is now a required table rather than one
breakdown among many.

The alternative — a liquidity-tier → confirmation-timeframe map giving session liquidity H1
confirmation — is fully specified in §11.2 and retained as the primary ablation
(`liq.tier_confirmation_tf`). It is measured, not assumed away.

**(b) The Monthly bias filter cannot be statistically validated on five years of data.**
Five years is 60 monthly bars and, realistically, 6–12 independent monthly regimes. No
subgroup analysis conditioned on "Monthly = BULLISH" will reach significance. §7.7 therefore
classifies Monthly bias as a **FROZEN structural choice measured by ablation only** — it may
never be tuned, and any performance difference attributed to it MUST be reported with its
power analysis attached (`BACKTEST_PROTOCOL.md` §5.4).

**(c) This document defines ~140 parameters. That is a catastrophic search space if treated
as one.** `PARAMETERS.md` classifies 8 as TUNABLE and the rest as FROZEN or ABLATION-ONLY.
The classification is part of the specification, not a suggestion: a configuration that
tunes a FROZEN parameter is a different, unregistered experiment and its results MUST be
reported as such.

---

## §1. Notation, data model, and the causality axiom

### 1.1 Bar series

A bar series on timeframe `TF` is an ordered sequence of bars `B^TF = [b_0 … b_n]` where

```
b_i = (t_i, O_i, H_i, L_i, C_i, V_i)
```

- `t_i` is the bar **open** time, timezone-aware, stored in **UTC**.
- `D(TF)` is the bar duration. Close time is `t_i + D(TF)`.
- Bars are contiguous in trading time: `t_(i+1) = t_i + D(TF)` for all bars inside a trading
  week. Weekend gaps are absent bars, not zero-length bars (§1.5).
- `V_i` is broker tick volume. **Volume MUST NOT appear in any rule in this specification.**
  Retail FX tick volume is a broker-specific artefact, is not comparable across data
  sources, and is not real traded volume; it is stored for diagnostics only.

Shorthand used throughout: `H_i, L_i, C_i, O_i`; `range_i = H_i − L_i`;
`body_i = |C_i − O_i|`; `uw_i = H_i − max(O_i, C_i)` (upper wick);
`lw_i = min(O_i, C_i) − L_i` (lower wick).

### 1.2 The causality axiom

> **AXIOM C.** For any decision, signal, or state transition attributed to time `T`, every
> input MUST be derivable from `{ b in B^TF : t_b + D(TF) <= T }` for every timeframe used.

Three consequences the rest of the document depends on:

1. **A bar is invisible until it closes.** The currently-forming bar contributes nothing to
   any signal rule. One explicitly scoped exception: the *live risk layer* reads current
   bid/ask for spread checks and stop management (§18.6). It never feeds the signal layer.
2. **Every derived object carries two timestamps** — `formed_at` (the bar it describes) and
   `confirmed_at` (the moment it became knowable). Downstream engines index on
   `confirmed_at`. `formed_at < confirmed_at` for anything requiring lookahead to detect;
   swings above all (§5.2).
3. **Amendment is legal; retraction is not.** An engine MAY revise a *label* it previously
   assigned (e.g. reclassify which swing is the protected low) provided the revision is
   caused by a bar close at time `T` and uses only data `<= T`. An engine MUST NOT withdraw,
   move, or re-price a **signal** (sweep event, CHoCH event, entry) once emitted. §25 gives
   the automated test that enforces both halves.

### 1.3 Price convention: bid, ask, spread

- Stored bars are **BID** bars. This MUST be verified at ingest against the provider's
  documentation and recorded in the dataset manifest. MT5 exports bid bars; most tick
  archives (Dukascopy, TrueFX) supply both sides.
- `ask(t) = bid(t) + spread(t)`.
- **Buy** orders fill at ask; their stops and targets execute on bid.
- **Sell** orders fill at bid; their stops and targets execute on ask.
- All *analysis* (swings, liquidity, structure, sweeps) is performed on the bid series only.
  Analysing a spread-inflated synthetic mid series introduces a symbol- and session-dependent
  distortion in exactly the wick regions this strategy reads.

A consequence worth stating because it is a frequent source of inflated backtests: a long
trade's stop must clear the sweep low **by at least the prevailing spread**, or the stop sits
inside the very noise band that produced the sweep. `sl.buffer` (§16.3) is therefore
`max(atr_component, spread_component)` — never a fixed pip count.

### 1.4 Instrument metadata

Per symbol, resolved once from the broker and cached in the dataset manifest:

| Field | Use |
|---|---|
| `digits` | pip size derivation |
| `point` | smallest price increment |
| `pip_size` | `10 × point` when `digits` is 3 or 5, else `point`. Majors 0.0001; JPY pairs 0.01 |
| `contract_size` | 100 000 units of base currency for FX |
| `lot_step`, `min_lot`, `max_lot` | position size quantisation (§18.2) |
| `base_ccy`, `quote_ccy` | pip value conversion (§18.2) |
| `swap_long`, `swap_short`, `swap_3day_weekday` | financing cost (§26) |
| `stops_level`, `freeze_level` | broker minimum stop distance; a setup whose stop is inside it MUST be rejected, not silently widened |

**v1.0 symbol universe.** Development on EURUSD, GBPUSD, USDJPY. Robustness testing on the
full set: EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF, NZDUSD, EURJPY, GBPJPY, EURGBP.
Metals, indices and crypto are out of scope for v1.0 — their session structure, gap behaviour
and volatility regimes differ enough to need their own parameter study, and including them
would silently multiply the search space.

### 1.5 Trading week, gaps, and holidays

- The FX week opens at `week.open_utc` (default **Sunday 21:00 UTC**) and closes at
  `week.close_utc` (default **Friday 21:00 UTC**). These are broker-specific and MUST be
  measured from the data rather than assumed: ingest reports the observed first and last bar
  of each week and flags any week deviating from the configured anchor by more than one hour.
- **Missing bars are missing, not filled.** No forward-fill, no synthetic bars. Index
  arithmetic in this document is over the *existing* bar list, so "3 bars back" means three
  actual bars, which may cross a weekend.
- A gap larger than `data.max_gap_bars` (default **3** bars, excluding the weekend gap) marks
  the surrounding region `DATA_SUSPECT`. Setups whose formation window intersects a
  `DATA_SUSPECT` region are excluded from headline statistics and reported in a separate
  bucket. Holidays are detected this way; **no holiday calendar is hard-coded**, because
  broker holiday behaviour varies and a wrong calendar silently deletes real trades.

### 1.6 ATR — the volatility unit

Every threshold that could have been written in pips is written in ATR multiples instead, so
one parameter set is meaningful across symbols and across volatility regimes. Wilder's ATR:

```
TR_i     = max( H_i − L_i , |H_i − C_(i−1)| , |L_i − C_(i−1)| )
ATR_n(i) = ((n−1) · ATR_n(i−1) + TR_i) / n        for i > n
ATR_n(n) = mean(TR_1 … TR_n)                       (seed)
```

- `atr.period` = **14** (FROZEN).
- **Reference-value rule (MUST):** any test applied to bar `i` uses `ATR_ref(i) = ATR_n(i−1)`
  — the value as of the *previous* closed bar. A displacement bar must not be permitted to
  raise the threshold it is being tested against; using `ATR_n(i)` makes large bars
  self-normalising and quietly destroys the displacement filter.
- ATR is computed per timeframe; `ATR^H4` and `ATR^D1` are distinct series. Every rule states
  which one it uses.
- **Warm-up:** no signal may be emitted before `atr.period + swing.fractal_n + 1` closed bars
  exist on every timeframe the signal depends on.

### 1.7 Object identity

Every derived object (level, swing, sweep, FVG, OB, setup, order, trade) carries:

```
id            ULID, monotonic by creation time, globally unique
symbol
timeframe
formed_at     bar open time of the bar it describes        (UTC)
confirmed_at  bar close time at which it became knowable   (UTC)
status        enum, per object type
source_ids[]  ids of the objects it was derived from
config_hash   SHA-256 of the resolved parameter set that produced it
```

`source_ids` is what makes a trade explicable afterwards: a trade points to its entry, which
points to its MSS, which points to its sweep, which points to its liquidity level, which
points to the session or swing that formed it. `config_hash` is what makes a backtest
reproducible: results are keyed by it, and a run whose hash is not in the registry is not a
result (`BACKTEST_PROTOCOL.md` §7).

---

## §2. Timeframe construction and the day boundary

### 2.1 Why timeframes are built, not fetched

Broker MN1/W1/D1/H4 candles are cut at the broker's server midnight, which varies by broker
(GMT+2, GMT+3, GMT+0 …) and shifts with the broker's own DST policy. A "Previous Day High"
therefore means a different price at two brokers, and a strategy validated on one broker's D1
series is not the strategy that runs at another. Worse, the same broker's D1 series changes
shape twice a year.

**Rule (MUST):** the bot ingests **M1 or M5** bars only, and constructs M15, H1, H4, D1, W1
and MN1 itself by resampling with an explicit, configurable anchor. Broker higher-timeframe
candles are never used for analysis. If M1 is unavailable, H1 is the minimum ingest
resolution and the session engine is degraded accordingly (§3.6).

### 2.2 The day boundary parameter

`tf.day_boundary` defines when a new trading day starts.

**Decision D-001: `tf.day_boundary = UTC 00:00`.** (See `DECISIONS.md`.)

| Value | Status | Rationale |
|---|---|---|
| **`UTC 00:00`** | **DEFAULT** | Neutral, DST-free, identical across every data source and broker without a tz database. The day boundary never moves, so H4 bar edges are stable across the year and a result computed on one dataset is reproducible on another |
| `America/New_York 00:00` | ABLATION | The convention the SMC/ICT literature this strategy derives from is written in. Retained as a full parallel run, not a sweep |

The boundary is still expressed as `(IANA timezone, local time)` — `("UTC", 00:00)` — so the
mechanism is unchanged and switching to the NY anchor for the ablation is a configuration
change, not a code change.

**What this does not change: sessions remain anchored to their financial centres' local time**
(§3.1). The day boundary and the session windows are separate mechanisms. London is still
`Europe/London 08:00–16:30` and still shifts against UTC twice a year; only the *day, H4, W1
and MN1 bucket edges* are now fixed in UTC. Conflating the two — "we chose UTC, so make the
sessions UTC too" — would reintroduce exactly the DST bug §3.1 prohibits.

Two consequences that follow directly and are handled in §2.6:

1. The H4 grid is now **00, 04, 08, 12, 16, 20 UTC**, fixed year-round. The London open
   (07:00/08:00 UTC) and the New York open (12:00/13:00 UTC) therefore fall at different
   points *within* an H4 bar in summer and winter. This is not a defect — it is the honest
   consequence of a fixed grid meeting a moving session, and it is why `dst_desync` and
   session attribution are recorded on every trade.
2. The Sunday market open (21:00 UTC) now falls **3 hours before** the UTC day boundary,
   producing a stub Sunday D1 bar. Under the previously-defaulted NY anchor it produced an
   8-hour stub instead. Either way it is a defect, and §2.6 rules on it.

### 2.3 Construction rules

Let `boundary(d)` be the UTC instant of the day boundary for local date `d`.

| TF | Bucket start | Notes |
|---|---|---|
| M15, H1 | Aligned to UTC hour | DST-independent; no anchor needed |
| **H4** | `boundary(d) + k·4h`, k in 0..5 | **H4 buckets are anchored to the day boundary.** Under D-001 that is **00, 04, 08, 12, 16, 20 UTC**. This is the most consequential resampling decision in the document: it determines which candles the entire strategy sees, and therefore where every swing, sweep and CHoCH is |
| **D1** | `boundary(d)` to `boundary(d+1)` | A period with no bars (weekend/holiday) produces no D1 bar at all. The Sunday stub is merged, not emitted — §2.6 |
| **W1** | The D1 bars of one trading week | Week starts at the D1 bar containing `week.open_utc`. Because the Sunday stub merges forward (§2.6), Monday's D1 bar carries the Sunday-gap region, and the W1 bar built from those D1 bars contains it too |
| **MN1** | The D1 bars whose `boundary(d)` local date falls in one calendar month | Attribution is by the local calendar date of the day boundary, so month membership never depends on UTC offset |

OHLC aggregation is standard: `O` = first bar's open, `H` = max high, `L` = min low,
`C` = last bar's close, `V` = sum.

### 2.4 Partial higher-timeframe bars

A D1/W1/MN1 bar is **CLOSED** only when the next boundary has passed *and* at least one bar
of the next period exists (proving the market reopened). Until then it is **FORMING** and is
excluded from every analysis. The practical effect: Monday's Weekly analysis operates on the
week that finished on Friday, and the current week's high/low are tracked as running extremes
for visualisation only, never as liquidity (§8.4).

One deliberate exception: **the current period's OPEN is knowable immediately** and is used
(`Current Month Open`, `Current Week Open`, `Daily Open` — all required by the brief). An
open price is not a lookahead value.

### 2.5 Analysis schedule

| Analysis | Trigger | Cached until |
|---|---|---|
| Monthly | First H4 close at or after the month boundary | Next month boundary |
| Weekly | First H4 close at or after the week boundary | Next week boundary |
| Daily | First H4 close at or after the day boundary | Next day boundary |
| H4 structure / liquidity / setups | Every H4 close | Next H4 close |
| Confirmation TF | Under D-002 this is H4 — the row above. The separate-TF trigger survives only for the H1 ablation runs of §11.2, and fires only while at least one setup is in a waiting state | — |
| Sessions | Every M15 close | — |
| Risk gates | Every setup evaluation, plus every 60 s while a position is open | — |

**MUST NOT** recompute Monthly, Weekly or Daily analysis on any other trigger. The cached
result carries its `config_hash` and the bar index it was computed at; the live engine
asserts the cache key matches the current period on every read, so a missed rollover fails
loudly instead of silently trading last month's bias.

**Recomputation-on-restart rule:** on process start, all four analyses are recomputed from
history and MUST reproduce the cached values exactly if a cache exists. A mismatch halts the
bot (`ARCHITECTURE.md` §6.3). This is the cheapest available detector of a lookahead bug that
has reached production.

### 2.6 Week-edge partial bars (added by decision D-001)

The FX week does not begin or end on a bucket boundary. Three partial bars result, and each
needs a ruling — the first of them is a genuine defect rather than a cosmetic one.

#### 2.6.1 The Sunday stub D1 bar — MUST be merged

The market opens Sunday 21:00 UTC; the UTC day boundary is Monday 00:00. A naive
implementation therefore emits a **3-hour "Sunday" D1 bar**, and that bar's high and low
become Monday's `PDH` / `PDL` (§4) — one of the most heavily used liquidity sources in the
model. A three-hour opening range standing in for "the previous day" is simply wrong data, and
it would be wrong every single week rather than occasionally.

**Rule:** `tf.sunday_handling = merge_into_monday` (FROZEN default).

```
if a D1 bucket's bar coverage < tf.stub_merge_threshold (default 0.25 of expected bars)
   AND the bucket is the first of the trading week:
       merge its bars forward into the next D1 bucket
```

Monday's D1 bar therefore spans Sun 21:00 → Mon 24:00 UTC (27 hours) and contains the
Sunday-gap region, which is where a large share of weekly liquidity events occur. The
alternative, `standalone_incomplete`, emits the stub and lets the §4 coverage rule skip it as
INCOMPLETE — correct in that it never becomes `PDH`/`PDL`, but it discards the Sunday price
action from D1 structure entirely. Retained as an ablation.

**This defect exists under either day-boundary choice.** Under the NY anchor the stub is 8
hours instead of 3, which is worse in the specific sense that it is large enough to look
plausible and therefore less likely to be noticed. It was latent in v1.0 and is fixed here.

#### 2.6.2 Partial H4 bars at the week edges

- Week open: Sun 21:00–24:00 UTC is 3 of 4 hours of the 20:00 bucket → **75% coverage**.
- Week close: Fri 20:00–21:00 UTC is 1 of 4 hours → **25% coverage**.

**Rule:** both are emitted as normal bars and tagged `partial_bar` with their coverage ratio.
They are **not** excluded, because excluding the Friday bar would discard the weekly close and
excluding the Sunday bar would discard the gap open. A bar with coverage below
`tf.min_bar_coverage_warn` (default 0.50) additionally carries `low_coverage = true`, and any
swing, level or sweep whose defining bar is low-coverage is reported in a separate bucket —
so a result that depends on one thin bar per week is visible rather than buried.

`exit.close_before_weekend` (Friday 19:00 UTC, §17.4) means the system is normally flat before
the Friday stub bar exists, so its practical exposure is limited to structure and liquidity,
not to open positions.

#### 2.6.3 The Friday D1 bar

Fri 00:00–21:00 UTC = 87.5% coverage → a normal bar, above every threshold. No special
handling.

---

## §3. Session engine

### 3.1 Definition

A session is a named, recurring, timezone-anchored window
`(name, tz, local_start, local_end, days)`. Sessions are defined in the **local time of their
financial centre**, so daylight saving is handled by the tz database and never by offset
arithmetic.

**MUST NOT** store or configure a session as a fixed UTC offset. The London session moves
between 07:00–15:30 and 08:00–16:30 UTC twice a year; a hard-coded offset is wrong for half
the year, and wrong in a way that still produces plausible-looking results.

### 3.2 Default session table (every value is a parameter)

| Session | Timezone | Local start | Local end | Days | Role |
|---|---|---|---|---|---|
| `ASIA` | `Asia/Tokyo` | 09:00 | 18:00 | Mon–Fri | Liquidity source; execution optional |
| `LONDON` | `Europe/London` | 08:00 | 16:30 | Mon–Fri | Liquidity source + execution |
| `NEW_YORK` | `America/New_York` | 08:00 | 17:00 | Mon–Fri | Liquidity source + execution |
| `ASIA_RANGE` | `America/New_York` | 20:00 | 00:00 | Sun–Thu | **Liquidity only.** The ICT-convention "Asian range". Deliberately distinct from `ASIA`; the two definitions disagree, and the disagreement is measurable |
| `LONDON_KZ` | `America/New_York` | 02:00 | 05:00 | Mon–Fri | Optional execution window (killzone) |
| `NY_KZ` | `America/New_York` | 07:00 | 10:00 | Mon–Fri | Optional execution window (killzone) |
| `OVERLAP` | derived | — | — | Mon–Fri | `LONDON ∩ NEW_YORK`, computed per day |

A session spanning local midnight (`ASIA_RANGE`) is attributed to the **trading day it ends
in**, so "Tuesday's Asian range" is the range available to Tuesday's London session.

**Note under D-001 (UTC day boundary):** `ASIA_RANGE` ends at 00:00 New York = 04:00/05:00 UTC,
which is now *inside* the UTC trading day rather than exactly on its boundary. Nothing breaks —
the range still closes ~3 hours before the London open and is therefore available to it — but
the previously implied coincidence between "Asian range close" and "day boundary" no longer
holds, and code MUST NOT assume it. Two levels that used to arrive together (`PDL` at the day
boundary, `ASIA_RANGE` low at its session close) now arrive four to five hours apart, which is
visible in the event log and is correct.

The consequence worth watching: the ~4-hour window between the UTC day boundary and the Asian
range close is a period in which `PDH`/`PDL` are live liquidity but the Asian range is not yet.
A sweep in that window can only reference daily-and-higher levels. This is reported as a
distinct `sweep_session` bucket rather than being smoothed over.

### 3.3 The DST desynchronisation window — a concrete, dated edge case

The EU and US change clocks on different dates:

- US: second Sunday in March → first Sunday in November.
- EU: last Sunday in March → last Sunday in October.

For roughly **three weeks in March** and **one week in late October**, London and New York
are 4 hours apart instead of 5. During those windows:

- The London/NY overlap is one hour longer or shorter than usual.
- `LONDON_KZ` (NY-anchored) shifts relative to the actual London open.

**Ruling:** sessions are computed per-day from the tz database, so this resolves correctly and
automatically. The backtest report MUST tag every trade with a `dst_desync` boolean and report
those weeks separately — not in order to trade them differently, but because a strategy whose
results depend materially on four weeks a year has found a calendar artefact, not an edge.

### 3.4 Session object

```
SessionInstance {
  id, symbol, session_name, trading_date
  start_utc, end_utc
  open, high, low, close
  high_ts, low_ts            # timestamp of the extreme, at session.source_tf resolution
  range      = high − low
  range_atr  = range / ATR_ref^D1
  bar_count
  status: FORMING | CLOSED | INCOMPLETE
}
```

`status = INCOMPLETE` when `bar_count < session.min_bar_coverage × expected_bars`
(default **0.60**) — a half-holiday, a data gap, or a broker outage. An INCOMPLETE session's
high and low **MUST NOT** become liquidity levels, and any setup referencing them is rejected
with reason `SESSION_INCOMPLETE`. Detecting this from bar coverage rather than a holiday
calendar makes it correct for every broker and every year with no maintenance.

### 3.5 Session levels become liquidity only at session close

`session_liquidity.use_running_extreme` = **false** (FROZEN default). While a session is
FORMING its running high/low are drawn on the chart but are not liquidity.

Reason: a running extreme is not yet a level anyone has positioned against, and treating it as
one lets a "sweep" be detected against a level created by the same price action that sweeps
it — circular, and a classic source of phantom edge in SMC backtests. The alternative
(`true`: intra-session sweeps of the running extreme) exists and is measured in the ablation
suite, never enabled by default.

### 3.6 Source timeframe, and the degraded mode

`session.source_tf` = **M15** (default). Session extremes and their timestamps come from this
series. **H4 bars cannot produce session levels** — one H4 bar spans a session boundary, so
its high may belong to either side. If only H1 data is available, session extremes are
accurate to the hour, `session.source_tf = H1` MUST be recorded in the dataset manifest, and
results from H1-derived session levels are reported separately from M15-derived ones because
they are not comparable.

### 3.7 Backtest

- **Unit:** a fixture year of synthetic bars spanning both DST transitions in both
  hemispheres; assert session boundaries in UTC for every day, including the desync weeks.
- **Property:** for every day, every session satisfies `start_utc < end_utc`, and `OVERLAP`
  duration is **3.5h normally and 4.5h on a `dst_desync` date** — never negative, never above
  4.5h. *(Corrected during Phase 1 implementation: v1.0 asserted {3h, 4h, 5h}, which was a
  guess. With London 08:00–16:30 and New York 08:00–17:00 the intersection is 13:00–16:30 UTC
  in winter and 12:00–15:30 UTC in summer — 3.5h either way — widening to 12:00–16:30 = 4.5h
  only when the US is on DST and the EU is not. Measured across 2026: 241 days at 3.5h, 20 at
  4.5h, and every one of the 20 is a `dst_desync` date.)*
- **Attribution:** every trade carries `entry_session`, `sweep_session`,
  `liquidity_source_session`. Session performance is a first-class breakdown
  (`BACKTEST_PROTOCOL.md` §4.2). The brief's instruction not to assume the Asian session is
  the best execution window is implemented as: `filter.allowed_execution_sessions` defaults
  to **all sessions enabled**, and the session filter is an ablation dimension rather than a
  pre-baked assumption.

---

## §4. Daily / weekly / monthly reference levels

Required by brief §4. These are bookkeeping, but they are the most-used liquidity sources in
the model, so the definitions are pinned exactly.

| Level | Definition | Available from |
|---|---|---|
| `DAILY_OPEN` | Open of the current (forming) D1 bar | The day boundary |
| `PDH` / `PDL` | High / low of the **last CLOSED** D1 bar | The day boundary |
| `PWH` / `PWL` | High / low of the last CLOSED W1 bar | The week boundary |
| `PMH` / `PML` | High / low of the last CLOSED MN1 bar | The month boundary |
| `CURRENT_DAY_HIGH/LOW` | Running extremes of the forming D1 bar | Continuously — **visualisation and trade management only, never liquidity** |
| `WEEK_OPEN`, `MONTH_OPEN` | Open of the forming W1 / MN1 bar | The respective boundary |

- `levels.history_lookback_days` (default **20**) — how many prior daily levels are retained
  as candidate liquidity. Older prior-day levels expire (§8.7).
- A holiday D1 bar (coverage below `session.min_bar_coverage`) is skipped: "previous day"
  means the previous *day with valid data*, and each skip is logged. Without this rule, a
  three-bar Christmas Day high becomes the reference level for the following week.

---

## §5. Swing High / Swing Low

*(Required definitions 6 and 7.)* Everything structural in this system is built on these two
objects, so their determinism and their confirmation lag propagate everywhere.

### 5.1 Rule

Given series `B^TF` and fractal half-width `N = swing.fractal_n[TF]`:

```
IsSwingHigh(i)  ⟺  i ≥ N  ∧  i ≤ last − N
                   ∧  ∀ j ∈ [i−N, i−1] : H_j <  H_i        (strict, left)
                   ∧  ∀ k ∈ [i+1, i+N] : H_k ≤  H_i        (non-strict, right)

IsSwingLow(i)   ⟺  i ≥ N  ∧  i ≤ last − N
                   ∧  ∀ j ∈ [i−N, i−1] : L_j >  L_i
                   ∧  ∀ k ∈ [i+1, i+N] : L_k ≥  L_i
```

The asymmetry (strict left, non-strict right) is a deliberate tie-break: on a plateau of
equal highs, the **leftmost** bar is the swing. Without an explicit rule, a plateau either
produces several adjacent swings or none, depending on the comparison operators — a silent,
data-dependent inconsistency. `swing.tie_rule ∈ {leftmost, rightmost}` flips the strictness;
`leftmost` is the FROZEN default.

Comparison uses `H_i`/`L_i` only. **A swing is defined by wicks, not bodies.** A body-based
variant is available for ablation (`swing.price_source ∈ {wick, body}`), because a
non-trivial part of the SMC literature reads structure on closes; the two produce materially
different structure and the difference is measurable.

### 5.2 Confirmation and the non-repainting contract

```
formed_index  = i
confirmed_index = i + N
confirmed_at  = close_time(bar i+N)
confirmation_lag = N bars
```

**The lag is N *bars*, which equals `N × D(TF)` in trading time but not in wall-clock
time.** A swing formed on the last H4 bar of Friday confirms on Monday — still exactly
two bars, but 52 hours later. This follows from §1.5 (index arithmetic is over existing
bars, and a weekend is absent bars rather than empty ones), and it is stated here
because every downstream timeout measured "in bars" inherits the same property:
`choch.max_bars_after_sweep = 12` is two trading days mid-week and four days over a
weekend. *(Clarified during Phase 5 implementation; v1.0 wrote `N × D(TF)` unqualified.)*

**MUST:** no engine may reference a swing before `confirmed_at`. `swings_as_of(T)` returns
exactly `{ s : s.confirmed_at ≤ T }`.

The practical cost, stated plainly: with `swing.fractal_n[H4] = 2`, an H4 swing low is known
**8 hours after the bar that made it**. Every downstream rule that "waits for a swing"
inherits that 8-hour delay. This is the price of not repainting, and it is why §11.1 defines a
separate, smaller micro-fractal for the post-sweep CHoCH reference — an 8-hour lag before we
even know what level to watch would make most session-driven setups unreachable.

### 5.3 Default fractal widths

| TF | `swing.fractal_n` | Confirmation lag | Minimum history required |
|---|---|---|---|
| MN1 | 1 | 1 month | 36 bars |
| W1 | 1 | 1 week | 104 bars |
| D1 | 2 | 2 days | 250 bars |
| **H4** | **2** | **8 hours** | 500 bars |
| H1 | 3 | 3 hours | 1000 bars |
| M15 | 5 | 75 minutes | 2000 bars |

`N` decreases with timeframe height because higher timeframes have fewer bars, not because
they are less noisy. Monthly `N = 1` is forced by data availability, and the consequence —
Monthly structure is low-resolution and slow — is accepted and flagged in §7.7, not
engineered around.

### 5.4 Alternation normalisation

Raw fractals can produce two consecutive swing highs with no intervening swing low. Structure
rules require an alternating sequence, so the `SwingStore` normalises **in confirmation
order**:

```
on_swing_confirmed(s):
    if store.empty: append(s); return
    last = store.last
    if last.type != s.type:
        append(s); return
    # same type, no opposite swing between: keep the more extreme, amend the label
    if (s.type == HIGH and s.price >  last.price) or
       (s.type == LOW  and s.price <  last.price):
        emit_amendment(replaced=last.id, replacement=s.id, at=now)
        store.replace_last(s)
    else:
        emit_amendment(rejected=s.id, at=now)      # s is discarded, last stands
```

This is an **amendment**, permitted by Axiom C consequence 3: it is caused by a bar close, it
uses no future data, and it never withdraws an already-emitted signal. Every amendment is
written to the event log with its cause, so the replay test (§25.2) can verify the amendment
*sequence*, not merely the final state.

Ties (`s.price == last.price`) keep the earlier swing — consistent with `tie_rule = leftmost`.

### 5.5 Labelling: HH / HL / LH / LL

Applied only to the normalised, alternating sequence:

```
SwingHigh s_k is  HH  if s_k.price >  previous_high.price   else  LH   (ties → LH)
SwingLow  s_k is  HL  if s_k.price >  previous_low.price    else  LL   (ties → LL)
```

The first swing of each type is `UNDEFINED` — there is nothing to compare it against. Ties
resolve to the weaker label (`LH`, `LL`) so an equal-highs plateau is never read as
continuation; equal highs are a liquidity pattern (§8.5), not a trend signal.

### 5.6 Example (H4, N = 2)

| idx | time (NY) | H | L | note |
|---|---|---|---|---|
| 40 | Mon 08:00 | 1.09420 | 1.09310 | |
| 41 | Mon 12:00 | 1.09510 | 1.09390 | |
| **42** | **Mon 16:00** | **1.09585** | 1.09470 | candidate swing high |
| 43 | Mon 20:00 | 1.09540 | 1.09400 | |
| 44 | Tue 00:00 | 1.09495 | 1.09330 | |

At bar 42: left side `H_40 = 1.09420 < 1.09585` and `H_41 = 1.09510 < 1.09585` ✓; right side
`H_43 = 1.09540 ≤ 1.09585` and `H_44 = 1.09495 ≤ 1.09585` ✓. Swing high confirmed —
`formed_at` = Mon 16:00, `confirmed_at` = **Tue 04:00** (close of bar 44). Between Mon 20:00
and Tue 04:00 the engine does not know this swing exists and MUST NOT act on it.

### 5.7 Edge cases

| Case | Ruling |
|---|---|
| Plateau of equal highs (`H_i = H_(i+1)`) | Leftmost qualifies (`tie_rule`). Exactly one swing per plateau |
| Both `IsSwingHigh(i)` and `IsSwingLow(i)` (inside-bar cluster) | Both are recorded; alternation normalisation (§5.4) resolves the ordering by confirmation time, high first if simultaneous |
| Fewer than `N` bars on either side | Not a swing. No partial confirmation, ever |
| Weekend gap inside the `[i−N, i+N]` window | Allowed; the window is over existing bars. Tagged `spans_gap = true` for diagnostics |
| Window intersects `DATA_SUSPECT` | Swing is created but tagged; setups derived from it are excluded from headline statistics |
| Two swings confirm on the same bar (different types) | Deterministic order: HIGH before LOW. Arbitrary but fixed, and pinned by test |

### 5.8 Backtest

- **Golden file:** a 500-bar hand-checked fixture with all 60 swings enumerated; any change
  to swing code that alters the file fails CI.
- **Causality property test:** for 200 random times `T`, computing swings on data truncated at
  `T` MUST equal `swings_as_of(T)` from the full-history replay (§25.2).
- **Sensitivity:** `swing.fractal_n[H4] ∈ {1,2,3,4}` is an ablation dimension. The count of
  swings per 1000 bars is reported per setting, because a change that alters swing density by
  more than 3× has changed the strategy, not tuned it.

---

## §6. Market structure engine — BOS, CHoCH, MSS

*(Required definitions 11, 12, 13.)*

### 6.1 State

Per symbol and per timeframe:

```
StructureState {
  trend: BULLISH | BEARISH | UNDEFINED
  last_swing_high, last_swing_low        # confirmed, normalised
  protected_low   # bullish trend: the swing low whose break flips the trend
  protected_high  # bearish trend: the swing high whose break flips the trend
  last_event: {type, at, level, swing_id}
  events[]                               # append-only BOS/CHoCH/MSS log
}
```

### 6.2 Initialisation

`trend = UNDEFINED` until **two confirmed swings of each type** exist. Then:

- If `last_high` is HH and `last_low` is HL → `BULLISH`, `protected_low = last_low`.
- If `last_high` is LH and `last_low` is LL → `BEARISH`, `protected_high = last_high`.
- Otherwise → stays `UNDEFINED` until the first break resolves it (§6.4).

**MUST NOT** guess a trend from price relative to a moving average or from the direction of
the last N bars. Structure state is derived from structure only; anything else makes the
`UNDEFINED` state meaningless, and `UNDEFINED` is a load-bearing state — it is how the engine
says "there is no structure to trade here", which is a legitimate and frequent answer.

### 6.3 Break test

A level `P` is **broken upward** at bar `i` when:

```
break_up(i, P)   ⟺  C_i > P + break.min_penetration_atr × ATR_ref(i)
break_down(i, P) ⟺  C_i < P − break.min_penetration_atr × ATR_ref(i)
```

- `structure.break_confirmation ∈ {close, wick}` = **close** (FROZEN). `wick` (`H_i > P`) is
  available for ablation and is expected to be materially worse — it converts every liquidity
  sweep into a false structure break, which is precisely the confusion this whole strategy
  exists to exploit.
- `break.min_penetration_atr` = **0.0** (default). A non-zero value (0.05–0.15) is an ablation
  dimension that filters marginal closes one point beyond a level.

### 6.4 BOS — Break of Structure (continuation)

**Rule.** In a `BULLISH` trend, a bullish BOS occurs at bar `i` when `break_up(i,
last_swing_high.price)` and `last_swing_high.confirmed_at ≤ close_time(i)`. Mirror for
bearish.

**Effect.**

- `trend` unchanged.
- `last_event = BOS`.
- `protected_low` is updated to **the most recent swing low confirmed at or before bar `i`**,
  and thereafter **ratchets upward only**: it is replaced whenever a later confirmed swing low
  is *higher*, never when lower. Mirror for `protected_high` in a bearish trend.

  **The reset at the BOS itself may move the level *down*** — if an `INTERNAL_LIQUIDITY_GRAB`
  printed a swing low below the protected low, that grab low is the most recent confirmed low
  and becomes the new invalidation point. This is standard SMC (the origin of the leg that
  broke structure is what invalidates it) and it is why §6.9's invariant is stated *between*
  BOS events rather than absolutely. The alternative — never letting the level move away from
  price — is `structure.protected_on_bos = ratchet_only`, a required ablation. See D-005.

The ratchet is what makes CHoCH meaningful. Without it, a deep pullback that prints a lower
swing low would quietly move the protected level down and the reversal signal would never
fire. With it, a **wick below the protected low that does not close below it** leaves the
trend intact and is recorded as `INTERNAL_LIQUIDITY_GRAB` — which is itself a first-class
liquidity source (§8.5, source `PROTECTED_SWING`), and arguably the highest-quality one in the
model, because the level is defined by the structure the market is currently trading.

`structure.on_wick_below_protected ∈ {keep, reset}` = **keep** (FROZEN).

**Pseudocode.**

```
on_bar_close(i, tf):
    st = state[tf]
    if st.trend == BULLISH:
        if st.last_swing_high and break_up(i, st.last_swing_high.price):
            emit(BOS_BULLISH, at=close_time(i), level=st.last_swing_high.price)
            st.protected_low = most_recent_swing_low_confirmed_by(i)
        elif st.protected_low and break_down(i, st.protected_low.price):
            emit(CHOCH_BEARISH, ...); st.trend = BEARISH
            st.protected_high = most_recent_swing_high_confirmed_by(i)
        elif st.protected_low and L_i < st.protected_low.price:      # wick only
            emit(INTERNAL_LIQUIDITY_GRAB, level=st.protected_low.price)
    # mirror for BEARISH
    # UNDEFINED: the first break in either direction sets the trend and emits BOS
    ratchet_protected(st)
```

### 6.5 CHoCH — Change of Character (reversal)

**Rule.** In a `BULLISH` trend, a bearish CHoCH occurs at bar `i` when
`break_down(i, protected_low.price)`. Mirror for bullish.

**Effect.** `trend` flips; the opposite protected level is initialised to the most recent
opposite swing confirmed at or before bar `i`; `last_event = CHoCH`.

A CHoCH carries **no displacement requirement and no sweep requirement.** It is the pure
structural event. This matters for measurement: CHoCH is the *superset*, and the marginal
value of each added filter is measurable only if the unfiltered event is also recorded.

### 6.6 MSS — Market Structure Shift (the tradable event)

CHoCH and MSS are used interchangeably in most SMC material. This specification separates
them, because a system needs a name for "structure changed" and a different name for
"structure changed in the way we are willing to trade":

> **MSS := a CHoCH that additionally satisfies all of:**
> 1. **Sweep context** — the CHoCH break bar occurs within `choch.max_bars_after_sweep`
>    bars of a CONFIRMED sweep in the corresponding direction (§9), and the swept level lies
>    beyond the extreme of the leg that produced the CHoCH.
> 2. **Displacement** — the leg from the sweep extreme to the break bar satisfies §10.
> 3. **Reference validity** — the broken level is the CHoCH reference selected by §11.1, and
>    it is within `choch.max_reference_distance_atr` of the sweep extreme.

**Clause 1's second half — "the swept level lies beyond the extreme of the leg that
produced the CHoCH" — is a diagnostic, not a condition** (decision D-009 §2). §11.5 states the
MSS conditions under the heading *"MSS confirmation, complete"* and omits it; the two sections
cannot both be right. 11.5 is operative, being the more specific and the one claiming
completeness. The clause is evaluated and reported anyway, so the cost of the other reading is
a number rather than an argument — 3 of 38 MSS events on the Phase 9 fixture.

So `MSS ⊂ CHoCH`. **Only an MSS can produce a trade.** Both are logged as separate event
types, and the backtest reports the population of CHoCH-that-were-not-MSS with their forward
returns — that comparison is the direct measurement of whether the sweep-and-displacement
requirement adds anything, which is the central claim of the whole methodology.

### 6.7 Example

H4, EURUSD. Confirmed swings: SL₁ 1.0820, SH₁ 1.0910, SL₂ 1.0865 (HL), SH₂ 1.0985 (HH).
Trend `BULLISH`, `protected_low = SL₂ @ 1.0865`.

- Bar closes 1.0994 → `break_up(1.0985)` ✓ → **BOS bullish**. Protected low updates to the
  most recent confirmed swing low, still SL₂ 1.0865, then ratchets up as new higher lows
  confirm.
- Later, a bar prints `L = 1.0858`, `C = 1.0881` → wick below the protected low, close above.
  **Not a CHoCH.** Recorded as `INTERNAL_LIQUIDITY_GRAB` at 1.0865, which registers a
  `PROTECTED_SWING` sell-side liquidity sweep and opens a *bullish* setup candidate.
- Different path: a bar closes at 1.0851 → `break_down(1.0865)` ✓ → **CHoCH bearish**, trend
  flips. It becomes an **MSS** only if a confirmed buy-side sweep occurred within the last
  `choch.max_bars_after_sweep` bars and the down-leg displaces.

### 6.8 Edge cases

| Case | Ruling |
|---|---|
| No `protected_low` exists yet (trend just initialised) | CHoCH cannot fire. Only BOS is possible until a protected level is established |
| One bar breaks the swing high **and** closes below the protected low | Evaluate **BOS first, then CHoCH**, in that fixed order, using the state as it stands at bar open. Both events are logged. Rare but real on high-impact news bars |
| Gap over the level (Sunday open) | A gap that opens beyond the level and closes beyond it is a valid break. Tagged `gap_break = true`; gap breaks are reported separately since they are unfillable at the stated level |
| The swing that would be broken has not confirmed yet | No break. The engine waits; `swing.fractal_n` lag applies |
| Trend flips twice within `structure.min_bars_between_flips` (default 2) | Second flip is recorded but marked `WHIPSAW`; setups from a WHIPSAW flip are rejected with reason `STRUCTURE_WHIPSAW` |

### 6.9 Backtest

- **Golden file** of BOS/CHoCH events over the fixture series.
- **Invariant tests:** trend never flips without a CHoCH; `protected_low` is monotonically
  non-decreasing **between BOS events** within a bullish trend (see §6.4 and D-005 — v1.0
  stated this without the exception, which contradicted §6.4); every MSS has a parent CHoCH
  and a parent sweep in `source_ids`.
- **One break per level.** A swing is consumed when broken. Without this a single sustained
  move emits a BOS on every bar until the next swing confirms N bars later — measured at 274
  events where 49 were real. Same principle as §8.9 for liquidity.
- **Marginal-value measurement:** forward return distributions at +1/+4/+12 bars for (a) all
  CHoCH, (b) MSS only, (c) CHoCH-not-MSS. If (b) and (c) are statistically indistinguishable,
  the sweep-plus-displacement requirement adds nothing and that is a headline finding, not a
  reason to re-tune (`BACKTEST_PROTOCOL.md` §6.2).

---

## §7. Bias engine (Monthly / Weekly / Daily / H4) and MTF alignment

*(Required definitions 1–4.)*

### 7.1 One engine, four instances

Monthly, Weekly, Daily and H4 analysis are **the same code** (§5 + §6) instantiated on
different bar series with different `fractal_n`. There is no separate "monthly logic". This
is deliberate: four bespoke analyses would be four times the surface area for a lookahead bug
and would make cross-timeframe comparisons meaningless.

Each instance produces, at its scheduled trigger (§2.5):

```
TimeframeAnalysis {
  timeframe, computed_at, bar_index
  trend, last_event, protected_level
  swings[]            # confirmed only
  bias: BULLISH | BEARISH | NEUTRAL
  bias_reason         # enum, for the log
  liquidity[]         # levels contributed to the Liquidity Engine (§8)
  reference_levels    # period open / prev period high / prev period low
}
```

### 7.2 Bias rule (`bias.method = structure`, the default)

```
BULLISH  ⟺ trend == BULLISH
           ∧ last_event ∈ {BOS_BULLISH, CHOCH_BULLISH}
           ∧ last_close > protected_low.price
           ∧ age(last_event) ≤ bias.max_event_age[tf]

BEARISH  ⟺ mirror

NEUTRAL  ⟺ otherwise
```

`NEUTRAL` is therefore reached in four enumerated ways, each logged as `bias_reason`:

| `bias_reason` | Meaning |
|---|---|
| `INSUFFICIENT_HISTORY` | Fewer than two confirmed swings of each type |
| `TREND_UNDEFINED` | Structure state has not resolved |
| `EVENT_STALE` | Last structure event older than `bias.max_event_age[tf]` |
| `BEYOND_PROTECTED` | Price has closed beyond the protected level but the opposite CHoCH has not confirmed — genuinely between states |

`bias.max_event_age`: MN1 **6** bars, W1 **8**, D1 **10**, H4 **20**.

### 7.3 Alternative bias methods (ablation only)

Three alternatives, all objective, all measured against `structure`:

| Method | Rule |
|---|---|
| `premium_discount` | Dealing range = [last confirmed swing low, last confirmed swing high] on that TF. BULLISH if `close < range_low + 0.5 × range` (discount), BEARISH if above equilibrium. NEUTRAL inside a `bias.equilibrium_band` (default ±5% of range) around 50% |
| `close_vs_open` | BULLISH if the last closed period's `close > open`; NEUTRAL never occurs. The deliberately naive control |
| `ema` | BULLISH if `close > EMA(bias.ema_period)`, default 20. The conventional-TA control |

Their purpose is not to be adopted. It is to answer "does *structure-based* bias beat a
trivial trend proxy?" — if it does not, the top-down analysis is decoration and the finding
belongs in the report.

### 7.4 The MTF alignment state

```
alignment = (monthly_bias, weekly_bias, daily_bias, h4_bias)
score     = Σ over tf of  w[tf] × sign(bias[tf])          # BULLISH +1, BEARISH −1, NEUTRAL 0
```

Default weights `bias.weights` = MN1 **1.0**, W1 **1.0**, D1 **1.0**, H4 **1.0** (FROZEN;
equal weighting is chosen precisely because there is no evidence for any other weighting, and
inventing one is unjustified curve-fitting). Score range: −4 … +4.

Derived label, for reporting only:

| Condition | Label |
|---|---|
| all four same non-neutral direction | `FULL_ALIGNMENT` |
| MN1 and W1 agree, D1 or H4 differ | `LTF_CORRECTION` |
| MN1 and W1 disagree | `HTF_CONFLICT` |
| ≥2 NEUTRAL | `UNRESOLVED` |

### 7.5 The gate — how bias qualifies a trade

`bias.gate_mode` selects one of five behaviours. **`none` is the scientific control and MUST
be run**; without it there is no way to know whether the top-down analysis helps.

| `gate_mode` | Rule |
|---|---|
| `none` | Every H4 setup is taken. **Control group.** |
| `htf_only` | Require `sign(monthly) == sign(weekly) == setup_direction`; D1 and H4 ignored |
| `daily_h4` | Require `sign(daily) == sign(h4) == setup_direction` |
| `score` (**default**) | Require `score × setup_direction ≥ bias.min_score` (default **2**) |
| `strict` | All four must equal `setup_direction` |

**The counter-Monthly rule** (brief §2: "do not automatically trade against the Monthly
direction") is implemented as `bias.counter_monthly_action`:

| Value | Behaviour |
|---|---|
| `block` (**default**) | A setup opposing a non-neutral Monthly bias is rejected, reason `COUNTER_MONTHLY` |
| `derisk` | Allowed at `risk.counter_monthly_multiplier` × normal risk (default 0.5) |
| `allow` | No special treatment (used with `gate_mode = none`) |

`block` is default because it is the brief's stated intent, **not** because it is known to be
correct. `derisk` and `allow` are measured in ablation. Rejected setups are fully logged
(§21.3), so the counterfactual "what would counter-Monthly trades have returned?" is directly
answerable from the rejection log without a second backtest run.

### 7.6 Worked alignment example

```
Monthly:  BULLISH   (BOS up 2 months ago, price above protected low)
Weekly:   BULLISH   (HH/HL intact)
Daily:    BEARISH   (CHoCH down 3 days ago)
H4:       BEARISH
score = +1 +1 −1 −1 = 0        label = LTF_CORRECTION
```

- A **bullish** H4 setup: `score × (+1) = 0 < 2` → rejected under `score` mode
  (`BIAS_SCORE_BELOW_MIN`). Under `htf_only` it would be **accepted** (MN1 and W1 both
  bullish). The two modes disagree here, which is exactly the case the ablation must resolve.
- A **bearish** H4 setup: `score × (−1) = 0 < 2` → rejected; and additionally rejected by
  `counter_monthly_action = block`.

### 7.7 The Monthly-bias power problem — a required disclosure

Five years of data contains 60 monthly bars, of which perhaps 6–12 are independent regimes
under a slow-moving structural definition. Any subgroup statistic conditioned on Monthly bias
therefore has an effective sample size in the single digits, whatever the trade count looks
like — the trades within one monthly regime are not independent observations of that regime.

**Rulings:**

1. Monthly bias parameters are **FROZEN**. They are never optimised.
2. Its effect is reported **only** as an ablation delta (gate on vs off) with a bootstrap CI
   computed on **monthly-block-resampled** trades, not on individual trades
   (`BACKTEST_PROTOCOL.md` §5.4). Block bootstrap is what stops the CI from being a fiction.
3. The report MUST state the effective sample size next to any Monthly claim.
4. If the ablation delta's CI spans zero — the expected outcome — the conclusion is
   "no measurable effect at this sample size", **not** "helps slightly".

The same reasoning applies with less force to Weekly bias (≈260 bars over 5 years).

### 7.8 Backtest

- Bias series (one value per period per TF) is dumped and reviewed against a chart for the
  three development symbols before any strategy result is generated. A bias engine that
  disagrees with an eyeball reading of trend on a monthly chart is broken, and that is worth
  finding before it contaminates thousands of trades.
- Bias transitions per year are counted; a method producing more than ~30 D1 bias flips a
  year is reacting to noise and is flagged.
- Every trade record carries all four bias values and the `gate_mode` that admitted it, so
  every breakdown the brief asks for (§26) is a group-by on the trade table.

---

## §8. Liquidity Engine

*(Required definition 8.)*

### 8.1 What a liquidity level is, operationally

A liquidity level is **a price at which resting stop orders are inferred to sit, identified by
a rule that could have been applied at the time the level formed.** No level in this system is
identified by how it looks. Every level traces to one of nine enumerated sources, each with a
formation rule and a formation timestamp.

### 8.2 Object

```
LiquidityLevel {
  id, symbol
  side: BUY_SIDE | SELL_SIDE          # BUY_SIDE = above price (stops of shorts / breakout buys)
  source: enum (§8.3)
  timeframe                            # the TF that produced it
  tier: 1 | 2 | 3                      # §8.6, drives confirmation TF and ranking
  price                                # the exact level to be swept
  formed_at, confirmed_at
  strength: int                        # count of merged constituent levels (§8.7)
  age_bars                             # in units of its own timeframe
  status: ACTIVE | SWEPT | INVALIDATED | EXPIRED | CONSUMED
  swept_by                             # sweep event id, when applicable
  source_ids[]
}
```

### 8.3 Sources — the complete enumeration

| # | `source` | Side | Price | Confirmed at |
|---|---|---|---|---|
| 1 | `PREV_DAY_HIGH` / `PREV_DAY_LOW` | BUY / SELL | High / low of last closed D1 | Day boundary |
| 2 | `PREV_WEEK_HIGH` / `PREV_WEEK_LOW` | BUY / SELL | High / low of last closed W1 | Week boundary |
| 3 | `PREV_MONTH_HIGH` / `PREV_MONTH_LOW` | BUY / SELL | High / low of last closed MN1 | Month boundary |
| 4 | `SESSION_HIGH` / `SESSION_LOW` | BUY / SELL | High / low of a CLOSED session instance | Session close |
| 5 | `SWING_HIGH` / `SWING_LOW` | BUY / SELL | `H_i` / `L_i` of a confirmed swing | Swing `confirmed_at` |
| 6 | `EQUAL_HIGHS` / `EQUAL_LOWS` | BUY / SELL | §8.5 | Confirmation of the last constituent swing |
| 7 | `PROTECTED_SWING` | BUY / SELL | Current `protected_high` / `protected_low` (§6.4) | On becoming protected |
| 8 | `RANGE_HIGH` / `RANGE_LOW` | BUY / SELL | Extremes of a detected consolidation (§8.5.2) | Range confirmation |
| 9 | `PREV_SESSION_EXTREME` | BUY / SELL | Prior instance of the same session, N days back | That session's close |

> **Phase 6 implementation notes (D-006), affecting three rows of this table.**
> **Source 7 (`PROTECTED_SWING`) does not produce independent levels.** The protected low
> *is* a confirmed swing low, so 95% of what it emits duplicates a `SWING_*` level at the
> identical price and merges on admission. Its real effect is `+1` strength on the protected
> swing — defensible, but it means the source will show a near-zero sweep rate in Phase 7,
> which must not be read as the source failing.
> **Source 9 (`PREV_SESSION_EXTREME`) is not implemented and is folded into source 4.** A
> tier-3 level lives 5 D1 bars, so yesterday's session extreme is still an ACTIVE
> `SESSION_*`; a second name for it would double-count every sweep.
> **Source 4 excludes `OVERLAP` and the killzones.** An overlap extreme coincides with the
> London or New York extreme on 90% of days — it is a sub-window of two sessions already
> counted, not an independent pool. Only sessions whose configured role includes `liquidity`
> contribute.

**Not liquidity, deliberately:** round numbers, Fibonacci levels, pivot points, moving
averages, option strikes. Each would be defensible; each also multiplies the level population
and therefore the number of "sweeps" available to find. v1.0 restricts itself to levels
derived from realised price structure. Round numbers are the strongest candidate for a v1.1
ablation and are noted as such in `OPEN_QUESTIONS.md` Q11.

### 8.4 The running-extreme prohibition

**MUST NOT** create a liquidity level from the extreme of a FORMING period or session. The
current day's high is not liquidity; the *previous* day's high is. Reason: a level that is
still being made cannot be swept, and code that allows it will report a "sweep" whenever price
pulls back from a new high — which is most bars, and which fabricates the strategy's central
event out of nothing.

### 8.5 Equal highs / equal lows, and ranges

#### 8.5.1 Equal highs (EQH); mirror for EQL

Given confirmed swing highs `s_1 … s_m` ordered by `formed_index`, a cluster `Q` qualifies as
EQH when:

```
|Q| ≥ eq.min_touches                                                 (default 2)
∀ a,b ∈ Q : |price_a − price_b| ≤ eq.tolerance_atr × ATR_ref          (default 0.10)
∀ adjacent a,b ∈ Q : |index_a − index_b| ≥ eq.min_separation_bars     (default 3)
max(index) − min(index) ≤ eq.max_span_bars                            (default 50)
```

Cluster price: `eq.cluster_price ∈ {extreme, mean}` = **extreme** (FROZEN) — the max high for
EQH, the min low for EQL. The sweep must clear *all* the stops resting above the highs, so the
extreme is the level that matters; using the mean would report a sweep while part of the
cluster is still untouched.

`strength = |Q|`. Confirmed at the `confirmed_at` of the last constituent swing.

#### 8.5.2 Ranges

A consolidation range is detected when, over a window of `range.window_bars` (default 20) H4
bars:

```
(max(H) − min(L)) ≤ range.max_height_atr × ATR_ref            (default 2.0)
∧ count of bars closing outside the middle 50% ≤ range.max_breakout_bars   (default 3)
```

`RANGE_HIGH = max(H)`, `RANGE_LOW = min(L)`, confirmed at the close of the window's last bar.
Ranges are the least well-founded source here and are `ABLATION-ONLY` — enabled off by
default (`liq.enable_range_source = false`).

### 8.6 Tiers

Tier drives (a) which confirmation timeframe the setup uses (§11.2) and (b) ranking (§8.8).

**Decision D-002: every tier confirms on H4.** (See `DECISIONS.md`.)

| Tier | Sources | Confirmation TF (**D-002 default**) | Ablation variant |
|---|---|---|---|
| **1** | `PREV_MONTH_*`, `PREV_WEEK_*`, `EQUAL_*` on D1/H4, `SWING_*` on D1 | **H4** | H4 |
| **2** | `PREV_DAY_*`, `SWING_*` on H4, `PROTECTED_SWING` on H4 | **H4** | H4 |
| **3** | `SESSION_*`, `PREV_SESSION_EXTREME`, `RANGE_*` | **H4** | H1 |

`liq.tier_confirmation_tf` is now `{1: H4, 2: H4, 3: H4}`. The tier map with `3: H1` is
retained as the primary ablation, so the cost of the H4-only rule is measured rather than
argued about. **Tier still governs ranking (§8.8) and expiry (§8.7) — it has simply stopped
governing the confirmation timeframe.**

Three things D-002 does **not** change, because the mistake would be easy to make:

1. **Session levels are still computed from M15 data** (§3.6). Accurate session extremes need
   sub-H4 resolution regardless of what timeframe confirms a sweep of them; an H4 bar straddles
   session boundaries. `session.source_tf = M15` stands.
2. **Session liquidity remains an enabled source.** Only the resolution at which its sweeps
   and CHoCHs are confirmed has changed.
3. **Entry fills are still resolved sub-H4** via `backtest.intrabar_mode = m1_path` (§17.5).
   With signals on H4, M1 path resolution is now the *only* source of sub-H4 precision in the
   system, which raises rather than lowers the importance of having M1 data.

The measurable cost of D-002 is a **smaller and slower setup population**. The Phase 9 funnel
gate (§27) is the checkpoint that decides whether it is affordable, and it is now evaluated on
the **development subset as well as the full universe** — three symbols over the in-sample
period is where a thin funnel will show up first.

### 8.7 Lifecycle

```
ACTIVE       created and untouched
  → SWEPT        a sweep event confirmed against it (§9)
  → INVALIDATED  price accepted through it (below)
  → EXPIRED      age exceeded liq.max_age_bars[tier]
  → CONSUMED     a setup that used it reached TRADE_CLOSED or SETUP_INVALIDATED
```

**Invalidation (acceptance) rule.** A BUY_SIDE level is INVALIDATED when
`liq.invalidate_closes` (default **2**) consecutive closes on its confirmation TF satisfy
`C > price + liq.invalidate_buffer_atr × ATR_ref` (default 0.25). Mirror for SELL_SIDE.

This is the distinction between *swept* (poked and rejected) and *broken* (accepted through).
Without it, every level that price simply trades past stays ACTIVE forever and eventually
produces a spurious sweep when price returns months later.

`liq.max_age_bars`: tier 1 **90** D1 bars, tier 2 **30** D1 bars, tier 3 **5** D1 bars.
`PREV_MONTH_*` never expires by age; it is replaced monthly.

### 8.8 Merging and ranking

**Merge.** Two ACTIVE levels on the same side within `liq.merge_tolerance_atr × ATR_ref`
(default 0.10) merge into one: `price` = the more extreme, `tier` = the lower (stronger)
tier, `strength` = sum, `source_ids` = union. A previous week high sitting one pip above a
previous day high is one level, not two, and treating it as two double-counts every sweep of
it.

**In-play filter.** A level is a candidate only when
`|price − last_close| ≤ liq.max_distance_atr × ATR_ref^H4` (default **5.0**).

**Rank** (used to cap concurrent setups, §14.4):

```
rank_score = w_tier[tier] + w_strength × min(strength, 4) + w_recency × recency_norm
             + w_bias × bias_alignment
```

with `liq.rank_weights` = tier {1:3.0, 2:2.0, 3:1.0}, strength 0.5, recency 1.0, bias 1.0.
`bias_alignment` = +1 when sweeping this level would produce a setup agreeing with the MTF
gate, else 0. **These weights are FROZEN** — they order candidates, they do not decide trades,
and tuning an ordering function is a very efficient way to overfit without appearing to.

### 8.9 Edge cases

| Case | Ruling |
|---|---|
| Level created and swept by the same bar that created it | Impossible by construction: a level's `confirmed_at` is at or after the close of its formation bar, and a sweep requires a *later* bar. Asserted in test |
| Two levels at the identical price, different sides | Cannot occur; side is determined by position relative to price at creation. If price crosses, the level does not change side — it invalidates or expires |
| A level is swept while an earlier setup on it is still live | The second sweep is ignored; a level's status is `SWEPT` only once. Re-sweeps of the same price require a newly formed level |
| Extreme illiquidity (Sunday open, NFP) creates a 200-pip level in one bar | Level is created but tagged `formed_in_gap` / `formed_in_data_suspect`. Reported separately |
| More than `liq.max_active_levels` (default 40) ACTIVE per symbol | Lowest-ranked are pruned to the cap. Pruning is logged; a pruned level never returns |

### 8.10 Backtest

- **Population report:** levels created per source per month per symbol. A source producing an
  order of magnitude more levels than others dominates the trade population by construction
  and must be identified before results are interpreted.
- **Sweep-rate report:** proportion of levels reaching SWEPT vs INVALIDATED vs EXPIRED, by
  source. A source whose levels are almost never swept contributes nothing; a source whose
  levels are almost always swept is not identifying a barrier.
- **The shuffled-liquidity control** (`BACKTEST_PROTOCOL.md` §6.3): re-run the whole strategy
  with levels replaced by random prices drawn from the same distance-from-price distribution.
  If performance is unchanged, liquidity identification adds nothing and the edge, if any, is
  in the CHoCH/displacement machinery. This is the single most informative test in the suite.

---

## §9. Liquidity Sweep

*(Required definition 9.)*

### 9.1 Rule

A sweep is a two-part event: **penetration** then **reclaim**, both bounded in size and time.

For a SELL_SIDE level at price `P` on confirmation timeframe `TFc`, with `A = ATR_ref^TFc`
evaluated at the trigger bar:

```
TRIGGER      bar s is the first bar with  L_s < P
             (and level.status == ACTIVE, level.confirmed_at ≤ open_time(s))

EXTREME      sweep_low = min(L_s … L_j) over the confirmation window

RECLAIM      ∃ j ∈ [s, s + sweep.max_confirmation_bars − 1] :
                 C_j > P + sweep.reclaim_buffer_atr × A

PENETRATION  pen = P − sweep_low
             sweep.min_penetration_atr × A  ≤  pen  ≤  sweep.max_penetration_atr × A

WICK         (optional) lw_s / range_s ≥ sweep.min_wick_ratio

CLOSE POS    (optional) (C_j − sweep_low) / (max(H_s..H_j) − sweep_low) ≥ sweep.min_close_position

CONFIRMED    ⟺ TRIGGER ∧ RECLAIM ∧ PENETRATION ∧ WICK ∧ CLOSE POS
```

Mirror for BUY_SIDE (`H_s > P`, reclaim `C_j < P − buffer`, `pen = sweep_high − P`).

`confirmed_at = close_time(j)`. The sweep's identity is `(level_id, s, j, sweep_low)`.

**Failure of RECLAIM within the window is not "no event".** It sets the level to
`INVALIDATED` with reason `ACCEPTED_THROUGH` and emits a `SWEEP_FAILED` event. That event is
logged and analysed: the ratio of confirmed sweeps to failed sweeps per source is a direct
measure of whether a level is a real barrier.

### 9.2 Parameters and their justification

| Parameter | Default | Class | Why it exists |
|---|---|---|---|
| `sweep.max_confirmation_bars` | 3 | TUNABLE | 1 = pure single-bar rejection wick; larger allows a two-bar poke-and-reclaim. Bounded at 5 |
| `sweep.min_penetration_atr` | 0.05 | FROZEN | Excludes a sub-pip nick, which is usually a spread artefact rather than a stop run |
| `sweep.max_penetration_atr` | 1.00 | TUNABLE | Above this the move is a breakout, not a sweep. This is the parameter that separates the two regimes and it is the one most likely to matter |
| `sweep.reclaim_buffer_atr` | 0.00 | ABLATION | Requiring the reclaim close to clear the level by a margin |
| `sweep.min_wick_ratio` | 0.00 (off) | ABLATION | Classic "long wick rejection". Test 0.3 / 0.5 |
| `sweep.min_close_position` | 0.00 (off) | ABLATION | Where in the sweep range the reclaim closes. Test 0.5 / 0.66 |
| `sweep.require_prior_level_age_bars` | 3 | FROZEN | **Applies to swing-derived levels only** — see §9.2.1. Measured in bars of the timeframe the swing was detected on |

Note the interaction, which the ablation must handle jointly rather than one factor at a time:
`min_wick_ratio` and `min_close_position` and `max_confirmation_bars` are near-substitutes.
With `max_confirmation_bars = 1` the wick ratio is nearly implied. Testing them independently
will produce three "significant" parameters that are one effect.

#### 9.2.1 The level-age rule applies to swing-derived levels only (corrected)

The v1.0 text read "the level must have existed for ≥3 bars of its own TF", applied to every
source. Under D-002 that is fatal, and it was wrong before D-002 too:

> An `ASIA_RANGE` low is confirmed at the Asian close (~09:00 UTC). Three bars of the H4
> confirmation timeframe is **12 hours**. The level would not become sweepable until 21:00
> UTC — after London has closed. The single setup the brief names as the flagship
> (Asian low → London sweep) would never have fired once, in five years, on any symbol.

Under the tier map with H1 confirmation it was less obviously fatal and therefore worse: three
H1 bars pushes the earliest sweepable moment to 12:00 UTC, which silently deletes the London
open and keeps only the later part of the session. That is the kind of defect that shows up as
"the strategy just doesn't trade the London open much", and gets rationalised rather than
found.

**Corrected rule:**

```
age_ok(level) =
    if level.source in {SWING_HIGH, SWING_LOW, PROTECTED_SWING, EQUAL_HIGHS, EQUAL_LOWS,
                        RANGE_HIGH, RANGE_LOW}:
        bars_since(level.confirmed_at, on = level.detection_tf)
            ≥ sweep.require_prior_level_age_bars
    else:                       # PREV_DAY_*, PREV_WEEK_*, PREV_MONTH_*, SESSION_*,
        true                    # PREV_SESSION_EXTREME
```

**Rationale.** The rule exists to stop a level being swept by the same impulse that created
it. A period-derived level cannot be: it is the extreme of a *completed* day, week, month or
session, so by construction the price action that formed it is already over when the level
comes into existence. A swing-derived level genuinely can be — a swing high confirmed two bars
ago is still inside the move that made it — so for those sources the rule stands, measured in
bars of the timeframe the swing was *detected* on (D1 swings age in D1 bars), not the timeframe
that happens to confirm the sweep.

`sweep.require_prior_level_age_bars` remains FROZEN at 3. What changed is its domain, not its
value. The pseudocode below calls `age_ok(lvl)` rather than comparing `bar_age` directly.

### 9.3 Pseudocode

```
on_bar_close(j, tf):
  for lvl in liquidity.active(side=SELL_SIDE, confirmation_tf=tf):
      w = sweep_window.get(lvl.id)
      if w is None:
          if L_j < lvl.price and age_ok(lvl):              # §9.2.1
              w = open_window(lvl, trigger=j, sweep_low=L_j, atr=ATR_ref(j))
          else: continue
      else:
          w.sweep_low = min(w.sweep_low, L_j)

      pen = lvl.price − w.sweep_low
      if pen > sweep.max_penetration_atr * w.atr:
          close_window(w); emit(SWEEP_FAILED, lvl, reason=OVER_PENETRATION)
          lvl.status = INVALIDATED; continue

      if C_j > lvl.price + sweep.reclaim_buffer_atr * w.atr:
          if pen < sweep.min_penetration_atr * w.atr:
              close_window(w); emit(SWEEP_REJECTED, reason=UNDER_PENETRATION); continue
          if not wick_ok(w.trigger) or not close_pos_ok(w, j):
              close_window(w); emit(SWEEP_REJECTED, reason=FILTER); continue
          emit(SWEEP_CONFIRMED, level=lvl, trigger=w.trigger, confirm=j,
               sweep_extreme=w.sweep_low, at=close_time(j))
          lvl.status = SWEPT
          open_setup(direction=BULLISH, sweep=..., state=LIQUIDITY_SWEPT)     # §14
          close_window(w); continue

      if bars_since(w.trigger) ≥ sweep.max_confirmation_bars:
          close_window(w); emit(SWEEP_FAILED, lvl, reason=NO_RECLAIM)
          lvl.status = INVALIDATED
```

### 9.4 Multi-level sweeps

One bar routinely penetrates several stacked levels. Rule:

- Emit **one `SWEEP_CONFIRMED` per level.**
- Group all sweeps confirmed on the same bar, same side, into a `SweepCluster`, anchored on
  the **deepest** level (lowest price for a sell-side cluster).
- **A cluster opens exactly one setup** (`cluster.strength = Σ level.strength`). Without this
  rule, three stacked levels produce three near-identical trades and triple the apparent
  sample size while tripling correlated risk.

### 9.5 Worked example (the brief's own case, completed)

EURUSD, **H4 confirmation** under D-002, UTC H4 grid under D-001. Winter (London UTC+0, New
York UTC−5). `ATR_ref^H4 = 0.00380`.

```
Level:  ASIA_RANGE low (NY 20:00–00:00 = 01:00–05:00 UTC), SELL_SIDE, tier 3,
        P = 1.16500, confirmed_at 05:00 UTC.
        Session-derived → age rule exempt (§9.2.1), sweepable immediately.
        Earliest eligible bar is the 08:00 UTC H4 bar, since §9.1 requires
        level.confirmed_at ≤ open_time(s) and 05:00 > 04:00.

Bar s  (08:00–12:00 UTC, London morning):
        O 1.16560   H 1.16600   L 1.16420   C 1.16540
```

- TRIGGER at `s`: `1.16420 < 1.16500` ✓. `sweep_low = 1.16420`.
- RECLAIM on the same bar: `C = 1.16540 > 1.16500 + 0` ✓ → **single-bar sweep**, legal (§9.6).
- `pen = 0.00080 = 0.21 × ATR^H4`. Within [0.05, 1.00] ✓.
- Wick filter (at 0.3): `lw = min(1.16560, 1.16540) − 1.16420 = 0.00120`,
  `range = 0.00180`, ratio `0.667` ✓.
- Close position (at 0.5): `(1.16540 − 1.16420) / (1.16600 − 1.16420) = 0.667` ✓.

**SWEEP CONFIRMED**, `confirmed_at` = **12:00 UTC** (close of the 08:00 bar). Setup opens in
state `LIQUIDITY_SWEPT`, direction BULLISH.

**Read the clock, because this is D-002's real cost.** The sweep happened during the London
morning but is only *knowable* at 12:00 UTC. `sweep.same_bar_choch_allowed = false`, so the
earliest possible MSS is the close of the 12:00 bar — **16:00 UTC**, the New York morning — and
the earliest realistic one is later still. Under the H1 tier map the same sweep would have been
confirmed around 10:00 UTC with an MSS possible from 11:00. That difference is the entire
substance of Q7, and it is now measured by the sweep-session × entry-session matrix rather than
argued about.

**No entry is permitted at this point.** The state machine's only legal outgoing transitions
are to `WAITING_FOR_DISPLACEMENT` / `WAITING_FOR_CHOCH` or to `SETUP_INVALIDATED`
(`STATE_MACHINE.md` §3).

### 9.6 Edge cases

| Case | Ruling |
|---|---|
| Reclaim on the trigger bar itself | Legal, `s == j`, a single-bar rejection. Tagged `single_bar_sweep`; reported separately because it is the fastest and possibly the most distinct sub-population |
| Same bar sweeps and closes beyond the CHoCH reference | Sweep is confirmed; CHoCH **MUST NOT** be confirmed on the same bar (`sweep.same_bar_choch_allowed = false`, FROZEN). The "WAIT" step is structural, not advisory. Occurrences are logged as `SAME_BAR_CHOCH_BLOCKED` so the cost of the rule is measurable |
| Weekend gap over the level, reopening beyond it | No penetration bar exists. Level is INVALIDATED with reason `GAPPED_THROUGH` — never swept |
| Price penetrates, reclaims, penetrates again inside the window | The window tracks the running extreme; the first qualifying reclaim confirms. `sweep_low` is the minimum over the whole window, so the stop (§16) sits below the deepest point |
| Penetration exceeds `max_penetration_atr` and *then* reclaims | Not a sweep. Level INVALIDATED. This is the deliberate boundary between sweep and breakout-and-retest, and misclassifying it in either direction is the main way this engine can be wrong |
| Level's confirmation TF differs from the bar TF being processed | Sweeps are evaluated per confirmation TF; a tier-3 level is only ever tested against H1 closes |

### 9.7 Backtest

- Sweep counts by source, side, session, symbol, per month. Stability of these counts over
  time is a prerequisite for trusting any downstream statistic.
- **Forward-return study, independent of the strategy:** distribution of returns at +1/+3/+6/
  +12 bars after `SWEEP_CONFIRMED`, versus a matched control sample of bars with the same
  session/volatility profile and no sweep. If confirmed sweeps show no directional edge at
  all, the strategy's foundation is unsupported, and the correct response is to report that,
  not to add filters until it appears.
- Parameter surface for `max_penetration_atr` × `max_confirmation_bars` reported as a heatmap
  of expectancy, with the plateau requirement of `BACKTEST_PROTOCOL.md` §5.5.

---

## §10. Displacement

*(Required definition 10.)*

### 10.1 Rule

Displacement is evaluated over a **leg**, not a single bar, because a two-bar drive and a
one-bar drive of the same magnitude are the same event.

Let the candidate leg be bars `[a … b]` where `b` is the CHoCH break bar and
`a = max(sweep_extreme_bar, b − disp.max_leg_bars + 1)`. With `A = ATR_ref(b)`:

```
net        = C_b − L_a_min      (bullish)         where L_a_min = min(L_a … L_b)
             H_a_max − C_b      (bearish)
gross      = Σ_{i=a..b} range_i
bodies     = Σ_{i=a..b} body_i  counting only bars closing in the leg direction
dir_bars   = count of bars in [a..b] with (C_i > O_i) for bullish, (C_i < O_i) for bearish

DISPLACEMENT(a,b) ⟺
      net           ≥ disp.min_leg_atr        × A        (default 1.5)
   ∧  bodies/gross  ≥ disp.min_body_ratio                (default 0.50)
   ∧  dir_bars      ≥ disp.min_directional_bars          (default 1)
   ∧  (¬disp.require_fvg  ∨  ∃ FVG in [a..b] with direction = leg direction)   (default true)
   ∧  (b − a + 1)   ≤ disp.max_leg_bars                  (default 3)
```

### 10.2 Why an FVG requirement is the default

A displacement is, mechanically, price moving far enough fast enough that the three-bar ranges
fail to overlap. Requiring an FVG inside the leg (§12) is therefore not an extra condition
layered on top — it is the same condition expressed structurally rather than in ATR units, and
it has the useful property of producing an *object* (the gap) that entry model C can use.
`disp.require_fvg = true` is the default for that reason, and `false` is an ablation run.

### 10.3 Single-bar variant

`disp.mode ∈ {leg, bar, either}` = **leg** (default). The `bar` variant is the classic
formulation and is retained for ablation:

```
DISPLACEMENT_BAR(i) ⟺ range_i ≥ disp.min_range_atr × ATR_ref(i)     (default 1.5)
                     ∧ body_i / range_i ≥ disp.min_body_ratio        (default 0.50)
                     ∧ sign(C_i − O_i) = leg direction
```

### 10.4 Example

H4 EURUSD, `ATR_ref = 0.00450`. Leg = 2 bars after a sell-side sweep at 1.0820:

```
bar a:  O 1.08240  H 1.08310  L 1.08150  C 1.08290     range 0.00160  body 0.00050  up
bar b:  O 1.08290  H 1.08760  L 1.08270  C 1.08720     range 0.00490  body 0.00430  up
```

- `L_a_min = 1.08150`, `net = 1.08720 − 1.08150 = 0.00570 = 1.27 × ATR` → **fails**
  `min_leg_atr = 1.5`. Displacement **not** confirmed; the setup stays in
  `WAITING_FOR_DISPLACEMENT` (or the CHoCH is recorded as CHoCH-not-MSS).
- Had bar `b` closed at 1.08830: `net = 0.00680 = 1.51 × ATR` ✓;
  `bodies/gross = (0.00050 + 0.00540)/(0.00160 + 0.00600) = 0.776` ✓; `dir_bars = 2` ✓; and a
  bullish FVG exists if `L_b > H_a_prev` for the preceding bar. → **DISPLACEMENT CONFIRMED.**

The example is deliberately one that fails. A threshold that never rejects anything is not a
filter, and the ablation must show the rejection rate per setting.

### 10.5 Edge cases

| Case | Ruling |
|---|---|
| The leg contains the sweep bar itself | Permitted and normal — the sweep low is the leg origin. `a` is clamped to the sweep extreme bar so the leg is never measured from before the sweep |
| Leg spans a weekend gap | Allowed but tagged. Gap-driven "displacement" is unfillable and is reported separately |
| `gross = 0` (all doji) | `bodies/gross` undefined → displacement fails. Guarded explicitly, never a division error |
| Displacement satisfied over `[a..b]` but not `[a'..b]` for a different valid `a` | The leg origin is fixed by the definition (`max(sweep_extreme_bar, b − max_leg_bars + 1)`). No search over origins — searching for the window that passes is how a filter becomes a formality |
| A single bar that both sweeps and displaces | Displacement can be satisfied, but the CHoCH still cannot confirm on that bar (§9.6). The displacement is carried forward and re-evaluated on the break bar |

### 10.6 Backtest

- Distribution of `net/ATR` for all post-sweep legs, with the threshold marked. If the default
  1.5 sits in the middle of a smooth unimodal distribution, it is an arbitrary cut and should
  be reported as such rather than defended.
- Ablation `disp.min_leg_atr ∈ {0 (off), 1.0, 1.25, 1.5, 2.0, 2.5}`. Requirement: a
  **plateau**, not a peak (`BACKTEST_PROTOCOL.md` §5.5).
- Joint ablation with `disp.require_fvg`, because the two are partially redundant by §10.2.

---

## §11. CHoCH reference selection and MSS confirmation

This section answers the four questions the brief asks in its §10, explicitly.

### 11.1 Which swing must be broken?

For a **bullish** setup following a sell-side sweep with extreme at bar `s`:

```
reference_mode = major:                                          (default)
    candidates = { swing highs SH : SH.formed_index < s
                                  ∧ SH.confirmed_at ≤ close_time(s)
                                  ∧ s − SH.formed_index ≤ choch.max_reference_lookback }
    walk candidates from most recent backwards; return the first SH satisfying
        max(H_(SH.formed_index+1) … H_s) ≤ SH.price          # unbroken since it formed
    if none → SETUP_INVALIDATED(NO_CHOCH_REFERENCE)

reference_mode = micro:
    N_micro = choch.micro_fractal_n                            (default 1)
    the first swing high, detected with N_micro on the confirmation TF, whose
    formed_index > s and which is confirmed after the sweep
    if none within choch.max_bars_after_sweep → SETUP_INVALIDATED(CHOCH_TIMEOUT)
```

Mirror for bearish (swing lows, `min(L…) ≥ SL.price`).

**`confirmed_at ≤ close_time(s)` must be read against the swing set as it stood at bar `s`, not
against the finished one** (D-009 §4). §5.4 normalisation removes a swing that a later, more
extreme same-kind swing supersedes, and it removes it from every earlier bar too — so the
finished store denies at `s` a swing that was live at `s`. The error is conservative rather
than lookahead, and nearly self-cancelling (the move that supersedes a swing high has usually
already broken it, which fails the unbroken test anyway), but it is real on 0.17% of fixture
sweeps.

The two modes are genuinely different strategies and **both MUST be tested**:

| | `major` | `micro` |
|---|---|---|
| Level broken | The last unbroken swing high **before** the sweep | The first pullback high **after** the sweep |
| Entry timing | Later, larger confirmation | Earlier, smaller confirmation |
| Stop distance | Larger (from sweep low to a higher reference) | Smaller |
| Failure mode | Move is over before confirmation | Confirms on noise |

`choch.reference_mode` = **major** (default), `micro` is the primary alternative in the
ablation. Additional constraint in both modes:

```
|reference.price − sweep_extreme| ≤ choch.max_reference_distance_atr × ATR_ref     (default 3.0)
```

A reference so far from the sweep that the resulting stop is untradeable is rejected up front
(`REFERENCE_TOO_FAR`) rather than surviving to be rejected later by the risk layer — the
distinction matters for the rejection log, which is used for counterfactual analysis.

### 11.2 Must the break be by candle close, and on which timeframe?

**Yes, by close.** `structure.break_confirmation = close` (§6.3), FROZEN. A wick break of the
CHoCH reference is exactly what a liquidity sweep of that reference looks like; accepting wick
breaks makes the system trade the pattern it is designed to fade.

**Timeframe: H4, for every liquidity tier** (decision D-002, §8.6). The setup timeframe and
the confirmation timeframe are now the same thing: liquidity, the bias gate, displacement, the
CHoCH break and trade management are all H4 objects. The only sub-H4 machinery remaining in the
system is (a) the M15 session engine that computes session extremes (§3.6) and (b) M1 intrabar
path resolution for fills (§17.5). Neither participates in a signal decision.

`execution.confirmation_timeframe_override ∈ {auto, H4, H1, M15}` = **auto**, which under
D-002 resolves to H4 for every tier. Two required ablation runs remain: the tier map with
session liquidity on H1, and everything on H1.

**The arithmetic consequence, stated once more because it governs how results must be read.**
Minimum sweep→MSS distance is 2 H4 bars (8 hours); `choch.max_bars_after_sweep = 12` gives a
2-day window; `entry.pending_expiry_bars = 6` adds up to another day. A setup therefore spans
up to ~3.5 days from sweep to entry expiry, and a filled trade up to 5 days beyond that
(`exit.max_bars_in_trade = 30`). **This is a multi-session swing model, not an intraday one.**
Every parameter default in §9–§17 was chosen against that reading, and the session filter
(`filter.allowed_execution_sessions`) is consequently much weaker than it would be on H1 — the
entry session is largely determined by when the MSS bar happens to close, not by a choice.

### 11.3 How much displacement is required?

§10, applied to the leg ending at the break bar. This is the difference between CHoCH (§6.5)
and MSS (§6.6).

### 11.4 How many candles are allowed after the sweep?

```
choch.max_bars_after_sweep   default 12 bars of the confirmation TF
choch.min_bars_after_sweep   default 1   (the "WAIT", FROZEN — see §9.6)
```

12 H4 bars is two trading days; 12 H1 bars is half a session. Both are TUNABLE within
[4, 24]. Exceeding the window is `SETUP_INVALIDATED(CHOCH_TIMEOUT)` — logged, and the
forward return from the timeout point is recorded so "we waited too long / not long enough"
is answerable from data.

**Amended by D-009 §3: the WAIT is not the only lower bound.** It is measured from the sweep
*extreme* bar `s`, but a sweep is not knowable until its *confirm* bar, up to
`sweep.max_confirmation_bars` later. Enforcing the WAIT alone would admit a break judged
against a sweep that had not yet happened as far as a live engine was concerned. Both bind:

```
first_bar = max(s + choch.min_bars_after_sweep,
                confirm_bar + (0 if sweep.same_bar_choch_allowed else 1))
```

**Measured consequence (Phase 9): this floor, not the ceiling, is what binds.** Every MSS on
the fixture lands within 7 bars of the sweep, with the mass at 2 — the first admissible bar for
most candidates — so `max_bars_after_sweep` changes nothing above 8 despite being one of the
eight TUNABLE parameters. See D-009 §7.

### 11.5 MSS confirmation, complete

```
MSS_CONFIRMED(bullish) at bar b ⟺
      setup.state ∈ {WAITING_FOR_CHOCH, WAITING_FOR_DISPLACEMENT, DISPLACEMENT_CONFIRMED}
   ∧  b − s ≥ choch.min_bars_after_sweep
   ∧  b − s ≤ choch.max_bars_after_sweep
   ∧  break_up(b, reference.price)                                   (§6.3)
   ∧  DISPLACEMENT(a, b)                                             (§10.1)
   ∧  min(L_s … L_b) ≥ sweep_extreme − invalidate.new_extreme_atr × ATR_ref
   ∧  no opposing SWEEP_CONFIRMED in (s, b]
   ∧  MTF gate passes at close_time(b)                               (§7.5)
```

**Clauses 5 and 6 are evaluated as clauses here and listed as setup invalidations in §11.6;
D-009 §1 reconciles the two.** Both are tracked as sticky flags over `(s, b]` and read at the
break bar, which is this section literally, and both surface as terminal outcomes when no break
ever comes, which is §11.6. The difference is not cosmetic: under a strict invalidation reading
a setup that makes a new extreme and *then* breaks its reference is never recorded at all, and
that is precisely the population §6.9's marginal-value test needs.

The fifth clause is important and easy to omit: if price made a **new low** materially below
the sweep extreme between the sweep and the break, the sweep failed — the level was accepted
through — and any subsequent upward break is a bounce inside a downtrend, not an MSS.
`invalidate.new_extreme_atr` default **0.10** (a small tolerance for a one-tick undercut).

`confirmed_at = close_time(b)`. On confirmation the setup transitions to
`CHOCH_CONFIRMED → WAITING_FOR_ENTRY` and the entry model (§15) is armed.

### 11.6 What invalidates the setup?

The complete list is §19. The four that live in this section:

1. `CHOCH_TIMEOUT` — window exceeded.
2. `NEW_EXTREME` — price exceeded the sweep extreme by more than the tolerance.
3. `OPPOSING_SWEEP` — a confirmed sweep in the opposite direction occurred first.
4. `NO_CHOCH_REFERENCE` / `REFERENCE_TOO_FAR` — no usable structural level to break.

### 11.7 Backtest

- MSS count vs CHoCH count vs sweep count, per symbol per month — the funnel. A funnel that
  converts 2% of sweeps into MSS will not produce a testable sample in five years, and that is
  a design finding to surface in Phase 9, before the entry engine is built.
- Median bars from sweep to MSS, by tier and session. If the median is at the window edge, the
  window is doing the work rather than the structure.
- `reference_mode` ablation reported as two full, separately pre-registered strategy variants
  — not as a parameter sweep, since they are different strategies (§11.1).

---

## §12. Fair Value Gap (FVG)

*(Required definition 14.)*

### 12.1 Rule

Over three consecutive bars `(n−2, n−1, n)`:

```
BULLISH FVG  ⟺  L_n > H_(n−2)
                zone = [ H_(n−2) , L_n ]        proximal edge = L_n        distal = H_(n−2)
BEARISH FVG  ⟺  H_n < L_(n−2)
                zone = [ H_n , L_(n−2) ]        proximal edge = H_n        distal = L_(n−2)
```

**The proximal/distal labels above were inverted in v1.0 and are corrected here (D-011
§1).** Proximal means the edge price reaches *first* on returning to the zone. A bullish
gap forms with price above it, so a return meets `L_n` (= `zone_high`) first — which is
what §12.2's touch rule (`bullish: L ≤ zone_high`) and §12.4's worked example (*"buy
limit at 1.08420 (proximal edge)"*, where 1.08420 is `L_n`) both already said. Entry
model C places its limit at the proximal edge, so the inverted label would have shipped
as a systematically wrong fill price.

- `size = |zone_high − zone_low|`; required `size ≥ fvg.min_size_atr × ATR_ref(n)`
  (default **0.10**) **and** `size ≥ fvg.min_size_pips` (default 0.5 pips — a spread guard).
- `formed_at` = bar `n−1` (the middle bar the gap is centred on);
  **`confirmed_at` = close of bar `n`.** The gap is not knowable until the third bar closes.
- `CE` (consequent encroachment) = zone midpoint. Used by entry model C.
- Bar `n−1` is not tested at all. A bullish FVG does not require `n−1` to be bullish; the gap
  is between the outer two bars by definition. Implementations that also test the middle bar
  are computing a different object.

### 12.2 Object and lifecycle

```
FVG { id, symbol, timeframe, direction, zone_high, zone_low, ce, size, size_atr,
      formed_at, confirmed_at, age_bars,
      status: UNMITIGATED | PARTIAL | MITIGATED | INVALIDATED | EXPIRED,
      first_touch_at, mitigated_at }
```

```
touch      the bar's range INTERSECTS the zone:  L ≤ zone_high  ∧  H ≥ zone_low
PARTIAL    price entered the zone but not past CE
MITIGATED  fvg.mitigation_mode reached — {touch, ce, full}, default ce
INVALIDATED bullish: close < zone_low − fvg.invalidate_buffer_atr × ATR_ref   (default 0.0)
EXPIRED    age_bars > fvg.max_age_bars   (default 30 bars of its own TF)
```

**The touch rule was one-sided in v1.0 and is corrected here (D-011 §2).** As written
(`bullish: L ≤ zone_high` alone) it is right whenever price returns from the gap's own
side, and wrong for the case §12.5 describes: a bar opening *below* a bullish zone
satisfies it while never having traded inside. That made `INVALIDATED` **unreachable
entirely** — a bullish close below `zone_low` implies a low below `zone_low`, which is at
or past every mitigation target, so mitigation always won the race and every gap-over was
counted as a fill. Requiring intersection agrees with the one-sided rule everywhere the
one-sided rule is right, and differs only in §12.5's case.

Mitigation is also tested **before** invalidation within a bar, so a bar that trades
through the zone to its mitigation threshold and then closes beyond it is MITIGATED: the
gap was used, wherever the bar happened to close. Invalidation is for the gap-over case,
where it never was.

`fvg.mitigation_mode = ce` (default) means a gap counts as used once price reaches its
midpoint. `touch` is the strictest (any tag consumes it), `full` the loosest. This choice
changes how many FVGs remain available for entry model C and is an ablation dimension.

### 12.3 Which FVG does a setup use?

Entry model C uses the FVG selected by `fvg.selection`:

| Value | Selection |
|---|---|
| `first` (**default**) | The earliest-formed qualifying FVG inside the displacement leg |
| `largest` | Largest by `size_atr` |
| `nearest` | Closest proximal edge to current price |

Qualifying = direction matches the setup, `status = UNMITIGATED` **as of the bar the
setup confirms on**, formed within the displacement leg `[a..b]` (§10.1), `size` above
minimum. The status qualifier is time-varying and must be read as of that bar (D-011 §3):
a gap mitigated later was still available at the moment of selection, and reading a
single end-of-run status field would be lookahead. If none qualifies, entry model C
cannot arm and the setup falls back per `entry.fallback_model` (§15.7) or is invalidated
(`NO_FVG_AVAILABLE`).

### 12.4 Example

```
bar n−2:  H 1.08310  L 1.08150
bar n−1:  H 1.08620  L 1.08290
bar n:    H 1.08760  L 1.08420
```

`L_n = 1.08420 > H_(n−2) = 1.08310` → bullish FVG, zone `[1.08310, 1.08420]`,
size `0.00110`. With `ATR_ref = 0.00450`, `size_atr = 0.244 ≥ 0.10` ✓. `CE = 1.08365`.
Confirmed at the close of bar `n`. Entry model C would place a buy limit at 1.08420 (proximal
edge) or 1.08365 (CE) per `entry.fvg_entry_point`.

### 12.5 Edge cases

| Case | Ruling |
|---|---|
| Weekend gap produces a huge FVG | Created, tagged `spans_gap`. `fvg.exclude_weekend_gaps` (default **true**) suppresses it: an unfillable price region is not an imbalance anyone will trade back into |
| Overlapping FVGs on consecutive bars | Both are kept as distinct objects. `fvg.merge_overlapping` (default false) is an ablation option |
| Zone fully inside a prior unmitigated FVG | Kept separately; nesting is real and the selection rule resolves which is used |
| Price gaps over the entire zone without touching | Zone is **not** mitigated (never touched) but is INVALIDATED if the close is beyond it. Prevents a stale untouched zone from arming an entry weeks later |
| `size` below minimum | Not created at all — not created-then-filtered, so it never appears in counts |

### 12.6 Backtest

- FVG population per TF per month; fill-rate curve (proportion mitigated within k bars).
- **Standalone edge test:** return from touching an unmitigated FVG in the direction of the
  gap, versus a matched control. This tests the FVG concept independently of the strategy, so
  a null result there localises the failure precisely.
- Ablation: `disp.require_fvg` on/off; `fvg.mitigation_mode`; `entry.fvg_entry_point ∈
  {proximal, ce, distal}`.

---

## §13. Order Block (OB)

*(Required definition 15.)*

### 13.1 The definitional problem, stated

"The last opposing candle before a move that breaks structure" is the standard formulation and
it is under-specified in three places: *which* opposing candle when several are adjacent,
whether the zone is the body or the full range, and whether "before the move" means before the
first displacement bar or before the leg origin. Different choices produce zones tens of pips
apart, which for a stop-based strategy is the difference between a win and a loss. v1.0
therefore specifies **four candidate definitions** and treats the choice as a first-class
ablation, not an implementation detail.

### 13.2 The four candidates

For a **bullish** setup with displacement leg `[a..b]` (mirror for bearish):

| Id | Definition | Origin bar |
|---|---|---|
| **OB-A** `last_opposing` (**default**) | The last bar at index `< a_disp` with `C < O`, where `a_disp` is the first bar of the displacement leg | that bar |
| **OB-B** `last_down_close_before_break` | The last bar with `C < O` before bar `b` (the CHoCH break bar) | that bar |
| **OB-C** `extreme_origin` | The bar with the lowest `L` in `[s, a_disp]` — i.e. the sweep-extreme bar itself in most cases | that bar |
| **OB-D** `breaker` | The last opposing bar of the **failed** move: the up-bar before a swing high that was subsequently broken downward, now used as resistance-turned-support | that bar |

Search is bounded: `ob.max_lookback_bars` (default **10**) bars before `a_disp`. If no
qualifying bar exists, no OB is produced and entry model D cannot arm.

**OB-D is under-specified relative to the other three (D-012 §1).** A, B and C are fully
determined by the table above and key off the displacement leg of the setup in hand. D
points at a *different* structural event in one line and does not say which swing, how far
back to search, or what "broken downward" means for a level that is broken upward by
definition. The reading implemented in `bot/core/order_blocks.py::_ob_d` is documented
there; at least two others are defensible. Its hit rate on the Phase 11 fixture — 72
blocks against OB-A's 178 — should be read as a property of that reading rather than of
the breaker concept.

### 13.3 Zone

`ob.zone_mode`:

| Value | Bullish zone |
|---|---|
| `full_range` (**default**) | `[L_ob, H_ob]` |
| `body` | `[min(O,C), max(O,C)]` |
| `wick_to_open` | `[L_ob, O_ob]` |

`proximal edge` is the edge nearest current price (the high, for a bullish OB approached from
above); `distal` is the far edge; `ce` the midpoint.

### 13.4 Validity constraints (all MUST hold)

```
1. The leg starting at the OB must satisfy DISPLACEMENT (§10)      — an OB without displacement is just a candle
2. The leg must produce the CHoCH/MSS break                        — ties the OB to the structural event
3. status = UNMITIGATED at the time of proposal                    — same lifecycle as §12.2
4. |proximal_edge − reference.price| ≤ ob.max_distance_atr × ATR_ref     (default 3.0)
5. The OB must not be older than ob.max_age_bars                   (default 30)
```

Constraint 1 is what stops OB-A from degenerating into "the last red candle", which on any
chart is never more than a few bars away and therefore always exists.

### 13.5 Lifecycle

Identical in shape to FVG (§12.2): `UNMITIGATED → PARTIAL → MITIGATED`, and `INVALIDATED` on
`ob.invalidate_closes` (default **1**) closes beyond the distal edge. A bullish OB whose low
is closed through is invalid: the orders it represented have been run.

### 13.6 Example

```
s   (sweep low bar)     O 1.08300  H 1.08340  L 1.08150  C 1.08210    down bar
a   (first disp bar)    O 1.08215  H 1.08480  L 1.08200  C 1.08460    up bar
b   (break bar)         O 1.08460  H 1.08790  L 1.08440  C 1.08760    up bar, breaks reference 1.08600
```

- **OB-A**: last down bar before `a` = bar `s`. Zone `full_range` = `[1.08150, 1.08340]`,
  proximal 1.08340, CE 1.08245.
- **OB-C**: lowest low in `[s, a]` = bar `s`. Same bar here — they frequently coincide, which
  is itself worth measuring: if OB-A and OB-C select the same bar 80% of the time, they are
  not two hypotheses.
- Entry model D arms a buy limit at 1.08340 (proximal) or 1.08245 (CE) per
  `entry.ob_entry_point`, with the stop below 1.08150 minus buffer (§16).

### 13.7 Edge cases

| Case | Ruling |
|---|---|
| No opposing candle within `max_lookback_bars` | No OB. Entry model D falls back (§15.7). Logged as `NO_OB_AVAILABLE` — the frequency of this is a quality signal for the definition |
| The opposing candle is a doji (`C == O`) | Not opposing. Requires strict `C < O` (bullish case) |
| OB zone overlaps the FVG zone | Normal and expected; models C and D will often propose similar entries. Trade records store both so the correlation is measurable rather than assumed |
| OB proximal edge is above the CHoCH reference | Rejected (`OB_ABOVE_REFERENCE`): entering above the level whose break defined the setup means the "retracement" entry is not a retracement |
| Price never returns to the zone | Entry expires unfilled (§15.6). Fill rate per model is a headline statistic — a model with a 20% fill rate has a fifth of the sample size and cannot be compared naively against model A |

### 13.8 Backtest

- **Definition bake-off:** OB-A/B/C/D as four separate pre-registered variants, identical in
  every other respect. Report the agreement matrix (how often they pick the same bar) as well
  as performance, because near-identical variants must not be counted as independent tests
  when applying the multiple-testing correction.

  **Same-bar agreement is necessary but not sufficient, and on the Phase 11 fixture it is
  badly misleading on its own (D-012 §2).** Measured there, OB-A and OB-C picked the same
  bar only 23% of the time and OB-D agreed with the others *never* — yet every pair's
  proposed entry price correlated above 0.87. The definitions pick **different bars that
  sit at almost the same price**, so same-bar agreement understates redundancy. What the
  correction needs is an effective test count computed from the correlation of proposed
  entries: **1.77 against a nominal 4**. Report both; correct with the second.
- Standalone edge test, as for FVG: forward return from first touch of an unmitigated OB.
- Fill-rate and time-to-fill distributions per definition and per `zone_mode`.

---

## §14. Setup assembly — the bullish and bearish models

*(Brief §12 and §13, made executable.)*

### 14.1 Setup object

```
Setup {
  id, symbol, direction: BULLISH | BEARISH
  state (see STATE_MACHINE.md)
  created_at, updated_at, terminal_at
  liquidity_cluster: { level_ids[], anchor_level_id, tier, strength, sources[] }
  sweep: { trigger_bar, confirm_bar, extreme_price, penetration_atr, confirmation_tf }
  reference: { swing_id, price, mode }
  displacement: { leg_start_bar, leg_end_bar, net_atr, body_ratio, fvg_id? }
  mss: { confirmed_at, break_price }
  bias_snapshot: { monthly, weekly, daily, h4, score, gate_mode, label }
  session_snapshot: { sweep_session, mss_session, current_session, dst_desync }
  entry_plan: { model, order_type, price, sl, tp[], size_lots, risk_pct, rr, expires_at }
  outcome: { fill_price, exit_price, exit_reason, r_multiple, pnl_ccy, duration_bars }
  invalidation: { reason, at }
  rejections[]: [{ gate, reason, at }]        # every gate that said no, even if another said no first
}
```

Recording **all** failing gates rather than short-circuiting on the first is deliberate: it
makes the rejection log a usable counterfactual dataset (§21.3) rather than a list biased by
gate evaluation order.

### 14.2 The bullish sequence, as executed

```
1  MTF context evaluated and cached                                     §7
2  SELL_SIDE liquidity levels maintained, ranked, in-play filtered      §8
3  Sweep of a sell-side level/cluster CONFIRMED                         §9
   → Setup created, state = LIQUIDITY_SWEPT, direction = BULLISH
   → NO ORDER MAY EXIST AT THIS POINT (state machine invariant)
4  CHoCH reference high selected                                        §11.1
   → state = WAITING_FOR_CHOCH        (fails → SETUP_INVALIDATED)
5  Each confirmation-TF close: test displacement + break                §10, §11.5
   → displacement only        → DISPLACEMENT_CONFIRMED
   → break without displacement → CHoCH logged, NOT an MSS; setup stays waiting
   → both                      → MSS CONFIRMED
6  MSS CONFIRMED → state = CHOCH_CONFIRMED → WAITING_FOR_ENTRY
7  Entry model arms an order                                            §15
8  Risk gates evaluated at arm time and again at fill time              §18
9  Fill → TRADE_OPEN. Management per §17.
10 Exit → TRADE_CLOSED. Full record written. Levels → CONSUMED.
```

The bearish sequence is the exact mirror: BUY_SIDE liquidity, sweep above, bearish
displacement, break of the CHoCH reference **low**, sell entry.

### 14.3 Setup direction is fixed at creation

A setup created bullish never becomes bearish. An opposing sweep invalidates it (§19) and a
new setup is created. This keeps one setup object to one hypothesis, which is what makes the
state machine analysable and the statistics countable.

### 14.4 Concurrency

| Limit | Parameter | Default |
|---|---|---|
| Live setups per symbol (any non-terminal state) | `setup.max_active_per_symbol` | 2 |
| Live setups per symbol per direction | `setup.max_active_per_direction` | 1 |
| Armed (unfilled) orders per symbol | `setup.max_armed_orders` | 1 |
| Open positions per symbol | `risk.max_positions_per_symbol` | 1 |

When a new setup would exceed a cap, the **lower-ranked** setup (by §8.8 `rank_score`) is
invalidated with reason `SUPERSEDED`, and this is logged with both ids so the discarded
alternative's outcome can still be evaluated in the counterfactual study.

---

## §15. Entry models

*(Required definition 16.)*

### 15.1 Common contract

Every model produces:

```
EntryPlan { model, order_type: MARKET | LIMIT, price, valid_from, expires_at,
            sl_model, tp_model, cancel_if }
```

- `valid_from` = `close_time(MSS bar)`. No order may exist before the MSS is confirmed.
- `expires_at` = `valid_from + entry.pending_expiry_bars × D(confirmation_tf)`
  (default **6**).
- `cancel_if` — hard cancels, evaluated on every confirmation-TF close **and** on price
  touching the level intrabar in live trading:
  1. Price reaches the planned SL before the entry fills (`SL_BEFORE_ENTRY`). Without this,
     a limit order can fill on the way back up from a level that already invalidated the idea.
  2. An opposing sweep confirms (`OPPOSING_SWEEP`).
  3. The MTF gate flips against the setup — `entry.cancel_on_bias_flip` (default **true**).
  4. Expiry reached (`ENTRY_EXPIRED`).

### 15.2 The five models

| Model | Order | Price |
|---|---|---|
| **A — market on MSS** | MARKET at `close_time(b)` | Backtest fill: **next bar open** + slippage (§26). Never the close of `b` — that price is not obtainable |
| **B — retracement of the displacement leg** | LIMIT | `leg_low + entry.retrace_pct × (break_high − leg_low)`, default `retrace_pct` **0.50** |
| **C — FVG** | LIMIT | Selected FVG (§12.3) at `entry.fvg_entry_point ∈ {proximal, ce, distal}`, default **ce** |
| **D — Order Block** | LIMIT | Selected OB (§13) at `entry.ob_entry_point ∈ {proximal, ce, distal}`, default **proximal** |
| **E — 50% of displacement leg** | LIMIT | `(leg_low + leg_high) / 2` where the leg is `[a..b]` of §10.1 |

Models B and E differ: B measures from the leg low to the *break* price, E from leg low to
leg high. They coincide when the break bar makes the leg high, which is common but not
universal — the distinction is kept because collapsing it would hide which measurement
matters.

### 15.3 Model A and the fill-price trap

Model A is the only market model and the only one with a 100% fill rate, which makes it the
natural baseline. It is also the model most easily flattered by a careless backtest: filling
at `C_b` (the close that triggered the signal) is a lookahead of one full bar and typically
adds 10–30% to headline returns on H4. **MUST:** market fills occur at the open of bar `b+1`
plus modelled slippage, or, when M1 data is available, at the first M1 price after
`close_time(b) + exec.latency_ms`.

### 15.4 Fill rules (backtest)

| Order | Fill condition | Fill price |
|---|---|---|
| MARKET | Always | Next-bar open (or M1 path price at `t + latency`) + slippage |
| BUY LIMIT at `p` | `L_bar ≤ p − backtest.limit_fill_buffer_pips` (default **0.2**) | `p` (+ spread → ask) |
| SELL LIMIT at `p` | `H_bar ≥ p + buffer` | `p` |
| STOP orders | Not used in v1.0 | — |

The buffer exists because a limit order that price merely *touches* is not reliably filled —
the queue may never reach you. Assuming touch-fills is one of the largest silent optimisms in
retail backtesting, and it disproportionately flatters exactly the retracement models B–E.

### 15.5 Fill rate is not a nuisance statistic

Models B–E do not always fill. Comparing their win rates against model A's is invalid without
also comparing **coverage**: a model that fills 35% of the time on the best-looking third of
setups will show a superior win rate and a worse total return. The report MUST present, per
model: fill rate, expectancy per *setup* (not per trade), and expectancy per *trade*
(`BACKTEST_PROTOCOL.md` §4.3). The per-setup figure is the one that compares like with like.

### 15.6 Expiry and the unfilled setup

At `expires_at` with no fill: `SETUP_INVALIDATED(ENTRY_EXPIRED)`. The would-have-been trade's
forward outcome (using the planned SL/TP over the next `exit.max_bars_in_trade` bars) is
computed and stored as a **shadow trade**. Shadow trades never touch equity; they exist so
"did we miss the good ones?" is answerable per model.

### 15.7 Fallback

`entry.fallback_model` (default **none**) — when the primary model cannot arm (no FVG, no OB),
either invalidate (`none`) or fall back to a named model. Default is `none` because a fallback
chain silently mixes populations and makes per-model statistics uninterpretable.

### 15.8 Backtest

All five models run as **separate pre-registered variants** over identical setups, from one
shared setup stream so the comparison is paired. Paired comparison massively increases the
power of the model bake-off relative to independent runs, and it makes "model C beats model A"
a statement about the same 400 setups rather than two different populations.

---

## §16. Stop Loss

*(Required definition 17.)*

### 16.1 Models

For a **BUY** (mirror for SELL):

| Id | `sl.model` | Level |
|---|---|---|
| **S1** | `sweep_extreme` (**default**) | `sweep_extreme − buffer` |
| **S2** | `structural_swing` | `min(L over [s..b]) − buffer` — the lowest low of the whole setup window |
| **S3** | `order_block` | `OB.distal_edge − buffer` (requires an OB) |
| **S4** | `atr` | `entry_price − sl.atr_multiple × ATR_ref` (default 1.5) |
| **S5** | `entry_minus_fixed_r` | Derived from a target R and the TP level; **rejected for v1.0** — sizing the stop from the target inverts the logic and guarantees the stop sits at a structurally arbitrary price |

S1 is the default because the sweep extreme is the price at which the setup's premise is
falsified: below it, the "sweep" was a breakout.

### 16.2 Buffer

```
buffer = max( sl.buffer_atr × ATR_ref ,
              sl.buffer_spread_mult × spread_at_entry ,
              symbol.stops_level + 1 point )
```

Defaults: `sl.buffer_atr` **0.10**, `sl.buffer_spread_mult` **2.0**. The spread term is not
optional (§1.3): on a JPY cross at a news time, two spreads can exceed 0.10 ATR, and a stop
inside that band is hit by quote noise rather than by price.

### 16.3 Constraints — a setup is REJECTED, never adjusted

```
sl_distance = |entry − sl|

REJECT if  sl_distance > risk.max_sl_atr × ATR_ref        (default 2.5)
REJECT if  sl_distance > risk.max_sl_pips[symbol]         (default 60 majors / 90 JPY crosses)
REJECT if  sl_distance < risk.min_sl_pips[symbol]         (default 8 / 12)
REJECT if  sl_distance < symbol.stops_level               (broker minimum)
REJECT if  computed_lots < symbol.min_lot                 (§18.2)
```

**MUST NOT** move the stop closer to satisfy a constraint. Tightening a stop to fit a risk cap
converts a rejected setup into a low-quality trade with a structurally wrong stop — and it
does so precisely on the widest, most volatile setups, which is a systematic bias, not random
noise. Rejections are logged with the measured distance so the caps can be re-examined against
data.

### 16.4 Example

```
sweep_extreme = 1.08150      ATR_ref = 0.00450      spread = 0.00012
buffer = max(0.10 × 0.00450, 2.0 × 0.00012, stops_level) = max(0.00045, 0.00024, …) = 0.00045
SL = 1.08150 − 0.00045 = 1.08105
Entry (model C, FVG CE) = 1.08365  →  sl_distance = 0.00260 = 26.0 pips = 0.58 ATR
Checks: 0.58 ≤ 2.5 ✓   26 ≤ 60 ✓   26 ≥ 8 ✓   → accepted
```

### 16.5 Edge cases

| Case | Ruling |
|---|---|
| Entry price is below the SL (limit below the sweep low) | Impossible by construction; asserted. If it occurs, it is a bug and the setup is killed with `INVALID_GEOMETRY`, never silently corrected |
| Stop inside the spread at fill time | Rejected at fill (`SPREAD_EXCEEDS_STOP`) even if it passed at arm time. Both checks are required |
| Gap through the stop | Filled at the gap open, worse than the stop. Modelled explicitly (§26); the R-multiple recorded is the realised one, which may be −2.4R |
| Broker `stops_level` changes intraday (news widening) | Live layer re-reads it before every order; a violated constraint cancels the order rather than adjusting it |

### 16.6 Backtest

Ablation across S1–S4 as paired variants on a shared setup stream. Report, per model:
R-distribution, the proportion of stopped trades whose extreme came within 2 pips of the stop
(a proxy for stop-hunting), and the counterfactual outcome had the stop been 0.5 ATR wider —
which distinguishes "stop too tight" from "idea wrong".

---

## §17. Take Profit and trade management

*(Required definition 18.)*

### 17.1 Models

| Id | `tp.model` | Definition |
|---|---|---|
| **T1** | `fixed_r` (**default**) | `entry ± tp.r_multiple × sl_distance`, `r_multiple ∈ {1.5, 2.0, 2.5, 3.0}` tested |
| **T2** | `opposing_liquidity` | The nearest ACTIVE opposing-side liquidity level with `rank_score ≥ tp.min_target_rank`, minus `tp.target_buffer_atr × ATR_ref` (default 0.15) so the order sits in front of the level rather than at it |
| **T3** | `partial_ladder` | 50% at 1R, 25% at 2R, 25% at T2's opposing liquidity; SL to breakeven after the first partial |
| **T4** | `structure_trail` | No fixed target; exit on the first opposing CHoCH on the confirmation TF |

### 17.2 The minimum-RR gate

```
rr = |tp_1 − entry| / sl_distance
REJECT if rr < tp.min_rr        (default 1.5)
```

`tp.below_min_rr_action ∈ {skip, fixed_fallback}` = **skip** (default). Falling back to a fixed
R target when the structural target is too close means taking the trade without the reason for
taking it, and it contaminates the T2 population with T1 trades.

### 17.3 Break-even and trailing

| Parameter | Default | Notes |
|---|---|---|
| `manage.be_trigger_r` | 0.0 (off) | Move SL to `entry ± manage.be_offset_atr × ATR_ref` when the trade reaches this R |
| `manage.be_offset_atr` | 0.05 | Offset covers spread + commission so breakeven is actually breakeven |
| `manage.trail_mode` | `none` | `structure` (behind each new confirmed HL/LH) or `atr` (`trail_atr_mult`, default 2.0) |
| `manage.trail_start_r` | 1.0 | Trailing does not begin before this |

Break-even is **off by default**. It reliably raises win rate and reliably lowers expectancy on
most systems; enabling it by default would flatter the headline statistic that matters least.
It is an ablation dimension.

### 17.4 Time and calendar exits

| Parameter | Default | Notes |
|---|---|---|
| `exit.max_bars_in_trade` | 30 (H4 ≈ 5 days) | `TIME_STOP` exit at market |
| `exit.close_before_weekend` | **true** | Close at `exit.weekend_close_utc` (default Friday 19:00 UTC). Avoids weekend gap risk and the triple swap |
| `exit.close_before_high_impact_news` | false | Requires a calendar feed (`OPEN_QUESTIONS.md` Q13). Off in v1.0 — a feature that cannot be reproduced in the backtest must not exist only in live |

The last row is a rule about the whole system: **any live behaviour that the backtest cannot
reproduce is prohibited.** Otherwise the live system and the tested system are different
systems and the backtest no longer describes what is running.

### 17.5 Intrabar ambiguity — the single largest backtest bias

When a bar's range contains both the stop and the target, the outcome depends on the path,
which OHLC does not record. `backtest.intrabar_mode`:

| Value | Behaviour |
|---|---|
| `m1_path` (**default when M1 data exists**) | Replay M1 bars within the H4 bar and resolve in true order. The only correct option |
| `pessimistic` | Stop is assumed hit first. Always |
| `ohlc_heuristic` | **Prohibited.** Inferring the path from open/close position is a guess that systematically favours whichever assumption was coded |

If M1 data is unavailable, `pessimistic` is mandatory and its cost MUST be quantified by
re-running any period where M1 *is* available under both modes and reporting the delta. That
delta is the error bar on every result produced without M1.

### 17.6 Example (T3 ladder)

```
Entry 1.08365   SL 1.08105   sl_distance 0.00260 (26 pips)   size 0.40 lots
1R = 1.08625 → close 0.20 lots, move SL to 1.08385 (entry + 0.05 ATR)
2R = 1.08885 → close 0.10 lots
Runner 0.10 lots → nearest opposing liquidity PDH 1.09240, target 1.09173 (−0.15 ATR buffer)
Realised R = 0.5(1.0) + 0.25(2.0) + 0.25(3.1) = 1.775R  before costs
```

Partial fills MUST respect `lot_step`: 0.40 lots at 50% is 0.20 ✓, but 0.03 lots at 50% is
0.015 → invalid. Rule: partials round **down** to `lot_step`, and if a partial would round to
zero the ladder degrades to the next model (`T1` at the final target) and the trade is tagged
`LADDER_DEGRADED` so those trades are not counted as T3 evidence.

### 17.7 Backtest

Paired T1–T4 variants on a shared setup stream. Additionally, the **MAE/MFE study**: for every
trade, maximum adverse and favourable excursion in R. The MFE distribution is what determines
whether any fixed-R target is well-placed, and it is computed once and reused for all target
models rather than re-optimising the target on the same trades that will report the result.

---

## §18. Risk management

*(Required definition 19.)*

### 18.1 Prohibited, by construction

The following are not "discouraged". The position sizing function has no access to the
information required to implement them, which is the only reliable way to prevent them:

- Martingale or any size increase after a loss.
- Loss-recovery sizing (raising risk % to recoup drawdown).
- Averaging down / adding to a losing position.
- Grid or hedging systems.
- Leverage beyond what `risk_pct` and the stop distance imply.

**Invariant (unit-tested):**

```
size(equity, risk_pct, sl_distance)  is a pure function.
It receives no trade history, no PnL, no streak counter.
risk_pct may only be REDUCED by the risk layer (drawdown ladder, §18.5), never increased.
```

### 18.2 Position sizing

```
risk_amount        = equity × risk_pct
value_per_price_unit_per_lot = contract_size × fx_rate(quote_ccy → account_ccy, at entry time)
raw_lots           = risk_amount / (sl_distance_price × value_per_price_unit_per_lot)
lots               = floor(raw_lots / lot_step) × lot_step
```

Then:

```
REJECT if lots < symbol.min_lot                        reason SIZE_BELOW_MIN
REJECT if lots > symbol.max_lot                        reason SIZE_ABOVE_MAX
REJECT if actual_risk < risk.min_realised_fraction × risk_amount    (default 0.5)
```

The last check catches lot-granularity distortion on small accounts: with `min_lot = 0.01` and
a 26-pip stop, a €2,000 account at 0.25% risk wants 0.019 lots, floors to 0.01, and takes
**half** the intended risk. Silently under-risking half the trades makes every per-trade
statistic wrong, so the trade is rejected and logged instead.

**The FX conversion is not optional.** For USDJPY on a USD account the quote currency is JPY;
`value_per_price_unit_per_lot = 100,000 / USDJPY_rate`. A backtest that treats every symbol as
if it had a fixed $10/pip is wrong by up to 40% on JPY pairs over a five-year window as the
rate moves. The conversion rate series is part of the dataset, and its absence blocks the
inclusion of any symbol whose quote currency is not the account currency.

### 18.3 Risk per trade

| Parameter | Default | Notes |
|---|---|---|
| `risk.pct_per_trade` | **0.35%** | Within the brief's 0.25–0.5% band. TUNABLE only within [0.10, 0.50] |
| `risk.counter_monthly_multiplier` | 0.5 | Applied when `bias.counter_monthly_action = derisk` |
| `risk.max_total_open_risk_pct` | 1.5% | Sum of open risk across all positions. A new trade is rejected if it would breach this |

### 18.4 Hard limits (all measured on **closed** PnL unless stated)

| Limit | Parameter | Default | On breach |
|---|---|---|---|
| Daily loss | `risk.max_daily_loss_pct` | 2.0% | Halt new entries until the next day boundary. Open positions are managed normally |
| Weekly loss | `risk.max_weekly_loss_pct` | 4.0% | Halt until the next week boundary |
| Monthly loss | `risk.max_monthly_loss_pct` | 8.0% | Halt until the next month boundary; requires manual re-enable |
| Consecutive losses | `risk.max_consecutive_losses` | 5 | Halt for `risk.consecutive_loss_pause_hours` (default 24) |
| Concurrent positions | `risk.max_open_positions` | 3 | Reject |
| Per symbol | `risk.max_positions_per_symbol` | 1 | Reject |
| Correlated cluster | `risk.max_correlated_positions` | 2 | §18.7 |
| Spread at entry | `risk.max_spread_pips[symbol]` | 2.0 / 3.5 JPY | Reject |
| Spread vs stop | `risk.max_spread_pct_of_sl` | 10% | Reject. Catches wide-spread-plus-tight-stop combinations that the absolute cap misses |
| Equity drawdown (**includes floating**) | `risk.equity_dd_kill_pct` | 10% from peak | Kill switch (§18.6) |

Loss-limit accounting uses the **risk day**, aligned to `tf.day_boundary` (§2.2) so a "daily"
limit and a "daily" bias refer to the same day.

### 18.5 The drawdown ladder (risk may only go down)

```
current_dd = (peak_equity − equity) / peak_equity

dd < 5%          → risk_pct × 1.00
5% ≤ dd < 8%     → risk_pct × 0.75
8% ≤ dd < 10%    → risk_pct × 0.50
dd ≥ 10%         → kill switch
```

Recovery restores the multiplier as drawdown falls, but **never above 1.00**. This is the
anti-martingale invariant expressed at portfolio level: the ladder is monotone non-increasing
in drawdown, and a test asserts no configuration can produce a multiplier > 1.

### 18.6 Kill switch

Triggers: `risk.equity_dd_kill_pct` breached; `risk.max_consecutive_losses` exceeded twice in
one week; data staleness > `ops.max_data_staleness_sec` (default 300) during market hours;
broker rejections > `ops.max_broker_errors` (default 5) in an hour; the presence of the file
`KILL_SWITCH` in the runtime directory.

Actions: cancel all pending orders; **do not** close open positions automatically (forced
liquidation at an arbitrary moment is itself a risk decision, and the stops are already
placed); halt all new entries; alert; require explicit manual re-enable.

The manual file trigger exists because a kill switch that can only fire automatically cannot
be used by the person watching the screen.

### 18.7 Correlation cap

`risk.correlation_window_days` (default 60) rolling daily-return correlation across the traded
universe, recomputed weekly. Symbols with `|ρ| ≥ risk.correlation_threshold` (default 0.70)
form a cluster; **directionally equivalent** exposure counts toward the cluster cap (long
EURUSD and short USDCHF are the same position). Cluster membership is recorded on every trade
so the realised correlation of the trade book can be checked after the fact.

### 18.8 Live-only safety layer

These run in live/paper only and have **no effect on signals** (§17.4's rule): reconciliation
of broker positions against internal state every 60 s with a halt on mismatch; verification
that every open position has a broker-side stop; heartbeat to the MQL5 watchdog
(`ARCHITECTURE.md` §5.3); startup reconciliation before any new order.

### 18.9 Backtest

- Every limit is exercised by a synthetic scenario (forced losing streak, forced drawdown).
- The realised distribution of risk-per-trade is reported: it MUST be a spike at
  `risk.pct_per_trade` with a lower tail only from lot rounding. Any mass above the nominal
  value is a sizing bug.
- Reported both with limits **on** and **off**. Limits change the equity path but must not be
  what creates the edge; a strategy that is only profitable with a daily loss limit engaged is
  a strategy with a fragility the limit is hiding.

---

## §19. Setup invalidation — the complete catalogue

*(Required definition 20.)* Every reason is an enum value, every occurrence is logged with the
setup id, the state it was in, and the bar that caused it. Nothing exits a setup silently.

| # | Reason | Fires in state | Condition |
|---|---|---|---|
| 1 | `SWEEP_FAILED_NO_RECLAIM` | (pre-setup) | Reclaim not achieved within `sweep.max_confirmation_bars` |
| 2 | `SWEEP_OVER_PENETRATION` | (pre-setup) | Penetration exceeded `sweep.max_penetration_atr` |
| 3 | `NO_CHOCH_REFERENCE` | LIQUIDITY_SWEPT | No unbroken opposing swing within `choch.max_reference_lookback` |
| 4 | `REFERENCE_TOO_FAR` | LIQUIDITY_SWEPT | Reference beyond `choch.max_reference_distance_atr` |
| 5 | `CHOCH_TIMEOUT` | WAITING_FOR_* | `choch.max_bars_after_sweep` elapsed with no MSS |
| 6 | `NEW_EXTREME` | WAITING_FOR_* | Price exceeded the sweep extreme by `invalidate.new_extreme_atr` |
| 7 | `OPPOSING_SWEEP` | any pre-trade | A confirmed sweep in the opposite direction |
| 8 | `STRUCTURE_WHIPSAW` | any pre-trade | Two trend flips within `structure.min_bars_between_flips` |
| 9 | `BIAS_GATE_FAIL` | WAITING_FOR_ENTRY | MTF gate fails at MSS confirmation |
| 10 | `COUNTER_MONTHLY` | WAITING_FOR_ENTRY | Opposes a non-neutral Monthly bias with `action = block` |
| 11 | `BIAS_FLIP` | WAITING_FOR_ENTRY | Gate flips after arming, `entry.cancel_on_bias_flip` |
| 12 | `ENTRY_EXPIRED` | WAITING_FOR_ENTRY | Pending order reached `expires_at` unfilled |
| 13 | `SL_BEFORE_ENTRY` | WAITING_FOR_ENTRY | Price reached the planned SL before filling |
| 14 | `NO_FVG_AVAILABLE` / `NO_OB_AVAILABLE` | CHOCH_CONFIRMED | Entry model C/D cannot arm |
| 15 | `OB_ABOVE_REFERENCE` | CHOCH_CONFIRMED | OB geometry invalid (§13.7) |
| 16 | `RR_BELOW_MIN` | CHOCH_CONFIRMED | `rr < tp.min_rr` |
| 17 | `SL_TOO_WIDE` / `SL_TOO_TIGHT` | CHOCH_CONFIRMED | §16.3 caps |
| 18 | `SIZE_BELOW_MIN` / `SIZE_ABOVE_MAX` / `SIZE_UNDER_RISK` | CHOCH_CONFIRMED | §18.2 |
| 19 | `SPREAD_TOO_WIDE` | WAITING_FOR_ENTRY / fill | §18.4, checked at arm **and** at fill |
| 20 | `RISK_LIMIT_*` (daily/weekly/monthly/consecutive/exposure/correlation) | any pre-trade | §18.4 |
| 21 | `SESSION_NOT_ALLOWED` | CHOCH_CONFIRMED | Entry time outside `filter.allowed_execution_sessions` |
| 22 | `SESSION_INCOMPLETE` | any | Setup references an INCOMPLETE session's level (§3.4) |
| 23 | `SUPERSEDED` | any pre-trade | Concurrency cap, lower-ranked setup dropped (§14.4) |
| 24 | `DATA_SUSPECT` | any | Formation window intersects a data gap (§1.5) |
| 25 | `KILL_SWITCH` | any | §18.6 |
| 26 | `MANUAL` | any | Operator intervention |

**Rule:** invalidation is terminal for that setup. The liquidity level moves to `CONSUMED`, not
back to `ACTIVE` — a level does not get a second sweep from the same event. A genuinely new
level at the same price (a new session low, a new swing) is a new object with a new id.

**Every invalidation record stores the forward return** over the following
`analysis.forward_bars` (default 12) bars in the setup's direction. This turns the rejection
log into the counterfactual dataset that answers "were our filters right?" without a single
extra backtest run — see §21.3.

---

## §20. Signal state machine (summary)

The complete table — states, events, guards, actions, timeouts, illegal transitions — is in
`STATE_MACHINE.md`. The invariants, restated here because they are strategy-level rules:

```
WAITING_FOR_LIQUIDITY → LIQUIDITY_IDENTIFIED → LIQUIDITY_SWEPT
   → WAITING_FOR_DISPLACEMENT ⇄ DISPLACEMENT_CONFIRMED
   → WAITING_FOR_CHOCH → CHOCH_CONFIRMED → WAITING_FOR_ENTRY
   → ENTRY_CONFIRMED → TRADE_OPEN → TRADE_CLOSED
any pre-trade state → SETUP_INVALIDATED → (setup is terminal; engine returns to
                                            WAITING_FOR_LIQUIDITY for new setups)
```

1. **`LIQUIDITY_SWEPT` has no edge to `ENTRY_CONFIRMED`.** The illegal transition is absent
   from the transition table, so "enter immediately after a sweep" is unrepresentable rather
   than merely disallowed by a check somebody could remove.
2. Every state except the terminal ones has a timeout with a defined invalidation reason.
3. State transitions occur **only** on a closed bar of the relevant timeframe, or on a fill /
   exit event from the broker. Never on a tick, never on a timer.
4. Every transition is written to the event log before its side effects execute, so a crash
   mid-transition is recoverable and the log is a complete audit trail.

---

## §21. Logging and telemetry

### 21.1 Three logs, three purposes

| Log | Format | Contents | Retention |
|---|---|---|---|
| `events.jsonl` | append-only JSONL | Every object creation, amendment, state transition, order action. The source of truth for replay and for chart rendering | Forever |
| `trades.parquet` | columnar | One row per closed trade, ~70 columns (§21.2) | Forever |
| `rejections.parquet` | columnar | One row per invalidated setup with all failing gates and the forward return | Forever |

`events.jsonl` is the primary artefact. `trades` and `rejections` are **derived from it** and
MUST be reproducible from it — a derivation that cannot be reproduced means the log is
incomplete.

### 21.2 Trade record — the required breakdown dimensions

Every field the brief's §26 asks to break down by is a column, so every breakdown is a
`group_by` rather than a re-run:

```
identity      trade_id, setup_id, symbol, config_hash, run_id
time          sweep_at, mss_at, entry_at, exit_at, duration_bars, duration_hours
context       monthly_bias, weekly_bias, daily_bias, h4_bias, bias_score, alignment_label,
              gate_mode, dst_desync, day_of_week, week_of_year, month
session       sweep_session, mss_session, entry_session, in_overlap, in_killzone
liquidity     liq_source, liq_side, liq_tier, liq_strength, liq_age_bars, cluster_size
sweep         penetration_atr, wick_ratio, close_position, confirmation_bars, single_bar_sweep
structure     choch_reference_mode, reference_price, bars_sweep_to_mss, displacement_net_atr,
              displacement_body_ratio, fvg_id, ob_id
execution     entry_model, order_type, planned_price, fill_price, slippage_pips, fill_latency_ms,
              spread_at_entry, sl_model, sl_price, sl_distance_pips, sl_distance_atr,
              tp_model, tp_prices[], planned_rr
sizing        equity_at_entry, risk_pct, risk_amount, lots, risk_realised_pct, dd_multiplier
outcome       exit_reason, exit_price, r_multiple, pnl_gross, commission, swap, pnl_net,
              mae_r, mfe_r, bars_to_mae, bars_to_mfe
regime        atr_h4_at_entry, atr_percentile_1y, realised_vol_20d, volatility_regime
quality       data_suspect, spans_gap, ladder_degraded, shadow_trade
```

`volatility_regime` = tercile of the trailing 1-year ATR percentile (LOW/MID/HIGH), computed
causally from data available at entry — required by the brief's volatility-regime breakdown.

### 21.3 Rejection record — the counterfactual dataset

Same context columns, plus `rejection_reasons[]` (all of them, §14.1), the planned
entry/SL/TP, and `forward_return_r` computed by simulating the planned trade over the next
`analysis.forward_bars` bars.

This makes the most valuable analysis in the project a query rather than an experiment: *for
each gate, what is the expectancy of the trades it rejected?* A gate whose rejected population
has positive expectancy is destroying edge; one whose rejected population has strongly
negative expectancy is earning its place. Crucially this is computed from **one** backtest run,
so it costs nothing against the out-of-sample evaluation budget (`BACKTEST_PROTOCOL.md` §7).

### 21.4 Human-readable setup narrative

For every setup, terminal or filled, a rendered text block in the format of the brief's §25
example, generated from the event log. It is the artefact used for manual review and the
attachment to every chart (§22).

---

## §22. Chart visualisation

### 22.1 Rendering principle

**Charts are rendered from `events.jsonl`, never by re-running the engine.** A chart produced
by recomputation shows what the code believes *now*; a chart produced from the log shows what
the bot knew *then*. Only the second can be used to check for lookahead, which is the main
reason the charts exist.

Every drawn object carries its `confirmed_at`, and the renderer MUST NOT draw an object to the
left of it without a visual marker distinguishing "formed here" from "known here" — a swing
high drawn at its formation bar with no indication that it was unknown for two more bars is
the visual equivalent of repainting.

### 22.2 Layers (all toggleable)

| Layer | Contents |
|---|---|
| HTF context | Monthly/Weekly high, low, open; major structure labels; bias ribbon per TF |
| Daily | PDH/PDL, daily open, current day extremes |
| Sessions | Asia/London/NY shaded backgrounds with their high/low rails and the killzone bands |
| Liquidity | Every level as a horizontal ray from `confirmed_at` to its terminal event, coloured by side, styled by status (solid ACTIVE / struck-through SWEPT / dotted INVALIDATED), labelled with source + strength |
| Structure | Swing markers (HH/HL/LH/LL) at `confirmed_at`, BOS and CHoCH lines, the protected level as a dashed rail |
| Sweep | The trigger bar highlighted, the penetration depth annotated in ATR, the reclaim bar marked |
| Displacement | The leg shaded, `net_atr` and body ratio annotated |
| FVG / OB | Zones as rectangles from formation to mitigation, faded when mitigated |
| Trade | Entry, SL, TP rails; the risk box (entry→SL) in red and the reward box (entry→TP) in green; MAE/MFE markers; realised R label |
| Rejections | Optional layer showing invalidated setups and their reason — often more instructive than the trades |

### 22.3 Output

- Per-trade standalone HTML (self-contained, no external assets) with the §21.4 narrative
  beside the chart.
- A run-level index page linking every trade, filterable by the §21.2 dimensions.

The stated goal — "make it visually obvious WHY the bot entered" — is met by the narrative and
the `source_ids` chain being on the same page as the chart, not by the chart alone.

---

## §23. Worked end-to-end example

EURUSD, tier-3 (session) liquidity, **H4 confirmation (D-002), UTC H4 grid (D-001)**, entry
model C, SL model S1, TP model T1 at 2R. Winter, so London is UTC+0 and New York UTC−5. Prices
are illustrative but internally consistent — every number below is checkable against the rule
it cites.

```
CONTEXT (cached)
  Monthly bias  BULLISH  (BOS up, 2 months ago, price above protected low 1.0705)
  Weekly bias   BULLISH  (HH/HL intact)
  Daily bias    BULLISH  (BOS up yesterday)
  H4 bias       BULLISH
  score = +4 → FULL_ALIGNMENT.  gate_mode = score, min_score = 2 → PASS for a long.
  ATR_ref^H4 = 0.00380

LIQUIDITY
  ASIA_RANGE (NY 20:00–00:00 = 01:00–05:00 UTC) closes: high 1.16610, low 1.16500
  → LiquidityLevel L#417  SELL_SIDE  source SESSION_LOW  tier 3  price 1.16500
    confirmed_at 05:00 UTC, status ACTIVE, strength 1
  Merged with PREV_DAY_LOW 1.16495, confirmed at the 00:00 UTC day boundary
    (within 0.10 ATR) → price 1.16500, strength 2, tier 2 (the stronger tier wins)
  Age rule: session- and period-derived → exempt (§9.2.1), sweepable from the 08:00 bar

SWEEP  (H4 confirmation)
  08:00–12:00 UTC  O 1.16560  H 1.16600  L 1.16420  C 1.16540
     trigger and reclaim on the same bar → single_bar_sweep
     penetration 0.00080 = 0.21 ATR ∈ [0.05, 1.00] ✓
     wick ratio 0.00120/0.00180 = 0.667 ✓    close position 0.667 ✓
  → SWEEP_CONFIRMED at 12:00 UTC.  sweep_session = LONDON
  → Setup S#902 created, direction BULLISH, state LIQUIDITY_SWEPT
  → NO ORDER EXISTS.

CHoCH REFERENCE  (mode = major)
  Last unbroken H4 swing high before the sweep bar: 1.16690
    formed at yesterday's 12:00 UTC bar, confirmed at 00:00 UTC today (fractal_n = 2)
    unbroken check: max H over the five bars since formation = 1.16600 ≤ 1.16690 ✓
  distance from sweep extreme = 0.00270 = 0.71 ATR ≤ 3.0 ✓
  → state WAITING_FOR_CHOCH, deadline = sweep bar + 12 H4 bars (two trading days)

DISPLACEMENT + MSS
  12:00–16:00 UTC  O 1.16541  H 1.16680  L 1.16505  C 1.16668     up, body 0.00127
  16:00–20:00 UTC  O 1.16669  H 1.17040  L 1.16660  C 1.17010     up, body 0.00341
                                                    ← closes above 1.16690
  leg a = the 08:00 bar (clamped to the sweep extreme bar), b = the 16:00 bar, 3 bars ≤ 3 ✓
  net = 1.17010 − 1.16420 = 0.00590 = 1.55 ATR ≥ 1.5 ✓
  gross = 0.00180 + 0.00175 + 0.00380 = 0.00735
  bodies (up-closing bars only) = 0.00127 + 0.00341 = 0.00468 → ratio 0.637 ≥ 0.50 ✓
  bullish FVG in the leg: L(16:00) 1.16660 > H(08:00) 1.16600
     → zone [1.16600, 1.16660], size 0.00060 = 0.158 ATR ≥ 0.10 ✓   CE = 1.16630
  no new low below 1.16420 ✓   no opposing sweep ✓   bias gate still passes ✓
  → MSS CONFIRMED at 20:00 UTC.  mss_session = NEW_YORK
  → state CHOCH_CONFIRMED → WAITING_FOR_ENTRY

ENTRY  (model C, fvg_entry_point = ce)
  BUY LIMIT 1.16630, valid_from 20:00 UTC, expires +6 H4 bars (20:00 UTC tomorrow)
  SL (S1) = sweep_low 1.16420 − buffer
     buffer = max(0.10×0.00380, 2.0×0.00012, stops_level) = max(0.00038, 0.00024) = 0.00038
     SL = 1.16382     sl_distance = 0.00248 = 24.8 pips = 0.65 ATR ✓ (≤ 2.5, ≤ 60, ≥ 8)
  TP (T1, 2R) = 1.16630 + 2 × 0.00248 = 1.17126
  Nearest opposing liquidity PDH 1.17240 sits beyond the target ✓ (T2 would also clear min_rr)
  rr = 2.0 ≥ 1.5 ✓
  Size: equity €10,000, risk 0.35% = €35; EURUSD quote = USD, rate 1.1663
        value per price unit per lot = 100,000 / 1.1663 = €85,741
        raw_lots = 35 / (0.00248 × 85,741) = 0.1646 → floor to lot_step → 0.16 lots
        realised risk = 0.16 × 0.00248 × 85,741 = €34.02 ≥ 0.5 × €35 ✓
  Gates: spread 1.2 pips ≤ 2.0 ✓ and 1.2/24.8 = 4.8% ≤ 10% ✓; daily loss 0 ✓; 1 position ✓
  → order armed

FILL AND OUTCOME
  20:00–00:00 UTC  O 1.17008  H 1.17060  L 1.16610  C 1.16720
     L 1.16610 ≤ 1.16630 − 0.2 pip → filled at 1.16630 (ask 1.16642).  entry_session = NEW_YORK
  next day 12:00–16:00 UTC  H 1.17180 ≥ TP → exit 1.17126 (bid)
  Costs: 1.2 pips spread + $7/lot round-turn commission on 0.16 lots ($1.12 = 0.7 pips) = 1.9 pips
  Realised: +2.00R gross − 0.08R costs = +1.92R net ≈ €65.4
```

**Read the session line.** `sweep_session = LONDON`, `mss_session = NEW_YORK`,
`entry_session = NEW_YORK`. That is D-002 working exactly as specified, and it is why §0.4(a)
says this is a session-to-session model rather than the intraday London reversal the brief's
§6 example depicts. The trade is the same idea; the clock is not.

### 23.2 The same setup, invalidated three ways

| Variation | Outcome |
|---|---|
| The 16:00 bar closes 1.16660 (below the reference) and no H4 close exceeds 1.16690 within the window | `CHOCH_TIMEOUT` at the close of the 12th H4 bar after the sweep — two trading days later. Forward return recorded. No trade |
| The 12:00 bar prints a low of 1.16370 | `NEW_EXTREME`: 1.16420 − 1.16370 = 0.00050 > 0.10 × 0.00380 = 0.00038. The sweep failed; the level was accepted through. No trade |
| MSS confirms but Daily bias flips BEARISH at the 00:00 UTC day boundary before the limit fills | `BIAS_FLIP` cancels the unfilled order. Logged with the counterfactual outcome |

---

## §24. Cross-cutting edge cases

Per-component edge cases live with their components. These span the system.

| # | Situation | Ruling |
|---|---|---|
| 1 | Sunday-open gap over a level | No sweep possible (§9.6). Levels gapped through are INVALIDATED. Gap-affected bars tagged everywhere |
| 2 | Broker holiday: sparse bars | Session INCOMPLETE (§3.4); D1 bar skipped; region `DATA_SUSPECT` |
| 3 | DST desync weeks | Handled by tz-anchored sessions; every trade tagged `dst_desync` and reported separately (§3.3) |
| 4 | High-impact news bar sweeps three levels and displaces in one bar | Cluster → one setup (§9.4); same-bar CHoCH blocked (§9.6). This is the sub-population most likely to be unrepresentative and it is tagged for separate reporting |
| 5 | Two symbols in the same correlation cluster both signal | Cluster cap (§18.7) admits the higher-ranked; the other logs `RISK_LIMIT_CORRELATION` |
| 6 | Setup spans a weekend with `exit.close_before_weekend = true` | An unfilled pending order is cancelled at the weekend close; an open position is closed at market. Both tagged `WEEKEND_EXIT` |
| 7 | Process restart mid-setup | State is rebuilt from `events.jsonl`; live broker state is reconciled; a mismatch halts rather than guesses (§18.8) |
| 8 | Broker rejects the order (requote, market closed) | Retry `ops.order_retries` (default 2) with a fresh spread check. Then `ORDER_REJECTED`, setup terminal, logged with the broker's reason code |
| 9 | Partial fill of a limit order | Position sized to the filled amount; SL/TP placed for that amount; trade tagged `partial_fill` and excluded from headline expectancy (its risk was not the planned risk) |
| 10 | Symbol's `stops_level` widens beyond the planned stop after arming | Order cancelled (`SPREAD_EXCEEDS_STOP`), never adjusted |
| 11 | Equity changes between arming and filling | Size is computed at **arm** time and not recomputed at fill; the delta over a few bars is immaterial and recomputation introduces a path dependency that makes backtest and live diverge |
| 12 | Two setups on the same symbol, opposite directions, both reach MSS | `setup.max_active_per_direction = 1` permits one each, but `risk.max_positions_per_symbol = 1` admits only the first to fill. The second is `SUPERSEDED` |
| 13 | Daylight-saving change *during* an open trade | No effect: all internal time is UTC; only session labels shift |
| 14 | A level is swept in the same bar that invalidates it (deep penetration then close beyond) | Over-penetration is evaluated **before** reclaim (§9.3), so it invalidates. Order of evaluation is fixed and tested |
| 15 | The FVG used for entry is filled and invalidated before the limit order fills | `SL_BEFORE_ENTRY` or `ENTRY_EXPIRED` handles it; the FVG's own invalidation does not by itself cancel the order, because the entry price is already fixed |

---

## §25. Non-repainting and determinism

*(Brief §29, made testable rather than asserted.)*

### 25.1 The three guarantees

1. **Causality** — Axiom C (§1.2).
2. **Reproducibility** — the same data plus the same `config_hash` produces byte-identical
   `events.jsonl`. No wall-clock reads, no unseeded randomness, no dict-ordering dependence,
   no floating-point accumulation whose order varies with parallelism.
3. **Signal immutability** — an emitted signal is never modified. Labels may be amended
   (§5.4); signals may not.

### 25.2 The replay test (the load-bearing one)

```
for T in random_sample(all_bar_close_times, n=200):
    live_state    = replay_engine(data_full, stop_at=T).state_snapshot()
    truncated     = fresh_engine(data[:T]).state_snapshot()
    assert live_state == truncated
```

Any lookahead anywhere in the system makes these two differ, because the truncated engine
cannot see the future bars the leaking component used. This runs in CI over every engine and
is the primary defence — code review does not reliably catch a lookahead bug, and a
suspiciously good equity curve is a very late signal.

### 25.3 The shifted-data test

Re-run the whole backtest on data shifted forward by one bar (dropping the first bar). Trade
count and expectancy should change only marginally. A large change indicates an index-alignment
bug — an off-by-one in a `shift()` is the most common form of accidental lookahead and it
survives the replay test if the leak is exactly one bar and consistent.

### 25.4 Prohibited constructs

- `shift(-n)`, negative indices, `.iloc[i+1:]` inside any signal path.
- Aggregations over the full series (`series.max()`, `.rolling(...).mean()` without a
  `closed='left'` equivalent) used at a specific bar.
- Reading a higher-timeframe bar that has not closed.
- `datetime.now()` anywhere in the signal path. Time comes from the bar being processed, so
  the same code runs identically in backtest and live.
- Any `if backtest: ... else: ...` branch inside strategy logic. Environment differences live
  behind the broker interface (`ARCHITECTURE.md` §4), nowhere else.

A static-analysis check for the first four runs in CI over the strategy package.

### 25.5 Determinism controls

Seeded RNG (used only in Monte Carlo, never in the strategy); pinned dependency versions;
`config_hash` computed over the fully-resolved parameter set including defaults; dataset
manifest hash covering every input file. A result is identified by `(config_hash,
dataset_hash, code_commit)` and any result missing one of the three is not admissible.

---

## §26. Realistic execution modelling

*(Brief §27. The full protocol is in `BACKTEST_PROTOCOL.md` §3; the rules that bind the
strategy are here.)*

| Cost | Model | Default |
|---|---|---|
| **Spread** | Per symbol, per session, from measured tick data where available; otherwise a session-scaled constant | EURUSD 0.8 (LN/NY) / 1.6 (Asia) pips; +100% in the 5 minutes around high-impact releases if a calendar is available |
| **Commission** | Per lot per side | $3.5 per side per standard lot (raw-spread account assumption) |
| **Slippage — entries** | `slip.entry_pips` fixed + `slip.entry_atr_mult × ATR` | 0.2 pips + 0.02 ATR, always adverse |
| **Slippage — stops** | Larger and asymmetric | 0.5 pips + 0.05 ATR, always adverse. Stops fill worse than limits; modelling them symmetrically is a systematic optimism |
| **Slippage — gaps** | Fill at the gap open | Realised R may exceed the planned −1R. Recorded honestly |
| **Latency** | `exec.latency_ms` | 250 ms signal-to-order |
| **Swap** | Per symbol, per side, triple on `swap_3day_weekday` | From broker table; applied at 22:00 server time to open positions |

**Sensitivity requirement:** every headline result is reported at 1×, 1.5× and 2× the modelled
costs. A strategy whose expectancy is destroyed by 1.5× costs is not deployable, because real
costs vary by more than that between brokers and across time.

---

## §27. Development phases and acceptance gates

The brief's 17 phases, each with a definition of done. **A phase is not complete until its
gate passes; a failed gate stops the project rather than deferring the issue.**

| Phase | Deliverable | Gate |
|---|---|---|
| 1 | Data ingest, timeframe construction, session engine | DST fixture year passes; resampled H4/D1 reconciles with broker candles to within a known, explained difference; data-quality report clean |
| 2–4 | Monthly / Weekly / Daily analysis | Bias series manually reviewed against charts on 3 symbols; flip counts within expected bounds |
| 5 | H4 structure engine | Golden-file swings and BOS/CHoCH; replay test passes |
| 6 | Liquidity engine | Population and sweep-rate reports by source; no level ever created from a forming period |
| 7 | Sweep detection | Sweep counts stable across years; **standalone forward-return study of sweeps** (§9.7) |
| 8 | Displacement | Threshold distribution reported; rejection rate per setting |
| 9 | CHoCH/MSS | **The funnel report** (§11.7). Gate: ≥ 300 MSS events across the universe **and ≥ 120 on the three development symbols** over the in-sample period, or the design is reconsidered before any entry code is written. The second half of the gate was added by D-002: H4-only confirmation thins the funnel, and a universe-wide count can hide a development set too thin to iterate on |
| 10 | FVG | Standalone edge test |
| 11 | Order Block | Definition bake-off with the agreement matrix |
| 12 | Entry engine | All five models arm correctly on a fixture; fill logic verified against M1 |
| 13 | Risk management | Every limit exercised by scenario; sizing purity test passes |
| 14 | Backtest engine | Full protocol (`BACKTEST_PROTOCOL.md`); replay + shifted-data tests green; cost sensitivity run |
| 15 | Visualisation | 20 trades reviewed by eye from the event log; any disagreement between chart and log is a blocker |
| 16 | Paper trading | ≥ 60 trading days; live signals reconcile with a same-period backtest to ≥ 95% on entries and ≥ 90% on fills. A divergence above that is a defect, not variance |
| 17 | Live, minimum size | Only if phase 16 passed **and** the pre-registered OOS acceptance criteria were met. Start at `risk.pct_per_trade = 0.10%` |

**Phase 9 is the project's decision point.** If the funnel does not produce a testable sample,
no amount of downstream work can rescue it, and the honest response is to report the finding
and revisit the sequence (for example by moving confirmation to H1 universally) as an
explicit, pre-registered redesign rather than as a quiet parameter change.

---

## §28. Open decisions

**All 17 answered on 2026-08-25. See `DECISIONS.md` for the record and the consequences.**
`OPEN_QUESTIONS.md` retains the questions and their answers.

The two that were answered against the recommended default, and what they changed:

| Id | Answer | Changed |
|---|---|---|
| **D-001** (Q3) | `tf.day_boundary = UTC 00:00` | H4 grid fixed at 00/04/08/12/16/20 UTC (§2.2); D1/W1/MN1 buckets; surfaced and fixed the Sunday stub-bar defect (§2.6); NY anchor demoted to ablation |
| **D-002** (Q7) | H4 confirmation for every liquidity tier | `liq.tier_confirmation_tf` = all H4 (§8.6, §11.2); surfaced and fixed the level-age defect (§9.2.1); the model is now explicitly session-to-session rather than intraday (§0.4a); Phase 9 funnel gate now also evaluated on the development subset |

The remaining 15 took the recommended defaults, including Q1 (raw-spread ECN, USD or EUR),
Q2 (Dukascopy tick for research + broker M1 for the live-matching set) and Q6 (entry model A
as the pre-registered baseline, B–E as challengers on per-setup expectancy).
