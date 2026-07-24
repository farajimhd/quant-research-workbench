# News Reaction Model V9

V9 is a controlled learning-task ablation over V8. It keeps the complete V8
input representation, encoder, chronological split, capacity, optimizer,
scheduler, W&B project, checkpointing, and artifact workflow. It removes all 30
ending/high/low range heads and replaces them with one four-class opportunity
head for each horizon.

V9 deliberately reads the completed V8 prepared table. There is no V9
preparation job and no duplicate feature matrix.

## Opportunity target

Each valid article/ticker/horizon receives exactly one class:

1. `no_meaningful_opportunity`
2. `upside_dominant`
3. `downside_dominant`
4. `two_sided_ambiguous`

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

When both sides move and the larger excursion is less than 1.25 times the
smaller excursion, the label is `two_sided_ambiguous`. Otherwise the larger
absolute excursion determines upside or downside dominance. The complete
versioned contract is in `opportunity.py`.

This rule is intentionally fixed before V9 training. It is not tuned against
the 2026 evaluation period.

## Model

```text
OpenAI text-embedding-3-large (3,072)
    -> unchanged V8 text projection

Point-in-time stock state (85)
    -> unchanged V8 state projection

unchanged V8 gated fusion
    -> unchanged horizon-conditioned residual MLP
    -> one four-logit opportunity head per horizon
```

The objective is sample-weighted cross entropy across all valid
article/horizon labels. Validation reports accuracy, macro-F1, balanced
accuracy, log loss, and mean winning-class confidence for every horizon and in
aggregate.

## Position and P&L diagnostic

Evaluation opens:

- one-share long for predicted `upside_dominant`;
- one-share short for predicted `downside_dominant`;
- no position for the other two classes.

At the user's request, the descriptive P&L proxy is:

```text
midpoint_return = (actual_high_return + actual_low_return) / 2
gross P&L       = position * anchor_price * midpoint_return
```

This is not a deployable exit rule. The model predicts no target price, and the
proxy uses realized label extrema, ignores their ordering, ignores costs, and
evaluates every horizon independently. Its purpose is to determine whether the
four-class task learns economically useful direction.

Evaluation writes:

- `evaluation_summary.json`
- `evaluation_positions.csv`
- `evaluation_anchor_price_pnl.csv`
- `evaluation_predictions.jsonl.gz`

## Data

V9 reuses:

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
python -m research.news_reaction_model.v9.run_profile_sizes --real-data
```

Training:

```powershell
python -m research.news_reaction_model.v9.run_train
```

The best validation-log-loss checkpoint is evaluated automatically after
training. To rerun evaluation:

```powershell
python -m research.news_reaction_model.v9.run_evaluate
```

Focused tests:

```powershell
python -m unittest research.news_reaction_model.v9.test_news_reaction_model_v9 -v
```
