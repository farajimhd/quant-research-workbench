# Masked Event Model v20

v20 is the event-token masked autoencoder variant that keeps the v12 fixed-mask
training path and cheap MLP decoder, but replaces v12's mean-only chunk
bottleneck with a fixed-grid chunk bottleneck. It is
version-local: model, masking, loss, progress, training, and profiling code live
under `research/masked_event_model/v20`.

The main change is masking at the event-token level:

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

All encoded tokens pass through the exported chunk embedding bottleneck before
the decoder sees the representation. v20 scatters the variable visible-token
encoder output back into a fixed semantic grid:

```text
encoded tokens [B, token_count, d_model]
  -> fixed grid [B, 130, d_model]
       slot 0: CLS
       slot 1: header
       slot 2..129: event positions 0..127
       masked event slots: zero vectors
       visible event slots: encoded event tokens
  -> flatten [B, 130 * d_model]
  -> MLP [B, embedding_dim]
```

This keeps the production chunk embedding on the reconstruction loss path while
giving the bottleneck fixed slot semantics in both training and production. The
event-position embedding is used only before the transformer for visible event
tokens. It is not used to initialize masked fixed-grid slots, so the bottleneck
does not receive learned placeholder content for hidden events. The transformer
still processes only visible events during MAE training. The decoder matches
the v12 low-cost reconstruction path: it receives the exported chunk embedding
plus a decoder-only masked event position embedding. That position embedding is
not used inside the fixed-grid bottleneck.

```text
chunk_embedding [B, embedding_dim]
  -> Linear + GELU + LayerNorm [B, d_model]
masked_event_indices [B, masked_events]
  -> masked_event_position_embedding [B, masked_events, d_model]
chunk_context + masked_event_position_embedding
  -> per-masked-event MLP
  -> event_bit_logits [B, masked_events, 16, 8]
```

There is no masked-event self-attention, no masked-query cross-attention, and no
target bytes are fed into the decoder. The decoder predicts only the removed
event bytes:

```text
event_bit_logits: [B, masked_events, 16, 8]
```

Loss is ordinary binary cross entropy with logits over masked event bits only.
v20 does not multiply semantic bit weights into the objective. The training loss
uses the PyTorch default mean reduction over all masked event bits. Semantic
metrics can still be emitted for diagnostics, but they are not used for
backpropagation. Production
embedding uses the explicit `encode(...)` path, which sees the full unmasked
header and all 128 events:

```text
chunk_embedding: [B, embedding_dim]
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
randomness: non-repeatable by default; pass --repeatable-randomness to replay the same seed stream
AMP dtype: bf16
FP16 GradScaler cap: 2048 with growth interval 10000
W&B project: June2026-event-token-mae-v20-mlp-decoder
```

By default v20 generates a fresh effective seed at process start, then records
that effective seed in the run config/checkpoints. This prevents continuation
runs from replaying the same shard order, record shuffle, event mask indices,
and bit-corruption stream. Validation masks are sampled inside an isolated RNG
context, so validation cannot reset or advance the training RNG stream. Use
`--repeatable-randomness` only for a controlled replay/debug run.

## Profiling

One-shard profiling:

```powershell
python research\masked_event_model\v20\run_profile_one_shard.py --steps 50 --batch-size 4096 --fresh-start
```

Model-size sweep:

```powershell
python research\masked_event_model\v20\run_model_size_sweep.py --steps 200 --fresh-start
```

The sweep includes practical combinations across embedding sizes 32 and 64,
batch sizes 1024/2048/4096/8192, and the tiny/small/medium/high model presets.

Focused five-run variation profile:

```powershell
python research\masked_event_model\v20\run_variation_profile.py --fresh-start
```

This runs medium `emb32/bs4096`, medium `emb32/bs8192`, medium `emb64/bs4096`,
medium_plus `emb32/bs2048`, and large `emb32/bs1024` for 200 steps each with a
fixed `2e-4` learning rate and no scheduler. It writes each subprocess log
under the sweep `logs/` folder and writes comparison rows to
`sweep_results.jsonl` and `sweep_results.csv`.

## Limited Real Training

```powershell
python research\masked_event_model\v20\train_medium_bit_limited_shards.py --fresh-start
```

This trains over 10 sample-cache shards and uses a shuffled 5% slice of the next
shard for validation. Each epoch is one pass over the selected train shards.

The final long-run launcher for the fixed-grid MLP decoder path is:

```powershell
python research\masked_event_model\v20\train_10shard_long.py --fresh-start
```

By default this v20 launcher uses BF16 AMP and keeps the cheaper MLP decoder inside the active AMP
dtype. To run the explicit FP16 decoder experiment:

```powershell
python research\masked_event_model\v20\train_10shard_long.py --fresh-start --run-name v20-fixedgrid-fp16decoder-fixedmask070-emb32-bs8192-10shards --amp-dtype fp16
```

If the FP16 decoder run raises non-finite loss/gradient errors, compare against
the conservative decoder path with `--decoder-force-fp32`.

Defaults are medium `d_model=256`, `embedding_dim=32`, `batch_size=8192`, 10
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
