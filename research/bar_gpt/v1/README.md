# BarGPT v1

BarGPT v1 is a causal multiscale bar representation model. It uses a shared
GPT-style decoder over continuous bar features, not discrete vocabulary tokens.
It exposes contextual embeddings, dense next-bar predictions, and direct
physical-horizon quantiles.

## Data authority

## Model contract

BarGPT is a decoder-only continuous-token transformer. It does not tokenize
prices and it does not switch into separate daily or weekly modes. Every
example is anchored at a 1-second origin and carries the same multiscale view
set: `1s`, `5s`, `10s`, `30s`, `1m`, `5m`, `30m`, `1h`, `1D`, `1W`, and `1MO`.
Each view is aligned by its causal `available_at_us` timestamp; a bar that was
not complete at the origin is masked, never backfilled from the future.

The model returns the origin embedding, autoregressive next-bar heads for the
intraday views only, and direct probabilistic physical-horizon heads. The direct heads
currently cover `5s`, `30s`, `1m`, `5m`, `15m`, and `1h`. Each direct horizon
contains the target contract in `targets.py`: endpoint return, upper/lower
excursion, realized volatility, log trade volume/count, trade/bid/ask/paired
quote availability, and halt/resume/news/LULD risk flags. Future support is
constructed on the loader/device side and masked when unavailable, preserving
point-in-time causality.

The daily/weekly/monthly views are context inputs at every origin. They are not
target views and have no autoregressive heads or losses. Adding explicit
1D/1W/1MO forecast horizons would change the output contract and requires a
new checkpoint rather than resuming the current one.

Every run writes reviewable architecture evidence to its runtime
`artifacts/` directory: `model_details.json`, `model_parameters.jsonl`,
`model_summary.txt`, `model_architecture.mmd`, and `model_architecture.md`.
For interactive review, open `inspect_model.ipynb`; it renders the same
torchinfo/torchview artifacts, prints real batch and target shapes, and runs a
bounded checkpoint evaluation when `CHECKPOINT` is set.

Training and production consume raw point-in-time SIP prices. The intraday
authority is one rich row per completed active one-second bucket in
`market_sip_compact.bar_gpt_1s_bars_v1_cohort_2tb`. It preserves:

- independent trade, quote-bid, and quote-ask price and size geometry;
- paired-quote spread, midpoint, microprice, and queue-imbalance moments;
- family availability, locked/crossed counts, and condition coverage;
- source ordinal/timestamp bounds and causal `available_at_us`;
- composable sufficient statistics for exact loader-side rollups.

The table does not store redundant 5-second through 1-hour rows or forecast
targets. The loader restores the 04:00-20:00 New York one-second clock with
explicit availability masks and builds the larger intraday bars online.

Exact halt/pause, resume, news-risk, and LULD-state events live in the sparse
`intraday_condition_bars_by_time_ticker` sidecar. Build and certify its 1-second
cohort coverage independently, without rebuilding the already complete base
bars:

```powershell
python -B -m research.bar_gpt.v1.run_build_conditions_1s
```

Add `--replace-existing` only when intentionally replacing a partially built
condition partition. Zero-event dates are still certified in the status table,
so an absent sparse row means a real negative label rather than missing data.
The BarGPT launcher enables that repair mode by default: certified dates are
never touched, while rows from an interrupted uncertified date are deleted and
rebuilt before its certification advances. The condition table itself contains
only seconds with at least one exact condition flag; zero and unchanged clock
seconds are never materialized.

The daily authority is
`market_sip_compact.daily_session_bars_by_symbol_time_v1`. It is built directly
from ordered SIP events and maintained by `download_update_events`. Each
ticker/date has explicit premarket, regular, and after-hours rows, including
zero-activity sessions. The loader collapses only completed session rows into
1D and derives completed ISO-week and calendar-month views online.

Massive aggregate bars are excluded because they do not retain the event,
quote, and size geometry of the SIP contract. Training begins in 2019. At that
authority boundary, missing 1D/1W/1MO history is represented by a zero padding
row plus causal `asof=-1`, never by a fabricated completed bar. As completed
2019 daily sessions become available, the loader grows the calendar prefixes
from 1 to their 90-day, 52-week, and 12-month caps. Dates on or after the
current session remain excluded.

Build and certify the missing 2019 one-second authority before requesting 2019
training or offline shards. The end date is exclusive:

```powershell
python -B -m research.bar_gpt.v1.run_build_1s --start-date 2019-01-01 --end-date 2020-01-01
python -B -m research.bar_gpt.v1.run_build_1s --start-date 2019-01-01 --end-date 2020-01-01 --execute
python -B -m research.bar_gpt.v1.run_build_conditions_1s --start-date 2019-01-01 --end-date 2020-01-01
```

The first command is a safe plan. The one-second builder is manifest-resumable;
the conditions launcher retains its explicit replace-and-certify behavior for
the requested range. The identity-alias builder already begins in 2019, and the
daily-session authority must also remain continuously certified from
`2019-01-01`.

## Causal split and identity contract

Globally adjusted historical tables are retired from every BarGPT default and
runnable path. A future split must never change an input at an earlier anchor.
For a share multiplier `q = split_to / split_from`, define `C(t)` as the product
of splits effective by `t`. For an example anchored at `a`:

```text
price_in_anchor_basis(t) = raw_price(t) * C(t) / C(a)
size_in_anchor_basis(t)  = raw_size(t)  * C(a) / C(t)
```

Counts, availability, queue imbalance, and price-size notional are invariant.
This covers forward splits, reverse splits, and multiple splits:

- context wholly before or after a split is unchanged;
- context crossing an already effective split is converted to the anchor basis;
- a future scheduled split never changes model input;
- a target crossing a realized split uses `C(target) / C(anchor)` only inside
  target construction, removing the mechanical price jump without leaking the
  future action into input;
- sizes inside a crossing target are converted back to the origin share basis;
- a split exactly at the 04:00 New York boundary uses the new basis.

Each loader worker reads the small action schedule once from
`q_live.market_stock_split_v1`. Conflicting ratios, invalid factors, ambiguous
identity mappings, or missing reference tables fail closed. Split execution
sessions are excluded from examples and calendar context because date-level
reference data cannot prove the basis of mixed late or corrected prints within
that session. Cash dividends, special distributions, spin-offs, merger
consideration, and ticker changes are not share-unit transformations: they
remain real market events or identity boundaries and are never silently treated
as splits.

Ticker aliases come from `q_live.id_symbol_interval_v1` and
`q_live.market_ticker_event_entity_v1`; when the canonical interval graph is
not yet populated, the loader reconstructs the same bounded timeline from
`q_live.market_ticker_event_v1`. Both the one-second and daily-context queries
use only the source ticker valid for each date and emit the stable canonical
model ticker. This retains FB history for canonical META while excluding the
unrelated earlier security that used META.

The raw 100-symbol materialization remains intact. Training currently
quarantines `GOOGL` because its provider event timeline ends at `GOOG`, and
`MOGO` because q_live has no ticker-event entity for it. They are explicit
reference-data failures, not silently renamed or dropped; they can re-enter the
training population after the q_live authority is repaired.

The retired adjusted database tables remain immutable evidence. Repository
configuration rejects them, and their builders and launchers have been removed.

## Materialization

The raw tables require the SSD policy named by
`CLICKHOUSE_LIVE_STORAGE_POLICY`. Inspect the complete run chain without
querying or writing ClickHouse:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_bar_gpt_data_build.ps1 -CommandOnly
```

Preview, then execute all resumable raw authorities:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_bar_gpt_data_build.ps1
powershell -ExecutionPolicy Bypass -File scripts\run_bar_gpt_data_build.ps1 -Execute
```

The chain runs the raw cohort 1-second builder, the raw point-in-time alias
builder, and the shared raw SIP daily-session builder. Alias rows such as FB use
the same raw schema with an independent manifest; monthly audits are
ticker-scoped so canonical and alias certification cannot contaminate one
another. Each child retains its Rich progress display. A failed or interrupted
stage prevents downstream execution. After Ctrl+C, wait for the child to return
and rerun the same command; completed certified units are skipped and only the
incomplete unit is retried.

The standalone one-second commands are:

```powershell
python -B -m research.bar_gpt.v1.run_build_1s
python -B -m research.bar_gpt.v1.run_build_1s --execute
```

Runtime evidence is written under `D:\TradingML\runtimes\bar_gpt\v1`; no
generated artifact is written into the repository.

## Loader and temporal contract

Training uses a certified, indexable global block plan. The durable work unit is
one canonical ticker over one calendar month. Months remain chronological for
partition locality and ticker order is deterministically shuffled inside each
month. PyTorch map workers fetch exact bounded future windows concurrently, but
the DataLoader emits only global sampler order: every block of the active
ticker-month reaches the trainer before the next ticker-month. Physical worker
identity never enters the training cursor.

Each bounded ClickHouse query contains only causal context, one or more planned
4,096-origin blocks, and target-only support. A response is buffered to a
complete Arrow boundary before any row is exposed. Truncated HTTP/Arrow reads
therefore contribute zero rows and retry the identical query with exponential
backoff; they cannot duplicate or skip an origin. Workers cache point-in-time
identity, split, and daily history and prefetch their future positions into a
bounded 64-block RAM cache. The CUDA stream stages the next collated batch while
the current batch computes. Source predicates remain qualified so the projected
canonical ticker cannot broaden a ClickHouse scan.

The prior completed session contributes up to 2,048 one-second context rows to
the first premarket origins. No overnight seconds are fabricated: timestamps
and `available_at_us` retain the real wall-clock boundary, while targets remain
inside the target session. Model time channels encode the actual New York
session-clock position from every bar timestamp, elapsed wall-clock ratio, and
an explicit sequence-boundary flag; they are not relative to the sampled window.
Autoregressive next-bar loss is masked across the overnight gap.

For simultaneous 1s and 5s inputs, the global origin clock moves one second and
the 5s encoder advances only after its bar closes:

```text
last coarse index where coarse.available_at_us <= fine.anchor_us
```

The coarse stream is encoded once and gathered causally for fine origins. The
default block has 2,048 input seconds, 4,096 origins, and a 3,600-second
target-only right halo. Horizon targets are built on the GPU from this shared
support with gathers, prefix differences, and pooling; the right halo is never
visible to the representation.

Daily rows are normalized to the current anchor basis before weekly and monthly
aggregation. This prevents a calendar bar spanning a split from combining
incompatible price or share units. The final week or month is withheld until a
following period proves it closed.

## Model

`BarGPTV1` is a decoder-only GPT-style model with:

- RMSNorm pre-normalized decoder blocks;
- grouped-query causal attention and rotary positions;
- SwiGLU feed-forward blocks;
- one shared backbone for every timeframe;
- continuous log-duration Fourier conditioning for unseen timeframes;
- microstructure, intraday, and calendar pathway identities;
- causal as-of fusion of coarse contextual states into fine origins;
- dense next-bar supervision at every scale;
- low-rank horizon-conditioned monotone quantiles;
- horizon quantiles for endpoint return, upper/lower excursion, realized
  volatility, trade volume, and trade count;
- separate trade, bid, ask, paired-quote, halt/pause, resume, news-risk, and
  LULD-state probability logits.

Spread and queue imbalance remain informative causal inputs but are not direct
forecast heads; the lower-value redundant liquidity heads were replaced by the
four exact condition-risk targets.

`features.py` projects raw sufficient statistics into stationary channels:
returns, opening gaps, excursions, log activity, VWAP deviation, size
dispersion, spread basis points, microprice lean, queue imbalance, and
availability. Absolute price is not passed to the backbone. The returned
embedding is the bar modality intended for later point-in-time multimodal
integration.

## Training

Benchmark the exact Arrow, online-rollup, collation, pinned-memory, split
normalization, and device-target path before training:

```powershell
python -B -m research.bar_gpt.v1.run_benchmark_loader
```

The canonical workstation training command is:

```powershell
python -B -m research.bar_gpt.v1.run_train
```

The canonical launcher now trains from certified v2 offline shards. Its bounded
training selection is `[2019-01-01, 2021-01-01)` and its fixed validation
selection is `[2026-01-01, 2026-08-01)`. One coverage epoch is one deterministic
exhaustive pass over every stored 04:00-20:00 block in the non-validation
tickers. Startup reads sidecar metadata—not tensor payloads—to verify every
requested ticker-month and derive exact session, block, and origin totals;
those totals drive progress, scheduling, validation spacing, ETA, and the
resume-plan hash. The final hour remains an origin
population, while horizons extending past 20:00 are masked rather than crossing
overnight.

Progress is origin-clocked, not optimizer-step-clocked. The primary bar is the
current epoch's planned origin budget and is labeled `epoch X/Y`; `run origins`
is cumulative across all epochs or stops at an explicit `--max-samples`
diagnostic cap. One origin is one supervised one-second prediction anchor. The
default has one epoch, so its epoch and full-run budgets are identical.

One block is one ticker and one contiguous 4,096-second origin interval. It is
one efficiently encoded sequence but contributes 4,096 distinct supervised
origins and target sets—not one example with one target. The visible 1-second
tensor contains 2,048 preceding context rows plus the 4,096
origin rows. Causal attention lets the first origin use the preceding 2,048
rows; each later origin may also use the earlier origin rows because those
seconds are already known at its as-of time. Thus the last origin has a 6,143-row
causal prefix. This is standard autoregressive packed-sequence training and is
equivalent to 4,096 causal examples with shared computation, except that it does
not impose a strict rolling 2,048-row attention window.

The shard stores no microbatch dimension. `--batch-size` is the number of
independent 4,096-origin blocks collated by the loader and may be tuned without
rebuilding shards. The production default is 16 blocks per microbatch with two
microbatches per optimizer update. Sixteen worker-owned mmap streams retain
shard-local access, eight in-flight batches per worker, and a shared bounded
RAM cache sized in loader blocks. One pinned device batch transfers on a
dedicated CUDA stream while the current batch computes. The terminal reports
actual cache fill and per-update GPU duty so starvation is visible. Future target support
never enters `batch.views`; eager horizon targets and masks are read directly
from the shard, so the device path performs no target materialization.

Each worker owns a deterministic shuffled sequence of ticker-month mmap shards
and persists one cursor per worker-owned stream. Cursors advance only after a
successful optimizer update.
Ctrl+C discards an incomplete accumulation group and replays it; resume
deterministically rehydrates each bounded raw cache from its committed cursor
rather than serializing gigabytes of bar tensors. A coverage-plan hash and the
loader-stream contract bind the population, dates, coverage mode, exact
session/block/origin totals, block shape, epochs, ticker sharding, and worker
count. Cache depth, retry budget, and per-worker prefetch depth may be tuned on
resume; worker count may not, because it changes ticker ownership.

Validation is a fixed eight-ticker, eight-week panel spanning liquid,
high-volatility, sector, event-driven, and illiquid names in 2026. Those
identities are excluded from training. Each slice contributes four fixed
stratified blocks. The full panel is materialized once into a bounded host-RAM
cache on background validation workers while training begins, then the same
prepared batches are reused for every validation. Validation uses the training
batch shape and CUDA transfer path, with workers capped at the eight held-out
tickers. Validation runs 100 times per coverage epoch: an early
checkpoint-sized health check, evenly spaced intermediate checks, and one at
completion. It reports loss,
per-horizon median MAE, return-sign accuracy, binary Brier score, quantile
coverage, and rare-condition average precision. At an intermediate validation
boundary, training prefetch is stopped first; validation evaluates the already
prepared host batches, then training restarts from consumed durable cursors.
Unconsumed training blocks replay safely rather than being marked complete.

Training refuses to start unless every requested ticker-month has a compatible
contract-v2 complete or explicitly covered-empty sidecar and every complete
sidecar has its tensor file. ClickHouse is not contacted by the offline training
path. Defaults are a 384-wide eight-layer decoder, BF16,
and six horizons from 5 seconds through 1 hour. `--max-samples 0` means the
complete coverage epoch; a positive value is an operator safety or diagnostic
cap and does not shorten the full-epoch learning-rate curve.

AdamW uses a `3e-4` peak learning rate and `0.1` weight decay. A shared MLOps
sample-clock scheduler starts at `3e-5`, linearly warms over the first 1% of the
resolved run population (about 75.6 million origins for the current epoch), then
performs one monotonic cosine decay to `3e-5` at the coverage-plan ceiling.
Explicit `--warmup-samples` overrides the fractional default. Scheduler,
optimizer, scaler, RNG, plan, logical data cursors, validation schedule,
checkpoint-manager state, and W&B run ID are part of every resumable checkpoint.
Resume continues the same W&B run; `--wandb-mode disabled` is an explicit
logging reset. Raw caches are deterministically rehydrated from the committed
cursors rather than copied into checkpoints.
Best-model selection uses fixed-panel validation loss. Training-loss minima are
not checkpointed because ticker/session composition changes over the epoch;
durable latest and archive checkpoints remain sample-clocked and Ctrl+C forces
a final latest checkpoint. An unhandled loader or training failure first stops
prefetch and forces a checkpoint at the last optimizer-committed global cursor;
any partially prepared block replays. Loader concurrency and ClickHouse transport settings
may change when resuming, while model, sampling, and causal data contracts must
still match exactly.

Interactive Rich output runs in the terminal alternate screen. Its primary bar
is exact epoch-origin coverage; the secondary bar is the latest durably trained
ticker-month and block offset. The stable dashboard also shows smoothed speed,
elapsed time, ETA and expected finish, scheduler phase, learning rate, loss,
validation, GPU duty, loader wait, RAM cache, checkpoint, and recent lifecycle
messages. Calendar context availability is reported separately from calendar
autoregressive event rate: calendar AR metrics are not emitted because
1D/1W/1MO are context-only; a zero calendar AR loss in older checkpoints was
expected unless a coarse
bar actually becomes available inside the current origin interval. Redirected
output remains plain text without cursor control.

Rare condition positives receive a configurable 32x BCE positive weight; the
four ordinary market-family availability labels remain unweighted. Preflight
also gates each condition channel: a channel with no positive evidence in the
certified training range is loss-ineligible and recorded as inactive in the
manifest and checkpoint config instead of being learned as always negative.

Profile the complete Arrow-to-optimizer path before the first full run:

```powershell
python -B -m research.bar_gpt.v1.run_profile_train
```

The candidate sweep measures loader wait, GPU time, origins/second, encoded
tokens/second, and peak device memory. OOM candidates fail independently; only
candidates at or below 90% reserved memory are eligible. `torch.compile` remains an
explicit opt-in candidate because Windows compilation can stall before the first measured
update; it is not part of the bounded default sweep. The current default uses
twelve single-thread ClickHouse workers instead of increasing server threads
per query. The bounded follow-up sweep compares that baseline with sixteen and
twenty-four workers on the complete training ticker universe.
Twelve measured updates cross
the initial worker/unit buffer so results include
ticker-month turnover rather than only warm-queue throughput. Promote the selected profile
into launcher defaults only after measuring it on the training workstation.

### Offline training-ready tensor shards

The production-scale loader can be decoupled from ClickHouse with a separate,
restart-safe offline compiler:

```powershell
python -B -m research.bar_gpt.v1.run_build_offline_shards

python -B -m research.bar_gpt.v1.run_build_offline_shards --execute
```

The first command is a read-only plan. The execute form balances whole tickers
by their planned block counts across bounded logical worker slots and writes
immutable ticker-month shards beneath
`D:\TradingML\runtimes\bar_gpt\v1\offline_shards_v2`. Both the requested
one-second authority and daily-session authority now begin in 2019. The
compiler fails preflight until those sources are continuously certified and
never fabricates unavailable intraday sessions or calendar history.

Each ticker runs in a fresh spawned process, so completing a ticker releases
PyTorch, Arrow, Polars, and Windows allocator arenas before that logical worker
slot advances to its next assigned ticker. ClickHouse Arrow-response concurrency
is bounded globally across all processes; zero for
`--clickhouse-max-concurrent-pages` selects the worker-aware bound. The measured
seven-day query and four-page prefetch remain the defaults because the laptop
client benchmark found them faster than the tested two- and three-day variants.
The bounded causal history keeps one contiguous cache authority instead of both
session chunks and their concatenated copy, and completed-ticker state is
discarded because ticker months are processed contiguously.
Within each ticker process, ClickHouse iteration runs in the foreground while one
bounded background slot compiles completed sessions. At most two session
compilations are pending, so source I/O and tensor projection overlap without
retaining an entire raw ticker-month or creating an unbounded memory queue.
Inside a completed session, blocks with the same tensor geometry are compiled
as one bounded batch: stationary projections, next-bar targets, physical
horizon prefixes, and horizon pooling all vectorize across the block dimension.
Tensor `searchsorted` replaces Python timestamp dictionaries. Pooling retains
an independent time axis per block, and exact single-block equivalence is a
regression gate, so batching cannot cross a causal or split boundary.
The final local write remains worker-owned: measured pilot writes plus SHA-256
certification took about 2.3--2.5 seconds for each 1.13 GiB shard, so sending
those payloads through a separate process writer would add IPC serialization
and memory pressure to a stage that accounted for less than one percent of the
observed wall time.

Each spawned process also receives a bounded PyTorch CPU-thread budget. Zero is
the automatic setting: it divides logical CPUs across the requested active
workers, capped at eight threads through 40 workers and six threads above 40.
Override it only from measured evidence with `--cpu-threads-per-worker N`.
This prevents every worker from independently creating a workstation-sized CPU
thread pool while leaving capacity for ClickHouse and the operating system.

Every executing compiler invocation creates a unique diagnostic directory at
`offline_shards_v2\manifest\build_runs\<run-id>`. Its parent-owned
`events.jsonl` records the resolved plan, worker PID/ticker launches, stages,
bounded progress, certifications, complete caught tracebacks, process exit
codes, last known work, and final catalog. `summary.json` records the final
status and exit code, while `workers\worker-<slot>-<ticker>-fatal.log` is wired
to Python `faulthandler` inside that spawned process. An abrupt native exit may
not produce a Python traceback, but the parent still records its nonzero exit
code, PID, ticker, last stage and fatal-log path immediately and counts it as a
failure in both Rich and text output. These operational files do not enter the
v2 storage hash and do not change shard payloads or ticker/year/month layout.

Each month stores every session-level 1s, intraday-rollup, and calendar tensor
once. Block records retain only slices, exact causal prefix corrections,
autoregressive masks, origin-specific physical-horizon targets, and metadata.
This reproduces the online collate contract without repeating each 720-bar
context. Shards use uncompressed PyTorch tensor containers so `torch.load` can
memory-map their storage. The runtime reader performs only mmap slicing,
padding into reusable pinned batches, and asynchronous CUDA handoff.

The v2 storage identity contains only settings that can alter one ticker-month
tensor payload. GPU batch size, loader workers, pinning, prefetch depth,
requested tickers, requested date range, validation ownership, query tuning,
and progress/concurrency controls are excluded. A stable ticker-month hash
replaces range-dependent unit numbering inside each shard. Consequently the
same certified shard can be collated into any loader-time batch size and two
disjoint build commands can safely accumulate compatible months in one root.
The loader stream contract remains part of the v2 identity to match the active
2019--2020 build and its certified sidecars.
`origin_bars_1s` remains storage geometry—the number of sequential origins in
one independently addressable block—not the number of blocks collated into a
training batch.

The current bounded eager build targets both calendar years 2019 and 2020 for
training and January through July 2026 for chronological validation. The two
commands intentionally share one root and are independently resumable:

```powershell
python -B -m research.bar_gpt.v1.run_build_offline_shards --execute --selection train --start-date 2019-01-01 --end-date 2021-01-01 --workers 32
python -B -m research.bar_gpt.v1.run_build_offline_shards --execute --selection validation --start-date 2026-01-01 --end-date 2026-08-01 --workers 8
```

Completion advances only after the shard is atomically renamed, SHA-256
certified, and accompanied by its sidecar manifest. Rerunning the same command
skips compatible certified or explicitly covered-empty ticker-months. A changed
feature, target, identity, split, table, range, or loader configuration changes
the config hash and therefore cannot silently reuse an incompatible cache.
`--max-shards N` provides a representative bounded pilot, and `--force-rebuild`
is the explicit replacement path. `--skip-hash` is diagnostic only and produces
uncertified shards that are intentionally not resume-skipped.

Execute-time coverage planning derives exact block totals from the certified
one-second sessions before workers start. Interactive output gives every worker
its own compiled-block progress bar, plus assigned shard count, active
ticker-month/session, fetched blocks, compiled sessions, and stage. The primary
progress bar, rate, and ETA are based on compiled blocks across all workers;
atomically certified ticker-month shards remain a separate durability counter.
The display also shows written GiB, origins, failures, output, and resume
semantics. New sidecars record
preparation wall time, compilation CPU time, write/hash time, and total unit wall
time so subsequent tuning is measurement-based.
Redirected output is plain text. Ctrl+C lets in-flight atomic writes finish,
stops workers, rebuilds the certified catalog, and returns exit code 130.

Audit the actual fetched feature and target contract before training:

```powershell
python -B -m research.bar_gpt.v1.run_audit_contract
```

The audit reports near-constant and zero feature channels, strongest absolute
correlations, target valid fractions, and retained condition blocks. Profiler
and audit evidence stays under `D:\TradingML\runtimes\bar_gpt\v1`.

The objective combines dense next-bar Huber and availability losses, direct
multi-horizon pinball loss, horizon availability BCE, and stop-gradient
next-state cosine prediction. A separate frozen ridge-probe job evaluates
whether embeddings retain held-out return information:

```powershell
python -B -m research.bar_gpt.v1.run_linear_probe `
  --checkpoint D:\TradingML\runtimes\bar_gpt\v1\train\<run>\checkpoints\checkpoint_best_val.pt
```

Checkpoints bind the exact model and data contract. Resume rejects a changed
contract. Runs own one directory under
`D:\TradingML\runtimes\bar_gpt\v1\train` containing manifests, metrics,
checkpoints, W&B files, and the model card.

After accepting a checkpoint, export causal embeddings with:

```powershell
python -B -m research.bar_gpt.v1.run_export_embeddings `
  --checkpoint D:\TradingML\runtimes\bar_gpt\v1\train\<run>\checkpoints\checkpoint_best_val.pt
```

`BarGPTEncoder` restores the checkpoint contract and returns embeddings,
validity, origin timestamps, ticker identities, and dates.
`PackedBarEmbeddingAdapter` projects them into the packed model modality width
while preserving the point-in-time validity mask. The packed model is not
coupled to an unaccepted BarGPT checkpoint.
