# Approximate tensor replay and chronological PPO

The opt-in `--broker-engine tensor-100ms` is the versioned
`rl-v6-tensor-participation-100ms-v1` environment. The existing reference OMS
remains the default. This environment estimates fills using eligible 100 ms
volume, VWAP and observed half-spread. It does not claim queue-position or
price-level capacity equivalence. No random execution noise is added initially.

The policy proposes one action per completed second, as in the draft notebook.
This differs from the reference collector's up to 64 sequential proposals per
second. Any throughput comparison must distinguish that reduction in policy
work from the fill-kernel speedup. The action vocabulary, identity axis, actual
120-candle histories, attention, recurrent execution memory and learned
fractional size are retained. Held and pending identities survive reranking.

## Broker data and state

`BrokerShards` projects only ticker/bucket, eligible volume/notional, high/low
and validity, and fresh quote timestamp/bid/ask/validity from pinned ARTE
broker attempts. Resolution is explicitly 100 ms. Sparse Parquet chunks are
certified with source/identity/attempt hashes and content hashes and reused
across epochs. No feature windows, duplicate indicators, derived VWAP/spread,
or dense execution grids are persisted. The initial cache covers the certified
population so policy-dependent entries never change the historical product.

One I/O worker reads one chunk ahead while the current chunk executes. The
default 15-second transient `[150,N]` tensors have a conservative 1 GiB
materialization bound. Existing sparse modeled LULD sidecars are projected
as-of into these chunks; they are not duplicated on disk. Future execution
rows stay private to the broker. The policy receives completed candles and
realized execution/account state only.

Cash, quantities, reservations, fees, bracket orders, liquidity consumption,
marks, penalties and financial summaries are GPU tensors. Listing arithmetic
is vectorized. The 100 ms clock is ordered because cash and orders are
path-dependent. `--compile-broker` compiles the fixed-shape fill step; compilation
startup is measured separately. CUDA compiler caches must be placed under the
configured runtime using `TORCHINDUCTOR_CACHE_DIR` and `TRITON_CACHE_DIR`.

Orders start after their proposal. Participation defaults to 10% of eligible
volume; partial entries retain their original cash reservation. Fees accumulate
per order rather than charging another minimum for each fill. Passive targets
retain their limit after partial fills. Stops win ambiguous stop/target buckets
and execute no earlier than the next bucket. Observed spread is charged once.
Quotes older than one second or invalid/missing rows provide no fill capacity.
Target prices must remain inside modeled bands. Price rounding uses the
explicit research precision of $0.0001, not a claim of official listing ticks.

Halt and terminal-exposure shaping remain separate from financial P&L.
Mandatory liquidation is requested before the known session close. Unfilled
exposure remains reported, rather than being closed at an invented price.
Compact order, position and equity ledgers are exported after collection.

## Reconstruction and backward work

The approximate path retains token and continuous latent samples on GPU.
Reconstruction advances candle history, account observations, masks and actual
execution memory chronologically and retains the existing likelihood gate.

* Candle projection is a bounded per-BPTT-chunk GEMM and transfer. Projection
  is independent per candle; future projected rows are consumed only at their
  completed clock. Temporal history and attention remain causal.
* Each ordered group of actual executions uses a fused GRU sequence sharing
  the existing GRUCell parameters. FP32 precision is retained; TF32 RNN
  execution is disabled to preserve gradient parity.
* Decoder packets can use the existing bounded independent batch dimension
  with `--decoder-batch-size 8`; there is no attention across packet times.
* GAE uses an affine doubling scan on the approximate path: O(log T) launches
  and O(T) temporary memory. Terminal and zero-time transitions are retained.
  Float summation order changes slightly and is tested against the serial path.

CPU ranking/identity bookkeeping, Python clock iteration and dynamic held axes
remain. This implementation does not claim the entire policy/trainer is one
GPU graph. Reference teacher behavior and the running immutable teacher process
are unaffected. Full PPO production is not started by adding this option.

## Validation

CPU/CUDA tests exercise fills, fee/reservation accounting, delayed stops,
targets, modeled bands, causal observations, execution GRU value/gradient
parity, actual policy likelihood reconstruction, optimizer updates and ledger
export. `tensor_profile` separately measures the fill kernel and actual actor/
optimizer on an explicitly synthetic bounded fixture:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m research.rl_trading.v6.tensor_profile --runtime-root D:/TradingML/runtimes --listings 1000 --broker-seconds 600 --policy-clocks 32
```

These measurements establish implementation performance, not historical
profitability or closeness of estimated fills to actual executions. Historical
source canaries and rollout diagnostics must report source coverage, missing
quotes, fill rates and financial differences explicitly before a production
PPO campaign is resumed.
