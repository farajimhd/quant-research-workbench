# News Reaction Model V6: financial-numeric channel ablation

V6 measures whether explicit financial-number structure improves V5's sparse
word/character TF-IDF baseline. It is a controlled ablation: V6 reuses the
exact persisted V5 lexical rows and frozen V5 vectorizers, while retaining the
same article population, reaction targets, chronological split, range classes,
heads, loss, optimizer defaults, 15 epochs, three cosine restarts, evaluation
policy, and W&B project.

The only experimental change is a third publication-time input channel.

## Input contract

For each single-ticker article, V6 consumes:

- V5 word TF-IDF IDs and weights, unchanged;
- V5 character TF-IDF IDs and weights, unchanged;
- typed, hashed financial-number tokens in a 32,768-entry vocabulary, learned
  through a compact 64-dimensional adapter and projected into the V5 width;
- 24 bounded numeric statistics.

The numeric parser recognizes currencies, percentages, basis points,
multiples, magnitude suffixes, explicit signs, years and quarters. It assigns
nearby financial contexts such as EPS, revenue, guidance, margin, price target,
cash flow, debt, shares, contract value, and valuation. It also extracts
comparable relationships such as `from $16 to $18`, `$1.25 beats $0.98`, and
guidance ranges. Sparse features preserve type, context, direction, magnitude
bin, and relationship. Dense features retain bounded counts, magnitude
summaries, percentage extrema, relative changes, and range width.

All inputs are derived only from text available at `published_at_utc`. Price
reaction labels, future prices, and post-publication text are never inputs.

```text
frozen V5 word TF-IDF ----> weighted EmbeddingBag --\
frozen V5 char TF-IDF ----> weighted EmbeddingBag ----> gated channel pooling
numeric typed features ----> weighted EmbeddingBag --/             |
numeric statistics --------> dense projection -------/             v
                                                       horizon fusion + V5 encoder
                                                                  |
                                                  ending / high / low range heads
```

## Representation integrity

Preparation does not refit TF-IDF or create another lexical authority. It reads
`market_sip_compact.news_reaction_sparse_tfidf_dataset_v5`, joins normalized
news text only to calculate numeric features, and writes
`market_sip_compact.news_reaction_numeric_tfidf_dataset_v6`.

The V6 representation SHA combines the frozen V5 bundle SHA with the complete
numeric parser contract and configuration. Training and live inference reject
rows or artifacts with a different SHA. Monthly materialization is bounded,
concurrent, resumable, and verifies exact row/article parity with V5.

Default artifacts:

- V5 lexical bundle: `D:\market-data\prepared\news_reaction_model\v5\sparse_tfidf_v2`
- V6 manifest: `D:\market-data\prepared\news_reaction_model\v6\numeric_tfidf_v1\manifest.json`
- V6 runtime: `D:\TradingML\runtimes\news-reaction-model\v6`
- W&B project: `news-reaction-model-v3`

## Run order

From `D:\TradingML\codes\news-reaction-model\v6` on the workstation:

```powershell
python -m research.news_reaction_model.v6.run_prepare_data --execute
python -m research.news_reaction_model.v6.run_profile_sizes --real-data
python -m research.news_reaction_model.v6.run_train
```

Preparation is expected to be materially cheaper than V5 preparation because
it reuses V5 sparse lexical arrays and computes only the numeric channel. The
profile remains useful because the third EmbeddingBag changes memory and batch
throughput. Use the same model size and batch size as V5 for the strictest
representation comparison; profile results can separately identify a faster
operational batch.

After training:

```powershell
python -m research.news_reaction_model.v6.run_evaluate
```

Compare V5 and V6 on held-out 2026 `val/log_loss`, exact range accuracy,
within-one-bin accuracy, per-horizon metrics, and the unchanged one-share
target-touch evaluation. Parameter count and throughput must be reported beside
predictive results. The numeric adapter is deliberately narrower than the V5
encoder so added capacity stays small while retaining a low-collision numeric
vocabulary.

## Live use

`LiveFeatureEncoder` loads the frozen V5 vectorizers and the V6 manifest once,
then applies lexical and numeric transforms to incoming single-ticker news. It
uses the identical checksummed numeric parser used by dataset preparation.
