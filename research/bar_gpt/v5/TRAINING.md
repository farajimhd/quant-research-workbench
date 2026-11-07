# Phase-one market-only pretraining

`run_pretrain.py` preserves the agreed large-model campaign. Run it only when
ready to start training, from a committed workstation code snapshot:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -u research/bar_gpt/v5/run_pretrain.py --output D:/TradingML/runtimes/bar_gpt/mae-v6-new/run
```

The output directory must be fresh. The launcher prints the equivalent command;
subsequent flags override its preset. No training runs merely by importing it.

| Setting | Preset |
|---|---|
| Dataset | `D:/TradingML/runtimes/bar_gpt/shards-2019-2025-v6` |
| Input | 64 real bars; 18 market channels and nine UTC time features |
| Model | Large: width 512, six encoder layers, eight attention heads |
| Attention | SDPA; attention dropout 0, residual dropout 0.1 |
| Precision | BF16 autocast; FP32 parameters and AdamW state |
| Batch / workers / prefetch | 4096 / 32 / two batches per worker |
| Duration | 50 full training passes; full validation after each epoch |
| Masking | 60% of market columns; all time context remains visible |
| Learning rate | 1,000-update linear warmup; epoch-wise cosine from 1e-3 to 5e-5 |
| Reporting | Loss observations every 10 updates; upload with metrics every 1,000 |
| Tracking | Online W&B project `bargpt-v5-mae-market-only-v6`; workstation panel enabled |
| Checkpoints | Every epoch, best validation, and atomically replaced last state |

The split freezes the currently published receipt inventory. Newly generated
shards do not enter an existing run. Prefer waiting for the intended extraction
scope before creating the final training split. Dates are split chronologically
near 80/20, and entire 64-bar contexts crossing the boundary are purged, including
hourly/daily contexts and gaps. Activity scaling is fitted on training data only.
The split binds the v6 dataset contract, receipt hashes and file hashes. A bad v6
trade bar is fatal; it is never silently counted as a no-price exclusion.

Use a fresh split and calibration for v6. Old 21-feature checkpoints are
incompatible and cannot resume this model. For a subsequent compatible resume,
pass `--resume <checkpoint_last.pt>` plus the original `--split-manifest` and
`--calibration` into a new output directory. Keep model, data, worker count and
schedule unchanged. Model, optimizer, scheduler, RNG states and metric history
are restored. An interrupted training epoch replays from its beginning; a saved
validation phase resumes validation without repeating that epoch's training.

Phase one still trains the masked encoder and reconstruction decoder. For phase
two, retain `model.encoder` and call `encode_full` on scaled `[B,64,18]` market
values, masks and `[B,64,9]` time context. It returns `[B,512]` for the large model
without executing the decoder and preserves gradients for joint fine-tuning.
