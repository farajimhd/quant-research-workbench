# Masked Event Model v17

v17 is the `v12` event-token masked autoencoder with one architecture change:
the exported chunk embedding bottleneck uses max pooling over projected encoder
tokens instead of adaptive average pooling. It is version-local: model, masking,
loss, progress, training, and profiling code live under
`research/masked_event_model/v17`.

The inherited event-token masking setup is:

```text
header_uint8: [B, 14]       always visible
events_uint8: [B, 128, 16]  fixed 70% event tokens removed from the encoder
```

At training time the encoder sees:

```text
[CLS] + header_token + visible_event_tokens
```

Every batch masks exactly 70% of the 128 event tokens, subject only to the
existing `min_masked_events` clamp. Header bytes are not removed; they can
receive low-rate bit corruption. Visible event bytes can also receive low-rate
bit corruption for robustness.

All encoded tokens are projected through the exported chunk embedding bottleneck
before the decoder sees the representation. The chunk embedding is the max pool of
the projected CLS, header, and visible event tokens rather than only the CLS
token:

```text
encoded tokens [B, token_count, d_model]
  -> to_embedding [B, token_count, embedding_dim]
  -> max over token_count [B, embedding_dim]
```

This keeps the production chunk embedding on the reconstruction loss path and
prevents the decoder from bypassing the exported `[B, embedding_dim]`
bottleneck. The decoder is intentionally small and independent per masked event:

```text
chunk_embedding [B, embedding_dim]
  -> LayerNorm + Linear [B, d_model]
masked_event_indices [B, masked_events]
  -> masked position embedding [B, masked_events, d_model]
position embedding + projected chunk memory [B, masked_events, d_model]
  -> MLP
  -> event_bit_logits [B, masked_events, 16, 8]
```

There is no masked-event self-attention, no masked-query cross-attention, and no
target bytes are fed into the decoder. The decoder predicts only the removed
event bytes:

```text
event_bit_logits: [B, masked_events, 16, 8]
```

Loss is ordinary binary cross entropy with logits over masked event bits only.
v17 does not multiply semantic bit weights into the objective. The training loss
uses the PyTorch default mean reduction over all masked event bits. Semantic
metrics can still be emitted for diagnostics, but they are not used for
backpropagation. Production
embedding uses the explicit `encode(...)` path, which sees the full unmasked
header and all 128 events:

```text
chunk_embedding: [B, embedding_dim]
event_embeddings: [B, 128, embedding_dim]
```

## Defaults

```text
data source: sample_cache
sample cache root: D:\market-data\prepared\event_sample_cache
events per chunk: 128
event mask ratio: 0.70
event mask schedule: fixed 70%
header bit corruption: 20% of samples, 5% of header bits
visible event bit corruption: 30% of samples, 20% of visible event bits
AMP dtype: auto, preferring BF16 on supported CUDA devices
FP16 GradScaler cap: 2048 with growth interval 10000
W&B project: June2026-event-token-mae-v17-mlp-decoder
```

## Profiling

One-shard profiling:

```powershell
python research\masked_event_model\v17\run_profile_one_shard.py --steps 50 --batch-size 4096 --fresh-start
```

Model-size sweep:

```powershell
python research\masked_event_model\v17\run_model_size_sweep.py --steps 200 --fresh-start
```

The sweep includes practical combinations across embedding sizes 32 and 64,
batch sizes 1024/2048/4096/8192, and the tiny/small/medium/high model presets.

Focused five-run variation profile:

```powershell
python research\masked_event_model\v17\run_variation_profile.py --fresh-start
```

This runs medium `emb32/bs4096`, medium `emb32/bs8192`, medium `emb64/bs4096`,
medium_plus `emb32/bs2048`, and large `emb32/bs1024` for 200 steps each with a
fixed `2e-4` learning rate and no scheduler. It writes each subprocess log
under the sweep `logs/` folder and writes comparison rows to
`sweep_results.jsonl` and `sweep_results.csv`.

## Limited Real Training

```powershell
python research\masked_event_model\v17\train_medium_bit_limited_shards.py --fresh-start
```

This trains over 10 sample-cache shards and uses a shuffled 5% slice of the next
shard for validation. Each epoch is one pass over the selected train shards.

The final long-run launcher for the per-masked-event MLP decoder path is:

```powershell
python research\masked_event_model\v17\train_10shard_long.py --fresh-start
```

By default this v17 launcher keeps the cheaper MLP decoder inside the active AMP
dtype. To run the explicit FP16 decoder experiment:

```powershell
python research\masked_event_model\v17\train_10shard_long.py --fresh-start --run-name v17-mlpdecoder-fp16decoder-fixedmask070-emb32-bs4096-10shards --amp-dtype fp16
```

If the FP16 decoder run raises non-finite loss/gradient errors, compare against
the conservative decoder path with `--decoder-force-fp32`.

Defaults are medium `d_model=256`, `embedding_dim=32`, `batch_size=4096`, 10
training shards, 4 epochs, one cosine cycle per selected-shard epoch,
validation at each shard boundary, async latest checkpoints every 25 steps, and no shard
interleaving. The launcher prints the equivalent low-level trainer command
before starting and accepts direct overrides for model size, batch size, shard
range, validation shard, W&B run name, and warm-start checkpoint.

## Artifacts

Each run writes a single run directory with:

```text
config.json
run_manifest.json
metrics.jsonl
logs/fatal_error.txt
checkpoints/
artifacts/model/model_details.json
artifacts/model/model_parameters.jsonl
artifacts/model/model_summary.txt
artifacts/model/model_summary_torchinfo.txt              # encoder-only production embedding path
artifacts/model/model_architecture_torchview.png         # encoder-only production embedding path
artifacts/model/model_architecture_torchview.svg         # encoder-only production embedding path
artifacts/model/model_summary_training_torchinfo.txt     # masked reconstruction path, logits-only output
```

If optional graph packages are unavailable, matching `*_error.txt` files are
written instead of silently skipping model artifacts.
