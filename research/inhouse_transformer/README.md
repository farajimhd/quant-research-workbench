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

Targets are the next `horizon` OHLC candles, encoded as log-return basis points from the current close:

```text
log(future_open / current_close) * 10000
log(future_high / current_close) * 10000
log(future_low / current_close) * 10000
log(future_close / current_close) * 10000
```

The model applies attention across features inside each bar, then attention across bars in the context window. Relative context position and market time-of-day features are included in the token embedding.

By default the loader carries the last context bars across sessions, but does not let targets cross a session boundary.

The default objective is Smooth L1 loss on the multi-horizon OHLC return targets only. The direction head is still reported through prediction-sign metrics, and an auxiliary BCE direction loss can be re-enabled with `--direction-loss-weight`.

Run a small dry run:

```powershell
python research\inhouse_transformer\train.py --dry-run --count-coverage --tickers USO --batch-size 128 --max-batches-per-session 2
```

Run the default experiment:

```powershell
python research\inhouse_transformer\train.py --device cuda --batch-size 1024 --epochs 1
```

Full coverage counting is disabled by default to avoid a complete pre-training pass over the train set. Add `--count-coverage` only when you want an exact window/batch count before training starts. Without `--max-steps`, training stops when the streamed dataset exhausts the configured epochs.

Artifacts are written under:

```text
D:\TradingData\quant-research-workbench\market_data\models\inhouse_transformer
```
