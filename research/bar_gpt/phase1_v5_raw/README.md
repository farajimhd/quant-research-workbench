# V5 Raw Baseline

Independent baseline for the approved V5 D128 -> E256 model. It does not change
or resume the transformed V5 run. Fresh initialization and a new output/W&B run
are required. Full training has not been launched by this implementation.

## Data and architecture

Use the same frozen corrected balanced phase-one split, source hashes, row order,
worker lanes, 64-bar windows and source-valid masks as V5. Shards are unchanged.
The 18 market inputs and targets remain in stored native units: prices in dollars,
activity in volume/size/count units. No log, local-reference division, centering,
standardization or inverse reconstruction is applied to market values. Existing
split adjustment in the source contract remains intact; this is not unadjusted SIP.
Invalid values are zero placeholders and excluded from losses/metrics.

Nine existing UTC/timeframe/relative-position features retain their original
transformations. Randomly remove 48 bars before attention; 16 x 27 visible inputs
enter the same six-layer, width128, eight-head, FF512 encoder and mean-pool/export
to256. Keep 7.5% visible-market feature dropout. Evaluation uses all64 bars and
no feature dropout. There is no reference token, reference head or decoder skip.
The small position-conditioned width128 MLP reconstructs 18 fields for requested
positions. It uses FP32 readout to avoid raw-price BF16 quantization. Training
decodes only the48 hidden positions; full evaluation decodes all64.

## Objective and metrics

Compute Huber (smooth L1, delta1 native unit) between raw predictions and raw
targets, only for source-valid masked entries. Average each channel over its
observed entries, multiply by the inverse of its frozen training-only standard
deviation (floor0.01), then average the12 price channels and6 activity channels
separately. Total loss is 0.5*price + 0.5*activity. These are loss weights only:
the model never receives normalized market values or reference statistics.
Calibration uses the same bounded six-frame training sampler; its evidence and
split hash are sealed. Validation calibration and transformed V5 calibration
files are rejected. Weights never refit on validation or resume.

Validation reports masked objective separately from all-visible raw MAE for each
frame/channel, price MAPE/median/P90/price bands, source-valid coverage and finite
checks. Raw predictions are compared directly to raw targets. Negative activity
predictions are counted, not silently clipped. W&B project is
`bargpt-bar-mae-v5-raw`; run name follows the new output directory.

## Execution

The launcher defaults to profile mode and refuses to overwrite a run. Training
requires `--mode train --execute`. It preserves V5 AdamW, schedule, clipping,
full validation, same-run resume and bounded rolling last/best checkpoints.
Resume contracts reject transformed V5 checkpoints. CPU prep uses256-row chunks
inside the original optimizer batch. Full validation and resume paths are tested
independently; native GPU performance still needs qualification without competing
with the currently running V5.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m research.bar_gpt.phase1_v5_raw.run --mode profile --output D:/TradingML/runtimes/bar_gpt/v5-raw-profile-NEW --encoder-width 128 --embedding-width 256 --batches 1024,4096,8192 --workers 32 --prepare-threads 1 --prefetch 4 --precision bf16 --steps 24 --warmup 4 --wandb-mode disabled --source-revision COMMITTED_SHA
```

Do not launch GPU profiling alongside another trainer. The implementation is an
experimental baseline, not a claim that raw-scale reconstruction learns better.
