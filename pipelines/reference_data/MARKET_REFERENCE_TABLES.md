# Market Reference Tables

The unified event builder should map provider ids/codes to compact dense ids in
ClickHouse, not in Python.

Load the current reference snapshots into `market_sip_compact` with:

```powershell
python -m pipelines.reference_data.run_load_market_references
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

For joins, first collapse each table to one row per `modifier_int`:

```sql
SELECT modifier_int, min(dense_id) AS dense_id
FROM market_sip_compact.ref_quote_conditions
GROUP BY modifier_int
```

This is required for quote conditions because the glossary contains repeated
modifier codes across SIP mappings, while the raw quote flatfile only stores the
modifier code. Trade condition modifiers are currently unique, but using the
same unique-map pattern is still safe and consistent.

The final unified event table stores condition-like metadata as one packed
`UInt64`, not as separate condition columns. The token IDs come from:

```text
market_sip_compact.event_condition_token_reference
```

The layout is:

```text
bits  0-39: five 8-bit token slots
bits 40-44: token count, overflow, unknown-token flags
bits 45-49: primary scale, secondary scale, tape code
bits 50-51: pack kind
bits 56-63: pack version
```

Quote rows pack the first four quote condition tokens and the first quote
indicator token. Trade rows pack the first four trade condition tokens and the
trade correction token decoded from `trade_flags`.

```sql
bitOr(
    toUInt64(coalesce(token_1.token_id, 0)),
    bitShiftLeft(toUInt64(coalesce(token_2.token_id, 0)), 8)
) AS condition_tokens_packed
```

Exchange mapping example:

```sql
coalesce(ex.dense_id, 0) AS exchange_primary
```

Tape mapping can be done directly from compact flags or via
`ref_stock_tapes` if a raw tape id is available in the query.
