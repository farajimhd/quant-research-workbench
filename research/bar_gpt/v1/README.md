# BarGPT v1

BarGPT v1 is a causal multiscale bar representation model. It uses a shared
GPT-style decoder over continuous bar features, not discrete vocabulary tokens.
It exposes contextual embeddings, dense next-bar predictions, and direct
physical-horizon quantiles.

## Data authority

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
quote, and size geometry of the SIP contract. Training starts in 2020; available
2019 SIP daily sessions supply left context and earlier unavailable context is
masked.

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

Training uses incremental ArrowStream record batches. The durable work unit is
one canonical ticker over one calendar month. Months remain chronological for
partition locality; ticker order is deterministically shuffled inside each
month and units are then divided among workers. A unit fetches its one-second
range in ordered seven-day ClickHouse subranges and carries context continuously
across those subranges. This bounds server-side sort memory without changing
the ticker-month lifecycle or refetching model context. External sort spills at
1 GiB instead of allowing a wide Arrow query to approach its 8 GiB hard limit.
The unit produces every training block from resident session data and releases
it before advancing. Daily history is cached once per ticker per worker.

The prior completed session contributes up to 2,048 one-second context rows to
the first premarket origins. No overnight seconds are fabricated: timestamps
and `available_at_us` retain the real wall-clock boundary, while targets remain
inside the target session. Model time channels encode the actual New York
session-clock position from every bar timestamp, elapsed wall-clock ratio, and
an explicit sequence-boundary flag; they are not relative to the sampled window.

For simultaneous 1s and 5s inputs, the global origin clock moves one second and
the 5s encoder advances only after its bar closes:

```text
last coarse index where coarse.available_at_us <= fine.anchor_us
```

The coarse stream is encoded once and gathered causally for fine origins. The
default block has 2,048 input seconds, 512 origins, and a 3,600-second
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

Training uses `[2020-01-01, 2026-01-01)`. One coverage epoch is one
deterministic pass over 72 month partitions and the 90 non-validation tickers.
Each ticker-month is fetched once and deterministically reduced to at most 16
blocks while retaining session-phase, activity-regime, and rare-condition
coverage. Selected blocks are copied out of their full-session Arrow buffers
before the unit is released. The default ceiling is 6,480 units, 103,680
blocks, and 53,084,160 origin timestamps; unavailable ticker-months can produce
fewer blocks and are never fabricated.

One microbatch contains two 512-origin blocks. Four microbatches are accumulated
before one optimizer update, normally 4,096 origins. Bounded persistent loader
workers prepare the next CPU batch, and one device batch transfers on a
dedicated CUDA stream while the current batch computes. Future target support
never enters `batch.views`; it is consumed only by GPU target construction and
the loss, so later origins or horizons in one update do not create lookahead.

The durable resume cursor is `(worker, global ticker-month index, selected block
offset)`. It advances only after a successful optimizer update. Ctrl+C discards
an incomplete accumulation group and replays it; resume jumps over completed
units rather than reading and discarding them. A coverage-plan hash binds the
population, dates, ordering seed, block quota, origin count, and epochs.

Validation is a fixed eight-ticker, eight-week panel spanning liquid,
high-volatility, sector, event-driven, and illiquid names in 2026. Those
identities are excluded from training. Each slice contributes four fixed
stratified blocks, at most 16 batches are evaluated, and validation runs four
times per coverage epoch with the final run at completion. It reports loss,
per-horizon median MAE, return-sign accuracy, binary Brier score, quantile
coverage, and rare-condition average precision.

Training refuses to start unless canonical one-second, daily, exact condition,
and point-in-time alias coverage are certified and the q_live identity and
split authorities exist. Defaults are a 384-wide eight-layer decoder, BF16,
and six horizons from 5 seconds through 1 hour. `--max-samples 0` means the
complete coverage epoch; a positive value is an operator safety or diagnostic
cap and does not shorten the full-epoch learning-rate curve.

AdamW starts with a `3e-4` peak learning rate and `0.1` weight decay. A shared
MLOps sample-clock scheduler linearly warms from `3e-5` to `3e-4` over the first
1,048,576 origins, then performs one monotonic cosine decay to `3e-5` at the
coverage-plan ceiling. Scheduler, optimizer, scaler, RNG, plan, and logical data
cursors are part of every resumable checkpoint.
Rare condition positives receive a configurable 32x BCE positive weight; the
four ordinary market-family availability labels remain unweighted.

Profile the complete Arrow-to-optimizer path before the first full run:

```powershell
python -B -m research.bar_gpt.v1.run_profile_train
```

The candidate sweep measures loader wait, GPU time, origins/second, encoded
tokens/second, and peak device memory. OOM candidates fail independently; only
candidates at or below 90% reserved memory are eligible. `torch.compile` remains an
explicit opt-in candidate because Windows compilation can stall before the first measured
update; it is not part of the bounded default sweep. The default two workers keep
Arrow HTTP concurrency within Windows socket limits. Eight measured updates cross
the initial worker/unit buffer so results include
ticker-month turnover rather than only warm-queue throughput. Promote the selected profile
into launcher defaults only after measuring it on the training workstation.

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
