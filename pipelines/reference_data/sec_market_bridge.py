from __future__ import annotations

from research.mlops.clickhouse import quote_ident


ACTIVE_MAPPING_STATUSES = ("active", "mapped", "accepted", "")


def us_market_listing_predicate_sql(
    *,
    security_alias: str = "sec",
    listing_alias: str = "l",
    symbol_alias: str = "sym",
    exchange_alias: str = "ex",
) -> str:
    """Return the shared U.S. SIP-addressable listing eligibility contract."""

    return "\n".join(
        [
            f"{security_alias}.status = 'active'",
            f"AND {listing_alias}.listing_status = 'active'",
            f"AND {listing_alias}.currency_code = 'USD'",
            f"AND {exchange_alias}.iso_country_code = 'US'",
            f"AND {security_alias}.product_type = 'STK'",
            f"AND {symbol_alias}.asset_type = 'stock'",
            f"AND {symbol_alias}.instrument_type IN ('ADRC', 'CS')",
        ]
    )


def active_bridge_cte_sql(
    *,
    database: str,
    table: str,
    cte_name: str = "bridge",
) -> str:
    """Read exact authoritative bridge rows without field-wise arbitrary aggregation."""

    statuses = ", ".join(f"'{value}'" for value in ACTIVE_MAPPING_STATUSES)
    return f"""
{cte_name} AS
(
    SELECT DISTINCT
        ifNull(ticker, '') AS ticker,
        cik,
        ifNull(accession_number, '') AS accession_number,
        valid_from_date,
        valid_to_date_exclusive,
        bridge_id,
        ifNull(security_id, '') AS security_id,
        ifNull(listing_id, '') AS listing_id,
        ifNull(symbol_id, '') AS symbol_id,
        confidence_score
    FROM {quote_ident(database)}.{quote_ident(table)} FINAL
    WHERE ifNull(ticker, '') != ''
      AND mapping_status IN ({statuses})
)
""".strip()
