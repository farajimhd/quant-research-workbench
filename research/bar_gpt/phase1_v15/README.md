# Phase1 V15: CLS visibility-count correction and stable attention

Two distinct defects were investigated. V12 developed excessive Q/K magnitudes and inaccurate BF16 attention gradients. Separately, a unique CLS token competed with16 bar tokens in training but64 in evaluation. Replicating the same16 tokens four times, with identical values, metadata and original positions, changed predictions substantially despite adding no information. This isolates a multiplicity effect rather than a normalization/data-distribution explanation.

For N visible bar tokens, `CountAttention` adds `log(N/16)` to the CLS-key attention score in every layer. At training's N=16 this is zero and the original forward/gradients are exactly unchanged. Under uniform k-fold replication of bars, the CLS unnormalized weight is also multiplied by k, preserving each layer's weighted average in exact arithmetic. The correction uses only the visible token count, never hidden values or target-derived statistics. Genuine new bars may still change the embedding; the invariant concerns duplication of existing tokens.

The `stable_balanced` variant also normalizes each Q/K head to fixed norm sqrt(head dimension) and computes QKV projection, normalization, SDPA and attention output projection in FP32. This bounds ideal raw attention scores and addresses the separate large-score numerical problem. Feedforward/stem autocast, model size, z-score target calibration, masked objective, decoder and exported256D representation remain unchanged. The decoder has no example-dependent input besides the exported embedding. The original frozen Phase0 teacher remains fixed.

## Causal evaluation, without retraining

On all11146 frozen development windows, applying only count correction to existing checkpoints produced:

| Checkpoint | All-visible1s close MAE before | After | Masked change |
|---|---:|---:|---:|
| V14 z-score seed17 |$78.9861|$0.2719|exactly none|
| V14 z-score seed29 |$75.5545|$0.3115|exactly none|
| V13 fresh stable |$54.9759|$0.8135|exactly none|

For512 development examples, duplicate-token mean absolute prediction changes fell0.3487→0.00235 and0.2416→0.00227 on the two BF16 originals, and0.2237→0.0000350 on the stable model. Remaining finite-precision differences are measured, not claimed zero. CPU FP32 tests verify duplication invariance at numerical tolerance, exact16-token original forward/gradient parity, hidden-target isolation, and standalone encoder export without a decoder.

Diagnosis source: `fc6168b1cc435b7284de224a046e9d8390bf96f6`; runtime `D:/TradingML/runtimes/bar_gpt/phase1-v15-count-diagnosis-20261121/comparison.json`. Original checkpoints and STOP markers are preserved.

## Qualification and reproducibility

`experiment.py` runs fresh stable-balanced50K-update qualification at LR3e-5, batch1024,16 fresh random bars, unchanged67606 training and11146 development windows, with all18-channel/six-timeframe final audits and W&B logging. Two initialization seeds are compared; data/mask seed555 is fixed. Training uses GPU-cached FP32 teacher embeddings, so throughput excludes raw loading and teacher inference. Each arm has a45-minute training deadline and saves partial state on failure. This is a bounded experiment, not a full-data restart.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -u -m research.bar_gpt.phase1_v15.experiment --output D:/TradingML/runtimes/bar_gpt/phase1-v15-stable-seed17-20261121 --revision SOURCE_SHA --seed 17 --steps 50000
```

`validate.py` verifies a pinned checkpoint against its result SHA and evaluates the original47750007-window validation population. Use `--profile-batches 128` first to measure complete-pipeline cost; omit it only for a full pass. Each invocation has a new output identity, online W&B logging, graph/eager parity checks inherited from V12, process ownership receipt and at most50minutes before graceful STOP. Partial validation is never complete evidence. Source/split/calibration/teacher hashes are recorded.

```powershell
python -B -u -m research.bar_gpt.phase1_v15.validate --checkpoint CANDIDATE/checkpoint.pt --output NEW_RUNTIME --source-revision SOURCE_SHA --variant stable_balanced --batch-size 4096 --workers 16 --profile-batches 128
```

`load_exported_encoder(path, device)` constructs only the encoder from an artifact containing `config`, `variant` and `encoder` state. The variant is mandatory for reproducing count balancing and precision; do not load these weights into an unmodified V12 encoder. Inputs are selected Phase0 latents, corresponding36 metadata fields, and original bar positions; output is256D. Reconstruction still requires the separately retained lightweight decoder and frozen Phase0 decoder.

W&B project: `mehdifaraji/bargpt-phase1-v15-visibility-fix`. Fixing numerical/visibility defects does not establish forecasting usefulness, optimal capacity or sufficient raw reconstruction quality. Those remain measured research questions.

## Fresh training and throughput

Both seeds completed 50,000 updates using source `7458e859322f00765c14138f32f7aa71c068adc4` (the seed17 manifest abbreviates it to `7458e8593`). Their final development results were:

| Seed | Masked latent loss | All-visible latent loss | Masked 1s close MAE | All-visible 1s close MAE | Cached training windows/s |
|---|---:|---:|---:|---:|---:|
| 17 |0.0677122|0.0680596|$0.242926|$0.232652|129,523|
| 29 |0.0677532|0.0680775|$0.265436|$0.269592|130,005|

Seed17 was retained: seed29/seed17 raw-MAE geometric mean ratios across 108 timeframe/channel groups were 1.0894 masked and 1.1196 all-visible. Seed29 was better in 47/108 masked and 37/108 all-visible groups; the worst regression was about 1.86x. This is evidence of seed sensitivity, not universal dominance. Seed17 training-subset latent losses were 0.0202032 masked and 0.0199872 all-visible, so a development gap remains. Its all-visible development price error fell from $55.09 initially to $0.337 at 10K and $0.233 at 50K, but fluctuated during training; monotonic raw improvement is not claimed.

W&B training receipts: [seed17](https://wandb.ai/mehdifaraji/bargpt-phase1-v15-visibility-fix/runs/4k723w34), [seed29](https://wandb.ai/mehdifaraji/bargpt-phase1-v15-visibility-fix/runs/37sl6sho). Both remotely read back as finished at step50,000.

Complete raw-loader/teacher/model/metrics profiles at source `77a027b38c198051b4a98af38412e23411067762` used 524,288 windows each: batch4096/workers16 yielded 50,924 pipeline windows/s (startup3.69s, total13.99s); batch8192/workers16 yielded 53,825 (startup3.02s, total12.76s). Batch8192 was selected. These include both masked and all-visible inference plus teacher controls and raw metrics; cached training speed must not be confused with this full validation pipeline. Different batch sizes can change traversal prefixes and masks, so these profiles are timing evidence, not a quality ablation.

Selected checkpoint: `D:/TradingML/runtimes/bar_gpt/phase1-v15-stable-seed17-20261121/stable_balanced/checkpoint.pt`, SHA256 `2506664581d4e1368256ed2e4283eab9d50eb991c57aec636b5cc2c43c2ee9a5`. Decoder-free sibling `encoder.pt`: SHA256 `e271dcd60c46237767c520a8344556315c3852b304d6b4e8a2cf489db4abeb4b`, 1,265,725 bytes. Model total339,088 parameters, encoder313,344, decoder25,744. Export proof is retained as `encoder.proof.json`; use the V15 loader so non-tensor attention semantics are reconstructed correctly.

## Full-population qualification: important limits

The corrected checkpoint completed all 47,750,007 original validation windows in 713.37 seconds (66,935 windows/s including startup), with zero nonfinite predictions. This is broader evidence than the development subset. No observations were filtered for price or error. The original chronological split and source-valid masks were retained.

| Timeframe | All-visible trade-close raw MAE |
|---|---:|
| 100ms |$6.39680|
| 1s |$5.50426|
| 10s |$3.58792|
| 1m |$1.91799|
| 1h |$1.63296|
| daily |$223.40601|

Full masked/all-visible latent losses were 0.1011925/0.1006433. The masked training-constant control was 0.5525701; the visible-context latent-mean control was 0.1223917. For 1s trade close, MAPE was 0.70237%, while the frozen Phase0 teacher's raw MAE was $0.030142. Moving-candle direction agreement was only 51.3801% after Phase1 versus 96.7975% through Phase0 alone. These are same-window reconstruction diagnostics, not forecasts. Strict overall three-way direction agreement also penalizes predicted nonzero movement on truly flat candles; do not confuse it with the moving-only statistic.

Hourly latent predicted variance was 0.971995 versus target6.412774. The corrected model remains a lossy temporal summary; fixing the attention defects does not establish adequate candle-detail retention. The small development set has only856 hourly and213 daily windows and a different price distribution, so its $0.233 result must not be presented as full-population accuracy. The contribution of specific extreme securities to dollar MAE has not been attributed by this aggregate evaluation.

Full receipt: `phase1-v15-full-fixed-20261121/result.json`; complete reducers and all18-channel metrics: `validation-latest.json`; [W&B](https://wandb.ai/mehdifaraji/bargpt-phase1-v15-visibility-fix/runs/o65rf8dl). The original long training remains stopped. This checkpoint is retained for diagnosis and comparison, not promoted as ready for forecasting.

`refine.py` tests an explicitly different objective exposure after these findings: paired5,000-update continuations from the same selected weights and AdamW state, one with16 visible bars throughout and one with64 visible bars every fourth batch. All-visible batches use the existing full-visible reconstruction objective; hidden-target masking remains unchanged on the other batches. Teacher, target/input normalization, architecture, embedding, decoder, LR and sample/mask RNG remain matched. Separate run identities and checkpoints prevent silently resuming a changed experiment.

## Full-population causal control

The same selected checkpoint, source77a027b38, batch8192, workers16, split and deterministic masks was evaluated again with only CLS count correction disabled (`--variant stable`). It also completed47,750,007 windows, in707.29s. All-visible latent loss was0.1142731 versus0.1006433 corrected; 1s close MAE was$28.65936 versus$5.50426 (80.8% reduction). Correction improved105/108 raw-MAE groups; geometric-mean fixed/control ratio0.322361. Regressions were daily trade volume17.227%, hourly trade volume0.352%, and hourly trade count0.0055%. Therefore the correction is strongly supported overall but not a universal group improvement.

Masked latent/raw metrics were exactly unchanged. Of970 masked scalar metrics,156 movement-reduction means differed only at floating-point summation scale: maximum absolute difference1.47e-14, maximum relative difference1.27e-13. The initial strict comparison correctly flagged this. `compare.py` now retains all these differences in its receipt and allows1e-12 relative/absolute tolerance only for movement reductions; all other masked metrics remain exact gates. No model/data changes or metric recomputation were used to erase differences.

Both W&B runs were independently read back as finished with matching final metrics: [fixed](https://wandb.ai/mehdifaraji/bargpt-phase1-v15-visibility-fix/runs/o65rf8dl), [control](https://wandb.ai/mehdifaraji/bargpt-phase1-v15-visibility-fix/runs/d6ys4l4j). Runtime `phase1-v15-monitor-20261121/full-comparison-qualified.json` contains every raw group and the numerical differences; the original strict-failure receipt is preserved separately. Original validation workers exited before the next experiment. The read-only Rich panel showed both passes and exited when their active receipt was marked finished; normal/compact, redirected, stale/failure/complete and local interruption behavior were exercised.

Reproduce the complete validation from the pinned77a027b38 snapshot, using a new output directory:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -u -m research.bar_gpt.phase1_v15.validate --checkpoint D:/TradingML/runtimes/bar_gpt/phase1-v15-stable-seed17-20261121/stable_balanced/checkpoint.pt --output D:/TradingML/runtimes/bar_gpt/NEW_VALIDATION --source-revision 77a027b38c198051b4a98af38412e23411067762 --variant stable_balanced --batch-size 8192 --workers 16
```

Use a second new output with `--variant stable` for the paired control, then `python -B -m research.bar_gpt.phase1_v15.compare --fixed FIXED_OUTPUT --control CONTROL_OUTPUT --output NEW_REPORT.json` with the current committed comparison utility.

## Paired exposure result and disposition

Both5,000-update continuations completed from the identical parent weights/AdamW state using source `b6231d11ea8956683ab4386e3c6de53e14a80d69`. Runtime `phase1-v15-exposure-20261121` retains both checkpoints, curves, all18-channel audits and `exposure-analysis.json`. Training/evaluation/checkpoint time was42.69s masked-only and46.83s mixed, excluding shared teacher caching and W&B setup. These are cached-subset measurements.

| Development metric | Masked-only continuation | 75% masked /25% all-visible |
|---|---:|---:|
| Masked latent loss |0.0678114|0.0677296|
| All-visible latent loss |0.0680978|0.0680419|
| Masked1s close MAE |$0.199352|$0.184064|
| All-visible1s close MAE |$0.193077|$0.175674|
| All-visible1s moving candle direction |52.7958%|53.2586%|
| All-visible1s range-bin agreement |10.9364%|10.9417%|

Mixed visibility improved87/108 masked and88/108 all-visible raw-MAE groups. Geometric-mean error ratios were0.918804 and0.915734. Worst regressions were10s bid-size errors,6.34% masked and6.27% all-visible. It is a modest reconstruction improvement on this subset; it does not solve directional/detail retention, and it has not received a full-population pass or a second-seed replication. Retain it as an experimental alternative, not the fully qualified recommendation. [Masked control](https://wandb.ai/mehdifaraji/bargpt-phase1-v15-visibility-fix/runs/o6ebaill), [mixed](https://wandb.ai/mehdifaraji/bargpt-phase1-v15-visibility-fix/runs/f0ghf5ps).

The supported engineering fix is stable FP32 normalized-QK attention plus CLS visibility-count correction, keeping train-only z-score inputs. The best full-population-tested artifact remains the seed17 checkpoint pinned above. Do not resume the old numerically unstable architecture or claim the current representation is ready for forecasting: full-data fine candle reconstruction is still weak. No full training was restarted. Subsequent research should test objectives/capacity that preserve within-window differences, with the same full-data and directional gates, rather than selecting by aggregate latent loss alone.

## Provenance and final verification

- Corrected full authority: `D:/TradingML/runtimes/bar_gpt/phase1-v6-cohort-balanced-v4/split.json`; split hash `d60f707c91c54b72ec7a11a9119334cc973351be699c8b6d5f3d724957640d63`.
- Frozen subset manifest hash `434661f361dbbc228f1bfd287455cb1ab71029c9c9e7d669788cfdbc959c2951`; train67606/development11146. Uniform cached-row sampling; calibration and metrics are unweighted. No new audit-subset lookups were made.
- Calibration: `phase1-v12-frozen16-mae-20261121/teacher/calibration.json`, SHA256 `2410c223feef8d7dffdd704df550d0c0c5a64e28fe6731721c9e23e1d43c75ee`. Fixed training-only16D mean/std; no clipping or target-window reference.
- Teacher SHA256 `4e61ef1e7ccb5d7b17e906d75dcd717b44c3d0241c5a281b61a203a83d75c7a7`. This is the retained best checkpoint during epoch19, step840000, not the end-of-epoch19 state.
- Both fresh training arms, both full-validation passes, and both refinement arms were remotely read back as finished in W&B with matching final metrics. The full validation counted47,750,007 windows per pass.
- Three focused local model tests passed, plus real tiny-data/parent-optimizer CPU updates at16 and64 visible bars and the real full-receipt comparison. GPU evaluation additionally enforced eager/graph parity before steady-state execution.
- Experiment processes, validation workers and the campaign panel exited. Prior full-run checkpoint and STOP remain preserved. No new scheduled automation was created; the existing manual panel entry was reused. No subagents were spawned.
