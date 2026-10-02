# Strategy 1: atomic Torch replay and comparison

`StrategyOneReplay` is a separate faithful-contract implementation of numbered
app Strategy 1. It does not replace the application's runtime or the generic
approximate `ReplayRunner`. Its default policy is reconstructed from atomic
expression arrays; the broker and account remain the authority for fills,
reservations and financial feedback.

## Fixed experiment

- Date: **2026-08-18**, 04:00–09:30 America/New_York.
- Initial cash: **$10,000**; strategy decisions every **100 ms** (198,000 slots).
- Saved app run: `51dcacfb-bc9e-44a0-a9c8-8e2d2f103156`, financial/terminal sequence 6,443.
- Configuration hash: `0a1eaf114247e1cc9026fc556090c720a618726931d585889312aa9b27e4d812`.
- GPU: RTX 5090 Laptop; Torch 2.12.0+cu130.

The saved reference is cold-verified through existing read contracts. Its
configuration, market plan, saved seed population, source attempts and complete
financial journal are pinned. No baseline order or fill is consumed by the GPU
policy. The baseline journal is read only for the independent comparison.

The experiment holds strategy clock and source contracts fixed. The coarse
broker sums ten completed 100 ms liquidity histograms/eligible volumes and
interval extremes into one second; it samples the final available displayed
quote once. This is a resolution-dependent execution approximation. It can
change fills, fees, cash-dependent admissions and P&L. Broker matching uses the
latest working order state at the completed boundary; it does not reconstruct
the sequence of quote touches or amendments inside a coarse interval.

## Pipeline and source authority

```text
saved configuration + certified source attempts
    -> verified 6,100-listing market / 957-listing candidate population
    -> frozen app static admission funnel: 1,342 facts, 10 replay tickers
    -> 8 bounded Arrow readers, source projection, immutable hashed parquet
    -> completed 100ms / 1s / 30s banks and full stable-ID V7 geometry
    -> primitive policy compilation and CUDA graph capture
    -> causal replay / independent accounts / fill ledger
    -> compare to the verified app journal
```

`strategy_one_inputs.py` reuses the official market-day source SQL and
price-specific liquidity contracts. It never opens raw SIP files, substitutes a
fresh attempt, writes a ClickHouse table or publishes an app run. Child source
certificates use at most three readers. Every surviving ticker retains its
whole-session tape because a position can outlive a scanner episode.

This funnel is valid for the **frozen Strategy 1 release**. Searching a looser
upstream rule cannot reuse these ten tickers as the whole market: it requires a
new certified population/data envelope. Whole-session materialization is bounded
by an explicit memory guard. This session uses approximately 4.66 GiB of resident
source banks. Neither identities nor eligible price histograms are truncated.

## Atomic strategy representation

The strategy is a set of programs rather than one opaque strategy opcode:

| Program | Representation | Searchable behavior |
|---|---|---|
| Entry/reentry and additions | Integer `[L,4]` scalar instructions | Atomic input/op labels, backward operands, constrained thresholds |
| Capital fraction, initial stop, initial target | Numeric scalar instructions | Numeric formulas and bounded values |
| Resistance observation and protection | Primitive ATen operation array plus operand trees | Tensor operations over individual evidence/state fields; bounded named protection thresholds |
| Feature dependencies | `FeatureAtom` declarations | Field, physical unit, source resolution, lag, history length and reduction |

Scalar instructions are `(opcode, input_or_node_a, input_or_node_b, value_index)`.
Nonnegative references label one atomic input. Negative references select a
previous node. Thresholds are `[B,P]`: each candidate owns an independent
account. Gate graphs validate Boolean output; action graphs validate numeric
output and physical unit. Validation rejects forward references, incompatible
scalar units, invalid integer thresholds and out-of-range values before replay.

The multidimensional IR exposes gather, scatter reductions, prefix sums,
comparisons and arithmetic over individual state/evidence lanes. Its schema
checks names, shapes and dtypes. It is rebuilt from the closed operation
vocabulary before execution. It does **not** provide complete dimensional-unit
proofs for arbitrary ATen graph mutations; mutated programs still need semantic
validation. Structural axes/shapes are fixed for a compiled program.

Use `Graph.with_arrays(...)` for a topology mutation before compilation.
`StrategyOneReplay(..., candidates=..., protection_values=..., action_values=...)`
accepts candidate parameter rows. `update_candidates(...)` validates and copies
new values into the same captured buffers between objective evaluations.
Changing topology, input dependencies or tensor shapes requires preparing and
compiling a new program. This package implements the objective; it does not
implement a global optimizer.

### Histories, indicators and fundamentals

`feature_bank.py` prepares only declared dependencies. One atom is one field:
open, close, MACD line, MACD signal, float shares and shares outstanding are
separate inputs. Histories count completed **source observations**, not repeated
100 ms copies. The initial lookback can be 12; the supported envelope is 1–64
observations including lag. Preparation uses Polars expressions and backward
as-of alignment; the hot replay uses resident Torch lanes only.

The bar catalog includes OHLC/NBBO, volume/notional/counts, EMA, MACD, RSI and ATR
fields with their actual units and validity masks. Dollar prices can be derived
from canonical integer prices. Return/bps formulas belong in expression graphs.
For example, `(price / prior_close - 1).to_bps()` converts a normalized return
to basis points. `from_bps()` converts back before forming a price action;
ratio/return multiplication preserves price units. Input kinds and units must
match the declared engine/source catalog rather than being relabeled by search.
Missing or unready evidence is NaN; three-valued gates cannot turn unknown input
into entry permission through NOT.

Float/share inputs call the existing point-in-time QMD reference resolver with
session date, ticker and conid. Explicit known-at update clocks support intraday
snapshots; without them the start snapshot is held and recorded. RVOL requires
the hash-validated QMD prior-20-session baseline and completed canonical 1 s
volume. Missing required identity/baseline fails before replay. These optional
inputs are supported by contracts but are not used by the released Strategy 1
and were not included in the measured session; real-data bar histories were
smoke-tested separately.

V7 full stable identities are retained for lifecycle tracking. The existing
`v7.py` catalog also exposes the V6-style five levels above/below price for generic
policies. Rank slots do not replace the stable-ID history needed by Strategy 1.

## Causal lifecycle and accounting

For each 100 ms boundary: adaptive repricing, broker match, completed evidence,
position/source ownership, resistance observation, protection proposals and
acknowledgement, then addition/entry and Portfolio admission.

The engine retains source and position-owned histories separately, first-held
boundary exclusion, prior filled high/closed clock for reentry, ordered distinct
resistance breaks, unfinished stop groups, retryable amendments and up to three
purchase groups. Financial inputs are confirmed engine registers, never policy
predictions of a fill.

Cash admission follows app lexical ticker order because listings share one
account; candidate accounts remain vectorized. Reservations, three-position
limit, cash fence, risk and order limits are enforced by the engine. Policy
arrays cannot write cash, short inventory or acknowledge their own amendments.
Orders preserve adaptive limits, original/repair OCA pairs, partial fills,
side-shared interval liquidity, delayed stop execution and cumulative minimum
commission. Price comparisons use the canonical integer grid to avoid binary
floating equality changing stop triggers; dollar accounting uses float64.

The tape may be fetched ahead, but a policy receives only completed evidence at
its clock. Time is still causal. CUDA graph blocks remove host scalar decisions;
they do not remove the recurrence across time or shared-cash ordering.

## Measured comparison

The 100 ms GPU replay matched **all 210 saved app fills**, comparing ticker,
time, quantity, price and fee with absolute numeric tolerance 1e-8. Every run
ends flat with 26 closed episodes. The 1 s resolution changes fills and P&L.

| Backtest | Broker slots | Fills | Open positions | Gross realized | Fees | Net P&L | Replay seconds |
|---|---:|---:|---:|---:|---:|---:|---:|
| Saved app, 100 ms | 198,000 | 210 | 0 | $433.74546 | $279.365 | $154.38046 | 80.65, historical |
| GPU, 100 ms | 198,000 | 210 | 0 | $433.74546 | $279.365 | $154.38046 | **483.380** |
| GPU, 1 s | 19,800 | 204 | 0 | $651.20875 | $274.49 | $376.71875 | **488.989** |

All runs use **198,000 strategy slots**. The app timing was not remeasured here.
GPU timing is one measured replay per variant on the laptop, not a repeat median.
This is correctness evidence for this release/session, not a universal proof or
profitability acceptance. The faithful single-account GPU implementation is
slower than the historical app measurement. The earlier subsecond
approximate-engine benchmarks do not apply to it.

### Setup and optimization

The optimized invocation reused a hashed tape after fresh source certification.
Certificate stages took 12.316 + 3.728 + 10.660 seconds; packing/transfer took
4.033 seconds. A prior fresh bounded Arrow fetch took about 1.4 seconds.

| Variant | Compilation | Capture | Runner setup + replay + export |
|---|---:|---:|---:|
| 100 ms | 118.487 s | 32.164 s | 634.075 s |
| 1 s | 1.736 s | 2.461 s | 493.210 s |

These use existing compiler caches; they are not clean-machine compilation
benchmarks. The complete invocation, including both variants, certification,
preparation, reporting and two separately timed profiler samples, took
**1,160.030 seconds (19.33 minutes)**. Profiling is excluded from replay timing.

Caching Portfolio totals once per boundary reduced replay from 520.218 to
483.380 seconds (**7.1%**) at 100 ms and from 522.932 to 488.989 seconds
(**6.5%**) at 1 s. Both optimized fill ledgers match their unoptimized ledgers;
positions, fees and P&L are unchanged. Lexical admission still updates cached
reservation/risk/occupancy totals after every accepted order.

The optimized profiler records **1,800 GPU operations per strategy slot**, down
from roughly 1,900. Book updates, masks/copies, shared-cash admission and geometry
ordering remain expensive. CUDA graph capture reduces host dispatch but does
not fuse kernels. A coarser broker clock alone does not remove masked launches.
Packing account/order transitions into fewer kernels, caching geometry ordering
and specializing inactive broker phases remain performance work. Batched-candidate
throughput has not been measured; no speedup is claimed for it.

### Evidence and validation

Runtime root: `D:/TradingML/runtimes/vectorized_backtest/strategy_one_audit/`.

- Optimized comparison: `4f27e295236b4926bbb4c57c1c1c5008/report.json`.
- Unoptimized comparison: `e11a5aa42de946de99f2da915ff948fd/report.json`.
- `optimization_validation.json` verifies both complete fill ledgers unchanged.
- `pnl_by_ticker.csv` compares every traded ticker across all three runs.
- `source_snapshot/` preserves the exact benchmark Python sources; its hashes
  were checked against the report.

The default policy arrays remain identical after the separately tested optional
bps/return conversion and quiet-second RVOL additions. Those optional features
are not used by this released strategy or its measured replay.
The delivered package passes **138 tests**, including CPU/CUDA app-reducer and
broker oracles, atomic graph validation, quiet-second RVOL and compiled numeric
unit conversions. Ruff and whitespace checks pass. Real-data lag/12-bar history
values and 100 ms as-of alignment were independently checked against Polars.

## Running and artifacts

From the repository root, use the existing CUDA environment with
`PYTHONDONTWRITEBYTECODE=1`, `POLARS_MAX_THREADS=1` and external Torch/Triton cache
paths under `D:/TradingML/runtimes/vectorized_backtest`. Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:POLARS_MAX_THREADS = '1'
$env:TORCHINDUCTOR_CACHE_DIR = 'D:/TradingML/runtimes/vectorized_backtest/torch_inductor'
$env:TRITON_CACHE_DIR = 'D:/TradingML/runtimes/vectorized_backtest/torch_triton'
$env:TORCH_EXTENSIONS_DIR = 'D:/TradingML/runtimes/vectorized_backtest/torch_extensions'
& C:/Users/g835l/miniconda3/envs/ml4t/python.exe -B -u -m research.vectorized_backtest.v1.torch_backtest.audit_strategy_one
```

The default executes both full broker clocks. `--cache PATH` reuses a hash-checked
full tape after current certification; `--slots N` is explicitly a diagnostic
prefix. `--profile-kernels` traces one tick after each measured replay.

Each immutable runtime directory contains source hashes/tokens, saved reference,
release payload, scalar/ATen policy arrays, full GPU fill ledgers, per-episode
P&L/fees, open positions and timing. `report.json` distinguishes execution
completion from 100 ms parity. Replay timing includes final GPU synchronization;
preparation, compilation, capture, reset and reporting are separate.
