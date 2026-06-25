# Market SIP Event Pipelines

This folder contains ClickHouse pipelines that derive reusable event-level
training tables from `market_sip_compact.events`.

## QMD-Compatible Market Bars

`clickhouse_build_trade_bars.py` builds three qmd-gateway-compatible bar tables
from `market_sip_compact.events`. The schema mirrors
`services/qmd-gateway/src/bars.rs` `BAR_SCHEMA_VERSION = 2`, so historical
flatfile-derived bars and live qmd bars can be queried with the same column
contract.

The three tables contain identical rows and columns but use different physical
layouts for the main access patterns:

| Table | Partition | Order key | Primary use |
| --- | --- | --- | --- |
| `live_market_bars` | `session_date` | `(session_date, timeframe, sym, bar_start)` | QMD/chart-compatible date slices |
| `bars_by_symbol_time` | `toYYYYMM(bar_start)` | `(sym, timeframe, bar_start)` | per-ticker temporal training windows |
| `bars_by_time_symbol` | `toYYYYMM(bar_start)` | `(timeframe, bar_start, sym)` | market-wide time-snapshot training |

The default timeframes are:

```text
1s,5s,1m,5m,1d,1w,1mo
```

The builder uses trade events for OHLCV and quote events for bid/ask, midpoint,
spread, quote counts, quote rates, and displayed-size fields. Tape-side,
effective-spread, LULD, and some intra-bar path fields are filled with
deterministic historical proxies or neutral values where the live qmd writer
depends on stream-local state that is not durable in the compact events table.

Run on the workstation:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\events\run_build_trade_bars.py
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

By default, the builder replaces rows in the requested date range before
inserting in all three layouts. Use `--drop-table` only when intentionally
rebuilding the selected bar tables from scratch.

## Bar Boundaries

All bar boundaries are computed in UTC from `events.sip_timestamp_us`.

- Intraday bars use exact UTC interval starts: `1s`, `5s`, `1m`, and `5m`.
- Daily bars start at UTC midnight.
- Weekly bars start on Monday UTC.
- Monthly bars start on the first day of the UTC month.

`--expand-boundaries` is enabled by default. Boundary expansion is applied per
timeframe before that timeframe is deleted and inserted. Intraday and daily
timeframes use the requested date range. Weekly timeframes expand to the full
affected Monday-Sunday week, and monthly timeframes expand to the full affected
month. For example, requesting `2026-06-03` with `1s,1w,1mo` builds `1s` only
for `2026-06-03`, `1w` for `2026-06-01 -> 2026-06-07`, and `1mo` for
`2026-06-01 -> 2026-06-30`. Use `--no-expand-boundaries` only when
intentionally building partial-period bars.

The first bar in a build window has no earlier bar inside that same window, so
history-derived fields such as `return_1_bar`, `return_3_bar`, `return_5_bar`,
and acceleration fields are zero at the leading edge. If exact leading-edge
history fields matter, include enough prior dates in the build range and query
only the final desired slice afterward.

The last bar is built from all events currently present through the requested
end date. If the source event table does not yet contain the complete current
day/week/month, the last higher-timeframe bar is necessarily partial and should
be rebuilt after the remaining events arrive.

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
