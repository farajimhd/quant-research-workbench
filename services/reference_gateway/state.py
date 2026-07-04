from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from research.mlops.clickhouse import ClickHouseHttpClient, sql_string
from services.reference_gateway.market_publications import IMPLEMENTED_PUBLICATION_COVERAGE_KINDS, PLANNED_PUBLICATION_TABLES, table
from services.reference_gateway.table_groups import REFERENCE_TABLE_GROUPS


@dataclass(frozen=True, slots=True)
class ReferenceSourceState:
    source: str
    status: str
    coverage: str
    rows: int | None
    targets: str
    note: str


@dataclass(frozen=True, slots=True)
class ReferenceTableDetail:
    table_name: str
    status: str
    rows: int
    latest_update: str


@dataclass(frozen=True, slots=True)
class ReferenceTableState:
    group_id: str
    status: str
    tables_present: int
    tables_total: int
    rows: int
    latest_update: str
    details: tuple[ReferenceTableDetail, ...]


SOURCE_TARGETS: dict[str, tuple[str, str, str]] = {
    "finra_short_volume:CNMS": ("FINRA short volume", "market_short_volume_v1", "daily CNMS short-volume publication"),
    "finra_short_interest": ("FINRA short interest", "market_short_interest_v1", "short-interest publication coverage"),
    "sec_fails_to_deliver": ("SEC fails-to-deliver", "market_fails_to_deliver_v1", "SEC FTD settlement publication"),
    "reg_sho_threshold": ("Reg SHO threshold", "market_reg_sho_threshold_v1", "SEC/Nasdaq threshold security publication coverage"),
    "massive_splits": ("Massive splits", "market_stock_split_v1", "split corporate actions"),
    "massive_dividends": ("Massive dividends", "market_cash_dividend_v1", "cash dividend corporate actions"),
    "massive_ipos": ("Massive IPOs", "market_ipo_v1", "IPO publication rows"),
    "massive_presentation_assets": ("Massive presentation assets", "market_presentation_asset_v1", "presentation asset inventory coverage"),
    "massive_flatfile_inventory": ("Massive flatfile inventory", "massive_flatfile_source_file_v1", "Massive flatfile source-file inventory coverage"),
    "massive_ticker_details": ("Massive ticker details", "market_security_market_snapshot_v1, market_security_float_v1", "current-state market snapshot and shares"),
    "ibkr_borrow_availability": ("IBKR borrow snapshot", "market_security_borrow_v1", "broker shortability and borrow availability"),
    "sec_country_assertions": ("Country assertions", "market_security_country_v1", "canonical exchange/listing country assertions"),
}


def collect_reference_state(client: ClickHouseHttpClient, *, database: str) -> tuple[list[ReferenceSourceState], list[ReferenceTableState]]:
    return collect_source_states(client, database=database), collect_table_states(client, database=database)


def collect_source_states(client: ClickHouseHttpClient, *, database: str) -> list[ReferenceSourceState]:
    coverage_rows = latest_publication_coverage(client, database=database)
    states: list[ReferenceSourceState] = []
    today = datetime.now(UTC).date()
    for coverage_kind in IMPLEMENTED_PUBLICATION_COVERAGE_KINDS:
        label, targets, note = SOURCE_TARGETS.get(coverage_kind, (coverage_kind, "-", "implemented publication source"))
        row = coverage_rows.get(coverage_kind)
        if row is None:
            states.append(ReferenceSourceState(label, "missing", "-", None, targets, "no coverage row yet"))
            continue
        coverage_end = parse_date(row.get("max_end"))
        rows_written = int(row.get("rows_written") or 0)
        rows_failed = int(row.get("rows_failed") or 0)
        latest_status = str(row.get("latest_status") or "")
        status = coverage_status(latest_status, coverage_end, today, rows_failed)
        coverage = coverage_text(row.get("min_start"), row.get("max_end"), row.get("windows"))
        detail = f"latest={latest_status or '-'}"
        if rows_failed:
            detail += f"; historical_failed_attempts={rows_failed:,}"
        states.append(ReferenceSourceState(label, status, coverage, rows_written, targets, f"{note}; {detail}"))

    for planned_table in sorted(PLANNED_PUBLICATION_TABLES):
        states.append(ReferenceSourceState(planned_source_label(planned_table), "planned", "-", None, planned_table, "schema planned; writer not enabled yet"))
    return states


def collect_table_states(client: ClickHouseHttpClient, *, database: str) -> list[ReferenceTableState]:
    all_tables = sorted({table_name for group in REFERENCE_TABLE_GROUPS for table_name in group.tables})
    if not all_tables:
        return []
    existing = existing_tables(client, database=database, table_names=all_tables)
    stats = active_part_stats(client, database=database, table_names=all_tables)
    output: list[ReferenceTableState] = []
    for group in REFERENCE_TABLE_GROUPS:
        present = [name for name in group.tables if name in existing]
        group_rows = sum(int(stats.get(name, {}).get("rows") or 0) for name in group.tables)
        latest = latest_update_text([stats.get(name, {}).get("latest_update") for name in group.tables])
        details = table_details(group.tables, existing, stats)
        status = table_group_status(
            group_id=group.group_id,
            tables_present=len(present),
            tables_total=len(group.tables),
            rows=group_rows,
        )
        output.append(
            ReferenceTableState(
                group.group_id,
                status,
                len(present),
                len(group.tables),
                group_rows,
                latest,
                details,
            )
        )
    return output


def latest_publication_coverage(client: ClickHouseHttpClient, *, database: str) -> dict[str, dict[str, Any]]:
    if not table_exists(client, database, "market_reference_publication_coverage_v1"):
        return {}
    kinds = ", ".join(sql_string(kind) for kind in IMPLEMENTED_PUBLICATION_COVERAGE_KINDS)
    sql = f"""
    SELECT
        coverage_kind,
        min(coverage_start_date) AS min_start,
        max(coverage_end_date) AS max_end,
        uniqExact(coverage_id) AS windows,
        sum(rows_written) AS rows_written,
        sum(rows_failed) AS rows_failed,
        argMax(status, inserted_at) AS latest_status,
        max(inserted_at) AS latest_update
    FROM {table(database, 'market_reference_publication_coverage_v1')} FINAL
    WHERE coverage_kind IN ({kinds})
    GROUP BY coverage_kind
    FORMAT JSONEachRow
    """
    text = client.execute(sql).strip()
    return {str(row["coverage_kind"]): row for row in parse_json_lines(text)}


def existing_tables(client: ClickHouseHttpClient, *, database: str, table_names: list[str]) -> set[str]:
    names = ", ".join(sql_string(name) for name in table_names)
    sql = f"""
    SELECT name
    FROM system.tables
    WHERE database = {sql_string(database)}
      AND name IN ({names})
    FORMAT JSONEachRow
    """
    text = client.execute(sql).strip()
    return {str(row["name"]) for row in parse_json_lines(text)}


def active_part_stats(client: ClickHouseHttpClient, *, database: str, table_names: list[str]) -> dict[str, dict[str, Any]]:
    names = ", ".join(sql_string(name) for name in table_names)
    sql = f"""
    SELECT
        table,
        sum(rows) AS rows,
        max(modification_time) AS latest_update
    FROM system.parts
    WHERE database = {sql_string(database)}
      AND active
      AND table IN ({names})
    GROUP BY table
    FORMAT JSONEachRow
    """
    text = client.execute(sql).strip()
    return {str(row["table"]): row for row in parse_json_lines(text)}


def table_exists(client: ClickHouseHttpClient, database: str, table_name: str) -> bool:
    value = client.execute(
        "SELECT count() FROM system.tables "
        f"WHERE database = {sql_string(database)} AND name = {sql_string(table_name)} FORMAT TSV"
    ).strip()
    return int(value or "0") > 0


def coverage_status(latest_status: str, coverage_end: date | None, today: date, rows_failed: int) -> str:
    lowered = latest_status.lower()
    if lowered in {"failed", "error"}:
        return "failed"
    if lowered in {"source_not_historical", "source_not_yet_available"}:
        return "planned"
    if coverage_end is None:
        return "missing"
    if rows_failed:
        return "warning"
    if coverage_end < today:
        return "stale"
    return "ok"


def table_group_status(*, group_id: str, tables_present: int, tables_total: int, rows: int) -> str:
    if tables_present < tables_total:
        return "missing"
    if rows == 0:
        return "empty" if group_id == "canonical_security_facts" else "warning"
    if group_id == "canonical_security_facts":
        return "partial"
    return "ok"


def table_details(table_names: tuple[str, ...], existing: set[str], stats: dict[str, dict[str, Any]]) -> tuple[ReferenceTableDetail, ...]:
    details: list[ReferenceTableDetail] = []
    for name in table_names:
        rows = int(stats.get(name, {}).get("rows") or 0)
        if name not in existing:
            status = "missing"
        elif rows == 0:
            status = "empty"
        else:
            status = "ok"
        details.append(
            ReferenceTableDetail(
                short_table_name(name),
                status,
                rows,
                latest_update_text([stats.get(name, {}).get("latest_update")]),
            )
        )
    return tuple(details)


def latest_update_text(values: list[Any]) -> str:
    clean = [str(value) for value in values if value not in {None, "", "0000-00-00 00:00:00"}]
    return max(clean) if clean else "-"


def coverage_text(start: Any, end: Any, windows: Any) -> str:
    if not start or not end:
        return "-"
    return f"{str(start)[:10]}->{str(end)[:10]} ({int(windows or 0):,})"


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def parse_json_lines(text: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def planned_source_label(table_name: str) -> str:
    labels = {
        "market_short_interest_v1": "FINRA short interest",
        "market_presentation_asset_v1": "Massive presentation assets",
        "massive_flatfile_source_file_v1": "Massive flatfile inventory",
        "market_reg_sho_threshold_v1": "Reg SHO threshold",
        "market_security_country_v1": "SEC country assertions",
    }
    return labels.get(table_name, table_name)


def short_table_name(table_name: str) -> str:
    for suffix in ("_v1", "_v2"):
        if table_name.endswith(suffix):
            table_name = table_name[: -len(suffix)]
            break
    replacements = {
        "market_security_market_snapshot": "snapshot",
        "market_reference_publication_coverage": "coverage",
        "feature_tradable_universe": "tradable",
        "feature_scanner_static": "scanner",
        "market_reference_alert_consumer_state": "consumer_state",
        "issuer_fundamental_metric_fact": "fundamental",
    }
    return replacements.get(table_name, table_name)
