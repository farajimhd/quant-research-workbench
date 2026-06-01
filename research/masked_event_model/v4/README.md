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

Default data root:

```text
D:/market-data/flatfiles/us_stocks_sip/derived/canonical_events_compact_v1
```

Build canonical compact data first:

```powershell
python -m research.mlops.build_compact_canonical --flatfiles-root D:\market-data\flatfiles\us_stocks_sip --canonical-root D:\market-data\flatfiles\us_stocks_sip\derived\canonical_events_compact_v1 --temp-root D:\market-data\flatfiles\us_stocks_sip\derived\_tmp_compact_canonical_parts --start-date 2025-11-01 --end-date 2025-12-05 --tickers ALL --processes 16 --normalize-processes 16 --merge-processes 16 --rebuild
```

Run training:

```powershell
python research\masked_event_model\v4\run_train.py
```

Run a smoke/dry run:

```powershell
python research\masked_event_model\v4\run_train.py --dry-run
```
