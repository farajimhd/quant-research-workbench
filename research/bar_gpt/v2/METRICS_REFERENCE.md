# BarGPT v2 metric reference

This is the metric contract implemented by `metrics.py` and `objectives.py`.
Lower loss and error metrics are better; higher skill and classification
metrics are better unless noted.

## Objective metrics

Each `*_loss/<group>` is reconstructed from the same per-target valid-support
means used by training. The total is a sum, not a weighted average.

| Metric | Calculation | Best | Typical worst |
|---|---|---:|---:|
| `*_loss/ar_regression` | Sum of per-target Huber means across AR views | 0 | Unbounded |
| `*_loss/ar_categorical` | Sum of per-target binary cross-entropy means | 0 | Unbounded |
| `*_loss/ar_return_class` | Sum of per-return-target five-class cross-entropy means | 0 | Unbounded |
| `*_loss/horizon_quantile` | Sum of per-target pinball-loss means across horizons and quantiles | 0 | Unbounded |
| `*_loss/horizon_categorical` | Sum of per-target binary cross-entropy means | 0 | Unbounded |
| `*_loss/horizon_return_class` | Sum of per-return-target five-class cross-entropy means | 0 | Unbounded |
| `*_loss/autoregressive` | Sum of the three AR groups | 0 | Unbounded |
| `*_loss/horizon` | Sum of the three physical groups | 0 | Unbounded |
| `*_loss/total` | AR plus physical loss | 0 | Unbounded |

Because the objective deliberately sums target means with different loss
families, compare a metric with its own history or a controlled model run. Do
not interpret one component's raw scale as importance.

## Five-class return metrics

These are emitted per return target and physical horizon, and per return target
and AR view. Summary metrics are arithmetic macros over their stated members.

| Metric suffix | Calculation | Best | Worst / reference |
|---|---|---:|---:|
| `accuracy` | Correct predictions divided by valid examples | 1 | 0 |
| `balanced_accuracy` | Mean recall over actual classes with support | 1 | 0; chance is 0.20 only with all five classes |
| `macro_f1` | Mean per-class F1 over classes present in actual or predicted data | 1 | 0 |
| `mcc` | Gorodkin multiclass Matthews correlation coefficient | 1 | -1; 0 is no correlation |
| `class_distance` | Mean absolute distance between ordinal class indices | 0 | 4 |

Every head also emits:

- `support/count`: valid observations.
- `support/active_actual_classes`: number of represented actual classes, 0-5.
- `support/<class>`: actual count for each named class.
- `actual_fraction/<class>` and `predicted_fraction/<class>`: prevalence and
  prediction-collapse diagnostics.

The most useful generalization summaries are
`validation_close_return_class_summary/mcc_macro`,
`validation_close_return_class_summary/balanced_accuracy_macro`, and their
`validation_ar_close_return_class_summary/*` counterparts. Always read them
with support and loss.

## Return regression and quantiles

The median physical quantile is decoded to exact simple percentage return
before error measurement.

| Metric | Calculation | Best | Worst |
|---|---|---:|---:|
| `*_return_error/mae_percent_<horizon>` | Mean absolute simple-percent error | 0 | Unbounded |
| `*_return_error/mae_bps_<horizon>` | Percent MAE multiplied by 100 | 0 | Unbounded |
| `*_return_baseline/zero_mae_percent_*` | MAE for a zero-return forecast | 0 | Unbounded |
| `*_return_baseline/continuation_mae_percent_*` | MAE for current-return continuation | 0 | Unbounded |
| `*_return_skill/skill_vs_*` | `1 - model_mae / baseline_mae` | 1 | Unbounded below; 0 ties baseline |
| `*_coverage_qNN/<horizon>` | Fraction of targets at or below qNN prediction | Nominal quantile | 0-1 |
| `*_calibration/error_<horizon>` | Mean absolute deviation from nominal coverage | 0 | 1 |
| `*_ranking/spearman_<horizon>` | Spearman rank correlation of median prediction and target | 1 | -1 |

## Existing categorical targets

Availability and condition targets do not receive regression or return-class
heads.

| Metric | Calculation | Best | Worst |
|---|---|---:|---:|
| `*_availability/brier_macro` | Mean squared probability error across availability heads | 0 | 1 |
| `*_condition_<name>/average_precision_*` | Precision-recall area for the condition label | 1 | 0; compare with prevalence |
| `*_condition_<name>/prevalence_*` | Positive fraction, diagnostic only | N/A | N/A |
| `*_condition_<name>/total_*` | Valid evaluated examples | N/A | N/A |
| `*_condition_<name>/positives_*` | Positive examples | N/A | N/A |

## Namespaces and comparison step

- `train_*`: bounded training diagnostics.
- `monitor_*`: repeated 2026 monitor panel.
- `validation_*`: final 2026 validation panel.
- `overfit_before_*` / `overfit_after_*`: tiny-panel memorization experiment.

Model-comparison W&B logs use cumulative training origins as the step axis, so
curves remain aligned even when efficient microbatch sizes differ. Monitor and
validation populations remain manifest-fixed and identity-disjoint from the
training panel.
