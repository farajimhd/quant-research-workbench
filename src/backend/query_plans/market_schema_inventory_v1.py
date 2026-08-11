from __future__ import annotations


QUERY_PLAN_ID = "market.schema_inventory.v1"
QUERY_PLAN_VERSION = 1


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
