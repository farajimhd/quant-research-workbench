# Market References

This folder stores stable market-data reference tables used by research data encoders.

## Massive

Refresh stock exchange and condition mappings:

```powershell
python research\market_references\download_massive_reference_tables.py
python research\market_references\extract_massive_glossary_tables.py
python pipelines\reference_data\clickhouse_load_market_references.py
```

The API downloader reads `MASSIVE_API_KEY` from the process environment or the
repo `.env` file and never writes the key to output files. The glossary
extractor reads the public Massive conditions/indicators page and does not need
an API key.

Saved files:

- `massive/stock_exchanges.json`
- `massive/stock_conditions.json`
- `massive/stock_tapes.json`
- `massive/reference_summary.json`
- `massive/conditions_indicators_glossary.json`

`conditions_indicators_glossary.json` is the source for separate ClickHouse
reference tables used by unified event construction:

- `market_sip_compact.ref_quote_conditions`
- `market_sip_compact.ref_trade_conditions`
- `market_sip_compact.ref_trade_corrections_nyse`
- `market_sip_compact.ref_financial_status`
- `market_sip_compact.ref_cta_security_status`
- `market_sip_compact.ref_halt_reason`
- `market_sip_compact.ref_utp_security_status`
- `market_sip_compact.ref_nbbo_indicators`
- `market_sip_compact.ref_held_trade_indicators`
- `market_sip_compact.ref_misc_indicators`
- `market_sip_compact.ref_luld_indicators`

Each glossary reference table includes `source_row`, `modifier_int`, and
`dense_id`. `source_row` preserves the visible row order from the Massive page
and keeps rows distinguishable when a table domain has repeated numeric
modifiers, such as parts of the LULD indicator section.

Use `dense_id` for model embeddings and binary/categorical packing:

- `dense_id = 0` is reserved for missing or unknown provider values.
- `dense_id_kind = actual` rows map to current Massive reference rows.
- `dense_id_kind = reserved_future` rows reserve capacity for future provider additions without changing the encoded bit width.
- `dense_id_binary` stores the fixed-width binary representation using `dense_id_bits`.

Keep raw Massive IDs in these tables for reverse mapping and reproducibility.

## Compact Microstructure Encoding

The compact quote/trade event representation is specified in
`compact_market_microstructure_representation.md`.
