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
