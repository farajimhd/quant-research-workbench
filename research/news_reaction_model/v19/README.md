# News Reaction Model V19

V19 is a model-only redesign over the completed V18.3 single-ticker episode
dataset. It does not build, copy, mutate, or query any source data. The V18
arrays and V15 representations remain the only authorities.

## Objective

V19 addresses defects observed in the completed V18 validation run:

- path prediction collapsed to 6 spike-fade and 4 flush-recovery predictions;
- supply-dominant flow was substantially under-predicted;
- direction, regression, path, and flow peaked at incompatible epochs;
- scalar channel pooling discarded simultaneous text, stock, time, and episode
  information;
- under-$1 and premarket rows had materially larger errors;
- independent high, low, and terminal predictions were not structurally
  constrained.

V19 retains the exact V18 direction, path, flow, and high/low/terminal target
contract. It does not claim that the variable response boundary has been
redesigned.

## Read-only source contract

V19 opens:

```text
D:\market-data\prepared\news_reaction_model\v18\single_ticker_episodes_v1
D:\market-data\prepared\news_reaction_model\v15\causal_context_v1
```

The required V18 dataset version is:

```text
news_reaction_single_ticker_episode_dataset_v18_3
```

There is deliberately no `run_prepare_data.py` in V19. A V19 run manifest
records `source_dataset_write_authority=false`.

Runtime-only features are derived from existing arrays:

- `log1p(anchor_price)`;
- one of five anchor-price regimes: under $1, $1-$5, $5-$10, $10-$20, or a
  $20+ follow-up;
- one of four existing exchange publication sessions;
- fraction of the eight prior-node slots currently populated.

None of these features is persisted back to the dataset.

## Architecture

The default model has 5,847,045 parameters and uses:

- `d_model=384`;
- two transformer encoder layers;
- six attention heads;
- feed-forward width 1,152;
- four task-specific residual towers;
- dropout 0.10.

The transformer receives:

```text
[CURRENT query]
[OpenAI text]
[point-in-time stock state]
[exchange time]
[current episode state]
[price/session/context regime]
[up to eight prior episode nodes]
```

Prior nodes retain V18's causal masks and completed-prior-response contract.
Missing prior nodes are transformer padding.

Direction, flow, and regression use separate task towers. Path is conditioned
on predicted direction probabilities and normalized predicted terminal,
upper-gap, and lower-gap components. Those conditioning values are detached,
so path loss cannot distort the direction or regression heads.

Regression is coherent by construction:

```text
terminal = predicted terminal component
high     = terminal + nonnegative upper gap
low      = terminal - nonnegative lower gap
```

Therefore every prediction satisfies:

```text
low <= terminal <= high
```

Regression components use training-only robust scales for each price-regime
and publication-session cell. Cells with fewer than 128 training rows use the
global scale. Predictions are converted back to actual percentage points
before metrics or evaluation are calculated.

## Balanced targets

Direction, path, and flow weights are fit only from 2019-2025 rows using the
effective-number formula with:

```text
beta=0.9999
minimum weight=0.25
maximum weight=4.0
```

The exact counts, weights, regression scales, and training medians are saved in
the run manifest, checkpoint, and `artifacts/training_statistics.json`.

## Three-stage training

One invocation performs all stages.

### 1. Joint representation

The transformer and all towers train together for 12 epochs by default.
Gradient cosine similarity between all four objectives is recorded on the
first batch of each of the first two epochs. The best shared checkpoint uses a
composite of direction, path, and flow macro-F1 plus regression MAE skill
against the training-median baseline. Collapsed spike-fade, flush-recovery, or
supply-dominant recall penalizes checkpoint selection.

### 2. Head specialization

The best shared transformer is frozen. Direction, flow, and regression towers
train for eight additional epochs. Each tower is selected independently using
its own validation metric. The corresponding tower from the selected joint
checkpoint is the baseline candidate; specialization replaces it only when its
validation score is strictly better.

Frozen transformer and tower modules remain in evaluation mode so dropout
cannot move the feature distribution during specialization.

### 3. Path specialization

The selected direction, flow, and regression towers are frozen. The
direction-and-excursion-conditioned path tower trains for 15 epochs and is
selected by path macro-F1 with explicit minority-class recall gates. Its
pre-specialization state under the assembled upstream towers is retained as the
baseline candidate.

`latest.pt` is the resume authority. `best_val.pt` is the final assembled
inference model containing the best shared representation and independently
selected towers. After assembly, the restored validation metrics are logged as
the final W&B step so the run summary describes `best_val.pt`, not merely the
last optimization epoch.

Each epoch uses an epoch-local cosine schedule. The peak learning rate decays
by 0.98 between epochs.

## Train

No preparation command is required:

```powershell
python -m research.news_reaction_model.v19.run_train
```

The launcher prints the underlying command. Defaults are:

```text
batch size                 2048
joint epochs               12
specialization epochs       8
path epochs                15
joint learning rate       3e-4
specialization rate       1e-4
W&B project               news-reaction-model-v3
```

Resume an interrupted run from `latest.pt` using the same configuration:

```powershell
python -m research.news_reaction_model.v19.run_train --resume <latest.pt>
```

## Profile

The profiler also reads V18 arrays without mutation:

```powershell
python -m research.news_reaction_model.v19.run_profile_sizes
```

It tests transformer widths, layers, and batches, and reports parameters,
throughput, elapsed time, and peak GPU allocation. It keeps six attention
heads when the requested width permits it and otherwise selects the first
compatible count from eight, four, two, or one; every result records the
resolved count.

## Evaluation

Training automatically evaluates the assembled `best_val.pt`. It reports:

- overall accuracy, balanced accuracy, and macro-F1;
- per-class support, prediction count, precision, recall, and F1;
- high, low, and terminal MAE/RMSE;
- regression skill against training-median baselines;
- regression coherence violations;
- singleton, multi-root, and multi-follow-up cohorts;
- current node role and episode root family;
- exchange publication session;
- anchor-price bucket;
- response-duration bucket;
- direction confidence/coverage sweep;
- descriptive terminal P&L for all directional predictions and separately for
  predicted sustained paths.

To rerun:

```powershell
python -m research.news_reaction_model.v19.run_evaluate --checkpoint <best_val.pt>
```

P&L remains descriptive. It excludes costs, fills, overlap, capital, and a
complete execution policy. It is not the checkpoint-selection authority.

## Deliberately deferred

The current V18 arrays do not contain the following, so V19 does not pretend to
implement them:

- granular semantic event and financial-quantity features;
- new pre-publication intraday price, volume, volatility, spread, or liquidity;
- standardized fixed-horizon or censor-aware targets;
- market-wide attention;
- new ClickHouse event reads.

Those require a separately versioned dataset experiment.
