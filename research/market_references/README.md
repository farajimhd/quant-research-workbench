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

Use dense integer IDs for model embeddings, with `0` reserved for missing or unknown. Keep raw Massive IDs in these tables for reverse mapping and reproducibility.
