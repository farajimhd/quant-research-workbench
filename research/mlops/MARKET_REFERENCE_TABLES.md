# Market Reference Tables

The unified event builder should map provider ids/codes to compact dense ids in
ClickHouse, not in Python.

Load the current reference snapshots into `market_sip_compact` with:

```powershell
python D:\TradingML\codes\masked_event_model\v4\research\mlops\run_load_market_references.py
```

Tables created:

```text
market_sip_compact.ref_quote_conditions
market_sip_compact.ref_trade_conditions
market_sip_compact.ref_stock_exchanges
market_sip_compact.ref_stock_tapes
```

Quote and trade conditions come from Massive's conditions/indicators glossary,
where the quote condition table and trade condition table are separate. Do not
use the generic `/v3/reference/conditions` table as the training condition map.

Condition table schema:

```sql
reference_name LowCardinality(String)
modifier_int Int16
raw_modifier LowCardinality(String)
dense_id UInt8
dense_id_bits UInt8
condition String
sip_mapping LowCardinality(String)
update_high_low UInt8
update_last UInt8
update_volume UInt8
provider LowCardinality(String)
```

Exchange/tape table schema:

```sql
reference_name LowCardinality(String)
raw_id Nullable(Int32)
raw_code LowCardinality(String)
dense_id UInt8
dense_id_bits UInt8
dense_id_kind LowCardinality(String)
name String
description String
provider LowCardinality(String)
```

The exchange/tape JSON snapshots already include:

```text
dense_id = 0 for unknown/missing
reserved future rows
dense_id_bits
```

The glossary-derived condition tables assign dense IDs independently:

```text
ref_quote_conditions: dense_id 0 = unknown, dense_id 1..193 = quote modifiers
ref_trade_conditions: dense_id 0 = unknown, dense_id 1..57 = trade modifiers
```

The unified event builder should left join these tables and default missing
matches to dense id `0`.

Quote condition mapping example:

```sql
coalesce(qc1.dense_id, 0) AS condition_1
```

Trade condition mapping example:

```sql
coalesce(tc1.dense_id, 0) AS condition_1
```

Exchange mapping example:

```sql
coalesce(ex.dense_id, 0) AS exchange_primary
```

Tape mapping can be done directly from compact flags or via
`ref_stock_tapes` if a raw tape id is available in the query.
