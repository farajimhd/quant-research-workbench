# Frozen 32-candidate local transformer experiment

Four families, eight settings each: candle-token causal attention; nonoverlapping
patch attention (4/12 observed rows); multiscale attention (patch plus single rows,
shared attention weights and averaged outputs); inverted latent-channel attention.
The last family embeds 120 prior observations per projected channel and attends
over channels. It is inspired by iTransformer, not a raw-channel implementation.
Patches use observed rows, never implied elapsed seconds. All input rows precede
the decision, and elapsed-time features remain unchanged. No target enters encoder.

| Setting | Width | Layers | Heads | FF multiplier | Pool |
|---|---:|---:|---:|---:|---|
| 1 | 64 | 2 | 2 | 2 | last |
| 2 | 64 | 3 | 4 | 4 | mean |
| 3 | 96 | 2 | 4 | 4 | last |
| 4 | 96 | 4 | 8 | 2 | mean |
| 5 | 128 | 2 | 4 | 4 | last |
| 6 | 128 | 4 | 8 | 4 | mean |
| 7 | 192 | 3 | 4 | 4 | last |
| 8 | 192 | 4 | 8 | 2 | mean |

Latent-channel pooling is always mean; its setting pool field does not affect
the computation. Parameter count deliberately varies; record it beside metrics.
This is a bounded architecture search, not a factorial attribution study.
All dropout remains zero for the initial memorization gate. Normalization and
features are the existing frozen TRAIN-only authority, with split-adjusted prior
context and unchanged V7 levels. No new features or labels are generated.

Heads remain identical across all arms: known-position conditional binary
ENTRY/WAIT versus HOLD/EXIT; separate sigmoid ENTRY/EXIT quality; sigmoid current
relative allocation; independent future hierarchy predicting reference-held state
and conditional action, current plus four next observed labels. Forecast recurrence
uses previous teacher labels during teacher forcing, predicted probabilities during
free running. Both losses are included equally. Current position is observed;
future reference position is predicted, not actual broker execution.

Final fixed-checkpoint admission requires all original current/future action,
allocation and conditional quality gates plus exact reload replay. Failed arms
have no validation metrics. Unused inherited value/stop/target outputs are not
supervised by this pilot and are not certified predictions.

Selection: retain a Pareto frontier over ENTRY AP/F1, current/future macro F1,
ENTRY/EXIT quality MAE and allocation MAE. Provisional primary ordering is ENTRY
average precision, then ENTRY F1, then current macro F1; display regression tradeoffs
separately. Quality alone cannot select a teacher because it ignores false ENTRYs.
The 32 public-development comparisons make this set tuning data. No independent
generalization claim, sealed inspection, production promotion or final training.
Top candidates require a separately authorized, larger comparison afterward.

Each arm sees 81,920 presentations of the same 128 TRAIN rows: 256 joint updates
then 384 frozen-representation head updates, identical order/seed/optimizer schedule.
Validation reuses the authenticated 2,898-row previously exposed public snapshot,
loaded only after each candidate passes admission. One seed is exploratory.
Sequential laptop GPU only, synchronized 50% duty, microbatch 8, CPU threads 2,
BelowNormal, 4 GiB VRAM and 8 GiB host-memory reserve. Online per-label W&B required.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
C:/Users/g835l/miniconda3/envs/ml4t/python.exe -B -m research.rl_trading.v6.run_architecture_benchmark --transformer-search --snapshot-runtime D:/TradingML/runtimes/v6-matched-architectures-20261011 --output D:/TradingML/runtimes/v6-transformer-search-32-20261011
```

Sources: https://arxiv.org/abs/2211.14730 (patches and channel independence),
https://arxiv.org/abs/2310.06625 (inverted attention over variates). These forecasting
results motivate candidates; they do not establish a winner for trading labels.
