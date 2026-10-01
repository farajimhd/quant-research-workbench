# Torch backtest v1

A searchable strategy array compiled into a causal Torch replay. Market features,
source history, orders, positions and cash remain on the selected device during
replay. The initial history policy uses **12 completed source observations**;
the lookback is an integer search parameter with a declared range of **1–64**.

This package is a research engine. It implements the existing approximate broker
contract and the released Early Squeeze admission funnel. Its example downstream
policy is not the full Candidate 328 resistance/reentry/trailing lifecycle.

It also does **not** execute the app's numbered Strategy 1. The separate
[Strategy 1 audit](STRATEGY_ONE_AUDIT.md) records its verified app baseline,
initial tensor reducer work and the remaining full-engine integration gaps.

## The pipeline

```text
Program [L,4] + parameter values [P] + atomic catalog
    → validate units, ranges, graph and physical data requirements
    → ClickHouse price envelope → released Early Squeeze → persistent watchlist
    → certified, projected ARTE chunks for watchlist listings
    → align once and transfer a bounded tensor tape
    → compile/capture once
    → update Theta [B,P] → replay → objective [B]
```

The certified input boundary reuses `../strategy_encoding/clickhouse.py`. It
pins build/attempt provenance and point-in-time listing identities, verifies
source counts/hashes and storage authority, and reads ARTE-owned bars,
indicators and liquidity products. Broker capacity comes from
`arte.liquidity_100ms_v1`, through the shared broker source contract; interval
execution VWAP derives from notional/volume. No historical flatfiles are read.

The price envelope and completed 100 ms Early Squeeze stream run in ClickHouse.
The released impulse requires at least 5 bps price increase plus increasing
trade count and volume, with the released five-minute episode semantics.
Admission persists through session end. Only admitted listings' required
downstream fields are fetched. Fetching uses bounded listing groups and time
chunks where needed. Whole-session admission determines allocation, while each
actual admission timestamp gates trading causally.

ClickHouse and initial alignment still use the shared Polars/NumPy boundary.
The replay/objective loop uses Torch only. Moving certified database loading to
GPU would not remove its I/O cost; the optimization benefit comes from reusing
the resident tape across many candidate evaluations.

## Atomic inputs and feature dimensions

Each atomic label identifies **one field at one source resolution**, with units,
normalization, validity and availability contracts. For example, close, high and
RSI at 1 s are separate labels. OHLC is a collection of atomic fields, rather
than an untyped vector operand. This lets the compiler reject invalid unit
combinations and project only the required columns.

| Tensor | Shape | Meaning |
|---|---|---|
| Instruction array | `[L,4]` int64 | Operation label, input/reference a, input/reference b, parameter slot |
| Candidate parameters | `[B,P]` float64 | Independent parameterized policies |
| Current market tape | `[T,N,F]` float64 | Completed, causally available atomic fields |
| Source bank | `[M+1,F]` | Packed actual observations; sentinel row is unknown |
| Source cursor | `[T,N]` int64 | Latest packed record for each listing and clock |
| Source feature view | `[N,F,H]` | Newest-first history, gathered on device |
| Intermediate expression | `[B,N]` | Broadcast market fields and candidate-specific account state |
| Action proposals | `[B,N,10]` | Flag/value pairs for enter, exit, add, stop and target |
| Account history | `[T_session,B,4]` | Cash, realized P&L, equity, market value |
| Objective | `[B]` | Return minus configured drawdown penalty |

`N` is the stable listing axis, `B` the candidate axis, `F` atomic fields and `H`
history. A separate bank exists for each source/resolution; a 100 ms bar and a
1 s indicator do not share an observation counter. `WindowBank.gather(row,
clock, length)` exposes the requested `[N,F,H]` view. Banks prevent history from
crossing listing boundaries and mask unavailable/invalid observations with NaN.
The engine avoids materializing a large `[T,N,F,H]` tensor.

## Two kinds of sliding operations

**Source history:** `HistoryOperation` extends the instruction grammar:

| Label | Operation | Semantics |
|---|---|---|
| 50 | `BAR_LAG` | Value k completed source observations back; current is index 0 |
| 51 | `BAR_MIN` | Minimum of the latest k observations |
| 52 | `BAR_MAX` | Maximum of the latest k observations |
| 53 | `BAR_MEAN` | Mean of the latest k observations |
| 54 | `BAR_SUM` | Sum of the latest k observations |

Operand a must be an atomic market field; b is `UNUSED`. The parameter slot
holds a bounded positive integer count. Rolling operations include the current
completed observation and require a complete valid window; missing history
produces unknown rather than a partial-window aggregate. Each clock advances
the source cursor and gathers history. Ten 100 ms decisions seeing the same 1 s
bar therefore do not count that bar ten times.

The declared maximum sizes the gathered window; candidate-specific masks select
the actual count. A source count can change from 12 to 8 or 24 without rebuilding
the tape or capturing another graph. Increasing the declared maximum or adding
new physical fields requires a new validated strategy/tape envelope.

**Decision history:** the shared `LAG`, `ROLLING_MIN`, `ROLLING_MAX` and crossing
operations slide over strategy observations. They can operate on expression
outputs and account state. Bounded rings update only on strategy boundaries,
including warm-up. Their lookback sizes are fixed at compilation; changing them
requires a new runner. This distinction matters when strategy and source clocks
differ.

All other shared operation labels and dimension rules are preserved, including
absolute prices, return fractions, basis-point gaps, arithmetic, comparisons,
three-valued Boolean logic and action expressions. `describe(catalog)` returns
the search-facing vocabulary and constraints. NaN means unknown; unknown action
conditions do not trade.

## Timing and approximate fills

Strategy and broker resolutions are independent `Session` settings, including
100 ms. The common clock uses their greatest common divisor. At each boundary:

1. Process the just-completed broker interval, if this is a broker boundary.
2. Fill eligible previously submitted orders, bounded by participation × volume.
   Sells provide cash first; competing buys share available cash proportionally.
   Quantities remain integers, fees and accounting use float64.
3. Queue protection exits from completed close evidence.
4. On a strategy boundary, evaluate the expression graph and queue new actions.
5. Mark the portfolio and record account values.

An order can fill only in an interval that started at or after its submission.
Consequently, an action cannot use a completed bar and fill retrospectively
inside that same bar. Missing liquidity gives no fill capacity. Partial orders
carry forward. Stops/targets use completed closes and queue subsequent orders;
these are approximate liquidity-bar fills, not exact broker event execution.

Time remains sequential because cash, positions and pending orders depend on
the preceding boundary. Within a boundary, listings and candidates are
vectorized. CUDA graphs reduce the Python loop to graph submissions; there are
no per-tick device-to-host scalar reads, transfers or dataframe executions.

## Searching the parameters

### V6's five levels on each side

The Torch catalog now includes **170 V7 atomic inputs**: ten nearest slots,
each with V6's eleven fields plus three basis-point distances and three absolute
prices. The original V6 `[C,2,5,11]` ordering is preserved: below/equal first,
above second, nearest center first on each side. Support/resistance/transition
remain separate role flags. For example:

```text
v7.above[0].center_distance_rel   # return fraction from that candle's close
v7.above[0].center_distance_bps   # same distance × 10,000
v7.below[2].lower_price          # raw ARTE candle close × (1 + V6 distance)
v7.above[4].log_observation_count
v7.below[0].present
```

Every field is an atomic scalar operand with an explicit unit. Counts and age
retain V6's `log1p` representation and have dimensionless ratio units; comparing
them directly to raw count or seconds parameters is rejected. Role/history
flags are Boolean. An empty slot's presence is known false; its other fields
are masked unknown. A candle without a V6 record cannot grant permission.
Absolute prices use the matching pinned ARTE close, not the later decision
price. Bps and price variants are deterministic projections of V6 geometry.

`v7_example(lookback=12)` enters when both nearest slots are present and the
rolling mean of nearest-above center distance exceeds a parameterized bps gap.
It sets a stop from the nearest-below lower band multiplied by a searchable
price ratio, and a target from the nearest-above center. These are generic
research rules, not a reproduction of the released V7 lifecycle.

Source-history operations work on V7 numeric fields and `BAR_LAG` also works on
presence/role flags. The logical history layout is `[N,H,2,5,11]`; the replay
materializes only the requested atomic fields, not a dense full level-history
tensor. History follows each **ranked slot at each actual completed candle**.
Its underlying level may change; the tensor does not contain persistent level
IDs. The adapter uses this session's candles; it does not prepend prior-day
level history. Unknown initial windows suppress actions until enough current
session observations exist.

V7 policies require an existing **certified V6 day directory**, its declared
prior directory, and their runtime root. `prepare_session(...,
v6_day_root=..., v6_previous_root=..., v6_runtime_root=...)` verifies V6's
day/plan/census/file certificates and checks that the source build, definition
and attempt-unit hash match the backtest. It then fetches the existing ARTE
watchlist projection and reads only watchlist listings' level records from the
bank. It never generates structural levels or reads retrospective levels as an
intraday substitute. Missing listing/bank certificates fail closed; certified
listings with no V7 coverage retain V6's masked empty slots.

On a projection-cache miss, bank integrity verification hashes the complete
current/prior bank files, including non-watchlist data. The verified watchlist
projection is then retained under the runtime root. Subsequent preparations
check producer seal bytes, the pinned market source and the retained projection's
SHA256 before consuming that copy. They do not reread upstream bank arrays.
Use `--reaudit-v7` when a fresh upstream byte audit is required. Changed producer
seals create a new cache identity; corruption of the retained copy fails closed.
Repeated objective evaluations reuse the resident tape. See
[PERFORMANCE.md](PERFORMANCE.md) for the full-session profile and cache semantics.

To run the new policy, add these arguments to the launcher command below:

```powershell
--example v7 --lookback 12 --v6-day-root //DESKTOP-SAAI85T/Workstation-D/TradingML/runtimes/rl-v6-forward-40cd11fac/2026-08-18 --v6-previous-root //DESKTOP-SAAI85T/Workstation-D/TradingML/runtimes/rl-v6-forward-40cd11fac/2026-08-17 --v6-runtime-root //DESKTOP-SAAI85T/Workstation-D/TradingML/runtimes
```

These are existing certified session paths; the consumer does not create them.
The default `history` example and non-V7 loader behavior remain unchanged.

The adapter was validated on the real Aug 18 certified V6 bank for
**04:00–04:10 America/New_York**, 96 watchlist listings, 600 strategy decisions
and 600 broker intervals. Three compiled-graph GPU replays took 12.84, 11.67
and 12.82 ms (**median 12.82 ms**). Full account/final state matched Polars at
1e-7 monetary tolerance with exact integer state; the oracle took 3.52 s.
Current/prior V6 certificate verification cost **159.42 s**, market preparation
**29.88 s**, and level projection **2.05 s**. Total preparation was 191.37 s,
followed by 0.14 s alignment, 0.002 s transfer, 8.46 s JIT and 0.005 s capture.
One cold evaluation was therefore approximately **200 s**, excluding export and
oracle validation. The 12.82 ms result is the resident replay, not that cold
pipeline. This is an expression policy, with no neural model inference. The
immutable run report is `57d3c11c435a4038b205259bb129af7e/report.json` beneath
the launcher runtime root.

```python
from research.vectorized_backtest.v1.torch_backtest import (
    ReplayRunner,
    compile_strategy,
    prepare_session,
    to_tensors,
)
from research.vectorized_backtest.v1.torch_backtest.examples import history_example

program, catalog = history_example(lookback=12)
strategy = compile_strategy(program, catalog)
# session and funnel are the shared validated Session/Funnel contracts.
prepared = prepare_session(session, funnel, strategy.dependencies)
tape = to_tensors(prepared, strategy, device="cuda", max_gib=4.0)

# Columns: source count, mean gap bps, minimum RSI, cash fraction.
theta = [[8, 2.0, 50.0, 0.02], [12, 2.0, 50.0, 0.02], [24, 2.0, 50.0, 0.02]]
runner = ReplayRunner(strategy, tape, values=theta, backend="compiled_graph")
result = runner.run()
scores = result["objective"]  # [3], still on GPU

# Same B/P shape, topology and dependency envelope: reuse captured pointers.
runner.set_parameters(
    [[10, 3.0, 52.0, 0.02], [16, 3.0, 52.0, 0.02], [20, 3.0, 52.0, 0.02]]
)
next_scores = runner.run()["objective"]
```

Each candidate has its own cash, orders and participation capacity; candidates
are alternative experiments, not accounts competing for the same simulated
market liquidity. The runner resets in place before each run and is not
thread-safe. Changing instruction labels/topology requires recompilation;
changing dependencies requires preparing a compatible tape. Changing upstream
funnel thresholds changes the dataset/cache identity. Source-window policy
fingerprints canonicalize numeric parameter types so 12 and 12.0 match.

This is a discrete objective function, not a differentiable trading simulator.
The package does not implement a strategy search algorithm, V6 model inference,
PPO rollout/GAE or training. A V6 adapter must supply one sampled-policy inference
per 1 s decision and preserve the same timing/masking contracts. The timings
below include expression policies, not neural inference.

## Run a real session on the laptop

From the repository root in PowerShell, using the existing CUDA environment:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
& C:/Users/g835l/miniconda3/envs/ml4t/python.exe -B -m research.vectorized_backtest.v1.torch_backtest.run_backtest --example history --lookback 12 --device cuda --backend compiled_graph --date 2026-08-18 --start 04:00 --end 09:30 --strategy-ms 1000 --broker-ms 1000 --compare-polars
```

The launcher uses the shared certified build manifest/ledger and repository
environment discovery. It never prints credentials. Outputs go to unique run
directories under `D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v1`;
compiler caches also stay under the runtime root. It records provenance, source
requirements, program/catalog, code hashes, preparation/alignment/transfer/JIT/
capture/replay timings, accounts and final state. `--compare-polars` validates
the entire account trajectory and final state using the independent offline
source-window oracle and the existing Polars account engine.

Choose `eager` for CPU, `compile` for Torch compilation, `cudagraph` for capture,
or `compiled_graph` for compilation plus capture. Unsupported backends or
compilation failures raise errors; there is no silent fallback.

`--graph-steps 32` captures 32 sequential ticks per submission, with a separate
exact remainder graph. It preserves causal state and reduces host launches;
the default remains 1 because measured replay gains are small. Add
`--profile-kernels` to record a bounded mid-session CUDA trace outside benchmark
timings. `--baseline-report PATH` checks full exported accounts and final state
against a saved run already validated with `--compare-polars`.

## Measured results

Real ARTE data for **2026-08-18, America/New_York**, laptop RTX 5090 Laptop GPU,
Torch 2.12.0+cu130. Three GPU repeats; one CPU repeat. Each experiment used one
candidate. These are implementation benchmarks, not profitability evidence.

| Policy / session | Watchlist | Decision slots / broker slots | GPU median | Comparison |
|---|---:|---:|---:|---:|
| Seeded original policy, 04:00–09:30, 1 s / 1 s | 882 | 19,800 / 19,800 | **0.856 s** | Polars 118.68 s |
| 12-observation history policy, 04:00–09:30, 1 s / 1 s | 882 | 19,800 / 19,800 | **1.163 s** | Polars 134.25 s; CPU Torch 58.70 s |
| History policy, 04:00–04:10, 100 ms / 1 s | 96 | 6,000 / 600 | **0.151 s** | Polars 17.02 s |

The full history session narrowed 6,100 universe tickers to 2,289 price
candidates and 882 watchlist listings; 348,066 broker rows were fetched. The
resident tape used 1.707 GiB. One-time costs were **32.79 s preparation**,
**6.03 s alignment**, **0.19 s transfer**, **7.54 s compilation** and **0.005 s
capture**. Together with one replay, that is approximately **47.72 s** before
export/reference validation. Repeated objective evaluation reuses these costs.
Replay timing includes reset and final GPU synchronization, but excludes result
cloning/export and initial preparation/JIT/capture.

Full accounts and final integer state matched the Polars reference; monetary
comparisons used absolute tolerance 1e-7. The seeded policy's largest account
difference was approximately 5.8e-11. Reports are saved in runtime run folders:

- Seeded GPU: `3b90469b4e504d2ca390653614364a38`
- Full history GPU + oracle: `1da83c29cb14457e8b518d98c3d955be`
- Full history CPU: `271cf4c8b29a453d989ab4b208ea015a`
- Mixed-resolution ten-minute GPU + oracle: `f8271b49175d494da681257588947e06`

The full 5.5-hour session at 100 ms has **not** been benchmarked. The current
whole-session `[T,N,F]` tape can exceed its default 4 GiB guard at that resolution;
the implementation raises rather than truncating or silently moving to CPU.
GPU time chunking and a compact event-driven tape are future performance work.
The number of concurrent candidates also remains bounded by state memory.

## Source layout and verification

| File | Responsibility |
|---|---|
| `compiler.py` | ABI validation, units, operation lowering, temporal rings |
| `data.py` | Certified frame boundary, alignment, source banks, device transfer |
| `replay.py` | Orders, shared cash, partial fills, actions, equity and objective |
| `examples.py` | Seeded original policy and searchable source-history policy |
| `vocabulary.py` | Operation labels and constraints for future search |
| `v7.py` | V6 fixed-slot atomic catalog and certified day-bank adapter |
| `v7_cache.py` | Hashed retained V7 projection and producer-seal binding |
| `profiling.py` | Bounded causal-prefix CUDA kernel sample |
| `validation.py` | Full comparison against a Polars-verified saved run |
| `reference.py` | Independent offline source-window/Polars oracle |
| `export.py` | Explicit host reporting boundary |
| `run_backtest.py` | Real-data launcher, timing and immutable run artifacts |
| `tests/test_replay.py` | CPU/CUDA accounting, causality, masks, histories and captures |

Tests cover independent strategy/broker clocks, partial fills, shared cash,
three-valued logic, observation windows, source-count optimization, compiled CUDA
capture parameter updates and fail-closed input/memory contracts. Use the
existing Python environment with `-B`, disable pytest's repository cache, and
place its temporary directory under `D:/TradingML/runtimes`.
