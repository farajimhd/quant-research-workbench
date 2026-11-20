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

## Executed qualification and full-run handoff, 2026-11-20

The approved16D model has141,762 parameters (encoder141,456; affine decoder306). Calibration used4,326,784 training-subset bar exposures; maximum native round-trip error divided by max(1,abs(target)) was9.54e-7. Calibration and original split identities are sealed in `D:/TradingML/runtimes/bar_gpt/phase0-v1-singlebar-20261120/calibration.json`.

The initial constant1e-3 easy-case test oscillated and was rejected. A CUDA-capture failure caused by constructing CPU loss constants was fixed with a registered device buffer. Revised MLP easy-case loss fell2086.05→0.03108 under a4000-step3e-4→1e-6 cosine test; the linear control finished1.76995 and is not selected. These are synthetic repeated-bar qualification results, not held-out quality claims. Two local model tests passed; production GPU eager/captured loss and gradient parity passed across the profiling configurations. Inference graph parity is checked in the real validation entry point.

Profiles used the complete certified loader, GPU preparation, backward and optimizer. Longer profiles measured223K windows/s at2048/16 workers,244K at4096/16,204K at8192/16, and216K at4096/8. Startup was≈28s for16 workers; measurements exclude that startup. Bigger batches and more workers were not universally faster. Selected training batch4096 windows means262,144 independently encoded bars. Existing bounded pinned staging, prefetch4,16 I/O workers and CUDA graphs are used; no source data rewritten. FP32 precision was retained throughout.

Full run: `D:/TradingML/runtimes/bar_gpt/phase0-v1-singlebar-20261120/full-mlp16-20ep`. W&B: https://wandb.ai/mehdifaraji/bargpt-phase0-singlebar-v1/runs/yhajxmps . Immutable training source: `768dcc4332cd2b2cbcfa43d786ee60d539eaae58`. The requested20 epochs use all186,575,722 windows/epoch and all64 bars, with cosine1e-4→1e-6, no masking, no weight decay. Full original validation runs each epoch; the fixed development subset runs every2000 updates. Checkpoint interval1000 updates.

An intentional50-update prefix validated real training, inference, W&B uploads and full-state checkpoint save/load. Its checkpoint was preserved under `resume-check-50`; the same run resumed with optimizer, RNG, traversal and scheduler continuity. Exact launch/resume commands and ownership/exit receipts are under the sibling launcher directory and runtime root. No recurring training automation was created. The existing manually triggered workstation panel task was reused after preserving its previous script.

At the verified step6000 development evaluation, loss fell667.40→14.88 and1s trade-close MAE$54.10→$0.706. Moving candle direction remained≈49.94%; range-bin accuracy≈4.32%. Thus early scale learning is confirmed, while fine geometry and16D sufficiency are NOT established. Live intervals were≈200–260K windows/s (12.8–16.6M bar exposures/s); remote W&B readback confirmed the metrics and running identity. Full training and the first full-population epoch validation were not complete at this handoff. The earlier Phase2 run remains stopped.
