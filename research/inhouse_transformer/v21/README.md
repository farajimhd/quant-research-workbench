# In-House Microstructure Transformer v21

v21 is a quote/trade-stream experiment based on the v14 target idea, but it does
not use 1-minute bars.

Each sample is one ticker at origin time `t`.

Input:

```text
one_second_values: [batch, 60, 54]
ten_second_values: [batch, 60, 85]
```

The 1-second branch summarizes the last 60 one-second microstructure snapshots.
Each row includes quote state, spread, depth imbalance, trade pressure, event
positions inside the second, and activity counts.

The 10-second branch summarizes the broader last 10 minutes as 60 ten-second
buckets. Each 10-second row includes an overall summary plus ten internal
1-second slot summaries so the model can see some within-bucket shape.

Target:

```text
targets: [batch, 6, 1, 13]
target_bps: [batch, 6, 1]
```

The six horizons are:

```text
t+10s, t+20s, t+30s, t+40s, t+50s, t+60s
```

For each horizon, v21 predicts future mid-price log-return bps from the current
mid:

```text
future_mid_return_bps = 10000 * log(future_mid[t + k * 10s] / current_mid[t])
```

The target is encoded like v14:

```text
[sign_bit, magnitude_bit_0, ..., magnitude_bit_11]
```

Training uses `BCEWithLogitsLoss`; sigmoid is used for diagnostics and decoding.

Data source:

```text
flatfiles_root/
  quotes_v1/YYYY/MM/YYYY-MM-DD.csv.gz
  trades_v1/YYYY/MM/YYYY-MM-DD.csv.gz
```

The loader also accepts `quotes/` and `trades/` roots and falls back to recursive
date matching when needed. The first pass over a session parses the csv.gz files
with Polars and writes cached one-second snapshots to:

```text
<cache-root>/one_second_snapshots/YYYY/MM/YYYY-MM-DD.parquet
```

For workstation training, put both flatfiles and cache on a local SSD/NVMe when
possible. Google Drive Desktop on HDD is acceptable for packaging, but it will
usually bottleneck parsing and repeated training.

Example workstation command:

```powershell
python research\inhouse_transformer\v21\train.py --flatfiles-root D:\flatfiles\us_stock_sip --cache-root D:\TradingData\quant-research-workbench\market_data\microstructure_cache\v21 --device cuda --batch-size 4096 --num-workers 8 --prefetch-factor 4 --tickers ALL --wandb-entity mehdifaraji --wandb-project May2026-microstructure-hybrid-v21
```

Use `--count-coverage --dry-run` first to verify file discovery and window counts.

