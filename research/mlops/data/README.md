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
- `PolarsTickerBlockBatchProvider`: bounded in-memory ticker block strategy. It
  uses Polars for sorting when available and emits the same batch contract.

Additional providers can be added without changing model code:

- ClickHouse block provider
- embedding-cache provider
- live qmd/market-ai provider
- news/SEC/fundamental replay providers

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

Run the smoke test:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\data\test_smoke.py
```

