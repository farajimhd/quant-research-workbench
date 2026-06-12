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
BCEWithLogitsLoss on masked byte bits only
```

The decoder predicts only masked byte positions:

```text
header_bit_logits: [masked_header_bytes, 8]
event_bit_logits:  [masked_event_bytes, 8]
```

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

To test model/data/training parameters on one finalized sample-cache shard:

```powershell
python D:\TradingML\codes\masked_event_model\v4\research\masked_event_model\v4\run_profile_one_shard.py --steps 25 --batch-size 1024
```

That launcher uses `--max-index-files 1`, disables validation and W&B by
default, profiles every step, and keeps the run small enough for quick
iteration over batch size, model size, masking, and learning-rate choices.
