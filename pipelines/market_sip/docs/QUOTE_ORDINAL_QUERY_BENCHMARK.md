# Quote Ordinal Query Benchmark

This benchmark estimates the benefit of a production ordinal index before
building it for the full SIP quote history.

It creates a small benchmark table:

```text
market_sip_compact.quotes_ordinal_benchmark
```

The table is ordered by:

```text
(ticker, ordinal)
```

`ordinal` is generated as:

```sql
row_number() OVER (
  PARTITION BY ticker
  ORDER BY sip_timestamp_us, sequence_number
)
```

The benchmark then samples an origin ordinal and queries exactly:

```sql
PREWHERE ticker = <ticker>
  AND ordinal >= <origin_ordinal - 127>
  AND ordinal <= <origin_ordinal>
ORDER BY ordinal ASC
LIMIT 128
```

This avoids the open-ended timestamp predicate:

```sql
sip_timestamp_us <= <origin>
```

and directly measures the lookup pattern we would want for training.

## Run

Build and benchmark a small one-day subset:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\run_benchmark_quote_ordinal_query.py --rebuild --tickers AAPL,MSFT,NVDA,TSLA,AMD,SPY,QQQ --start-date 2026-05-15 --end-date 2026-05-15 --batch-size 256 --benchmark-batches 10 --workers 32
```

Benchmark an already-built table without rebuilding:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\run_benchmark_quote_ordinal_query.py --no-build --batch-size 256 --benchmark-batches 10 --workers 32
```

Key metrics:

```text
data/batch_build_seconds
data/fetch_wall_seconds
data/query_sum_seconds
data/query_seconds_p50
data/query_seconds_p95
throughput/accepted_samples_per_second
```
