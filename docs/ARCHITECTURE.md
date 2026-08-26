# Architecture — SMC Bot v1.0

Companion to `SMC_STRATEGY_SPECIFICATION_v1.0.md`.

---

## §1. The technology decision

**Recommendation: Python for everything that decides, MT5 for data and execution, and a small
MQL5 Expert Advisor whose only job is to protect open positions when the Python process is
not there.**

### 1.1 Why Python is the strategy language

The requirement that settles this is not performance, it is §28 of the brief (anti-overfitting)
plus §29 (no repainting). Both demand tooling that MQL5 does not have and cannot practically
grow: walk-forward harnesses, block bootstrap, deflated Sharpe, Monte Carlo resampling,
parameter-surface plots, and — most importantly — the replay/causality test of §25.2, which
requires running the whole engine repeatedly against truncated datasets. In MQL5 that test is
a research project of its own; in Python it is thirty lines.

Secondary reasons, in order of weight:

1. **One implementation.** The backtest and the live bot execute the *same* strategy module.
   The most common failure mode in retail algo trading is a strategy validated in one language
   and executed in another, where the two silently disagree. The specification forbids even an
   `if backtest:` branch inside strategy logic (§25.4); two languages would make that
   prohibition unenforceable.
2. The multi-timeframe, multi-object, stateful model here (levels, sweeps, setups, FVGs, OBs,
   each with a lifecycle) is ordinary object-oriented code in Python and painful in MQL5's
   procedural, array-oriented environment.
3. Timezone correctness (§3) needs the IANA database. Python has `zoneinfo` in the standard
   library. MQL5 has server time and offsets — precisely the thing §3 prohibits.
4. Data engineering: Parquet, pandas/polars, the resampling in §2, data-quality reporting.

**The honest cost:** the `MetaTrader5` Python package requires the MT5 terminal running on
**Windows** (or under Wine), it is not thread-safe, and round-trip latency is tens of
milliseconds. For an H4/H1 strategy where signals fire on bar closes and orders are limits or
market-on-open, that is immaterial. It would be disqualifying for a scalping system; it is not
one here.

### 1.2 Why MT5 rather than a REST broker API

- The strategy is FX-only and MT5 is the standard retail FX venue, so broker choice stays open.
- It supplies bars, ticks, symbol specifications, swap tables and account state through one
  interface, and the same terminal serves demo, paper and live.
- Phase 16 (paper trading) is a configuration change, not a port.

If the eventual broker is not MT5 (`OPEN_QUESTIONS.md` Q1), only the adapter in §4 changes;
nothing above it does. That is the point of the adapter.

### 1.3 What MQL5 is for — and what it is not for

**Not** for the strategy. Reimplementing the entry logic in MQL5 for "server-side execution"
recreates exactly the two-implementations problem in §1.1.

**Yes** for a ~200-line watchdog EA (§5.3) that runs in the same terminal and does three things
Python cannot guarantee when its own process has died:

1. Verify every open position has a stop; attach an emergency stop if one is missing.
2. On loss of the Python heartbeat for `watchdog.timeout_sec` (default 300), cancel pending
   orders and optionally flatten open positions per `watchdog.on_timeout ∈ {alert, cancel,
   flatten}` (default `cancel`).
3. Enforce a hard account-level equity floor independent of Python.

This is the only defensible reason to write MQL5 in this project, and it is a strong one: a
Python process that dies with an unprotected position open is the single most expensive failure
mode the system has.

### 1.4 Rejected alternatives, with reasons

| Option | Verdict |
|---|---|
| **MQL5 for everything** | Fails §28 and §25.2. Rejected on testability, not on capability |
| **Python strategy → MQL5 signal file → EA executes** | A second implementation of order logic plus a file-based IPC layer to debug. The watchdog gives the same crash protection at a fraction of the surface area |
| **backtrader / vectorbt / Backtesting.py as the engine** | vectorbt is vectorised and cannot express a per-setup state machine with causal multi-TF confirmation without fighting it end to end; backtrader's multi-TF handling and its data-replay semantics are hard to audit for the §25 guarantees. **Use `quantstats` / `pyfolio` for metrics and reporting; write the engine.** The engine is roughly 1,500 lines and is the part that must be *provably* causal — an inherited one cannot be proven |
| **Rust / C++ core** | Premature. The dataset is ~5 years × 10 symbols × M1 ≈ 20M bars; a Python engine over Parquet with numpy backing runs a full backtest in minutes. Revisit only if the walk-forward grid becomes the bottleneck |
| **PostgreSQL / TimescaleDB for bars** | Overkill for a single-machine research project and slower than Parquet for full-history scans. Chosen only if multi-machine or a live dashboard becomes a requirement |

### 1.5 Stack

| Layer | Choice | Note |
|---|---|---|
| Language | Python 3.12+ (built and tested on 3.14) | `zoneinfo` in stdlib |
| **tz database** | **`tzdata` package, pinned** | **Hard dependency.** Windows ships no system IANA database, so `ZoneInfo("America/New_York")` raises without it. Its version decides historical DST transitions and is recorded in the dataset manifest |
| Data frames | numpy in `core/`; polars/pandas only at reporting edges | **Revised in Phase 1.** Bucket construction is written explicitly over numpy arrays rather than delegated to a library `group_by_dynamic`: the anchored, DST-aware, stub-merging bucket rule of SPEC 2 is precisely the thing §2.1 says must not be inherited from someone else's semantics. Parquet I/O is pyarrow direct |
| Numerics | numpy | ATR, rolling windows |
| Bars storage | **Parquet**, partitioned `symbol/timeframe/year` | Columnar, compressed, no server |
| Event log | **JSONL** (append-only, one file per run) | Human-readable, greppable, replayable |
| Trades / rejections | **Parquet**, queried with **DuckDB** | Every §26 breakdown is one SQL query |
| Config | **YAML** + **pydantic** models | Types validated at load; unknown keys are an error, never ignored |
| Broker | `MetaTrader5` package | Behind the adapter of §4 |
| Charts | **plotly** (self-contained HTML) | No external assets, per §22.3 |
| Stats | `scipy`, `statsmodels`, `arch` (bootstrap), `quantstats` | |
| Tests | `pytest`, `hypothesis` (property tests for §5, §9) | |
| Scheduling | APScheduler, or a plain loop on bar close | The bot is bar-driven, not tick-driven |

---

## §2. Module layout

```
bot/
  config/          YAML files + pydantic schema + config_hash computation
  data/
    ingest.py          broker/file → Parquet; validation; dataset manifest
    resample.py        §2 timeframe construction, UTC day-boundary anchored; §2.6 stub-bar merge
    quality.py         gaps, duplicates, spikes, DATA_SUSPECT regions       [Phase 1]
    calendar.py        trading week, session windows, DST (zoneinfo)          [Phase 1]
    synthetic.py       fixture generator — test scaffolding, never a result   [Phase 1]
  core/                ← pure, no I/O, no clock, no broker. Every §5–§13 engine lives here
    bars.py            Bar/Series types, as-of accessors                      [Phase 1]
    indicators.py      Wilder ATR with the ATR_ref(i) = ATR(i−1) rule         [Phase 1]
    sessions.py        §3 SessionInstance builder (M15 source, SPEC 3.6)      [Phase 1]
    swings.py          §5 fractal detection, alternation, HH/HL/LH/LL          [Phase 5]
    structure.py       §6 trend state, BOS / CHoCH, protected swings           [Phase 5]
    bias.py            §7
    liquidity.py       §8 level sources, lifecycle, merge, rank                [Phase 6]
    sweeps.py          §9 penetration + reclaim, clusters, failures            [Phase 7]
    displacement.py    §10 leg-based drive test, fixed origin              [Phase 8]
    fvg.py             §12 detection, lifecycle, selection            [Phase 8/10]
    mss.py             §11 CHoCH reference selection, MSS confirmation    [Phase 9]
    order_blocks.py    §13 four definitions, zones, lifecycle           [Phase 11]
    entries.py         §15 five models, fill resolution, S1 stop        [Phase 12]
  strategy/
    setup.py           §14 Setup object
    machine.py         STATE_MACHINE.md transition table (data-driven, not if/else)
    stops.py           §16
    targets.py         §17
  risk/
    sizing.py          §18.2, pure function, no history
    limits.py          §18.4–18.7
    killswitch.py      §18.6
  execution/
    broker.py          §4 Protocol
    mt5_broker.py      live adapter
    sim_broker.py      backtest adapter (fills, slippage, costs)
  backtest/
    engine.py          bar loop, exactly the §4 ordering of STATE_MACHINE.md
    costs.py           spread / commission / swap / slippage models
    metrics.py, walkforward.py, montecarlo.py, ablation.py
  research/            ← the falsification suite (BACKTEST_PROTOCOL §6)
    sweep_study.py     §9.7 forward-return study of sweeps (H2)               [Phase 7]
    displacement_study.py  §10.6 threshold distribution + rejection rates  [Phase 8]
    funnel.py          §11.7 the sweep -> CHoCH -> MSS funnel and its gate  [Phase 9]
    fvg_study.py       §12.6 standalone FVG edge test                    [Phase 10]
    ob_study.py        §13.8 bake-off, agreement matrix, M_eff          [Phase 11]
    stats.py           shared bootstrap / power / verdict primitives     [Phase 10]
    marginal_value.py  §6.2 MSS vs CHoCH-not-MSS (tests H5)                  [study]
  reporting/
    charts.py          renders from events.jsonl only
    narrative.py       §21.4
    report.py          run report
  live/
    runner.py          bar-close loop, reconciliation, heartbeat
    heartbeat.py       to the MQL5 watchdog
mql5/
  SMCWatchdog.mq5      §5.3
```

**The `core/` boundary is the architectural rule that matters.** Nothing in `core/` may import
a broker, read a clock, touch the filesystem, or know whether it is in a backtest. Every
function there takes bars plus configuration and returns objects. That is what makes §25.2
cheap to run and what keeps the two execution environments honest.

---

## §3. Data contracts between modules

Contracts are pydantic models, versioned, and validated at every boundary in development and
backtest (validation may be disabled in the live hot path only if profiling shows it matters —
it will not, at H4).

```
BarSeries        symbol, timeframe, arrays(t, o, h, l, c, v), tz=UTC, manifest_hash
                 as_of(T) -> BarSeries       # the only way any engine reads bars
SwingSet         list[Swing]                 # Swing: type, formed_idx, confirmed_idx, price
StructureState   §6.1
LiquidityBook    active/swept/invalidated levels, ranked, in-play filtered
SweepEvent       §9.1
Setup            §14.1
EntryPlan        §15.1
OrderRequest     symbol, side, type, price, sl, tp[], lots, client_id, comment
Fill             order_id, price, lots, ts, slippage, spread_at_fill
Position         symbol, side, lots, entry, sl, tp[], opened_at, broker_ticket
```

`BarSeries.as_of(T)` is the **only** accessor exposed to `core/`. There is deliberately no way
to reach the raw arrays from an engine, which makes the most common lookahead bug — indexing
past the current bar — impossible to write rather than merely wrong.

---

## §4. Broker adapter

```python
class Broker(Protocol):
    def symbol_spec(self, symbol) -> SymbolSpec: ...
    def account(self) -> AccountState: ...          # equity, balance, currency, leverage
    def bars(self, symbol, timeframe, start, end) -> BarSeries: ...
    def current_spread(self, symbol) -> float: ...
    def place(self, req: OrderRequest) -> OrderResult: ...
    def cancel(self, order_id) -> OrderResult: ...
    def modify(self, ticket, sl=None, tp=None) -> OrderResult: ...
    def close(self, ticket, lots=None) -> OrderResult: ...
    def positions(self) -> list[Position]: ...
    def orders(self) -> list[OrderRequest]: ...
    def fx_rate(self, ccy_from, ccy_to, at) -> float: ...   # §18.2 pip-value conversion
```

Two implementations — `MT5Broker` and `SimBroker` — and **exactly one** strategy path above
them. `SimBroker` owns everything in §26: fill rules, the limit-fill buffer, slippage,
commission, swap, latency and the intrabar path resolution of §17.5. Cost modelling belongs to
the broker, not to the strategy, so that "what happens when costs double" is a broker
configuration rather than a strategy edit.

`fx_rate` is on the broker interface because §18.2 needs a quote→account conversion at a
historical instant; in the simulator it reads a stored rate series, which must therefore be
part of the dataset.

---

## §5. Runtime

### 5.1 Live loop

```
every 5 seconds:
    if not market_open(now): idle
    for tf in [M15, H4] where a new bar has closed since the last tick:     # H1 only for the §11.2 ablations
        run the §4 pipeline of STATE_MACHINE.md for that timeframe
    every 60s: reconcile with broker; emit heartbeat; re-evaluate risk limits
```

Bar-close-driven, never tick-driven. The 5-second poll only detects bar closes; the pipeline
itself runs at most once per closed bar, and it is idempotent per `(symbol, tf, bar_time)` so a
duplicate detection cannot double-process.

### 5.2 Concurrency

Single-threaded strategy path per symbol. Symbols may run in separate processes (the `core/`
engines share no state), but the **risk layer is global and single-owner**: portfolio limits
(§18.4) cannot be evaluated correctly from a process that can only see its own symbol. Design:
symbol workers propose orders; one risk process approves and places them.

### 5.3 The MQL5 watchdog

```
Python writes  <terminal>/MQL5/Files/smc_heartbeat.json  every 60s: {ts_utc, run_id, pid}
SMCWatchdog.mq5, on every tick:
    if now − heartbeat.ts > watchdog.timeout_sec (300):
        cancel pending orders
        ensure every open position has a stop; attach emergency SL at watchdog.emergency_sl_atr
        if watchdog.on_timeout == flatten: close all
        alert
    independently: if equity < watchdog.equity_floor: flatten and refuse new orders
```

The EA never opens a position and never reads strategy state. It is a safety device, and
keeping its scope this small is what makes it trustworthy without testing infrastructure of
its own.

### 5.4 Operations

Structured logging to stdout plus rotating files; alerts via a pluggable notifier (Telegram is
the pragmatic default) for kill switch, halts, broker errors, reconciliation mismatches and
data staleness; a daily summary containing trades, rejections by reason, and the funnel counts
of §11.7 — the funnel is the health metric that degrades first when something upstream breaks.

---

## §6. Configuration and determinism

### 6.1 Layering

```
defaults.yaml  →  profile.yaml (research | paper | live)  →  symbol overrides  →  CLI
```

Resolved into one frozen pydantic object. `config_hash = sha256(canonical_json(resolved))`,
stamped on every object, every event and every result row. Unknown keys are a load error: a
typo'd parameter name that is silently ignored means the run tested the default while the
report claims otherwise.

### 6.2 Run identity

```
run_id = (config_hash, dataset_hash, code_commit, run_type, started_at)
```

Registered in `runs.parquet` **before** execution. A result whose `run_id` is not in the
registry is not admissible — this is the mechanism behind the out-of-sample budget ledger
(`BACKTEST_PROTOCOL.md` §7) and it only works if registration happens up front.

### 6.3 Cache integrity

Cached HTF analyses (§2.5) store `(timeframe, period_start, bar_index, config_hash, payload)`.
On every read the engine asserts the period matches the current bar's period and the hash
matches the running config. On restart, all caches are recomputed and compared. **A mismatch
halts the bot** rather than refreshing the cache: the two most likely causes are a missed
rollover and a lookahead bug, and both are worse than downtime.

---

## §7. Testing strategy

| Layer | Approach |
|---|---|
| `core/` engines | Hand-built fixture bar sequences with every §n edge case enumerated; golden files for swings, structure, liquidity, sweeps |
| Property tests | `hypothesis`: swings alternate after normalisation; a sweep's extreme is always beyond its level; `protected_low` is non-decreasing within a bullish trend; sizing is monotone in stop distance |
| Causality | §25.2 replay test over 200 random cut points, in CI |
| Alignment | §25.3 shifted-data test |
| State machine | Every illegal transition in `STATE_MACHINE.md` §3.1 raises |
| Risk | Scenario tests for each limit; the sizing-purity test (no history argument reachable) |
| Execution | `SimBroker` fill rules against hand-computed M1 paths, including gap-through-stop |
| End-to-end | The §23 worked example reproduced bar for bar as an integration test |
| Static | Ban list of §25.4 constructs enforced over `core/` and `strategy/` |

CI runs the full suite plus a fixed-seed one-year backtest on EURUSD and diffs the trade
count and expectancy against a checked-in baseline. Any change to either is a deliberate
decision that must be acknowledged in the commit, not a surprise found weeks later.
