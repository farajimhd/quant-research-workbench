"""Operator-owned, typed ARTE journal schema under construction.

This module deliberately does not install tables. Trading and Backtest runtime
principals must never have DDL authority. The tables below are the shared
envelope and execution foundation; they are not yet a complete recovery schema.
"""
from __future__ import annotations

from dataclasses import dataclass


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
            ("event_hash", "FixedString(64)"),
            ("execution_hash", "FixedString(64)"),
            ("commission_hash", "FixedString(64)"),
            ("committed_at", "DateTime64(6, 'UTC')"),
        ),
        "toYYYYMM(run_month)", "run_id, attempt_id, last_sequence, batch_id",
    ),
)


def schema_ddl() -> tuple[str, ...]:
    """Return DDL for a separately authorized installer, never run it here."""
    return tuple(table.ddl() for table in TABLES)
