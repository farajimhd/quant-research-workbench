from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password, quote_ident, sql_string
from services.reference_gateway.config import ReferenceGatewayConfig
from services.reference_gateway.market_publications import SEC_COMPANY_COUNTRY_COVERAGE_KIND, insert_publication_coverage, table_exists
from services.reference_gateway.sec_company_profiles import materialize_sec_company_profiles


@dataclass(frozen=True, slots=True)
class CountryAssertionResult:
    status: str
    rows_written: int
    reason: str
    source_run_id: str


def write_country_assertions(config: ReferenceGatewayConfig, *, reason: str) -> CountryAssertionResult:
    if not config.execute:
        return CountryAssertionResult("skipped", 0, "execute_false:" + reason, "")
    client = ClickHouseHttpClient(config.clickhouse_url, config.clickhouse_user, default_clickhouse_password())
    sec_database = os.environ.get("REFERENCE_SEC_CLICKHOUSE_DATABASE") or os.environ.get("SEC_CLICKHOUSE_DATABASE") or "sec_core"
    source_required = (
        "feature_tradable_universe_v1", "ref_exchange_v1", "id_listing_v1", "id_sec_market_bridge_v3",
    )
    target_required = ("market_security_country_v1", "market_issuer_company_profile_v1")
    missing = [name for name in source_required if not table_exists(client, config.clickhouse_read_database, name)]
    missing.extend(name for name in target_required if not table_exists(client, config.clickhouse_write_database, name))
    if not table_exists(client, sec_database, "sec_bulk_mirror_company_v3"):
        missing.append(f"{sec_database}.sec_bulk_mirror_company_v3")
    if missing:
        return CountryAssertionResult("skipped", 0, "missing_tables:" + ",".join(missing), "")
    run_id = "reference_gateway_country_assertions_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    started_at = datetime.now(UTC)
    inserted_at = started_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    profile_result = materialize_sec_company_profiles(
        client,
        read_database=config.clickhouse_read_database,
        write_database=config.clickhouse_write_database,
        sec_database=sec_database,
        start_date=started_at.date(),
        end_date=started_at.date(),
        run_id=run_id,
        include_current_submissions=True,
    )
    client.execute(
        f"""
INSERT INTO {table(config.clickhouse_write_database, 'market_security_country_v1')}
(country_assertion_id, symbol_id, listing_id, security_id, issuer_id, provider_ticker, assertion_date, listing_country_code, issuer_legal_country_code, issuer_hq_country_code, security_issue_country_code, effective_country_code, confidence_score, source_system, source_event_key, source_evidence_ref, source_run_id, source_content_sha256, inserted_at, available_at_utc)
WITH
    today() AS assertion_date,
    {sql_string(run_id)} AS source_run_id,
    toDateTime64({sql_string(inserted_at)}, 3, 'UTC') AS write_inserted_at,
    exchanges AS
    (
        SELECT
            exchange_code,
            if(uniqExactIf(upper(iso_country_code), ifNull(iso_country_code, '') != '') = 1,
               anyIf(upper(iso_country_code), ifNull(iso_country_code, '') != ''),
               CAST(NULL, 'Nullable(String)')) AS iso_country_code
        FROM {table(config.clickhouse_read_database, 'ref_exchange_v1')} FINAL
        GROUP BY exchange_code
    ),
    profiles AS
    (
        SELECT
            issuer_id,
            argMaxIf(issuer_legal_country_code, tuple(available_at_utc, inserted_at), ifNull(issuer_legal_country_code, '') != '') AS issuer_legal_country_code,
            argMaxIf(issuer_business_country_code, tuple(available_at_utc, inserted_at), ifNull(issuer_business_country_code, '') != '') AS issuer_business_country_code
        FROM {table(config.clickhouse_write_database, 'market_issuer_company_profile_v1')} FINAL
        WHERE available_at_utc <= now64(9, 'UTC')
        GROUP BY issuer_id
    )
SELECT
    concat('country:', u.symbol_id, ':', toString(assertion_date), ':', lower(hex(MD5(concat(u.symbol_id, ':', ifNull(ex.iso_country_code, ''), ':', ifNull(p.issuer_legal_country_code, ''), ':', ifNull(p.issuer_business_country_code, '')))))) AS country_assertion_id,
    u.symbol_id,
    u.listing_id,
    u.security_id,
    u.issuer_id,
    upper(u.ticker) AS provider_ticker,
    assertion_date,
    nullIf(upper(ifNull(ex.iso_country_code, '')), '') AS listing_country_code,
    nullIf(upper(ifNull(p.issuer_legal_country_code, '')), '') AS issuer_legal_country_code,
    nullIf(upper(ifNull(p.issuer_business_country_code, '')), '') AS issuer_hq_country_code,
    CAST(NULL, 'Nullable(String)') AS security_issue_country_code,
    coalesce(nullIf(upper(ifNull(p.issuer_business_country_code, '')), ''), nullIf(upper(ifNull(p.issuer_legal_country_code, '')), ''), nullIf(upper(ifNull(ex.iso_country_code, '')), '')) AS effective_country_code,
    multiIf(ifNull(p.issuer_business_country_code, '') != '', 0.95, ifNull(p.issuer_legal_country_code, '') != '', 0.90, ifNull(ex.iso_country_code, '') != '', 0.80, 0.0) AS confidence_score,
    'reference_gateway' AS source_system,
    concat('feature_tradable_universe_v1:', toString(u.universe_date), ':', u.symbol_id) AS source_event_key,
    concat('id_listing_v1/ref_exchange_v1:', ifNull(listing.exchange_code, ''), ';market_issuer_company_profile_v1:', u.issuer_id) AS source_evidence_ref,
    source_run_id,
    lower(hex(MD5(concat(u.symbol_id, ':', ifNull(listing.exchange_code, ''), ':', ifNull(ex.iso_country_code, ''), ':', ifNull(p.issuer_legal_country_code, ''), ':', ifNull(p.issuer_business_country_code, ''))))) AS source_content_sha256,
    write_inserted_at AS inserted_at,
    toDateTime64(write_inserted_at, 9, 'UTC') AS available_at_utc
FROM
(
    SELECT *
    FROM {table(config.clickhouse_read_database, 'feature_tradable_universe_v1')} FINAL
    WHERE universe_date = (SELECT max(universe_date) FROM {table(config.clickhouse_read_database, 'feature_tradable_universe_v1')} FINAL)
) AS u
LEFT JOIN {table(config.clickhouse_read_database, 'id_listing_v1')} AS listing FINAL ON listing.listing_id = u.listing_id
LEFT JOIN exchanges AS ex ON ex.exchange_code = listing.exchange_code
LEFT JOIN profiles AS p ON p.issuer_id = u.issuer_id
WHERE u.symbol_id != ''
  AND ifNull(listing.exchange_code, '') != ''
""".strip()
    )
    rows = int(
        client.query_tsv(
            f"""
            SELECT count()
            FROM {table(config.clickhouse_write_database, 'market_security_country_v1')} FINAL
            WHERE source_run_id = {sql_string(run_id)}
            """
        ).strip()
        or "0"
    )
    finished_at = datetime.now(UTC)
    insert_publication_coverage(
        client,
        database=config.clickhouse_write_database,
        coverage_id=f"{run_id}:sec_country_assertions:{started_at.date().isoformat()}",
        coverage_kind=SEC_COMPANY_COUNTRY_COVERAGE_KIND,
        source_system="reference_gateway",
        source_object="sec_bulk_mirror_company_v3/market_issuer_company_profile_v1/id_listing_v1/ref_exchange_v1",
        start_date=started_at.date(),
        end_date=started_at.date() + timedelta(days=1),
        status="completed",
        rows_read=rows,
        rows_written=rows,
        rows_failed=0,
        started_at_utc=started_at,
        finished_at_utc=finished_at,
        details={
            "reason": reason,
            "submissions_profiles": profile_result.submissions_rows,
            "filing_profiles_read": profile_result.filing_rows_read,
            "filing_profiles_written": profile_result.filing_rows_written,
            "filing_profiles_rejected": profile_result.filing_rows_rejected,
            "filing_profiles_skipped": profile_result.filing_rows_skipped,
        },
        source_run_id=run_id,
    )
    return CountryAssertionResult("completed", rows, reason, run_id)


def table(database: str, name: str) -> str:
    return f"{quote_ident(database)}.{quote_ident(name)}"
