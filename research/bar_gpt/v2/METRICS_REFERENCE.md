# BarGPT v2 metric reference

This is the metric contract implemented by `metrics.py` and `objectives.py`.
Lower loss, error, and calibration metrics are better; higher classification
metrics are better unless noted.

## Sample-clock phases

The shared trainer uses the same schedule for model comparison and ordinary
full training. Cumulative valid training origins are the frequency clock;
microbatch count is not used because model sizes have different efficient
microbatches.

| Phase | Frequency | Population | Additional forward pass |
|---|---:|---|---|
| Required objective | Every microbatch | Current training microbatch | No |
| Host/W&B objective record | Every 1,000,000 origins | Origin-weighted microbatch accumulation | No |
| F1 training evaluation | Every 5,000,000 origins | Optimizer update crossing the threshold | No; reuses predictions |
| F2 monitor evaluation | Every 25,000,000 origins | Deterministic prefix capped at 250,000 origins | Yes, inference only |
| Epoch evaluation | End of every epoch | Fixed training prefix up to 1,000,000 origins and complete validation panel | Yes, inference only |

If an F2 boundary would fall within one F2 interval of the epoch boundary, the
paired epoch evaluation replaces it. For a 100M-origin comparison epoch this
means monitor evaluations at 25M, 50M, and 75M, followed by the epoch audit.
Losses and finite-value safety checks are still calculated every microbatch;
only the bounded host transfer and W&B emission wait for the 1M clock.
The monitor cap is shared by comparison and full training. Because whole
collated batches are consumed, the observed count can exceed 250,000 by less
than one fixed eight-block evaluation batch. Complete validation is never run
between epoch boundaries.

The dedicated repetition experiment uses four 25M-origin data epochs while
retaining the baseline's evaluation sample clock. Its first three epoch
boundaries emit the same bounded `monitor_*` schema; only the fourth/final
boundary emits `epoch_train_*`, `validation_*`, and
`epoch_generalization_gap/*`. This explicit experimental option does not
change ordinary full-training or baseline-comparison epoch behavior.

The separate full-catalog chunk trainer has a different evaluation cadence:

| Phase | Frequency | Population | Additional forward pass |
|---|---:|---|---|
| Required objective | Every microbatch | Current training microbatch | No |
| Host/W&B objective record | Every 1,000,000 seen origins | Origin-weighted accumulation | No |
| F1 training evaluation | Every 5,000,000 seen origins | Threshold-crossing update | No |
| Chunk monitor | After each approximately 30M-origin block-aligned chunk | A different deterministic stratified panel of at least 1,000,000 held-out origins | Yes |
| Outer-epoch evaluation | After every complete training-population pass | Fixed 1M training audit and fixed complete 5M validation | Yes |

Every training block is consumed once per outer epoch. Chunk boundaries are
defined by complete blocks, so observed chunk origins vary around 30M. The
next epoch uses a different deterministic worker-owned shuffle and therefore
different chunk membership. Monitor panels vary by epoch and chunk but are
disjoint from training, fixed validation, and locked test. They are diagnostic
and never select the final model directly. Complete validation drives
epoch-level early stopping. All records, including chunk and validation
records, retain cumulative valid training origins as the W&B step.

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

Training, monitor, and validation logging deliberately report direction metrics
only for close-return heads. High/low/open categorical heads remain in the
objective, but their redundant per-head metric series are not sent to W&B.
Summary metrics are arithmetic macros over their stated close heads.

| Metric suffix | Calculation | Best | Worst / reference |
|---|---|---:|---:|
| `balanced_accuracy` | Mean recall over actual classes with support | 1 | 0; chance is 1/3 only with all three classes |
| `mcc` | Gorodkin multiclass Matthews correlation coefficient | 1 | -1; 0 is no correlation |

The most useful generalization summaries are
`validation_close_return_class_summary/mcc_macro`,
`validation_close_return_class_summary/balanced_accuracy_macro`, and their
`validation_ar_close_return_class_summary/*` counterparts. Always read them
with loss. MCC exposes majority-class collapse more reliably than plain
accuracy. MCC is `NaN` when only one actual class exists because directional
association is then undefined. The bounded overfit report retains per-class
support locally because its pass gate needs it; normal training does not create
those W&B series.

## Return regression and quantiles

The median physical quantile is decoded to exact simple percentage return
before error measurement.

| Metric | Calculation | Best | Worst |
|---|---|---:|---:|
| `*_return_error/mae_bps_<horizon>` | Mean absolute simple-return error in basis points | 0 | Unbounded |
| `*_<family>_summary/mae_bps_macro` | Macro mean over that family's OHLC/horizon MAEs | 0 | Unbounded |
| `*_<family>_summary/calibration_macro` | Macro absolute quantile-coverage error | 0 | 1 |

`mae_bps` is computed after exactly inverting the stored
`asinh(log_return*100)` transform to simple return. Percent MAE, zero and
continuation baselines, skill ratios, per-target coverage series, and Spearman
sorting were removed as redundant or unused. Calibration remains because the
physical value heads are probabilistic quantile heads.

## Existing categorical targets

Availability and condition targets do not receive regression or return-class
heads.

| Metric | Calculation | Best | Worst |
|---|---|---:|---:|
| `*_availability/brier_macro` | Mean squared probability error across availability heads | 0 | 1 |

Condition heads still receive their own binary losses where support exists.
The comparison monitor and validation panels contain no positive condition
blocks, so condition average precision and prevalence series are not reported
as if they measured held-out generalization. A future condition claim requires
a separately certified condition-enriched evaluation panel.

## Namespaces and comparison step

- `train_*`: bounded training diagnostics.
- `monitor_*`: repeated deterministic 250K-origin prefix of the 2026 monitor
  authority; intended for trend detection, not the final model claim.
- `chunk_monitor_*`: rotating stratified 1M-origin panels used by full-catalog
  chunk training; each record also carries `chunk/*` epoch and chunk metadata.
- `epoch_train_*`: deterministic training-population epoch audit.
- `validation_*`: final 2026 validation panel.
- `epoch_generalization_gap/*`: positive means validation degraded relative to
  the epoch training audit.
- `overfit_before_*` / `overfit_after_*`: tiny-panel memorization experiment.

Model-comparison W&B logs use cumulative training origins as the step axis, so
curves remain aligned even when efficient microbatch sizes differ. Monitor and
validation populations remain manifest-fixed and identity-disjoint from the
training panel.

Full-catalog W&B logs use the same cumulative-origin step. The additional
`train_progress/outer_epoch`, `train_progress/chunk_index`,
`train_progress/chunk_origins_seen`, and
`train_progress/chunk_blocks_seen` fields describe position without replacing
the sample clock.

Full training logs `train_optimization/learning_rate`,
`train_optimization/epoch_peak_learning_rate`, and
`train_optimization/chunk_cosine_progress`. The W&B step remains cumulative
origins seen. Chunk cosine progress is completed blocks divided by the active
block-aligned chunk budget. It reaches 1 at the chunk boundary, restarts for
the next chunk, and the epoch peak decays by 0.95 only at an outer-epoch
transition.

## Epoch checkpoints

After the paired epoch evaluation, the trainer atomically queues one immutable
checkpoint named `checkpoint_epoch_NNNN.pt`, for example
`checkpoint_epoch_0001.pt`. It contains the model, optimizer, scaler,
scheduler, sample clocks, durable cursors, contracts, epoch-training metrics,
validation metrics, generalization gaps, and W&B run identity.
`checkpoint_latest.pt` and `checkpoint_best_val.pt` remain available, but do
not replace the immutable epoch checkpoint. An existing epoch filename is
never silently overwritten.

Full-catalog training additionally stages `checkpoint_latest.pt` after each
chunk monitor. Its full-chunk state contains the outer epoch, chunk boundary,
durable worker cursors, epoch-plan hash, and early-stopping counters, so resume
does not regenerate a different active plan. Immutable epoch checkpoints and
`checkpoint_best_val.pt` retain their ordinary meanings.

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
