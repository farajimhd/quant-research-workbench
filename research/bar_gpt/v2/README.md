# BarGPT v2

BarGPT v2 is an isolated model/loss/evaluation revision built on the immutable,
certified BarGPT v12 shard catalog. It does not rebuild or rewrite that data
authority. The default input remains:

`D:\TradingML\runtimes\bar_gpt\v1\offline_shards_v12`

All v2 checkpoints, reports, manifests, profiles, and analysis output belong
under `D:\TradingML\runtimes\bar_gpt\v2`.

## What changed from v1

- Latent prediction is removed from the model, objective, metrics, and
  checkpoint contract.
- Each of the 12 trade/bid/ask OHLC return targets retains its regression head
  and gains an independent five-class head: `strong_negative`, `negative`,
  `neutral`, `positive`, and `strong_positive`.
- The old binary direction heads are replaced, not retained.
- Non-return floating-point targets remain regression-only. Existing
  categorical targets remain categorical-only.
- Each target loss is reduced to its mean over valid observations. Those
  target means are summed directly. There are no objective coefficients,
  positive-class weights, class weights, or final division by the number of
  targets.
- Checkpoints are explicitly stamped `bar_gpt/v2`; v1 and unstamped
  checkpoints fail closed on restore.

The causal model and certified target tensors are otherwise unchanged. The
five-class labels are derived on the training/evaluation side from the stored
continuous return targets; shards are not relabeled or duplicated.

## Return-class contract

Stored transformed return `z` is decoded exactly as:

```text
log_return = sinh(z) / 100
simple_percent_return = expm1(log_return) * 100
```

For neutral threshold `N` and strong threshold `S`, both in percent:

```text
strong_negative: pct < -S
negative:        -S <= pct < -N
neutral:         -N <= pct <= N
positive:         N < pct <= S
strong_positive:  pct > S
```

Physical horizons use:

| Horizon | N | S |
|---|---:|---:|
| 5s | 0.05% | 0.20% |
| 30s | 0.08% | 0.30% |
| 60s | 0.10% | 0.40% |
| 300s | 0.20% | 0.75% |
| 900s | 0.30% | 1.25% |
| 3600s | 0.50% | 2.00% |

Autoregressive views use:

| View | N | S |
|---|---:|---:|
| 1s | 0.03% | 0.12% |
| 5s | 0.05% | 0.20% |
| 10s | 0.06% | 0.25% |
| 30s | 0.08% | 0.30% |
| 1m | 0.10% | 0.40% |
| 5m | 0.20% | 0.75% |
| 30m | 0.40% | 1.50% |
| 1h | 0.50% | 2.00% |

## Loss contract

The total loss is the exact sum of six groups:

```text
AR regression target means
+ AR categorical target means
+ AR return-class target means
+ physical quantile-regression target means
+ physical categorical target means
+ physical return-class target means
```

Within an AR target, valid observations are pooled across views before its
mean is calculated. Within a physical target, valid observations are pooled
across horizons before its mean is calculated. A target with no valid support
contributes differentiable zero. Padding, unavailable targets, and invalid
origins never enter either numerator or denominator.

## Primary commands

Run the full aggressive v2 grid before treating any v1-derived launch preset
as a v2 optimum:

```powershell
python -m research.bar_gpt.v2.run_profile_train
```

After selecting a candidate, rerun a longer final profile for one size with:

```powershell
python -m research.bar_gpt.v2.run_profile_model_performance --model-size current
```

Run a bounded overfit experiment for any model size:

```powershell
python -m research.bar_gpt.v2.run_overfit_pilot --model-size current
python -m research.bar_gpt.v2.run_overfit_pilot --model-size medium
python -m research.bar_gpt.v2.run_overfit_pilot --model-size large
```

Preview or execute the aligned sequential model comparison:

```powershell
python -m research.bar_gpt.v2.run_train_model_comparison
python -m research.bar_gpt.v2.run_train_model_comparison --execute
```

The comparison uses the fixed experiment manifest, all catalog tickers,
100,000,000 training origins from 2019-2025, 1,000,000 monitor origins from
2026, and 5,000,000 final-validation origins from 2026. W&B project authority
is `bar gpt v2 model comparison`; sample count is the comparison step axis.

Analyze return-class support concurrently and resumably over the same fixed
panels:

```powershell
python -m research.bar_gpt.v2.run_train_model_comparison --prepare-manifest-only

python -m research.bar_gpt.v2.run_analyze_return_classes `
  --experiment-manifest D:\TradingML\runtimes\bar_gpt\v2\model_comparison\fixed_panels_v2.json `
  --execute
```

The comparison and analyzer require `--execute`; profiler and overfit launchers
execute by default after printing their equivalent command. The analyzer
writes per-unit checkpoints and aggregate JSON/CSV reports under
the v2 runtime root; reruns skip completed units.

## Interpretation and safety

See `METRICS_REFERENCE.md` for metric formulas and ranges. Five-class balanced
accuracy is macro recall over classes with actual support; inspect
`active_actual_classes` and per-class support alongside it. A value of 0.20 is
the natural five-class chance reference only when all five actual classes are
represented and predictions are uninformative.

The model remains causal: full attention uses native causal SDPA, while masked
or local attention supplies an explicit lower-triangular/local allowed mask and
therefore correctly sets `is_causal=False` to avoid combining two masks. Shard
availability and as-of indices remain the point-in-time authority.
