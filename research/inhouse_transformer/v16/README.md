# In-House 1m Bar Transformer Baseline v16

This module trains a scratch PyTorch transformer baseline on provider-built 1-minute bars.

This folder is a versioned experiment. v16 starts from `../v14` and changes only
the model input fusion. v14 added the projected time features to every market
feature token. v16 makes market features and time features parallel token sets:
each market feature and each time feature is projected from its scalar value,
gets its own feature-id embedding, shares the same context-position embedding,
and then all market/time tokens are concatenated before feature attention.

The default split is encoded in `config.py`:

- train: `2024-01-22` through `2025-12-31`
- validation: `2026-01-01` through `2026-02-28`
- test: `2026-03-01` through the latest available provider session

The input shape is:

```text
values:        [batch, context_length, feature_count]
time_features: [batch, context_length, time_feature_count]
targets:       [batch, horizon, target_count, 13]
target_bps:    [batch, horizon, target_count]
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

v7/v16 add scaled decoded market-time fields to that same separate tensor:

```text
year_scaled, month_scaled, day_scaled, hour_scaled, minute_scaled,
second_scaled, microsecond_scaled, minute_of_day_scaled,
day_of_year_scaled, day_of_week_scaled
```

These added fields are computed from `bar_time_market`, not UTC, and are not
anchored to the origin bar or per-window z-scored. In v16, each time feature is
treated as a separate token with its own time-feature embedding. The market and
time tokens share the same context-position embedding for each bar.

The v16 main transformer defaults to `--target-mode binary_magnitude_bps`.
First, each future OHLC target is converted to log-return bps from the current
close:

```text
target_bps = 10000 * log(future_price / current_close)
```

Then the rounded absolute magnitude is clipped to `0..4095` and represented as
12 little-endian binary bits. The sign bit is `1` for non-negative target bps
and `0` for negative target bps:

```text
encoded_target = [sign_bit, magnitude_bit_0, ..., magnitude_bit_11]
```

The model emits raw logits with shape `[batch, horizon, target_count, 13]`.
Training uses binary cross entropy with logits. Sigmoid is used only for bit
accuracy diagnostics and for decoding predictions back to bps/prices in metrics.

Metrics also include explicit baselines:

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

The model applies attention across concatenated market/time feature tokens
inside each bar, then attention across bars in the context window. Relative
context position is shared by both token families.

Default model size:

```text
d_model=256
num_heads=8
ff_dim=1024
temporal_layers=6
feature_attention_layers=1
```

By default the loader carries the last context bars across sessions, but does not let targets cross a session boundary.

The objective is BCE-with-logits on the encoded multi-horizon OHLC target bits only. The model has no direction head. Direction accuracy is reporting-only and is computed from decoded predicted and actual target moves.

The default `--lr-scheduler auto` uses `CosineAnnealingWarmRestarts` for overfit runs and `ReduceLROnPlateau` for normal training. For overfit, `--cosine-restart-t0-steps 0` resolves to `--eval-steps`, and `--cosine-restart-t-mult` defaults to `2`. For normal training, plateau reduces LR after warmup when validation loss has not improved for `--lr-plateau-patience` eval points. Use `--lr-scheduler cosine`, `--lr-scheduler cosine_warm_restarts`, `--lr-scheduler plateau`, or `--lr-scheduler constant` to force a specific behavior.

Validation and test evaluation use the same AMP setting as training and stream partial progress every `--eval-progress-batches` batches, default `5`, to the console and `metrics.jsonl`. Set `--eval-progress-batches 0` to disable partial eval logs. W&B receives the compact stable aliases from the v14 metric cleanup, such as `validation/h1_expected_signed_mae_bps`, `validation/h1_expected_dir_acc_pct`, and `validation/h1_edge_vs_last_move_naive_bps`; the W&B x-axis metric is `train_step`.

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
python research\inhouse_transformer\v16\train.py --dry-run --count-coverage --tickers USO --batch-size 128 --max-batches-per-session 2
```

Run the default experiment:

```powershell
python research\inhouse_transformer\v16\train.py --device cuda --batch-size 1024 --epochs 1
```

Run the main transformer one-session overfit test with wandb logging:

```powershell
python research\inhouse_transformer\v16\train.py --device cuda --overfit-session 2024-01-22 --batch-size 512 --epochs 200 --eval-steps 25 --logging-steps 25 --validation-window-count 5000 --test-window-count 10000 --allow-target-across-session --wandb-entity mehdifaraji --wandb-project May2026-1m-timeseries-v14-variants
```

The default W&B run name starts with `v16-` and includes
`binary_magnitude_bps` so it can be
compared directly against the v14 baseline and later v14-derived variants.

Run the flat MLP overfit sanity test:

```powershell
python research\inhouse_transformer\v16\train_mlp.py --device cuda --tickers USO --train-start-date 2024-01-22 --train-end-date 2024-01-22 --validation-start-date 2026-01-02 --validation-end-date 2026-01-02 --batch-size 256 --overfit-batches 4 --epochs 50 --eval-steps 25
```

The MLP script flattens `[context, features + time_features]` directly to the multi-horizon OHLC target. It is intended as a basic learning-control path: on a cached small sample, train loss should fall quickly.

Run the actual-value LSTM sanity test:

```powershell
python research\inhouse_transformer\v16\train_lstm.py --device cuda --tickers USO --train-start-date 2024-01-22 --train-end-date 2024-01-22 --validation-start-date 2026-01-02 --validation-end-date 2026-01-02 --test-start-date 2026-03-02 --test-end-date 2026-03-02 --batch-size 256 --target-column close --horizon 1 --hidden-size 32 --layers 1 --overfit-batches 8 --epochs 200 --eval-steps 25 --allow-target-across-session
```

The LSTM script follows the Keras weather example shape more closely: actual OHLC/volume/quote values are normalized while each window is fed, concatenated with time features, and passed as `[batch, context, features]`. In default window mode, actual price columns and the actual price target are z-scored from the context window price mean and standard deviation. The default target is the next actual close price, with metrics reported back in bps versus current close and a naive current-close forecast. Add `--normalization-mode train_split` to compute Keras-style global train statistics before training.

Full coverage counting is disabled by default to avoid a complete pre-training pass over the train set. Add `--count-coverage` only when you want an exact window/batch count before training starts. Without `--max-steps`, training stops when the streamed dataset exhausts the configured epochs.

Artifacts are written under:

```text
D:\TradingData\quant-research-workbench\market_data\models\inhouse_transformer\v16
```
