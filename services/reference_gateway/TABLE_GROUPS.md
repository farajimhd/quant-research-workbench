# Reference Gateway Table Groups

This document defines the table groups owned by `reference_gateway`. It excludes
market reference publications by design. Those tables are not integrated into
this phase:

```text
market_security_market_snapshot_v1
market_security_float_v1
market_short_interest_v1
market_short_volume_v1
market_stock_split_v1
market_cash_dividend_v1
market_ipo_v1
market_presentation_asset_v1
massive_flatfile_source_file_v1
```

## Ownership Rule

Only `reference_gateway` should update these groups after the historical
migration is complete. Other runtime services read them:

- QMD reads symbols/listings for market-data routing and conid-aware downstream
  consumers.
- News reads ticker relationships for joins and features.
- SEC reads issuer/security/listing mappings for bridge validation.
- The live trading app reads `feature_tradable_universe_v1`.

They should not write the canonical graph.

Historical migration scripts are allowed one-time/bootstrap writes, but ongoing
sync and correction writes belong here.

## Groups

| Group | Tables | Update Policy |
| --- | --- | --- |
| `reference_dimensions` | `ref_country_v1`, `ref_asset_class_v1`, `ref_exchange_v1`, `ref_exchange_currency_v1`, `ref_ticker_type_v1` | Add clear new source codes. Unmapped Massive/IBKR exchange codes become issues. |
| `issuer_identity` | `id_issuer_v1`, `id_issuer_identifier_v1` | Resolve by durable identifiers first. Fill missing fields only when unambiguous. |
| `security_identity` | `id_security_v1`, `id_security_identifier_v1` | Resolve issuer first, then match security by FIGI/ISIN/CUSIP/conid evidence. |
| `listing_symbol_identity` | `id_listing_v1`, `id_symbol_v1` | Resolve issuer and security first. Fill missing conid only on one exact compatible IBKR contract. |
| `source_mapping_and_issues` | `id_source_mapping_v1`, `id_mapping_issue_v1`, `id_sec_market_bridge_v1` | Compact accepted evidence goes to mappings. Conflicts and ambiguity go to issues and block tradability. |
| `tradable_scanner_publications` | `feature_tradable_universe_v1`, `feature_scanner_static_v1` | Rebuild from canonical graph and enrichment tables. These are outputs, not source truth. |

## Write Semantics

Canonical tables use append/replacement semantics. A writer should:

1. read current canonical rows,
2. classify source observations as `no_change`, `fill_missing_field`,
   `insert_candidate`, or `conflict_issue`,
3. insert replacement rows only when the change is unambiguous,
4. write conflicts to `id_mapping_issue_v1`,
5. keep affected rows non-tradable until issues are resolved.

Do not store full SEC, Massive, or IBKR payloads in canonical tables. Use raw
artifact storage when a full payload must be retained, and store only compact
evidence in mapping/issue rows.

