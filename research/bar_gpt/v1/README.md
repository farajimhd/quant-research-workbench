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
`q_live.market_ticker_event_entity_v1`. The one-second query uses only the
source ticker valid for each date and emits the stable canonical model ticker.
This retains FB history for canonical META while excluding the unrelated
earlier security that used META.

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

Training uses incremental ArrowStream record batches. A worker owns an ordered
canonical-ticker shard and queries each point-in-time source-symbol interval.
The comparatively small daily range is fetched once for the worker's ticker
shard and partitioned in memory.

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
- separate trade, bid, ask, and paired-quote availability logits.

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

Training uses `[2020-01-01, 2026-01-01)` and validation uses 2026 onward. It
refuses to start unless canonical one-second, alias one-second, and daily
coverage are continuously certified and the q_live identity and split
authorities exist. Defaults are a
384-wide eight-layer decoder, BF16, six horizons from 5 seconds through 1 hour,
and 50 million training origins.

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
