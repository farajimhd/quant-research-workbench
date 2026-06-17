# Masked Event Model Versions

This folder contains event-token masked autoencoder experiments for compact
market microstructure chunks. Each version is self-contained under `vN/` and
keeps its own model, loss, masking, progress, training, profiling, and notebook
code. Shared runtime utilities live in `research/mlops`.

## Shared Data Shape

The current versions train on compact event sample-cache records:

```text
header_uint8: [B, 14]
events_uint8: [B, 128, 16]
```

The header is always visible. The event tensor contains 128 compact event
tokens, each represented by 16 packed bytes. The model unpacks bytes into bits
inside the network and predicts the removed event tokens as bit logits:

```text
event_bit_logits: [B, masked_events, 16, 8]
```

## Shared Architecture

The common architecture is a masked autoencoder over event tokens:

```text
header_uint8 + visible event_uint8
  -> bit/token projections
  -> CLS + header + visible event tokens
  -> transformer encoder
  -> chunk embedding bottleneck [B, embedding_dim]
  -> decoder memory [B, 1, d_model]
  -> masked-event query decoder
  -> event bit logits [B, masked_events, 16, 8]
```

The production path uses `encode(...)` and does not run masking or the decoder:

```text
chunk_embedding: [B, embedding_dim]
event_embeddings: [B, 128, embedding_dim]
```

The decoder is intentionally downstream of the exported bottleneck. This keeps
the embedding on the reconstruction loss path, so the encoder can later be
removed and reused by temporal or supervised models.

## Engineering Baseline

v6, v7, v8, and v9 now share the same current training engineering:

- sample-cache training path
- Rich progress panels, including semantic reconstruction metrics
- W&B logging
- async checkpointing
- BF16/FP16 AMP handling with bounded FP16 GradScaler
- FP32 decoder bridge for stability
- TF32 matmul precision default on CUDA
- `torch.compile` enabled by default
- 5 epoch default for train launchers

## Version Differences

| Version | Masking | Chunk Embedding | Training Loss | Purpose |
| --- | --- | --- | --- | --- |
| v6 | Mixed random event mask ratio | Mean pooling over projected encoded tokens | Semantic-weighted BCE mean | Tests random mask-ratio robustness with the mean-pooling encoder. |
| v7 | Mixed random event mask ratio | Learned attention pooling over projected encoded tokens | Semantic-weighted BCE mean | Tests whether learned token importance improves the exported chunk embedding. |
| v8 | Fixed 70% event mask ratio | Mean pooling over projected encoded tokens | Semantic-weighted BCE mean | Fixed-mask ablation of v6 for cleaner throughput/loss comparison. |
| v9 | Fixed 70% event mask ratio | Mean pooling over projected encoded tokens | Ordinary unweighted BCE mean | Loss ablation of v8: same architecture and masking, no semantic bit weights in the objective. |

## Interpretation

Use v8 as the current fixed-mask weighted-loss baseline. Use v9 to determine
whether semantic bit weighting is helping or hurting optimization and downstream
embedding quality. Compare v6 against v8 to isolate the effect of random mask
ratio. Compare v7 against v6 to isolate the effect of learned attention pooling
in the chunk embedding bottleneck.

## Common Commands

Run a version's long 10-shard training:

```powershell
python research\masked_event_model\v8\train_10shard_long.py --fresh-start
```

Swap `v8` for `v6`, `v7`, or `v9` as needed. Each launcher prints the equivalent
low-level trainer command before starting.

Run a one-shard profile:

```powershell
python research\masked_event_model\v8\run_profile_one_shard.py --steps 50 --batch-size 4096 --fresh-start
```

Run a smoke test:

```powershell
python research\masked_event_model\v8\test_smoke.py
```
