# News Reaction Model V5: sparse lexical ablation

V5 tests whether exact lexical evidence is more predictive than the frozen Qwen
0.6B embeddings used by V4. It keeps V4's horizon-specific ending/high/low
range heads, labels, ordinal classification loss, chronological split,
inference policy, evaluation, scheduler, and W&B project. Only the article
input adapter changes.

## Final design

Each article has two sparse channels:

- word unigrams and bigrams;
- character-within-word 3-5 grams.

Preparation streams the 2019-2025 training population in bounded batches. A
stateless hashing analyzer maps n-grams into 1,048,576 candidate buckets. The
65,536 buckets with the greatest training document frequency are retained for
each channel, and their smoothed IDF weights are frozen. Hashing keeps the
vocabulary build bounded; the large candidate space keeps collisions low.

Every materialized article stores only selected nonzero feature IDs and TF-IDF
weights:

```text
word_ids       [81, 417, 9201, ...]
word_weights   [0.13, 0.42, 0.08, ...]
char_ids       [12, 991, 4410, ...]
char_weights   [0.17, 0.11, 0.31, ...]
```

The model consumes each channel with a supervised weighted `EmbeddingBag`:

```text
sparse word TF-IDF -> weighted word EmbeddingBag ----┐
                                                     ├-> V4 gated pooling
sparse char TF-IDF -> weighted char EmbeddingBag ----┘
                                                          |
                                             V4 residual encoder
                                                          |
                                      unchanged ending/high/low heads
```

The embedding tables learn directly which lexical features correlate with
future reactions. There is no SVD, dense 65K histogram, or unsupervised semantic
compression.

## Why the original SVD implementation was removed

The first V5 implementation built two corpus-wide sparse matrices of roughly
486K articles by 65,536 features and requested 1,024 randomized-SVD components.
That was mathematically valid as latent semantic analysis but operationally
wrong for this baseline: it was CPU-bound, created multi-gigabyte intermediate
matrices, repeated the work for character n-grams, provided no progress inside
SVD, and introduced another learned semantic compression layer.

The replacement has memory proportional to one source batch plus two bounded
document-frequency arrays. Feature fitting reports batch, article, and active
bucket progress continuously.

## Comparison contract

| Concern | V4 | V5 |
|---|---|---|
| Article/ticker population | `news_reaction_percentage_dataset_v4` | exact same rows |
| Train / validation | 2019-2025 / 2026 | unchanged |
| Ending/high/low heads | percentage-range classifiers | unchanged |
| Labels, bins, and loss | V4 contract | unchanged |
| Position and exit evaluation | V4 dominant-excursion policy | unchanged |
| Scheduler | 15 epochs, cosine, 3 restarts | unchanged |
| W&B project | `news-reaction-model-v3` | unchanged |
| Input | frozen Qwen vectors | sparse lexical TF-IDF + supervised adapters |

V5 is V4-like rather than parameter-count identical: two 65,536-by-384 lexical
embedding tables add supervised input capacity. Results must therefore be
interpreted as an end-to-end lexical-model comparison, not a frozen-feature
linear probe.

## Data products

- Table: `market_sip_compact.news_reaction_sparse_tfidf_dataset_v5`
- Feature bundle: `D:\market-data\prepared\news_reaction_model\v5\sparse_tfidf_v2`
- Training run: `news-v5-tfidf-d384-l4-b2048`
- W&B project: `news-reaction-model-v3`

The feature bundle records selected hash buckets, frozen IDF values, corpus
size, source version, train/validation ranges, and a SHA-256 checksum.
Preparation rejects partial refits, mixed representation checksums, invalid
sparse IDs/weights, duplicate identities, and population differences from V4.

## Workstation commands

Stop any preparation process started from the removed SVD implementation, then
run the updated preparation from the workstation runtime:

```powershell
cd D:\TradingML\codes\news-reaction-model\v5
python -m research.news_reaction_model.v5.run_prepare_data --execute
```

After preparation passes its final audit:

```powershell
python -m research.news_reaction_model.v5.run_train
```

Optional commands:

```powershell
python -m research.news_reaction_model.v5.run_profile_sizes --real-data
python -m research.news_reaction_model.v5.run_evaluate
```

An intentional vocabulary/IDF refit is a full-range rebuild:

```powershell
python -m research.news_reaction_model.v5.run_prepare_data --execute --refit-features --rebuild
```

Preparation requires scikit-learn, SciPy, and joblib. No `.env` file belongs in
the runtime or artifacts; normal environment discovery supplies credentials.
