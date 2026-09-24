"""Operator-owned, typed ARTE journal schema under construction.

This module deliberately does not install tables. Trading and Backtest runtime
principals must never have DDL authority. The tables below are the shared
envelope and execution foundation; they are not yet a complete recovery schema.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any


STORAGE_POLICY = "live_market_ssd"
BATCH_LOOKUP_INDEX = "batch_id_bloom_v1"
MARKET_READ_TABLES = frozenset({
    "bars_v1", "indicators_v1", "liquidity_100ms_v1",
    "structural_level_coverage_v7", "structural_level_observations_v7",
    "structural_levels_v7",
})


@dataclass(frozen=True, slots=True)
class TableContract:
    name: str
    columns: tuple[tuple[str, str], ...]
    partition: str
    order: str

    def ddl(self) -> str:
        columns = ",\n    ".join(f"{name} {kind}" for name, kind in self.columns)
        if any(name == "batch_id" for name, _ in self.columns):
            columns += (f",\n    INDEX {BATCH_LOOKUP_INDEX} batch_id "
                        "TYPE bloom_filter(0.01) GRANULARITY 1")
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
        "trading_runtime_config_v1",
        (
            ("run_id", "String"),
            ("run_month", "Date"),
            ("strategy_id", "String"),
            ("strategy_revision", "UInt32"),
            ("anchor_date", "Date"),
            ("run_plan_id", "String"),
            ("safety_supervisor_enabled", "UInt8"),
            ("checkpoint_interval_events", "UInt32"),
            ("write_progress_checkpoints", "UInt8"),
            ("content_hash", "FixedString(64)"),
        ),
        "toYYYYMM(run_month)", "run_id",
    ),
    TableContract(
        "trading_run_account_v1",
        (
            ("run_id", "String"),
            ("run_month", "Date"),
            ("ordinal", "UInt16"),
            ("account_id", "String"),
            ("content_hash", "FixedString(64)"),
        ),
        "toYYYYMM(run_month)", "run_id, ordinal",
    ),
    TableContract(
        "trading_run_context_commit_v1",
        (
            ("run_id", "String"),
            ("run_month", "Date"),
            ("run_hash", "FixedString(64)"),
            ("config_hash", "FixedString(64)"),
            ("account_count", "UInt16"),
            ("account_hash", "FixedString(64)"),
            ("committed_at", "DateTime64(6, 'UTC')"),
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
        "trading_run_transition_v1",
        (
            ("record_id", "UUID"),
            ("run_id", "String"),
            ("event_month", "Date"),
            ("batch_id", "UUID"),
            ("account_id", "String"),
            ("status", "LowCardinality(String)"),
            ("processed_events", "Nullable(UInt64)"),
            ("source_event_time", "DateTime64(9, 'UTC')"),
            ("content_hash", "FixedString(64)"),
        ),
        "toYYYYMM(event_month)", "run_id, source_event_time, record_id",
    ),
    TableContract(
        "trading_operational_fault_v1",
        (
            ("record_id", "UUID"),
            ("run_id", "String"),
            ("event_month", "Date"),
            ("batch_id", "UUID"),
            ("account_id", "String"),
            ("status", "LowCardinality(String)"),
            ("error", "String"),
            ("entries_frozen", "UInt8"),
            ("source_event_time", "DateTime64(9, 'UTC')"),
            ("content_hash", "FixedString(64)"),
        ),
        "toYYYYMM(event_month)", "run_id, source_event_time, record_id",
    ),
    TableContract(
        "trading_account_risk_state_v1",
        (
            ("record_id", "UUID"),
            ("run_id", "String"),
            ("event_month", "Date"),
            ("batch_id", "UUID"),
            ("account_id", "String"),
            ("account_key", "String"),
            ("state", "LowCardinality(String)"),
            ("enforced", "UInt8"),
            ("net_liquidation", "Decimal(38, 18)"),
            ("available_funds", "Decimal(38, 18)"),
            ("buying_power", "Decimal(38, 18)"),
            ("gross_exposure", "Decimal(38, 18)"),
            ("net_exposure", "Decimal(38, 18)"),
            ("reserved_notional", "Decimal(38, 18)"),
            ("open_risk", "Decimal(38, 18)"),
            ("daily_loss", "Decimal(38, 18)"),
            ("drawdown", "Decimal(38, 18)"),
            ("position_count", "UInt32"),
            ("protection_required", "Decimal(38, 18)"),
            ("protection_coverage", "Decimal(38, 18)"),
            ("internal_reaction_ms", "Nullable(Decimal(38, 18))"),
            ("reason_count", "UInt16"),
            ("source_event_time", "DateTime64(9, 'UTC')"),
            ("content_hash", "FixedString(64)"),
        ),
        "toYYYYMM(event_month)", "run_id, account_id, source_event_time, record_id",
    ),
    TableContract(
        "trading_account_risk_reason_v1",
        (
            ("record_id", "UUID"),
            ("run_id", "String"),
            ("event_month", "Date"),
            ("batch_id", "UUID"),
            ("parent_record_id", "UUID"),
            ("ordinal", "UInt16"),
            ("reason", "String"),
            ("content_hash", "FixedString(64)"),
        ),
        "toYYYYMM(event_month)", "run_id, parent_record_id, ordinal, record_id",
    ),
    TableContract(
        "trading_strategy_signal_v1",
        (
            ("record_id", "UUID"),
            ("run_id", "String"),
            ("event_month", "Date"),
            ("batch_id", "UUID"),
            ("account_id", "String"),
            ("strategy_id", "String"),
            ("strategy_revision", "UInt32"),
            ("signal_id", "String"),
            ("signal_type", "String"),
            ("ticker", "LowCardinality(String)"),
            ("action", "LowCardinality(String)"),
            ("direction", "LowCardinality(String)"),
            ("score", "Decimal(38, 18)"),
            ("confidence", "Decimal(38, 18)"),
            ("reason", "String"),
            ("working_timeframe", "LowCardinality(String)"),
            ("invalidation_price", "Nullable(Decimal(38, 10))"),
            ("source_signal_count", "UInt16"),
            ("source_event_time", "DateTime64(9, 'UTC')"),
            ("content_hash", "FixedString(64)"),
        ),
        "toYYYYMM(event_month)",
        "run_id, strategy_id, ticker, source_event_time, record_id",
    ),
    TableContract(
        "trading_signal_source_v1",
        (
            ("record_id", "UUID"),
            ("run_id", "String"),
            ("event_month", "Date"),
            ("batch_id", "UUID"),
            ("parent_record_id", "UUID"),
            ("source_ordinal", "UInt16"),
            ("source_signal_id", "String"),
            ("content_hash", "FixedString(64)"),
        ),
        "toYYYYMM(event_month)",
        "run_id, parent_record_id, source_ordinal, record_id",
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
            ("currency", "LowCardinality(String)"),
            ("net_amount", "Nullable(Decimal(38, 10))"),
            ("cumulative_quantity", "Nullable(Decimal(38, 10))"),
            ("average_price", "Nullable(Decimal(38, 10))"),
            ("liquidity", "String"),
            ("liquidation_trade", "UInt8"),
            ("signal_price", "Nullable(Decimal(38, 10))"),
            ("arrival_midpoint", "Nullable(Decimal(38, 10))"),
            ("planned_risk", "Nullable(Decimal(38, 10))"),
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
            ("time_authority", "LowCardinality(String)"),
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
            ("aux_price", "Nullable(Decimal(38, 10))"),
            ("outside_rth", "UInt8"),
            ("parent_command_id", "String"),
            ("oca_group", "String"),
            ("strategy_id", "String"),
            ("strategy_revision", "UInt32"),
            ("created_at", "DateTime64(9, 'UTC')"),
            ("content_hash", "FixedString(64)"),
            ("security_type", "LowCardinality(String)"),
            ("listing_exchange", "LowCardinality(String)"),
            ("trailing_amount", "Nullable(Decimal(38, 10))"),
            ("trailing_type", "LowCardinality(String)"),
            ("single_group", "UInt8"),
            ("manual_indicator", "UInt8"),
            ("external_operator", "String"),
            ("referrer", "String"),
            ("broker_strategy", "String"),
            ("parent_broker_order_id", "String"),
        ),
        "toYYYYMM(event_month)", "account_id, run_id, command_id, record_id",
    ),
    TableContract(
        "trading_order_command_context_v1",
        (
            ("record_id", "UUID"), ("parent_record_id", "UUID"),
            ("run_id", "String"), ("event_month", "Date"),
            ("batch_id", "UUID"), ("account_id", "String"),
            ("strategy_intent_id", "String"), ("order_group_id", "String"),
            ("policy_version", "String"),
            ("content_hash", "FixedString(64)"),
        ),
        "toYYYYMM(event_month)",
        "run_id, account_id, parent_record_id, record_id",
    ),
    TableContract(
        "trading_strategy_intent_use_v1",
        (
            ("record_id", "UUID"), ("parent_record_id", "UUID"),
            ("run_id", "String"), ("event_month", "Date"),
            ("batch_id", "UUID"), ("account_id", "String"),
            ("intent_record_id", "UUID"),
            ("intent_content_hash", "FixedString(64)"),
            ("content_hash", "FixedString(64)"),
        ),
        "toYYYYMM(event_month)",
        "run_id, account_id, parent_record_id, record_id",
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
        "trading_oms_group_state_v1",
        (
            ("record_id", "UUID"), ("run_id", "String"),
            ("event_month", "Date"), ("batch_id", "UUID"),
            ("account_id", "String"), ("group_id", "String"),
            ("strategy_id", "String"), ("strategy_revision", "UInt32"),
            ("strategy_intent_id", "String"),
            ("state", "LowCardinality(String)"),
            ("created_at", "DateTime64(9, 'UTC')"),
            ("updated_at", "DateTime64(9, 'UTC')"),
            ("submitted_at", "Nullable(DateTime64(9, 'UTC'))"),
            ("rejection_reason", "String"),
            ("decision_to_submit_ms", "Nullable(Decimal(38, 10))"),
            ("reprice_count", "UInt32"),
            ("last_reprice_at", "Nullable(DateTime64(9, 'UTC'))"),
            ("failed_reprice_at", "Nullable(DateTime64(9, 'UTC'))"),
            ("internal_reaction_ms", "Nullable(Decimal(38, 10))"),
            ("deferred_reprice_from", "Nullable(Decimal(38, 10))"),
            ("deferred_reprice_to", "Nullable(Decimal(38, 10))"),
            ("high_water_price", "Decimal(38, 10)"),
            ("low_water_price", "Decimal(38, 10)"),
            ("cancel_strategy_protection", "UInt8"),
            ("protection_reconciliation_required", "UInt8"),
            ("filled_quantity", "Decimal(38, 10)"),
            ("remaining_quantity", "Decimal(38, 10)"),
            ("current_limit_price", "Nullable(Decimal(38, 10))"),
            ("protection_required_quantity", "Decimal(38, 10)"),
            ("protection_coverage_quantity", "Decimal(38, 10)"),
            ("protection_delegated", "UInt8"),
            ("order_count", "UInt16"), ("broker_binding_count", "UInt16"),
            ("warning_count", "UInt16"), ("cancel_oca_count", "UInt16"),
            ("content_hash", "FixedString(64)"),
        ),
        "toYYYYMM(event_month)", "run_id, account_id, group_id, updated_at, record_id",
    ),
    TableContract(
        "trading_oms_order_state_v1",
        (
            ("record_id", "UUID"), ("parent_record_id", "UUID"),
            ("run_id", "String"), ("event_month", "Date"),
            ("batch_id", "UUID"), ("account_id", "String"),
            ("ordinal", "UInt16"), ("batch_ordinal", "UInt16"),
            ("slice_id", "String"), ("client_order_id", "String"),
            ("parent_broker_order_id", "String"),
            ("conid", "UInt64"), ("ticker", "LowCardinality(String)"),
            ("security_type", "LowCardinality(String)"),
            ("listing_exchange", "LowCardinality(String)"),
            ("side", "LowCardinality(String)"),
            ("order_type", "LowCardinality(String)"),
            ("time_in_force", "LowCardinality(String)"),
            ("quantity", "Nullable(Decimal(38, 10))"),
            ("cash_quantity", "Nullable(Decimal(38, 10))"),
            ("limit_price", "Nullable(Decimal(38, 10))"),
            ("aux_price", "Nullable(Decimal(38, 10))"),
            ("trailing_amount", "Nullable(Decimal(38, 10))"),
            ("trailing_type", "String"),
            ("outside_rth", "UInt8"), ("single_group", "UInt8"),
            ("manual_indicator", "UInt8"),
            ("external_operator", "String"), ("referrer", "String"),
            ("broker_strategy", "String"),
            ("content_hash", "FixedString(64)"),
        ),
        "toYYYYMM(event_month)", "run_id, parent_record_id, ordinal, record_id",
    ),
    TableContract(
        "trading_oms_broker_binding_v1",
        (
            ("record_id", "UUID"), ("parent_record_id", "UUID"),
            ("run_id", "String"), ("event_month", "Date"),
            ("batch_id", "UUID"), ("account_id", "String"),
            ("ordinal", "UInt16"), ("broker_order_id", "String"),
            ("has_role", "UInt8"), ("role", "String"),
            ("has_slice", "UInt8"), ("slice_id", "String"),
            ("request_index", "Nullable(UInt16)"),
            ("has_filled_quantity", "UInt8"),
            ("filled_quantity", "Decimal(38, 10)"),
            ("terminal", "UInt8"),
            ("content_hash", "FixedString(64)"),
        ),
        "toYYYYMM(event_month)", "run_id, parent_record_id, ordinal, record_id",
    ),
    TableContract(
        "trading_oms_warning_v1",
        (
            ("record_id", "UUID"), ("parent_record_id", "UUID"),
            ("run_id", "String"), ("event_month", "Date"),
            ("batch_id", "UUID"), ("account_id", "String"),
            ("ordinal", "UInt16"), ("message_id", "String"),
            ("content_hash", "FixedString(64)"),
        ),
        "toYYYYMM(event_month)", "run_id, parent_record_id, ordinal, record_id",
    ),
    TableContract(
        "trading_oms_cancel_oca_v1",
        (
            ("record_id", "UUID"), ("parent_record_id", "UUID"),
            ("run_id", "String"), ("event_month", "Date"),
            ("batch_id", "UUID"), ("account_id", "String"),
            ("ordinal", "UInt16"), ("oca_group", "String"),
            ("content_hash", "FixedString(64)"),
        ),
        "toYYYYMM(event_month)", "run_id, parent_record_id, ordinal, record_id",
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
        "trading_strategy_intent_v1",
        (
            ("record_id", "UUID"), ("run_id", "String"), ("event_month", "Date"),
            ("batch_id", "UUID"), ("account_id", "String"),
            ("intent_id", "String"), ("ticker", "LowCardinality(String)"),
            ("action", "LowCardinality(String)"),
            ("quantity", "Decimal(38, 18)"),
            ("reference_price", "Decimal(38, 18)"),
            ("schema_version", "UInt16"),
            ("invalidation_price", "Nullable(Decimal(38, 18))"),
            ("profit_target_price", "Nullable(Decimal(38, 18))"),
            ("trailing_amount", "Nullable(Decimal(38, 18))"),
            ("urgency", "LowCardinality(String)"),
            ("time_in_force", "String"), ("outside_rth", "UInt8"),
            ("reason", "String"),
            ("capital_mode", "Nullable(String)"),
            ("capital_value", "Nullable(Decimal(38, 18))"),
            ("capital_minimum_quantity", "Nullable(Decimal(38, 18))"),
            ("capital_maximum_quantity", "Nullable(Decimal(38, 18))"),
            ("capital_allow_replacement", "Nullable(UInt8)"),
            ("execution_policy_id", "Nullable(String)"),
            ("execution_policy_revision", "Nullable(UInt32)"),
            ("execution_policy_name", "Nullable(String)"),
            ("execution_partial_fill_policy", "Nullable(String)"),
            ("execution_quote_source", "Nullable(String)"),
            ("execution_maximum_buy_price", "Nullable(Decimal(38, 18))"),
            ("execution_minimum_sell_price", "Nullable(Decimal(38, 18))"),
            ("execution_deadline_ms", "Nullable(UInt32)"),
            ("execution_maximum_reprices", "Nullable(UInt16)"),
            ("execution_minimum_reprice_interval_ms", "Nullable(UInt32)"),
            ("execution_persist_until_cancelled", "Nullable(UInt8)"),
            ("protection_profile_id", "Nullable(String)"),
            ("protection_profile_revision", "Nullable(UInt32)"),
            ("protection_add_policy", "Nullable(String)"),
            ("protection_profit_pocket_transition", "Nullable(String)"),
            ("protection_mandatory_catastrophic_backstop", "Nullable(UInt8)"),
            ("protection_emergency_repair_deadline_ms", "Nullable(UInt32)"),
            ("protection_slice_count", "UInt16"),
            ("content_hash", "FixedString(64)"),
        ),
        "toYYYYMM(event_month)", "run_id, account_id, intent_id, record_id",
    ),
    TableContract(
        "trading_intent_protection_slice_v1",
        (
            ("record_id", "UUID"), ("parent_record_id", "UUID"),
            ("run_id", "String"), ("event_month", "Date"),
            ("batch_id", "UUID"), ("account_id", "String"),
            ("ordinal", "UInt16"), ("slice_id", "String"),
            ("quantity_fraction", "Decimal(38, 18)"),
            ("profit_target_price", "Nullable(Decimal(38, 18))"),
            ("inherit_profit_target", "UInt8"),
            ("stop_rule_type", "LowCardinality(String)"),
            ("stop_order_type", "LowCardinality(String)"),
            ("stop_price", "Nullable(Decimal(38, 18))"),
            ("stop_distance_percent", "Nullable(Decimal(38, 18))"),
            ("stop_distance_bps", "Nullable(Decimal(38, 18))"),
            ("stop_maximum_cash_risk", "Nullable(Decimal(38, 18))"),
            ("stop_volatility_multiple", "Nullable(Decimal(38, 18))"),
            ("stop_buffer_bps", "Decimal(38, 18)"),
            ("stop_limit_offset_bps", "Nullable(Decimal(38, 18))"),
            ("anchor_observation_id", "Nullable(String)"),
            ("anchor_price", "Nullable(Decimal(38, 18))"),
            ("anchor_confirmed_at", "Nullable(DateTime64(9, 'UTC'))"),
            ("anchor_timeframe", "Nullable(String)"),
            ("anchor_ordinal", "Nullable(String)"),
            ("trailing_rule_type", "LowCardinality(String)"),
            ("trailing_amount", "Nullable(Decimal(38, 18))"),
            ("trailing_percent", "Nullable(Decimal(38, 18))"),
            ("trailing_volatility_multiple", "Nullable(Decimal(38, 18))"),
            ("trailing_activation_gain_percent", "Decimal(38, 18)"),
            ("trailing_breakeven_buffer_bps", "Decimal(38, 18)"),
            ("trailing_structural_timeframe", "String"),
            ("content_hash", "FixedString(64)"),
        ),
        "toYYYYMM(event_month)", "run_id, parent_record_id, ordinal, record_id",
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
            ("signal_count", "UInt32"),
            ("signal_source_count", "UInt32"),
            ("execution_count", "UInt32"),
            ("commission_count", "UInt32"),
            ("order_command_count", "UInt32"),
            ("order_transition_count", "UInt32"),
            ("account_snapshot_count", "UInt32"),
            ("position_snapshot_count", "UInt32"),
            ("event_hash", "FixedString(64)"),
            ("signal_hash", "FixedString(64)"),
            ("signal_source_hash", "FixedString(64)"),
            ("execution_hash", "FixedString(64)"),
            ("commission_hash", "FixedString(64)"),
            ("order_command_hash", "FixedString(64)"),
            ("order_transition_hash", "FixedString(64)"),
            ("account_snapshot_hash", "FixedString(64)"),
            ("position_snapshot_hash", "FixedString(64)"),
            ("intent_count", "UInt32"),
            ("intent_slice_count", "UInt32"),
            ("intent_hash", "FixedString(64)"),
            ("intent_slice_hash", "FixedString(64)"),
            ("order_context_count", "UInt32"),
            ("order_context_hash", "FixedString(64)"),
            ("oms_group_state_count", "UInt32"),
            ("oms_order_state_count", "UInt32"),
            ("oms_broker_binding_count", "UInt32"),
            ("oms_warning_count", "UInt32"),
            ("oms_cancel_oca_count", "UInt32"),
            ("oms_group_state_hash", "FixedString(64)"),
            ("oms_order_state_hash", "FixedString(64)"),
            ("oms_broker_binding_hash", "FixedString(64)"),
            ("oms_warning_hash", "FixedString(64)"),
            ("oms_cancel_oca_hash", "FixedString(64)"),
            ("intent_use_count", "UInt32"),
            ("intent_use_hash", "FixedString(64)"),
            ("run_transition_count", "UInt32"),
            ("run_transition_hash", "FixedString(64)"),
            ("operational_fault_count", "UInt32"),
            ("operational_fault_hash", "FixedString(64)"),
            ("account_risk_state_count", "UInt32"),
            ("account_risk_state_hash", "FixedString(64)"),
            ("account_risk_reason_count", "UInt32"),
            ("account_risk_reason_hash", "FixedString(64)"),
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


def batch_lookup_index_upgrade_ddl() -> tuple[str, ...]:
    """Operator-only, restart-safe index installation for global UUID readback."""
    names = (table.name for table in TABLES
             if any(column == "batch_id" for column, _ in table.columns))
    return tuple(
        f"ALTER TABLE arte.{name} ADD INDEX IF NOT EXISTS {BATCH_LOOKUP_INDEX} "
        "batch_id TYPE bloom_filter(0.01) GRANULARITY 1"
        for name in names
    )


def batch_lookup_index_materialize_ddl() -> tuple[str, ...]:
    """Build the new index for pre-existing parts after its metadata is installed."""
    return tuple(
        f"ALTER TABLE arte.{table.name} MATERIALIZE INDEX {BATCH_LOOKUP_INDEX}"
        for table in TABLES if "batch_id" in dict(table.columns)
    )


def intent_schema_upgrade_ddl() -> tuple[str, ...]:
    """Restart-safe operator DDL for the two intent families and old fences."""
    by_name = {table.name: table for table in TABLES}
    empty_hash = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    return (
        by_name["trading_strategy_intent_v1"].ddl(),
        by_name["trading_intent_protection_slice_v1"].ddl(),
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        "intent_count UInt32 DEFAULT 0 AFTER position_snapshot_hash",
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        "intent_slice_count UInt32 DEFAULT 0 AFTER intent_count",
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        f"intent_hash FixedString(64) DEFAULT '{empty_hash}' AFTER intent_slice_count",
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        f"intent_slice_hash FixedString(64) DEFAULT '{empty_hash}' AFTER intent_hash",
    )


def order_context_upgrade_ddl() -> tuple[str, ...]:
    """Add a typed command-to-strategy link without rewriting old commands."""
    by_name = {table.name: table for table in TABLES}
    empty_hash = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    return (
        by_name["trading_order_command_context_v1"].ddl(),
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        "order_context_count UInt32 DEFAULT 0 AFTER intent_slice_hash",
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        f"order_context_hash FixedString(64) DEFAULT '{empty_hash}' AFTER order_context_count",
    )


def oms_state_upgrade_ddl() -> tuple[str, ...]:
    """Operator-only additive DDL for normalized OMS recovery components."""
    by_name = {table.name: table for table in TABLES}
    empty_hash = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    return (
        *(by_name[name].ddl() for name in (
            "trading_oms_group_state_v1", "trading_oms_order_state_v1",
            "trading_oms_broker_binding_v1", "trading_oms_warning_v1",
            "trading_oms_cancel_oca_v1")),
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        "oms_group_state_count UInt32 DEFAULT 0 AFTER order_context_hash",
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        "oms_order_state_count UInt32 DEFAULT 0 AFTER oms_group_state_count",
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        "oms_broker_binding_count UInt32 DEFAULT 0 AFTER oms_order_state_count",
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        "oms_warning_count UInt32 DEFAULT 0 AFTER oms_broker_binding_count",
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        "oms_cancel_oca_count UInt32 DEFAULT 0 AFTER oms_warning_count",
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        f"oms_group_state_hash FixedString(64) DEFAULT '{empty_hash}' AFTER oms_cancel_oca_count",
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        f"oms_order_state_hash FixedString(64) DEFAULT '{empty_hash}' AFTER oms_group_state_hash",
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        f"oms_broker_binding_hash FixedString(64) DEFAULT '{empty_hash}' AFTER oms_order_state_hash",
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        f"oms_warning_hash FixedString(64) DEFAULT '{empty_hash}' AFTER oms_broker_binding_hash",
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        f"oms_cancel_oca_hash FixedString(64) DEFAULT '{empty_hash}' AFTER oms_warning_hash",
    )


def intent_use_upgrade_ddl() -> tuple[str, ...]:
    """Operator-only exact intent-revision links for command and OMS consumers."""
    by_name = {table.name: table for table in TABLES}
    empty_hash = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    return (
        by_name["trading_strategy_intent_use_v1"].ddl(),
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        "intent_use_count UInt32 DEFAULT 0 AFTER oms_cancel_oca_hash",
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        f"intent_use_hash FixedString(64) DEFAULT '{empty_hash}' AFTER intent_use_count",
    )


def run_transition_upgrade_ddl() -> tuple[str, ...]:
    """Operator-only typed lifecycle detail and additive fence fields."""
    by_name = {table.name: table for table in TABLES}
    empty_hash = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    return (
        by_name["trading_run_transition_v1"].ddl(),
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        "run_transition_count UInt32 DEFAULT 0 AFTER intent_use_hash",
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        f"run_transition_hash FixedString(64) DEFAULT '{empty_hash}' AFTER run_transition_count",
    )


def operational_fault_upgrade_ddl() -> tuple[str, ...]:
    """Operator-only typed broker/risk fault detail and additive fence fields."""
    by_name = {table.name: table for table in TABLES}
    empty_hash = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    return (
        by_name["trading_operational_fault_v1"].ddl(),
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        "operational_fault_count UInt32 DEFAULT 0 AFTER run_transition_hash",
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        f"operational_fault_hash FixedString(64) DEFAULT '{empty_hash}' AFTER operational_fault_count",
    )


def account_risk_upgrade_ddl() -> tuple[str, ...]:
    """Operator-only normalized account risk metrics and ordered reasons."""
    by_name = {table.name: table for table in TABLES}
    empty_hash = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    return (
        by_name["trading_account_risk_state_v1"].ddl(),
        by_name["trading_account_risk_reason_v1"].ddl(),
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        "account_risk_state_count UInt32 DEFAULT 0 AFTER operational_fault_hash",
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        f"account_risk_state_hash FixedString(64) DEFAULT '{empty_hash}' AFTER account_risk_state_count",
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        "account_risk_reason_count UInt32 DEFAULT 0 AFTER account_risk_state_hash",
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        f"account_risk_reason_hash FixedString(64) DEFAULT '{empty_hash}' AFTER account_risk_reason_count",
    )


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
    indexed = {table.name for table in TABLES
               if any(column == "batch_id" for column, _ in table.columns)}
    indexes = _rows(client,
        "SELECT table,name,type,expr,granularity FROM system.data_skipping_indices "
        f"WHERE database='arte' AND table IN ({names}) FORMAT JSONEachRow")
    if (len(indexes) != len(indexed)
            or {row["table"] for row in indexes} != indexed
            or any((row["name"], row["type"], row["expr"], int(row["granularity"]))
                   != (BATCH_LOOKUP_INDEX, "bloom_filter", "batch_id", 1)
                   for row in indexes)):
        raise ValueError("Typed journal batch lookup indexes are missing or incompatible")
    bad_parts = _rows(client,
        "SELECT table,disk_name FROM system.parts WHERE database='arte' "
        f"AND table IN ({names}) AND active AND disk_name!='live_market_ssd' "
        "LIMIT 1 FORMAT JSONEachRow")
    if bad_parts:
        raise ValueError("Typed journal has active parts outside live_market_ssd")


def journal_permission_preflight(client: Any) -> None:
    """Fail closed unless this principal can only read market and append journal."""
    journal = {table.name for table in TABLES}
    market = MARKET_READ_TABLES
    required = journal | market
    names = ",".join(f"'{name}'" for name in sorted(required))
    actual = _rows(client,
        "SELECT name FROM system.tables WHERE database='arte' "
        f"AND name IN ({names}) FORMAT JSONEachRow")
    if {str(row["name"]) for row in actual} != required:
        raise ValueError("Required journal or market tables are missing from permission audit")

    # An unrestricted catalog scan can block on unrelated tables with very
    # large part catalogs. Audit this principal's grant *surface* instead: a
    # new table must not silently become writable just because it was absent
    # from a startup snapshot. Unknown grant/role syntax fails closed.
    user = client.execute("SELECT currentUser()").strip()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", user) is None:
        raise ValueError("Journal principal has an unsafe identity")
    grant_lines = client.execute("SHOW GRANTS").splitlines()
    if not grant_lines:
        raise ValueError("Journal principal has no inspectable grants")
    for line in grant_lines:
        match = re.fullmatch(
            r"GRANT ([A-Z ,]+) ON ([A-Za-z_][A-Za-z0-9_]*|\*)\."
            r"([A-Za-z_][A-Za-z0-9_]*|\*) TO ([A-Za-z_][A-Za-z0-9_]*)",
            line.strip(),
        )
        if match is None or match.group(4) != user:
            raise ValueError("Journal principal has an unrecognized or delegated grant")
        privileges = {part.strip() for part in match.group(1).split(",")}
        database, table = match.group(2), match.group(3)
        if not privileges or "" in privileges:
            raise ValueError("Journal principal has an invalid grant")
        for privilege in privileges:
            if privilege == "INSERT" and database == "arte" and table in journal:
                continue
            if privilege == "SELECT" and ((database == "arte" and table in required)
                                          or (database == "system" and table in {
                                              "storage_policies", "tables", "columns", "parts",
                                              "data_skipping_indices"})):
                continue
            if privilege in {"SHOW DATABASES", "SHOW TABLES", "SHOW COLUMNS", "CHECK"}:
                continue
            raise ValueError(f"Journal principal has unauthorized {privilege} grant")

    def allowed(privilege: str, scope: str) -> bool:
        result = client.execute(f"CHECK GRANT {privilege} ON {scope}").strip()
        if result not in {"0", "1"}:
            raise ValueError("ClickHouse grant check returned an invalid result")
        return result == "1"

    for privilege in ("CREATE TABLE", "INSERT", "ALTER", "DROP TABLE", "TRUNCATE"):
        if allowed(privilege, "arte.*"):
            raise ValueError(f"Journal principal has broad arte {privilege} authority")
    readable = required
    for name in sorted(required):
        target = f"arte.{name}"
        if name in readable and not allowed("SELECT", target):
            raise ValueError(f"Journal principal cannot read {target}")
        if allowed("ALTER", target) or allowed("DROP TABLE", target) or allowed("TRUNCATE", target):
            raise ValueError(f"Journal principal may alter or remove {target}")
        if allowed("ALTER DELETE", target) or allowed("ALTER UPDATE", target):
            raise ValueError(f"Journal principal may mutate {target}")
        can_insert = allowed("INSERT", target)
        if can_insert != (name in journal):
            raise ValueError(f"Journal principal has incorrect insert authority on {target}")
