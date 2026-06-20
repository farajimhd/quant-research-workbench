# Flatfile Event Update Pipeline

`pipelines/market_sip/flatfiles/download_update_events.py` keeps Massive quote
and trade flatfiles on disk, then inserts only unified compact events into
ClickHouse. It does not persist raw or compact quote/trade tables.

## Flow

1. Discover remote Massive SIP quote/trade flatfiles for the requested date
   range.
2. Download missing or incomplete quote/trade flatfiles to the configured
   flatfile root with atomic `.part` replacement.
3. For each completed day, in chronological order, have ClickHouse read the
   quote/trade gzip CSV files through `file()`.
4. Convert raw rows directly to the current `market_sip_compact.events` schema:
   price integer plus scale flags, size fields, exchanges, packed conditions,
   event type, event date, and SIP timestamp in microseconds.
5. Filter structurally invalid rows before they enter `events`.
6. Assign ticker-local ordinals using `events_ordinal_continuity`.
7. Write `events_build_manifest` and `events_ordinal_continuity` rows for the
   processed day.

Downloads can run concurrently. Event insertion is intentionally chronological:
each day depends on the previous continuity state, so concurrent day inserts
would corrupt ordinals.

## Example

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\flatfiles\download_update_events.py `
  --database market_sip_compact `
  --start-date 2026-06-01 `
  --end-date 2026-06-05 `
  --download-workers 8 `
  --max-threads 32
```

Useful smoke-test options:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\flatfiles\download_update_events.py `
  --start-date 2026-06-01 `
  --end-date 2026-06-05 `
  --limit-days 1 `
  --dry-run
```

## Retry Safety

Days with latest manifest status `ok` are skipped. Failed, started, or
interrupted days are not rebuilt unless retry flags are provided. Use
`--force-day-delete` with retry flags when reprocessing an incomplete day so
old event and continuity rows are removed before new rows are inserted.

## Storage Contract

The pipeline writes:

- flatfiles on disk under `FLATFILES_ROOT` / `--flatfiles-root-win`
- compact unified events in `market_sip_compact.events`
- event build status in `market_sip_compact.events_build_manifest`
- ordinal continuity in `market_sip_compact.events_ordinal_continuity`

The pipeline does not write `market_sip_compact.quotes` or
`market_sip_compact.trades`.
