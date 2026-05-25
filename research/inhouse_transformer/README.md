# In-House 1m Bar Transformer Baseline

This module trains a scratch PyTorch transformer baseline on provider-built 1-minute bars.

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

The transformer input `values` are built from raw actual bar/quote columns, not return-encoded engineered features:

```text
open, high, low, close, volume, transactions, spread_bps,
quote_bid_size, quote_ask_size, quoted_share_depth,
quote_imbalance, quote_valid_ratio
```

Before the tensor is fed to the model, each raw input column is normalized causally with only per-window z-score: `(value - context_column_mean) / context_column_std`. No return encoding, log transform, clipping/scaling, or train-wide statistics are applied to input values.

The main transformer defaults to `--target-mode actual_price_zscore`. Targets are the next `horizon` OHLC candles, encoded as actual future prices z-scored by each context window's actual OHLC mean and standard deviation:

```text
(future_open - context_price_mean) / context_price_std
(future_high - context_price_mean) / context_price_std
(future_low - context_price_mean) / context_price_std
(future_close - context_price_mean) / context_price_std
```

Reported metrics are converted back to bps versus the current close. Direction accuracy is a reporting-only metric: for `horizon=1`, it checks whether `predicted_next_close - current_close` has the same sign as `actual_next_close - current_close`. Use `--target-mode return_bps` for the older return-target behavior.

The model applies attention across features inside each bar, then attention across bars in the context window. Relative context position and market time-of-day features are included in the token embedding.

Default model size:

```text
d_model=256
num_heads=8
ff_dim=1024
temporal_layers=6
feature_attention_layers=1
```

By default the loader carries the last context bars across sessions, but does not let targets cross a session boundary.

The default objective is Smooth L1 loss on the multi-horizon OHLC targets only. Direction is not part of the default training objective. The direction head exists for experiments and an auxiliary BCE direction loss can be enabled with `--direction-loss-weight`.

The default learning-rate scheduler is `ReduceLROnPlateau` on validation loss. After warmup, it reduces the LR by `--lr-plateau-factor` when validation loss has not improved for `--lr-plateau-patience` eval points. Use `--lr-scheduler cosine` or `--lr-scheduler constant` for the older behaviors.

Validation and test evaluation use the same AMP setting as training and stream partial progress every `--eval-progress-batches` batches, default `5`, to both console and W&B. Set `--eval-progress-batches 0` to disable partial eval logs. W&B keeps the full metric names from `metrics.jsonl` and also logs short aliases such as `validation/h1_dir`, `validation/h1_mae_bps`, and `validation/h1_edge_bps`; the W&B x-axis metric is `train_step`.

When `--overfit-batches` is used, the script logs `overfit_cache_predictions/*` W&B charts after the final test pass. It selects three tickers from the cached training batches and overlays predicted h1 close against target h1 close for each ticker, plus a table with the underlying values.

Run a small dry run:

```powershell
python research\inhouse_transformer\train.py --dry-run --count-coverage --tickers USO --batch-size 128 --max-batches-per-session 2
```

Run the default experiment:

```powershell
python research\inhouse_transformer\train.py --device cuda --batch-size 1024 --epochs 1
```

Run the main transformer one-session overfit test with wandb logging:

```powershell
python research\inhouse_transformer\train.py --device cuda --overfit-session 2024-01-22 --target-columns close --horizon 1 --batch-size 1024 --epochs 200 --eval-steps 25 --logging-steps 25 --validation-window-count 8192 --test-window-count 8192 --warmup-steps 0 --wandb-entity mehdifaraji --wandb-project May2026-1m-timeseries-forecasting
```

The default W&B run name includes `raw-window-zscore-only` so it can be compared directly against earlier raw-input mixed-normalization runs.

Run the flat MLP overfit sanity test:

```powershell
python research\inhouse_transformer\train_mlp.py --device cuda --tickers USO --train-start-date 2024-01-22 --train-end-date 2024-01-22 --validation-start-date 2026-01-02 --validation-end-date 2026-01-02 --batch-size 256 --overfit-batches 4 --epochs 50 --eval-steps 25
```

The MLP script flattens `[context, features + time_features]` directly to the multi-horizon OHLC target. It is intended as a basic learning-control path: on a cached small sample, train loss should fall quickly.

Run the actual-value LSTM sanity test:

```powershell
python research\inhouse_transformer\train_lstm.py --device cuda --tickers USO --train-start-date 2024-01-22 --train-end-date 2024-01-22 --validation-start-date 2026-01-02 --validation-end-date 2026-01-02 --test-start-date 2026-03-02 --test-end-date 2026-03-02 --batch-size 256 --target-column close --horizon 1 --hidden-size 32 --layers 1 --overfit-batches 8 --epochs 200 --eval-steps 25 --allow-target-across-session
```

The LSTM script follows the Keras weather example shape more closely: actual OHLC/volume/quote values are normalized while each window is fed, concatenated with time features, and passed as `[batch, context, features]`. In default window mode, actual price columns and the actual price target are z-scored from the context window price mean and standard deviation. The default target is the next actual close price, with metrics reported back in bps versus current close and a naive current-close forecast. Add `--normalization-mode train_split` to compute Keras-style global train statistics before training.

Full coverage counting is disabled by default to avoid a complete pre-training pass over the train set. Add `--count-coverage` only when you want an exact window/batch count before training starts. Without `--max-steps`, training stops when the streamed dataset exhausts the configured epochs.

Artifacts are written under:

```text
D:\TradingData\quant-research-workbench\market_data\models\inhouse_transformer
```
