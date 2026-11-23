# Phase 0 V5: residual MLP baseline with V4 coordinates

This controlled baseline changes the feature-attention encoder to V1-style
residual dense layers. It starts from random weights, never V4 weights.

Each independent bar supplies 27 triples (standardized coordinate, coordinate
validity, source validity), flattened to 81 scalars. The encoder is
81→128→two pre-normalized residual blocks (128→256→128)→16.
The decoder is a single 16→18 affine layer: no input skips or target references.
There are 145,218 parameters, including 306 decoder parameters.

The V4 transform, source-valid objective, calibration, dataset, metrics and
schedule are retained verbatim. Prices use log close and OHLC differences.
Activity uses the predicted symmetric quantity reference and differences,
with zero-safe log1p volume/count coordinates. Every inverse reference is
predicted through the bottleneck. Time metadata is context, not a target.
The reconstruction objective is equal price/activity SmoothL1 in log units
(price residuals ×1000), plus 0.1 standardized-coordinate loss. There is no
masking of this single-bar reconstruction task.

The version-local trainer/metrics/panel are copied from V4 to freeze the
comparison semantics. `configure_execution` accepts legacy attention profiler
arguments for compatibility, but they have no effect on this attention-free
model. FP32 is the default; optional BF16 applies only to hidden block layers.
CUDA graphs retain FP32 output and optimizer state and do not omit tail data.

Run from a committed, pushed, hash-verified workstation snapshot:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m research.bar_gpt.phase0_v5.run_train --output D:/TradingML/runtimes/bar_gpt/UNIQUE-RUN --source-revision FULL-COMMIT --calibration D:/TradingML/runtimes/bar_gpt/phase0-v4-setup-20261123/calibration.json --epochs 20 --batch-size 4096 --workers 8 --validation-batch-size 1024 --validation-workers 8 --chunk-bars 262144 --optimized --precision fp32 --training-panel-metrics --validation-every 1000 --checkpoint-every 500
```

Use `launch --console-log EXISTING-RUNTIME-DIRECTORY/console.log` for detached
Windows execution, with the same remaining arguments. W&B project is
`bargpt-phase0-v5-mlp16`; checkpoints stay on disk, not W&B. The progress panel
uses `python -B -m research.bar_gpt.phase0_v5.panel --run RUN --keep-open`.
The launcher records source/data/calibration hashes, fresh initialization,
optimizer and traversal state. STOP requests preserve a resumable checkpoint.
Reconstruction quality does not establish forecasting usefulness.

## Workstation baseline, 2026-11-23

Source snapshot: `10d06cbfc1a5bb49f1d83d35e0776b308d5bef4a`.
All 31 focused tests passed, followed by a real full-loader CUDA smoke run.
Transforms, loss, metrics, data adapter and scheduler are byte-identical to V4.
The previous attention run `c75kl8i7` stopped gracefully at step 11,811; its
checkpoint and logs remain in its original runtime directory.

Measured complete-pipeline profiles (warm steady-state, not epoch averages):

| Precision | Windows/batch | Workers | Windows/s | Loader startup |
|---|---:|---:|---:|---:|
| FP32 | 4,096 | 8 | 156,330 | 13.6 s |
| BF16 hidden layers | 4,096 | 8 | 168,588 | 13.7 s |
| FP32 | 8,192 | 16 | 159,507 | 28.2 s |
| FP32 | 4,096 | 16 | 154,449 | 27.1 s |

Selected FP32/4096/8 to preserve the precision and batch comparison with V1
and the original V4 FP32 run. The BF16 gain was only 7.8%; gradient relative
L2 drift was 0.00243. FP32 graph parity passed. The larger batch gain was
small, and 16 workers doubled startup without improving 4096-batch speed.

Run: `phase0-v5-mlp16-fp32-random-full20-20261123`, W&B
`mehdifaraji/bargpt-phase0-v5-mlp16/rz5xaifz`. It uses fresh seed-17 weights,
20 epochs, cosine 1e-4→1e-6, prefetch 4, development/training-sample metrics
every 1,000 updates, checkpoint every 500, and full validation each epoch.
Both train and validation cover the unchanged Phase1 split. Detailed profiles,
selection rationale and exact launch command are under the workstation runtime
`bar_gpt/phase0-v5-setup-20261123/`. Learning quality remains to be measured;
these speed results are not evidence that the new representation is better.
