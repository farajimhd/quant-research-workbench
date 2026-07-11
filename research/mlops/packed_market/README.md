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

## Legacy Reader

`PackedMarketDataset` can still read a pre-existing packed block cache for debugging. It is not the default training path and should not be used for new packed-market experiments unless explicitly requested.
