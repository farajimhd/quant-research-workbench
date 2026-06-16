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
  -> sample_cache/build_event_sample_cache.py
  -> research/masked_event_model/vN
```

The sample-cache record contract is documented in
`docs/EVENT_SAMPLE_CACHE.md`. The unified event table contract is documented in
`docs/UNIFIED_EVENTS_TABLE.md`.

## Compatibility

Temporary wrappers remain under `research/mlops` for active workstation commands.
New commands and docs should prefer `pipelines/market_sip/...`.

The shared utility code that model versions import directly remains in
`research/mlops`, especially:

- `research/mlops/clickhouse.py`
- `research/mlops/clickhouse_events.py`
- `research/mlops/compact_events.py`
- `research/mlops/event_sample_cache.py`

Do not move those shared provider modules while active training jobs still
depend on them.
