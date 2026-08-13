# BarGPT v2 metric reference

This is the metric contract implemented by `metrics.py` and `objectives.py`.
Lower loss and error metrics are better; higher skill and classification
metrics are better unless noted.

## Objective metrics

Each `*_loss/<group>` is reconstructed from the same per-target valid-support
means used by training. The total is a sum, not a weighted average.

For every target `j`, the masked mean is calculated independently:

```text
mean_loss_j = sum(valid_ij * loss_ij) / max(1, sum(valid_ij))
```

An unsupported target contributes differentiable zero, not a fabricated
observation. Padding and unavailable targets never enter a numerator or
denominator. AR means pool a target's valid transitions across all eight AR
views before division. Physical means pool a target across all six horizons;
quantile regression also pools its quantile axis. The objective then sums the
individual target means exactly once. Sample weights, class weights, task
coefficients, and a final target-count division are not used.

| Metric | Calculation | Best | Typical worst |
|---|---|---:|---:|
| `*_loss/ar_regression` | Sum of per-target Huber means across AR views | 0 | Unbounded |
| `*_loss/ar_categorical` | Sum of per-target binary cross-entropy means | 0 | Unbounded |
| `*_loss/ar_return_class` | Sum of per-return-target three-class cross-entropy means | 0 | Unbounded |
| `*_loss/horizon_quantile` | Sum of per-target pinball-loss means across horizons and quantiles | 0 | Unbounded |
| `*_loss/horizon_categorical` | Sum of per-target binary cross-entropy means | 0 | Unbounded |
| `*_loss/horizon_return_class` | Sum of per-return-target three-class cross-entropy means | 0 | Unbounded |
| `*_loss/autoregressive` | Sum of the three AR groups | 0 | Unbounded |
| `*_loss/horizon` | Sum of the three physical groups | 0 | Unbounded |
| `*_loss/total` | AR plus physical loss | 0 | Unbounded |

The underlying losses are:

```text
Huber(error, delta=1): 0.5*error^2 when |error| <= 1,
                       |error| - 0.5 otherwise

Binary cross-entropy: -[y*log(sigmoid(logit))
                         + (1-y)*log(1-sigmoid(logit))]

Three-class cross-entropy: -log(softmax(logits)[actual_class])

Pinball(q, error): max(q*error, (q-1)*error),
where error = target - prediction
```

AR continuous targets use Huber loss. Existing AR availability targets use
binary cross-entropy. Physical continuous targets use pinball loss at every
configured quantile. Existing physical availability/condition targets use
binary cross-entropy. Each OHLC return additionally uses three-class
cross-entropy from the fixed one-basis-point direction label.

Because the objective deliberately sums target means with different loss
families, compare a metric with its own history or a controlled model run. Do
not interpret one component's raw scale as importance.

## Three-class return-direction metrics

Every stored OHLC return is assigned by exact simple-percent equivalent:

```text
negative: return < -0.01%
neutral:  -0.01% <= return <= +0.01%
positive: return > +0.01%
```

The same rule applies to all AR views and physical horizons. Regression and
quantile heads continue to learn return magnitude independently.

These are emitted per return target and physical horizon, and per return target
and AR view. Summary metrics are arithmetic macros over their stated members.

| Metric suffix | Calculation | Best | Worst / reference |
|---|---|---:|---:|
| `accuracy` | Correct predictions divided by valid examples | 1 | 0 |
| `balanced_accuracy` | Mean recall over actual classes with support | 1 | 0; chance is 1/3 only with all three classes |
| `macro_f1` | Mean per-class F1 over classes present in actual or predicted data | 1 | 0 |
| `mcc` | Gorodkin multiclass Matthews correlation coefficient | 1 | -1; 0 is no correlation |
| `class_distance` | Mean absolute distance between ordinal class indices | 0 | 2 |

Every head also emits:

- `support/count`: valid observations.
- `support/active_actual_classes`: number of represented actual classes, 0-3.
- `support/<class>`: actual count for each named class.
- `actual_fraction/<class>` and `predicted_fraction/<class>`: prevalence and
  prediction-collapse diagnostics.

The most useful generalization summaries are
`validation_close_return_class_summary/mcc_macro`,
`validation_close_return_class_summary/balanced_accuracy_macro`, and their
`validation_ar_close_return_class_summary/*` counterparts. Always read them
with support and loss.

`accuracy` can be high for a collapsed head when one class dominates.
`balanced_accuracy`, macro F1, MCC, the confusion support, and predicted class
fractions expose that failure. MCC is reported as `NaN` when only one actual
class exists, because directional association is then undefined.

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

`mae_percent` is computed after exactly inverting the stored
`asinh(log_return*100)` transform to simple percentage return. `mae_bps` is
`mae_percent * 100`. A positive skill value beats its named baseline; a
negative value is worse.

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

## Overfit acceptance contract

The bounded overfit run uses certified v12 blocks, disables dropout and weight
decay, bounds physical origins and AR transitions, and optimizes the same full
objective used by comparison training. It passes only when:

- total loss improves by the configured minimum;
- at least one physical close-return task and one AR close-return task contain
  all three actual classes at the configured minimum support;
- every such supported close task reaches the configured minimum balanced
  accuracy and MCC.

Unsupported class tasks are reported but cannot create a false pass. The
default report is written under
`D:\TradingML\runtimes\bar_gpt\v2\overfit_pilot_3class_1bp_v1`.
