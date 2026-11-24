# Phase 0 V7: eligible bars, direct average-ratio supervision, robust geometry

Fresh 32D residual MLP (width128, two blocks, affine32→18 decoder), 147,570
parameters. No decoder input except the exported embedding and fixed calibration.
The certified source shards and chronological partitions remain immutable.

## Scientific contract

- Every source-valid trade/bid/ask OHLC must lie in **[0.05,100000] inclusive**.
  Missing fields do not reject a bar; a bar with no valid price is ineligible.
  These are the actual shard values, adjusted to each window's causal final-bar
  share basis. This is a model-domain restriction, not unadjusted trading eligibility.
- Reject the whole bar by masking every market and time input before the learned
  stem and every loss target. Retain its temporal position, so directional pairs
  never bridge a rejected bar. Neutral placeholders retain fixed CUDA-graph
  shapes; no out-of-range price is clipped into a false target.
- Preserve V4 absolute log-close and body/upper/lower-wick coordinates. Apply
  **asinh only to standardized encoder body/wick inputs**. Decoder coordinates
  and raw-unit inverse are unchanged; no information is removed by compression.
- Supervise **q=log(1+volume)-log(1+trade_count)** directly in place of the
  reconstructed count loss. Its source mask requires both quantities. Volume
  remains supervised. This is the log of a smoothed average, not exact V/N.
  Reconstructed trade-count metrics are derived diagnostics only.
- Retain symmetric log1p quantity reference/differences, original source masks,
  equal price/activity SmoothL1, price factor1000 and 0.1 coordinate objective.
- Fit new mean/scale on eligible bars in the frozen training subset only. No
  development/validation statistics enter calibration. All eligible bar exposures
  receive equal weight. Preparation records per-feature counts and exclusions.

Training still traverses every certified window for 20 epochs. Progress counts
scanned windows; `train_coverage/*` counts eligible bars and exclusion reasons.
Validation reports `eligible_bars`, `excluded_bars`, and per-timeframe reason
counts. Low/high reasons can overlap. No eligible observation is dropped by
batch tails. Activity directions require both adjacent eligible endpoints.

## Execution and visibility

FP32, batch4096, eight workers, prefetch4; validation batch1024/eight workers.
Training uses CUDA graphs and transfer overlap; full validation captures all
metrics. Cosine1e-4→1e-6, development every1000, checkpoints every500, logs100.
All optimizer/RNG/traversal state is checkpointed. Fresh seed17 initialization.
W&B project `bargpt-phase0-v7-mlp32`; checkpoints stay in runtime storage.

The panel keeps independent TRAINING SAMPLE, DEVELOPMENT and FULL VALIDATION
tables, in that order. Each has its own step, scanned-window count and loss.
Full validation remains visible when development subsequently updates. Pending
full validation is explicitly empty, never substituted with development data.
The compact120×30 layout retains all three tables without flicker.

Finite raw predictions may still overflow a derived statistic. Such metrics are
reported as null with `unavailable_metrics` names and a nonfinite-metric count,
never replaced with zeros. Nonfinite predictions themselves still fail evaluation.
The experiment does not silently repair suspicious source candles such as GOLD.

## Commands (from a committed hash-verified workstation snapshot)

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m research.bar_gpt.phase0_v7.prepare --output D:/TradingML/runtimes/bar_gpt/phase0-v7-setup-20261124/calibration.json
python -B -m research.bar_gpt.phase0_v7.run_train --output D:/TradingML/runtimes/bar_gpt/phase0-v7-mlp32-filtered-full20-20261124 --source-revision FULL-COMMIT --calibration D:/TradingML/runtimes/bar_gpt/phase0-v7-setup-20261124/calibration.json --epochs 20 --batch-size 4096 --workers 8 --validation-batch-size 1024 --validation-workers 8 --prefetch 4 --chunk-bars 262144 --optimized --precision fp32 --graph-validation --training-panel-metrics --validation-every 1000 --checkpoint-every 500 --log-every 100 --learning-rate 1e-4 --min-learning-rate 1e-6 --seed 17
python -B -m research.bar_gpt.phase0_v7.panel --run D:/TradingML/runtimes/bar_gpt/phase0-v7-mlp32-filtered-full20-20261124 --keep-open
```

Use `launch --console-log EXISTING-RUNTIME-DIR/console.log` for detached Windows
training. `profile_pipeline` measures serial/overlapped training and eager/graph
validation from a pinned checkpoint while checking all metric scalars for parity.
Never run GPU profiling alongside training. Results are reconstruction evidence,
not evidence of forecasting usefulness. V7 losses are not directly comparable to
V6 losses because both eligibility and the count objective changed.
