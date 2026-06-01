# Masked Event Model v4

v4 trains a compact byte-level masked autoencoder over `EventsChunk` samples.

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

Default precomputed chunk root:

```text
D:/market-data/prepared/us_stocks_sip/v4_compact_event_chunks_v1
```

Build precomputed v4 chunks first:

```powershell
python -m research.mlops.run_build_v4_chunks --rebuild
```

The trainer accepts either the prepared root above or its `chunks` subfolder as `--precomputed-chunk-root`.
Chunk build issues and state are written under:

```text
D:/market-data/prepared/us_stocks_sip/v4_compact_event_chunks_v1/issues
```

Run training:

```powershell
python research\masked_event_model\v4\run_train.py
```

Run a smoke/dry run:

```powershell
python research\masked_event_model\v4\run_train.py --dry-run
```
