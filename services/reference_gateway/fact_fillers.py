from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password, quote_ident, sql_string
from services.reference_gateway.config import ReferenceGatewayConfig
from services.reference_gateway.market_publications import table_exists


@dataclass(frozen=True, slots=True)
class FactFillResult:
    status: str
    tradability_rows: int
    routing_rows: int
    reason: str
    source_database: str
    issue_database: str
    target_database: str
    source_run_id: str

    @property
    def total_rows(self) -> int:
        return self.tradability_rows + self.routing_rows


def fill_reference_tradability_and_routing_facts(config: ReferenceGatewayConfig, *, reason: str) -> FactFillResult:
    if not config.execute:
        return skipped_result(config, "execute_false", reason)
    if config.integrity_mode == "report-only":
        return skipped_result(config, "integrity_report_only", reason)
    client = ClickHouseHttpClient(config.clickhouse_url, config.clickhouse_user, default_clickhouse_password())
    source_database = resolve_source_database(client, config)
    issue_database = resolve_issue_database(client, config)
    required_targets = ("security_tradability_fact_v1", "security_routing_fact_v1")
    missing_targets = [name for name in required_targets if not table_exists(client, config.clickhouse_write_database, name)]
    if missing_targets:
        return skipped_result(config, "missing_fact_tables:" + ",".join(missing_targets), reason)
    run_id = "reference_gateway_fact_fill_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
    tradability_rows = insert_tradability_facts(
        client,
        source_database=source_database,
        issue_database=issue_database,
        target_database=config.clickhouse_write_database,
        source_run_id=run_id,
    )
    routing_rows = insert_routing_facts(
        client,
        source_database=source_database,
        target_database=config.clickhouse_write_database,
        source_run_id=run_id,
    )
    return FactFillResult(
        status="completed",
        tradability_rows=tradability_rows,
        routing_rows=routing_rows,
        reason=reason,
        source_database=source_database,
        issue_database=issue_database,
        target_database=config.clickhouse_write_database,
        source_run_id=run_id,
    )


def insert_tradability_facts(
    client: ClickHouseHttpClient,
    *,
    source_database: str,
    issue_database: str,
    target_database: str,
    source_run_id: str,
) -> int:
    source = table(source_database, "feature_tradable_universe_v1")
    issue_source = table(issue_database, "id_mapping_issue_v1")
    target = table(target_database, "security_tradability_fact_v1")
    client.execute(
        f"""
INSERT INTO {target}
(tradability_fact_id, issuer_id, security_id, listing_id, symbol_id, provider_ticker, effective_at_utc, observed_at_utc, is_tradable, block_status, block_reason, issue_type, issue_id, severity, confidence_score, source_system, source_table, source_event_id, source_evidence_ref, source_content_sha256, source_run_id, inserted_at)
WITH
    now64(3, 'UTC') AS now64,
    latest_date AS
    (
        SELECT max(universe_date) AS universe_date
        FROM {source} FINAL
    ),
    latest_universe AS
    (
        SELECT *
        FROM {source} FINAL
        WHERE universe_date = (SELECT universe_date FROM latest_date)
    ),
    open_issues AS
    (
        SELECT
            source_entity_key,
            argMax(mapping_issue_id, opened_at_utc) AS mapping_issue_id,
            argMax(issue_type, opened_at_utc) AS issue_type
        FROM {issue_source} FINAL
        WHERE lower(issue_status) NOT IN ('resolved', 'closed', 'ignored')
          AND source_entity_key != ''
        GROUP BY source_entity_key
    ),
    universe_keys AS
    (
        SELECT
            symbol_id,
            arrayJoin(arrayFilter(x -> x != '', [symbol_id, listing_id, security_id, issuer_id, upper(ticker)])) AS source_entity_key
        FROM latest_universe
    ),
    issue_by_symbol AS
    (
        SELECT
            k.symbol_id,
            argMax(i.mapping_issue_id, i.mapping_issue_id) AS issue_id,
            argMax(i.issue_type, i.mapping_issue_id) AS issue_type
        FROM universe_keys AS k
        INNER JOIN open_issues AS i ON i.source_entity_key = k.source_entity_key
        GROUP BY k.symbol_id
    ),
    current_state AS
    (
        SELECT
            u.issuer_id,
            u.security_id,
            u.listing_id,
            u.symbol_id,
            u.ticker AS provider_ticker,
            multiIf(
                ifNull(issue.issue_id, '') != '', 'open_mapping_issue',
                ifNull(u.exclusion_reason, '') != '', u.exclusion_reason,
                u.is_tradable = 1, '',
                'not_tradable_reference_rule'
            ) AS resolved_block_reason,
            if(resolved_block_reason = '', 1, 0) AS resolved_is_tradable,
            if(resolved_is_tradable = 1, 'tradable', 'blocked') AS block_status,
            if(resolved_block_reason = '', CAST(NULL, 'Nullable(String)'), resolved_block_reason) AS block_reason,
            if(ifNull(issue.issue_type, '') != '', issue.issue_type, if(resolved_block_reason = '', CAST(NULL, 'Nullable(String)'), resolved_block_reason)) AS issue_type,
            if(ifNull(issue.issue_id, '') = '', CAST(NULL, 'Nullable(String)'), issue.issue_id) AS issue_id,
            if(resolved_is_tradable = 1, 'info', 'warning') AS severity,
            if(resolved_is_tradable = 1, 1.0, 0.95) AS confidence_score,
            concat(toString(u.universe_date), ':', u.symbol_id, ':', block_status, ':', ifNull(block_reason, '')) AS source_event_id,
            concat('feature_tradable_universe_v1:', toString(u.universe_date), ':', u.symbol_id) AS source_evidence_ref,
            lower(hex(MD5(concat(u.symbol_id, ':', toString(u.universe_date), ':', block_status, ':', ifNull(block_reason, ''), ':', ifNull(issue_id, ''))))) AS source_content_sha256,
            now64 AS effective_at_utc,
            now64 AS observed_at_utc,
            now64 AS inserted_at
        FROM latest_universe AS u
        LEFT JOIN issue_by_symbol AS issue ON issue.symbol_id = u.symbol_id
    ),
    latest_fact AS
    (
        SELECT
            symbol_id,
            argMax(is_tradable, inserted_at) AS latest_is_tradable,
            argMax(block_status, inserted_at) AS latest_block_status,
            argMax(ifNull(block_reason, ''), inserted_at) AS latest_block_reason,
            argMax(ifNull(issue_id, ''), inserted_at) AS latest_issue_id
        FROM {target} FINAL
        GROUP BY symbol_id
    ),
    rows_to_insert AS
    (
        SELECT c.*
        FROM current_state AS c
        LEFT JOIN latest_fact AS f ON f.symbol_id = c.symbol_id
        WHERE f.symbol_id = ''
           OR f.symbol_id IS NULL
           OR f.latest_is_tradable != c.resolved_is_tradable
           OR f.latest_block_status != c.block_status
           OR f.latest_block_reason != ifNull(c.block_reason, '')
           OR f.latest_issue_id != ifNull(c.issue_id, '')
    )
SELECT
    concat('tradability:', symbol_id, ':', lower(hex(MD5(concat(source_event_id, ':', {sql_string(source_run_id)}))))) AS tradability_fact_id,
    issuer_id,
    security_id,
    listing_id,
    symbol_id,
    provider_ticker,
    effective_at_utc,
    observed_at_utc,
    resolved_is_tradable AS is_tradable,
    block_status,
    block_reason,
    issue_type,
    issue_id,
    severity,
    confidence_score,
    'reference_gateway' AS source_system,
    'feature_tradable_universe_v1' AS source_table,
    source_event_id,
    source_evidence_ref,
    source_content_sha256,
    {sql_string(source_run_id)} AS source_run_id,
    inserted_at
FROM rows_to_insert
""".strip()
    )
    return count_run_rows(client, target_database, "security_tradability_fact_v1", source_run_id)


def insert_routing_facts(
    client: ClickHouseHttpClient,
    *,
    source_database: str,
    target_database: str,
    source_run_id: str,
) -> int:
    symbols = table(source_database, "id_symbol_v1")
    listings = table(source_database, "id_listing_v1")
    securities = table(source_database, "id_security_v1")
    exchanges = table(source_database, "ref_exchange_v1")
    target = table(target_database, "security_routing_fact_v1")
    client.execute(
        f"""
INSERT INTO {target}
(routing_fact_id, issuer_id, security_id, listing_id, symbol_id, provider_ticker, broker, ibkr_conid, contract_symbol, sec_type, currency_code, exchange_code, listing_exchange, routing_status, ambiguity_status, valid_from_utc, valid_to_utc, confidence_score, source_system, source_table, source_event_id, source_evidence_ref, source_content_sha256, source_run_id, inserted_at)
WITH
    now64(3, 'UTC') AS now64,
    current_state AS
    (
        SELECT
            sec.issuer_id AS issuer_id,
            sec.security_id AS security_id,
            l.listing_id AS listing_id,
            s.symbol_id AS symbol_id,
            upper(s.ticker) AS provider_ticker,
            'ibkr' AS broker,
            ifNull(l.ibkr_conid, CAST(NULL, 'Nullable(String)')) AS ibkr_conid,
            upper(s.ticker) AS contract_symbol,
            sec.product_type AS sec_type,
            l.currency_code AS currency_code,
            l.exchange_code AS exchange_code,
            l.exchange_code AS listing_exchange,
            multiIf(
                l.listing_status != 'active', 'inactive_listing',
                s.status != 'active', 'inactive_symbol',
                NOT match(ifNull(l.ibkr_conid, ''), '^[1-9][0-9]*$'), 'missing_or_invalid_ibkr_conid',
                upper(l.currency_code) != 'USD', 'non_usd_currency',
                upper(ifNull(ex.iso_country_code, '')) != 'US', 'non_us_exchange',
                upper(sec.product_type) NOT IN ('STK', 'STOCK', 'STOCKS'), 'unsupported_product_type',
                'valid'
            ) AS routing_status,
            if(routing_status = 'valid', 'unique', 'unresolved') AS ambiguity_status,
            now64 AS valid_from_utc,
            CAST(NULL, 'Nullable(DateTime64(3, \\'UTC\\'))') AS valid_to_utc,
            if(routing_status = 'valid', 0.95, 0.20) AS confidence_score,
            concat('ibkr:', s.symbol_id, ':', ifNull(l.ibkr_conid, ''), ':', routing_status) AS source_event_id,
            concat('id_listing_v1:', l.listing_id, ':id_symbol_v1:', s.symbol_id) AS source_evidence_ref,
            lower(hex(MD5(concat(s.symbol_id, ':', l.listing_id, ':', ifNull(l.ibkr_conid, ''), ':', routing_status)))) AS source_content_sha256,
            now64 AS inserted_at
        FROM {symbols} AS s FINAL
        INNER JOIN {listings} AS l FINAL ON l.listing_id = s.listing_id
        INNER JOIN {securities} AS sec FINAL ON sec.security_id = l.security_id
        LEFT JOIN {exchanges} AS ex FINAL ON ex.exchange_code = l.exchange_code
        WHERE s.primary_symbol_flag = 1
    ),
    latest_fact AS
    (
        SELECT
            symbol_id,
            argMax(ifNull(ibkr_conid, ''), inserted_at) AS latest_ibkr_conid,
            argMax(routing_status, inserted_at) AS latest_routing_status,
            argMax(ambiguity_status, inserted_at) AS latest_ambiguity_status,
            argMax(ifNull(exchange_code, ''), inserted_at) AS latest_exchange_code,
            argMax(ifNull(currency_code, ''), inserted_at) AS latest_currency_code
        FROM {target} FINAL
        GROUP BY symbol_id
    ),
    rows_to_insert AS
    (
        SELECT c.*
        FROM current_state AS c
        LEFT JOIN latest_fact AS f ON f.symbol_id = c.symbol_id
        WHERE f.symbol_id = ''
           OR f.symbol_id IS NULL
           OR f.latest_ibkr_conid != ifNull(c.ibkr_conid, '')
           OR f.latest_routing_status != c.routing_status
           OR f.latest_ambiguity_status != c.ambiguity_status
           OR f.latest_exchange_code != ifNull(c.exchange_code, '')
           OR f.latest_currency_code != ifNull(c.currency_code, '')
    )
SELECT
    concat('routing:ibkr:', symbol_id, ':', lower(hex(MD5(concat(source_event_id, ':', {sql_string(source_run_id)}))))) AS routing_fact_id,
    issuer_id,
    security_id,
    listing_id,
    symbol_id,
    provider_ticker,
    broker,
    ibkr_conid,
    contract_symbol,
    sec_type,
    currency_code,
    exchange_code,
    listing_exchange,
    routing_status,
    ambiguity_status,
    valid_from_utc,
    valid_to_utc,
    confidence_score,
    'reference_gateway' AS source_system,
    'id_listing_v1' AS source_table,
    source_event_id,
    source_evidence_ref,
    source_content_sha256,
    {sql_string(source_run_id)} AS source_run_id,
    inserted_at
FROM rows_to_insert
""".strip()
    )
    return count_run_rows(client, target_database, "security_routing_fact_v1", source_run_id)


def resolve_source_database(client: ClickHouseHttpClient, config: ReferenceGatewayConfig) -> str:
    if (
        table_exists(client, config.clickhouse_write_database, "feature_tradable_universe_v1")
        and scalar_int(client, f"SELECT count() FROM {table(config.clickhouse_write_database, 'feature_tradable_universe_v1')} FINAL LIMIT 1") > 0
    ):
        return config.clickhouse_write_database
    return config.clickhouse_read_database


def resolve_issue_database(client: ClickHouseHttpClient, config: ReferenceGatewayConfig) -> str:
    if table_exists(client, config.clickhouse_write_database, "id_mapping_issue_v1"):
        return config.clickhouse_write_database
    return config.clickhouse_read_database


def count_run_rows(client: ClickHouseHttpClient, database: str, table_name: str, source_run_id: str) -> int:
    text = client.query_tsv(
        f"""
        SELECT count()
        FROM {table(database, table_name)} FINAL
        WHERE source_run_id = {sql_string(source_run_id)}
        """
    ).strip()
    return int(text or "0")


def scalar_int(client: ClickHouseHttpClient, sql: str) -> int:
    text = client.query_tsv(sql).strip()
    return int(text or "0")


def skipped_result(config: ReferenceGatewayConfig, status: str, reason: str) -> FactFillResult:
    return FactFillResult(
        status="skipped",
        tradability_rows=0,
        routing_rows=0,
        reason=f"{status}:{reason}",
        source_database=config.clickhouse_read_database,
        issue_database=config.clickhouse_write_database,
        target_database=config.clickhouse_write_database,
        source_run_id="",
    )


def table(database: str, name: str) -> str:
    return f"{quote_ident(database)}.{quote_ident(name)}"
