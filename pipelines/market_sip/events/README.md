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
| `macro_bars_by_time_symbol` | `toYYYYMM(bar_start)` | `(timeframe, bar_start, sym, bar_family)` | macrostructure and future-label joins |

The default timeframes are:

```text
1d,1w,1y
```

The builder emits three non-mixed bar families for each `(session_date,
timeframe, sym)`:

| `bar_family` | Event source | Price fields | Size fields |
| --- | --- | --- | --- |
| `trade` | trade events only | trade `open`, `close`, `high`, `low` | `size_sum`, `event_count` |
| `quote_bid` | quote events only | bid `open`, `close`, `high`, `low` | bid `size_open`, `size_close`, `size_high`, `size_low`, `event_count` |
| `quote_ask` | quote events only | ask `open`, `close`, `high`, `low` | ask `size_open`, `size_close`, `size_high`, `size_low`, `event_count` |

The table intentionally does not store derived mid/spread/VWAP/dollar-volume
fields by default. Those can be derived downstream from the family bars when an
experiment asks for them. Daily bars are grouped by the New York extended-hours session:
04:00 ET inclusive through 20:00 ET exclusive. This means the daily `close` is
the last valid event in that family before the after-hours close, not the 16:00
regular-market close.

Run on the workstation:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\events\run_build_trade_bars.py
```

If `macro_bars_by_time_symbol` was previously populated by the old all-bars
or old trade-only path, rebuild it once from scratch so stale UTC-midnight daily
bars, stale `1mo` rows, and the old no-`bar_family` schema are removed:

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

## Intraday Base Bars

`clickhouse_build_intraday_base_bars.py` builds reusable intraday grid artifacts
from compact events. Trade and quote bars are kept in
`intraday_base_bars_by_time_ticker`; condition bars are intentionally stored in
the separate `intraday_condition_bars_by_time_ticker` table.

This separation keeps numeric price/size bars clean:

| Table | Partition | Order key | Contents |
| --- | --- | --- | --- |
| `intraday_base_bars_by_time_ticker` | `toYYYYMM(local_date)` | `(ticker, local_date, label_resolution_us, bucket_index, bar_family)` | `trade`, `quote_bid`, and `quote_ask` OHLC/size/count bars |
| `intraday_condition_bars_by_time_ticker` | `toYYYYMM(local_date)` | `(ticker, local_date, label_resolution_us, bucket_index)` | sparse condition buckets with halt/pause, resume, news-risk, and LULD flags |

Condition bars use the canonical dense token ids in
`event_condition_token_reference` and only include the four forecastable
condition groups currently used by the loader/model labels:

```text
condition_halt_pause_flag
condition_resume_flag
condition_news_risk_flag
condition_luld_limit_state_flag
```

Each condition row represents one ticker/date/resolution/bucket where at least
one selected condition event occurred. Flags are aggregated with `max()` and
`condition_event_count` records the number of condition-bearing events in that
bucket. Missing rows mean all condition flags are false for that bucket.

The intraday builder does not aggregate every requested resolution directly
from raw events. It first builds sparse buckets at the smallest requested
resolution, then rolls larger resolutions up from that base grid in the same
insert. This keeps the raw event scan to one pass per local session day while
preserving exact open/close/high/low/count semantics for coarser horizons.

The default long-run plan is month chunking plus automatic ticker batching. The
planner reads `events_ticker_day_index`, estimates event volume for the month,
and splits tickers into batches of roughly `--ticker-batch-max-events` events
with at most `--ticker-batch-max-tickers` symbols. This is required because the
events table is monthly-partitioned and ordered by `(ticker, ordinal)`: a
full-market single-day query can still scan a large part of the monthly
partition, while a ticker-batched monthly query can use the primary-key order.

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

`clickhouse_build_sec_context.py` materializes filing and XBRL context from
`q_live` into `market_sip_compact` for structured training inputs. SEC text
tokenization and embedding no longer depend on a materialized text-context copy.

The migration creates filing and XBRL context tables by default. The legacy
text-context table is created only with `--no-skip-text`:

| Table | Partition | Order key | Contents |
| --- | --- | --- | --- |
| `sec_filing_context_v3` | `toYYYYMM(accepted_at_utc)` | `(ticker, timestamp_us, accession_number, cik)` | one SEC filing metadata row per valid ticker/accession mapping |
| `sec_filing_text_context_v3` | `toYYYYMM(accepted_at_utc)` | `(ticker, timestamp_us, accession_number, text_rank, document_id)` | legacy optional materialization; not used by v3 tokenization or embedding |
| `sec_xbrl_context_v3` | `toYYYYMM(accepted_at_utc)` | `(ticker, timestamp_us, accession_number, xbrl_row_kind, taxonomy, tag, unit_code, period_end_date, source_id)` | company facts and frame observations joined to their filing event time |

The accepted timestamp source is always `q_live.sec_filing_v3.accepted_at_utc`.
Rows with null `accepted_at_utc` are skipped because they do not have a safe
no-lookahead event time. `id_sec_market_bridge_v3` is used only to map CIK or
accession to a market ticker; it is deduplicated inside the migration and is not
used as the event-time source.

`q_live.sec_filing_text_v3` stores submitted text-source documents such as HTML,
plain text, and non-XBRL XML. The compact `sec_filing_text_context_v3.text` field
is deterministic packed model input rendered from those submitted sources by
`sec_packed_text_renderer_v6`. HTML and inline-XBRL HTML are parsed by visible
tags, real HTML tables are packed into column/value lines, hidden/script/style
content is skipped, and plain text is rendered conservatively. XML is parsed
with tag/path context so schema tags that carry heading meaning are preserved.
Repeated blocks are compacted only when their normalized length is at least 200
characters; duplicate occurrences are replaced with `DUPLICATE of [first 15
chars]`. Structured fund-report XML such as NPORT/N-CEN is preserved in the
upstream source table but marked `structured_xml_excluded` in context so it is
not tokenized as readable filing text. The renderer
does not summarize, remove SEC/legal boilerplate, remove risk factors, remove
signatures, or rewrite substantive contract/table text. `q_live.sec_filing_text_rendered_v3`
stores the renderer/normalizer output for audit. The context table records
`source_text_char_count`, `source_text_hash`, `model_text_hash`,
`model_normalizer_version`, `removed_layout_line_count`, renderer block counts,
table block counts, long-block duplicate counts, and per-block hashes for auditability.

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

Audit the packed text renderer against raw daily archive filings:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_packed_text_renderer_audit.py --archive-root-win D:\market-data\sec_core\daily_archives --start-date 2026-01-01 --end-date 2026-07-11 --samples-per-content-type 3 --content-formats html,xml,plain_text
```

The audit samples submitted text-source payloads by `content_format`, shows the
upstream extracted source text, renders the packed model text, and includes
examples of repeated long renderer blocks when duplicate block hashes are detected.

By default, the migration deletes the target accepted-time range from the compact
tables and waits for ClickHouse mutations before reinserting. This keeps reruns
idempotent and avoids requiring `FINAL` in the hot training path. Ctrl+C exits
with status code `130` and writes an interrupted row to the JSONL report.

## Qwen Text Tokens And Embeddings

`clickhouse_build_text_tokens.py` pre-tokenizes news and SEC filing text for
training. `clickhouse_build_qwen_text_embeddings.py` is the combined embedding
launcher for the same pipeline: it defaults to `--build-embeddings` and
`--embedding-input-source source_text`, so one run writes token rows and their
float32 Qwen embeddings. Pass `--embedding-input-source token_tables`
explicitly only for an embedding-only repair from existing token rows.

The builder creates two separate tables:

| Table | Partition | Order key | Source |
| --- | --- | --- | --- |
| `news_text_tokens` | `toYYYYMM(published_at_utc)` | `(ticker, timestamp_us, source_id, token_chunk_index)` | `q_live.benzinga_news_ticker_v1` joined to `q_live.benzinga_news_normalized_v1` |
| `sec_filing_text_tokens_v3` | `toYYYYMM(accepted_at_utc)` | `(ticker, timestamp_us, accession_number, text_rank, document_id, source_id)` | `q_live.sec_filing_text_rendered_v3` joined to document, filing, and event-valid bridge rows |
| `news_text_embeddings` | `toYYYYMM(published_at_utc)` | `(ticker, timestamp_us, source_id, token_chunk_index)` | one float32 Qwen embedding per news token chunk |
| `sec_filing_text_embeddings_v3` | `toYYYYMM(accepted_at_utc)` | `(ticker, timestamp_us, accession_number, text_rank, document_id, source_id)` | one float32 Qwen embedding per SEC token chunk |

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

SEC tokenization reads each `sec_filing_text_rendered_v3` document row directly,
without a prefix cap and without concatenating documents from the same filing.
It joins `sec_filing_document_v3`, `sec_filing_v3`, and the event-valid
`id_sec_market_bridge_v3` relation to attach sequence, acceptance time, and
ticker identity. Tokenization currently uses up to eight 1024-token rows per
rendered document as a temporary safety policy pending corpus length statistics.
Both token tables include `token_chunk_index`, `token_start`, and `token_end`,
so multiple chunks do not collapse under the same replacing key.

Embeddings are also chunk-level, not item-level. The LLM is run independently for
each stored chunk:

- news: up to `2` embeddings per ticker/article row
- SEC text: up to `8` embeddings per filing text row

The embedding rows repeat the token metadata and store:

- `embedding Array(Float32)`
- `embedding_model`
- `embedding_pooling`
- `embedding_dtype = 'float32'`
- `embedding_dim`

Do not average chunks before storage. The rolling/temporal model should combine
chunk embeddings later with chunk positional encoding, item pooling or attention,
and modality-specific context encoders.

The default tokenizer id remains `Qwen/Qwen3-0.6B` to match the existing token
tables. The default embedding checkpoint is `Qwen/Qwen3-Embedding-0.6B`, and the
default pooling is `last_token`, matching the Qwen embedding model usage pattern.

The tokenized text starts with explicit metadata lines such as `NEWS` or
`SEC FILING`, provider/form/ticker/timestamp fields, and then the bounded source
body. This keeps the modality and provenance visible to the text encoder while
still using a single tokenizer model.

Run on the workstation:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\events\run_build_text_tokens.py --start-date 2019-01-01 --end-date 2026-12-31
```

Build tokens and embeddings together from normalized source text. The Qwen
launcher enables `--build-embeddings` and `--embedding-input-source source_text`
by default:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\events\run_build_qwen_text_embeddings.py `
  --start-date 2019-01-01 `
  --end-date 2026-12-31
```

Profile Qwen embedding throughput without mutating ClickHouse:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\events\run_build_qwen_text_embeddings.py `
  --start-date 2019-02-01 `
  --end-date 2019-02-01 `
  --profile-embeddings-only `
  --embedding-profile-source-rows 256 `
  --no-local-files-only
```

The profile writes `insert_batch` JSONL rows with `embedding_seconds`,
`embedding_sequences_per_second`, and `embedding_tokens_per_second`. Saved
embeddings are always float32; `--embedding-torch-dtype` only controls the model
inference dtype used during extraction. Use `--no-local-files-only` on the first
run if the `Qwen/Qwen3-Embedding-0.6B` weights are not already cached.

Only rebuild tokens and embeddings together when token rows are missing or need
to be regenerated with a different tokenizer/chunking policy:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\events\run_build_qwen_text_embeddings.py `
  --start-date 2019-01-01 `
  --end-date 2026-12-31 `
  --embedding-input-source source_text
```

If the workstation environment does not already have HuggingFace installed:

```powershell
python -m pip install "transformers>=4.50" "tokenizers>=0.20" "huggingface_hub>=0.25"
```

The tokenizer files are loaded at script startup. Embedding builds additionally
load the Qwen model weights. By default the script uses the local HuggingFace
cache; add `--no-local-files-only` on the first real token or embedding run if
the Qwen files need to be downloaded. Token-only builds are CPU/Rust-tokenizer
work, not GPU model inference. Embedding builds do run the model and should be
profiled on the workstation before launching a full historical range.

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
