# Phase0 V2 feature-attention ablation

Only the encoder changes relative to Phase0 V1. The original54 inputs, fixed training-only calibration,18 output coordinates,16-dimensional exported embedding,306-parameter linear decoder, prediction constraints, losses, source-valid masks, full data population and20-epoch1e-4→1e-6 cosine schedule remain unchanged. The seeded decoder initialization is bit-identical to the original MLP. No MLP checkpoint weights are transferred; this is a new scientific run in the same `bargpt-phase0-singlebar-v1` W&B project.

Each bar supplies18 feature tokens. Each token has its normalized coordinate times a learned feature-specific value projection, a learned feature identity, and a projection of the same remaining36 time/validity inputs. Standard self-attention operates only among these18 tokens, never across bars or examples. Residual attention and feedforward blocks are followed by mean pooling and a learned16D readout. The decoder receives only16D. All computation staysFP32, including SDPA; no BF16/TF32 precision change is hidden in this architecture comparison. Attention chunks bound workspace/CUDA grids without changing outputs or discarding bars.

Initial candidate: width32, two layers, four heads. Smaller width16/one-layer/two-head configurations may be profiled if needed to approach MLP throughput. Architecture choices and batch/worker settings must be retained in each run's config and runtime profiles. Use `--width`, `--layers`, `--heads`, `--chunk-bars`; existing qualification/production flags remain the same.

Measured first candidate: only13–14K windows/s versus the prior MLP's244K windows/s. Loader waiting was negligible. The width16/one-head/one-layer candidate passed the unchanged tiny convergence gate after40000 bounded updates (loss2174.53→0.0263); shorter4000/12000-step tests failed and remain recorded. Qualification steps are distinct from the unchanged full-training schedule. A further encoder-only option `--pooling flatten --width 4 --layers 1 --heads 1` retains ordered feature-token identity in a72→16 readout, avoiding the rank restriction of mean-pooling four-channel tokens. It still uses full feature self-attention and supplies only16D to the same decoder. No precision changes.

```
python -B -m research.bar_gpt.phase0_v2.run --mode qualify --output NEW_RUNTIME --source-revision SHA --calibration D:/TradingML/runtimes/bar_gpt/phase0-v1-singlebar-20261120/calibration.json --profile-configs 256:8,512:16 --profile-batches 128
python -B -m research.bar_gpt.phase0_v2.launch --mode train --output NEW_RUNTIME --source-revision SHA --calibration D:/TradingML/runtimes/bar_gpt/phase0-v1-singlebar-20261120/calibration.json --epochs 20 --batch-size WINDOWS --workers WORKERS
```

Reuse V1's checked engine, loss, metrics and terminal panel. Preserve V1's human STOP and checkpoint archive `attention-pause-364472`; its epoch8 full validation was interrupted, so only epochs1–7 are completed full evaluations. Runtime files belong under `D:/TradingML/runtimes/bar_gpt`, never source. Commit/push and hash-verify immutable snapshots before workstation execution.

## Executed attention experiment, 2026-11-20

Selected width4, one layer, one head, flattened72-to16 readout:1938 parameters (encoder1632, unchanged decoder306). This is the fastest qualified attention variant, not a proven quality winner. Five local tests passed, including seeded decoder parity, bar independence, finite attention gradients and chunk parity. Production eager/captured loss and gradient parity passed. Tiny qualification required40000 updates; the12000-step failure remains recorded. Full-training schedule is unchanged.

Production pipeline profiles: width16/one-layer/mean64761 windows/s; width4/flatten74912 at1024 windows/8 workers,78736 at2048/8,80339 at4096/16. Selected4096/16, chunk32768 bars, bounded prefetch4, CUDA graphs, FP32. This remains about3x slower than MLP244023 windows/s despite far fewer parameters. Feature-token operations dominate, rather than loader waiting. No reduced precision or new dependencies were introduced.

New run `D:/TradingML/runtimes/bar_gpt/phase0-v2-feature-attention-20261120/full-attention4x1-emb16-20ep`, W&B https://wandb.ai/mehdifaraji/bargpt-phase0-singlebar-v1/runs/qozcaps8 . Pinned training source29c8c3468415b3d275f9af76f03e1665462a626e. Its intentional2000-update prefix reached development loss36.2157 from670.071 and1s trade-close MAE$3.4368 from$56.0699. Original MLP at matched2000 updates had loss26.74 andMAE$2.39. Attention moving-candle direction49.72% is not useful yet. These are fixed development-subset reconstruction results, not full-population validation or forecasting evidence. Final model capacity/generalization remain unproven.

Prefix checkpoint retained under `resume-check-2000`, SHA256f85ea095babc0481d02a2a2feee54a8960f47bb85aa85948185a47e401f79b95. The same W&B identity and full optimizer/RNG/traversal/scheduler checkpoint are used for continuation. Exact ownership/launch/exit receipts, all profiles and `comparison.json`/`comparison.md` are under the campaign runtime root. Full training uses20 epochs of186575722 windows, all64 bars independently; full47750007-window validation each epoch, fixed development every2000 updates, checkpoint every1000.

Reproduce the continuation from the immutable snapshot (launcher sets `PYTHONDONTWRITEBYTECODE=1`):

```
python -B -m research.bar_gpt.phase0_v2.launch --mode train --output D:/TradingML/runtimes/bar_gpt/phase0-v2-feature-attention-20261120/full-attention4x1-emb16-20ep --source-revision 29c8c3468415b3d275f9af76f03e1665462a626e --calibration D:/TradingML/runtimes/bar_gpt/phase0-v1-singlebar-20261120/calibration.json --width 4 --layers 1 --heads 1 --pooling flatten --chunk-bars 32768 --epochs 20 --batch-size 4096 --workers 16 --validation-every 2000 --checkpoint-every 1000 --resume D:/TradingML/runtimes/bar_gpt/phase0-v2-feature-attention-20261120/full-attention4x1-emb16-20ep/checkpoint_last.pt
```
