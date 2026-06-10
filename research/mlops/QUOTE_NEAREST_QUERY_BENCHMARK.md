# Quote Nearest Query Benchmark

This benchmark isolates the ClickHouse read cost for quote-only event chunks.
It does not query trades, merge quote/trade streams, encode tensors, or run the
model.

For each sampled origin:

1. Sample a ticker from a sampling-index table such as:

   ```text
   market_sip_compact.train_2019_to_2025
   ```

2. Sample a random origin timestamp inside that ticker's split range.
3. Query the `N` closest prior quote events:

   ```sql
   PREWHERE ticker = <ticker>
     AND sip_timestamp_us <= <origin>
   WHERE sip_timestamp_us > 0
   ORDER BY sip_timestamp_us DESC, sequence_number DESC
   LIMIT <N>
   ```

4. Count the sample as accepted if ClickHouse returns `N` rows.

`ticker` is intentionally omitted from `ORDER BY` because it is already fixed
by the `WHERE` predicate. Keeping `ORDER BY ticker DESC` makes ClickHouse do
extra ordering work and is slower against tables ordered by:

```text
(ticker, sip_timestamp_us, sequence_number)
```

Use `--lookback-us` to test a bounded time range:

```sql
AND sip_timestamp_us >= <origin - lookback_us>
```

The benchmark uses `PREWHERE` for the primary lookup predicate so ClickHouse can
filter by `ticker` and timestamp before reading the wider quote payload columns.

This can be faster, but it may reject illiquid samples if fewer than `N` quote
events exist inside the lookback window. Leave it at `0` for exact nearest-N
queries without a lower time bound.

This measures the lower bound for a quote-only provider that can fill trade
fields as absent/zero later. It also shows how much overhead comes from adding
trade reads and local quote/trade merging.

## Run

Small comparison:

```powershell
python D:\TradingML\codes\masked_event_model\v4\research\mlops\run_benchmark_quote_nearest_query.py --batch-size 16 --benchmark-batches 2 --workers 8 --events-per-sample 128
```

Larger batch:

```powershell
python D:\TradingML\codes\masked_event_model\v4\research\mlops\run_benchmark_quote_nearest_query.py --batch-size 256 --benchmark-batches 10 --workers 32 --events-per-sample 128
```

Bounded lookback experiment:

```powershell
python D:\TradingML\codes\masked_event_model\v4\research\mlops\run_benchmark_quote_nearest_query.py --batch-size 256 --benchmark-batches 10 --workers 32 --events-per-sample 128 --lookback-us 86400000000
```

Key metrics:

```text
data/batch_build_seconds
data/fetch_wall_seconds
data/query_sum_seconds
data/query_seconds_p50
data/query_seconds_p95
data/query_requests
data/lookback_us
data/accept_pct
throughput/accepted_samples_per_second
```
