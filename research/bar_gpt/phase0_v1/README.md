# Phase0 V1 single-bar autoencoder

User-approved first experiment:16-dimensional embedding,20 epochs over all certified Phase1 windows and all64 bars independently. One training exposure is one bar; overlapping/repeated bars in the certified window population are retained. No temporal attention, bar mixing, masking, local reference or input-to-decoder bypass. This is reconstruction pretraining, not forecasting.

Each trade/bid/ask family supplies absolute log close, log(open/close), log(high/max(open,close)), log(min(open,close)/low), log1p(quantity), log1p(count). The18 coordinates plus9 existing time fields are standardized by fixed training-only means/stds and concatenated with27 source validity flags. A width128 two-block residual MLP maps54 inputs to16 embedding dimensions. The decoder is a single16-to18 affine layer. Entire model/readout isFP32. Output wick/activity coordinates are clamped nonnegative (never source values); bid/ask ordering uses the existing prediction-only projection. A coordinate loss on unprojected decoder outputs still trains negative coordinates toward valid targets. Exact-zero bodies/wicks remain representable.

Loss: equal price/activity SmoothL1 means, price residuals1000*log units and activitylog1p units, plus0.1 standardized coordinate loss. No true scale/median is needed for inverse reconstruction. Missing fields follow existing deterministic coordinate completion; source-valid masks control native reconstruction. No extreme valid observations are clipped or dropped.

Calibration uses all bars of the existing frozen67,606-window training subset after manifest/chronological identity verification; it does not use development/validation. Training uses all186,575,722 certified windows per epoch, validation all47,750,007. Development diagnostics use the fixed11,146-window subset and are explicitly labelled. The16-dimensional bottleneck is smaller than18 market features, so lossless recovery of arbitrary independent features is not guaranteed.

Entry points (set `PYTHONDONTWRITEBYTECODE=1`; all outputs under workstation runtimes):

```
python -B -m research.bar_gpt.phase0_v1.prepare --output CALIBRATION_JSON
python -B -m research.bar_gpt.phase0_v1.run --mode qualify --output PROFILE_RUNTIME --source-revision SHA --calibration CALIBRATION_JSON
python -B -m research.bar_gpt.phase0_v1.run --mode train --output TRAIN_RUNTIME --source-revision SHA --calibration CALIBRATION_JSON --epochs 20 --batch-size WINDOWS --workers WORKERS
```

Qualification compares a linear16-dimensional control with the nonlinear model on an easy repeated-bar case, then verifies eager/CUDA-graph gradient parity and profiles six batch/worker configurations on the production loader. Full training uses measured batch/workers, bounded pinned staging/prefetch, CUDA backward graphs and fused AdamW, with cosine1e-4→1e-6. Checkpoints preserve optimizer, RNG, exact worker traversal cursor and consumed-window scheduler progress. `STOP` gracefully checkpoints; `--resume checkpoint_last.pt` requires identical scientific contract and preserves W&B identity. Rolling last/best checkpoints only; one full validation report per epoch.

New W&B project: `mehdifaraji/bargpt-phase0-singlebar-v1`. Metrics include train loss/speed/wait/memory and development/full-validation native MAE, signed errors, percentage errors with explicit nonzero counts, candle direction, adjacent-close reconstruction direction, high-low range accuracy, exact-zero classes and±0.05% neutral-band accuracy. Signed-magnitude bins follow Phase1 diagnostic boundaries0,.05,.1,.25,.5 percent. All these are input reconstruction metrics. A large neutral population can inflate neutral-band accuracy. Complete confusion matrices/counts are saved in runtime JSON for classwise interpretation.
