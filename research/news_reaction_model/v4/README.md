# News Reaction Model v4

V4 keeps the V3 frozen-Qwen embedding encoder and replaces its actionable,
direction, and regression heads with interpretable percentage-range
classification heads. It trains on single-ticker news from 2019-2025 and uses
2026 only as the chronological holdout.

V4 deliberately uses the same W&B project as V3,
`news-reaction-model-v3`, so runs can be compared in one project. Run names and
manifests still identify the model as V4.

## Label contract

For every applicable horizon, the model classifies three anchor-relative raw
returns:

```text
ending = ending price / pre-news anchor price - 1
high   = highest price / pre-news anchor price - 1
low    = lowest price / pre-news anchor price - 1
```

The source columns are `target_return`, `high_return`, and `low_return` from
`news_reaction_labels_v2`. V4 does not use abnormal returns, robust scales, or
precomputed intraday bars. The versioned prepared table stores raw returns;
range classes are derived in Python, leaving range definitions auditable and
changeable without repeating the embedding/reaction join.

Every horizon shares these economically important tails:

```text
... -100..-50, -50..-20, -20..-10, -10..-5, -5..-2, -2..-1
+1..+2, +2..+5, +5..+10, +10..+20, +20..+50, +50..+100, >+100 percent
```

The resolution near zero is finer for short horizons and coarser for long
horizons. Values below -100% are invalid rather than a class. The full authority
is `ranges.py`.

## Model and objective

The publication-time input remains at most two frozen 1,024-dimensional Qwen
embedding chunks. Masked gated pooling and the residual MLP encoder are shared.
Each horizon then has separate ending, high, and low categorical heads sized to
that horizon's range vocabulary.

Training minimizes the mean categorical cross-entropy plus a small ordinal CDF
loss. The ordinal term penalizes distant-bin errors more than adjacent-bin
errors without mixing classification with regression.

## Inference and evaluation

V4 has no direct long/short/flat head. For each horizon:

1. Convert the winning high range to its conservative positive boundary.
2. Convert the winning low range to its conservative negative magnitude.
3. If their total span does not clear the horizon threshold, abstain.
4. If upside is larger, go long; if downside is larger, go short; ties abstain.
5. Use the selected conservative boundary as the target.

Ending-price range does not determine direction. In the descriptive holdout
ledger, a one-share position exits at the predicted target if the actual
horizon high/low touches it; otherwise it exits at the actual ending price.
There is intentionally no first-touch ordering head, stop, risk management,
cost model, overlapping-capital reconciliation, or forced trade. Those require
a later robust-model/risk design.

## Commands

From the workstation runtime root:

```powershell
conda activate ml4t
cd D:\TradingML\codes\news-reaction-model\v4

# One-time, resumable V4 prepared-data build.
python -m research.news_reaction_model.v4.run_prepare_data --execute

# Optional size/batch profiler.
python -m research.news_reaction_model.v4.run_profile_sizes --real-data

# Default: d384, 4 layers, batch 2048, 15 epochs, 3 cosine restarts.
python -m research.news_reaction_model.v4.run_train
```

Preparation is month-atomic, bounded by worker count and ClickHouse memory,
manifested, and restart-safe. Training writes config, manifest, JSONL metrics,
model artifacts/diagram, checkpoints, W&B state, final model card, compressed
holdout predictions, and horizon/overall P&L summaries under one run directory.

## Main files

- `ranges.py`: horizon-specific percentage ranges and conservative boundaries.
- `data.py` / `prepare_data.py`: exact single-ticker identity join and V4 table.
- `model.py`: shared encoder and per-horizon range heads.
- `losses.py` / `metrics.py`: classification and ordinal metrics.
- `inference.py`: range forecasts and dominant-excursion plan.
- `evaluate.py`: target-touch/ending-fallback 2026 ledger.
- `train.py`: 15-epoch, three-restart training and artifact workflow.
