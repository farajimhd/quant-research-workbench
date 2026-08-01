# BarGPT v1

BarGPT v1 is the first causal multiscale bar representation model. It uses a
shared GPT-style decoder over continuous bar features, not discrete vocabulary
tokens. The implementation exposes contextual embeddings, dense next-bar
predictions, and direct physical-horizon quantiles.

## Data authority

The intraday storage authority is one rich row per completed, active 1-second
bucket in `market_sip_compact.bar_gpt_1s_bars_v1`. The table is built directly
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
python -m research.bar_gpt.v1.run_build_1s --start-date 2026-07-24 --end-date 2026-07-25 --tickers AAPL
```

Execute that bounded build:

```powershell
python -m research.bar_gpt.v1.run_build_1s --execute --start-date 2026-07-24 --end-date 2026-07-25 --tickers AAPL
```

Execute all coverage advertised by `events_ticker_day_index`:

```powershell
python -m research.bar_gpt.v1.run_build_1s --execute
```

The all-coverage command is intentionally explicit because it is a large job.
The builder partitions work by market date and bounded ticker/event batches,
records completion only after each insert and manifest write, collapses safe
retry duplicates with `ReplacingMergeTree`, audits exact key uniqueness and
availability at monthly certification, and cancels the active query on Ctrl+C.

Runtime reports are written under:

```text
D:\TradingML\runtimes\bar_gpt\v1\build_1s
```

No runtime output is written into the repository.

Training reads use incremental `ArrowStream` record batches through
`loader.py`; the response is not materialized with `read_all()`. A worker owns
an ordered ticker/date range, retains only the current session plus the bounded
context/target halo, and yields the previous session as soon as its key changes.

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

`BarGPTV1` uses:

- RMSNorm and pre-normalized decoder blocks;
- grouped-query causal self-attention;
- rotary position embeddings;
- SwiGLU feed-forward blocks;
- one shared backbone for every timeframe plus learned timeframe embeddings;
- causal as-of fusion of coarse contextual states into fine origins;
- a next-bar head for dense autoregressive supervision;
- a low-rank horizon-conditioned quantile head.

`features.py` converts the stored sufficient statistics into causal stationary
channels: close returns, open gaps, nonnegative excursions, log activity,
VWAP deviation, size dispersion, spread basis points, microprice lean, queue
imbalance, and availability. Absolute price levels are not passed to the shared
backbone.

The returned `embeddings` tensor is the bar-modality representation intended
for later point-in-time integration with `packed_market_model/v2`. The existing
packed model v1 remains unchanged.

`targets.py` builds endpoint return, path excursions, realized volatility,
activity, spread, imbalance, and family-availability targets for every physical
horizon from the dense right-support tensor. It uses index gathers, prefix
differences, and GPU pooling; it does not construct or store per-origin input
windows.
