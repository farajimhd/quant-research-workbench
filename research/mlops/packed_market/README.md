# Packed Market Data Layer

This package provides the direct ClickHouse streaming data path for `packed_market_model`.

The active path is:

```text
ClickHouse events_YYYY + events_ticker_day_index
  -> ClickHouseTickerStreamDataset
  -> packed ticker blocks
  -> packed_market_model trainer
```

The old daily-index/materialized/offline cache builders are intentionally not used here.

## Main Loader

Use `ClickHouseTickerStreamDataset` from:

```python
from research.mlops.packed_market import ClickHouseTickerStreamConfig, ClickHouseTickerStreamDataset
```

The loader:

- reads ticker/month plans from `events_ticker_day_index`
- assigns ticker/month plans to concurrent ticker stream workers
- fetches each ticker by ordinal range from `events_YYYY`
- builds packed blocks in memory
- computes event-derived future labels with vectorized NumPy/Polars
- emits ready blocks to a bounded queue
- cancels active ClickHouse queries on stop

Each worker owns one ticker/month stream at a time and releases data that is no longer needed.

## Packed Block Contract

```text
events:           [T, F]
origin_positions: [M]
labels:           dict[label_name, [M]]
label_masks:      dict[label_name, [M]]
```

Future support rows may be fetched to compute labels, but they are not included in `events`.

## Profiling

Run:

```powershell
python -m research.packed_market_model.v1.run_profile_ticker_stream_loader `
  --months 2019-02 `
  --max-blocks 20 `
  --ticker-workers 24 `
  --ready-queue-blocks 8
```

For a small smoke:

```powershell
python -m research.packed_market_model.v1.run_profile_ticker_stream_loader `
  --months 2019-02 `
  --tickers AAPL `
  --max-blocks 2 `
  --ticker-workers 1
```

The profile logs:

- planner timing
- fetch/process timing per emitted block
- event rows and origins per block
- ready queue depth
- worker status
- optional model forward/backward timing

## Scanner Bar Benchmark

Before wiring scanner into training, measure the ClickHouse sidecar cost directly:

```powershell
python -m research.mlops.packed_market.run_benchmark_scanner_bars `
  --date 2019-02-01 `
  --start-et 09:45:00 `
  --end-et 10:15:00
```

The benchmark times:

- raw event count for the ET window
- direct trade / quote-bid / quote-ask bars for `1s,5s,15s,30s,1m`
- sidecar mode: materialize `1s` bars once into a run-scoped ClickHouse table
- aggregate `5s,15s,30s,1m` bars from the `1s` table
- optional scanner rank timing from the base trade bars

The default temporary table uses ClickHouse `Memory` engine and is dropped at
the end of the run. For a larger/full-day benchmark, increase `--end-et` and
consider `--materialize-engine MergeTree --keep-temp-table` only when you
explicitly want to inspect the generated table.

## Scanner Sidecar In Loader Profiles

The packed full-modality profile uses a ClickHouse sidecar for scanner state.
The sidecar:

- materializes run-scoped `1s` trade / quote-bid / quote-ask scanner rows into
  `market_sip_compact.packed_scanner_sidecar_bars`
- grows a same-day sidecar from 04:00 ET to 20:00 ET instead of deleting
  intra-day rows
- drops older source dates only when the loader moves to a later source date
- does a blocking `5s` warmup by default, then requests the remaining scanner
  window from the background sidecar builder
- advances background sidecar builds in `60s` chunks by default, and does not
  hold the sidecar manager lock while ClickHouse executes the insert query
- computes scanner ranks from the base trade bars and persisted same-day open
  references
- fetches only completed bars, where `bar_end_timestamp_us <= floor(origin_timestamp_us, 1s)`
- never fetches the current in-progress second
- keeps current-day rows on normal shutdown unless explicit cleanup is requested

Volume scanner ranks are split by price bucket:

```text
penny: trade_close < 1
small: 1 <= trade_close < 20
mid:   20 <= trade_close < 100
large: trade_close >= 100
```

The table also keeps the generic `top_volume_*` all-volume fields for backward
compatibility.

Loader fetches are bounded: by default the profile/loader reads only the top
`16` rows per scanner rank family plus the active ticker row. It does not pull
every ticker's scanner row into a block.

Run the full loader profile:

```powershell
python -m research.packed_market_model.v1.run_profile_full_workstation
```

## Legacy Reader

`PackedMarketDataset` can still read a pre-existing packed block cache for debugging. It is not the default training path and should not be used for new packed-market experiments unless explicitly requested.
