from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password, quote_ident, sql_string
from services.reference_gateway.config import ReferenceGatewayConfig
from services.reference_gateway.market_publications import insert_publication_coverage, table_exists


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
    required = ("feature_tradable_universe_v1", "ref_exchange_v1", "market_security_country_v1")
    missing = [name for name in required if not table_exists(client, config.clickhouse_write_database, name)]
    if missing:
        return CountryAssertionResult("skipped", 0, "missing_tables:" + ",".join(missing), "")
    run_id = "reference_gateway_country_assertions_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    started_at = datetime.now(UTC)
    inserted_at = started_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    client.execute(
        f"""
INSERT INTO {table(config.clickhouse_write_database, 'market_security_country_v1')}
(country_assertion_id, symbol_id, listing_id, security_id, issuer_id, provider_ticker, assertion_date, listing_country_code, issuer_legal_country_code, issuer_hq_country_code, security_issue_country_code, effective_country_code, confidence_score, source_system, source_event_key, source_evidence_ref, source_run_id, source_content_sha256, inserted_at)
WITH
    today() AS assertion_date,
    {sql_string(run_id)} AS source_run_id,
    toDateTime64({sql_string(inserted_at)}, 3, 'UTC') AS inserted_at
SELECT
    concat('country:', u.symbol_id, ':', toString(assertion_date), ':', lower(hex(MD5(concat(u.symbol_id, ':', ifNull(ex.iso_country_code, '')))))) AS country_assertion_id,
    u.symbol_id,
    u.listing_id,
    u.security_id,
    u.issuer_id,
    upper(u.ticker) AS provider_ticker,
    assertion_date,
    nullIf(upper(ifNull(ex.iso_country_code, '')), '') AS listing_country_code,
    CAST(NULL, 'Nullable(String)') AS issuer_legal_country_code,
    CAST(NULL, 'Nullable(String)') AS issuer_hq_country_code,
    CAST(NULL, 'Nullable(String)') AS security_issue_country_code,
    nullIf(upper(ifNull(ex.iso_country_code, '')), '') AS effective_country_code,
    if(upper(ifNull(ex.iso_country_code, '')) = 'US', 0.85, 0.65) AS confidence_score,
    'reference_gateway' AS source_system,
    concat('feature_tradable_universe_v1:', toString(u.universe_date), ':', u.symbol_id) AS source_event_key,
    concat('ref_exchange_v1:', ifNull(u.exchange_code, '')) AS source_evidence_ref,
    source_run_id,
    lower(hex(MD5(concat(u.symbol_id, ':', ifNull(u.exchange_code, ''), ':', ifNull(ex.iso_country_code, ''))))) AS source_content_sha256,
    inserted_at
FROM
(
    SELECT *
    FROM {table(config.clickhouse_write_database, 'feature_tradable_universe_v1')} FINAL
    WHERE universe_date = (SELECT max(universe_date) FROM {table(config.clickhouse_write_database, 'feature_tradable_universe_v1')} FINAL)
) AS u
LEFT JOIN {table(config.clickhouse_write_database, 'ref_exchange_v1')} AS ex FINAL ON ex.exchange_code = u.exchange_code
WHERE u.symbol_id != ''
  AND ifNull(u.exchange_code, '') != ''
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
        coverage_kind="sec_country_assertions",
        source_system="reference_gateway",
        source_object="feature_tradable_universe_v1/ref_exchange_v1",
        start_date=started_at.date(),
        end_date=started_at.date() + timedelta(days=1),
        status="completed",
        rows_read=rows,
        rows_written=rows,
        rows_failed=0,
        started_at_utc=started_at,
        finished_at_utc=finished_at,
        details={"reason": reason},
        source_run_id=run_id,
    )
    return CountryAssertionResult("completed", rows, reason, run_id)


def table(database: str, name: str) -> str:
    return f"{quote_ident(database)}.{quote_ident(name)}"
