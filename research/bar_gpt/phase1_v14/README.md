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
