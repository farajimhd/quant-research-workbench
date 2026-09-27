"""Operator-owned normalized execution-tactic journal contract.

An OMS group revision owns exactly one tactic-state row. ``has_tactic`` is
explicit so that an absent tactic cannot be confused with missing evidence.
Step rows belong to that revision, not to mutable group identity. Neither
Backtest nor live trading may install these tables.
"""
from __future__ import annotations

from .arte_journal_schema import TableContract


TACTIC = TableContract(
    "trading_oms_execution_tactic_v1",
    (
        ("record_id", "UUID"), ("parent_record_id", "UUID"),
        ("run_id", "String"), ("event_month", "Date"),
        ("batch_id", "UUID"), ("account_id", "String"),
        ("has_tactic", "UInt8"),
        ("urgency", "LowCardinality(String)"),
        ("side", "LowCardinality(String)"),
        ("quote_bid", "Nullable(Decimal(38, 10))"),
        ("quote_ask", "Nullable(Decimal(38, 10))"),
        ("quote_observed_at", "Nullable(DateTime64(9, 'UTC'))"),
        ("quote_tick_size", "Nullable(Decimal(38, 10))"),
        ("maximum_duration_ms", "Nullable(UInt32)"),
        ("step_count", "UInt16"),
        ("content_hash", "FixedString(64)"),
    ),
    "toYYYYMM(event_month)",
    "run_id, account_id, parent_record_id, record_id",
)

STEP = TableContract(
    "trading_oms_execution_step_v1",
    (
        ("record_id", "UUID"), ("parent_record_id", "UUID"),
        ("run_id", "String"), ("event_month", "Date"),
        ("batch_id", "UUID"), ("account_id", "String"),
        ("ordinal", "UInt16"), ("after_ms", "UInt32"),
        ("price", "Decimal(38, 10)"),
        ("content_hash", "FixedString(64)"),
    ),
    "toYYYYMM(event_month)",
    "run_id, parent_record_id, ordinal, record_id",
)

TABLES = (TACTIC, STEP)
