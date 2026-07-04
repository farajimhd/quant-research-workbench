# Text Embedding Gateway

`services.text_embed_gateway` keeps the live Qwen text embedding path ready for
news and SEC text. It uses the same tokenizer, model, pooling, and ClickHouse
schemas as the historical builder in `pipelines/market_sip/events`.

## Flow

1. Load `.env` files, connect to ClickHouse, and load Qwen to GPU at service
   startup.
2. Ensure token tables, embedding tables, and `text_embedding_coverage_v1`.
3. During market collection hours, poll only the recent live window for:
   - source text rows that do not yet have token rows
   - token rows that do not yet have embedding rows
4. Outside market collection hours, process broader historical gaps.
5. Persist token rows, embedding rows, and lightweight coverage rows.
6. On shutdown, cancel active ClickHouse queries, finish the current persist
   step when possible, release model references, and clear CUDA cache.

The gateway does not retain SEC filings, article bodies, PDFs, enriched text, or
embedding arrays in memory after a batch is written. The terminal keeps only a
small TTL-bounded status history.

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
TEXT_EMBED_SEC_LIVE_FILING_TABLE=sec_filing_v2
TEXT_EMBED_SEC_LIVE_TEXT_TABLE=sec_filing_text_v2
TEXT_EMBED_SEC_TICKER_MAPPING_DATABASE=
TEXT_EMBED_SEC_TICKER_MAPPING_TABLE=sec_bulk_mirror_company_ticker_v1
TEXT_EMBED_MODEL=Qwen/Qwen3-Embedding-0.6B
TEXT_EMBED_TOKENIZER_MODEL=Qwen/Qwen3-0.6B
TEXT_EMBED_DEVICE=auto
TEXT_EMBED_TORCH_DTYPE=bfloat16
TEXT_EMBED_POOLING=last_token
TEXT_EMBED_LOCAL_FILES_ONLY=true
TEXT_EMBED_BATCH_SIZE=16
TEXT_EMBED_SOURCE_BATCH_SIZE=64
TEXT_EMBED_TOKEN_BATCH_SIZE=256
TEXT_EMBED_LIVE_LOOKBACK_MINUTES=180
TEXT_EMBED_HISTORICAL_LOOKBACK_DAYS=30
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

`TEXT_EMBED_SEC_TICKER_MAPPING_DATABASE` can be left empty. The gateway resolves
`sec_bulk_mirror_company_ticker_v1` from `q_live`, `market_sip_compact`,
`sec_core`, or the configured source/context/target databases at startup. If it
cannot find the table, SEC source-text tokenization is skipped but news and
existing-token embedding still continue.
