# Phase1 V14: input-only normalization

Hypothesis: conditioning of the frozen16D Phase0 embeddings changes optimization and16-visible/64-visible generalization. This experiment varies only the affine input transform: training-only z-score, identity, training min–max, and training median/IQR. It does not test different reconstruction loss weights. Encoder `mean`/`scale` continue to define the same z-scored target and inverse in every arm; separate `input_center`/`input_scale` buffers define the visible input transform.

All arms start from identical fresh weights for a specified seed, use the original V12 attention (not V13 normalized attention), width128/two layers/four heads,256D export and unchanged lightweight decoder. Only the exported embedding reaches the decoder. Masking selects16 random bars per window and batch. LR3e-5, AdamW weight decay0.01, batch1024,15000 updates, identical sample indices and masks. No pretrained warm-start surgery or target-derived references. Metadata is unchanged.

The fixed67606-window training and11146-window development subsets and pinned Phase0 epoch19 teacher are inherited from V12. Statistics use source-valid training bar embeddings only. No clipping or winsorization. Min–max validation values outside0–1 are counted. Zero ranges/IQRs fail explicitly. All16 channels' offsets/scales and calibration/split/teacher hashes are retained in the runtime manifest. The full original run remains stopped.

Each arm evaluates masked and all-visible reconstruction every3000 updates, audits all18 raw channels/six timeframes/directional and range statistics at the end, evaluates the complete training subset for the gap, and saves one final model/optimizer/RNG checkpoint and hash. Teacher targets are cached in GPU memory; throughput is cached training speed, not full raw-data pipeline speed. The15-minute per-arm training deadline and new output STOP bound execution. Initial arm failures are not complete comparisons. Each arm has a unique W&B run in `mehdifaraji/bargpt-phase1-v14-input-normalization`.

Run from a pushed, hash-verified immutable workstation snapshot with `PYTHONDONTWRITEBYTECODE=1`:

```powershell
python -B -u -m research.bar_gpt.phase1_v14.experiment --output D:/TradingML/runtimes/bar_gpt/phase1-v14-inputnorm-seed17-20261121 --revision SOURCE_SHA --seed 17 --steps 15000
```

Use a new output for any repeat. `--modes zscore,robust --seed 29` permits a matched second-seed finalist comparison, chosen only after inspecting the first comparison. No full-data restart or forecast-usefulness claim is authorized by these bounded results alone.

Local tests verify exact z-score parity with V12, unchanged loss/inverse when only input statistics change, hidden-token isolation, validity-aware calibration, round trips and no clipping of out-of-range values.

## Completed comparison (2026-11-21)

Seven arms completed15000 updates: all four transforms with initialization seed17, then z-score/identity/robust with seed29. Data and mask sampling seed555 was held fixed across both initialization seeds. All used source `cff65408390cbe811a833340faeb02c00f166b57`. Runtime directories are `phase1-v14-inputnorm-seed17-20261121` and `phase1-v14-inputnorm-seed29-20261121` beneath the workstation BarGPT runtime root. Nine local tests passed. Actual workstation training, complete subset validation, checkpoint hashes and remote W&B summaries were verified; no experiment process remains live.

Each entry below is seed17 / seed29, except min–max which was rejected after seed17. Dollar errors refer to1s trade-close on the11146-window development subset, not full-population validation or the prior pretrained best model.

| Input transform | Masked latent loss | Masked MAE | All-visible MAE |
|---|---:|---:|---:|
| z-score |0.067955 /0.067941|$0.3144 /$0.3142|$78.99 /$75.55|
| none |0.068021 /0.068110|$0.5564 /$0.8329|$39.34 /$122.53|
| median/IQR |0.067973 /0.067938|$0.5848 /$0.3812|$67.42 /$54.66|
| min–max |0.089340|$16.2711|$57.42|

Across108 timeframe/channel raw-MAE groups, identity worsened masked error in91/88 groups across the two seeds. Its apparent all-visible advantage did not replicate: improved100 groups in seed17 but only25 in seed29. Robust scaling worsened masked error in84/83 groups, but improved all-visible error in90/89 groups. Its balanced geometric-mean raw-MAE ratio versus z-score was1.318/1.150 masked and0.808/0.854 all-visible. This is a consistent trade-off, not an overall win. Worst robust masked regression was2.14x in seed17 and1.84x in seed29; worst all-visible regression was1.30x/1.48x. Min–max worsened107/108 masked groups, with a9.93x geometric-mean ratio and54.39x worst-group error.

Keep z-score as the masked-reconstruction default and robust scaling as a retained alternative for later architecture comparisons. Reject min–max for this setup; identity does not establish a repeatable overall advantage. No transform resolved the16-visible to64-visible generalization gap, which also occurs on the training subset. These findings do not establish a forecast-usefulness improvement or justify restarting full training. Target loss weighting was deliberately unchanged and remains a separate untested variable.

Observed cached training throughput was149K–159K windows/s; each arm took100–107seconds including its evaluations/checkpoint work, excluding shared cache/stat preparation and W&B setup. This is not full-loader throughput. Calibration retained one min–max development coordinate outside0–1 without clipping. Maximum input round-trip absolute error was1.14e-5 for min–max,3.81e-6 for z-score/robust, and0 for identity. All final detailed audits recorded zero nonfinite predictions. Source datasets, previous checkpoints and human STOP markers were preserved.

Each run directory contains `manifest.json`, `comparison.json`, `balanced-comparison.json`, per-arm `result.json`, `checkpoint.pt` with recorded SHA256, `detailed-development.json`, and `detailed-scalars.json`. Seed17 also contains the combined `wandb-readback.json`. Final W&B summary reconciliation used those saved measured metrics after an initially lagging readback; no training steps or model weights were changed. W&B project: [input normalization comparison](https://wandb.ai/mehdifaraji/bargpt-phase1-v14-input-normalization).

For a reproducible retained z-score candidate, seed29 checkpoint SHA256 is `f432783d6d2a57c79657a5f2a8d8c772aecdc4c7eb6f8e7f61859857de813333`; location `phase1-v14-inputnorm-seed29-20261121/zscore/checkpoint.pt`. Reproduce from the pinned source with `--seed 29 --modes zscore --steps 15000` and a new output directory. This is a bounded experimental checkpoint, not a recommended full-training restart.
