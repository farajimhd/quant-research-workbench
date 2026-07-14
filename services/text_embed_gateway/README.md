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

SEC source rows are read directly from `q_live.sec_filing_text_rendered_v3`,
joined to document metadata, filing acceptance time, and the event-valid
`id_sec_market_bridge_v3` ticker/listing relationship. No duplicate SEC text
context table is refreshed by this service.

The gateway does not retain SEC filings, article bodies, PDFs, enriched text, or
embedding arrays in memory after a batch is written. The terminal keeps only a
small TTL-bounded status history.

The height-bounded Rich terminal keeps the exact mode/source/stage/window and
last extraction in `Work Focus`, then presents one stable `Mode And Source
Coverage` matrix for Live News, Live SEC, Historical News, and Historical SEC.
The matrix combines available embeddings, detected gaps, completed work,
remaining work, last-cycle time, and window without allowing a zero live cycle
to erase historical state. `Embedding Timing` remains visible when terminal
height permits. Compact terminals combine focus and the four coverage rows into
one panel so current work stays above the fold.

Runtime errors now have explicit active and resolved timestamps plus mode/source
scope. A successful historical cycle cannot clear an active live-cycle error;
recovery is shown only after the matching mode completes successfully.

For each gap cycle, the gateway reports the current scanned UTC window,
detected gaps, completed rows in that cycle, an estimated remaining count, and
the min/max missing event period for:

- news source rows missing tokens
- news token rows missing embeddings
- SEC rendered document rows missing tokens
- SEC token rows missing embeddings

SEC rows blocked by missing ticker mapping are shown on the bridge-mapping row as
`blocked_mapping=N` and are retried in later cycles.

## Important Env Vars

```text
TEXT_EMBED_GATEWAY_BIND=127.0.0.1:8798
TEXT_EMBED_SOURCE_DATABASE=q_live
TEXT_EMBED_TARGET_DATABASE=market_sip_compact
TEXT_EMBED_NEWS_TOKEN_TABLE=news_text_tokens
TEXT_EMBED_SEC_TOKEN_TABLE=sec_filing_text_tokens_v3
TEXT_EMBED_NEWS_EMBEDDING_TABLE=news_text_embeddings
TEXT_EMBED_SEC_EMBEDDING_TABLE=sec_filing_text_embeddings_v3
TEXT_EMBED_COVERAGE_TABLE=text_embedding_coverage_v1
TEXT_EMBED_SEC_LIVE_FILING_TABLE=sec_filing_v3
TEXT_EMBED_SEC_LIVE_DOCUMENT_TABLE=sec_filing_document_v3
TEXT_EMBED_SEC_LIVE_RENDERED_TEXT_TABLE=sec_filing_text_rendered_v3
TEXT_EMBED_SEC_BRIDGE_TABLE=id_sec_market_bridge_v3
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
TEXT_EMBED_SEC_CHUNK_TOKENS=1024
TEXT_EMBED_SEC_MAX_CHUNKS=0
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

SEC tokenization and embedding use the same direct source join as the offline
builder:

```text
q_live.sec_filing_text_rendered_v3
  + q_live.sec_filing_document_v3
  + q_live.sec_filing_v3
  + q_live.id_sec_market_bridge_v3
-> market_sip_compact.sec_filing_text_tokens_v3
-> market_sip_compact.sec_filing_text_embeddings_v3
```

`sec_filing_text_rendered_v3` is the authoritative normalized input. The
embedding gateway does not run a second renderer. Each rendered document row is
an independent source item; documents from the same filing are not concatenated.
SEC documents are split into complete 1024-token chunks. A max-chunks value of
`0` means unlimited, so the gateway does not discard a document tail. Rows with
date-only fallback acceptance times remain blocked until their raw SEC event
timestamp is repaired.

`id_sec_market_bridge_v3` is read-only here and should be maintained by the
reference gateway. If a SEC filing text row has no valid bridge yet, the gateway
does not embed it; it writes coverage with `blocked_missing_ticker_mapping` and
retries on later cycles. News and existing-token embedding still continue.

SEC `source_id` is intentionally compatible with the historical token builder:
`accession_number:text_rank:document_id`. `text_rank` is the submitted document
sequence number capped to the current UInt8 schema, and `document_id` keeps each
rendered submitted document distinct.

## Service Boundaries

| Service | Writes | Text embedding dependency |
| --- | --- | --- |
| `news_gateway` | `q_live.benzinga_news_normalized_v1`, `q_live.benzinga_news_ticker_v1` | Final normalized/ticker rows are the news source. |
| `sec_gateway` | `q_live.sec_filing_v3`, `q_live.sec_filing_document_v3`, `q_live.sec_filing_text_v3`, `q_live.sec_filing_text_rendered_v3`, SEC XBRL v3 tables | Raw SEC source and renderer output only; it does not own ticker mapping or embeddings. |
| `reference_gateway` | `q_live.id_sec_market_bridge_v3` and canonical reference mappings | Owns ongoing CIK/accession-to-market ticker bridge maintenance. |
| `text_embed_gateway` | `market_sip_compact.*_tokens`, `market_sip_compact.*_embeddings`, `text_embedding_coverage_v1` | Directly joins rendered SEC documents to filing and bridge rows, then persists tokens/embeddings. |
