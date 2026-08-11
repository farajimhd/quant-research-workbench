from __future__ import annotations

from research.mlops.clickhouse import quote_ident, sql_string


QUERY_PLAN_ID = "market.schema_inventory.v1"
QUERY_PLAN_VERSION = 1
TIME_COLUMN_CANDIDATES = (
    "published_at_utc",
    "accepted_at_utc",
    "observed_at_utc",
    "source_timestamp_utc",
    "event_time",
    "sip_timestamp_utc",
    "timestamp_utc",
    "created_at_utc",
    "updated_at_utc",
    "started_at_utc",
    "coverage_start_utc",
    "last_started_at_utc",
    "updated_at",
    "source_archive_date",
    "filing_date",
    "trade_date",
    "universe_date",
    "coverage_start_date",
    "period_end_date",
    "list_date",
)


def table_inventory() -> str:
    """Bounded-to-current-database table inventory for configuration review."""
    return """
        SELECT
            database,
            name,
            engine,
            total_rows,
            total_bytes
        FROM system.tables
        WHERE database = currentDatabase()
        ORDER BY name
    """


def column_inventory() -> str:
    """Bounded-to-current-database column inventory for configuration review."""
    return """
        SELECT
            table,
            name,
            type,
            position
        FROM system.columns
        WHERE database = currentDatabase()
        ORDER BY table, position
    """


def schema_inventory_queries() -> tuple[str, str]:
    """Registered query-plan entrypoint exposing both catalog projections."""
    return table_inventory(), column_inventory()


def configured_table_preview(
    *, database: str, table: str, time_column: str = "", limit: int = 20
) -> str:
    """Preview one already-authorized configured service table."""
    safe_limit = max(1, min(int(limit), 100))
    order_clause = (
        f"\n        ORDER BY {quote_ident(time_column)} DESC" if time_column else ""
    )
    return f"""
        SELECT *
        FROM {quote_ident(database)}.{quote_ident(table)}
        {order_clause}
        LIMIT {safe_limit}
        FORMAT JSONEachRow
    """


def configured_table_stats(targets: list[dict[str, str]]) -> str:
    pairs = _target_pairs(targets)
    if not pairs:
        return ""
    return f"""
        SELECT
            t.database,
            t.name AS table,
            t.engine,
            toUInt64(ifNull(sum(p.rows), 0)) AS rows,
            toUInt64(ifNull(sum(p.bytes_on_disk), 0)) AS bytes_on_disk,
            ifNull(toString(max(p.modification_time)), '') AS latest_update
        FROM system.tables AS t
        LEFT JOIN system.parts AS p
            ON p.database = t.database
           AND p.table = t.name
           AND p.active
        WHERE (t.database, t.name) IN ({pairs})
        GROUP BY t.database, t.name, t.engine
        FORMAT TSV
    """


def configured_table_columns(targets: list[dict[str, str]]) -> str:
    pairs = _target_pairs(targets)
    if not pairs:
        return ""
    return f"""
        SELECT
            database,
            table,
            groupArray(name) AS names
        FROM system.columns
        WHERE (database, table) IN ({pairs})
        GROUP BY database, table
        FORMAT TSV
    """


def configured_table_count_buckets(
    targets: list[dict[str, str]],
    columns: dict[tuple[str, str], set[str]],
    *,
    years: tuple[int, ...],
) -> str:
    selects: list[str] = []
    for target in targets:
        key = (target["database"], target["table"])
        time_column = _time_column(columns.get(key, set()))
        if not time_column:
            continue
        date_expr = f"toDate({quote_ident(time_column)})"
        year_exprs = ",\n                ".join(
            f"toUInt64(countIf(toYear({date_expr}) = {year})) AS rows_{year}"
            for year in years
        )
        selects.append(
            f"""
            SELECT
                {sql_string(target['database'])} AS database,
                {sql_string(target['table'])} AS table,
                {sql_string(time_column)} AS time_column,
                toUInt64(countIf({date_expr} = today())) AS rows_today,
                toUInt64(countIf({date_expr} >= today() - 7)) AS rows_last_week,
                toUInt64(countIf({date_expr} >= today() - 30)) AS rows_last_month,
                {year_exprs}
            FROM {quote_ident(target['database'])}.{quote_ident(target['table'])}
            """
        )
    return "\nUNION ALL\n".join(selects) + ("\nFORMAT TSV" if selects else "")


def _target_pairs(targets: list[dict[str, str]]) -> str:
    return ", ".join(
        f"({sql_string(target['database'])}, {sql_string(target['table'])})"
        for target in targets
    )


def _time_column(columns: set[str]) -> str:
    for candidate in TIME_COLUMN_CANDIDATES:
        if candidate in columns:
            return candidate
    return ""
