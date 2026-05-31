# Market References

This folder stores stable market-data reference tables used by research data encoders.

## Massive

Refresh stock exchange and condition mappings:

```powershell
python research\market_references\download_massive_reference_tables.py
```

The script reads `MASSIVE_API_KEY` from the process environment or the repo `.env` file and never writes the key to output files.

Saved files:

- `massive/stock_exchanges.json`
- `massive/stock_conditions.json`
- `massive/stock_tapes.json`
- `massive/reference_summary.json`

Use `dense_id` for model embeddings and binary/categorical packing:

- `dense_id = 0` is reserved for missing or unknown provider values.
- `dense_id_kind = actual` rows map to current Massive reference rows.
- `dense_id_kind = reserved_future` rows reserve capacity for future provider additions without changing the encoded bit width.
- `dense_id_binary` stores the fixed-width binary representation using `dense_id_bits`.

Keep raw Massive IDs in these tables for reverse mapping and reproducibility.
