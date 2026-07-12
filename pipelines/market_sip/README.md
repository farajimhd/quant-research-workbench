# Market SIP Pipelines

This folder owns historical Massive SIP quote/trade workflows. These scripts are
operational data pipelines, not model-version experiments.

## Layout

```text
flatfiles/     Download Massive SIP quote/trade flatfiles.
ingest/        Ingest raw/compact SIP flatfiles into ClickHouse.
events/        Build unified ordinal event tables and sampling indexes.
sample_cache/  Build, validate, and audit fixed-width event sample shards.
benchmarks/    Benchmark ClickHouse queries and compact batch providers.
validation/    Validate compact tables, audit sources, reconcile manifests, and delete bad rows.
legacy/        Older canonical/chunk builders kept for reference and compatibility.
docs/          Market SIP data contracts, runbooks, and benchmark notes.
```

## Current Training Path

The active packed-model training path is:

```text
Massive flatfiles
  -> ingest/clickhouse_ingest_sip_compact_codec.py
  -> events/clickhouse_build_unified_events.py
  -> events/clickhouse_build_trade_bars.py
  -> market_sip_compact.events_YYYY + events_ticker_day_index
  -> research/mlops/packed_market/streaming.py
  -> research/packed_market_model/v1
```

The packed loader streams ticker/month blocks directly from ClickHouse and does
not require the legacy fixed-width sample cache. The unified event table
contract is documented in `docs/UNIFIED_EVENTS_TABLE.md`.

For incremental flatfile updates that should keep quotes/trades only on disk
and write unified events plus qmd-compatible bars to ClickHouse, use
`flatfiles/download_update_events.py`. Its runbook is documented in
`docs/FLATFILE_EVENT_UPDATE.md`.

To materialize reusable training macro bars from `market_sip_compact.events`,
use the events bar builder:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\events\run_build_trade_bars.py
```

It writes the compact macro table `macro_bars_by_time_symbol` with `1d,1w,1y`
bars. Use `--full-rebuild` once if an older all-bars run left UTC-midnight daily
bars or unsupported `1mo` rows in the macro table. The qmd-compatible
`live_market_bars` / `bars_by_*` staging path is legacy and is available only
through explicit `--bar-mode qmd`.

The `sample_cache` scripts and `docs/EVENT_SAMPLE_CACHE.md` remain historical
pipeline tooling; they are not on the packed-model run chain. Review them as a
separate pipeline cleanup boundary before removal because external data audits
may still use the shard readers.

## Compatibility

The old market-SIP compatibility wrappers under `research/mlops` have been
removed. Use the scripts and module paths under `pipelines/market_sip/...` for
all market-SIP operations.

The shared utility code that model versions import directly remains in
`research/mlops`, especially:

- `research/mlops/clickhouse.py`
- `research/mlops/clickhouse_events.py`
- `research/mlops/compact_events.py`
- `research/mlops/event_sample_cache.py`

These modules also support operational pipelines or the reserved Market AI
boundary and are not part of the removed temporal-model abstraction.
