# Reference Gateway Table Groups

This document defines the table groups owned by `reference_gateway`, including
the slow market-reference publication tables used by scanner setup and
tradability checks.

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
| `point_in_time_symbol_identity` | `id_symbol_interval_v1`, `market_ticker_event_correction_v1` | Publish half-open ticker validity intervals from exact Composite FIGI mappings. Reviewed SEC-backed corrections are bound to exact Composite and Share Class FIGIs and fail closed on provider-signature drift; retain all other conflicts as coverage findings. |
| `source_mapping_and_issues` | `id_source_mapping_v1`, `id_mapping_issue_v1`, `id_issuer_relationship_v1`, `id_sec_market_bridge_v3` | Compact accepted evidence goes to mappings. Conflicts and ambiguity go to issues and block tradability. Validity-dated issuer relationships support evidence-backed listed-parent resolution; v3 is the only active SEC bridge. |
| `tradable_scanner_publications` | `feature_tradable_universe_v1`, `feature_scanner_static_v1` | Rebuild from canonical graph and enrichment tables. These are outputs, not source truth. |
| `market_reference_publications` | `market_security_market_snapshot_v1`, `market_security_float_v1`, `market_short_interest_v1`, `market_short_volume_v1`, `market_stock_split_v1`, `market_cash_dividend_v1`, `market_ipo_v1`, `market_presentation_asset_v1`, `market_issuer_presentation_candidate_v1`, `market_issuer_presentation_selection_v1`, `market_fails_to_deliver_v1`, `market_reg_sho_threshold_v1`, `market_security_borrow_v1`, `market_issuer_company_profile_v1`, `market_security_country_v1`, `market_ticker_event_entity_v1`, `market_ticker_event_v1`, `market_ticker_event_entity_coverage_v1`, `market_reference_publication_coverage_v1` | Fill from source publications. Reference Gateway resolves issuer-linked presentation candidates from verified SEC images and Massive icon/logo assets; Massive remains the immediate fallback. FINRA owns short volume, Massive owns short interest, ticker events, corporate actions, overview snapshots, and float/share-supply rows; SEC owns fails-to-deliver plus issuer legal-name, incorporation, business-address, mailing-address, and country evidence; canonical listing/exchange identity owns listing country; IBKR owns broker-specific borrow availability. Date-window, candidate-ledger, and per-entity coverage rows define completeness. |
| `source_schedule` | `market_reference_source_schedule_v1` | Persist provider cadence and completion state so daemon restarts do not repeat expensive refreshes. |

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

The former generic fact and reference-alert tables are stale and are not part
of the active gateway contract. Existing database tables are left untouched for
non-destructive retirement, but the gateway does not create, fill, audit, or
consume them.

## Market Publication Coverage

`market_reference_publication_coverage_v1` is the source of truth for whether a
market publication source has been inspected for a date window. A completed
coverage row can mean rows were inserted, or the source was checked and no rows
existed for that window.

Initial implemented historical sources:

- FINRA daily short sale volume files into `market_short_volume_v1`.
- Massive short-interest publications into `market_short_interest_v1`.
- SEC fails-to-deliver files into `market_fails_to_deliver_v1`.
- NasdaqTrader Reg SHO threshold lists into `market_reg_sho_threshold_v1`.

Point-in-time and later-stage sources:

- IBKR borrow availability writes `market_security_borrow_v1`; it is not
  historically reconstructable from IBKR.
- Massive overview, splits, dividends, IPOs, and presentation assets keep using
  compact source evidence and coverage rows.
- SEC company profiles preserve legal name, incorporation jurisdiction,
  structured business and mailing addresses, exact source availability, and
  filing/submissions provenance. Current submissions snapshots are live-only;
  historical profiles come from inline-XBRL DEI facts at SEC acceptance time.
- Country assertions keep listing, legal issuer, disclosed-business-address,
  issue, and effective country fields separate. A U.S. listing therefore does
  not turn a foreign issuer into a U.S. company.
