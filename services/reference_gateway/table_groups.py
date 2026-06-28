from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReferenceTableGroup:
    group_id: str
    owner: str
    purpose: str
    tables: tuple[str, ...]
    update_policy: str


REFERENCE_TABLE_GROUPS: tuple[ReferenceTableGroup, ...] = (
    ReferenceTableGroup(
        group_id="reference_dimensions",
        owner="reference_gateway",
        purpose="Small controlled dimensions used by identity and tradability resolution.",
        tables=(
            "ref_country_v1",
            "ref_asset_class_v1",
            "ref_exchange_v1",
            "ref_exchange_currency_v1",
            "ref_ticker_type_v1",
        ),
        update_policy=(
            "Add new source codes only when they map cleanly to one canonical dimension row. "
            "Unmapped Massive/IBKR exchange evidence becomes an issue instead of an overwrite."
        ),
    ),
    ReferenceTableGroup(
        group_id="issuer_identity",
        owner="reference_gateway",
        purpose="Canonical issuer/company identity and durable issuer identifiers.",
        tables=("id_issuer_v1", "id_issuer_identifier_v1"),
        update_policy=(
            "Resolve by durable identifiers first. Fill missing issuer fields from SEC/Massive/IBKR only when "
            "unambiguous; conflicting populated fields create mapping issues."
        ),
    ),
    ReferenceTableGroup(
        group_id="security_identity",
        owner="reference_gateway",
        purpose="Canonical instruments issued by issuers and durable security identifiers.",
        tables=("id_security_v1", "id_security_identifier_v1"),
        update_policy=(
            "Resolve parent issuer first, then match securities by FIGI/ISIN/CUSIP/conid evidence. "
            "Do not create or merge securities from ticker/name evidence alone."
        ),
    ),
    ReferenceTableGroup(
        group_id="listing_symbol_identity",
        owner="reference_gateway",
        purpose="Exchange/currency listings and provider symbols attached to listings.",
        tables=("id_listing_v1", "id_symbol_v1"),
        update_policy=(
            "Resolve issuer and security before creating listings or symbols. Fill missing IBKR conids only "
            "when IBKR returns exactly one compatible STK/USD contract."
        ),
    ),
    ReferenceTableGroup(
        group_id="source_mapping_and_issues",
        owner="reference_gateway",
        purpose="Accepted source-to-canonical mappings, unresolved mapping issues, and SEC-to-market bridge.",
        tables=("id_source_mapping_v1", "id_mapping_issue_v1", "id_sec_market_bridge_v1"),
        update_policy=(
            "Accepted mappings store compact evidence only. Ambiguity, conflict, missing parents, or failed "
            "resolver steps are recorded as issues and block tradability."
        ),
    ),
    ReferenceTableGroup(
        group_id="tradable_scanner_publications",
        owner="reference_gateway",
        purpose="Derived daily publications consumed by live trading and scanner setup.",
        tables=("feature_tradable_universe_v1", "feature_scanner_static_v1"),
        update_policy=(
            "Rebuild from the canonical graph and enrichment tables. These tables are outputs, not source "
            "truth; rows become tradable only after all identity, listing, conid, exchange, and issue checks pass."
        ),
    ),
    ReferenceTableGroup(
        group_id="market_reference_publications",
        owner="reference_gateway",
        purpose="Slow-changing market publications used by scanner setup, liquidity stress, and tradability checks.",
        tables=(
            "market_security_market_snapshot_v1",
            "market_security_float_v1",
            "market_short_interest_v1",
            "market_short_volume_v1",
            "market_stock_split_v1",
            "market_cash_dividend_v1",
            "market_ipo_v1",
            "market_presentation_asset_v1",
            "massive_flatfile_source_file_v1",
            "market_fails_to_deliver_v1",
            "market_reg_sho_threshold_v1",
            "market_security_borrow_v1",
            "market_security_country_v1",
            "market_reference_publication_coverage_v1",
        ),
        update_policy=(
            "Fill from authoritative or best-available publication sources. FINRA owns short volume and short "
            "interest, SEC owns fails-to-deliver and XBRL-derived country/float evidence, Massive owns corporate "
            "actions and overview snapshots, and IBKR owns broker-specific borrow availability. Coverage rows are "
            "the source of truth for historical/gap-fill completeness."
        ),
    ),
    ReferenceTableGroup(
        group_id="reference_alerts",
        owner="reference_gateway",
        purpose="Universal reference alerts and per-consumer processing state.",
        tables=("market_reference_alert_v1", "market_reference_alert_consumer_state_v1"),
        update_policy=(
            "Emit compact alerts from normalized provider data and internal reference checks. "
            "Consumers track their own processing state without mutating the alert row."
        ),
    ),
)


OWNED_REFERENCE_TABLES: tuple[str, ...] = tuple(table for group in REFERENCE_TABLE_GROUPS for table in group.tables)


def table_group_markdown() -> str:
    lines = [
        "| Group | Owner | Tables | Update Policy |",
        "| --- | --- | --- | --- |",
    ]
    for group in REFERENCE_TABLE_GROUPS:
        tables = "<br>".join(f"`{table}`" for table in group.tables)
        lines.append(f"| `{group.group_id}` | `{group.owner}` | {tables} | {group.update_policy} |")
    return "\n".join(lines)
