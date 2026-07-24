# News Reaction Model V14: sparse TF-IDF token transformer

V14 tests whether the weak result from one compressed OpenAI article vector is
a representation bottleneck. It starts from the corrected V10 experiment and
changes the text representation and its fusion only:

- V10's three opportunity classes and exact per-horizon target rules remain;
- the 2019-2025 train and 2026 validation split remains;
- causal stock state and publication-time features remain;
- equal-horizon cross-entropy, deterministic article shuffling, optimizer,
  scheduler, checkpointing, W&B project, and evaluation remain;
- the OpenAI vector is replaced by V7's persisted word TF-IDF, character
  TF-IDF, and financial-number features;
- horizon queries cross-attend to individual bounded sparse features rather
  than to one already-compressed text vector.

This is a representation-and-fusion experiment. It is not a new target,
labeling, split, or trading-policy experiment.

## Source data

V14 reads the completed, checksummed V7 table directly:

```text
market_sip_compact.news_reaction_stock_state_dataset_v7
dataset_version = news_reaction_stock_state_dataset_v7
representation = v6_tfidf_numeric_plus_point_in_time_stock_state_v1
```

No V14 data preparation step is required and no duplicate feature table is
created. The V7 representation contains:

- hashed word 1-2 grams with TF-IDF weights;
- hashed character 3-5 grams with TF-IDF weights;
- typed financial-number context features and 24 numeric summaries;
- the 85-value causal point-in-time stock-state vector;
- publication timestamp, publication session, and reaction targets.

The V5 and V6 checksummed feature bundles remain the live-inference authority.
V14 rejects representation drift rather than silently fitting a new
vocabulary.

## Bounded token contract

The loader keeps the strongest features per article:

| Token family | Default maximum |
|---|---:|
| Word n-grams | 256 |
| Character n-grams | 512 |
| Financial-number contexts | 64 |

Selection is by descending absolute TF-IDF weight. Feature ID is the
deterministic tie-breaker, so resume reconstructs byte-identical batches.
Missing positions use a dedicated padding ID and an explicit attention mask.
No row is dropped when a sparse channel is empty.

Every selected sparse feature becomes:

```text
learned feature embedding
+ learned signed-log TF-IDF weight encoding
+ learned feature-family embedding
```

Numeric summary, stock state, and publication time each become one additional
dense token. With defaults, the maximum memory sequence is 835 tokens:
256 word + 512 character + 64 numeric + 3 dense causal tokens.

Across the completed V7 corpus, these caps retain approximately 92.3% of word,
82.6% of character, and 97.8% of numeric TF-IDF squared-weight mass on average.
The initially considered 128-character cap retained only 59.0% and was rejected
before training.

TF-IDF is unordered. V14 therefore uses no positional embedding and does not
pretend that feature-array order is text order. Word and character n-grams
already retain bounded local language structure.

## Architecture

```text
Top 256 word n-grams ----------\
Top 512 character n-grams ------\
Top 64 financial-number tokens ---+--> feature + weight + type tokens
Numeric summary ------------------+
Causal stock state ---------------+
Causal publication time ---------/
                                      ^
10 learned horizon queries -----------|
                                      |
                         6-head cross-attention
                                      |
                    unchanged V10 horizon embedding
                                      |
                      unchanged V10 residual stack
                                      |
                  one 3-class opportunity head/horizon
```

The attention block is a transformer-style horizon-query cross-attention block
with residual feed-forward processing. It attends over actual sparse lexical
features. This differs from V13, whose attention memory contains only three
already-compressed modality vectors.

The default model has 65,218,830 trainable parameters, primarily in the three
sparse feature embedding tables. This is acceptable on the target 96 GB GPU
but is not parameter-matched to V10/V13. Any improvement establishes the value
of the combined sparse-token representation and attention system; it does not
by itself prove that attention, rather than added lexical capacity, caused it.

## Unchanged target

Every valid article/ticker/horizon receives exactly one V10 class:

1. `no_meaningful_opportunity`
2. `upside_dominant`
3. `downside_dominant`

The no-opportunity threshold and dominant-excursion rules are imported
unchanged from `opportunity.py`. Evaluation goes long for upside, short for
downside, and opens no position for no opportunity. Its midpoint return remains
a descriptive proxy before costs, overlap reconciliation, and risk controls.

## Training

On the workstation, from
`D:\TradingML\codes\news-reaction-model\v14`:

```powershell
python -m research.news_reaction_model.v14.run_profile_sizes --real-data
python -m research.news_reaction_model.v14.run_train
```

Profiling is recommended because token attention has a different memory curve
from V10/V13. The default profiler tests batches from 128 through 4096. The
training launcher initially retains V10's batch 2048 and should be adjusted
only if real-data profiling shows a safer or faster configuration.

The default training command resolves to:

```powershell
python -m research.news_reaction_model.v14.train `
  --train-start 2019-01-01 --train-end-exclusive 2026-01-01 `
  --validation-start 2026-01-01 --validation-end-exclusive 2027-01-01 `
  --batch-size 2048 --loader-workers 2 --prefetch-batches 4 `
  --shuffle-buffer-articles 32768 `
  --max-word-tokens 256 --max-char-tokens 512 --max-numeric-tokens 64 `
  --d-model 384 --hidden-dim 384 --layers 4 --attention-heads 6 `
  --epochs 50 --learning-rate 3e-4 `
  --scheduler cosine --scheduler-restarts 49 `
  --scheduler-cycle-decay 0.98 --scheduler-eta-min 1e-6
```

The W&B project remains `news-reaction-model-v3` for comparison with V10-V13.
Run artifacts, diagrams, metrics, manifests, and checkpoints are written under
one V14 run directory.

Resume:

```powershell
python -m research.news_reaction_model.v14.run_train `
  --resume-checkpoint D:\TradingML\runtimes\news-reaction-model\v14\train\news-v14-opportunity-tfidf-token-transformer-d384-a6-l4-w256-c512-n64-b2048-e50-cosine-r49-gamma098\checkpoints\checkpoint_latest.pt
```

Evaluation:

```powershell
python -m research.news_reaction_model.v14.run_evaluate
```

## Interpretation

Three comparisons matter:

1. V14 versus V10 tests sparse token-level TF-IDF attention against the
   compressed OpenAI vector under the same opportunity task.
2. V14 versus V7/V6 indicates whether the sparse representation remains useful
   after changing to the corrected three-class target.
3. A future parameter-matched TF-IDF `EmbeddingBag` three-class baseline would
   isolate the contribution of token attention from the contribution of
   returning to TF-IDF. V14 alone changes both representation and text fusion,
   so it cannot prove which of those two caused any improvement.
