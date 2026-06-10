# Compact Batch Provider Benchmark

This benchmark tests whether v4 `EventsChunk` batches can be built directly
from existing compact ClickHouse `quotes` and `trades` tables without
materializing a full unified event table.

## Process

For each requested sample:

1. Sample a ticker uniformly from a sampling-index table such as:

   ```text
   market_sip_compact.train_2019_to_2025
   ```

2. Sample a random origin timestamp inside that ticker's split range.
3. Query the latest `fetch_per_kind` quotes before the origin.
4. Query the latest `fetch_per_kind` trades before the origin.
5. Merge quote/trade rows locally by:

   ```text
   sip_timestamp, sequence_number, event_type
   ```

6. Keep the latest `events_per_chunk` unified events.
7. Encode the result into v4 tensors:

   ```text
   header_uint8: [B, 14]
   events_uint8: [B, 128, 16]
   ```

Rejected samples are retried up to `batch_size * max_sample_attempt_multiplier`.

## Important Limit

This benchmark samples origin timestamps uniformly inside a ticker's time range.
It does not yet sample true event ordinals uniformly. True ordinal-uniform
sampling requires either a stronger ClickHouse query strategy or an additional
ordinal helper index.

## Run

Small smoke test:

```powershell
python -m research.mlops.run_benchmark_compact_batch_provider --batch-size 16 --benchmark-batches 2 --workers 8
```

Workstation test:

```powershell
python D:\TradingML\codes\masked_event_model\v4\research\mlops\run_benchmark_compact_batch_provider.py --batch-size 256 --benchmark-batches 10 --workers 32
```

Key metrics:

```text
data/batch_build_seconds
data/fetch_wall_seconds
data/query_sum_seconds
data/encode_sum_seconds
data/accept_pct
throughput/accepted_samples_per_second
```

If throughput is not close to the training batch requirement, the next step is
to add an ordinal helper index or a more efficient grouped ClickHouse query,
not to create a full duplicated unified event table.
