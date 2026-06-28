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

The active masked-event pretraining path is:

```text
Massive flatfiles
  -> ingest/clickhouse_ingest_sip_compact_codec.py
  -> events/clickhouse_build_unified_events.py
  -> events/clickhouse_build_trade_bars.py
  -> sample_cache/build_event_sample_cache.py
  -> research/masked_event_model/vN
```

The sample-cache record contract is documented in
`docs/EVENT_SAMPLE_CACHE.md`. The unified event table contract is documented in
`docs/UNIFIED_EVENTS_TABLE.md`.

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

To build only validation sample-cache shards, pass `--splits validation` to
`sample_cache/run_build_event_sample_cache.py` or directly to
`sample_cache/build_event_sample_cache.py`. The number of validation shards is
approximately `ceil(validation_cache_gib / shard_size_gib)`, except the final
shard may be partial.

For reconstruction-only masked-event pretraining, prefer the x-only launcher:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\sample_cache\run_event_sample_cache_pretrain_cycle.py --smoke
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\sample_cache\run_event_sample_cache_pretrain_cycle.py
```

It uses the current ClickHouse bundled sampler but writes v1-compatible
`samples.bin` shards only, with no future `y.bin` and no label sidecar.

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

Do not move those shared provider modules while active training jobs still
depend on them.
