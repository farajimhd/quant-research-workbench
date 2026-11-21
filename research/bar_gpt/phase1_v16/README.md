# Phase1 V16: direction retention

Frozen Phase0 16D inputs and the existing36 metadata fields are unchanged. Every encoder exports exactly256 learned values. No raw features, references, directions, summary statistics or deterministic slots are appended to that vector. Every decoder takes only that vector. Target direction appears only in the loss.

The bounded campaign compares a matched continuation control with a log-body reconstruction objective, then tests decoder compression and positional readout if necessary. Data, train-only latent z-score, teacher, stable count-balanced attention and seed are inherited from V15. Every fourth training batch is all-visible; other batches select16 random bars. This common exposure follows the prior measured comparison, not a silent change to the stopped full run.

Arms: `base` is the V15 architecture/objective; `body` adds0.1 times mean SmoothL1 error in log(O/C) measured in log-basis-point units. Source-valid open/close pairs only participate; visible bars receive weight1 and hidden bars0.1. The frozen Phase0 decoder's log-body coordinate avoids subtraction cancellation. `linear_body` removes the secondary256-to64 decoder compression using a single256-to1024 linear map. `slots_body` additionally aligns learned token states by original bar position and projects the flattened states to256. Empty hidden positions are zeros inside the encoder, not decoder inputs. It has2,640,896 parameters versus339,088 baseline and576,512 linear-decoder-only. Body/linear-body use the same CLS encoder; the positional projection is a separately labelled architecture/capacity experiment.

All compatible parent parameters are loaded from V15 seed17; changed heads are freshly initialized and listed in an initialization receipt. All arms reset AdamW at3e-5, batch1024, seed17 and sampling seed559. This is not an optimizer-state resume or a pure decoder-only ablation where initialization differs. Each arm is bounded to30minutes, normally15K updates, with500-step single-training-window overfit diagnostics on a cloned model, periodic validation, complete development raw metrics, W&B and final checkpoints. No full-data run is automatically launched.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -u -m research.bar_gpt.phase1_v16.experiment --checkpoint D:/TradingML/runtimes/bar_gpt/phase1-v15-stable-seed17-20261121/stable_balanced/checkpoint.pt --output D:/TradingML/runtimes/bar_gpt/NEW_DIRECTION_RUN --revision SOURCE_SHA --steps 15000 --arms base,body
```

GPU-cached subset throughput excludes raw loading and Phase0 encoding. Compare moving-candle direction and range agreement alongside all18 native-unit errors, masked versus all-visible, and teacher controls. Hidden random-bar directions may be inherently unpredictable; all-visible and visible-only reconstruction must be distinguished from forecasting. Improvement requires no target inputs or embedding augmentation. Any winner needs broader verification before a full-training recommendation.
