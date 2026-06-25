# ML Ops Data Package

`research.mlops.data` is the shared data-preparation package for research,
training, and live serving. Model versions should consume its batch contracts
instead of implementing one-off loaders.

## Responsibilities

- define stable data contracts for market, news, SEC, fundamentals, and global context
- convert compact market events into 128-event chunks
- batch chunks for encoders
- maintain per-ticker embedding queues
- build multimodal temporal samples and batches
- attach training labels without leaking future data into features
- provide provider strategies that can be benchmarked and swapped
- profile each data-preparation stage

## Stable Contracts

- `CompactEvent`: one compact quote/trade event from live or historical sources
- `EventChunk`: one market-structure encoder input (`header_uint8`, `events_uint8`)
- `EncoderBatch`: model-ready encoder batch
- `EmbeddingRecord`: one embedding with ticker/time/source metadata
- `MultiModalTemporalSample`: one ticker-origin sample with market/news/SEC/fundamental contexts
- `MultiModalTemporalBatch`: tensor batch consumed by temporal models

## Provider Strategies

- `StreamingReplayBatchProvider`: production-compatible replay. It processes
  events in order through rolling state and should be the correctness baseline.
- `RollingMarketSampleEngine`: production-aligned event-queue strategy. It keeps
  one continuous queue per ticker, appends historical ClickHouse day blocks or
  live qmd events, creates short plus sparse-long 128-event chunk indices, and
  then materializes either raw compact chunks for training or cached embeddings
  for production.
- `PolarsTickerBlockBatchProvider`: bounded in-memory ticker block strategy. It
  uses Polars for sorting when available and emits the same batch contract.
- `ClickHouseTickerBlockBatchProvider`: chronological multi-ticker block
  strategy for training-data experiments. It selects tickers without replacement
  inside each ticker epoch, queries contiguous `(ticker, ordinal)` ranges, builds
  128-event compact chunks, and derives future time-bar labels from the fetched
  block.

Additional providers can be added without changing model code:

- embedding-cache provider
- live qmd/market-ai provider
- news/SEC/fundamental replay providers

## Rolling Training/Production Loader

The rolling loader is the main path for temporal-model data because it mirrors
production. Historical training and live serving both append ordered events into
one queue per ticker. The same sample-index logic is then used in both modes:

1. Choose one origin event for one ticker.
2. Build 128-event chunk windows ending at prior origins.
3. Attach as-of-only context that is visible at the origin timestamp.
4. Attach labels that are strictly after the origin timestamp.

Training materialization emits raw compact chunks so the market encoder can be
trained or fine-tuned. Production materialization uses the same sample indices
but gathers cached market-encoder embeddings instead of re-encoding windows.

The carryover rule is explicit:

```text
carryover_events = max_context_lag + events_per_chunk - 1
```

With the default farthest lag of `1850`, every ticker queue keeps at least
`1977` prior events across day boundaries. This prevents the first samples of a
new day from losing long-context chunks.

## Rolling Batch Contract

The training batch is represented by `RollingTrainingBatch`. The production
batch is represented by `RollingProductionBatch`. Both are keyed by the same
`RollingSampleIndex` list.

Default notation:

```text
B = batch size
C = context chunks = 27 by default
D = market encoder embedding dimension
```

Complete default training-batch shape summary:

| Group | Key Pattern | Default Shape |
| --- | --- | --- |
| sample identity | `ticker`, `origin_ordinal`, `origin_timestamp_us` | `[B]` |
| origin time | `time_features[*]` | `[B]` |
| market chunks | `headers_uint8` | `[B, 27, 14]` |
| market chunks | `events_uint8` | `[B, 27, 128, 16]` |
| market chunk metadata | `context_mask`, `chunk_origin_*` | `[B, 27]` |
| ticker macro bars/session state | `macro_features[*]` | `[B]` |
| global market bars | `global_features[*]` | `[B]` |
| ticker news tokens | `text_inputs["news"]["input_ids"]` | `[B, 32, 2, 1024]` |
| market news tokens | `text_inputs["market_news"]["input_ids"]` | `[B, 64, 2, 1024]` |
| SEC text tokens | `text_inputs["sec_filings"]["input_ids"]` | `[B, 16, 8, 1024]` |
| XBRL fundamentals | `xbrl_inputs[*]` | `[B, 64]` |
| future labels | `labels[*]` | usually `[B]` |

### Sample Identity

Each row in the batch is one ticker at one selected origin event.

| Field | Shape | Type | Meaning |
| --- | --- | --- | --- |
| `ticker` | `[B]` | `object` | Market symbol for each sample, for example `AAPL`. |
| `origin_ordinal` | `[B]` | `int64` | Event-table ordinal of the chosen origin event. |
| `origin_timestamp_us` | `[B]` | `int64` | The only absolute timestamp exposed by the batch contract. It is the SIP timestamp of the chosen origin event in UTC microseconds. |

All other timestamps in model-facing tensors are relative to
`origin_timestamp_us`. Raw absolute source timestamps are kept only in
`external_context` for audits and debugging.

Example:

```text
ticker = "AAPL"
origin_ordinal = 123456789
origin_timestamp_us = 1767216600123456
```

If an SEC filing has `timestamp_us = 1767130200000000`, the model receives:

```text
timestamp_delta_us = -86400123456
age_us = 86400123456
age_seconds_log1p = log1p(86400.123456)
```

### Origin Time Features

`time_features` converts the one absolute origin timestamp into cyclic numeric
features. These are safe because they are derived only from the origin event.

| Key | Shape | Type | Meaning |
| --- | --- | --- | --- |
| `utc_second_of_day_sin` | `[B]` | `float32` | Sine encoding of UTC time within the day. |
| `utc_second_of_day_cos` | `[B]` | `float32` | Cosine encoding of UTC time within the day. |
| `utc_day_of_week_sin` | `[B]` | `float32` | Sine encoding of UTC weekday. |
| `utc_day_of_week_cos` | `[B]` | `float32` | Cosine encoding of UTC weekday. |
| `utc_day_of_year_sin` | `[B]` | `float32` | Sine encoding of calendar day-of-year. |
| `utc_day_of_year_cos` | `[B]` | `float32` | Cosine encoding of calendar day-of-year. |
| `years_since_2000` | `[B]` | `float32` | Slow trend feature for calendar regime. |

### Market Event Context

The market context is a grid of compact quote/trade chunks.

| Field | Shape | Type | Meaning |
| --- | --- | --- | --- |
| `headers_uint8` | `[B, C, 14]` | `uint8` | One compact chunk header per context chunk. |
| `events_uint8` | `[B, C, 128, 16]` | `uint8` | 128 compact events per chunk. |
| `context_mask` | `[B, C]` | `bool` | True when the chunk is valid. |
| `chunk_origin_ordinal` | `[B, C]` | `int64` | Origin ordinal of each 128-event chunk. |
| `chunk_origin_timestamp_us` | `[B, C]` | `int64` | Absolute timestamp retained as metadata. Do not feed directly to models unless explicitly intended. |
| `chunk_origin_delta_us` | `[B, C]` | `int64` | `chunk_origin_timestamp_us - origin_timestamp_us`; usually `<= 0`. |
| `chunk_age_seconds_log1p` | `[B, C]` | `float32` | `log1p(max(0, origin - chunk_origin) / 1e6)`. |

`C = len(context_lags)`. With defaults, `C = 27`:

- 16 dense recent chunks
- 11 sparse long-history chunks at lags such as `32`, `48`, `72`, ..., `1850`

Example for one sample:

```text
context_lags = (0, 1, 2, 3, ..., 15, 32, 48, ..., 1850)
headers_uint8[0, 0] = header for the most recent 128 events ending at origin
events_uint8[0, 0] = those 128 compact quote/trade rows
chunk_origin_delta_us[0, 0] = 0
chunk_origin_delta_us[0, 1] < 0
```

### Production Market Embeddings

`RollingProductionBatch` replaces raw chunk bytes with cached encoder
embeddings:

| Field | Shape | Type | Meaning |
| --- | --- | --- | --- |
| `market_embeddings` | `[B, C, D]` | `float32` | Cached market-encoder output for each chunk. |
| `market_mask` | `[B, C]` | `bool` | True when an embedding exists for that context slot. |

The sample identity, time features, macro/global features, text inputs, XBRL
inputs, and raw audit context are the same conceptually as training.

## As-Of Feature Groups

All feature groups below are as-of the sample origin. They must not use rows or
bars whose event time is after `origin_timestamp_us`.

### Macro Features

`macro_features` describes the sample ticker itself. It has two sources:

1. Completed historical bars from `macro_bars_by_time_symbol`.
2. Current-session prefix features computed from the in-memory event queue up
   to the origin.

Completed-bar keys follow this pattern:

```text
{timeframe}_{field}
```

Default timeframes are `1d`, `1w`, `1mo`, and `1y`. Bar fields include:

| Field | Meaning |
| --- | --- |
| `open` | Open price of the latest completed/as-of bar. |
| `high` | High price of the latest completed/as-of bar. |
| `low` | Low price of the latest completed/as-of bar. |
| `close` | Close price of the latest completed/as-of bar. |
| `volume` | Trade volume. |
| `dollar_volume` | Dollar volume when available. |
| `trade_count` | Number of trades in the bar. |
| `quote_count` | Number of quote events in the bar. |
| `vwap` | Volume-weighted average price. |

Example macro keys for `AAPL`:

```text
1d_open
1d_high
1d_low
1d_close
1d_volume
1w_close
1mo_vwap
1y_trade_count
```

Current-session prefix keys are calculated directly from events before or at the
origin. They describe the ticker's state so far in the current session:

| Example Key | Meaning |
| --- | --- |
| `session_bid_price` | Latest bid price as of the origin. |
| `session_ask_price` | Latest ask price as of the origin. |
| `session_mid_price` | Latest midpoint as of the origin. |
| `session_spread` | Latest ask minus bid. |
| `session_bid_size` | Latest bid size. |
| `session_ask_size` | Latest ask size. |
| `session_quote_count_so_far` | Quote events observed so far. |
| `session_last_trade_price` | Latest trade price as of the origin. |
| `session_last_trade_size` | Latest trade size as of the origin. |
| `session_trade_high_so_far` | Highest trade price from session start through origin. |
| `session_trade_low_so_far` | Lowest trade price from session start through origin. |
| `session_trade_volume_so_far` | Trade volume from session start through origin. |
| `session_trade_count_so_far` | Trade count from session start through origin. |
| `session_trade_vwap_so_far` | Session VWAP through origin. |

The exact key set is generated by the current implementation. The important
rule is that every value is prefix-only and therefore no-lookahead.

### Global Features

`global_features` provides broad market context using the same as-of bar fields
as macro features, but for configured market symbols. Defaults are:

```text
SPY, QQQ, IWM, DIA
```

Keys are prefixed by symbol:

```text
SPY_1d_close
SPY_1d_volume
QQQ_1w_high
IWM_1mo_vwap
DIA_1y_trade_count
```

Example interpretation:

```text
SPY_1d_close = latest SPY daily close at or before the sample origin
QQQ_1w_volume = latest QQQ weekly volume at or before the sample origin
```

These features let the downstream model see market regime without relying on
future bars.

### Ticker News Inputs

Ticker news is read from `market_sip_compact.news_text_tokens`. It is built from
`q_live.benzinga_news_ticker_v1` joined to
`q_live.benzinga_news_normalized_v1`. The source timestamp is
`published_at_utc`.

The tensor group is `text_inputs["news"]`.

| Field | Shape | Type | Meaning |
| --- | --- | --- | --- |
| `input_ids` | `[B, 32, 2, 1024]` | `int32` | Qwen tokenizer ids for up to 32 ticker-related articles; each article has up to 2 chunks. |
| `attention_mask` | `[B, 32, 2, 1024]` | `uint8` | 1 for real tokens, 0 for padding. |
| `item_mask` | `[B, 32]` | `bool` | True when the article slot is present. |
| `chunk_mask` | `[B, 32, 2]` | `bool` | True when the article chunk exists. |
| `timestamp_delta_us` | `[B, 32]` | `int64` | Article timestamp minus origin timestamp. |
| `age_us` | `[B, 32]` | `int64` | `max(0, origin - article_timestamp)`. |
| `age_seconds_log1p` | `[B, 32]` | `float32` | Log-scaled news age. |

Example:

```text
text_inputs["news"]["item_mask"][7, 0] = True
text_inputs["news"]["timestamp_delta_us"][7, 0] = -4_200_000
```

This means sample 7 has a ticker-related article 4.2 seconds before the origin.
The corresponding token row is:

```text
input_ids[7, 0, 0, :]       = first 1024-token chunk for that article
attention_mask[7, 0, 0, :]  = 1 for real tokens, 0 for padding
chunk_mask[7, 0, 0]         = True
```

### Market News Inputs

Market news uses the same token table as ticker news but does not filter by
ticker. It selects the latest distinct article sources as-of the origin and
stores them under the synthetic ticker `__MARKET__`.

The tensor group is `text_inputs["market_news"]`.

| Field | Shape | Type | Meaning |
| --- | --- | --- | --- |
| `input_ids` | `[B, 64, 2, 1024]` | `int32` | Qwen tokenizer ids for up to 64 market-wide articles. |
| `attention_mask` | `[B, 64, 2, 1024]` | `uint8` | 1 for real tokens, 0 for padding. |
| `item_mask` | `[B, 64]` | `bool` | True when the market-news slot is present. |
| `chunk_mask` | `[B, 64, 2]` | `bool` | True when the article chunk exists. |
| `timestamp_delta_us` | `[B, 64]` | `int64` | Article timestamp minus origin timestamp. |
| `age_us` | `[B, 64]` | `int64` | `max(0, origin - article_timestamp)`. |
| `age_seconds_log1p` | `[B, 64]` | `float32` | Log-scaled market-news age. |

### SEC Filing Text Inputs

SEC filing text is read from
`market_sip_compact.sec_filing_text_tokens`. It is built from
`market_sip_compact.sec_filing_text_context`, which maps SEC filings to market
tickers and uses `sec_filing_v2.accepted_at_utc` as the no-lookahead timestamp.

The tensor group is `text_inputs["sec_filings"]`.

| Field | Shape | Type | Meaning |
| --- | --- | --- | --- |
| `input_ids` | `[B, 16, 8, 1024]` | `int32` | Qwen tokenizer ids for up to 16 filing text rows; each row has up to 8 chunks. |
| `attention_mask` | `[B, 16, 8, 1024]` | `uint8` | 1 for real tokens, 0 for padding. |
| `item_mask` | `[B, 16]` | `bool` | True when the filing-text slot is present. |
| `chunk_mask` | `[B, 16, 8]` | `bool` | True when the text chunk exists. |
| `timestamp_delta_us` | `[B, 16]` | `int64` | Filing accepted timestamp minus origin timestamp. |
| `age_us` | `[B, 16]` | `int64` | `max(0, origin - accepted_timestamp)`. |
| `age_seconds_log1p` | `[B, 16]` | `float32` | Log-scaled filing age. |

Example filing text item:

```text
form_type = "10-Q"
accession_number = "0000320193-26-000050"
text_rank = 0
timestamp_delta_us = -12_345_600_000_000
```

The model-facing tensor receives token ids and relative age. The accession,
form, and source metadata remain available in `external_context` for audit.

### XBRL Fundamental Inputs

XBRL fundamentals are read from `market_sip_compact.sec_xbrl_context`. The
source table is pre-migrated for training so that the loader can fetch XBRL rows
with a simple ticker/time range. Rows include company facts and frame
observations mapped to a market ticker and an accepted timestamp.

The tensor group is `xbrl_inputs`.

`xbrl_max_items = 64` means up to 64 as-of XBRL rows per sample. It does not
mean XBRL is squeezed into 64 feature columns. Each XBRL attribute is its own
array with shape `[B, 64]`.

| Field | Shape | Type | Meaning |
| --- | --- | --- | --- |
| `mask` | `[B, 64]` | `bool` | True when this XBRL slot is present. |
| `timestamp_delta_us` | `[B, 64]` | `int64` | XBRL accepted timestamp minus origin timestamp. |
| `age_us` | `[B, 64]` | `int64` | `max(0, origin - accepted_timestamp)`. |
| `age_seconds_log1p` | `[B, 64]` | `float32` | Log-scaled filing/fact age. |
| `value` | `[B, 64]` | `float32` | Numeric XBRL value. |
| `fiscal_year` | `[B, 64]` | `int16` | Filing/fact fiscal year when available. |
| `age_days` | `[B, 64]` | `float32` | Age in days from accepted timestamp to origin. |
| `period_end_days` | `[B, 64]` | `int32` | Period-end date encoded as epoch day. |
| `taxonomy_id` | `[B, 64]` | `uint32` | Stable hash id for taxonomy, for example `us-gaap`. |
| `tag_id` | `[B, 64]` | `uint32` | Stable hash id for concept tag. |
| `unit_id` | `[B, 64]` | `uint32` | Stable hash id for unit, for example `USD` or `shares`. |
| `form_id` | `[B, 64]` | `uint32` | Stable hash id for form type, for example `10-Q`. |
| `row_kind_id` | `[B, 64]` | `uint8` | `1` for company fact, `2` for frame observation. |
| `calendar_period_id` | `[B, 64]` | `uint32` | Stable hash id for SEC frame period code. |
| `location_id` | `[B, 64]` | `uint32` | Stable hash id for location code when available. |
| `accepted_at_source_id` | `[B, 64]` | `uint32` | Stable hash id for accepted-timestamp source. |
| `mapping_confidence` | `[B, 64]` | `float32` | Confidence of the CIK/accession-to-market mapping. |

Example XBRL row before tensorization:

```text
ticker = "AAPL"
timestamp_us = 1767130200000000
xbrl_row_kind = "company_fact"
taxonomy = "us-gaap"
tag = "RevenueFromContractWithCustomerExcludingAssessedTax"
unit_code = "USD"
form_type = "10-Q"
fiscal_year = 2026
period_end_date = "2026-03-31"
value = 123456789000.0
mapping_confidence_score = 0.98
```

Example model-facing slot:

```text
xbrl_inputs["mask"][0, 0] = True
xbrl_inputs["value"][0, 0] = 123456789000.0
xbrl_inputs["row_kind_id"][0, 0] = 1
xbrl_inputs["timestamp_delta_us"][0, 0] = timestamp_us - origin_timestamp_us
```

The raw tag names and accessions are intentionally not dense strings in the
model-facing tensor. They are converted to stable ids for training speed and
kept in `external_context` for audit/debugging.

### Raw External Context

`external_context` keeps the raw as-of rows returned by context stores. It is
not the primary model input. Its purpose is:

- audit no-lookahead behavior
- debug tokenization or mapping issues
- trace a prediction back to source article/filing/fact ids

It may contain absolute source timestamps, article ids, SEC accessions, concept
tags, and other source metadata.

## Labels

Future labels are separate from features. They are never included in any feature
group.

### Macro Future Labels

Macro future labels come from the first future bar whose `bar_start` is after
the sample origin for each configured `label_timeframes` entry. Default
timeframes are `1d`, `1w`, `1mo`, and `1y`.

Keys use the prefix `future_`:

```text
future_1d_open
future_1d_high
future_1d_low
future_1d_close
future_1d_volume
future_1w_close
future_1mo_vwap
future_1y_trade_count
```

### Intraday Future Labels

Intraday labels are computed from the current in-memory event queue after the
origin. Default horizons are:

```text
100ms, 250ms, 500ms, 750ms, 1s, 5s, 10s, 30s, 60s, 120s,
180s, 300s, 600s, 1200s, 1800s, 3600s, 7200s, 3h, 4h, 5h
```

Keys use the prefix `future_intraday_bar_` and include:

```text
has_trade, open, high, low, close, volume, trade_count
```

Example:

```text
future_intraday_bar_1s_high
future_intraday_bar_1s_low
future_intraday_bar_300s_volume
future_intraday_bar_3600s_trade_count
```

### No-Lookahead Boundary

Features are selected with `timestamp <= origin_timestamp_us`. Labels are
selected from bars/events after the origin. For daily, weekly, monthly, and
yearly labels, the loader fetches enough lookahead bars from the same bar table
but keeps them only in `labels`.

## Profiling

Every provider can attach a `DataPrepProfile` to each batch. Metrics include:

- source rows read
- chunks created
- encoder batches created
- samples created
- labels created
- output batches created
- per-stage timings
- samples/sec and batches/sec

Run a synthetic benchmark:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\data\run_benchmark_provider.py --batches 4
```

Profile the chronological ticker-block strategy without a database:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\data\run_profile_ticker_block_provider.py --synthetic --synthetic-tickers 16 --ticker-group-size 4 --events-per-ticker-block 20000 --sample-stride-events 16 --batches 4
```

Workstation runtime equivalent:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\research\mlops\data\run_profile_ticker_block_provider.py --synthetic --synthetic-tickers 16 --ticker-group-size 4 --events-per-ticker-block 20000 --sample-stride-events 16 --batches 4
```

Profile the production-aligned rolling provider locally without ClickHouse:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\data\run_profile_rolling_provider.py --synthetic --synthetic-tickers 16 --synthetic-events 8000 --batch-size 1024 --materialize-batches 2 --profile-production-gather
```

Workstation runtime equivalent:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\research\mlops\data\run_profile_rolling_provider.py --synthetic --synthetic-tickers 16 --synthetic-events 8000 --batch-size 1024 --materialize-batches 2 --profile-production-gather
```

Profile against ClickHouse for a real trading day:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\research\mlops\data\run_profile_rolling_provider.py --database market_sip_compact --events-table events --macro-bars-table macro_bars_by_time_symbol --index-table train_2019_to_2025 --event-date 2025-01-02 --ticker-limit 64 --batch-size 4096 --materialize-batches 2 --sample-stride-events 1 --max-threads 8 --max-memory-usage 80G --profile-production-gather --report-path D:\market-data\prepared\data_provider_profiles\rolling_provider_profile.jsonl
```

Pass `--skip-q-live-contexts` when you want to profile only market events and
macro bars without reading q_live news/SEC/XBRL tables.

Profile against ClickHouse:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\data\run_profile_ticker_block_provider.py --database market_sip_compact --events-table events --index-table train_2019_to_2025 --ticker-group-size 64 --events-per-ticker-block 250000 --future-tail-events 4096 --sample-stride-events 16 --max-samples-per-ticker 2048 --batches 4 --max-threads 8 --max-memory-usage 80G
```

Compare ordinal-range fetches with date-block fetches for the same ticker
groups:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\data\run_profile_ticker_block_provider.py --mode compare --date-block-date 2025-01-02 --database market_sip_compact --events-table events --index-table train_2019_to_2025 --ticker-group-size 64 --events-per-ticker-block 250000 --future-tail-events 4096 --sample-stride-events 16 --max-samples-per-ticker 2048 --batches 4 --max-threads 8 --max-memory-usage 80G --report-path D:\market-data\prepared\data_provider_profiles\ticker_block_compare.jsonl
```

For a controlled liquid-ticker comparison:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\data\run_profile_ticker_block_provider.py --mode compare --date-block-date 2025-01-02 --tickers AAPL,MSFT,NVDA,TSLA --database market_sip_compact --events-table events --index-table train_2019_to_2025 --ticker-group-size 4 --events-per-ticker-block 4096 --future-tail-events 512 --sample-stride-events 64 --max-samples-per-ticker 64 --batches 1 --max-threads 4 --max-memory-usage 20G --report-path D:\market-data\prepared\data_provider_profiles\ticker_block_compare_liquid.jsonl
```

Workstation runtime equivalent:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\research\mlops\data\run_profile_ticker_block_provider.py --database market_sip_compact --events-table events --index-table train_2019_to_2025 --ticker-group-size 64 --events-per-ticker-block 250000 --future-tail-events 4096 --sample-stride-events 16 --max-samples-per-ticker 2048 --batches 4 --max-threads 8 --max-memory-usage 80G
```

Workstation compare command:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\research\mlops\data\run_profile_ticker_block_provider.py --mode compare --date-block-date 2025-01-02 --database market_sip_compact --events-table events --index-table train_2019_to_2025 --ticker-group-size 64 --events-per-ticker-block 250000 --future-tail-events 4096 --sample-stride-events 16 --max-samples-per-ticker 2048 --batches 4 --max-threads 8 --max-memory-usage 80G --report-path D:\market-data\prepared\data_provider_profiles\ticker_block_compare.jsonl
```

The main tuning knobs are:

- `ticker_group_size`: how many tickers are fetched in one prep cycle.
- `events_per_ticker_block`: how many chronological origin events are advanced
  per selected ticker.
- `future_tail_events`: extra future events fetched for labels.
- `sample_stride_events`: spacing between generated origins inside each block.
- `max_samples_per_ticker`: cap to keep one liquid ticker from dominating one
  prepared pool.
- `--polars-assembly`: optional profiler path that materializes a single sorted
  Polars table for the fetched block. The default avoids this copy because the
  per-ticker array path is usually faster for training.
- `--mode ordinal|date|compare`: `ordinal` fetches cursor-controlled ordinal
  ranges, `date` fetches all rows for the selected tickers inside one
  `event_date`, and `compare` profiles both modes side by side.
- `--tickers`: optional comma-separated ticker override for controlled profiling
  or ticker-specific training experiments.
- `--report-path`: optional JSONL output for profiler results.

The modes answer different questions:

- `ordinal` is best for precise chronological cursor advancement and targeted
  ticker/range training.
- `date` is best when a fetched day is fully consumed. It is wasteful when
  `max_samples_per_ticker` is low because it still reads the whole date block.

Ticker scheduling is epoch-style without replacement: every active ticker is
selected once before the ticker list is reshuffled. Cursors are advanced only
after a batch is built successfully and can be persisted with `--state-path`.

Run the smoke test:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\data\test_smoke.py
```
