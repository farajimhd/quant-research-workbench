# Masked Event Model v4

v4 trains a compact byte-level masked autoencoder over compact event samples.
The intended training path is now:

```text
market_sip_compact.events -> SSD sample cache -> GPU training
```

Input:

```text
header_uint8: [B, 14]
events_uint8: [B, 128, 16]
```

The encoder produces:

```text
chunk_embedding: [B, 32]
event_embeddings: [B, 128, 32]
```

Training objective:

```text
BCE on masked byte bits only
```

The decoder predicts only masked byte positions:

```text
header_bit_probs: [masked_header_bytes, 8]
event_bit_probs:  [masked_event_bytes, 8]
```

The decoder applies sigmoid at the final bit head. The training loss consumes
those probabilities directly with plain binary cross entropy. Reported learning
metrics include raw bit accuracy, majority-bit baseline, bit-accuracy lift,
balanced bit accuracy, byte exact accuracy, byte-mode baseline, and byte-exact
lift. The baseline/lift metrics matter because compact bytes are sparse in bit
space and raw bit accuracy can look high even for a weak predictor.

Default ClickHouse source used to build the sample cache:

```text
events table: market_sip_compact.events
train index:  market_sip_compact.train_2019_to_2025
val index:    market_sip_compact.validation_2026
```

The cache builder samples ordinal spans:

```text
batch_size = num_spans * origins_per_span
```

Default:

```text
batch_size = 4096
num_spans = 128
origins_per_span = 32
random origin_stride = 1..16
query_bundle_spans = 64
```

Each span chooses one ticker from the split index, one base origin ordinal, and
a random stride. It fetches one continuous ClickHouse range with:

```sql
PREWHERE ticker = <ticker>
  AND ordinal >= <low>
  AND ordinal <= <high>
FORMAT RowBinary
```

The builder then makes multiple training samples from that span by sliding
128-event windows locally. This avoids one ClickHouse query per sample.

The saved cache stores flat sample records, not fixed batches:

```text
sample_bytes = 14 + 128 * 16 = 2062
```

This means the same cache can be reused with different training batch sizes.

Build a first cache:

```powershell
python research\mlops\run_build_event_sample_cache.py
```

Validate it:

```powershell
python research\mlops\run_validate_event_sample_cache.py --cache-root D:\market-data\prepared\event_sample_cache
```

Run training:

```powershell
python research\masked_event_model\v4\run_train.py
```

Run the initial medium bit-input real-training pass over 10 train shards and
10% of the next shard as fixed validation:

```powershell
python research\masked_event_model\v4\train_medium_bit_limited_shards.py --fresh-start
```

This launcher uses:

```text
input_representation = bit
model_size = medium
embedding_dim = 32
batch_size = 4096
epochs = 10
train shards = train/shard_000000..train/shard_000009
validation = first 10% of train/shard_000010
W&B project = June2026-compact-bit-event-training
```

Each training run writes its analyzable run state under one run directory:

```text
config.json
run_manifest.json
metrics.jsonl
logs/fatal_error.txt
checkpoints/
artifacts/model/model_details.json
artifacts/model/model_parameters.jsonl
artifacts/model/model_summary.txt
artifacts/model/model_summary_torchinfo.txt
artifacts/model/model_architecture_torchview.png
artifacts/model/model_architecture_torchview.svg
```

If optional graph packages are unavailable, the run writes a matching
`*_error.txt` file instead of silently skipping the artifact. When W&B is
enabled, metrics are streamed during training and the model summary/diagram
files are saved to the run.

Alternative older data paths remain available:

```powershell
python research\masked_event_model\v4\run_train.py --data-source clickhouse_events
python research\masked_event_model\v4\run_train.py --data-source precomputed
python research\masked_event_model\v4\run_train.py --data-source canonical
```

Sample-cache and precomputed training use shard epochs:

```text
step = one optimizer update on one mini-batch
shard_step = one mini-batch inside the currently loaded shard
epoch = one pass over cached shards in shard-index order
```

For sample-cache training, each shard is loaded into memory, shuffled once, and
then consumed as contiguous mini-batch slices. The final partial mini-batch in a
shard is dropped by default.

The validation cache is fixed at startup: it samples `pretrain_validation_steps`
validation batches and keeps them in memory for cheap repeated
evaluation. Set `--max-steps 0 --epochs 1` to run one complete train-shard
epoch without a step cap.

Run a smoke/dry run:

```powershell
python research\masked_event_model\v4\run_train.py --dry-run
```

## Progress And Profiling

Training supports a Rich console layout:

```powershell
python research\masked_event_model\v4\run_train.py --progress-layout rich
```

The profiler can be enabled for the first `N` optimizer steps and/or at a fixed
interval:

```powershell
--profile-first-steps 25
--profile-training-every-steps 100
--profile-inference-every-steps 100
```

Profile metrics include data wait, host-to-device transfer, masking,
forward/loss, backward, optimizer, inference encode timing, process RSS, system
memory, and CUDA allocated/reserved/peak memory when running on GPU.

Large batches can enable chunked masked-byte decoding:

```powershell
--decoder-chunk-size 524288
```

This keeps the same masked-byte BCE objective but decodes/backprops masked bytes
in chunks to reduce peak decoder activation memory. When this mode is enabled,
`--compile-model` is ignored because the training step uses a custom
encoder-output-gradient accumulation path instead of the model's single
`forward` method.

To test model/data/training parameters on one finalized sample-cache shard:

```powershell
python D:\TradingML\codes\masked_event_model\v4\research\masked_event_model\v4\run_profile_one_shard.py --steps 25 --batch-size 1024
```

That launcher uses `--max-index-files 1`, disables validation and W&B by
default, uses `--decoder-chunk-size 524288`, profiles every step, and keeps the run small enough for quick
iteration over batch size, model size, masking, and learning-rate choices.

Run the full model-size profiling sweep:

```powershell
python D:\TradingML\codes\masked_event_model\v4\research\masked_event_model\v4\run_model_size_sweep.py --fresh-start
```

Default practical sweep:

```text
tiny_d128:  embedding_dim = 16, 32, 64; batch_size = 4096
small_plus: embedding_dim = 16, 32, 64; batch_size = 4096, 8192
medium:     embedding_dim = 16, 32, 64; batch_size = 4096
steps = 200
scheduler_t0_steps = steps unless explicitly overridden
decoder_chunk_size = 524288
```

The full grid from the earlier broad sweep remains available with
`--profile-set grid`.

The sweep runs one trainer subprocess per combination, so CUDA allocator state
does not leak between model sizes. Each run writes its normal training
artifacts under its own run directory. The sweep also writes aggregate files:

```text
v4-prob-bce-size-sweep-summary/sweep_config.json
v4-prob-bce-size-sweep-summary/sweep_results.jsonl
v4-prob-bce-size-sweep-summary/sweep_results.csv
```

Useful overrides:

```powershell
python D:\TradingML\codes\masked_event_model\v4\research\masked_event_model\v4\run_model_size_sweep.py --profile-set grid --model-sizes tiny_d128,medium --embedding-dims 32 --batch-sizes 4096 --steps 10 --print-only
```
