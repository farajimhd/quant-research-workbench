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
  and gains an independent three-class direction head: `negative`, `neutral`,
  and `positive`.
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
three-class labels are derived on the training/evaluation side from the stored
continuous return targets; shards are not relabeled or duplicated.

## Return-class contract

Stored transformed return `z` is decoded exactly as:

```text
log_return = sinh(z) / 100
simple_percent_return = expm1(log_return) * 100
```

One fixed one-basis-point neutral band applies to every return target, physical
horizon, and autoregressive view. One basis point is `0.01%`:

```text
negative: pct < -0.01%
neutral:  -0.01% <= pct <= +0.01%
positive: pct > +0.01%
```

Boundary values at exactly `-1 bp` and `+1 bp` are neutral. Labels are formed
in the reversible stored-target basis so an unnecessary floating-point decode
cannot move a value across a boundary.

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

The final profiler defaults to the fixed model-comparison training manifest,
so its block-density and ticker mix represent the real 100M-origin experiment
instead of the denser first month of the shard catalog. Pass
`--experiment-manifest ""` only when a deliberate date-range profile is
required.

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
The shared comparison/full-training schedule calculates losses every
microbatch, logs objective aggregates every 1M origins, reuses the crossing
training update for F1 metrics every 5M origins, evaluates a deterministic
250K-origin monitor prefix every 25M origins, and runs paired fixed training
and complete validation
evaluations at each epoch boundary. Every completed epoch writes an immutable
`checkpoints/checkpoint_epoch_NNNN.pt` alongside latest and best-validation
checkpoints.

Run final full-catalog chunk training (Medium by default):

```powershell
python -m research.bar_gpt.v2.run_train_full_chunks
python -m research.bar_gpt.v2.run_train_full_chunks --prepare-manifest-only
python -m research.bar_gpt.v2.run_train_full_chunks --execute
```

The default `production` run stamp is stable: rerunning the same `--execute`
command resumes `checkpoint_latest.pt`. Use `--run-stamp NAME` only when
starting an independent run.

The launcher freezes every eligible 2019-2025 block into the training
authority and keeps disjoint 2026 monitor-pool, validation, and locked-test
authorities. Every outer epoch consumes every training block exactly once.
The manifest binds that complete population by certified catalog hash and
summary; the loader streams the immutable units directly instead of copying
roughly one million block-reference records into every worker.
The worker-owned stream receives a new deterministic shuffle each epoch and
is divided into block-aligned boundaries averaging 30M origins; blocks are
never split. A different stratified 1M monitor panel is evaluated after each
chunk, while the fixed 5M validation panel is evaluated only at the outer
epoch boundary. The next epoch's lightweight monitor/chunk metadata plan is
prepared concurrently without reading shard tensors. Training stops after at
most ten epochs or two non-improving complete validations. W&B continues to
use cumulative `samples_seen` as its step; epoch and chunk position are logged
as additional `train_progress/*` fields.

The full trainer uses block-aligned cosine annealing. After the initial 4M
origin warmup, each complete chunk is one cosine cycle and the next chunk
restarts at that outer epoch's peak learning rate. The peak is unchanged
between chunks in the same epoch and decays by `0.95` only when a new outer
epoch starts; the minimum learning rate remains `3e-5`. Because cosine
progress follows completed blocks, variable origins per block cannot drift
the restart away from the real chunk boundary. Rich displays the active
chunk-cosine phase, progress, epoch peak, and epoch decay.

Run the paired data-diversity/repetition experiment after the baseline
comparison finishes:

```powershell
python -m research.bar_gpt.v2.run_train_repetition_comparison
python -m research.bar_gpt.v2.run_train_repetition_comparison --prepare-manifest-only
python -m research.bar_gpt.v2.run_train_repetition_comparison --execute
```

This separate launcher does not alter the baseline comparison. It requires the
existing baseline `fixed_panels_v2.json` and fails closed if that exact parent
manifest is absent; it never creates or replaces the baseline manifest. The
`--prepare-manifest-only` command creates only the child repetition manifest.
That child derives a deterministic nested 25M-origin training panel from the
baseline manifest; audits ticker, year, month, activity-regime, session-phase,
and block-length
distributions, and exposes that exact panel for four deterministically
reshuffled epochs. The model therefore sees approximately the same 100M total
origins as the baseline but only one quarter as many unique origins. Model
profiles, initialization seed, cumulative learning-rate schedule, W&B project,
and metric names remain identical. Its monitor and validation block lists are
copied exactly from the baseline and verified by exact equality, while the
parent manifest hash is recorded as experiment provenance. Epochs
1-3 end with the bounded monitor; the expensive paired training/full-validation
audit runs only after epoch 4.

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
`return_class_analysis/direction_3class_1bp_v2`; reruns skip only unit results
bound to the same manifest and three-class contract. The overfit report is
written under `overfit_pilot_3class_1bp_v1`, separately from obsolete
five-class experiments.

## Interpretation and safety

See `METRICS_REFERENCE.md` for metric formulas and ranges. Three-class balanced
accuracy is macro recall over classes with actual support. A value of one third
is the natural three-class chance reference only when all three classes are
represented and predictions are uninformative. Normal W&B logging keeps
close-return balanced accuracy and MCC; detailed class supports are retained
only by the overfit artifact that requires them for its pass gate.

The model remains causal: full attention uses native causal SDPA, while masked
or local attention supplies an explicit lower-triangular/local allowed mask and
therefore correctly sets `is_causal=False` to avoid combining two masks. Shard
availability and as-of indices remain the point-in-time authority.
