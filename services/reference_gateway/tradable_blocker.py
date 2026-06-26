from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password, quote_ident, sql_string
from services.reference_gateway.config import ReferenceGatewayConfig
from services.reference_gateway.market_publications import table_exists


@dataclass(frozen=True, slots=True)
class TradabilityBlockResult:
    status: str
    rows_blocked: int
    table: str
    reason: str


def block_latest_universe_for_open_issues(config: ReferenceGatewayConfig, *, reason: str) -> TradabilityBlockResult:
    client = ClickHouseHttpClient(config.clickhouse_url, config.clickhouse_user, default_clickhouse_password())
    target = table(config.clickhouse_write_database, "feature_tradable_universe_v1")
    if not table_exists(client, config.clickhouse_write_database, "feature_tradable_universe_v1"):
        return TradabilityBlockResult("skipped", 0, target, "target_feature_tradable_universe_missing")
    if not table_exists(client, config.clickhouse_write_database, "id_mapping_issue_v1"):
        return TradabilityBlockResult("skipped", 0, target, "target_issue_table_missing")
    count = count_blockable_rows(client, config)
    if count == 0:
        return TradabilityBlockResult("completed", 0, target, "no_latest_tradable_rows_touched_by_open_issues")
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    source_run_id = "reference_gateway_immediate_block_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    client.execute(
        f"""
        INSERT INTO {target}
        (universe_date, symbol_id, listing_id, security_id, issuer_id, ticker, exchange_code, currency_code, ibkr_conid, massive_ticker, product_type, asset_class, listing_status, symbol_status, is_tradable, exclusion_reason, source_run_id, inserted_at)
        WITH
        latest_date AS
        (
            SELECT max(universe_date) AS universe_date
            FROM {target} FINAL
        ),
        open_issue_keys AS
        (
            SELECT DISTINCT source_entity_key
            FROM {table(config.clickhouse_write_database, 'id_mapping_issue_v1')} FINAL
            WHERE lower(issue_status) NOT IN ('resolved', 'closed', 'ignored')
              AND source_entity_key != ''
        )
        SELECT
            universe_date,
            symbol_id,
            listing_id,
            security_id,
            issuer_id,
            ticker,
            exchange_code,
            currency_code,
            ibkr_conid,
            massive_ticker,
            product_type,
            asset_class,
            listing_status,
            symbol_status,
            0 AS is_tradable,
            'open_mapping_issue' AS exclusion_reason,
            {sql_string(source_run_id)} AS source_run_id,
            toDateTime64({sql_string(now)}, 3, 'UTC') AS inserted_at
        FROM {target} FINAL
        WHERE universe_date = (SELECT universe_date FROM latest_date)
          AND is_tradable = 1
          AND (
              symbol_id IN (SELECT source_entity_key FROM open_issue_keys)
              OR listing_id IN (SELECT source_entity_key FROM open_issue_keys)
              OR security_id IN (SELECT source_entity_key FROM open_issue_keys)
              OR issuer_id IN (SELECT source_entity_key FROM open_issue_keys)
              OR upper(ticker) IN (SELECT upper(source_entity_key) FROM open_issue_keys)
          )
        """
    )
    return TradabilityBlockResult("completed", count, target, reason)


def count_blockable_rows(client: ClickHouseHttpClient, config: ReferenceGatewayConfig) -> int:
    text = client.query_tsv(
        f"""
        WITH
        latest_date AS
        (
            SELECT max(universe_date) AS universe_date
            FROM {table(config.clickhouse_write_database, 'feature_tradable_universe_v1')} FINAL
        ),
        open_issue_keys AS
        (
            SELECT DISTINCT source_entity_key
            FROM {table(config.clickhouse_write_database, 'id_mapping_issue_v1')} FINAL
            WHERE lower(issue_status) NOT IN ('resolved', 'closed', 'ignored')
              AND source_entity_key != ''
        )
        SELECT count()
        FROM {table(config.clickhouse_write_database, 'feature_tradable_universe_v1')} FINAL
        WHERE universe_date = (SELECT universe_date FROM latest_date)
          AND is_tradable = 1
          AND (
              symbol_id IN (SELECT source_entity_key FROM open_issue_keys)
              OR listing_id IN (SELECT source_entity_key FROM open_issue_keys)
              OR security_id IN (SELECT source_entity_key FROM open_issue_keys)
              OR issuer_id IN (SELECT source_entity_key FROM open_issue_keys)
              OR upper(ticker) IN (SELECT upper(source_entity_key) FROM open_issue_keys)
          )
        """
    ).strip()
    return int(text or "0")


def table(database: str, name: str) -> str:
    return f"{quote_ident(database)}.{quote_ident(name)}"

