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
