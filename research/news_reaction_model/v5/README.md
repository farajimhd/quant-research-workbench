# News Reaction Model V5: lexical representation ablation

V5 tests whether the frozen Qwen 0.6B embeddings are the limiting factor in the
news-reaction models. It replaces only the input representation with frozen
word and character TF-IDF features. The V4 model, heads, targets, range bins,
losses, inference policy, chronological split, evaluation, scheduler, and W&B
project remain unchanged.

This is a controlled representation ablation, not a new trading policy.

## Research decision

The earlier model may discard wording that matters to market reactions because
the source text is compressed through a small frozen language model, two
1,024-token chunks, last-token pooling, and then a learned 384-wide encoder.
The following practical baselines were considered:

1. Word and character TF-IDF n-grams. This is the selected baseline because it
   preserves exact phrases, spelling variants, numbers, and short financial
   constructions without another pretrained semantic model.
2. A larger Qwen embedding model. This tests model capacity but is slower and
   less diagnostic because it keeps the same embedding family.
3. Finance-tuned retrieval embeddings such as FinE5. These add financial-domain
   semantics but may optimize retrieval rather than reaction forecasting.
4. Modern general embedding models such as Jina Embeddings v3, BGE-M3, and
   ModernBERT-derived encoders. They are useful later comparisons, but they add
   more pretrained-model assumptions than a lexical baseline.

Raw sparse TF-IDF cannot enter V4's fixed `[B, 2, 1024]` interface. V5 therefore
uses train-only truncated SVD as an unsupervised representation adapter:

- channel 0: word unigrams/bigrams -> TF-IDF -> 1,024-value LSA vector;
- channel 1: character-within-word 3-5 grams -> TF-IDF -> 1,024-value LSA vector.

Both channels are L2-normalized. The vectorizers and SVD transforms are fitted
only on 2019-2025 articles, frozen, checksummed, and then applied unchanged to
both training and 2026 validation. No reaction label or post-publication price
is available to the feature fit.

## Fair-comparison contract

| Concern | V4 | V5 |
|---|---|---|
| Article/ticker population | `news_reaction_percentage_dataset_v4` | exactly the same rows |
| Train / validation | 2019-2025 / 2026 | unchanged |
| Input tensor | `[B, 2, 1024]` | unchanged |
| Encoder and all heads | V4 range classifier | byte-for-byte shape equivalent |
| Ending/high/low range labels | V4 bins | unchanged |
| Loss and weights | V4 ordinal classification | unchanged |
| Position and exit evaluation | V4 dominant-excursion policy | unchanged |
| Scheduler | cosine, 3 restarts, 15 epochs | unchanged |
| W&B project | `news-reaction-model-v3` | unchanged |
| Representation | frozen Qwen embeddings | frozen word/char TF-IDF + LSA |

V5's preparation refuses partial feature refits, mixed representation
checksums, invalid vector dimensions, missing labels, duplicate article IDs, or
a population that differs from V4.

## Data products

- ClickHouse table: `market_sip_compact.news_reaction_tfidf_dataset_v5`
- Feature bundle: `D:\market-data\prepared\news_reaction_model\v5\tfidf_word_char_lsa_v1`
- Default training run: `news-v5-tfidf-d384-l4-b2048`
- W&B project: `news-reaction-model-v3`

The feature bundle contains the fitted vectorizers/SVD transforms plus a
manifest recording the training range, source version, configuration, and
SHA-256 checksum. It is a required reproducibility artifact.

## Workstation commands

Run from the V5 workstation runtime:

```powershell
cd D:\TradingML\codes\news-reaction-model\v5

# One-time resumable preparation. Fits the feature bundle if it is absent,
# materializes the V5 table, and audits exact parity with V4.
python -m research.news_reaction_model.v5.run_prepare_data --execute

# Optional: profile model and batch sizes against prepared V5 data.
python -m research.news_reaction_model.v5.run_profile_sizes --real-data

# Fair V4-vs-V5 training run.
python -m research.news_reaction_model.v5.run_train

# Evaluate the best validation checkpoint with the same position/P&L policy.
python -m research.news_reaction_model.v5.run_evaluate
```

An intentional feature refit is a full-dataset operation:

```powershell
python -m research.news_reaction_model.v5.run_prepare_data --execute --refit-features --rebuild
```

Do not use `--refit-features` for a partial date range. Preparation requires
`scikit-learn`, `scipy`, and `joblib`; training uses the same PyTorch/W&B stack
as V4.

## Package map

- `text_features.py`: publication-time text contract, TF-IDF/LSA fit, transform,
  checksum, and frozen artifact loading.
- `prepare_data.py`: resumable bounded-concurrency materialization and audits.
- `data.py`: V5 table loader with exact two-channel integrity checks.
- `model.py`, `losses.py`, `ranges.py`, `inference.py`: V4-compatible model and
  decision contract.
- `train.py`, `evaluate.py`: reproducible artifacts, W&B logging, checkpoints,
  and held-out 2026 evaluation.
- `run_*.py`: workstation launchers with visible safe defaults.
