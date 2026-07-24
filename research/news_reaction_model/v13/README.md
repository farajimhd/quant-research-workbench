# News Reaction Model V13

V13 is a controlled fusion ablation derived from V12. It keeps V12's prepared
data, 11-value causal exchange-time vector without input LayerNorm, modality
projections, horizon-conditioned residual encoder, targets, loss, shuffling,
chronological split, optimizer, 50-epoch scheduler, checkpointing, evaluation,
and W&B project unchanged.

Its experimental change is narrow: V12's single shared scalar modality gate is
replaced by one six-head cross-attention layer. Ten learned queries—one for each
forecast horizon—attend independently to the projected text, point-in-time
stock-state, and publication-time tokens. The attention uses the existing
learned modality projections directly as keys and values instead of adding
another full set of query/key/value matrices. This keeps the default model
within roughly 0.13 percent of V12's parameter count while allowing short and
long horizons to use different modality mixtures.

V13 retains the five fundamental V10 training corrections:

1. checkpoints restore the exact model, optimizer, scheduler, scaler, RNG,
   training cursor, rolling metrics, epoch metrics, and best-checkpoint state;
2. training uses bounded deterministic article-level shuffling, not only a
   shuffled month order;
3. every available horizon contributes equally to the optimized loss;
4. rolling and full-epoch metrics are aggregated across every contributing
   batch, while validation reports both horizon-macro and label-micro metrics;
5. the model receives explicit causal exchange-session and publication-time
   features.

V13 checkpoints are intentionally architecture- and resume-incompatible with
V12 because the fusion parameter graph differs. Use a new V13 run; do not
resume a V12 checkpoint.

V13 deliberately reads the completed V8 prepared table. There is no V13
preparation job and no duplicate feature matrix.

## Opportunity target

Each valid article/ticker/horizon receives exactly one class:

1. `no_meaningful_opportunity`
2. `upside_dominant`
3. `downside_dominant`

For each label:

```text
upside_pct   = max(actual_high_return, 0) * 100
downside_pct = max(-actual_low_return, 0) * 100
span_pct     = upside_pct + downside_pct
```

`no_meaningful_opportunity` is assigned when the span is no larger than the
unchanged V8 flat-span width for that horizon:

| Horizon | Minimum span |
| --- | ---: |
| 1m | 0.10% |
| 5m, 10m | 0.20% |
| 30m, 1h | 0.50% |
| 2h, 3h, session closes | 1.00% |

For every move larger than the no-opportunity threshold, the larger absolute
excursion determines upside or downside dominance. A two-sided move is not a
separate class. An exact non-zero tie abstains as no opportunity rather than
inventing a direction; discrete prices make this a real boundary rather than a
purely theoretical floating-point case. The complete versioned contract is in
`opportunity.py`.

This rule is intentionally fixed before V13 training. It is not tuned against
the 2026 evaluation period.

## Model

```text
OpenAI text-embedding-3-large (3,072)
    -> unchanged V8 text projection

Point-in-time stock state (85)
    -> unchanged V8 state projection

Causal publication time (11, already bounded)
    -> Linear(11, d_model)
    -> GELU

10 learned horizon queries
    -> one projection-free 6-head cross-attention layer
       over the 3 already projected modality tokens
    -> concatenate the unchanged 32-value horizon embedding
    -> unchanged horizon-conditioned residual MLP
    -> one three-logit opportunity head per horizon
```

This is cross-attention over modalities, not over words. The OpenAI embedding
remains one compressed article vector, so V13 tests whether horizon-specific
feature fusion is better than V12's shared fusion; it does not claim to recover
token-level language structure.

The objective is the arithmetic mean of per-horizon cross-entropy means in each
batch. This prevents horizons with more available labels from owning the
gradient. Validation reports per-horizon metrics, label-micro aggregate
metrics, and horizon-macro accuracy, balanced accuracy, macro-F1, and log loss.
`val/loss` is the horizon-macro log loss and is the best-checkpoint authority.

The time channel is strictly causal and contains:

- premarket, regular, after-hours, or closed-session one-hot state;
- cyclic exchange-local minute and weekday;
- scaled time to 09:30, 16:00, and 20:00 New York.

It deliberately excludes ticker, issuer, year, and month.

## Training and resume semantics

Training reads one canonical source order and shuffles bounded 32,768-article
buffers deterministically for each epoch. The buffer bounds memory while
mixing articles across adjacent source batches. A mid-epoch resume reconstructs
the same epoch permutation and skips only complete, durably committed batches.

A resume is rejected if its dataset, range, population size, batch size,
shuffle buffer, seed, model, optimizer, scheduler, epochs, or sample cap differs
from the current run. This is intentional: changing any of these would no
longer be an exact continuation.

`checkpoint_best_train.pt` is no longer written from a single optimization
batch. The authoritative model is `checkpoint_best_val.pt`.

## Position and P&L diagnostic

Evaluation opens:

- one-share long for predicted `upside_dominant`;
- one-share short for predicted `downside_dominant`;
- no position for predicted `no_meaningful_opportunity`.

At the user's request, the descriptive P&L proxy is:

```text
midpoint_return = (actual_high_return + actual_low_return) / 2
gross P&L       = position * anchor_price * midpoint_return
```

This is not a deployable exit rule. The model predicts no target price, and the
proxy uses realized label extrema, ignores their ordering, ignores costs, and
evaluates every horizon independently. Its purpose is to determine whether the
three-class task learns economically useful direction.

Evaluation writes:

- `evaluation_summary.json`
- `evaluation_positions.csv`
- `evaluation_anchor_price_pnl.csv`
- `evaluation_predictions.jsonl.gz`

## Data

V13 reuses:

```text
market_sip_compact.news_reaction_openai_stock_state_dataset_v8
dataset_version = news_reaction_openai_stock_state_dataset_v8
```

Default split:

- train: `2019-01-01` through `2025-12-31`
- validation/evaluation: `2026-01-01` through `2026-12-31`

## Commands

Optional model/batch profiler:

```powershell
python -m research.news_reaction_model.v13.run_profile_sizes --real-data
```

The V13 profiler defaults to widths `96,192,288,384`, all divisible by the
six attention heads. A custom width must likewise be divisible by
`--attention-heads`.

Corrected training:

```powershell
python -m research.news_reaction_model.v13.run_train
```

The launcher now uses the aligned 50-epoch schedule: initial learning rate
`3e-4`, one cosine cycle per epoch, `0.98` peak decay after each cycle, and
`1e-6` minimum learning rate. It writes a new V13 run and leaves V12 intact as
the direct comparison baseline. Both versions log to
`news-reaction-model-v3`.

Equivalent explicit command:

```powershell
python -m research.news_reaction_model.v13.run_train --d-model 384 --attention-heads 6 --epochs 50 --learning-rate 3e-4 --scheduler cosine --scheduler-restarts 49 --scheduler-cycle-decay 0.98 --scheduler-eta-min 1e-6 --shuffle-buffer-articles 32768 --run-name news-v13-opportunity-horizon-attention-d384-a6-l4-b2048-e50-cosine-r49-gamma098
```

To continue an interrupted corrected run, point to that same run's latest
checkpoint and keep every training argument unchanged:

```powershell
python -m research.news_reaction_model.v13.run_train --resume-checkpoint D:\TradingML\runtimes\news-reaction-model\v13\train\news-v13-opportunity-horizon-attention-d384-a6-l4-b2048-e50-cosine-r49-gamma098\checkpoints\checkpoint_latest.pt
```

The best validation-log-loss checkpoint is evaluated automatically after
training. To rerun evaluation:

```powershell
python -m research.news_reaction_model.v13.run_evaluate
```

To compare the best checkpoint on the complete dropout-disabled 2019-2025
training population and the complete 2026 validation population:

```powershell
python -m research.news_reaction_model.v13.run_fit_diagnostic
```

This writes `fit_diagnostic_summary.json` and
`fit_diagnostic_metrics.csv` beside the training run. It is the authoritative
train-versus-validation comparison. The `train/accuracy` printed during
ordinary training is only the current dropout-enabled optimization batch and is
not a complete training-set evaluation.

To test whether the V13 architecture and unchanged optimizer can memorize one
fixed label population:

```powershell
python -m research.news_reaction_model.v13.run_memorization_test
```

The memorization diagnostic selects 10,000 training articles by a deterministic
identity hash, initializes a fresh model with the architecture stored in the
reference checkpoint, trains and evaluates on that exact same subset, and stops
when eval-mode accuracy reaches 99 percent or after 100 epochs. It never loads
the reference model weights. Its JSONL curve and final JSON summary are written
under the run's `memorization_test` directory.

Focused tests:

```powershell
python -m unittest research.news_reaction_model.v13.test_news_reaction_model_v13 -v
```
