# Lightweight transformer full-training configuration

User-authorized replacement for paused Conv64: four normalized-attention blocks,
width 128, four heads of dimension 32, four learned pooling tokens, 512 exported
values (480 shape plus 32 learned scale values), unchanged affine decoder. Sixteen
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

Runtime root: `D:/TradingML/runtimes/bar_gpt/phase1-v11-light512-20261118`.
W&B project: `mehdifaraji/bargpt-phase1-v11-mlp512`, new run identities for lightweight
qualification and full training. Conv64 STOP/checkpoints remain preserved. Do not
modify task history. Immutable snapshot source and runnable launch arguments are
recorded in deployment and launcher receipts. Full-run progress panel includes
training and validation speeds and ETA.
