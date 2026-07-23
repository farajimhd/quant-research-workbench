# OpenAI News Embeddings V1

This pipeline embeds the exact single-ticker article population used by
`news_reaction_stock_state_dataset_v7` and persists one 3,072-dimensional
`text-embedding-3-large` vector per `(canonical_news_id, ticker, published_at)`.
It reuses the V5/V7 publication-text contract so embedding comparisons use the
same article content as the existing reaction-model experiments.

## Safety contract

- The compiled job ceiling is **$50.00**. A CLI override may only lower it.
- Every input is tokenized locally before the first paid request.
- Submitted work is reserved at the full synchronous price ($0.26/M input
  tokens), while reconciled Batch usage is charged in the ledger at the Batch
  price ($0.13/M). This conservative reservation prevents queued work from
  hiding spend.
- At most one 2.5M-token Batch is in flight. Items, batches, estimated tokens,
  actual API usage, actual cost, and outstanding reservations are durable in
  ClickHouse.
- OpenAI quota/billing-limit responses stop new submissions immediately while
  retaining restartable state.
- A normal API key does not expose the account's remaining dollar balance.
  Therefore, the local ledger is the enforceable job-level guard; configure a
  $50 project budget/alert in the OpenAI dashboard as the independent
  account-level guard.

The prices above are the published prices verified on 2026-07-22. Review them
before reusing this version after a pricing change; create a new embedding
version if the model, dimensions, text contract, or pricing contract changes.

## Run

From the repository or the self-contained workstation runtime:

```powershell
# Read-only approximate plan. No DB or OpenAI writes.
python -m research.news_reaction_model.openai_embeddings_v1.run_build

# Exact pre-tokenization, durable planning, and one-at-a-time Batch execution.
python -m research.news_reaction_model.openai_embeddings_v1.run_build --execute

# Submit one bounded Batch and return. Rerun the same command to reconcile.
python -m research.news_reaction_model.openai_embeddings_v1.run_build --execute --no-wait

# Inspect durable coverage, spend, duplicate keys, and vector dimensions.
python -m research.news_reaction_model.openai_embeddings_v1.run_build --audit

# Explicitly retry correctable failures, never more than three attempts/item.
python -m research.news_reaction_model.openai_embeddings_v1.run_build --execute --retry-failed
```

`OPENAI_API_KEY` is discovered through the shared MLOps `.env` loader. The key
is never copied into manifests, logs, Batch metadata, database rows, or the
workstation runtime. On the workstation, provision the key separately in
`D:\TradingML\secrets\.env`; syncing this runtime intentionally does not copy
the laptop repository's `.env` file.

## Durable tables

- `market_sip_compact.news_openai_embeddings_v1`: final vectors and provenance.
- `market_sip_compact.news_openai_embedding_items_v1`: per-article state,
  token count, attempt count, and bounded error detail.
- `market_sip_compact.news_openai_embedding_batches_v1`: remote IDs, expected
  and actual tokens/cost, outstanding reservations, and restart state.

Local Batch input text is written under
`NEWS_REACTION_OPENAI_EMBEDDING_ROOT/inputs` only while needed. Input, output,
and manifest files plus corresponding remote Files API objects are removed after
durable reconciliation. The compact `status.jsonl` contains counters and IDs,
not article text or secrets.
