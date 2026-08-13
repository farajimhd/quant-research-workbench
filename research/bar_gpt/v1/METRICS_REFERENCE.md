# BarGPT v1 metric reference and cleanup review

This document describes the metrics emitted by the current BarGPT v1 implementation. It is an implementation reference, not a proposal: formulas below are traced to `metrics.py`, `objectives.py`, `train.py`, and `prefetch.py` as of 2026-08-12.

## Executive interpretation

For model selection, use the fixed `monitor_*` panel during development and the fixed `validation_*` or `final_validation_*` panel for final comparison. The principal directional metrics should be:

1. `{namespace}_close_direction_summary/mcc_macro`
2. `{namespace}_close_direction_summary/balanced_accuracy_macro`
3. `{namespace}_trade_close_direction_summary/mcc_macro`
4. Per-horizon trade-close MCC and balanced accuracy

Do not use raw accuracy, mixed OHLC family summaries, training-sample metrics, or runtime metrics as the primary evidence of directional generalization.

`monitor_ar_direction_balanced/balanced_accuracy_macro` is calculated consistently with its current implementation, but its aggregation is misleading. It pools confusion counts for all 12 OHLC direction targets inside each autoregressive view, then averages the resulting balanced accuracies over the eight views. High and low targets are naturally one-sided. A rising value can reflect improved next-bar prediction, but it is not clean evidence that future close direction generalizes.

## Metric namespaces and dimensions

The same evaluation metric patterns can appear under different namespaces:

| Namespace | Population | Suitable use |
|---|---|---|
| `train_*` quality metrics | One optimizer update sampled when the configured training-metric interval is crossed | Noisy training diagnostic only |
| `monitor_*` | Fixed 1M-origin model-comparison monitor panel | Learning curves and early comparison |
| `validation_*` | Fixed 5M-origin model-comparison validation panel at natural training completion | Primary model comparison |
| `final_validation_*` | Separately evaluated fixed validation panel | Reproducible post-training scorecard |

The comparison campaign evaluates:

- Horizons: `5s`, `30s`, `60s`, `300s`, `900s`, and `3600s`.
- Price families: `trade`, `bid`, and `ask`.
- OHLC fields: `open`, `high`, `low`, and `close`.
- Physical return/direction targets: 12 family-field combinations.
- Autoregressive views: `1s`, `5s`, `10s`, `30s`, `1m`, `5m`, `30m`, and `1h`.
- Quantiles: `q10`, `q50`, and `q90`.
- Conditions: `halt_pause`, `resume`, `news_risk`, and `luld_limit_state`.

In the patterns below, `{namespace}` means `train`, `monitor`, `validation`, or `final_validation`; `{family}` means `trade`, `bid`, or `ask`; `{field}` means an OHLC field; `{horizon}` means one of the six horizon labels; `{view}` means one of the eight autoregressive views; and `{target}` means a family-field return target such as `trade_close_return`.

## Direction-score definitions

Direction metrics exclude neutral observations. A target is directional only when its absolute return exceeds the configured 1 basis-point neutral band. The model predicts positive when its direction logit is greater than zero.

For true positives `TP`, true negatives `TN`, false positives `FP`, and false negatives `FN`:

| Metric | Formula | Range | Best | Chance/no-skill reference | Worst | Important interpretation |
|---|---|---:|---:|---:|---:|---|
| Accuracy | `(TP + TN) / total` | `[0, 1]` | `1` | Majority-class prevalence, not necessarily `0.5` | `0` | Misleading when positive and negative prevalence is imbalanced. |
| Balanced accuracy | `(TPR + TNR) / 2` | `[0, 1]` | `1` | `0.5` | `0` | Preferred over accuracy for imbalance. Below `0.5` indicates systematically inverted predictions. Undefined if the evaluated target has only one actual class. |
| MCC | `(TP*TN - FP*FN) / sqrt((TP+FP)(TP+FN)(TN+FP)(TN+FN))` | `[-1, 1]` | `1` | `0` | `-1` | Preferred single direction metric. Constant predictions against a two-class target are recorded as `0`. Undefined if the evaluated population contains only one actual class. |
| Neutral fraction | `neutral valid observations / all valid observations` | `[0, 1]` | No universal optimum | N/A | No universal worst | Describes population difficulty; it is not model skill. Direction scores exclude these observations. |
| Positive fraction | `(TP + FN) / directional count` | `[0, 1]` | No optimum | N/A | No worst | Actual class prevalence after neutral removal. |
| Predicted-positive fraction | `(TP + FP) / directional count` | `[0, 1]` | Should broadly track actual prevalence | N/A | Collapse at `0` or `1` | Detects an always-down or always-up head. |
| Directional count | `TP + TN + FP + FN` | `[0, +inf)` | Larger is statistically safer | N/A | `0` | Support, not skill. |

Macro metrics use the arithmetic mean of finite component values. Undefined components are omitted. Consequently, macro composition can change when a target or view contains only one actual class; support and valid-head counts should be checked before comparing sparse evaluations.

## 1. Training objective and loss metrics

All losses are minimized. Except for latent cosine loss, their upper bound is unbounded. A value of zero is the mathematical best, but different components have different scales and weights and must not be compared directly.

The current objective is:

```text
AR = AR continuous + 0.25 * AR availability + 0.10 * AR direction
Horizon = horizon quantile + 0.25 * horizon availability + 0.10 * horizon direction
Total = 0.35 * AR + 1.00 * Horizon + 0.05 * latent prediction
```

Condition-positive binary loss terms additionally use positive-class weight `32`.

| W&B metric | Calculation | Best | Worst / interpretation |
|---|---|---:|---|
| `train_loss/total` | Weighted total objective above | `0` | Unbounded above. Primary optimization value, not by itself proof of generalization. |
| `train_loss/autoregressive` | Mean AR continuous loss across views plus weighted AR availability and direction | `0` | Unbounded. |
| `train_loss/horizon` | Horizon quantile plus weighted availability and direction | `0` | Unbounded. |
| `train_loss/ar_continuous` | Mean masked Huber loss over continuous next-bar targets and AR views | `0` | Unbounded. |
| `train_loss/ar_availability` | Mean masked binary cross-entropy over next-bar availability channels and AR views | `0` | Unbounded. |
| `train_loss/ar_direction` | Mean binary cross-entropy over non-neutral next-bar OHLC directions and AR views | `0` | Unbounded. |
| `train_loss/horizon_quantile` | Masked pinball loss across continuous physical-horizon targets and q10/q50/q90 | `0` | Unbounded. |
| `train_loss/horizon_availability` | Masked BCE across physical-horizon binary targets; condition positives receive weight 32 | `0` | Unbounded. |
| `train_loss/horizon_direction` | BCE across non-neutral physical-horizon OHLC direction heads | `0` | Unbounded. |
| `train_loss/latent_prediction` | Mean `1 - cosine_similarity(predicted latent, detached next latent)` | `0` | Range `[0, 2]`; `1` means orthogonal and `2` opposite. |
| `train_loss_ar_views/ar_{view}` | Per-view `continuous + 0.25*availability + 0.10*direction` | `0` | Unbounded; views have different populations and should not be ranked solely by raw loss. |
| `{namespace}_loss/*` | Origin-count-weighted mean of batch-level loss values on an evaluation panel | `0` | Unbounded. See the batch-aggregation issue in the cleanup section. |
| `{namespace}_loss_ar_views/ar_{view}` | Evaluation counterpart of the per-view AR loss | `0` | Unbounded. |

## 2. Physical-horizon return magnitude

The q50 prediction and target are inverted from `asinh(log_return * 100)` into log-return basis points as `sinh(value) * 100`.

| Metric pattern | Calculation | Range | Best | Worst / interpretation |
|---|---|---:|---:|---|
| `{namespace}_{family}_{field}_return_error/mae_bps_{horizon}` | Mean absolute q50 return error in basis points over valid endpoints | `[0, +inf)` | `0` | Unbounded. Lower is better. |
| `{namespace}_{family}_{field}_return_error/persistence_mae_bps_{horizon}` | MAE of the current 1-second family close return repeated as the forecast for that future OHLC target | `[0, +inf)` | `0` | Baseline diagnostic. It is a current-return continuation baseline, not a zero-return price-persistence baseline. |
| `{namespace}_{family}_{field}_return_skill/skill_vs_persistence_{horizon}` | `1 - model_MAE / baseline_MAE` | `(-inf, 1]` | `1` | `0` ties baseline; positive beats it; negative is worse; unbounded negative. Undefined when baseline MAE is zero. |
| `{namespace}_{family}_summary/mae_macro` | Finite mean over four fields × six horizons | `[0, +inf)` | `0` | Mixes close returns with one-sided future-window extrema. |
| `{namespace}_{family}_summary/persistence_mae_macro` | Finite mean baseline MAE over four fields × six horizons | `[0, +inf)` | `0` | Baseline difficulty summary. |
| `{namespace}_{family}_summary/skill_vs_persistence_macro` | Finite mean skill over four fields × six horizons | `(-inf, 1]` | `1` | `0` ties the baseline on average. |

MAE is the clearest magnitude metric. For generalization, require validation MAE to improve over training/monitor trends and require skill versus a clearly named baseline to remain positive.

## 3. Physical-horizon direction

| Metric pattern | Calculation | Best | No skill | Worst | Use |
|---|---|---:|---:|---:|---|
| `{namespace}_{family}_{field}_direction/accuracy_{horizon}` | Accuracy on returns outside ±1 bp | `1` | Majority prevalence | `0` | Diagnostic only. |
| `{namespace}_{family}_{field}_direction/balanced_accuracy_{horizon}` | Mean positive and negative recall | `1` | `0.5` | `0` | Good per-head imbalance-aware score. |
| `{namespace}_{family}_{field}_direction_quality/mcc_{horizon}` | MCC on the same directional population | `1` | `0` | `-1` | Best per-head direction score. |
| `{namespace}_{family}_{field}_direction_quality/neutral_fraction_{horizon}` | Fraction of valid endpoints inside ±1 bp | No optimum | N/A | No worst | Population diagnostic. |
| `{namespace}_{family}_{field}_direction_count/directional_count_{horizon}` | Non-neutral support | Larger support | N/A | `0` | Reliability diagnostic. |
| `{namespace}_{family}_{field}_direction_prevalence/positive_fraction_{horizon}` | Actual positive prevalence | No optimum | N/A | No worst | Required to interpret accuracy. |
| `{namespace}_{family}_{field}_direction_prevalence/predicted_positive_fraction_{horizon}` | Predicted positive prevalence | Match actual without collapse | N/A | Collapse at `0` or `1` | Required to identify constant heads. |

### Preferred close-only summaries

| Metric pattern | Components | Best | No skill | Worst |
|---|---|---:|---:|---:|
| `{namespace}_{family}_close_direction_summary/accuracy_macro` | Six close horizons for one family | `1` | Prevalence-dependent | `0` |
| `{namespace}_{family}_close_direction_summary/balanced_accuracy_macro` | Six close horizons for one family | `1` | `0.5` | `0` |
| `{namespace}_{family}_close_direction_summary/mcc_macro` | Six close horizons for one family | `1` | `0` | `-1` |
| `{namespace}_close_direction_summary/accuracy_macro` | Trade/bid/ask close × six horizons | `1` | Prevalence-dependent | `0` |
| `{namespace}_close_direction_summary/balanced_accuracy_macro` | Trade/bid/ask close × six horizons | `1` | `0.5` | `0` |
| `{namespace}_close_direction_summary/mcc_macro` | Trade/bid/ask close × six horizons | `1` | `0` | `-1` |

The overall close MCC is the preferred single direction metric. Trade-close MCC is the preferred execution-price-specific metric.

### Legacy mixed family summaries

These keys average all four OHLC fields and all six horizons within one family:

- `{namespace}_{family}_summary/accuracy_macro`
- `{namespace}_{family}_summary/balanced_macro`
- `{namespace}_{family}_summary/mcc_macro`
- `{namespace}_{family}_summary/neutral_macro`

Their numerical ranges match the corresponding base metrics. They should remain diagnostics only because future-window highs are predominantly positive and lows predominantly negative. Mixing these extrema with open and close can obscure close-direction collapse or improvement.

## 4. Autoregressive next-bar direction

AR metrics evaluate the direction of the next stored nonempty completed bar in each view. Sparse event time is intentional: a wall-clock gap does not invalidate a next-bar transition.

### Per-target, per-view metrics

For every `{target}` and `{view}`:

| Metric pattern | Calculation | Best | No skill | Worst |
|---|---|---:|---:|---:|
| `{namespace}_ar_{target}_direction_accuracy/accuracy_{view}` | Direction accuracy outside ±1 bp | `1` | Prevalence-dependent | `0` |
| `{namespace}_ar_{target}_direction_balanced/balanced_accuracy_{view}` | Balanced direction accuracy | `1` | `0.5` | `0` |
| `{namespace}_ar_{target}_direction_mcc/mcc_{view}` | Direction MCC | `1` | `0` | `-1` |
| `{namespace}_ar_{target}_direction_neutral/neutral_fraction_{view}` | Neutral fraction among valid next-bar targets | No optimum | N/A | No worst |
| `{namespace}_ar_{target}_direction_count/directional_count_{view}` | Directional support | Larger is safer | N/A | `0` |
| `{namespace}_ar_{target}_direction_prevalence/positive_fraction_{view}` | Actual positive prevalence | No optimum | N/A | No worst |
| `{namespace}_ar_{target}_direction_prevalence/predicted_positive_fraction_{view}` | Predicted positive prevalence | Avoid collapse | N/A | Collapse at `0` or `1` |

### Current pooled AR metrics

For each view, the implementation sums the confusion matrices of all 12 OHLC targets and then computes one accuracy, balanced accuracy, and MCC. The `macro` is the finite arithmetic mean of those eight view-level values.

| Metric pattern | Current aggregation | Best | No skill | Worst |
|---|---|---:|---:|---:|
| `{namespace}_ar_direction_accuracy/accuracy_{view}` | Pooled confusion over 12 targets | `1` | Prevalence-dependent | `0` |
| `{namespace}_ar_direction_balanced/balanced_accuracy_{view}` | Balanced accuracy from pooled confusion | `1` | `0.5` | `0` |
| `{namespace}_ar_direction_mcc/mcc_{view}` | MCC from pooled confusion | `1` | `0` | `-1` |
| `{namespace}_ar_direction_neutral/neutral_fraction_{view}` | Neutral counts pooled over 12 targets | No optimum | N/A | No worst |
| `{namespace}_ar_direction_accuracy/accuracy_macro` | Mean over eight pooled view scores | `1` | Prevalence-dependent | `0` |
| `{namespace}_ar_direction_balanced/balanced_accuracy_macro` | Mean over eight pooled view scores | `1` | `0.5` | `0` |
| `{namespace}_ar_direction_mcc/mcc_macro` | Mean over eight pooled view scores | `1` | `0` | `-1` |
| `{namespace}_ar_direction_neutral/neutral_fraction_macro` | Mean view neutral fraction | No optimum | N/A | No worst |

This explains why `monitor_ar_direction_balanced/balanced_accuracy_macro` can rise. It may reflect genuine improvement somewhere in the 96 view-target combinations, but it does not identify whether close directions improved, whether only high/low improved, or whether the improvement is concentrated in one view. A clean AR generalization summary needs close-only macros plus fixed component counts.

## 5. Quantile coverage and calibration

| Metric pattern | Calculation | Range | Best | Worst / interpretation |
|---|---|---:|---:|---|
| `{namespace}_{family}_{field}_coverage_qXX/{horizon}` | Fraction of valid targets `<=` predicted quantile | `[0, 1]` | Nominal quantile: `0.10`, `0.50`, or `0.90` | Distance from nominal is miscalibration. |
| `{namespace}_{family}_{field}_calibration/error_{horizon}` | Mean of `abs(empirical coverage - nominal quantile)` over q10/q50/q90 | `[0, 0.9]` | `0` | Higher is worse; upper extreme requires pathological predictions/crossing. |
| `{namespace}_{family}_coverage_qXX/macro` | Mean empirical coverage over four fields × six horizons | `[0, 1]` | Corresponding nominal quantile | Can hide opposing calibration errors. |
| `{namespace}_{family}_summary/calibration_macro` | Mean per-head calibration error | `[0, 0.9]` | `0` | Higher is worse. |

Coverage checks marginal calibration only. It does not measure interval sharpness, quantile crossing, or conditional calibration.

## 6. Return ranking and direction confidence

| Metric pattern | Calculation | Range | Best | Worst / interpretation |
|---|---|---:|---:|---|
| `{namespace}_{family}_{field}_ranking/spearman_{horizon}` | Spearman correlation between predicted and actual return bps over valid endpoints, with average ranks for ties | `[-1, 1]` | `1` | `0` means no monotonic rank association; `-1` is perfectly reversed. Undefined with fewer than two observations or constant ranks. |
| `{namespace}_{family}_summary/rank_macro` | Finite mean Spearman over four fields × six horizons | `[-1, 1]` | `1` | Mixed OHLC diagnostic; close-specific ranks should be inspected directly. |
| `{namespace}_{family}_{field}_confidence/top_10pct_{horizon}` | Accuracy among the 10% largest absolute direction logits | `[0, 1]` | `1` | `0`; vulnerable to class imbalance and not a probability-calibration metric. |
| `{namespace}_{family}_{field}_confidence/top_20pct_{horizon}` | Accuracy among the 20% largest absolute direction logits | `[0, 1]` | `1` | Same caveat. |
| `{namespace}_{family}_summary/top10_macro` | Mean top-10% accuracy over four fields × six horizons | `[0, 1]` | `1` | Mixed OHLC and prevalence-sensitive. |
| `{namespace}_{family}_summary/top20_macro` | Mean top-20% accuracy over four fields × six horizons | `[0, 1]` | `1` | Mixed OHLC and prevalence-sensitive. |

## 7. Availability and condition metrics

| Metric pattern | Calculation | Range | Best | Worst / interpretation |
|---|---|---:|---:|---|
| `{namespace}_availability/brier_{horizon}` | Mean squared probability error across all valid binary channels at the horizon | `[0, 1]` | `0` | `1`. This is a micro-average over common availability and rare condition channels. |
| `{namespace}_availability/brier_macro` | Mean of the six horizon Brier scores | `[0, 1]` | `0` | `1`. |
| `{namespace}_condition_{condition}/positives_{horizon}` | Number of positive condition labels | `[0, +inf)` | More support is safer | `0`; support only. |
| `{namespace}_condition_{condition}/average_precision_{horizon}` | Area under the precision-recall step curve from ranked predicted probabilities | `(approximately 0, 1]` | `1` | Data-dependent; random reference is condition prevalence. Undefined with zero positives. |
| `{namespace}_condition_{condition}/active_horizons` | Number of horizons with at least one positive and an emitted AP | `[0, 6]` | `6` for evaluability | `0`; not model skill. |
| `{namespace}_condition_{condition}/average_precision_macro` | Finite mean AP over active horizons only | `[0, 1]` | `1` | Must be interpreted with active horizons and prevalence. |
| `train_data/condition_positive_rate` | Positive masked condition labels divided by all valid masked condition labels in the logged update | `[0, 1]` | No optimum | Population diagnostic, not model quality. |

## 8. Evaluation population metrics

| Metric pattern | Meaning | Best/worst |
|---|---|---|
| `{namespace}_data/origins` | Valid origins accumulated by evaluation | Larger fixed support is safer; comparisons require the same panel. |
| `{namespace}_data/batches` | Batches accumulated by evaluation | Descriptive; it can change with batch size while origins remain fixed. |

## 9. Optimization, progress, and runtime metrics

These metrics diagnose throughput and stability. None measures generalization.

| W&B metric | Calculation | Preferred interpretation |
|---|---|---|
| `train_progress/origins_seen` | Cumulative valid origins committed by optimizer updates | Sample clock; compare runs at equal origins. |
| `train_progress/microbatches_seen` | Cumulative consumed microbatches | Progress only. |
| `train_progress/optimizer_steps` | Cumulative optimizer updates | Progress only; not aligned across different effective batch sizes. |
| `train_progress/blocks_seen` | Cumulative blocks consumed | Progress only. |
| `train_progress/units_seen` | Cumulative distinct epoch/worker/unit identities | Coverage diagnostic. |
| `train_progress/condition_blocks_seen` | Cumulative blocks marked as having condition targets | Coverage diagnostic. |
| `train_optimization/accumulation_microbatches` | Microbatches in the completed update | Should equal configured accumulation except a final partial update. |
| `train_optimization/learning_rate` | Current scheduled learning rate | No universal best; interpret against schedule. |
| `train_optimization/gradient_norm` | Total pre-clipping gradient norm returned by `clip_grad_norm_` | No fixed optimum. Persistent non-finite or explosive values are bad; values above clip norm are clipped. |
| `train_optimization/amp_scale` | GradScaler scale for FP16; `1` for BF16/no scaler | No universal best. Repeated decreases indicate FP16 overflow. |
| `train_runtime/origins_per_second` | Valid origins in the update divided by update wall time | Higher is faster for the same data/model contract. |
| `train_runtime/update_wall_seconds` | Wall time for the optimizer update | Lower is faster for equivalent work. |
| `train_runtime/gpu_seconds` | Sum of CUDA event durations for forward/backward and optimizer work in the update | Lower for equivalent work; not utilization by itself. |
| `train_runtime/gpu_duty_cycle` | `gpu_seconds / update_wall_seconds` | Nominal `[0,1]`; higher means less host/input idle time, but does not measure kernel occupancy or model quality. |
| `train_runtime/loader_wait_seconds` | Main-loop wait attributed to obtaining batches in the update | Lower is better. |
| `train_runtime/host_cache_batches` | Current host-cache fill | Descriptive; sustained zero with waits suggests starvation. |
| `train_runtime/host_cache_capacity` | Configured host-cache capacity | Configuration, not performance. |
| `train_runtime_loader/host_cache_empty_reads` | Cumulative empty host-cache reads for the current iterator | Lower growth is better. |
| `train_runtime_loader/device_stage_empty_waits` | Cumulative waits for the staged-device queue | Lower growth is better. |
| `train_runtime_loader/device_staged_batches` | Cumulative batches staged to device | Progress diagnostic. |
| `train_runtime_loader/h2d_completed_batches` | Cumulative completed measured H2D transfers | Progress diagnostic. |
| `train_runtime_loader/h2d_seconds` | Cumulative CUDA-event H2D duration observed without forcing synchronization | Lower per completed batch is better. |
| `train_runtime_loader/intraday_page_seconds` | Direct-event loader time reading intraday Arrow pages in the update | Lower for equivalent pages/work. Usually absent for offline-shard training. |
| `train_runtime_loader/unit_prepare_seconds` | Direct-event loader unit preparation time in the update | Lower for equivalent work. Usually absent for offline-shard training. |

Any unrecognized `train/<leaf>` is mapped to `train_misc/<leaf>` by the W&B key mapper and should be treated as an uncatalogued implementation diagnostic until documented.

## Confirmed interpretation and calculation issues for cleanup

The following should be reviewed before changing the emitted contract:

1. **Pooled AR direction summary is not a clean generalization metric.** The current AR view score pools all 12 targets before calculating balanced accuracy and MCC. Add close-only AR summaries and a macro across fixed target-view heads. Keep the existing key temporarily as explicitly named legacy pooled output.

2. **Mixed OHLC family summaries obscure close behavior.** High and low are future-window extrema and are naturally one-sided. Do not rank models by `{family}_summary/accuracy_macro`, `balanced_macro`, or `mcc_macro`. Close-only summaries are already available for physical horizons.

3. **Evaluation loss is a weighted mean of batch means.** Each batch loss is normalized by its own valid mask, then evaluation weights that scalar by origin count. This is not an exact global numerator/valid-denominator reduction and can vary with batching when mask density varies. Validation loss should accumulate exact numerators and denominators per component.

4. **Empty-support regression and Brier cells can appear artificially perfect.** MAE, coverage, and Brier arrays divide by `count.clamp_min(1)`, so a zero-count cell becomes numeric zero instead of undefined. Large panels normally provide support, but the implementation should emit `NaN` plus explicit valid counts when support is zero.

5. **The persistence baseline is misnamed.** It repeats the current 1-second close return across future horizons. That is a current-return continuation or momentum baseline. A price-persistence/no-change baseline for a future return target is zero return. Both baselines would be useful and should be named explicitly.

6. **Availability Brier mixes heterogeneous binary tasks.** It micro-averages four common availability channels and four rare condition channels. Emit per-channel Brier and macro-by-channel summaries so common availability does not hide rare-condition behavior.

7. **Condition AP lacks prevalence denominator.** Positives and active horizons are emitted, but total valid examples and positive prevalence are not. Add both so AP can be compared with its random prevalence baseline.

8. **Top-confidence accuracy is imbalance-sensitive and not calibrated confidence.** Add top-confidence balanced accuracy/MCC, coverage, and probability calibration if these heads will drive selective decisions.

9. **Finite-only macros can change composition.** Undefined single-class heads are omitted. Emit `valid_head_count`, `expected_head_count`, and support-weighted alternatives; model ranking should require a fixed component set.

10. **Periodic `train_*` quality metrics are not generalization metrics.** They summarize only the optimizer update that crosses the interval, not the full interval or a fixed population. Rename or document them as sampled-update diagnostics and never compare architectures by them.

## Practical model-review checklist

At equal origins seen:

1. Check `monitor_close_direction_summary/mcc_macro` and balanced accuracy.
2. Confirm trade-close per-horizon MCC and balanced accuracy, especially the economically relevant horizons.
3. Check actual and predicted positive fractions for collapse.
4. Confirm directional support and neutral fraction are stable across comparisons.
5. Check q50 MAE, Spearman, and skill versus the explicitly defined baseline.
6. Check q10/q50/q90 coverage and calibration error.
7. Inspect condition AP only alongside positives, active horizons, and—after cleanup—prevalence.
8. Confirm the final result on the fixed 5M validation population; do not select from the monitor panel alone.
9. Use throughput, GPU duty, and loader waits only to choose efficient configurations after quality is acceptable.
