# Benzinga article-level OpenAI embeddings v2

This pipeline embeds every certified `benzinga_news_rendered_v2` article exactly
once. It does not issue one OpenAI request per ticker.

The durable products are:

- `q_live.benzinga_news_openai_embedding_v2`: one 3,072-dimensional vector per
  article;
- `q_live.benzinga_news_openai_embedding_ticker_v2`: lightweight links from
  that article vector to every current ticker relationship;
- `q_live.benzinga_news_openai_embedding_item_v2`: resumable item state;
- `q_live.benzinga_news_openai_embedding_batch_v2`: OpenAI Batch state and
  actual/reserved cost accounting.

The embedding build refuses to run unless the structured renderer authority is
`ready` with zero audit errors. The compiled and command-line cost ceiling is
USD 50. Requests use the Batch API, durable reconciliation, exact token
planning, one active batch at a time, and restart-safe item state.

Run after the renderer rebuild has certified v2:

```powershell
python -m research.news_reaction_model.openai_embeddings_v2.run_build --execute
```

Audit without resubmitting completed articles:

```powershell
python -m research.news_reaction_model.openai_embeddings_v2.run_build --audit
```

The source text contract is the structured article text, capped at 50,000
characters and then token-safely capped at 8,000 tokens. Truncation is recorded
per article. Multi-ticker articles retain one vector; ticker links never copy
the vector.
