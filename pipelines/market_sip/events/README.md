# Market SIP Event Pipelines

This folder contains ClickHouse pipelines that derive reusable event-level
training tables from `market_sip_compact.events`.

## Macro Bars

`clickhouse_build_trade_bars.py` builds training macro bars directly from
`market_sip_compact.events` into `market_sip_compact.macro_bars_by_time_symbol`.
The default path does not create `_staging_trade_bars` and does not copy rows
through the qmd-compatible intraday layouts.

The macro table is intentionally small:

| Table | Partition | Order key | Primary use |
| --- | --- | --- | --- |
| `macro_bars_by_time_symbol` | `toYYYYMM(bar_start)` | `(timeframe, bar_start, sym)` | macrostructure and future-label joins |

The default timeframes are:

```text
1d,1w,1y
```

The builder uses trade events for OHLCV/dollar volume/VWAP and quote events for
`quote_count`. Daily bars are grouped by the New York extended-hours session:
04:00 ET inclusive through 20:00 ET exclusive. This means the daily `close` is
the last valid trade before the after-hours close, not the 16:00 regular-market
close.

Run on the workstation:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\events\run_build_trade_bars.py
```

If `macro_bars_by_time_symbol` was previously populated by the old all-bars
path, rebuild it once from scratch so stale UTC-midnight daily bars and stale
`1mo` rows are removed:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\events\run_build_trade_bars.py `
  --full-rebuild `
  --start-date 2019-01-01 `
  --end-date 2026-12-31
```

The default launcher uses a Rich terminal layout with:

- run summary and per-timeframe build ranges
- overall build progress across delete/insert stages
- current operation
- recent messages

Ctrl+C exits with status code `130`, closes the Rich layout cleanly, appends an
`interrupted` row to the JSONL report, and best-effort sends `KILL QUERY ... SYNC`
for the active ClickHouse delete/insert query. If the interruption lands during
a ClickHouse mutation, check `system.mutations` before restarting.

Use plain line-based output when redirecting logs or when Rich rendering is not
useful:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\events\run_build_trade_bars.py --progress-layout text
```

Inspect the exact command without running:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\events\run_build_trade_bars.py --print-only
```

Preview DDL/DML without mutating ClickHouse:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\events\run_build_trade_bars.py --dry-run
```

By default, the builder replaces rows in the requested macro timeframe/date
range before inserting into `macro_bars_by_time_symbol`. Use `--full-rebuild`
when intentionally rebuilding the macro bar table from scratch. `--full-rebuild`
implies `--drop-table` and purges unsupported macro timeframes such as stale
`1mo` rows left by older all-bars builds.

## Bar Boundaries

Macro bar grouping is based on `events.sip_timestamp_us`, converted to
`America/New_York` for session filtering and period assignment.

- Daily bars include events from 04:00 ET through 20:00 ET for one New York
  trading date.
- Weekly bars group those same extended-session events by Monday-start New York
  week.
- Yearly bars group those same extended-session events by New York calendar
  year.

`--expand-boundaries` is enabled by default. Boundary expansion is applied per
timeframe before that timeframe is deleted and inserted. Daily timeframes use
the requested date range. Weekly timeframes expand to the full affected
Monday-Sunday week, and yearly timeframes expand to the full affected calendar
year. For example, requesting `2026-06-03` with `1d,1w,1y` builds `1d` only for
`2026-06-03`, `1w` for `2026-06-01 -> 2026-06-07`, and `1y` for
`2026-01-01 -> 2026-12-31`. Use `--no-expand-boundaries` only when intentionally
building partial-period bars.

The first bar in a build window has no earlier bar inside that same window, so
history-derived fields such as `return_1_bar`, `return_3_bar`, `return_5_bar`,
and acceleration fields are zero at the leading edge. If exact leading-edge
history fields matter, include enough prior dates in the build range and query
only the final desired slice afterward.

The last bar is built from all events currently present through the requested
end date. If the source event table does not yet contain the complete current
day/week/year, the last higher-timeframe bar is necessarily partial and should
be rebuilt after the remaining events arrive.

## Legacy QMD-Compatible Bars

The old qmd-compatible staging path is still available for explicit repair or
chart backfills:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\events\run_build_trade_bars.py `
  --bar-mode qmd `
  --timeframes "1s,5s,1m,5m,1d,1w,1mo"
```

That path writes `live_market_bars`, `bars_by_symbol_time`, and
`bars_by_time_symbol` through `_staging_trade_bars`. It is no longer the default
training macro-bar build, and it should not be used to populate
`macro_bars_by_time_symbol`.

## SEC Context Migration

`clickhouse_build_sec_context.py` materializes SEC and XBRL context from
`q_live` into `market_sip_compact` so training does not repeatedly query raw SEC
tables with `FINAL`, text joins, and CIK-to-market bridge joins.

The migration creates three compact context tables:

| Table | Partition | Order key | Contents |
| --- | --- | --- | --- |
| `sec_filing_context` | `toYYYYMM(accepted_at_utc)` | `(ticker, timestamp_us, accession_number, cik)` | one SEC filing metadata row per valid ticker/accession mapping |
| `sec_filing_text_context` | `toYYYYMM(accepted_at_utc)` | `(ticker, timestamp_us, accession_number, text_rank, document_id)` | bounded SEC filing text rows for model tokenization |
| `sec_xbrl_context` | `toYYYYMM(accepted_at_utc)` | `(ticker, timestamp_us, accession_number, xbrl_row_kind, taxonomy, tag, unit_code, period_end_date, source_id)` | company facts and frame observations joined to their filing event time |

The accepted timestamp source is always `q_live.sec_filing_v2.accepted_at_utc`.
Rows with null `accepted_at_utc` are skipped because they do not have a safe
no-lookahead event time. `id_sec_market_bridge_v1` is used only to map CIK or
accession to a market ticker; it is deduplicated inside the migration and is not
used as the event-time source.

The script uses `CLICKHOUSE_HISTORICAL_STORAGE_POLICY` by default through the
shared `default_storage_policy()` helper. Override it with `--storage-policy`
only when intentionally targeting a different ClickHouse disk policy.

Run on the workstation:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\events\run_build_sec_context.py --start-date 2019-01-01 --end-date 2026-12-31
```

Inspect the exact command without running:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\events\run_build_sec_context.py --print-only
```

Preview DDL/DML without mutating ClickHouse:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\events\run_build_sec_context.py --dry-run --start-date 2026-01-01 --end-date 2026-01-02
```

By default, the migration deletes the target accepted-time range from the compact
tables and waits for ClickHouse mutations before reinserting. This keeps reruns
idempotent and avoids requiring `FINAL` in the hot training path. Ctrl+C exits
with status code `130` and writes an interrupted row to the JSONL report.

## Text Token Tables

`clickhouse_build_text_tokens.py` pre-tokenizes news and SEC filing text for
training. This avoids repeatedly tokenizing the same Benzinga article or SEC
filing document while materializing rolling batches.

The builder creates two separate tables:

| Table | Partition | Order key | Source |
| --- | --- | --- | --- |
| `news_text_tokens` | `toYYYYMM(published_at_utc)` | `(ticker, timestamp_us, source_id, token_chunk_index)` | `q_live.benzinga_news_ticker_v1` joined to `q_live.benzinga_news_normalized_v1` |
| `sec_filing_text_tokens` | `toYYYYMM(accepted_at_utc)` | `(ticker, timestamp_us, accession_number, text_rank, document_id, source_id)` | `market_sip_compact.sec_filing_text_context` |

Each row stores source metadata plus fixed-length tokenizer output:

- `input_ids Array(UInt32)`
- `attention_mask Array(UInt8)`
- `token_count`
- `original_token_count`
- `padding_tokens`
- `was_truncated`
- `text_prefix_truncated`
- `tokenizer_model`
- `max_tokens`
- source identifiers and timestamps

News uses up to two 1024-token rows per ticker/article row by default. It is
assembled explicitly from `title`, `teaser`, `body_text`, `external_text`, and
`pdf_text` in `benzinga_news_normalized_v1`, with section labels in the tokenized
text. This avoids relying on a prefix of `normalized_full_text`, which can miss
enriched external/PDF text when it appears later in the merged article.

SEC filing text uses up to eight 1024-token rows per source text row. Both token
tables include `token_chunk_index`, `token_start`, and `token_end`, so multiple
chunks do not collapse under the same replacing key.

The tokenized text starts with explicit metadata lines such as `NEWS` or
`SEC FILING`, provider/form/ticker/timestamp fields, and then the bounded source
body. This keeps the modality and provenance visible to the text encoder while
still using a single tokenizer model.

Run on the workstation:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\events\run_build_text_tokens.py --start-date 2019-01-01 --end-date 2026-12-31
```

If the workstation environment does not already have HuggingFace installed:

```powershell
python -m pip install "transformers>=4.50" "tokenizers>=0.20" "huggingface_hub>=0.25"
```

The tokenizer files are loaded at script startup. By default the script uses the
local HuggingFace cache; add `--no-local-files-only` on the first real run if the
Qwen tokenizer files need to be downloaded. Tokenization is CPU/Rust-tokenizer
work, not GPU model inference, so you do not need to stop GPU training just for
this token table build.

Smoke a small range before a full build:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\events\run_build_text_tokens.py --start-date 2026-01-02 --end-date 2026-01-02 --limit-rows-per-chunk 1000
```

Inspect the exact command without running:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\events\run_build_text_tokens.py --print-only
```

Preview DDL/DML without mutating ClickHouse:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\events\run_build_text_tokens.py --dry-run --start-date 2026-01-02 --end-date 2026-01-02
```

## Training Category References

`clickhouse_build_training_category_reference.py` scans categorical fields that
are part of the rolling multimodal training data and writes dense ids to
`market_sip_compact.training_category_reference`.

Id `0` is intentionally reserved for missing or unknown. Table rows start at
`category_id = 1` and also store `one_hot_index = category_id - 1`, so training
code can either use embedding ids or sparse one-hot indices.

The current model-facing XBRL tensor consumes reference ids for:

- `xbrl.taxonomy`
- `xbrl.tag`
- `xbrl.unit_code`
- `xbrl.form_type`
- `xbrl.xbrl_row_kind`
- `xbrl.location_code`

The rolling text tensor path consumes these text metadata categories:

- `news.provider`
- `news.url_domain`
- `news.channels`
- `news.provider_tags`
- `news.quality_flags`
- `sec_filings.form_type`
- `sec_filings.text_kind`
- `sec_filings.quality_flags`

It deliberately does not create category ids for `fiscal_period`,
`calendar_period_code`, or `accepted_at_source`; those should be represented by
time/period features or kept in audit context rather than learned as arbitrary
category labels.

Run on the workstation after SEC context and text token tables are current:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\events\run_build_training_category_reference.py
```

Preview the exact command:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\events\run_build_training_category_reference.py --print-only
```

Summarize existing token tables without deleting, inserting, or loading the
tokenizer:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\events\run_build_text_tokens.py --summary-only --start-date 2019-01-01 --end-date 2026-12-31
```

Production runs are strict by default: the script fails if the configured
HuggingFace tokenizer is unavailable. For a local smoke test only, add
`--allow-fallback-tokenizer` to generate deterministic hash tokens without
downloading or loading the real model:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\events\run_build_text_tokens.py --dry-run --start-date 2026-01-02 --end-date 2026-01-02 --limit-rows-per-chunk 100 --allow-fallback-tokenizer
```

The script uses `CLICKHOUSE_HISTORICAL_STORAGE_POLICY` by default and writes a
JSONL report under
`D:\market-data\prepared\clickhouse_sip_ingest\text_tokens`.
When `--replace-range` is enabled, it waits for ClickHouse delete mutations
before inserting replacement token rows.

If an earlier schema was already created before SEC chunking/stat columns were
added, rerun once with `--drop-target-tables` before the production build.
