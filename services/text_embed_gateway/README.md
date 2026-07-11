# Text Embedding Gateway

`services.text_embed_gateway` keeps the live Qwen text embedding path ready for
news and SEC text. It uses the same tokenizer, model, pooling, and ClickHouse
schemas as the historical builder in `pipelines/market_sip/events`.

## Flow

1. Load `.env` files, connect to ClickHouse, and load Qwen to GPU at service
   startup.
2. Ensure token tables, embedding tables, and `text_embedding_coverage_v1`.
3. Poll the recent live window for news and SEC rows, even when the market is
   closed or after-hours text arrives. Cadence is market-aware:
   - active collection window: `TEXT_EMBED_LIVE_POLL_SECONDS`
   - closed weekday: `TEXT_EMBED_CLOSED_POLL_SECONDS`
   - weekend: `TEXT_EMBED_WEEKEND_POLL_SECONDS`
4. For each live poll:
   - source text rows that do not yet have token rows
   - token rows that do not yet have embedding rows
5. Outside market collection hours, also process broader historical gaps.
6. Persist token rows, embedding rows, and lightweight coverage rows.
7. On shutdown, cancel active ClickHouse queries, finish the current persist
   step when possible, release model references, and clear CUDA cache.

Historical SEC context refresh is chunked before tokenization/embedding. This
avoids forcing ClickHouse to sort the full historical lookback window in one
query when selecting the top SEC text rows per filing. The gateway scans chunks
newest-to-oldest and advances the cursor only after a chunk succeeds.

The gateway does not retain SEC filings, article bodies, PDFs, enriched text, or
embedding arrays in memory after a batch is written. The terminal keeps only a
small TTL-bounded status history.

The Rich terminal separates progress into several panels:

- `Cumulative Rows`: total source rows fetched, token rows fetched, embeddings
  written, coverage rows, and current active ClickHouse queries.
- `Work Focus`: shows the current mode/source/stage/window being processed and
  the last embedding extraction batch, including sequence count, token count,
  inference seconds, insert seconds, and sequences/second.
- `Current Operation`: shows `WORKING` while a query/embed/persist stage is
  running and `WAITING` between cycles, including the next poll time and the
  active/closed/weekend cadence.
- `Cycle Summary`: keeps the last recent live cycle and the last historical
  gap-fill cycle visible at the same time, including window, detected gaps,
  completed gaps, remaining gaps, rows written, and cycle seconds.
- `Coverage Report`: keeps separate stable rows for Live News, Live SEC,
  Historical News, and Historical SEC. Each row reports available source text,
  token chunks, embedding chunks, source gaps, embedding gaps, rows processed
  in the last cycle for that mode, remaining rows, and the source availability
  period.
- `Gap Summary`: shows source, embedding, and SEC context gaps by mode/source
  so a zero live poll does not erase the last historical gap-fill state.
- `Embedding Timing`: reports live and historical inference batches, sequences,
  average inference seconds, last inference seconds, average ms/sequence,
  sequences/second, tokens/second, insert seconds, and batch seconds.

For each gap cycle, the gateway reports the current scanned UTC window,
detected gaps, completed rows in that cycle, an estimated remaining count, and
the min/max missing event period for:

- news source rows missing tokens
- news token rows missing embeddings
- SEC context rows missing from recent raw SEC rows
- SEC context rows missing tokens
- SEC token rows missing embeddings

SEC rows blocked by missing ticker mapping are shown on the SEC context row as
`blocked_mapping=N` and are retried in later cycles.

## Important Env Vars

```text
TEXT_EMBED_GATEWAY_BIND=127.0.0.1:8798
TEXT_EMBED_SOURCE_DATABASE=q_live
TEXT_EMBED_CONTEXT_DATABASE=market_sip_compact
TEXT_EMBED_TARGET_DATABASE=market_sip_compact
TEXT_EMBED_NEWS_TOKEN_TABLE=news_text_tokens
TEXT_EMBED_SEC_TOKEN_TABLE=sec_filing_text_tokens
TEXT_EMBED_NEWS_EMBEDDING_TABLE=news_text_embeddings
TEXT_EMBED_SEC_EMBEDDING_TABLE=sec_filing_text_embeddings
TEXT_EMBED_COVERAGE_TABLE=text_embedding_coverage_v1
TEXT_EMBED_SEC_CONTEXT_FILING_TABLE=sec_filing_context
TEXT_EMBED_SEC_CONTEXT_TEXT_TABLE=sec_filing_text_context
TEXT_EMBED_SEC_LIVE_FILING_TABLE=sec_filing_v2
TEXT_EMBED_SEC_LIVE_TEXT_TABLE=sec_filing_text_v2
TEXT_EMBED_SEC_BRIDGE_TABLE=id_sec_market_bridge_v1
TEXT_EMBED_SEC_MAX_TEXT_ROWS_PER_FILING=0  # deprecated no-op; SEC context stores every text row
TEXT_EMBED_SEC_CONTEXT_REFRESH_CHUNK_HOURS=24
TEXT_EMBED_SEC_CONTEXT_HISTORICAL_MAX_CHUNKS_PER_CYCLE=7
TEXT_EMBED_MODEL=Qwen/Qwen3-Embedding-0.6B
TEXT_EMBED_TOKENIZER_MODEL=Qwen/Qwen3-0.6B
TEXT_EMBED_DEVICE=auto
TEXT_EMBED_TORCH_DTYPE=bfloat16
TEXT_EMBED_POOLING=last_token
TEXT_EMBED_LOCAL_FILES_ONLY=true
TEXT_EMBED_BATCH_SIZE=16
TEXT_EMBED_SOURCE_BATCH_SIZE=64
TEXT_EMBED_TOKEN_BATCH_SIZE=256
TEXT_EMBED_LIVE_POLL_SECONDS=2
TEXT_EMBED_CLOSED_POLL_SECONDS=60
TEXT_EMBED_WEEKEND_POLL_SECONDS=300
TEXT_EMBED_LIVE_LOOKBACK_MINUTES=180
TEXT_EMBED_HISTORICAL_LOOKBACK_DAYS=60
TEXT_EMBED_HISTORICAL_BATCH_LIMIT=512
TEXT_EMBED_RECENT_STATUS_RETENTION_HOURS=2
```

Saved embeddings are always written as `Array(Float32)`. `TEXT_EMBED_TORCH_DTYPE`
only controls model inference dtype.

## Commands

Config-only check:

```powershell
python -m services.text_embed_gateway.main --check-only
```

Load/release Qwen once:

```powershell
python -m services.text_embed_gateway.main --load-model-check
```

Run service:

```powershell
python -m services.text_embed_gateway.main
```

PowerShell launcher:

```powershell
.\scripts\run_text_embed_gateway.ps1 -CheckOnly
.\scripts\run_text_embed_gateway.ps1 -LoadModelCheck
.\scripts\run_text_embed_gateway.ps1
```

First-time model download/cache warmup:

```powershell
.\scripts\run_text_embed_gateway.ps1 -LoadModelCheck -NoLocalFilesOnly
```

Use the default local-files-only mode after the Qwen tokenizer/model files are
cached, so production does not depend on HuggingFace network availability.

SEC tokenization reads the same historical-compatible context table used by the
offline builder: `market_sip_compact.sec_filing_text_context` by default. Before
each SEC live/gap-fill cycle, the gateway performs a small idempotent context
refresh for the active lookback window:

```text
q_live.sec_filing_v2 + q_live.sec_filing_text_v2
  + q_live.id_sec_market_bridge_v1
-> market_sip_compact.sec_filing_context
-> market_sip_compact.sec_filing_text_context
```

The gateway applies the same deterministic SEC model-text normalizer as the
historical context builder while refreshing `sec_filing_text_context`. Upstream
`q_live.sec_filing_text_v2` remains full readable text; the compact context text
normalizes line endings, removes high-confidence layout and extraction artifacts,
collapses repeated horizontal whitespace and excess blank lines, and records
source/model hashes plus `model_normalizer_version` for audit. The normalizer
does not remove SEC/legal boilerplate, risk factors, signatures, or substantive
contract/table text. Tokenization and embedding read that context field directly,
so there is no second SEC parser inside the embedding step.

`id_sec_market_bridge_v1` is read-only here and should be maintained by the
reference gateway. If a SEC filing text row has no valid bridge yet, the gateway
does not embed it; it writes coverage with `blocked_missing_ticker_mapping` and
retries on later cycles. News and existing-token embedding still continue.

SEC `source_id` is intentionally compatible with the historical token builder:
`accession_number:text_rank:document_id`. The SEC context builder currently uses
`text_rank=0` for selected filing text rows and keeps `document_id` in the key,
so the live gateway does the same instead of creating a row-number rank.

## Service Boundaries

| Service | Writes | Text embedding dependency |
| --- | --- | --- |
| `news_gateway` | `q_live.benzinga_news_normalized_v1`, `q_live.benzinga_news_ticker_v1` | Final normalized/ticker rows are the news source. |
| `sec_gateway` | `q_live.sec_filing_v2`, `q_live.sec_filing_document_v2`, `q_live.sec_filing_text_v2`, SEC XBRL tables | Raw SEC source only; it does not own ticker mapping or embeddings. |
| `reference_gateway` | `q_live.id_sec_market_bridge_v1` and canonical reference mappings | Owns ongoing CIK/accession-to-market ticker bridge maintenance. |
| `text_embed_gateway` | `market_sip_compact.*_tokens`, `market_sip_compact.*_embeddings`, `text_embedding_coverage_v1`; idempotent recent SEC context rows | Uses historical-compatible source rows and Qwen to persist tokens/embeddings. |
