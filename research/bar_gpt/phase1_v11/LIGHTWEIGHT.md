# Lightweight transformer full-training configuration

User-authorized replacement for paused Conv64: four normalized-attention blocks,
width 128, four heads of dimension 32, four learned pooling tokens, 256 exported
values (224 shape plus 32 learned scale values), same affine decoder design. Sixteen
random visible bars plus one visible-only reference token and four pooling tokens
enter the encoder. No input or reference bypasses the embedding into the decoder.
Median-centered transformation, source-valid losses and FP32 output remain unchanged.

Use the same frozen phase-one subset for tiny qualification and initial training,
then all certified full training/validation windows for 100 epochs. Qualify with
seed 110731, batch 1024, 19,984 updates, cosine 3e-4 to 1e-5. Full continuation
retains the trained optimizer and uses cosine 1e-4 to 1e-5, seed 17. Batch, workers
and bounded prefetch budgets must be selected from full-pipeline measurements.
The profiler now accepts an explicit seed; use 17 for this campaign, avoiding the
previous discrepancy between profile seed 110731 and full-training seed 17.

Existing BF16 encoder, FP32 readout, PyTorch SDPA attention, fused AdamW, CUDA
training/inference replay, pinned CPU prefetch and asynchronous device copies are
retained. Test 16/32 workers and larger GPU batches; report measured loader wait,
startup and sustained throughput rather than claiming zero waits. Shard prefetch
is bounded per worker; increasing it uses host memory, not GPU memory. Preserve
ordered traversal, all tails, chronological split and exact resume state.

Runtime root: `D:/TradingML/runtimes/bar_gpt/phase1-v11-light256-20261118`.
W&B project: `mehdifaraji/bargpt-phase1-v11-mlp512`, new run identities for lightweight
qualification and full training. Conv64 STOP/checkpoints remain preserved. Do not
modify task history. Immutable snapshot source and runnable launch arguments are
recorded in deployment and launcher receipts. Full-run progress panel includes
training and validation speeds and ETA.

The user changed the embedding from 512 to 256 during profiling. Preserve the
completed `t01-w128-l4-e512` qualification under `phase1-v11-light512-20261118`;
its remaining profiling was explicitly cancelled, not completed. Its 4096/16-worker
profile completed at 172,091 windows/s with 0.44s loader wait in 71.4s. The 8192
profile was stopped and must not be ranked as a completed measurement.

Qualification uses immutable source `15e0e0b2d949f7cd40c9c6494ec2c652d86cf7d9`:

| Embedding | Encoder parameters | Decoder parameters | Masked price | All-visible price | Masked activity log MAE | All-visible activity log MAE |
|---|---:|---:|---:|---:|---:|---:|
| 512 | 1,058,496 | 1,773,225 | 1.0253% | 1.1451% | 0.75916 | 0.64426 |
| 256 | 927,168 | 888,489 | 1.0629% | 1.1197% | 0.76137 | 0.66972 |

Both completed 19,984 updates and complete frozen-development evaluation. T02
checkpoint SHA256 is `d127ff43c78e96b030f1bdcc0b0fae6e6b845c5e7ae11558a11e4dfa6a23d726`.
W&B qualification IDs are `eeh64nmg` (512) and `bss9ducf` (256). No audit subset
was consulted. Full training proceeds with 256 as explicitly requested, not as
a claim that it dominates the 512-dimensional representation on every metric.
