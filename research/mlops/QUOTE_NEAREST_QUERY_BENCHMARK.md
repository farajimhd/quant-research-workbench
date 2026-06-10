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
   WHERE ticker = <ticker>
     AND sip_timestamp_us <= <origin>
   ORDER BY ticker DESC, sip_timestamp_us DESC, sequence_number DESC
   LIMIT <N>
   ```

4. Count the sample as accepted if ClickHouse returns `N` rows.

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

Key metrics:

```text
data/batch_build_seconds
data/fetch_wall_seconds
data/query_sum_seconds
data/query_seconds_p50
data/query_seconds_p95
data/query_requests
data/accept_pct
throughput/accepted_samples_per_second
```
