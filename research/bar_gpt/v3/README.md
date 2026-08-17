# BarGPT v3

BarGPT v3 is a warm-started objective and evaluation revision of v2. It keeps
the certified v12 shards, loader, causal decoder, model dimensions, batching,
chunk replay, optimizer type, scheduler shape, and operational runtime design.
Generated output belongs under `D:\\TradingML\\runtimes\\bar_gpt\\v3`.

## Intentional changes from v2

- Production training uses two outer epochs.
- A new run must specify `--initialize-from-v2-checkpoint`; every exact
  shape-compatible model tensor is copied and the optimizer, scaler, and
  scheduler start fresh. The migration report records copied, ignored, and
  deterministically initialized tensors plus the source checkpoint SHA-256.
- Three-class return heads and their cross-entropy losses are removed.
  Direction metrics remain available by applying the frozen v2 thresholds to
  continuous predicted and realized returns. Re-score v2 using the same rule
  for an apples-to-apples comparison.
- Log volume and log trade-count targets remain in both AR and physical paths.
- Every AR view predicts a six-class time-to-next-event distribution. The AR
  mark head conditions on the predicted gap distribution, marginalized by its
  probabilities; observed future time is never supplied.
- Physical horizon heads receive a zero-initialized residual built from known
  New York target-clock features. Warm-start behavior therefore initially
  matches v2 for every retained output.
- Each retained loss family is independently normalized, averaged within the
  family, and summed with coefficient one. There are no manual loss weights.

## Global validation

Every 500,000,000 training origins, independently of chunks and outer epochs,
the trainer pauses updates and writes:

`checkpoint_global_validation_origins_<samples_seen>.pt`

The file is immutable and fully written before the frozen 2026 validation
panel is evaluated. `artifacts/global_validation_runs.jsonl` binds the exact
checkpoint path and SHA-256 to the model/learning contract, sample boundary,
experiment-manifest hash, validation metrics, and completion time. This makes
the checkpoint reusable for later evaluation on different datasets.

`checkpoint_best_global.pt` is promoted only when trade-close MAE improves and
close MCC, trade high/low range MAE, and trade quantile calibration do not
regress. Composite total loss is never a promotion criterion. The exact
decision is stored in `artifacts/global_checkpoint_selection.json`.

The complete validation panel remains the global authority. Per-chunk panels
remain local stopping signals and must not be compared across chunks.

## Production launcher

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m research.bar_gpt.v3.run_train_full_chunks `
  --initialize-from-v2-checkpoint D:\\path\\to\\stable-v2-checkpoint.pt `
  --execute
```

The launcher resumes its named v3 `checkpoint_latest.pt` when present. A new
run fails closed without the v2 initialization checkpoint. The source file is
read through short-lived handles only; no checkpoint handle is retained during
training.

The full-training launcher pins the v2 production optimizer and scheduler as
explicit defaults: peak learning rate `3e-4`, weight decay `0.1`, gradient
clip `1.0`, BF16 AMP, 4M-origin warmup, `epoch-chunk-cosine`, 100M-origin
cosine cycles, minimum learning rate `1e-5`, and outer-epoch peak decay `0.95`.

## Diagnostic evaluation

The global checkpoint is suitable for fixed diagnostic panels covering
liquidity/activity, session phase, long gaps and cross-session transitions,
price levels, market capitalization, and unusual market conditions. Such
panels must carry frozen manifests and authoritative grouping provenance;
market-cap or price buckets must not be inferred from ticker identity.

All unchanged storage, loader, causal-availability, condition-target, and
full-chunk contracts remain documented in `research/bar_gpt/v2/README.md` and
are inherited without modification.
