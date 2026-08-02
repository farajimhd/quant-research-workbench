# BarGPT v1

BarGPT v1 is the first causal multiscale bar representation model. It uses a
shared GPT-style decoder over continuous bar features, not discrete vocabulary
tokens. The implementation exposes contextual embeddings, dense next-bar
predictions, and direct physical-horizon quantiles.

## Data authority

The intraday storage authority is one rich row per completed, active 1-second
bucket in `market_sip_compact.bar_gpt_1s_bars_v1_cohort_2tb`. The table is built directly
inside ClickHouse from the ordered `events_YYYY` tables. It preserves:

- independent trade, quote-bid, and quote-ask price and size geometry;
- exact paired-quote spread, midpoint, microprice, and queue imbalance moments;
- family and paired-quote availability;
- locked/crossed quote counts and condition-token coverage;
- source ordinal/timestamp bounds and causal `available_at_us`;
- additive and first/last/min/max sufficient statistics required for exact
  loader-side rollups.

The table does **not** persist redundant `5s` through `1h` rows and does not
persist forecast targets. The loader derives fixed intraday scales from 1-second
sufficient statistics. Entirely inactive seconds remain sparse on disk; the
loader restores the one-second clock with explicit zero availability masks and
does not fabricate family OHLC values. Existing completed `1d` rows from
`macro_bars_by_time_symbol` are the calendar base; ISO-week and calendar-month
views are derived in the loader. `daily_range_query`,
`daily_family_frame_to_view`, and `calendar_period_ids` implement that path; the
loader never reads persisted weekly or monthly rows.

QMD structure decisions are not fabricated by the SQL builder. The v1 table
contains the exact paired-event geometry that QMD and the model can consume.
Timeframe-local structure transitions that require the shared sequential QMD
engine must arrive through a separately versioned point-in-time enrichment
contract before they are enabled as strict training inputs.

## One-second materialization

The destination and manifest tables require the SSD policy named by
`CLICKHOUSE_LIVE_STORAGE_POLICY`. The launcher fails before creating tables if
the policy is absent or unknown.

Safe preview with no writes:

```powershell
python -B -m research.bar_gpt.v1.run_build_1s --start-date 2026-07-24 --end-date 2026-07-25
```

Execute that bounded build:

```powershell
python -B -m research.bar_gpt.v1.run_build_1s --execute --start-date 2026-07-24 --end-date 2026-07-25
```

Execute all available coverage for the canonical cohort:

```powershell
python -B -m research.bar_gpt.v1.run_build_1s --execute
```

`BAR_GPT_COHORT_2TB` in `config.py` is the versioned 100-symbol authority. It
contains macro and sector instruments, liquid equities, extreme regimes,
lifecycle names, and persistently illiquid equities. The canonical launcher
uses that cohort and the dedicated `bar_gpt_1s_bars_v1_cohort_2tb` and
`bar_gpt_1s_build_manifest_v1_cohort_2tb` tables by default. `DataConfig` reads
the same target. Runtime preflight evidence records the resolved ticker count
and SHA-256 fingerprint. A custom `--tickers` list must also provide custom
`--target-table` and `--manifest-table` names so a different population cannot
silently contaminate the canonical cohort.

The full-coverage command is intentionally explicit because it is a large job.
The builder partitions work by market date and bounded ticker/event batches,
records completion only after each insert and manifest write, collapses safe
retry duplicates with `ReplacingMergeTree`, audits exact key uniqueness and
availability at monthly certification, and cancels the active query on Ctrl+C.

In an interactive terminal, the launcher retains a Rich dashboard showing the
current stage, market date and batch, durable day/unit/row/source-event counts,
skipped restart units, last-unit throughput, elapsed time, latest actionable
message, runtime evidence path, and a day-level progress bar. Completed,
failed, and interrupted states remain visible after the live display stops.
Narrow terminals use the same stacked hierarchy without truncating the current
batch. Redirected output uses stable timestamped JSONL evidence plus concise
plain-text lifecycle events and emits no cursor-control sequences. Ctrl+C marks
the run interrupted and returns exit code 130; during an active insert it also
submits the ClickHouse query cancellation before returning.

Runtime reports are written under:

```text
D:\TradingML\runtimes\bar_gpt\v1\build_1s
```

No runtime output is written into the repository.
The canonical launcher commands use Python's `-B` mode, and the launcher
propagates bytecode suppression to its child process, so workstation execution
does not create `__pycache__` under the synchronized code tree.

Training reads use incremental `ArrowStream` record batches through
`loader.py`; the one-second response is not materialized with `read_all()`. A
worker owns an ordered ticker shard, retains only the current session plus
bounded prepared examples, and yields the previous session as soon as its key
changes. Because the daily table is time-first rather than ticker-first, each
worker performs one bounded daily query for its complete ticker shard and
partitions that comparatively small result in memory instead of rescanning the
same seven-year daily range once per ticker.

Sparse storage is restored to the complete 04:00–20:00 New York one-second
clock. Empty seconds have zero availability masks; prices and activity are not
fabricated. This retains genuinely illiquid sessions and gives every example a
consistent session boundary. A session's exact 5-second through 1-hour rollups
are computed once and then sliced for non-overlapping groups of origins. The
default block encodes 2,048 context seconds and 512 origins together, with a
3,600-second target-only right halo. It therefore does not create one copied
input window per origin.

Daily rows for held-out and training tickers remain separate from the one-second
source. Weekly and monthly rows are computed from canonical daily sessions.
The last weekly/monthly group for a ticker is withheld until a following period
proves that the group closed, preventing a delisting or partial final period
from appearing as a completed calendar bar.

## Temporal contract

For simultaneous `1s` and `5s` inputs, the global origin clock moves one
second. The 5-second encoder advances only when a completed 5-second bar becomes
available. At fine anchor `t`, the coarse lookup is:

```text
last coarse index where coarse.available_at_us <= fine.anchor_us
```

The coarse stream is encoded once. Multiple fine origins gather its latest
causal contextual state rather than copying the same coarse window.

For a one-hour maximum prediction horizon, the loaded 1-second block includes a
3,600-bar target-only right support region. Horizon targets are indexed from
that support; it is not visible to the origin representation.

## Model

`BarGPTV1` is a decoder-only GPT-style model and uses:

- RMSNorm and pre-normalized decoder blocks;
- grouped-query causal self-attention;
- rotary position embeddings;
- SwiGLU feed-forward blocks;
- one shared backbone for every timeframe;
- continuous log-duration Fourier conditioning, so an unseen physical
  timeframe is accepted without adding a new vocabulary ID;
- microstructure (`1s`, `5s`, `30s`), intraday (`1m` through `1h`), and
  calendar (`1D`, `1W`, `1MO`) pathway identities;
- causal as-of fusion of coarse contextual states into fine origins;
- a next-bar head for dense autoregressive supervision at every scale;
- a low-rank horizon-conditioned monotone-quantile head for continuous targets;
- separate availability logits for trade, bid, ask, and paired quotes.

`features.py` converts the stored sufficient statistics into causal stationary
channels: close returns, open gaps, nonnegative excursions, log activity,
VWAP deviation, size dispersion, spread basis points, microprice lean, queue
imbalance, and availability. Absolute price levels are not passed to the shared
backbone.

The returned `embeddings` tensor is the bar-modality representation intended
for later point-in-time integration with `packed_market_model/v2`. The existing
packed model v1 remains unchanged.

`targets.py` builds scale-normalized endpoint return, path excursions, realized volatility,
activity, spread, imbalance, and family-availability targets for every physical
horizon from the dense right-support tensor. It uses index gathers, prefix
differences, and GPU pooling; it does not construct or store per-origin input
windows.

## Training

The canonical workstation launch is:

```powershell
python -B -m research.bar_gpt.v1.run_train
```

Before the first training run, exercise the exact multi-worker Arrow, online
rollup, collation, pinned-memory, and device-target path:

```powershell
python -B -m research.bar_gpt.v1.run_benchmark_loader
```

The bounded default uses four workers, 16 warmup batches, and 128 measured
batches over the last quarter of 2025. It reports cold-start latency, sustained
origins and batches per second, loader-wait p50/p95/max, device handoff plus
physical-target construction, and loader-wait share. The benchmark does not
claim capacity acceptance unless a measured trainer demand is supplied:

```powershell
python -B -m research.bar_gpt.v1.run_benchmark_loader `
  --required-origins-per-second <measured CUDA trainer demand>
```

The 2026 held-out validation path is independently benchmarked with:

```powershell
python -B -m research.bar_gpt.v1.run_benchmark_loader `
  --split validation `
  --start-date 2026-01-01 `
  --end-date 2026-08-01
```

`--progress-layout rich` forces the compact interactive progress surface;
redirected output is stable text, and `--json --progress-layout none` emits one
machine-readable result. The command is read-only and writes no runtime
artifacts.

The launcher prints the complete equivalent command before execution. Important
defaults are visible in `run_train.py`: 2,048 one-second context bars, 512
origins per block, batch size 2, four loader workers, a 384-wide eight-layer
decoder, BF16, six physical horizons from 5 seconds through 1 hour, and 50
million training origins. Overrides follow the printed defaults, for example:

```powershell
python -B -m research.bar_gpt.v1.run_train `
  --max-samples 1000000 `
  --run-name bar-gpt-v1-pilot
```

Training refuses to start unless the one-second target, its build manifest, and
the canonical daily table exist and the complete requested date range is
covered by contiguous `certified_range` records. The split is both
cross-sectional and point-in-time: a stable SHA-256 ranking holds out 15% of
tickers, and validation for those tickers begins at the configured validation
date. Training examples use bounded deterministic activity-regime resampling;
loss weighting remains balanced when a small batch does not contain every
regime.

The objective combines:

- dense next-bar Huber and availability losses at every scale;
- direct multi-horizon pinball loss for continuous targets;
- binary cross-entropy for horizon trade/bid/ask/quote availability.
- stop-gradient next-state cosine prediction at every scale as latent-prediction
  regularization.

Checkpoints contain model, optimizer, AMP scaler, exact epoch/batch cursor,
random states, and the model/data contract. Resume rejects a changed model or
data contract and deterministically skips to the next unconsumed batch:

```powershell
python -B -m research.bar_gpt.v1.run_train `
  --resume-checkpoint D:\TradingML\runtimes\bar_gpt\v1\train\<run>\checkpoints\checkpoint_latest.pt `
  --run-name <run>
```

Frozen embedding acceptance is a separate job, so probe gradients can never
alter the pretrained encoder:

```powershell
python -B -m research.bar_gpt.v1.run_linear_probe `
  --checkpoint D:\TradingML\runtimes\bar_gpt\v1\train\<run>\checkpoints\checkpoint_best_val.pt
```

It fits ridge probes for transformed endpoint return at every configured
horizon on non-held-out tickers and reports held-out-ticker R², MAE, and
directional accuracy. Probe weights, normalization, metrics, and a reproduction
manifest are stored under the linear-probe runtime root.

Each run owns one directory under `D:\TradingML\runtimes\bar_gpt\v1\train`
containing `config.json`, `run_manifest.json`, local JSONL metrics, checkpoints,
W&B files, and `model_card.json`. No generated training artifact is written to
the repository. The interactive Rich terminal keeps run health, current
ticker/date work, durable checkpoint, progress, throughput, loader/GPU time,
objective losses, and actionable messages visible. Redirected output uses
stable text without cursor-control sequences.
