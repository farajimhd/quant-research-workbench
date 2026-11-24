# Phase 0 V6: 32D bottleneck ablation

V6 copies the optimized Phase 0 V5 implementation and changes only the default
embedding dimension from 16 to 32. Run/module/project names distinguish the new
experiment. Initialization is fresh seed 17, with no checkpoint resume.

Architecture: 81 inputs → width 128 → two residual MLP blocks → **32D** →
affine 18-coordinate decoder. The readout grows from 128→16 to 128→32 and the
decoder from 16→18 to 32→18. Total parameters: **147,570**, including 594 in
the decoder. No input bypass reaches the decoder.

Transforms, calibration, loss, all metrics, split, masks, precision and schedule
remain unchanged. The full Phase1 training population is used for 20 epochs,
FP32, batch 4096, eight workers, prefetch four, cosine 1e-4→1e-6. Validation
batch is 1024 with eight workers and complete-metric CUDA graph replay.
Development checks run every 1000 updates, checkpoints every 500, logging every
100. Full validation runs every epoch. Training uses transfer overlap.

The V5 infinite prediction-variance reporting failure is NOT silently fixed in
this dimension-only ablation. If it recurs, the existing fail-visible behavior
and resumable checkpoint remain; it must not be interpreted as completed
validation or hidden by replacing nonfinite measurements with zeros.

From the committed, hash-verified workstation snapshot:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m research.bar_gpt.phase0_v6.run_train --output D:/TradingML/runtimes/bar_gpt/phase0-v6-mlp32-fp32-random-full20-20261124 --source-revision FULL-COMMIT --calibration D:/TradingML/runtimes/bar_gpt/phase0-v4-setup-20261123/calibration.json --epochs 20 --embedding 32 --batch-size 4096 --workers 8 --validation-batch-size 1024 --validation-workers 8 --prefetch 4 --chunk-bars 262144 --optimized --precision fp32 --graph-validation --training-panel-metrics --validation-every 1000 --checkpoint-every 500 --log-every 100 --learning-rate 1e-4 --min-learning-rate 1e-6 --seed 17
```

Detached execution uses `launch --console-log EXISTING-RUNTIME-DIR/console.log`
with the same remaining arguments. W&B project: `bargpt-phase0-v6-mlp32`.
Checkpoints stay on disk. The panel is `python -B -m
research.bar_gpt.phase0_v6.panel --run RUN-DIRECTORY --keep-open`.
