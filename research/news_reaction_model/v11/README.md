# News Reaction Model V11

V11 is a controlled time-representation ablation derived from the fixed V10.
It keeps V10's OpenAI embedding, point-in-time stock state, model width,
three-class opportunity heads, target rules, chronological split, balanced
loss, deterministic article shuffling, scheduler, evaluation, and exact-resume
contract. The only model/data change is:

```text
V10 custom publication-time vector (11 values)
    -> V11 packed-market causal V1 timestamp vector (12 values)
```

The shared authority is
`research/mlops/packed_market/time_features.py`. Both packed-market causal V1
and V11 import that contract so their definitions cannot drift.

## Time columns

All values are calculated from the article's `published_at_utc`. Nothing after
publication is read.

| # | Column | Definition |
| ---: | --- | --- |
| 1 | `utc_second_of_day_sin` | sine encoding of UTC second within the day |
| 2 | `utc_second_of_day_cos` | cosine encoding of UTC second within the day |
| 3 | `utc_day_of_week_sin` | sine encoding of UTC weekday, Monday = 0 |
| 4 | `utc_day_of_week_cos` | cosine encoding of UTC weekday |
| 5 | `utc_day_of_year_sin` | sine encoding of zero-based UTC day of year over 366 days |
| 6 | `utc_day_of_year_cos` | cosine encoding of zero-based UTC day of year over 366 days |
| 7 | `years_since_2000` | UTC year minus 2000 plus zero-based day of year divided by 366 |
| 8 | `session_second` | raw second of the New York exchange-local day |
| 9 | `session_progress` | clipped progress from 04:00 to 20:00 New York, in `[0, 1]` |
| 10 | `is_regular_hours` | 1 from 09:30 inclusive to 16:00 exclusive New York |
| 11 | `is_premarket` | 1 from 04:00 inclusive to 09:30 exclusive New York |
| 12 | `is_afterhours` | 1 from 16:00 inclusive to 20:00 exclusive New York |

Outside 04:00-20:00 New York, all three session flags are zero.
`session_second` intentionally remains raw, exactly as in causal V1; the time
projection begins with `LayerNorm`. `years_since_2000` is also intentionally
retained from causal V1. It may encode temporal drift, so V11 remains evaluated
on the untouched chronological 2026 split rather than a random validation set.

## Unchanged V10 task

Each valid article/ticker/horizon receives one class:

1. `no_meaningful_opportunity`
2. `upside_dominant`
3. `downside_dominant`

The no-opportunity thresholds and dominance rules are unchanged from V10.
Likewise, evaluation still opens one-share long for upside, one-share short for
downside, and no position for no opportunity. Its realized-extrema midpoint P&L
is a descriptive comparison proxy, not a deployable execution rule.

The targets are raw, anchor-relative asset returns:

```text
ending_return = ending_price / pre_news_anchor_price - 1
high_return   = highest_price / pre_news_anchor_price - 1
low_return    = lowest_price / pre_news_anchor_price - 1
```

They are not market-adjusted or SPY-relative abnormal returns. V1-V3 used the
separate `abnormal_*_return` columns; V4 changed the experiment family to the
raw `target_return`, `high_return`, and `low_return` columns, and V8 preserved
that contract through V7 for V10 and V11. The 2019-2025 versus 2026 split only
defines training and evaluation time ranges; it does not transform returns.

The midpoint proxy is still not realized trading P&L because it chooses the
midpoint of the observed high and low without an executable exit, ordering,
capital-overlap, spread, slippage, fee, or risk-management contract.

The default split remains:

- training: 2019-01-01 through 2025-12-31;
- validation/evaluation: 2026-01-01 through 2026-12-31.

V11 reads the existing
`market_sip_compact.news_reaction_openai_stock_state_dataset_v8` rows and
derives time features during loading. There is no V11 preparation job and no
duplicate feature matrix.

## Model

```text
OpenAI text embedding (3,072) ─┐
Point-in-time stock state (85) ├─> gated three-channel fusion
Causal V1 time vector (12) ────┘
    -> unchanged horizon-conditioned residual MLP
    -> one three-logit opportunity head per horizon
```

The objective remains the arithmetic mean of per-horizon cross-entropy means,
so horizons with more labels do not dominate the gradient. Validation reports
per-horizon metrics plus label-micro and horizon-macro aggregates.

Operational invariants inherited from fixed V10:

- `val/loss` is the equal-horizon mean log loss and selects the best checkpoint;
- `val/micro_log_loss` is separately weighted by every valid label;
- training logs contain bounded rolling metrics and full-epoch metrics;
- single-batch `best_train` checkpoints are disabled;
- periodic latest/archive checkpoints fire when a sample threshold is crossed,
  rather than requiring a batch boundary to equal the threshold exactly.

## Run

No preparation step is needed. The one-value input-width change does not
justify re-profiling the unchanged model, but the profiler remains available.

```powershell
python -m research.news_reaction_model.v11.run_train
```

The launcher uses the same comparison settings as fixed V10: batch 2,048,
`d_model=384`, four layers, 50 epochs, deterministic 32,768-article shuffle
buffers, initial learning rate `3e-4`, one cosine cycle per epoch, peak decay
`0.98`, and minimum learning rate `1e-6`. It logs to the same W&B project,
`news-reaction-model-v3`, under a V11-specific run name.

To resume, use the latest checkpoint from the same V11 run and do not change
dataset, model, optimizer, scheduler, batch, shuffle, or epoch settings:

```powershell
python -m research.news_reaction_model.v11.run_train --resume-checkpoint D:\TradingML\runtimes\news-reaction-model\v11\train\news-v11-opportunity-openai-stock-state-causalv1-time-d384-l4-b2048-e50-cosine-r49-gamma098\checkpoints\checkpoint_latest.pt
```

Optional commands:

```powershell
python -m research.news_reaction_model.v11.run_profile_sizes --real-data
python -m research.news_reaction_model.v11.run_evaluate
python -m research.news_reaction_model.v11.run_fit_diagnostic
python -m research.news_reaction_model.v11.run_memorization_test
python -m unittest research.news_reaction_model.v11.test_news_reaction_model_v11 -v
```
