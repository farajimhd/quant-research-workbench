# BarGPT v3 metric reference

## Optimization losses

| Metric | Meaning |
|---|---|
| `*_loss/ar_regression` | Mean of independently normalized AR continuous targets, including OHLC returns, log volume, and log trade count |
| `*_loss/ar_categorical` | Mean of AR availability binary losses |
| `*_loss/ar_time_to_event` | Six-class next-event gap negative log likelihood |
| `*_loss/horizon_quantile` | Mean quantile loss across physical continuous targets, including returns, volatility, volume, and count |
| `*_loss/horizon_categorical` | Mean binary loss across availability and condition targets |
| `*_loss/total` | Sum of the five family means above; no manual coefficients |

Return-direction cross-entropy is not part of v3 optimization.

## Direction metrics

Direction labels are retained as evaluation-only diagnostics. Predicted classes
come from the median continuous return prediction and realized classes come
from the target, both using the same frozen v2 threshold tables. Balanced
accuracy, MCC, supports, and confusion-derived summaries therefore remain
available without a categorical-return head.

For an apples-to-apples v2/v3 comparison, re-score v2 continuous median returns
with this same rule. Metrics from v2's legacy direction-head argmax belong in a
separate `legacy_direction_head/*` namespace.

## Time-to-event metrics

`*_ar_time_to_event_<view>/accuracy` reports gap-class accuracy for each AR
view, with macro accuracy under `*_ar_time_to_event_summary/accuracy_macro`.
Diagnostic runs additionally report support for one interval, two intervals,
three-to-five, six-to-thirty, more-than-thirty, and cross-session classes.

## Global validation provenance

`artifacts/global_validation_runs.jsonl` binds each `global_validation/*`
metric set to its immutable sample-numbered checkpoint SHA-256 and fixed-panel
manifest hash. Chunk-validation metrics remain local to their exact chunk and
must not be interpreted as a global trend.

Global promotion uses `global_validation_trade_close_summary/mae_bps_macro` as
the primary score. Close MCC, trade high/low range MAE, and trade quantile
calibration are strict non-regression gates. `*_loss/total` is not used.
