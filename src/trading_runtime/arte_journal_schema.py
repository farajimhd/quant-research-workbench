"""Operator-owned, typed ARTE journal schema under construction.

This module deliberately does not install tables. Trading and Backtest runtime
principals must never have DDL authority. The tables below are the shared
envelope and execution foundation; they are not yet a complete recovery schema.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any


STORAGE_POLICY = "live_market_ssd"


@dataclass(frozen=True, slots=True)
class TableContract:
    name: str
    columns: tuple[tuple[str, str], ...]
    partition: str
    order: str

    def ddl(self) -> str:
        columns = ",\n    ".join(f"{name} {kind}" for name, kind in self.columns)
        return (
            f"CREATE TABLE IF NOT EXISTS arte.{self.name} (\n    {columns}\n) "
            f"ENGINE = MergeTree PARTITION BY {self.partition} "
            f"ORDER BY ({self.order}) SETTINGS storage_policy = '{STORAGE_POLICY}'"
        )


# All monetary and quantity fields use Decimal rather than Float64. The event
# envelope is intentionally payload-free: a record's detail belongs in its
# typed family table, keyed by record_id. Do not add a JSON escape hatch here.
TABLES = (
    TableContract(
        "trading_run_v1",
        (
            ("run_id", "String"),
            ("run_month", "Date"),
            ("mode", "LowCardinality(String)"),
            ("evaluation_interval_ms", "Nullable(UInt32)"),
            ("session_date", "Nullable(Date)"),
            ("configuration_hash", "FixedString(64)"),
            ("code_hash", "FixedString(64)"),
            ("market_plan_token", "String"),
            ("started_at", "DateTime64(6, 'UTC')"),
        ),
        "toYYYYMM(run_month)", "run_id",
    ),
    TableContract(
        "trading_event_v1",
        (
            ("run_id", "String"),
            ("event_month", "Date"),
            ("attempt_id", "UUID"),
            ("batch_id", "UUID"),
            ("record_id", "UUID"),
            ("sequence", "UInt64"),
            ("event_time", "DateTime64(9, 'UTC')"),
            ("recorded_at", "DateTime64(6, 'UTC')"),
            ("category", "LowCardinality(String)"),
            ("entity_type", "LowCardinality(String)"),
            ("entity_id", "String"),
            ("account_id", "String"),
            ("correlation_id", "String"),
            ("causation_id", "String"),
            ("content_hash", "FixedString(64)"),
        ),
        "toYYYYMM(event_month)", "run_id, attempt_id, sequence, record_id",
    ),
    TableContract(
        "trading_execution_v1",
        (
            ("record_id", "UUID"),
            ("run_id", "String"),
            ("event_month", "Date"),
            ("batch_id", "UUID"),
            ("account_id", "String"),
            ("execution_id", "String"),
            ("broker_order_id", "String"),
            ("client_order_id", "String"),
            ("conid", "UInt64"),
            ("ticker", "LowCardinality(String)"),
            ("side", "LowCardinality(String)"),
            ("quantity", "Decimal(38, 10)"),
            ("price", "Decimal(38, 10)"),
            ("exchange", "LowCardinality(String)"),
            ("source_event_time", "DateTime64(9, 'UTC')"),
            ("received_at", "DateTime64(6, 'UTC')"),
            ("strategy_id", "String"),
            ("strategy_revision", "UInt32"),
            ("setup", "String"),
            ("exit_reason", "String"),
            ("content_hash", "FixedString(64)"),
        ),
        "toYYYYMM(event_month)",
        "account_id, run_id, execution_id, source_event_time, record_id",
    ),
    TableContract(
        "trading_commission_v1",
        (
            ("record_id", "UUID"),
            ("run_id", "String"),
            ("event_month", "Date"),
            ("batch_id", "UUID"),
            ("account_id", "String"),
            ("execution_id", "String"),
            ("commission", "Decimal(38, 10)"),
            ("currency", "LowCardinality(String)"),
            ("status", "LowCardinality(String)"),
            ("realized_pnl", "Nullable(Decimal(38, 10))"),
            ("source_event_time", "DateTime64(9, 'UTC')"),
            ("received_at", "DateTime64(6, 'UTC')"),
            ("content_hash", "FixedString(64)"),
        ),
        "toYYYYMM(event_month)",
        "account_id, run_id, execution_id, source_event_time, record_id",
    ),
    TableContract(
        "trading_order_command_v1",
        (
            ("record_id", "UUID"),
            ("run_id", "String"),
            ("event_month", "Date"),
            ("batch_id", "UUID"),
            ("account_id", "String"),
            ("command_id", "String"),
            ("client_order_id", "String"),
            ("conid", "UInt64"),
            ("ticker", "LowCardinality(String)"),
            ("side", "LowCardinality(String)"),
            ("order_type", "LowCardinality(String)"),
            ("time_in_force", "LowCardinality(String)"),
            ("quantity", "Nullable(Decimal(38, 10))"),
            ("cash_quantity", "Nullable(Decimal(38, 10))"),
            ("limit_price", "Nullable(Decimal(38, 10))"),
            ("stop_price", "Nullable(Decimal(38, 10))"),
            ("outside_rth", "UInt8"),
            ("parent_command_id", "String"),
            ("oca_group", "String"),
            ("strategy_id", "String"),
            ("strategy_revision", "UInt32"),
            ("created_at", "DateTime64(9, 'UTC')"),
            ("content_hash", "FixedString(64)"),
        ),
        "toYYYYMM(event_month)", "account_id, run_id, command_id, record_id",
    ),
    TableContract(
        "trading_order_transition_v1",
        (
            ("record_id", "UUID"),
            ("run_id", "String"),
            ("event_month", "Date"),
            ("batch_id", "UUID"),
            ("account_id", "String"),
            ("command_id", "String"),
            ("broker_order_id", "String"),
            ("client_order_id", "String"),
            ("conid", "UInt64"),
            ("ticker", "LowCardinality(String)"),
            ("status", "LowCardinality(String)"),
            ("broker_status", "String"),
            ("total_quantity", "Decimal(38, 10)"),
            ("filled_quantity", "Decimal(38, 10)"),
            ("remaining_quantity", "Decimal(38, 10)"),
            ("average_fill_price", "Nullable(Decimal(38, 10))"),
            ("can_modify", "UInt8"),
            ("can_cancel", "UInt8"),
            ("terminal", "UInt8"),
            ("rejection_code", "String"),
            ("rejection_reason", "String"),
            ("source_event_time", "DateTime64(9, 'UTC')"),
            ("received_at", "DateTime64(6, 'UTC')"),
            ("content_hash", "FixedString(64)"),
        ),
        "toYYYYMM(event_month)",
        "account_id, run_id, broker_order_id, source_event_time, record_id",
    ),
    TableContract(
        "trading_account_snapshot_v1",
        (
            ("record_id", "UUID"),
            ("run_id", "String"),
            ("event_month", "Date"),
            ("batch_id", "UUID"),
            ("snapshot_id", "String"),
            ("account_id", "String"),
            ("currency", "LowCardinality(String)"),
            ("net_liquidation", "Decimal(38, 10)"),
            ("total_cash_value", "Decimal(38, 10)"),
            ("buying_power", "Decimal(38, 10)"),
            ("gross_position_value", "Decimal(38, 10)"),
            ("available_funds", "Decimal(38, 10)"),
            ("excess_liquidity", "Decimal(38, 10)"),
            ("snapshot_complete", "UInt8"),
            ("source_event_time", "DateTime64(9, 'UTC')"),
            ("content_hash", "FixedString(64)"),
        ),
        "toYYYYMM(event_month)", "account_id, run_id, source_event_time, snapshot_id",
    ),
    TableContract(
        "trading_position_snapshot_v1",
        (
            ("record_id", "UUID"),
            ("run_id", "String"),
            ("event_month", "Date"),
            ("batch_id", "UUID"),
            ("snapshot_id", "String"),
            ("account_id", "String"),
            ("conid", "UInt64"),
            ("ticker", "LowCardinality(String)"),
            ("currency", "LowCardinality(String)"),
            ("asset_class", "LowCardinality(String)"),
            ("quantity", "Decimal(38, 10)"),
            ("market_price", "Decimal(38, 10)"),
            ("market_value", "Decimal(38, 10)"),
            ("average_cost", "Decimal(38, 10)"),
            ("average_price", "Decimal(38, 10)"),
            ("realized_pnl", "Decimal(38, 10)"),
            ("unrealized_pnl", "Decimal(38, 10)"),
            ("source_event_time", "DateTime64(9, 'UTC')"),
            ("content_hash", "FixedString(64)"),
        ),
        "toYYYYMM(event_month)",
        "account_id, run_id, snapshot_id, conid, record_id",
    ),
    TableContract(
        "trading_commit_v1",
        (
            ("run_id", "String"),
            ("run_month", "Date"),
            ("attempt_id", "UUID"),
            ("batch_id", "UUID"),
            ("prior_batch_id", "UUID"),
            ("first_sequence", "UInt64"),
            ("last_sequence", "UInt64"),
            ("event_count", "UInt32"),
            ("execution_count", "UInt32"),
            ("commission_count", "UInt32"),
            ("order_command_count", "UInt32"),
            ("order_transition_count", "UInt32"),
            ("account_snapshot_count", "UInt32"),
            ("position_snapshot_count", "UInt32"),
            ("event_hash", "FixedString(64)"),
            ("execution_hash", "FixedString(64)"),
            ("commission_hash", "FixedString(64)"),
            ("order_command_hash", "FixedString(64)"),
            ("order_transition_hash", "FixedString(64)"),
            ("account_snapshot_hash", "FixedString(64)"),
            ("position_snapshot_hash", "FixedString(64)"),
            ("source_cursor", "String"),
            ("status", "LowCardinality(String)"),
            ("committed_at", "DateTime64(6, 'UTC')"),
        ),
        "toYYYYMM(run_month)", "run_id, attempt_id, last_sequence, batch_id",
    ),
)


def schema_ddl() -> tuple[str, ...]:
    """Return DDL for a separately authorized installer, never run it here."""
    return tuple(table.ddl() for table in TABLES)


def _rows(client: Any, query: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in client.execute(query).splitlines() if line.strip()]


def storage_preflight(client: Any) -> None:
    """Verify the exact typed schema and physical placement without writes."""
    policies = _rows(client,
        "SELECT disks FROM system.storage_policies "
        "WHERE policy_name='live_market_ssd' FORMAT JSONEachRow")
    if len(policies) != 1 or policies[0].get("disks") != [STORAGE_POLICY]:
        raise ValueError("Typed journal requires an SSD-only live_market_ssd policy")
    names = ",".join(f"'{table.name}'" for table in TABLES)
    actual_tables = _rows(client,
        "SELECT name,engine,storage_policy,partition_key,sorting_key FROM system.tables "
        f"WHERE database='arte' AND name IN ({names}) FORMAT JSONEachRow")
    by_name = {row["name"]: row for row in actual_tables}
    if set(by_name) != {table.name for table in TABLES}:
        raise ValueError("Typed journal tables are missing")
    for table in TABLES:
        row = by_name[table.name]
        if (row["engine"], row["storage_policy"], row["partition_key"],
                row["sorting_key"]) != (
                    "MergeTree", STORAGE_POLICY, table.partition, table.order):
            raise ValueError(f"Typed journal layout differs: {table.name}")
    actual_columns = _rows(client,
        "SELECT table,name,type FROM system.columns WHERE database='arte' "
        f"AND table IN ({names}) ORDER BY table,position FORMAT JSONEachRow")
    for table in TABLES:
        columns = tuple((row["name"], row["type"])
                        for row in actual_columns if row["table"] == table.name)
        if columns != table.columns:
            raise ValueError(f"Typed journal columns differ: {table.name}")
    bad_parts = _rows(client,
        "SELECT table,disk_name FROM system.parts WHERE database='arte' "
        f"AND table IN ({names}) AND active AND disk_name!='live_market_ssd' "
        "LIMIT 1 FORMAT JSONEachRow")
    if bad_parts:
        raise ValueError("Typed journal has active parts outside live_market_ssd")
