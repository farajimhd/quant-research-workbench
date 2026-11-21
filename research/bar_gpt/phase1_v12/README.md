# Phase1 V12: frozen Phase0 latent-target MAE

User-approved experiment: freeze the user-selected Phase0 V2 epoch19 single-bar encoder, reconstruct its64x16 latent sequence from16 randomly selected bars, train20 epochs over exactly the same certified training population, and validate on exactly the same full validation population. This is temporal reconstruction pretraining, not forecasting.

The user revised the handoff to stop Phase0 and use epoch19. Its retained best-development checkpoint is step840000 during epoch19 (18 fully completed epochs); the exact end-of-epoch19 rolling checkpoint was overwritten in epoch20. Admission verifies the requested checkpoint epoch, exact traversal/count arithmetic, split hash, checkpoint proof and stopped/completed run state. Calibration pins an independent hash-verified copy under the new runtime. This is explicitly the retained within-epoch19 best-development checkpoint, not the end-of-epoch19 model. Teacher weights remainFP32 and frozen. All pretrained artifacts remain intact.

## Model and objective

Each64-bar window becomes64x16 frozen bar embeddings. Fixed means/stds are fitted only on valid bar exposures of the already frozen67606-window training subset. No local references or target-derived inversion metadata are used. Existing time features and27 source-valid flags are supplied only for the16 visible positions. Learned position identities retain the original64-bar slots; hidden bars are physically absent from encoder attention. Each window/batch gets a new uniformly random16-position mask. The same16 positions apply to all dimensions within one window.

Default temporal encoder: width128, two pre-normalized self-attention layers, four heads,2x feedforward expansion, one CLS readout,256-dimensional exported window representation. Temporal encoder usesBF16; latent normalization, exported readout, teacher and decoder stayFP32. The decoder projects256D to64D, adds learned fixed position queries, and uses a shared64-to64-to16 MLP. Its only example-dependent input is the256D representation. No selected-token bypass, true latent reference, raw value or metadata reaches the decoder.

Loss is normalized-latent SmoothL1 on48 hidden positions plus0.1 times visible-position loss. All16 latent dimensions are trained; a target bar is eligible if any original market feature is valid. This is necessary because latent dimensions mix features and have no individual source-feature validity mask. Original market masks remain unchanged for raw diagnostics. No source observations are clipped or sanitized. Full frozen bar exposures, including repeated overlapping windows, remain in the population.

The training schedule is20 epochs with AdamW, weight decay0.01, clipping norm1,1000-update warmup, then consumed-window cosine3e-4 to3e-6. Seed17. Batch/workers are selected by end-to-end profiling. Frozen teacher online inference, raw GPU preparation, bounded CPU prefetch/pinned staging, CUDA backward graphs and fused optimizer are included in profiles. A complete FP32 latent cache alone is~960GB before metadata; the runtime drive had~631GB free at preparation. No alternate storage or lossy cache is silently substituted.

## Evaluation and reproducibility

Full validation uses all47750007 original Phase0 validation windows; training uses all186575722 windows each epoch. The chronological/purged split authority remains `phase1-v6-cohort-balanced-v4/split.json`. Full validation runs after every epoch. The existing11146-window development subset runs every2000 updates and is explicitly labelled. Fixed validation generator seed917 makes masks repeatable for a fixed loader configuration and does not alter training RNG. Source/count/hash admission is mandatory.

Masked and all-visible latent SmoothL1, MSE, MAE, target/prediction variance, training-derived zero-normalized constant and visible-context mean controls are recorded by timeframe. Raw native-unit, price-relative, candle direction and range diagnostics pass predictions through the frozen Phase0 decoder. The same decoder applied to true Phase0 embeddings supplies a labelled teacher reconstruction reference. This uses targets for evaluation only, never decoder inputs or training inverses. Teacher diagnostic aggregates are computed once per immutable validation/development traversal and reused, since teacher weights, population, masks and decoder never change. Student diagnostics are recomputed every evaluation. Raw diagnostics do not enter the latent objective.

Bounded rolling last/best checkpoints preserve model, optimizer, all RNG state, traversal cursor, scheduler progress and scientific contract. Resume uses the same W&B identity and rejects changed science. No automatic scheduled jobs. A detached finite launcher writes exact command, owner creation times and exit receipts. Human STOP files remain authoritative. Phase0 is gracefully stopped under the revised user instruction; exact owner/worker exit is checked before GPU handoff. Its later partial-epoch20 stop checkpoint remains available for resume.

Commands run from a committed, pushed, hash-verified immutable workstation snapshot. Set `PYTHONDONTWRITEBYTECODE=1`; Python launchers also set it. All output paths are under `D:/TradingML/runtimes/bar_gpt/phase1-v12-frozen16-mae-20261121`:

```
python -B -m research.bar_gpt.phase1_v12.run --mode calibrate --output ROOT/teacher --source-revision SHA --teacher PHASE0_RUN/checkpoint_best.pt --teacher-epoch 19
python -B -m research.bar_gpt.phase1_v12.run --mode qualify --output ROOT/profile --source-revision SHA --calibration ROOT/teacher/calibration.json
python -B -m research.bar_gpt.phase1_v12.launch --mode train --output ROOT/full-20ep --source-revision SHA --calibration ROOT/teacher/calibration.json --epochs 20 --batch-size PROFILED_BATCH --workers PROFILED_WORKERS
python -B -m research.bar_gpt.phase1_v12.panel --output ROOT/full-20ep
```

W&B project: `mehdifaraji/bargpt-phase1-v12-frozen16-mae`. Validation and train metrics use separate prefixes. All raw reconstruction claims must be qualified by masked/all-visible mode and teacher baseline. Low latent loss does not prove that discarded Phase0 information is recovered, nor that a256D window embedding improves forecasting. This first experiment tests the proposed hierarchy; downstream usefulness needs a later matched probe.

## Executed qualification and launch (2026-11-21)

Pinned teacher SHA256: `4e61ef1e7ccb5d7b17e906d75dcd717b44c3d0241c5a281b61a203a83d75c7a7`, step840000 during epoch19. Phase0 stopped gracefully at step872610; its later resume checkpoint and logs are preserved under the runtime handoff archive. Seven focused tests passed locally and on the workstation. The real tiny overfit loss fell from2.273903 to0.0000067844 over4000 updates. Training-only normalization round-trip error divided by max(1,abs(target)) was at most1.78814e-7.

Complete pipeline profiles (128 timed updates after warmup) measured143250 windows/s at batch2048/workers8;156360 at4096/16;156192 at8192/16;141662 at8192/32;150193 at16384/16. Selected4096/16, not the largest allocation. Loader startup was28.22seconds for that profile, excluded from throughput. CUDA validation accumulator graphs improved cached-teacher validation from71804 to93489 windows/s on the same524288-window prefix. First-pass validation with teacher diagnostics measured62573 windows/s. These are bounded profiles, not complete population evaluations. Comparing47986 numeric metric values before/after optimization passed rtol1e-6/atol1e-8; maximum absolute difference3.80e-7.

The full20-epoch run is `full-frozen16-mae256-20ep`, W&B [zk1en78s](https://wandb.ai/mehdifaraji/bargpt-phase1-v12-frozen16-mae/runs/zk1en78s). Initial scientific source is `abc1ff06831c67c77b761e5a0d82737f7a891867`; engineering revision `819f5df88f287a2fd7617230086ce4f3047a753a` fixes checkpoint metric scalar serialization. At step1000, a NumPy float64 best-loss value was rejected by the strict checkpoint hasher after the full temporary checkpoint had been written. Recovery changed only that scalar to an identical native float, verified all remaining state hashes unchanged, and resumed optimizer/RNG/traversal under the same W&B identity. The original partial file and failure receipts remain intact. Subsequent rolling checkpoint step7000 was verified. This did not restart the scientific experiment.

At the post-launch check, sustained training was approximately161K windows/s with95% GPU utilization. Development masked loss fell from0.518574 at step0 to0.071046 at step6000, compared with0.081626 for the visible-context mean control. All-visible 1s trade-close MAE fell from$55.13 to$1.634, still far above the frozen teacher reference of$0.010799. These are early development results; no full epoch validation or forecasting benefit is established at this checkpoint.

Runtime receipts: `teacher/calibration.json`, `profile-bf16/profiles.json`, `profile-validation-graphs/evaluation_profiles.json`, `validation-parity.json`, `phase0-handoff.json`, `checkpoint-recovery-1000.json`, `live-readback.json`. Resume from a verified current rolling checkpoint using the immutable snapshot `D:/TradingML/codes/bargpt-phase1-v11-819f5df88` and:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$run='D:/TradingML/runtimes/bar_gpt/phase1-v12-frozen16-mae-20261121'
& 'C:/Users/Mehdi/miniconda3/envs/ml4t/python.exe' -B -u -m research.bar_gpt.phase1_v12.launch --mode train --output "$run/full-frozen16-mae256-20ep" --source-revision 819f5df88f287a2fd7617230086ce4f3047a753a --calibration "$run/teacher/calibration.json" --epochs 20 --batch-size 4096 --workers 16 --validation-every 2000 --checkpoint-every 1000 --resume "$run/full-frozen16-mae256-20ep/checkpoint_last.pt"
```

Run this only after verifying the previous owner/workers exited and respecting any human STOP marker. The launch contract preserves the original scientific revision and records engineering revisions separately.
