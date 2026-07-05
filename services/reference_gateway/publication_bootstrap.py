from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password, quote_ident, sql_string
from services.reference_gateway.config import ReferenceGatewayConfig
from services.reference_gateway.market_publications import insert_publication_coverage, table_exists


@dataclass(frozen=True, slots=True)
class PublicationCoverageBootstrapResult:
    status: str
    rows_written: int
    sources_checked: int
    sources_covered: int
    reason: str


BOOTSTRAP_SOURCES: tuple[dict[str, str], ...] = (
    {
        "coverage_kind": "massive_short_interest",
        "source_system": "massive",
        "source_object": "market_short_interest_v1",
        "table": "market_short_interest_v1",
        "date_column": "settlement_date",
    },
    {
        "coverage_kind": "reg_sho_threshold",
        "source_system": "sec",
        "source_object": "market_reg_sho_threshold_v1",
        "table": "market_reg_sho_threshold_v1",
        "date_column": "threshold_date",
    },
    {
        "coverage_kind": "massive_presentation_assets",
        "source_system": "massive",
        "source_object": "market_presentation_asset_v1",
        "table": "market_presentation_asset_v1",
        "date_column": "last_seen_at_utc",
    },
)


def bootstrap_existing_publication_coverage(config: ReferenceGatewayConfig, *, reason: str) -> PublicationCoverageBootstrapResult:
    if not config.execute:
        return PublicationCoverageBootstrapResult("skipped", 0, 0, 0, "execute_false:" + reason)
    client = ClickHouseHttpClient(config.clickhouse_url, config.clickhouse_user, default_clickhouse_password())
    run_id = "reference_gateway_publication_bootstrap_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    written = 0
    covered = 0
    for spec in BOOTSTRAP_SOURCES:
        try:
            if not table_exists(client, config.clickhouse_write_database, spec["table"]):
                continue
            if coverage_exists(client, config, spec["coverage_kind"], spec["source_system"]):
                continue
            if not column_exists(client, config.clickhouse_write_database, spec["table"], spec["date_column"]):
                continue
            summary = table_date_summary(client, config.clickhouse_write_database, spec["table"], spec["date_column"])
            if summary["rows"] <= 0 or summary["min_date"] is None or summary["max_date"] is None:
                continue
        except Exception:
            continue
        start_date = summary["min_date"]
        end_date = summary["max_date"] + timedelta(days=1)
        now = datetime.now(UTC)
        insert_publication_coverage(
            client,
            database=config.clickhouse_write_database,
            coverage_id=f"{run_id}:{spec['coverage_kind']}:{start_date.isoformat()}:{end_date.isoformat()}",
            coverage_kind=spec["coverage_kind"],
            source_system=spec["source_system"],
            source_object=spec["source_object"],
            start_date=start_date,
            end_date=end_date,
            status="bootstrap_trusted",
            rows_read=summary["rows"],
            rows_written=0,
            rows_failed=0,
            started_at_utc=now,
            finished_at_utc=now,
            details={"reason": reason, "table": spec["table"], "date_column": spec["date_column"]},
            source_run_id=run_id,
        )
        written += 1
        covered += 1
    return PublicationCoverageBootstrapResult("completed", written, len(BOOTSTRAP_SOURCES), covered, reason)


def coverage_exists(client: ClickHouseHttpClient, config: ReferenceGatewayConfig, coverage_kind: str, source_system: str) -> bool:
    if not table_exists(client, config.clickhouse_write_database, "market_reference_publication_coverage_v1"):
        return False
    value = client.query_tsv(
        f"""
        SELECT count()
        FROM {table(config.clickhouse_write_database, 'market_reference_publication_coverage_v1')} FINAL
        WHERE coverage_kind = {sql_string(coverage_kind)}
          AND source_system = {sql_string(source_system)}
          AND status IN ('completed', 'covered_empty', 'bootstrap_trusted')
        """
    ).strip()
    return int(value or "0") > 0


def column_exists(client: ClickHouseHttpClient, database: str, table_name: str, column_name: str) -> bool:
    value = client.query_tsv(
        f"""
        SELECT count()
        FROM system.columns
        WHERE database = {sql_string(database)}
          AND table = {sql_string(table_name)}
          AND name = {sql_string(column_name)}
        """
    ).strip()
    return int(value or "0") > 0


def table_date_summary(client: ClickHouseHttpClient, database: str, table_name: str, date_column: str) -> dict[str, Any]:
    rows = query_json_each_row(
        client,
        f"""
        SELECT
            count() AS rows,
            min(toDate({quote_ident(date_column)})) AS min_date,
            max(toDate({quote_ident(date_column)})) AS max_date
        FROM {table(database, table_name)} FINAL
        WHERE isNotNull({quote_ident(date_column)})
        """,
    )
    if not rows:
        return {"rows": 0, "min_date": None, "max_date": None}
    row = rows[0]
    return {
        "rows": int(row.get("rows") or 0),
        "min_date": parse_date(row.get("min_date")),
        "max_date": parse_date(row.get("max_date")),
    }


def query_json_each_row(client: ClickHouseHttpClient, sql: str) -> list[dict[str, Any]]:
    import json

    text = client.execute(sql.rstrip(";") + " FORMAT JSONEachRow").strip()
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def table(database: str, table_name: str) -> str:
    return f"{quote_ident(database)}.{quote_ident(table_name)}"
