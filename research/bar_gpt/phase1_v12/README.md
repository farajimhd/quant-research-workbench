# Phase1 V12: frozen Phase0 latent-target MAE

User-approved experiment: freeze the completed Phase0 V2 epoch20 single-bar encoder, reconstruct its64x16 latent sequence from16 randomly selected bars, train20 epochs over exactly the same certified training population, and validate on exactly the same full validation population. This is temporal reconstruction pretraining, not forecasting.

The teacher checkpoint must have epoch20, completed traversal/scheduler state, matching split hash/counts, a verified checkpoint proof, completed run status, and completed full validation020. Calibration pins an independent hash-verified copy under the new runtime. No earlier checkpoint or best-development substitute is allowed. Teacher weights remainFP32 and frozen. All pretrained artifacts remain intact.

## Model and objective

Each64-bar window becomes64x16 frozen bar embeddings. Fixed means/stds are fitted only on valid bar exposures of the already frozen67606-window training subset. No local references or target-derived inversion metadata are used. Existing time features and27 source-valid flags are supplied only for the16 visible positions. Learned position identities retain the original64-bar slots; hidden bars are physically absent from encoder attention. Each window/batch gets a new uniformly random16-position mask. The same16 positions apply to all dimensions within one window.

Default temporal encoder: width128, two pre-normalized self-attention layers, four heads,2x feedforward expansion, one CLS readout,256-dimensional exported window representation. Temporal encoder usesBF16; latent normalization, exported readout, teacher and decoder stayFP32. The decoder projects256D to64D, adds learned fixed position queries, and uses a shared64-to64-to16 MLP. Its only example-dependent input is the256D representation. No selected-token bypass, true latent reference, raw value or metadata reaches the decoder.

Loss is normalized-latent SmoothL1 on48 hidden positions plus0.1 times visible-position loss. All16 latent dimensions are trained; a target bar is eligible if any original market feature is valid. This is necessary because latent dimensions mix features and have no individual source-feature validity mask. Original market masks remain unchanged for raw diagnostics. No source observations are clipped or sanitized. Full frozen bar exposures, including repeated overlapping windows, remain in the population.

The training schedule is20 epochs with AdamW, weight decay0.01, clipping norm1,1000-update warmup, then consumed-window cosine3e-4 to3e-6. Seed17. Batch/workers are selected by end-to-end profiling. Frozen teacher online inference, raw GPU preparation, bounded CPU prefetch/pinned staging, CUDA backward graphs and fused optimizer are included in profiles. A complete FP32 latent cache alone is~960GB before metadata; the runtime drive had~631GB free at preparation. No alternate storage or lossy cache is silently substituted.

## Evaluation and reproducibility

Full validation uses all47750007 original Phase0 validation windows; training uses all186575722 windows each epoch. The chronological/purged split authority remains `phase1-v6-cohort-balanced-v4/split.json`. Full validation runs after every epoch. The existing11146-window development subset runs every2000 updates and is explicitly labelled. Fixed validation generator seed917 makes masks repeatable for a fixed loader configuration and does not alter training RNG. Source/count/hash admission is mandatory.

Masked and all-visible latent SmoothL1, MSE, MAE, target/prediction variance, training-derived zero-normalized constant and visible-context mean controls are recorded by timeframe. Raw native-unit, price-relative, candle direction and range diagnostics pass predictions through the frozen Phase0 decoder. The same decoder applied to true Phase0 embeddings supplies a labelled teacher reconstruction reference. This uses targets for evaluation only, never decoder inputs or training inverses. Teacher diagnostic aggregates are computed once per immutable validation/development traversal and reused, since teacher weights, population, masks and decoder never change. Student diagnostics are recomputed every evaluation. Raw diagnostics do not enter the latent objective.

Bounded rolling last/best checkpoints preserve model, optimizer, all RNG state, traversal cursor, scheduler progress and scientific contract. Resume uses the same W&B identity and rejects changed science. No automatic scheduled jobs. A detached finite launcher writes exact command, owner creation times and exit receipts. Human STOP files remain authoritative. The original Phase0 run must finish naturally before GPU ownership passes to this experiment.

Commands run from a committed, pushed, hash-verified immutable workstation snapshot. Set `PYTHONDONTWRITEBYTECODE=1`; Python launchers also set it. All output paths are under `D:/TradingML/runtimes/bar_gpt/phase1-v12-frozen16-mae-20261121`:

```
python -B -m research.bar_gpt.phase1_v12.run --mode calibrate --output ROOT/teacher --source-revision SHA --teacher PHASE0_RUN/checkpoint_last.pt
python -B -m research.bar_gpt.phase1_v12.run --mode qualify --output ROOT/profile --source-revision SHA --calibration ROOT/teacher/calibration.json
python -B -m research.bar_gpt.phase1_v12.launch --mode train --output ROOT/full-20ep --source-revision SHA --calibration ROOT/teacher/calibration.json --epochs 20 --batch-size PROFILED_BATCH --workers PROFILED_WORKERS
python -B -m research.bar_gpt.phase1_v12.panel --output ROOT/full-20ep
```

W&B project: `mehdifaraji/bargpt-phase1-v12-frozen16-mae`. Validation and train metrics use separate prefixes. All raw reconstruction claims must be qualified by masked/all-visible mode and teacher baseline. Low latent loss does not prove that discarded Phase0 information is recovered, nor that a256D window embedding improves forecasting. This first experiment tests the proposed hierarchy; downstream usefulness needs a later matched probe.
