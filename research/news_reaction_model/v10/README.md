# News Reaction Model V10

V10 is a controlled learning-task ablation over V9. It keeps the complete V9
input representation, encoder, chronological split, capacity, optimizer,
scheduler, W&B project, checkpointing, and artifact workflow. It removes all 30
ending/high/low range heads and uses one three-class opportunity head for each
horizon. The only V9-to-V10 change is the target space: V10 removes the
two-sided/ambiguous class and assigns every meaningful move to its larger
absolute excursion.

V10 deliberately reads the completed V8 prepared table. There is no V10
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

This rule is intentionally fixed before V10 training. It is not tuned against
the 2026 evaluation period.

## Model

```text
OpenAI text-embedding-3-large (3,072)
    -> unchanged V8 text projection

Point-in-time stock state (85)
    -> unchanged V8 state projection

unchanged V8 gated fusion
    -> unchanged horizon-conditioned residual MLP
    -> one three-logit opportunity head per horizon
```

The objective is sample-weighted cross entropy across all valid
article/horizon labels. Validation reports accuracy, macro-F1, balanced
accuracy, log loss, and mean winning-class confidence for every horizon and in
aggregate.

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

V10 reuses:

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
python -m research.news_reaction_model.v10.run_profile_sizes --real-data
```

Training:

```powershell
python -m research.news_reaction_model.v10.run_train
```

The best validation-log-loss checkpoint is evaluated automatically after
training. To rerun evaluation:

```powershell
python -m research.news_reaction_model.v10.run_evaluate
```

To compare the best checkpoint on the complete dropout-disabled 2019-2025
training population and the complete 2026 validation population:

```powershell
python -m research.news_reaction_model.v10.run_fit_diagnostic
```

This writes `fit_diagnostic_summary.json` and
`fit_diagnostic_metrics.csv` beside the training run. It is the authoritative
train-versus-validation comparison. The `train/accuracy` printed during
ordinary training is only the current dropout-enabled optimization batch and is
not a complete training-set evaluation.

To test whether the unchanged V10 architecture and optimizer can memorize one
fixed label population:

```powershell
python -m research.news_reaction_model.v10.run_memorization_test
```

The memorization diagnostic selects 10,000 training articles by a deterministic
identity hash, initializes a fresh model with the architecture stored in the
reference checkpoint, trains and evaluates on that exact same subset, and stops
when eval-mode accuracy reaches 99 percent or after 100 epochs. It never loads
the reference model weights. Its JSONL curve and final JSON summary are written
under the run's `memorization_test` directory.

Focused tests:

```powershell
python -m unittest research.news_reaction_model.v10.test_news_reaction_model_v10 -v
```
