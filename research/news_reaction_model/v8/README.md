# News Reaction Model V8: OpenAI text-representation ablation

V8 is a controlled copy of V7 with one experimental change:

- remove V7 word TF-IDF, character TF-IDF, and structured numeric-text channels;
- replace those text-derived channels with the durable 3,072-value
  `text-embedding-3-large` representation;
- keep the V7 point-in-time stock-state channel unchanged.

The horizon definitions, range classes, ending/high/low heads, losses, target
construction, chronological split, optimizer, scheduler, epochs, evaluation,
P&L simulation, checkpointing, artifacts, W&B project, and random seed remain
the V7 contract. This makes V8 an incremental text-representation ablation
rather than a new forecasting design.

## Representation

```text
OpenAI embedding (3,072) ----> LayerNorm + projection --\
                                                         +--> gated 2-channel pooling
V7 stock state (85) --------> unchanged projection -----/
                                                         + horizon embedding
                                                         + residual MLP
                                                         + unchanged range heads
```

The OpenAI authority is:

- table: `market_sip_compact.news_openai_embeddings_v1`
- version: `news_openai_text_embedding_3_large_3072_v1`
- model: `text-embedding-3-large`
- dimensions: `3072`
- text contract: `news_reaction_v7_publication_text_12000chars_8000tokens_v1`

V8 does not call OpenAI during training. It consumes only embeddings already
validated and persisted by `openai_embeddings_v1`.

The prepared table retains `Array(Float32)` vectors for auditability. Loaders
project those arrays as lossless little-endian Float32/base64 payloads before
HTTP transport, avoiding the much larger decimal JSON representation without
changing any vector value. Each scalar is padded to exactly four bytes before
concatenation because ClickHouse numeric-to-string reinterpretation otherwise
omits trailing zero bytes. The loader validates the byte count and reports the
exact article identity before constructing a tensor.

## Prepared dataset

The preparation job joins the exact V7 article identity
`(canonical_news_id, ticker, published_at_utc)` to the embedding authority and
copies the V7 stock state, labels, sessions, and split without recomputation.
Before any V8 database or artifact write, it requires:

- one unique V7 row per article;
- one valid 3,072-value embedding for every V7 row;
- the configured model, embedding version, and text contract;
- finite embedding values;
- one V7 representation revision across the requested range.

Partial embedding coverage fails loudly and leaves V8 untouched. The
materializer is month-partitioned, concurrent, resumable, and verifies exact
source/target parity before checkpointing a month.

Default prepared table:

`market_sip_compact.news_reaction_openai_stock_state_dataset_v8`

Default representation artifact:

`D:\market-data\prepared\news_reaction_model\v8\openai_embedding_stock_state_v1`

Default experiment root:

`D:\TradingML\runtimes\news-reaction-model\v8`

## Commands

From the self-contained workstation runtime:

```powershell
python -m research.news_reaction_model.v8.run_prepare_data
```

This first command is non-mutating unless `--execute` is supplied. While the
OpenAI extraction is incomplete it reports exact matched and missing coverage.
After `openai_embeddings_v1 --audit` reports full coverage:

```powershell
python -m research.news_reaction_model.v8.run_prepare_data --execute
python -m research.news_reaction_model.v8.run_profile_sizes --real-data
python -m research.news_reaction_model.v8.run_train
```

Profiling is recommended because replacing sparse TF-IDF bags with a dense
3,072-value tensor changes loader and GPU memory behavior. Using the V7 batch
size without profiling would not be a controlled systems assumption. The
profiler streams its real-data source sample through one bounded 2,048-article
loader instead of issuing concurrent queries at the largest candidate batch
size; only the requested 32,768-article maximum sample is retained in host
memory.

Evaluation:

```powershell
python -m research.news_reaction_model.v8.run_evaluate
```

Evaluation preserves the original V7/V8 position ledger and additionally runs
a selection-only threshold sweep from the same checkpoint. It does not retrain
the model or change the baseline plan:

- `plan_confidence = min(high winning-class probability, low winning-class probability)`
  because both excursion heads determine direction;
- `directional_edge_pct = abs(conservative upside - conservative downside)`;
- an existing non-flat plan is retained only when both configured thresholds
  are met;
- the `(confidence=0, edge=0)` row is checked against the original evaluation
  counts and P&L and the job fails on any drift.

Default grids are confidence `0,0.3,0.4,0.5,0.6,0.7` and directional edge
`0,0.25,0.5,1,2` percentage points. Override them without another inference
implementation:

```powershell
python -m research.news_reaction_model.v8.run_evaluate `
  --confidence-thresholds 0,0.25,0.35,0.45,0.55 `
  --edge-thresholds-pct 0,0.1,0.25,0.5,1
```

New evaluation artifacts are:

- `evaluation_threshold_sweep.csv`: coverage, long/short counts and P&L,
  win rate, profit factor, target-touch rate, mean/median active return, a
  position-level mean-return interval, and an overall article-clustered P&L
  interval for every horizon/rule;
- `evaluation_threshold_sweep_anchor_price.csv`: the same selection grid split
  into the established anchor-price bands;
- `evaluation_predictions.jsonl.gz`: now also records all three head
  confidences, plan confidence, conservative upside/downside, span, and
  directional edge, allowing later offline audits;
- `evaluation_summary.json`: embeds the grid, contracts, rows, baseline audit,
  and the explicit warning that choosing a rule on 2026 is exploratory tuning.

Confidence is not assumed to be calibrated probability of profit. The sweep
tests whether stricter selection improves held-out trade quality. A threshold
chosen using 2026 must be confirmed on a later untouched period before it is
treated as production evidence.

## Comparison contract

V8 uses the same W&B project as V7: `news-reaction-model-v3`. Its default run
name is:

`news-v8-openai-stock-state-d384-l4-b2048`

Compare V8 against V7 on the same held-out 2026 population. The causal stock
state and all forecast/evaluation rules are identical; differences measure the
effect of replacing V7 text features with the OpenAI embedding, subject to
ordinary stochastic training variance.

## Live inference

`LiveFeatureEncoder` requires both inputs explicitly:

- a 3,072-value OpenAI embedding produced with the V8 text contract;
- the unchanged 85-value V7 point-in-time stock-state vector.

It does not silently regenerate TF-IDF, synthesize missing state, or issue an
OpenAI request. Online embedding acquisition belongs to the serving pipeline.
