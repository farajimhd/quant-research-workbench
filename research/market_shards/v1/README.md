# ARTE premarket shards V1

Independent generator and causal batch loader. No V7 levels or float/shares
fundamentals are extracted. Existing optimizer versions and active runs are
unchanged. This is a versioned market-data interface, not a drop-in replacement
for V64's 834 scalar input IDs; consumers must explicitly adopt its window API.

The funnel reads previous regular-session closes first ($0.80–$50 inclusive,
opening-known split basis, price-valid 1m close with XNYS regular boundaries), ranks positive trailing 60-second trade counts in
ClickHouse, and returns only five tickers per completed second. Ties use source
ticker order. Source membership is the frozen approved retrospective ARTE
population, not a historical membership certification. Missing previous close
blocks/rejects explicitly; no raw-event fallback or acquisition is performed.

Trade windows: 60×1s, 30×10s, 30×1m, 16×1h, 20×regular daily. Quote windows:
60×1s for each bid/ask side, with OHLC, size and event count. Daily/hourly bars
are stored once and referenced across sessions. Missing scheduled bars remain
masked. Splits adjust historical price/volume at read time. No future daily or
unfinished hourly bar is exposed. Execution preserves the V64 one-second view
of certified 100ms liquidity, including displayed sizes and quote timestamps,
through 20:00 NY; model decisions are restricted to 04:00–09:30.

## Workstation commands

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -u research/market_shards/v1/run_build.py --output D:/TradingML/runtimes/market_shards/v1 --workers 8
python -B -m research.market_shards.v1.progress --output D:/TradingML/runtimes/market_shards/v1
```

Defaults resolve completed producer receipts under `D:/TradingML/runtimes`,
credentials from `D:/TradingML/secrets/.env`, and the workstation-local
ClickHouse endpoint. Read-only query settings are mandatory. `--days` limits a
profile to specific complete sessions; use a separate output root for each
profile. `--end` pins the inventory cutoff. Each worker has one active query,
one disk writer, and at most two queued Arrow batches. Default server parallelism
is one thread per query; increase worker count only after measuring throughput
and resource headroom. Aggregate configured query memory is capped at 64 GiB.

Create `stop.request` in the output directory to drain current sessions and stop.
Remove that request before resume. Sealed files are hash checked and reused;
unsealed partial files are replaced only within this run's output directory.
OS locking prevents simultaneous controllers. Closing the observer is harmless.
Status and completion distinguish blocked sessions from ready datasets. The
first January session may be blocked when December's close is absent.

After all ready sessions complete, manifests split them chronologically 80/20.
There is no holdout. No fitting is performed by this generator; any learned
normalization must use training only. `Session.windows()` allocates only a batch
and its selected tickers; it does not materialize an entire session of windows or
place the whole dataset on a GPU. `execution_pages()` is separate from model
context. Loading checks immutable seals and files once per referenced session.

Progress is published once per second and shows every worker, the active
phase, durable completed/reused/failed/blocked counts, bytes, timings and ETA.
Scanner ETA is explicitly phase-only until extraction has been measured.
