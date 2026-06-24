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
production:

1. Each ticker has one ordered event queue.
2. A new historical day or live stream append extends the queue.
3. The sample-index builder creates chunk windows from the same rule used in
   serving:
   - base chunk: 128 consecutive events ending at an origin event
   - recent context: dense chunk origins such as `0,1,2,...`
   - older context: sparse lags such as `32,48,72,...,1850`
4. Training materialization returns:
   - `headers_uint8`: `[batch, context_chunks, 14]`
   - `events_uint8`: `[batch, context_chunks, 128, 16]`
   - metadata arrays for ticker, origin ordinal, origin timestamp, and chunk origins
   - `macro_features`: as-of ticker bars plus current session prefix features
   - `global_features`: as-of bars for configured market symbols
   - `text_inputs`: Qwen-ready token tensors for news and SEC filing text
   - `xbrl_inputs`: tensorized SEC XBRL fact rows
   - `external_context`: raw as-of payloads retained for audit/debugging
   - `labels`: strict-future macro bars plus same-queue intraday future bars
5. Production materialization uses the same sample indices but gathers cached
   encoder embeddings instead of re-encoding windows.

The carryover rule is explicit:

```text
carryover_events = max_context_lag + events_per_chunk - 1
```

With the default farthest lag of `1850`, every ticker queue keeps at least
`1977` prior events across day boundaries. This prevents the first samples of a
new day from losing long-context chunks.

Macro/global context is loaded through `macro_bars_by_time_symbol` by default.
It is always as-of the sample origin timestamp.

Macro ticker features describe the sample ticker itself. For every configured
macro timeframe (`1d`, `1w`, `1mo`, `1y` by default), the provider returns the
latest bar at or before the origin:

- `open`, `high`, `low`, `close`
- `volume`, `dollar_volume`, `trade_count`, `quote_count`
- `vwap`

The same `macro_features` dictionary also includes current-session prefix
features computed from the in-memory event queue up to the origin with no
lookahead:

- latest quote state: bid, ask, spread, mid, bid/ask sizes, quote count so far
- latest trade state: last trade price/size
- session trade state so far: high, low, volume, trade count, vwap

Global features use the same as-of bar fields for configured market symbols
(`SPY`, `QQQ`, `IWM`, `DIA` by default). Keys are prefixed by symbol, for
example `SPY_1d_close` or `QQQ_1w_volume`. These are intended to give the
temporal model broad market state without leaking future bars.

The default multimodal context comes from concrete `q_live` tables:

- news: `benzinga_news_ticker_v1` joined to `benzinga_news_normalized_v1` by
  `canonical_news_id`, timestamped by `published_at_utc`
- SEC filing text: filings come from `sec_filing_v2`, bounded text snippets
  from `sec_filing_text_v2`, and the as-of timestamp is always
  `sec_filing_v2.accepted_at_utc`. The SEC gateway enriches this timestamp
  while processing each accession. Rows whose `accepted_at_utc` is null are
  excluded because they do not have a safe event time for no-lookahead
  training. The loader uses `id_sec_market_bridge_v1` only to attach a CIK or
  accession to a market ticker when the mapping is valid on the accepted date;
  the bridge is not the event-time source.
- XBRL fundamentals: numeric company facts come from
  `sec_xbrl_company_fact_v1` and frame observations from
  `sec_xbrl_frame_observation_v1`. Each XBRL row is joined back to
  `sec_filing_v2` by `(cik, accession_number)` and uses the related filing
  `accepted_at_utc` as its timestamp. The same `id_sec_market_bridge_v1`
  mapping rule attaches the row to a ticker.

Every row is normalized to `timestamp_us` and the materializer only returns
items with `timestamp_us <= sample_origin_timestamp_us`, so future text or
fundamental rows cannot leak into features. Extra sources can still be added
with `ExternalAsOfContextConfig`; its `timestamp_unit` supports microseconds,
nanoseconds, milliseconds, and seconds.

Text context is materialized into model-ready Qwen inputs:

- `text_inputs["news"]["input_ids"]`: `[batch, news_max_items, text_max_tokens]`
- `text_inputs["news"]["attention_mask"]`: same shape, `uint8`
- `text_inputs["news"]["item_mask"]`: `[batch, news_max_items]`
- `text_inputs["sec_filings"]["input_ids"]`: `[batch, sec_max_items, text_max_tokens]`
- `text_inputs["sec_filings"]["attention_mask"]`: same shape, `uint8`
- `text_inputs["sec_filings"]["item_mask"]`: `[batch, sec_max_items]`

The default tokenizer model is `Qwen/Qwen3-0.6B`. By default the loader uses
local tokenizer files if present and falls back to a deterministic hash
tokenizer for smoke tests. Set `strict_text_tokenizer=True` when a training run
must fail unless the real Qwen tokenizer is available.

XBRL context is materialized into fixed tensors:

- `xbrl_inputs["mask"]`: `[batch, xbrl_max_items]`
- `xbrl_inputs["timestamp_us"]`: `[batch, xbrl_max_items]`
- `xbrl_inputs["value"]`: `[batch, xbrl_max_items]`
- `xbrl_inputs["fiscal_year"]`, `age_days`, `period_end_days`
- stable categorical ids for `taxonomy`, `tag`, `unit_code`, and `form_type`
- `row_kind_id`, where `1` is a company fact and `2` is a frame observation
- stable categorical ids for `calendar_period_code` and `location_code`
- `accepted_at_source_id` so downstream audits can distinguish how the SEC
  gateway populated the accepted timestamp
- `mapping_confidence` from the CIK/accession-to-market mapping

Future labels are separate from features. They include:

- macro future bars from the first bar whose `bar_start` is after the sample
  origin for each configured `label_timeframes` entry
- intraday future bars computed from the current in-memory event queue for
  `100ms`, `250ms`, `500ms`, `750ms`, `1s`, `5s`, `10s`, `30s`, `60s`, `120s`,
  `180s`, `300s`, `600s`, `1200s`, `1800s`, `3600s`, `7200s`, `3h`, `4h`, and
  `5h`

Intraday label keys use the prefix `future_intraday_bar_` and include
`has_trade`, `open`, `high`, `low`, `close`, `volume`, and `trade_count`.
Macro label keys use the prefix `future_`. The default bar query uses a 40-day
macro lookback and a 400-day label lookahead so daily, weekly, monthly, and
yearly labels can be populated from the same bar table.

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
