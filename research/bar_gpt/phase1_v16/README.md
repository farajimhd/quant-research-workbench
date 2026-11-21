# Phase1 V16: direction retention

**Qualification: direction retention improved, but the checkpoint is rejected for full training because full-population activity reconstruction has extreme finite errors. Exported weights are diagnostic artifacts, not a promoted model.**

Frozen Phase0 16D inputs and the existing36 metadata fields are unchanged. Every encoder exports exactly256 learned values. No raw features, references, directions, summary statistics or deterministic slots are appended to that vector. Every decoder takes only that vector. Target direction appears only in the loss.

The bounded campaign compares a matched continuation control with a log-body reconstruction objective, then tests decoder compression and positional readout if necessary. Data, train-only latent z-score, teacher, stable count-balanced attention and seed are inherited from V15. Every fourth training batch is all-visible; other batches select16 random bars. This common exposure follows the prior measured comparison, not a silent change to the stopped full run.

Arms: `base` is the V15 architecture/objective; `body` adds0.1 times mean SmoothL1 error in log(O/C) measured in log-basis-point units. Source-valid open/close pairs only participate; visible bars receive weight1 and hidden bars0.1. The frozen Phase0 decoder's log-body coordinate avoids subtraction cancellation. `linear_body` removes the secondary256-to64 decoder compression using a single256-to1024 linear map. `slots_body` additionally aligns learned token states by original bar position and projects the flattened states to256. Empty hidden positions are zeros inside the encoder, not decoder inputs. It has2,640,896 parameters versus339,088 baseline and576,512 linear-decoder-only. Body/linear-body use the same CLS encoder; the positional projection is a separately labelled architecture/capacity experiment.

All compatible parent parameters are loaded from V15 seed17; changed heads are freshly initialized and listed in an initialization receipt. All arms reset AdamW at3e-5, batch1024, seed17 and sampling seed559. This is not an optimizer-state resume or a pure decoder-only ablation where initialization differs. Each arm is bounded to30minutes, normally15K updates, with500-step single-training-window overfit diagnostics on a cloned model, periodic validation, complete development raw metrics, W&B and final checkpoints. No full-data run is automatically launched.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -u -m research.bar_gpt.phase1_v16.experiment --checkpoint D:/TradingML/runtimes/bar_gpt/phase1-v15-stable-seed17-20261121/stable_balanced/checkpoint.pt --output D:/TradingML/runtimes/bar_gpt/NEW_DIRECTION_RUN --revision SOURCE_SHA --steps 15000 --arms base,body
```

GPU-cached subset throughput excludes raw loading and Phase0 encoding. Compare moving-candle direction and range agreement alongside all18 native-unit errors, masked versus all-visible, and teacher controls. Hidden random-bar directions may be inherently unpredictable; all-visible and visible-only reconstruction must be distinguished from forecasting. Improvement requires no target inputs or embedding augmentation. Any winner needs broader verification before a full-training recommendation.

## Measured diagnosis

The V15-style control and body-loss-only arm completed15K updates from the same parent, with the same fresh optimizer and batch/mask sequence. All-visible1s moving-candle direction was52.26% versus53.00%, while close MAE worsened$0.4383→$0.7725. A linear decoder alone could fit a single training window much better (body loss1.53 versus82.12 after500 updates), but did not generalize direction with the CLS encoder:49.78% and$1.0282. This rejects a loss-only or decoder-only fix for this configuration.

The positional projection initially suffered a second visible-count shift: at3K updates all-visible1s close MAE was$5,104.57. It was intentionally stopped atstep11,575 with a partial checkpoint and STOP preserved; W&B labels the interrupted run failed. This is a rejected incomplete arm, not a completed evaluation. `scaled_slots_body` uses the usual inverted-dropout factor64/N on position-aligned learned states inside the encoder. Nothing is added to its exported256D vector. At the same3K point all-visible MAE was$2.404. At15K, direction reached82.69% and MAE$1.2215. This is a measured architecture/capacity/readout improvement, not proof that capacity alone or pooling alone caused the original defect.

The frozen linear probe on same-window trade log bodies recovered1s moving direction at78.79% development/77.31% training from this model, versus52.18%/51.58% from the control. All67,606 training and11,146 development windows had the complete trade pairs needed by the probe; no windows were excluded. Probes use training-fitted feature statistics and ridge coefficient0.01, and never modify the encoder. These are reconstruction-information probes, not forecasting results. Their body-coordinate signs avoid log-price subtraction cancellation, so they differ slightly from the native inverse-reconstruction metric.

Input quantization was checked separately on the first256 frozen1s development windows: the Phase0 teacher retained99.58% moving direction in FP32 and99.52% after rounding standardized16D latents to BF16 (3,357 moving pairs). This small diagnostic does not support input quantization as the principal cause; it is not a universal precision guarantee. The earlier V12 Q/K-gradient defect remains separate.

## Scale recovery and qualification

Matched15K-update continuations from the positional checkpoint reset AdamW at1e-5. The body-only continuation reached93.04% direction/$1.0854 MAE. `scaled_slots_price` adds0.01 times three-stream close log-price SmoothL1, in units1000*log-price-error, with the same source-valid and visible/hidden weighting. It reached91.45%/$0.4673. The retained8,258-window audit subset confirmed direction gains: old V15 seed17=50.91%/$0.4544; body continuation=92.19%/$1.5429; price-loss continuation=90.30%/$0.7917. This audit is unseen by V16 training but is not a pristine external test. Price accuracy still regressed against the old model, motivating the eighth and final training arm: `scaled_slots_price_strong`, coefficient0.1 instead of0.01, otherwise matched to the scale continuations.

The complete raw-loader/teacher/model/metrics profile of the price-loss candidate processed1,048,576 windows in20.619s, startup2.821s and pipeline17.798s (~58,916 pipeline windows/s), using batch8192/workers16. It included masked and all-visible reconstructions, all18 raw metrics and teacher controls; zero nonfinite predictions. This is a bounded prefix, not full-validation evidence. Each full qualification has a50-minute deadline and graph/eager parity gates through `validate.py`.

`load_exported_encoder` builds only the encoder. CPU tests verify exact export parity at16 and64 visible bars, hidden-target isolation, positional-order consistency and finite gradients. The positional encoder has2,377,728 parameters and the single-layer decoder263,168; total2,640,896. Inputs and output dimensions remain unchanged. No extra decoder inputs, target-derived references, directions or raw features are appended.

Runtime roots under `D:/TradingML/runtimes/bar_gpt`: `phase1-v16-body-20261121`, `phase1-v16-readout-20261121`, `phase1-v16-scaled-20261121`, `phase1-v16-scale-recovery-20261121`, `phase1-v16-strong-scale-20261121`, `phase1-v16-probes-20261121`, `phase1-v16-audit-20261121`, `phase1-v16-profile-20261121`, and campaign receipts in `phase1-v16-campaign-20261121`. Each training arm records source, parent/data/split hashes, fresh/loaded parameters, tiny-fit check, learning curves, raw audits and checkpoint identity. W&B project: `mehdifaraji/bargpt-phase1-v16-direction`. No full training is automatically restarted.

The eighth arm was rejected: the stronger penalty ended at81.63% direction/$0.5334 MAE, versus91.45%/$0.4673 with the mild penalty. Although71/108 all-visible raw-MAE groups improved and their geometric-mean ratio was0.5034, daily bid-size error worsened26.63x. This is why neither the mean dollar error nor aggregate latent loss alone selects the model. The milder `scaled_slots_price` is the retained direction finalist; it is not an across-the-board reconstruction winner.

Pinned finalist checkpoint: `phase1-v16-scale-recovery-20261121/scaled_slots_price/checkpoint.pt`, SHA256 `17d5f81f59934c4cae082d44ed34f65d4a38ef0f8172c545aa94c389e46f607d`. Decoder-free sibling `encoder.pt`: SHA256 `5d1003f775831bd7b55c4297c2e8db75a1d8f7150dd67c7a0ec14f6612532754`,9,522,173 bytes. Its proof records exact CPU encoder parity at16 and64 visible bars. Use `load_exported_encoder`, with selected16D Phase0 embeddings, existing36 metadata fields and their unique original positions. No decoder is constructed by this loader. `Config.decoder_width` is inherited and unused by the linear-decoder arms; their actual decoder weight is1024x256.

Reproduce the retained training in two bounded stages: source `844755c38c376928a0f93a9982b275c561f0469c` with the pinned V15 parent, `--arms scaled_slots_body --steps 15000 --lr 3e-5`; then source `ce02b8126340da65ef8e512d3ecb922baaac45fa` with that stage's checkpoint, `--arms scaled_slots_price --steps 15000 --lr 1e-5`. Every invocation needs a new runtime output and its actual `--revision`. Parent and data hashes are checked. These are new scientific identities, not resumes of the old full run.

Full-population qualification uses source `9a280883dd1a887eee491574b67bd2df8d05cb42`:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -u -m research.bar_gpt.phase1_v16.validate --checkpoint D:/TradingML/runtimes/bar_gpt/phase1-v16-scale-recovery-20261121/scaled_slots_price/checkpoint.pt --output D:/TradingML/runtimes/bar_gpt/NEW_FULL_VALIDATION --source-revision 9a280883dd1a887eee491574b67bd2df8d05cb42 --variant scaled_slots_price --batch-size 8192 --workers 16
```

## Full validation: direction success, all-feature rejection

All47,750,007 validation windows completed in720.748s (~66,251 windows/s including startup), using the original Phase0 split, fixed teacher and source-valid masks. No difficult values were filtered or clipped. [Full W&B evaluation](https://wandb.ai/mehdifaraji/bargpt-phase1-v16-direction/runs/3ny33stz).

| Full-population metric | V15 corrected baseline | V16 finalist |
|---|---:|---:|
| All-visible1s moving-candle direction |51.3801%|91.1753%|
| Masked-hidden1s moving-candle direction |50.3920%|49.6354%|
| All-visible1s trade-close MAE |$5.50426|$1.11702|
| Masked1s trade-close MAE |$5.52825|$1.15936|
| All-visible1s trade-close MAPE |0.70237%|0.97053%|
| All-visible1s range-bin agreement |13.5833%|14.4406%|
| All-visible latent loss |0.100643|0.116591|
| Masked latent loss |0.101193|0.153661|
| Nonfinite predictions |0|0|

All-visible trade moving-direction agreement improved across all six timeframes:100ms51.82→91.23%,1s51.38→91.18%,10s50.78→91.67%,1m51.10→93.56%,1h52.32→91.50%,daily52.64→96.15%. These reconstruct observed past bars; they are not future-direction accuracies. Hidden random-bar direction remains near chance. A better all-visible representation does not make unseen random directions recoverable.

The full audit exposed enormous **finite** activity errors: daily bid-size all-visible MAE9.28368e21 and daily ask-count masked MAE3.65675e48. The frozen Phase0 teacher's daily bid-size MAE was419.69, so this failure is introduced by the Phase1 reconstruction path. Only60/108 raw groups improved; all-visible geometric-mean error ratio was39.17 and the largest regression1.427e18x. Masked worst-group regression exceeded1e44x. Zero nonfinite predictions is therefore not an adequate acceptance gate. Dollar close MAE improved while relative error and several other metrics worsened. **No full training was restarted or recommended with these weights.**

Direct activity-output error is not part of this experiment's extra body/price objectives; it is penalized implicitly through the latent objective. Off-distribution latent errors amplified by the exponential activity inverse are a plausible failure mechanism, not a completed attribution to particular securities. The next bounded campaign should test explicit activity-log/tail reconstruction constraints and representative training-tail coverage, preserving the successful positional encoder and256D-only decoder contract. Do not conceal the problem by clipping targets or dropping valid examples.

The final same-window frozen probe used source `e1f10f36cc79c8beb0a29ce07191adc0ea8f7c6c`, all67,606 training and11,146 development windows, and training-only ridge fitting. For1s moving trade candles, V15's embedding gave51.74% development/51.25% training; the retained V16 embedding gave79.14%/78.95%. This supports improved information retention in the embedding, not merely a better reconstruction head. [Probe W&B](https://wandb.ai/mehdifaraji/bargpt-phase1-v16-direction/runs/qh6udz66). It does not establish forecasting or trading benefit.

Eight training experiments were attempted: seven completed and one was intentionally stopped/rejected. Original datasets, checkpoints, STOP markers and unrelated changes were preserved. The campaign ledger and full comparison are `phase1-v16-campaign-20261121/ledger.json` and `full-comparison.json`; the selected checkpoint folder contains `qualification.json` marking its rejection. Training, audit, full evaluation, frozen probes and encoder export are reproducible from their recorded snapshots. No TASK_HISTORY.csv changes were made by this campaign.
