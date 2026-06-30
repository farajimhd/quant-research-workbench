# Flatfile Event Update Pipeline

`pipelines/market_sip/flatfiles/download_update_events.py` keeps Massive quote
and trade flatfiles on disk, then inserts unified compact events and
training macro bars into ClickHouse. It does not persist raw or compact
quote/trade tables.

Use this pipeline when the goal is to extend `market_sip_compact.events` from
new Massive flatfiles without spending ClickHouse disk on intermediate quote and
trade tables. Bars are rebuilt from the compact `events` rows after event
insertion succeeds.

## What It Builds

The pipeline writes:

- downloaded quote/trade `.csv.gz` flatfiles on disk
- unified event rows in `market_sip_compact.events`
- day build status rows in `market_sip_compact.events_build_manifest`
- per-ticker ordinal carry-forward rows in
  `market_sip_compact.events_ordinal_continuity`
- macro bar rows in `market_sip_compact.macro_bars_by_time_symbol`

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
9. Rebuild macro bar rows for the successfully updated date range directly from
   `events`. The default macro timeframes are `1d,1w,1y`. Daily bars use the
   New York extended-hours session, 04:00 ET through 20:00 ET, so the daily
   close is the after-hours close. Weekly and yearly bars expand their own
   delete/insert ranges to full affected week/year boundaries.

The standalone bar builder, `pipelines/market_sip/events/run_build_trade_bars.py`,
uses the same direct event-to-macro aggregation and writes
`macro_bars_by_time_symbol`. It has a Rich progress layout for long backfills.
Use it directly when rebuilding macro bars from already inserted `events` rows:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\events\run_build_trade_bars.py `
  --database market_sip_compact `
  --start-date 2019-01-01 `
  --end-date 2026-12-31
```

If the table was previously built by the old all-bars/staging path, run a
one-time full rebuild instead. This drops and recreates the macro table, removes
stale `1mo` rows, and rewrites `1d,1w,1y` with the current extended-hours
session boundaries:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\events\run_build_trade_bars.py `
  --database market_sip_compact `
  --start-date 2019-01-01 `
  --end-date 2026-12-31 `
  --full-rebuild
```

Macro bars are assigned from `events.sip_timestamp_us` converted to
`America/New_York`. Daily bars cover 04:00-20:00 ET. Weekly bars are Monday-start
New York weeks. Yearly bars are New York calendar years. The builder expands
weekly/yearly requests per timeframe by default; pass `--no-expand-boundaries`
only when a partial boundary bar is intentional.

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
it rewrites the destinations to isolated temp tables.

Temp table names use this pattern:

- `{test_prefix}_{run_id}_events`
- `{test_prefix}_{run_id}_manifest`
- `{test_prefix}_{run_id}_continuity`
- `{test_prefix}_{run_id}_macro_bars_by_time_symbol`

The production `events`, `events_build_manifest`,
`events_ordinal_continuity`, and `macro_bars_by_time_symbol` tables are not
touched. Test mode also refuses `--dry-run`, because it must insert temp rows
and then audit them.

After the temp insert, the script audits:

- structural event integrity: valid event type, timestamps, nonzero quote/trade
  prices and sizes, quote/trade field semantics, and duplicate
  `(ticker, ordinal)` rows
- continuity integrity: temp event counts must match temp continuity counts per
  ticker/day
- raw-source integrity: deterministic samples from the temp `events` table are
  matched back to the exact quote/trade `.csv.gz` files used for the test run.
  The validator scans those raw CSVs, converts candidate rows in memory with the
  same event encoding and condition packing rules, and compares the decoded event
  fields directly.

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
- macro bar rebuild range and per-timeframe insert profiles
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
- `--macro-bars-table`: target macro bar table, default
  `macro_bars_by_time_symbol`.
- `--bar-timeframes`: comma-separated macro bar timeframes to rebuild after
  event insertion. Default: `1d,1w,1y`.
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
- `--skip-bars`: update only events/continuity and skip the bar rebuild stage.
- `--bar-replace-range` / `--no-bar-replace-range`: controls whether
  overlapping bars are deleted before reinserting the updated range. Keep the
  default enabled for normal updates.
- `--dry-run`: discover/download-plan only; no event inserts.
- `--test-mode`: build isolated temp events/manifest/continuity tables and
  audit them against the raw quote/trade CSVs used for that run. Production
  event tables are not modified.
- `--test-keep-tables`: keep successful test-mode temp tables for manual
  inspection. Failed test tables are always kept.

## Retry Safety

Days with latest manifest status `ok` are skipped. Failed, started, or
interrupted days are not rebuilt unless retry flags are provided. Use
`--force-day-delete` with retry flags when reprocessing an incomplete day so
old event and continuity rows are removed before new rows are inserted.

The bar stage is intentionally derived from `events`, not tracked as a separate
per-day event manifest. If a run successfully inserts flatfile rows into
`events` but fails before or during bar creation, rerun the same date range. The
event stage will skip manifest-`ok` days, those skipped days are still passed to
the bar stage, and overlapping bars are rebuilt in all three layouts from the
already-inserted events. Keep the default `--bar-replace-range` enabled for
this recovery path.

If the process is stopped during a day insert, rerun with:

```powershell
--retry-started --force-day-delete
```

If a day failed because of a transient ClickHouse or filesystem issue, rerun
with:

```powershell
--retry-failed --force-day-delete
```

If Ctrl+C is pressed during the download phase, the parent process terminates
download workers, leaves completed files in place, and leaves incomplete
downloads as `.part` files. The next run checks remote sizes again and retries
incomplete files.

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
- price scale, tape, condition pack kind, and pack version are packed into `condition_tokens_packed`
- quote rows pack quote condition tokens plus the first quote indicator token
- trade rows pack trade condition tokens plus the trade correction token
- structurally invalid rows are filtered before insertion

For the detailed event schema, see `UNIFIED_EVENTS_TABLE.md`.
