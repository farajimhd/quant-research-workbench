# Masked Event Model v25

v25 is the event-token masked autoencoder variant copied from v21 and extended
with a header reconstruction objective. It keeps v20's fixed-mask training path
and the cheap per-masked-event MLP decoder, keeps v21's semantic branch
fixed-grid bottleneck, and adds a second decoder head:

```text
chunk_embedding [B, embedding_dim] -> header_bit_logits [B, 14, 8]
```

The header decoder receives only the exported chunk embedding, not the raw
header token. This forces the reusable embedding to carry chunk-level anchor,
count, flag, and timing information that was previously only visible to the
encoder. v25 is version-local: model, masking, loss, progress, training, and
profiling code live under `research/masked_event_model/v25`.

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
the decoder sees the representation. v25 scatters the variable visible-token
encoder output back into a fixed semantic grid:

```text
encoded tokens [B, token_count, d_model]
  -> fixed grid [B, 130, d_model]
       slot 0: CLS
       slot 1: header
       slot 2..129: event positions 0..127
       masked event slots: zero vectors
       visible event slots: encoded event tokens
  -> grouped nonlinear bottleneck [B, embedding_dim]
```

This keeps the production chunk embedding on the reconstruction loss path while
giving the bottleneck fixed slot semantics in both training and production. The
event-position embedding is used only before the transformer for visible event
tokens. It is not used to initialize masked fixed-grid slots, so the bottleneck
does not receive learned placeholder content for hidden events. The transformer
still processes only visible events during MAE training.

The v25 bottleneck tests whether preserving coarse semantic groups helps the
exported representation. The fixed grid is split into 10 nonlinear branches:

```text
slot 0 CLS token             -> MLP -> [B, embedding_dim]
slot 1 header token          -> MLP -> [B, embedding_dim]
events 0..15                 -> flatten -> MLP -> [B, embedding_dim]
events 16..31                -> flatten -> MLP -> [B, embedding_dim]
events 32..47                -> flatten -> MLP -> [B, embedding_dim]
events 48..63                -> flatten -> MLP -> [B, embedding_dim]
events 64..79                -> flatten -> MLP -> [B, embedding_dim]
events 80..95                -> flatten -> MLP -> [B, embedding_dim]
events 96..111               -> flatten -> MLP -> [B, embedding_dim]
events 112..127              -> flatten -> MLP -> [B, embedding_dim]
10 branch embeddings concat  -> nonlinear merge -> [B, embedding_dim]
```

The final merge keeps the exported embedding shape identical to v20, so the
existing decoder, training scripts, and temporal linear probes remain
compatible.

The event decoder matches the v12 low-cost reconstruction path: it receives the
exported chunk embedding plus a decoder-only masked event position embedding.
That position embedding is not used inside the fixed-grid bottleneck.

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
target bytes are fed into the decoder. The event decoder predicts the removed
event bytes:

```text
event_bit_logits: [B, masked_events, 16, 8]
```

The header decoder predicts all compact header bytes, including time-related
header fields:

```text
header_bit_logits: [B, 14, 8]
```

Loss is ordinary binary cross entropy with logits over masked event bits plus a
weighted BCE term over all header bits:

```text
loss = event_bce + header_loss_weight * header_bce
default header_loss_weight = 0.25
```

v25 does not multiply semantic bit weights into the objective. The event loss
uses the PyTorch default mean reduction over all masked event bits, and the
header loss uses the same mean reduction over all 14 header bytes. Semantic
metrics can still be emitted for diagnostics, but they are not used for
backpropagation. Production embedding uses the explicit `encode(...)` path,
which sees the full unmasked header and all 128 events:

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
W&B project: June2026-event-token-mae-v25-mlp-decoder
```

By default v25 generates a fresh effective seed at process start, then records
that effective seed in the run config/checkpoints. This prevents continuation
runs from replaying the same shard order, record shuffle, event mask indices,
and bit-corruption stream. Validation masks are sampled inside an isolated RNG
context, so validation cannot reset or advance the training RNG stream. Use
`--repeatable-randomness` only for a controlled replay/debug run.

## Profiling

One-shard profiling:

```powershell
python research\masked_event_model\\v25\run_profile_one_shard.py --steps 50 --batch-size 4096 --fresh-start
```

Model-size sweep:

```powershell
python research\masked_event_model\\v25\run_model_size_sweep.py --steps 200 --fresh-start
```

The sweep includes practical combinations across embedding sizes 32 and 64,
batch sizes 1024/2048/4096/8192, and the tiny/small/medium/high model presets.

Focused five-run variation profile:

```powershell
python research\masked_event_model\\v25\run_variation_profile.py --fresh-start
```

This runs medium `emb32/bs4096`, medium `emb32/bs8192`, medium `emb64/bs4096`,
medium_plus `emb32/bs2048`, and large `emb32/bs1024` for 200 steps each with a
fixed `2e-4` learning rate and no scheduler. It writes each subprocess log
under the sweep `logs/` folder and writes comparison rows to
`sweep_results.jsonl` and `sweep_results.csv`.

## Limited Real Training

```powershell
python research\masked_event_model\\v25\train_medium_bit_limited_shards.py --fresh-start
```

This trains over 10 sample-cache shards and uses a shuffled 5% slice of the next
shard for validation. Each epoch is one pass over the selected train shards.

The final long-run launcher for the fixed-grid MLP decoder path is:

```powershell
python research\masked_event_model\\v25\train_10shard_long.py --fresh-start
```

The mixed-cache restart launcher trains from the step-260352 checkpoint using
all shards from `cache_20260611_195259` plus the first 54 shards from
`cache_pretrain_xonly_20260621_140813`. It keeps validation on
`cache_20260617_112833`, uses non-repeatable randomness by default, and shuffles
the combined train shard file list at the beginning of each epoch. The default
schedule runs 3 epochs with base LR `4e-4`:

```powershell
python research\masked_event_model\\v25\train_full_pretrain_mixed_cache.py
```

Use `--print-only` to inspect the fully expanded trainer command before
starting the run. Use `--repeatable-randomness` only for deterministic replay.

The full dual-cache v25 header-reconstruction pretrain launcher starts a fresh
run over all discovered train shards from both `cache_20260611_195259` and
`cache_pretrain_xonly_20260621_140813`. It validates from
`cache_20260617_112833`, uses embedding dim `32`, batch size `8192`, BF16 AMP,
torch compile, shard-decay cosine scheduling, and runs 2 epochs by default:

```powershell
python research\masked_event_model\\v25\train_full_pretrain_dual_cache.py
```

Use `--primary-train-shards` or `--secondary-train-shards` only when you want to
cap a cache for a shorter test run. The default `0` value means all discovered
shards in that cache.

After `cache_pretrain_xonly_20260621_140813` has enough x-only shards, continue
from the latest mixed-cache checkpoint using only that cache:

```powershell
python research\masked_event_model\\v25\train_full_pretrain_xonly_continue.py
```

This defaults to the first 100 x-only train shards, the same validation cache,
3 epochs, base LR `4e-4`, non-repeatable randomness, BF16 AMP, compile enabled,
and warm-starts from:

```text
\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes\masked_event_model\\v25\pretrain\v25-fullpretrain-mixedcache-fixedmask070-emb32-bs8192-lr4e4-3epochs-freshrng-from-step260352\checkpoints\checkpoint_latest.pt
```

Use `--print-only` before starting a long run if you want to inspect the exact
expanded trainer arguments and discovered shard counts.

## Grouped Bottleneck v20 Comparison

Use this focused launcher to compare v25 against the v20 capacity-test runs
without running the whole four-variant sweep. It runs only:

```text
v25 grouped bottleneck
embedding_dim=32
AMP dtype=bf16
batch_size=4096
pretrain on one x-only shard for 5 epochs
then temporal v1 labeled-cache linear probe
```

It uses the same W&B projects as the v20 capacity experiment:

```text
pretrain: June2026-event-token-mae-capacity-tests
linear probe: June2026-event-encoder-linear-probes
```

Workstation command:

```powershell
python D:\TradingML\codes\masked_event_model\\v25\research\masked_event_model\\v25\run_grouped_bottleneck_probe.py
```

Dry run:

```powershell
python D:\TradingML\codes\masked_event_model\\v25\research\masked_event_model\\v25\run_grouped_bottleneck_probe.py --print-only
```

## Embedding Capacity And Bottleneck Precision Test

The capacity/precision launcher runs four controlled variants sequentially.
Each variant first pretrains from scratch on one x-only pretraining shard for
5 epochs at `batch_size=4096`, then immediately runs the same temporal v1
linear probe on the labeled cache:

```text
emb32 bf16 bottleneck
emb32 fp32 bottleneck
emb128 bf16 bottleneck
emb128 fp32 bottleneck
```

The FP32 bottleneck variants keep the transformer and decoder in the active AMP
dtype, but run only the fixed-grid chunk embedding projection outside AMP. This
tests whether the exported representation path loses useful signal in BF16.

Workstation command:

```powershell
python D:\TradingML\codes\masked_event_model\\v25\research\masked_event_model\\v25\run_embedding_precision_probe.py
```

Use `--print-only` first to inspect all generated pretrain and linear-probe
commands. Use `--only emb32-bf16,emb128-bf16` to run a subset.

By default this v25 launcher uses BF16 AMP and keeps the cheaper MLP decoder inside the active AMP
dtype. To run the explicit FP16 decoder experiment:

```powershell
python research\masked_event_model\\v25\train_10shard_long.py --fresh-start --run-name v25-fixedgrid-fp16decoder-fixedmask070-emb32-bs8192-10shards --amp-dtype fp16
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
