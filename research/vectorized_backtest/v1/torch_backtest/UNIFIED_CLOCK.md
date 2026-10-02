# Unified-clock GPU replay

The main clock drives **both policy evaluation and broker matching**. The
default is **1,000 ms**; **500 ms** is also supported. This is an explicitly
versioned research approximation of Strategy 1. It does not replace the app's
immutable release or the faithful 100 ms audit.

## Current causal version

`unified-strategy-one-causal-v2` matches the interval against orders already in
force at its start, before end-quote repricing. The former latest-working-order
approximation could apply an amendment at the same boundary as its fill. Source
rows are now explicitly sorted BEFORE coarse rounding, preserving first/last
chronological witnesses. Equity, purchase-group quantities, finite balances,
terminal marks and deterministic resets are checked at objective boundaries.

Full Aug 18 premarket, two independent repeats per clock:

| Clock | Steps | Median replay | Net P&L | Episodes | Fills | Open at end |
|---|---:|---:|---:|---:|---:|---:|
| 1 s | 19,800 | 38.89 s | $3,160.51 | 26 | 182 | 0 |
| 500 ms | 39,600 | 77.77 s | $560.87 | 25 | 210 | 0 |

Setup is additional (cached-source compile/capture: 25.92/17.20 s at 1 s,
23.80/15.64 s at 500 ms). The earlier results below are historical v1 evidence,
not the current implementation. Runtime receipts: `da515dd3c6654f07912e341e05e9e808`
and `c3891a03a7764802b67e1cf7f5cac277` under the Strategy 1 audit runtime root.

See [optimization and qualification](OPTIMIZATION.md) for the `[B,10]` genome,
objective, one-/two-session genetic search, validation split and restart rules.

## Clock and data contract

| Component | Contract |
|---|---|
| Main clock | 500 or 1,000 ms; session end must align, with no truncated tail |
| Broker | Same clock; existing orders match before new policy proposals |
| Order eligibility | Submitted by the completed interval's start; a new order cannot fill retrospectively |
| Strategy features | Timeframe must be at least the main clock; lower-timeframe inputs fail preflight |
| Main price bar | Aggregated completed 100 ms source buckets |
| MACD | Certified completed 1 s values; never averaged from 100 ms MACD |
| Resistance observations | Completed 1 s boundaries, with the causal producer geometry |
| Trailing low | Completed 30 s bars |
| Longer-timeframe inputs | Latest completed value only, without exposing a forming bar |
| Partial fills | Shared eligible-price capacity, OCA pairs and cumulative per-order fees |

At 1 s there are 19,800 steps in a 5.5-hour premarket session. At 500 ms there
are 39,600. There is no hidden 198,000-step loop and no retained 100 ms feature
bank in either variant. Source aggregation happens once in Polars during
preparation, before transfer. Replay decisions and financial calculations use
Torch on the GPU. No market products are written or generated.

Aggregation preserves first/last valid prices, extrema and volume/notional
sums. Equal-price execution capacities are merged without clipping. Quotes and
displayed sizes use the last valid snapshot; displayed size is never summed.
Quote age is recalculated at the coarse interval's actual end. Interval VWAP
is interval execution notional divided by execution volume; zero valid volume
does not fabricate a price. Empty sparse buckets retain the certified source's
zero-activity semantics; absent required evidence remains unknown.

At 500 ms, atomic price/quote/volume histories can be prepared from these
aggregates. A 12-bar mean, a lagged close and a quote size remain separate
atomic inputs with their declared units. Missing 500 ms MACD or other
indicators are rejected; the default policy uses available 1 s MACD instead.
Float/share and RVOL inputs retain their point-in-time and certified-baseline
contracts. `prepare_features(..., clock_ms=500)` prepares only the requested
feature dependencies on the main clock.

## Policy approximation and search boundary

The default add rule checks the main-clock price bar and bullish completed
1 s MACD, removing the faithful rule's separate 100 ms MACD requirement. The
latest certified candidate within each coarse interval is sampled at that
interval's completed boundary. Activation timestamps remain causal. This
changes entry timing, sizing, additions and subsequent protection paths.

The upstream certified candidate funnel is still the saved release's frozen
population, including its finer source-event evidence. The no-subclock rule
applies to downstream strategy feature dependencies, not raw database inputs
used for aggregation or the frozen upstream funnel. This tape is **not** an
unrestricted strategy-search universe: loosening upstream admission requires
a new certified search envelope.

Entry, add and numeric action policies remain atomic expression arrays;
resistance/protection policies retain their primitive tensor programs.
Threshold updates use `update_candidates()` between independent replays.
Changing instruction topology, batch shape or data dependencies requires new
preparation/compilation. The same resident tape and compiled runner can be
reused for many objective evaluations without database reads or graph capture.
These benchmarks include expression policies, not V6 neural inference or PPO.

## Runnable path

From the repository root, using the laptop CUDA environment:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:POLARS_MAX_THREADS = '1'
& C:/Users/g835l/miniconda3/envs/ml4t/python.exe -B -m research.vectorized_backtest.v1.torch_backtest.run_unified_backtest --repeats 3 --profile-kernels
```

The default saved-run identity selects **2026-08-18, 04:00–09:30 ET**, with
$10,000 initial cash. `--run-id` selects another compatible certified saved
04:00 baseline session. `--clock-ms 500` selects the half-second variant.
`--cache PATH` reuses a hash-verified source tape after current source
certification; omitting it performs the bounded certified database fetch.
`--graph-steps` overrides the unified default of one captured step. Runtime
artifacts and compiler caches stay under `D:/TradingML/runtimes`.

The original `audit_strategy_one` default remains the faithful 100 ms strategy
comparison with 100 ms/1 s brokers. It keeps the original policy and compile
stages; the additional fusion is enabled only for the unified approximation.

## Measured full-session results

RTX 5090 Laptop GPU, one candidate account, ten static-funnel survivors of the
saved 6,100-ticker universe. Full 5.5-hour replay; synchronized GPU timing.
Source tape was reused. The app timing is historical documentary evidence,
**not a fresh app run**.

| Engine | Main steps | Replay time | Net P&L | Closed episodes | Fills | Open positions |
|---|---:|---:|---:|---:|---:|---:|
| Saved app, 100 ms | 198,000 | 80.65 s | $154.38 | 26 | 210 | 0 |
| Previous faithful GPU, 100 ms | 198,000 | 483.38 s | $154.38 | 26 | 210 | 0 |
| Unified GPU, 1 s, three-run median | 19,800 | **39.15 s** | **$2,594.02** | 26 | 205 | 0 |
| Unified GPU, 500 ms, two-run median | 39,600 | **78.31 s** | **$153.97** | 25 | 207 | 0 |

1 s repeats: 39.130, 39.146 and 39.262 s. 500 ms repeats: 78.415 and 78.211 s.
Independent repeats matched final P&L, fill count and open-position state.
The complete 205-fill ledger matched the earlier 32-step graph experiment
after changing the graph block and adding supported bar-history aggregation.

The 1 s variant is 12.35 times faster than the prior GPU replay and 2.06 times
faster than the documented app run **for prepared replay**. It does not retain
the app's P&L closeness. The 500 ms aggregate P&L differs by about $0.41, but
its fill ledger and episode count differ; that is not faithful parity or proof
of equivalent behavior on other sessions.

### Setup is additional

| Measured stage | 1 s | 500 ms |
|---|---:|---:|
| Source certification | 26.14 s | 27.61 s |
| Fresh fetch | 0; cache reused | 0; cache reused |
| Aggregation, packing and GPU transfer | 3.65 s | 3.81 s |
| Compile/lowering, with existing compiler caches | 23.79 s | 125.85 s |
| Graph warmup/capture | 17.82 s | 43.11 s |
| Each prepared replay | 39.15 s | 78.31 s |

The complete three-replay 1 s job took 190.44 s. Subtracting the two additional
measured replays gives a **112.03 s single-evaluation equivalent**, including
initial verification, setup and reporting/profile overhead. This is derived,
not a separately measured fresh one-run job. It is still slower than the
documented app run end to end. The two-replay 500 ms job took 358.39 s.
Compilation/cache timings are setup-specific; the first new topology's 1 s
compile was 139.65 s. Do not treat warm replay timing as cold launch latency.

### Profile and retained optimization

Source gathers and broker financial writes are fused into compiled stages.
The default one-step graph avoids constructing a 32-step graph containing tens
of thousands of GPU nodes. Observed 1 s capture cost fell from 45.73 to 17.82 s;
those runs had different compiler-cache warmness, so these are measured setup
results rather than an isolated capture microbenchmark.

The final 1 s tick profile records 1,408 GPU kernels and about 1,905 microseconds
of device work. Broker matching accounts for approximately 899 microseconds,
and admission/reservations for 722; together about **85%**. Expression decisions
use approximately 21 microseconds. Further acceleration should consolidate
broker and admission state transitions into larger kernels. It should preserve
lexical shared-cash ordering, per-order commission history and liquidity/OCA
ownership. Database concurrency is not the dominant prepared-replay bottleneck.

Kernel profiles are excluded from replay timing. Attribution links both CUDA
driver and runtime launch correlation IDs to their CPU stage ranges, avoiding
double-counted parent/child profiler aggregates.

Runtime evidence under `D:/TradingML/runtimes/vectorized_backtest/strategy_one_audit`:

- `70e5c1be161648509339cc1a527cf3cf`: initial 1 s, 32-step graph.
- `2ef92b78fe11468da2a0c440037398cd`: optimized 1 s, three full replays.
- `3adce05503c24fafa000ea2d409b32f3`: 500 ms, two full replays.

Each run retains source tokens/hashes, policy arrays, reference financial
evidence, complete fills, episodes and a raw CUDA trace. A consolidated runtime
comparison includes corrected stage attribution from these original traces;
the original reports are retained unchanged.
