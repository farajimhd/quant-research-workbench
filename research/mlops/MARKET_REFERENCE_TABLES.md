# Market Reference Tables

The unified event builder should map provider ids/codes to compact dense ids in
ClickHouse, not in Python.

Load the current reference snapshots into `market_sip_compact` with:

```powershell
python D:\TradingML\codes\masked_event_model\v4\research\mlops\run_load_market_references.py
```

Tables created:

```text
market_sip_compact.ref_stock_conditions
market_sip_compact.ref_stock_exchanges
market_sip_compact.ref_stock_tapes
```

Schema:

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

The JSON snapshots already include:

```text
dense_id = 0 for unknown/missing
reserved future rows
dense_id_bits
```

The unified event builder should left join these tables and default missing
matches to dense id `0`.

Condition mapping example:

```sql
coalesce(c1.dense_id, 0) AS condition_1
```

Exchange mapping example:

```sql
coalesce(ex.dense_id, 0) AS exchange_primary
```

Tape mapping can be done directly from compact flags or via
`ref_stock_tapes` if a raw tape id is available in the query.
