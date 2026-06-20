# Flatfile Event Update Pipeline

`pipelines/market_sip/flatfiles/download_update_events.py` keeps Massive quote
and trade flatfiles on disk, then inserts only unified compact events into
ClickHouse. It does not persist raw or compact quote/trade tables.

Use this pipeline when the goal is to extend `market_sip_compact.events` from
new Massive flatfiles without spending ClickHouse disk on intermediate quote and
trade tables.

## What It Builds

The pipeline writes:

- downloaded quote/trade `.csv.gz` flatfiles on disk
- unified event rows in `market_sip_compact.events`
- day build status rows in `market_sip_compact.events_build_manifest`
- per-ticker ordinal carry-forward rows in
  `market_sip_compact.events_ordinal_continuity`

It does not write `market_sip_compact.quotes` or `market_sip_compact.trades`.

## End-To-End Flow

1. Discover remote Massive SIP quote/trade flatfiles for the requested date
   range. Missing weekends/holidays are naturally skipped because discovery is
   based on remote objects, not a hard-coded calendar.
2. Download missing or incomplete quote/trade flatfiles to the configured
   flatfile root with atomic `.part` replacement.
3. Wait until both quote and trade files for a day are complete.
4. For each completed day, in chronological order, have ClickHouse read the
   quote/trade gzip CSV files through `file()`.
5. Convert raw rows directly to the current `market_sip_compact.events` schema:
   price integer plus scale flags, size fields, exchanges, packed conditions,
   event type, event date, and SIP timestamp in microseconds.
6. Filter structurally invalid rows before they enter `events`.
7. Assign ticker-local ordinals using `events_ordinal_continuity`.
8. Write `events_build_manifest` and `events_ordinal_continuity` rows for the
   processed day.

Downloads can run concurrently. Event insertion is intentionally chronological:
each day depends on the previous continuity state, so concurrent day inserts
would corrupt ordinals.

## Prerequisites

Environment variables are loaded through the repo's normal `.env` discovery.
The script needs:

- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `S3_ENDPOINT_URL`
- `BUCKET`
- `FLATFILES_ROOT`, or pass `--flatfiles-root-win`
- ClickHouse URL/user/password variables used by the market SIP pipelines
- `CLICKHOUSE_LIVE_STORAGE_POLICY` or explicit `--storage-policy`

ClickHouse must be able to read the local flatfile path through its configured
`user_files_path` or mounted file root. The script maps:

```text
--flatfiles-root-win -> --flatfiles-root-ch
```

For example:

```text
D:\market-data\flatfiles\us_stocks_sip
-> /mnt/d/market-data/flatfiles/us_stocks_sip
```

## Recommended Smoke Test

Use `--limit-days 1` first. `--dry-run` checks discovery and download planning
without inserting events.

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\flatfiles\download_update_events.py `
  --database market_sip_compact `
  --start-date 2026-06-01 `
  --end-date 2026-06-03 `
  --limit-days 1 `
  --dry-run
```

## Safe Test Mode

Before updating production `events`, use `--test-mode`. This runs the same
flatfile-to-events insert path over at least one complete quote/trade day, but
it rewrites the destinations to isolated temp tables:

```text
<test-prefix>_<run-id>_events
<test-prefix>_<run-id>_manifest
<test-prefix>_<run-id>_continuity
```

The production `events`, `events_build_manifest`, and
`events_ordinal_continuity` tables are not touched. Test mode also refuses
`--dry-run`, because it must insert temp rows and then audit them.

After the temp insert, the script audits:

- structural event integrity: valid event type, timestamps, nonzero quote/trade
  prices and sizes, quote/trade field semantics, and duplicate
  `(ticker, ordinal)` rows
- continuity integrity: temp event counts must match temp continuity counts per
  ticker/day
- reference-table integrity: deterministic random clean samples from the main
  compact `quotes` and `trades` tables must match rows in the temp `events`
  table after the same event conversion and condition packing

By default, successfully audited temp tables are dropped. Failed temp tables are
left in place for inspection. Pass `--test-keep-tables` to keep successful temp
tables too.

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\flatfiles\download_update_events.py `
  --database market_sip_compact `
  --start-date 2026-06-01 `
  --end-date 2026-06-03 `
  --test-mode `
  --download-workers 8 `
  --max-threads 32 `
  --test-sample-size 100
```

Use `--test-reference-quote-table` and `--test-reference-trade-table` only if
the main compact quote/trade table names differ from `quotes` and `trades`.

To run one real day:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\flatfiles\download_update_events.py `
  --database market_sip_compact `
  --start-date 2026-06-01 `
  --end-date 2026-06-03 `
  --limit-days 1 `
  --download-workers 8 `
  --max-threads 32
```

## Typical Production Run

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\flatfiles\download_update_events.py `
  --database market_sip_compact `
  --start-date 2026-06-01 `
  --end-date 2026-06-19 `
  --download-workers 8 `
  --max-threads 32 `
  --max-memory-usage 400G
```

The script prints:

- discovered complete quote/trade day pairs
- per-day download completion
- day insert start and ETA
- ClickHouse query profile lines from the shared `run_profiled` helper
- final JSONL report path

Reports are written under:

```text
--output-root-win\flatfile_event_update_<run_id>.jsonl
```

## Important Parameters

Date and scope:

- `--start-date`: first source date to discover, inclusive.
- `--end-date`: last source date to discover, inclusive.
- `--limit-days`: process only the first N complete quote/trade day pairs after
  filtering. Use for smoke tests.
- `--day-offset`: skip the first N complete day pairs. Use to continue a manual
  staged run.

ClickHouse:

- `--clickhouse-url`: ClickHouse HTTP endpoint.
- `--user`: ClickHouse user.
- `--password`: ClickHouse password.
- `--database`: target database, default `market_sip_compact`.
- `--events-table`: target events table, default `events`.
- `--manifest-table`: build manifest table, default `events_build_manifest`.
- `--continuity-table`: ordinal continuity table, default
  `events_ordinal_continuity`.
- `--storage-policy`: MergeTree storage policy for created tables.
- `--max-threads`: ClickHouse query threads used for event insertion.
- `--max-memory-usage`: ClickHouse query memory cap, for example `400G`.
- `--partition-mode`: event table partitioning, default `month`.
- `--max-partitions-per-insert-block`: ClickHouse insert safety setting.

Flatfile path mapping:

- `--flatfiles-root-win`: Windows path where flatfiles are stored.
- `--flatfiles-root-ch`: path visible to ClickHouse's `file()` function.

Remote download:

- `--download-workers`: concurrent day-download workers. Each worker downloads
  quote and trade for one day.
- `--s3-endpoint-url`: Massive flatfile S3-compatible endpoint.
- `--bucket`: remote bucket.
- `--aws-access-key-id`: S3 access key.
- `--aws-secret-access-key`: S3 secret.
- `--chunk-bytes`: HTTP download chunk size.
- `--timeout-seconds`: per-request timeout.
- `--overwrite-incomplete`: replace local files whose size differs from remote.

Retry and safety:

- `--retry-failed`: allow retry of days with latest manifest status `failed`.
- `--retry-started`: allow retry of days with latest status `started` or
  `interrupted`.
- `--force-day-delete`: delete existing `events` and continuity rows for a day
  before retrying it. Use this with retry flags to avoid duplicate rows.
- `--dry-run`: discover/download-plan only; no event inserts.
- `--test-mode`: build isolated temp events/manifest/continuity tables and
  audit them against the main compact quote/trade tables. Production event
  tables are not modified.
- `--test-keep-tables`: keep successful test-mode temp tables for manual
  inspection. Failed test tables are always kept.

## Retry Safety

Days with latest manifest status `ok` are skipped. Failed, started, or
interrupted days are not rebuilt unless retry flags are provided. Use
`--force-day-delete` with retry flags when reprocessing an incomplete day so
old event and continuity rows are removed before new rows are inserted.

If the process is stopped during a day insert, rerun with:

```powershell
--retry-started --force-day-delete
```

If a day failed because of a transient ClickHouse or filesystem issue, rerun
with:

```powershell
--retry-failed --force-day-delete
```

## Why Event Inserts Are Chronological

The event table uses ticker-local `ordinal` values. For a given day, each
ticker's new ordinal offset comes from the latest prior
`events_ordinal_continuity` row. If two days were inserted concurrently, the
later day could read the wrong offset. For that reason, the script downloads
concurrently but inserts days in chronological order.

## Data Semantics

The event rows match the unified event table contract:

- quote rows use `event_type = 0`
- trade rows use `event_type = 1`
- quote primary price is ask, secondary price is bid
- trade primary price is trade price, secondary price is zero
- price scale bits are packed into `event_flags`
- quote conditions use quote condition dense IDs
- trade conditions use trade condition dense IDs
- structurally invalid rows are filtered before insertion

For the detailed event schema, see `UNIFIED_EVENTS_TABLE.md`.
