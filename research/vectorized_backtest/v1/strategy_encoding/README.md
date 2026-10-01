# Encoded strategies and a watchlist-driven Polars backtest

This package implements a complete **research backtest pipeline**. A strategy is
an integer instruction array plus a numeric parameter array. ClickHouse computes
the upstream funnel, Polars receives only watchlisted listings, and a causal
replay evaluates compiled proposals against configurable liquidity intervals.

## Read the package in this order

| File | Responsibility |
| --- | --- |
| `config.py` | Session, funnel and broker-model settings |
| `catalog.py` | Atomic input labels, physical ARTE columns and units |
| `core.py` | Encoding, constraints, type checking and expression compilation |
| `examples.py` | A small, readable encoded strategy |
| `vocabulary.py` | Search-facing operation/input labels and parameter bounds |
| `clickhouse.py` | Certified admission SQL, bounded fetching and resumable caches |
| `replay.py` | Independent strategy/broker clocks, fills, state and objective |
| `run_backtest.py` | End-to-end real-data launcher and runtime report |
| `tests/test_pipeline.py` | Independent causal, typing and accounting checks |

```text
Certified ARTE population
       │
       ▼
ClickHouse price-envelope ticker pruning
       │
       ▼
ClickHouse 100ms Early Squeeze impulses + accepted episode starts
       │
       ▼
First watchlist admission per historical listing
       │                 membership persists to session end
       ▼
Chunked dependency-only bars / indicator projections
       │                 pre-admission history is warm-up, not permission to trade
       ▼
Resident sparse data + encoded program → Polars expressions
       │
       ▼
Causal account / order replay → objective and reproducible evidence
```

## 1. Atomic inputs

An input is one field, not a preassembled trading condition. An `AtomicInput`
declares its label, column, unit, resolution, validity mask and availability clock.

`arte_catalog()` exposes all persisted bar fields and indicator fields at the
supported resolutions: **100ms, 1s, 5s, 10s, 30s, 1m, 5m and 1h**. These include
OHLC, volume, trade count, execution volume/notional, EMA 7/9/12/15/20/26/50,
MACD line/signal/histogram, RSI 14, ATR 14, previous close, sample count,
average gain/loss and readiness flags. State inputs include holdings, entry
price, protection, pending quantity, cash and watchlist membership.

The catalog is a vocabulary, **not an instruction to fetch every field**. The
compiler's `dependencies` determine the projection. Preserve label meanings;
changing the catalog changes the strategy fingerprint.

Prices retain ARTE's integer source columns and an explicit `0.0001` scale.
Market inputs must carry an availability column measured in UTC microseconds.
Missing, invalid, nonfinite or future inputs propagate as unknown. Even `NOT`
does not turn an unavailable input into a trading permission.

## 2. Array contract

```text
instructions: [L, 4] int64
values:       [P]    float64
mask:         [L]    Boolean

instruction = [operation_label, operand_a, operand_b, parameter_slot]
```

* A nonnegative operand is an atomic input label.
* A negative operand `-(i + 1)` references instruction `i`'s result.
* `UNUSED` marks unused fields; it is not an input or result reference.
* A `PARAMETER` instruction exposes one value with its declared unit.
* References must point backward to non-action nodes. Cycles and forward
  references are rejected.
* Padding must be a trailing, masked `PAD` prefix-complement, never a hole in
  an active graph. Programs are bounded to 64 instructions and 64 parameters.

```python
from research.vectorized_backtest.v1.strategy_encoding import compile_strategy
from research.vectorized_backtest.v1.strategy_encoding.examples import momentum_example

program, catalog = momentum_example(
    gap_bps=20.0,
    min_rsi=60.0,
    cash_fraction=0.10,
)

instructions, values, mask = program.to_arrays()  # [16,4], [3], [16]
compiled = compile_strategy(program, catalog)
print(compiled.dependencies)
```

To inspect the complete search vocabulary, call `describe(catalog)`. To propose
a new candidate, edit the integer instructions, threshold values, or both, then
use `Program.from_arrays(instructions, values, mask)` and compile again. Invalid
units, references, thresholds or masks raise `EncodingError` before replay.

The instruction graph searches **structure**; the numeric values search
**thresholds and sizing**. They are separate arrays because categorical operation
labels are not continuous quantities. This is an executable search space, not a
differentiable objective: comparison gates and discrete fills have discontinuities.

The example expresses: close is at least a specified number of basis points
above EMA 20, RSI is above its threshold, enter with a cash fraction; exit when
MACD line is below signal. **It is a research example, not Candidate 328.**

## 3. Operations and parameter constraints

| Family | Operations | Unit rule |
| --- | --- | --- |
| Access | `INPUT`, `PARAMETER` | Metadata supplies the type/unit |
| Arithmetic | Add, subtract, multiply, divide | Equal units or explicitly dimensionless scaling |
| Normalization | Relative gap, gap in bps, return to bps | Compatible reference; zero denominator becomes unknown |
| Comparison | Greater, greater/equal, less, less/equal, equal | Equal units |
| Logic | AND, OR, NOT | Boolean operands |
| Temporal | Lag, rolling min/max, crossing above/below | Bounded causal observation history |
| Proposal | Enter, exit, add, set stop, set target | Boolean condition plus typed fraction/price |

Use subtraction for an absolute-price gap, `RELATIVE_GAP` for a fractional
return and `GAP_BPS` for a basis-point gap. RSI thresholds are native RSI points;
volume thresholds are shares. Converting an RSI value to price units is invalid.

Each `Parameter` defines a range, unit, optional discrete choices and whether it
must be an integer. RSI ranges lie within `[0,100]`; cash fractions within `[0,1]`.
`Constraint` supports cross-parameter relations such as `min_price < max_price`.
Composed temporal history must remain within `Catalog.max_lookback`.

The compiler materializes intermediate aliases before applying later windows.
This supports composed temporal operators without illegal nested Polars windows
or exponential duplication of a shared expression graph. All ticker calculations
remain native expressions; Python traverses the small instruction sequence once
per compilation.

**Temporal lookbacks count strategy observation rows**, not elapsed seconds or
distinct source candles. A 1s close sampled on a 100ms strategy clock may repeat.
Timeframe-specific bar-count rolling features need a separately declared source
projection; do not reinterpret observation windows as candle windows.

## 4. ClickHouse admission and prepared data

Default Early Squeeze admission uses the released completed-bar definition:
a valid 100ms close advances at least **5bps** versus the preceding valid sparse
bar, with increasing volume and trade count. `lagInFrame` performs the comparisons.
Native `arrayFold` preserves the **300-second episode expiry** before selecting
the first price-eligible accepted start. An impulse during an existing episode
does not become a new start because its price has crossed a filter boundary.

The initial price scan is a necessary-condition optimization: it removes listings
that never have a valid close inside the envelope. Their earlier/later prices do
not become policy observations. For retained listings the signal scan keeps its
full session prefix. Polars permits actions only at/after `admitted_at_us`.

The loader pins build/day/stage attempts, verifies full saved row counts, unique
keys and hashes, checks `live_market_ssd` table/part placement, and verifies the
historical population certificate. There is no raw SIP-file fallback and no
database writing. Only metadata/admission records cross the wire before watchlist
selection; downstream price and indicator rows belong to admitted listings.

Chunks have explicit schemas, response/memory bounds, SHA256 checks and atomic
progress checkpoints. Completed caches are sealed. Raw price validity failures
remain masked; an absent certified sparse interval has no execution capacity.
For a structural search, prepare the **union** of allowed feature dependencies.
A candidate outside that envelope is rejected. Changing upstream funnel settings
requires a new prepared dataset/cache identity.

## 5. Strategy and broker clocks

`Session.strategy_ms` and `Session.broker_ms` are independent. Examples:

| Strategy | Broker | Behavior |
| --- | --- | --- |
| 100ms | 1s | Frequent proposals; fills use subsequent complete 1s intervals |
| 1s | 100ms | Pending orders can receive ten broker updates between evaluations |

At a shared boundary, completed broker fills happen before strategy evaluation.
An order is eligible only if `submitted_us <= broker_interval_start`. It cannot
consume full volume/VWAP from an interval partly preceding its submission.

The replay uses persistent partial orders, volume participation, interval VWAP,
fee-aware proportional shared-cash allocation and average-cost accounting.
Exit proposals cancel unfinished buys after a holding exists. Additions require
an existing holding and no remaining order. Close-based broker protection works
between strategy evaluations and queues an exit for a subsequent interval.

The objective is marked return minus a fixed drawdown penalty. Fees,
participation and protection defaults belong to `Broker`, outside the strategy
search. Terminal holdings/orders are reported; no immediate liquidation is
fabricated. Source timestamps are UTC microseconds; sessions are specified in
America/New_York, and ARTE bucket indices start at local midnight.

## 6. Run and reuse

From the laptop repository, using a Python environment with Polars:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:POLARS_MAX_THREADS = '1'
python -B -m research.vectorized_backtest.v1.strategy_encoding.run_backtest

# Bounded real-data check: 600 strategy calls and 600 broker intervals.
python -B -m research.vectorized_backtest.v1.strategy_encoding.run_backtest --end 04:10

# Different clocks; Early Squeeze detection remains 100ms.
python -B -m research.vectorized_backtest.v1.strategy_encoding.run_backtest --end 04:10 --strategy-ms 100 --broker-ms 1000
```

The default date is **2026-08-18, 04:00–09:30 New York**. Manifest/ledger paths
point to the pinned workstation build. Runtime outputs go under
`D:/TradingML/runtimes/vectorized_backtest/encoded_strategy_v1`. Credentials are
loaded using existing environment discovery, never embedded or printed.

For repeated objective evaluations, call `prepare_session()` once, then compile
and call `replay.evaluate()` for each candidate. No network or file I/O happens
inside that objective. Data preparation and replay timings are separate.

```python
from research.vectorized_backtest.v1.strategy_encoding import (
    Broker,
    Program,
    compile_strategy,
    evaluate,
    prepare_session,
)

# config and funnel define the fixed upstream envelope.
prepared = prepare_session(config, funnel, compiled.dependencies)
for gap in (2.0, 5.0, 10.0):
    candidate = Program.from_arrays(instructions, [gap, 60.0, 0.10], mask)
    policy = compile_strategy(candidate, catalog)
    result = evaluate(policy, prepared, Broker())
    print(gap, result["objective"], result["wall_seconds"])
```

This loop iterates candidates, not tickers. For structural changes, first prepare
the union of candidate dependencies. A different upstream funnel needs another
preparation; reusing its old watchlist would evaluate a different problem.

## Boundaries and evidence

This package supports the released **admission signal** and generic downstream
research policies. It does **not** reproduce Candidate 328's event-driven V7
resistance selection, breakout reset/reentry, per-resistance addition lifecycle
or fixed-distance trailing implementation. Those need additional certified
structural/event adapters and explicit lifecycle state transitions. No released
strategy, production OMS, existing notebook or optimizer is changed here.

The focused tests cover type errors, padding, parameter constraints, serialization,
future-input masking, composed windows, historical identity partitioning, delayed
partial fills, mixed clocks, capacity, accounting, watchlist admission and future
tail invariance. The real ten-minute integration check admitted 96 of 6,100
certified listings and fetched 9,063 broker bars. Its example replay took about
3.6 seconds on one CPU thread. This is not a benchmark of the released lifecycle
and does not establish trading profitability or maximum attainable speed.

The full **2026-08-18 premarket, 04:00–09:30**, example run measured:

| Quantity | Measured value |
| --- | ---: |
| Certified universe | 6,100 listings |
| Price-envelope candidates | 2,289 |
| Persistent watchlist | 882 |
| Fetched broker rows | 348,066 |
| Resident projected source data | 44.2 MiB |
| Strategy evaluations / broker intervals | 19,800 / 19,800 |
| Cold preparation, including certification | 34.44 seconds |
| Resident replay, including feed preparation | 122.97 seconds |
| Preparation plus replay | 157.41 seconds |

A separate real ten-minute mixed-clock run used **6,000 strategy evaluations
at 100ms** and **600 broker intervals at 1s**. Replay took **17.51 seconds**;
cold preparation took 34.76 seconds. Its final return, fees and filled quantity
matched the 1s/1s example: the selected market features update at 1s, and all
sub-second proposals wait for a subsequent complete broker interval.

These are measured single-thread laptop results, not an ETA extrapolation.
Resident-source size excludes transient frames and Python feed-index overhead.
The generic engine is slower than the earlier specialized notebook; vectorizing
across listings does not eliminate per-clock dataframe planning and joins. This
version prioritizes a checked, reusable objective contract and does not claim
maximum speed. Exact stateless-policy batching and compiled/account execution
optimizations need separate equivalence checks before adoption. Every CLI run
saves its program, catalog, code hash, source identity, clocks, timings and
account/final-state evidence under a unique runtime run directory.
