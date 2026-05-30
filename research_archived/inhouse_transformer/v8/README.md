# In-House 1m Bar Transformer Baseline v8

This module trains a scratch PyTorch transformer baseline on provider-built 1-minute bars.

This folder is a versioned experiment. `../v5` preserves the anchored activity
input experiment with separate time conditioning, and `../v6` preserves the
calendar-as-value-channel ablation. v8 starts from v7 expanded time
conditioning and sets the default model capacity to Option A: Moderate Bigger. The main
value feature count remains unchanged.

The default split is encoded in `config.py`:

- train: `2024-01-22` through `2025-12-31`
- validation: `2026-01-01` through `2026-02-28`
- test: `2026-03-01` through the latest available provider session

The input shape is:

```text
values:        [batch, context_length, feature_count]
time_features: [batch, context_length, time_feature_count]
targets:       [batch, horizon, 4]
```

The transformer input `values` are built from bar/quote columns:

```text
open, high, low, close, volume, transactions, spread_bps,
quote_bid_size, quote_ask_size, quoted_share_depth,
quote_imbalance, quote_valid_ratio
```

Before the tensor is fed to the model, the OHLC input columns are converted to
log-return bps relative to the origin bar's current close:

```text
input_ohlc = 10000 * log(context_price / current_close)
```

Volume, transactions, quote bid size, quote ask size, and quoted share depth are
converted to anchored log ratios against the origin bar:

```text
input_activity = log1p(context_value) - log1p(origin_value)
```

Spread bps, quote imbalance, and quote valid ratio are left in their existing units.

Then every input column is normalized causally with only per-window z-score:
`(value - context_column_mean) / context_column_std`. No train-wide statistics
are applied to input values.

The separate `time_features` tensor includes the v5 cyclical/session features:

```text
minute_sin, minute_cos, regular_position_sin, regular_position_cos,
is_premarket, is_regular, is_afterhours, is_new_session, gap_minutes_clipped
```

v8 adds scaled decoded market-time fields to that same separate tensor:

```text
year_scaled, month_scaled, day_scaled, hour_scaled, minute_scaled,
second_scaled, microsecond_scaled, minute_of_day_scaled,
day_of_year_scaled, day_of_week_scaled
```

These added fields are computed from `bar_time_market`, not UTC, and are not
anchored to the origin bar or per-window z-scored. They enter the model only
through the time projection that is broadcast across feature tokens for each
bar.

The v8 main transformer defaults to `--target-mode return_bps`. Targets are the
next `horizon` OHLC candles encoded as log returns from the current close:

```text
target = 10000 * log(future_price / current_close)
```

This is meant to reduce the price-level shortcut where the model learns to copy
the current/last price action. Metrics also include explicit baselines:

```text
persistence: predicted return = 0
last-move continuation: predicted return = last close return * horizon
mean reversion: predicted return = -last close return * horizon
```

The v1 default was `--target-mode actual_price_zscore`, where targets are the
next `horizon` OHLC candles encoded as actual future prices z-scored by each
context window's actual OHLC mean and standard deviation:

```text
(future_open - context_price_mean) / context_price_std
(future_high - context_price_mean) / context_price_std
(future_low - context_price_mean) / context_price_std
(future_close - context_price_mean) / context_price_std
```

Reported metrics are in bps versus the current close. Direction accuracy is a reporting-only metric: for `horizon=1`, it checks whether the predicted next return has the same sign as the actual next return.

The model applies attention across features inside each bar, then attention across bars in the context window. Relative context position and market time-of-day features are included in the token embedding.

Default model size (Option A: Moderate Bigger):

```text
d_model=384
num_heads=8
ff_dim=1536
temporal_layers=6
feature_attention_layers=1
```

By default the loader carries the last context bars across sessions, but does not let targets cross a session boundary.

The default objective is Smooth L1 loss on the multi-horizon OHLC targets only. Direction is not part of the default training objective. The direction head exists for experiments and an auxiliary BCE direction loss can be enabled with `--direction-loss-weight`.

The default `--lr-scheduler auto` uses `CosineAnnealingWarmRestarts` for overfit runs and `ReduceLROnPlateau` for normal training. For overfit, `--cosine-restart-t0-steps 0` resolves to `--eval-steps`, and `--cosine-restart-t-mult` defaults to `2`. For normal training, plateau reduces LR after warmup when validation loss has not improved for `--lr-plateau-patience` eval points. Use `--lr-scheduler cosine`, `--lr-scheduler cosine_warm_restarts`, `--lr-scheduler plateau`, or `--lr-scheduler constant` to force a specific behavior.

Validation and test evaluation use the same AMP setting as training and stream partial progress every `--eval-progress-batches` batches, default `5`, to both console and W&B. Set `--eval-progress-batches 0` to disable partial eval logs. W&B keeps the full metric names from `metrics.jsonl` and also logs short aliases such as `validation/h1_dir`, `validation/h1_mae_bps`, and `validation/h1_edge_bps`; the W&B x-axis metric is `train_step`.

Overfit cache size is fixed by window count, not by batch size. By default,
`--overfit-session` caches `8192` train windows. The deprecated
`--overfit-batches 8` is interpreted as `8 * 1024 = 8192` windows regardless of
the current `--batch-size`. Use `--overfit-window-count` to set the cache size
explicitly. This keeps overfit comparisons fair when batch size changes for VRAM
or performance reasons.

When an overfit cache is used, the script logs `overfit_timeline_predictions/*`
W&B data after the final test pass. It selects three tickers from the cached
training windows, reloads their chronological session data, logs the underlying
rows as W&B tables, and creates W&B line-series plots for predicted h1 close
versus target h1 close.

Run a small dry run:

```powershell
python research\inhouse_transformer\v8\train.py --dry-run --count-coverage --tickers USO --batch-size 128 --max-batches-per-session 2
```

Run the default experiment:

```powershell
python research\inhouse_transformer\v8\train.py --device cuda --batch-size 1024 --epochs 1
```

Run the main transformer one-session overfit test with wandb logging:

```powershell
python research\inhouse_transformer\v8\train.py --device cuda --overfit-session 2024-01-22 --target-columns close --horizon 1 --batch-size 1024 --epochs 200 --eval-steps 25 --logging-steps 25 --validation-window-count 8192 --test-window-count 8192 --warmup-steps 0 --wandb-entity mehdifaraji --wandb-project May2026-1m-timeseries-forecasting
```

The default W&B run name starts with `v8-` and includes
`expanded-time-conditioning-window-zscore` and `return_bps` so it can be
compared directly against v5 and v6.

Run the flat MLP overfit sanity test:

```powershell
python research\inhouse_transformer\v8\train_mlp.py --device cuda --tickers USO --train-start-date 2024-01-22 --train-end-date 2024-01-22 --validation-start-date 2026-01-02 --validation-end-date 2026-01-02 --batch-size 256 --overfit-batches 4 --epochs 50 --eval-steps 25
```

The MLP script flattens `[context, features + time_features]` directly to the multi-horizon OHLC target. It is intended as a basic learning-control path: on a cached small sample, train loss should fall quickly.

Run the actual-value LSTM sanity test:

```powershell
python research\inhouse_transformer\v8\train_lstm.py --device cuda --tickers USO --train-start-date 2024-01-22 --train-end-date 2024-01-22 --validation-start-date 2026-01-02 --validation-end-date 2026-01-02 --test-start-date 2026-03-02 --test-end-date 2026-03-02 --batch-size 256 --target-column close --horizon 1 --hidden-size 32 --layers 1 --overfit-batches 8 --epochs 200 --eval-steps 25 --allow-target-across-session
```

The LSTM script follows the Keras weather example shape more closely: actual OHLC/volume/quote values are normalized while each window is fed, concatenated with time features, and passed as `[batch, context, features]`. In default window mode, actual price columns and the actual price target are z-scored from the context window price mean and standard deviation. The default target is the next actual close price, with metrics reported back in bps versus current close and a naive current-close forecast. Add `--normalization-mode train_split` to compute Keras-style global train statistics before training.

Full coverage counting is disabled by default to avoid a complete pre-training pass over the train set. Add `--count-coverage` only when you want an exact window/batch count before training starts. Without `--max-steps`, training stops when the streamed dataset exhausts the configured epochs.

Artifacts are written under:

```text
D:\TradingData\quant-research-workbench\market_data\models\inhouse_transformer\v8
```
