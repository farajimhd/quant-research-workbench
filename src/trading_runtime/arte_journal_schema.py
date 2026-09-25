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
POLICY_NUMERIC_FIELDS = (
    "eligible_equity_fraction", "minimum_cash_reserve", "entry_fee_buffer_bps",
    "maximum_buying_power_utilization", "maximum_gross_exposure",
    "maximum_net_long_exposure", "maximum_net_short_exposure",
    "maximum_position_fraction", "maximum_ticker_fraction",
    "maximum_strategy_fraction", "maximum_sector_fraction",
    "maximum_industry_fraction", "maximum_correlated_group_fraction",
    "maximum_planned_risk_fraction", "maximum_open_risk_fraction",
    "maximum_order_quantity", "maximum_order_notional", "maximum_daily_loss",
    "maximum_drawdown", "daily_loss_warning", "emergency_loss",
)
POLICY_INTEGER_FIELDS = (
    "maximum_open_positions", "maximum_snapshot_age_ms",
    "maximum_protection_slices", "maximum_internal_reaction_ms",
)
POLICY_BOOLEAN_FIELDS = (
    "allow_long", "allow_short", "allow_margin", "allow_unsettled_cash",
    "allow_outside_rth", "allow_overnight", "block_on_unattributed_position",
    "allow_stop_limit_protection", "allow_partial_profit_pocket",
    "allow_emergency_auto_liquidation",
)
POLICY_ALLOWED_FIELDS = (
    "allowed_security_types", "allowed_currencies", "restricted_symbols",
    "allowed_execution_policies", "allowed_protection_profiles",
)
POLICY_ALLOWED_TABLES = {
    "allowed_security_types": ("trading_portfolio_policy_security_type_v1", "security_type"),
    "allowed_currencies": ("trading_portfolio_policy_currency_v1", "currency"),
    "restricted_symbols": ("trading_portfolio_policy_restricted_symbol_v1", "symbol"),
    "allowed_execution_policies": ("trading_portfolio_policy_execution_policy_v1", "execution_policy"),
    "allowed_protection_profiles": ("trading_portfolio_policy_protection_profile_v1", "protection_profile"),
}


@dataclass(frozen=True, slots=True)
class TableContract:
    name: str
    columns: tuple[tuple[str, str], ...]
    partition: str
    order: str

    def __post_init__(self) -> None:
        # Journal evidence must have an explicit relational contract. Reject
        # catchall payloads at definition time, not only in a schema test.
        forbidden_types = re.compile(
            r"\b(?:JSON|Object|Dynamic|Variant|Array|Map|Tuple|AggregateFunction)\s*(?:\(|$)",
            re.IGNORECASE,
        )
        forbidden_names = re.compile(r"(?:^|_)(?:json|blob|payload|raw_bytes)(?:_|$)", re.IGNORECASE)
        if len({name for name, _ in self.columns}) != len(self.columns):
            raise ValueError(f"Duplicate journal columns: {self.name}")
        for name, kind in self.columns:
            if forbidden_names.search(name) or forbidden_types.search(kind):
                raise ValueError(f"Journal column requires a normalized typed contract: {self.name}.{name}")

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
        "trading_portfolio_snapshot_v1",
        (("run_id", "String"), ("snapshot_month", "Date"),
         ("account_id", "String"), ("state_revision", "UInt64"),
         ("account_key", "String"), ("control_mode", "LowCardinality(String)"),
         ("sync_state", "LowCardinality(String)"), ("snapshot_id", "String"),
         ("snapshot_at", "Nullable(DateTime64(6, 'UTC'))"),
         ("observed_at", "Nullable(DateTime64(6, 'UTC'))"),
         ("stale_reason", "String"),
         ("peak_net_liquidation", "Decimal(38, 18)"),
         ("realized_pnl_baseline", "Nullable(Decimal(38, 18))"),
         ("selected_policy_hash", "Nullable(FixedString(64))")),
        "toYYYYMM(snapshot_month)", "run_id, account_id, state_revision",
    ),
    TableContract(
        "trading_portfolio_disabled_strategy_v1",
        (("run_id", "String"), ("snapshot_month", "Date"),
         ("account_id", "String"), ("state_revision", "UInt64"),
         ("strategy_id", "String")),
        "toYYYYMM(snapshot_month)", "run_id, account_id, state_revision, strategy_id",
    ),
    TableContract(
        "trading_portfolio_command_v1",
        (("run_id", "String"), ("snapshot_month", "Date"),
         ("account_id", "String"), ("state_revision", "UInt64"),
         ("ordinal", "UInt16"), ("command_id", "String"),
         ("command", "LowCardinality(String)"), ("reason", "String"),
         ("status", "LowCardinality(String)"), ("error", "Nullable(String)"),
         ("completed_at", "Nullable(DateTime64(6, 'UTC'))")),
        "toYYYYMM(snapshot_month)", "run_id, account_id, state_revision, ordinal",
    ),
    TableContract(
        "trading_portfolio_request_v1",
        (("run_id", "String"), ("snapshot_month", "Date"),
         ("account_id", "String"), ("state_revision", "UInt64"),
         ("request_id", "String"), ("ticker", "String"),
         ("assignment_id", "String"),
         ("requested_at", "DateTime64(6, 'UTC')"),
         ("last_validated_at", "DateTime64(6, 'UTC')")),
        "toYYYYMM(snapshot_month)", "run_id, account_id, state_revision, request_id",
    ),
    TableContract(
        "trading_portfolio_request_reason_v1",
        (("run_id", "String"), ("snapshot_month", "Date"),
         ("account_id", "String"), ("state_revision", "UInt64"),
         ("request_id", "String"), ("ordinal", "UInt16"), ("reason", "String")),
        "toYYYYMM(snapshot_month)",
        "run_id, account_id, state_revision, request_id, ordinal",
    ),
    TableContract(
        "trading_portfolio_reservation_v1",
        (("run_id", "String"), ("snapshot_month", "Date"),
         ("account_id", "String"), ("state_revision", "UInt64"),
         ("reservation_id", "String"), ("decision_id", "String"),
         ("intent_id", "String"), ("account_key", "String"),
         ("strategy_id", "String"), ("assignment_id", "String"),
         ("ticker", "String"), ("action", "LowCardinality(String)"),
         ("quantity", "Decimal(38, 18)"),
         ("remaining_quantity", "Decimal(38, 18)"),
         ("reference_price", "Decimal(38, 18)"),
         ("reserved_notional", "Decimal(38, 18)"),
         ("reserved_planned_risk", "Decimal(38, 18)"),
         ("created_at", "DateTime64(6, 'UTC')"),
         ("status", "LowCardinality(String)"),
         ("filled_quantity", "Decimal(38, 18)"),
         ("admission_epoch", "UInt64"), ("admission_owner", "String"),
         ("reserved_entry_fees", "Decimal(38, 18)"),
         ("cash_tranche_key", "String"),
         ("cash_tranche_size", "Decimal(38, 18)"),
         ("cash_tranche_count", "UInt32"), ("cash_tranche_next", "UInt32"),
         ("cash_tranche_budget", "Decimal(38, 18)")),
        "toYYYYMM(snapshot_month)",
        "run_id, account_id, state_revision, reservation_id",
    ),
    TableContract(
        "trading_portfolio_allocation_v1",
        (("run_id", "String"), ("snapshot_month", "Date"),
         ("account_id", "String"), ("state_revision", "UInt64"),
         ("allocation_id", "String"), ("account_key", "String"),
         ("strategy_id", "String"), ("strategy_revision", "UInt32"),
         ("assignment_id", "String"), ("ticker", "String"),
         ("quantity", "Decimal(38, 18)"),
         ("average_price", "Decimal(38, 18)"),
         ("planned_risk", "Decimal(38, 18)"),
         ("realized_pnl", "Decimal(38, 18)"),
         ("source", "LowCardinality(String)"),
         ("updated_at", "DateTime64(6, 'UTC')")),
        "toYYYYMM(snapshot_month)",
        "run_id, account_id, state_revision, allocation_id",
    ),
    TableContract(
        "trading_portfolio_reconciliation_v1",
        (("run_id", "String"), ("snapshot_month", "Date"),
         ("account_id", "String"), ("state_revision", "UInt64"),
         ("account_key", "String"), ("ticker", "String"),
         ("broker_quantity", "Decimal(38, 18)"),
         ("attributed_quantity", "Decimal(38, 18)"),
         ("unattributed_quantity", "Decimal(38, 18)"),
         ("observed_at", "DateTime64(6, 'UTC')")),
        "toYYYYMM(snapshot_month)",
        "run_id, account_id, state_revision, ticker",
    ),
    TableContract(
        "trading_portfolio_snapshot_commit_v1",
        (("run_id", "String"), ("snapshot_month", "Date"),
         ("account_id", "String"), ("state_revision", "UInt64"),
         ("state_hash", "FixedString(64)"),
         ("disabled_strategy_count", "UInt32"), ("command_count", "UInt32"),
         ("request_count", "UInt32"), ("request_reason_count", "UInt32"),
         ("reservation_count", "UInt32"), ("allocation_count", "UInt32"),
         ("reconciliation_count", "UInt32"),
         ("committed_at", "DateTime64(6, 'UTC')")),
        "toYYYYMM(snapshot_month)", "run_id, account_id, state_revision",
    ),
    TableContract(
        "trading_admission_fence_v1",
        (("run_id", "String"), ("admission_month", "Date"),
         ("account_id", "String"), ("state_revision", "UInt64"),
         ("attempt_id", "UUID"), ("batch_id", "UUID"),
         ("first_sequence", "UInt64"), ("last_sequence", "UInt64"),
         ("phase", "LowCardinality(String)"),
         ("snapshot_hash", "Nullable(FixedString(64))"),
         ("captured_at", "DateTime64(6, 'UTC')"),
         ("content_hash", "FixedString(64)")),
        "toYYYYMM(admission_month)",
        "run_id, account_id, state_revision, phase",
    ),
    TableContract(
        "trading_portfolio_sync_fence_v1",
        (("run_id", "String"), ("sync_month", "Date"),
         ("account_id", "String"), ("state_revision", "UInt64"),
         ("attempt_id", "UUID"), ("batch_id", "UUID"),
         ("first_sequence", "UInt64"), ("last_sequence", "UInt64"),
         ("snapshot_hash", "FixedString(64)"),
         ("difference_hash", "FixedString(64)"),
         ("captured_at", "DateTime64(6, 'UTC')"),
         ("content_hash", "FixedString(64)")),
        "toYYYYMM(sync_month)", "run_id, account_id, state_revision",
    ),
    TableContract(
        "trading_portfolio_sync_snapshot_marker_v1",
        (("run_id", "String"), ("sync_month", "Date"),
         ("account_id", "String"), ("state_revision", "UInt64"),
         ("batch_id", "UUID"), ("snapshot_id", "String"),
         ("captured_at", "DateTime64(6, 'UTC')"),
         ("content_hash", "FixedString(64)")),
        "toYYYYMM(sync_month)", "run_id, account_id, state_revision",
    ),
    TableContract(
        "trading_backtest_snapshot_anchor_v1",
        (("run_id", "String"), ("anchor_month", "Date"),
         ("account_id", "String"), ("state_revision", "UInt64"),
         ("batch_id", "UUID"), ("last_sequence", "UInt64"),
         ("snapshot_hash", "FixedString(64)"),
         ("anchored_at", "DateTime64(6, 'UTC')"),
         ("content_hash", "FixedString(64)")),
        "toYYYYMM(anchor_month)", "run_id, account_id, last_sequence",
    ),
    TableContract(
        "trading_portfolio_policy_v1",
        (("policy_hash", "FixedString(64)"), ("policy_id", "String"),
         ("revision", "UInt32"))
        + tuple((name, "Decimal(38, 18)") for name in POLICY_NUMERIC_FIELDS)
        + tuple((name, "UInt32") for name in POLICY_INTEGER_FIELDS)
        + tuple((name, "UInt8") for name in POLICY_BOOLEAN_FIELDS),
        "cityHash64(policy_hash) % 32", "policy_hash",
    ),
    *(TableContract(
        table, (("policy_hash", "FixedString(64)"), ("ordinal", "UInt16"),
                (column, "String")),
        "cityHash64(policy_hash) % 32", "policy_hash, ordinal",
    ) for table, column in POLICY_ALLOWED_TABLES.values()),
    TableContract(
        "trading_portfolio_policy_commit_v2",
        (("policy_hash", "FixedString(64)"), ("allowed_count", "UInt32"),
         ("allowed_hash", "FixedString(64)"),
         ("committed_at", "DateTime64(6, 'UTC')")),
        "cityHash64(policy_hash) % 32", "policy_hash",
    ),
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
        "trading_strategy_assignment_command_v1",
        (
            ("record_id", "UUID"), ("run_id", "String"),
            ("event_month", "Date"), ("batch_id", "UUID"),
            ("account_id", "String"), ("assignment_id", "String"),
            ("strategy_id", "String"), ("strategy_revision", "UInt32"),
            ("ticker", "LowCardinality(String)"),
            ("command", "LowCardinality(String)"),
            ("status", "LowCardinality(String)"),
            ("updated_at", "DateTime64(6, 'UTC')"),
            ("source_event_time", "DateTime64(9, 'UTC')"),
            ("content_hash", "FixedString(64)"),
        ),
        "toYYYYMM(event_month)", "run_id, assignment_id, record_id",
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
            ("evidence_node_count", "UInt32"),
            ("decision_assignment_id", "Nullable(String)"),
            ("decision_reference_price", "Nullable(Decimal(38, 10))"),
            ("decision_status", "Nullable(String)"),
            ("decision_reason_detail", "Nullable(String)"),
            ("source_event_time", "DateTime64(9, 'UTC')"),
            ("content_hash", "FixedString(64)"),
        ),
        "toYYYYMM(event_month)",
        "run_id, strategy_id, ticker, source_event_time, record_id",
    ),
    TableContract(
        "trading_strategy_signal_evidence_node_v1",
        (
            ("record_id", "UUID"), ("run_id", "String"),
            ("event_month", "Date"), ("batch_id", "UUID"),
            ("parent_record_id", "UUID"), ("parent_node_id", "Nullable(UUID)"),
            ("ordinal", "UInt32"), ("map_key", "Nullable(String)"),
            ("value_kind", "LowCardinality(String)"),
            ("value_text", "Nullable(String)"), ("value_int", "Nullable(Int64)"),
            ("value_float", "Nullable(Float64)"), ("value_bool", "Nullable(UInt8)"),
            ("content_hash", "FixedString(64)"),
        ),
        "toYYYYMM(event_month)",
        "run_id, parent_record_id, parent_node_id, ordinal, record_id",
    ),
    TableContract(
        "trading_intent_decision_v1",
        (
            ("record_id", "UUID"), ("run_id", "String"),
            ("event_month", "Date"), ("batch_id", "UUID"),
            ("account_id", "String"), ("intent_id", "String"),
            ("ticker", "LowCardinality(String)"),
            ("decision_kind", "LowCardinality(String)"),
            ("action", "LowCardinality(String)"),
            ("reason_code", "LowCardinality(String)"),
            ("reason_detail", "String"),
            ("reference_price", "Nullable(Decimal(38, 10))"),
            ("strategy_id", "String"), ("strategy_revision", "UInt32"),
            ("assignment_status", "String"), ("reason_count", "UInt16"),
            ("source_event_time", "DateTime64(9, 'UTC')"),
            ("content_hash", "FixedString(64)"),
        ),
        "toYYYYMM(event_month)",
        "run_id, account_id, ticker, source_event_time, record_id",
    ),
    TableContract(
        "trading_intent_decision_reason_v1",
        (
            ("record_id", "UUID"), ("run_id", "String"),
            ("event_month", "Date"), ("batch_id", "UUID"),
            ("parent_record_id", "UUID"), ("account_id", "String"),
            ("ordinal", "UInt16"), ("reason", "String"),
            ("content_hash", "FixedString(64)"),
        ),
        "toYYYYMM(event_month)",
        "run_id, parent_record_id, ordinal, record_id",
    ),
    TableContract(
        "trading_portfolio_decision_v1",
        (("record_id", "UUID"), ("run_id", "String"), ("event_month", "Date"),
         ("batch_id", "UUID"), ("account_id", "String"), ("decision_id", "String"),
         ("request_id", "String"), ("account_key", "String"),
         ("ticker", "String"), ("action", "String"), ("policy_id", "String"),
         ("policy_revision", "UInt32"), ("snapshot_id", "String"),
         ("status", "LowCardinality(String)"),
         ("requested_quantity", "Decimal(38, 18)"),
         ("approved_quantity", "Decimal(38, 18)"),
         ("approved_notional", "Decimal(38, 18)"),
         ("planned_loss", "Decimal(38, 18)"), ("reservation_id", "String"),
         ("reason_count", "UInt16"), ("decided_at", "DateTime64(6, 'UTC')"),
         *((f"{phase}_{metric}", "Decimal(38, 18)")
           for phase in ("before", "after") for metric in (
               "net_liquidation", "available_funds", "buying_power", "gross_exposure",
               "net_exposure", "reserved_notional", "open_risk", "daily_loss",
               "drawdown", "position_count")),
         ("content_hash", "FixedString(64)")),
        "toYYYYMM(event_month)", "run_id, account_id, decided_at, record_id",
    ),
    TableContract(
        "trading_portfolio_decision_reason_v1",
        (("record_id", "UUID"), ("run_id", "String"), ("event_month", "Date"),
         ("batch_id", "UUID"), ("parent_record_id", "UUID"),
         ("account_id", "String"), ("ordinal", "UInt16"),
         ("reason", "String"), ("content_hash", "FixedString(64)")),
        "toYYYYMM(event_month)", "run_id, parent_record_id, ordinal, record_id",
    ),
    TableContract(
        "trading_portfolio_reservation_event_v1",
        (("record_id", "UUID"), ("run_id", "String"), ("event_month", "Date"),
         ("batch_id", "UUID"), ("account_id", "String"),
         ("reservation_id", "String"), ("event", "String"),
         ("decision_id", "String"), ("intent_id", "String"),
         ("account_key", "String"), ("strategy_id", "String"),
         ("assignment_id", "String"), ("ticker", "String"), ("action", "String"),
         ("quantity", "Decimal(38, 18)"),
         ("remaining_quantity", "Decimal(38, 18)"),
         ("reference_price", "Decimal(38, 18)"),
         ("reserved_notional", "Decimal(38, 18)"),
         ("reserved_planned_risk", "Decimal(38, 18)"),
         ("created_at", "DateTime64(6, 'UTC')"), ("status", "String"),
         ("filled_quantity", "Decimal(38, 18)"), ("admission_epoch", "UInt64"),
         ("admission_owner", "String"),
         ("reserved_entry_fees", "Decimal(38, 18)"),
         ("cash_tranche_key", "String"),
         ("cash_tranche_size", "Decimal(38, 18)"),
         ("cash_tranche_count", "UInt32"), ("cash_tranche_next", "UInt32"),
         ("cash_tranche_budget", "Decimal(38, 18)"),
         ("content_hash", "FixedString(64)")),
        "toYYYYMM(event_month)", "run_id, account_id, reservation_id, record_id",
    ),
    TableContract(
        "trading_portfolio_reconciliation_event_v1",
        (("record_id", "UUID"), ("run_id", "String"), ("event_month", "Date"),
         ("batch_id", "UUID"), ("account_id", "String"),
         ("account_key", "String"), ("snapshot_id", "String"),
         ("difference_count", "UInt32"),
         ("difference_hash", "FixedString(64)"),
         ("source_event_time", "DateTime64(9, 'UTC')"),
         ("content_hash", "FixedString(64)")),
        "toYYYYMM(event_month)", "run_id, account_id, source_event_time, record_id",
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
        "trading_backtest_market_authority_v1",
        (("record_id", "UUID"), ("run_id", "String"),
         ("event_month", "Date"), ("batch_id", "UUID"),
         ("account_id", "String"),
         ("execution_plan_token", "FixedString(64)"),
         ("parent_market_plan_token", "FixedString(64)"),
         ("content_hash", "FixedString(64)")),
        "toYYYYMM(event_month)", "run_id, event_month, record_id",
    ),
    TableContract(
        "trading_backtest_cursor_v1",
        (("record_id", "UUID"), ("run_id", "String"),
         ("event_month", "Date"), ("batch_id", "UUID"),
         ("account_id", "String"), ("session_date", "Date"),
         ("boundary_ms", "UInt32"), ("market_sequence", "UInt64"),
         ("frame_as_of", "Nullable(DateTime64(6, 'UTC'))"),
         ("frame_ticker", "Nullable(String)"),
         ("frame_timeframe", "Nullable(String)"),
         ("frame_sequence", "Nullable(UInt64)"),
         ("content_hash", "FixedString(64)")),
        "toYYYYMM(event_month)", "run_id, event_month, record_id",
    ),
    TableContract(
        "trading_backtest_progress_v1",
        (("record_id", "UUID"), ("run_id", "String"),
         ("event_month", "Date"), ("batch_id", "UUID"),
         ("parent_record_id", "UUID"),
         ("controller_time", "DateTime64(9, 'UTC')"),
         ("controller_processed_events", "UInt64"),
         ("controller_warmup_events", "UInt64"),
         ("controller_processed_frames", "UInt64"),
         ("runtime_processed_events", "UInt64"),
         ("runtime_last_event_time", "Nullable(DateTime64(9, 'UTC'))"),
         ("content_hash", "FixedString(64)")),
        "toYYYYMM(event_month)", "run_id, parent_record_id, record_id",
    ),
    TableContract(
        "trading_prepared_v7_lease_v1",
        (("record_id", "UUID"), ("run_id", "String"),
         ("event_month", "Date"), ("batch_id", "UUID"),
         ("account_id", "String"), ("stream_id", "String"),
         ("owner_pid", "UInt32"),
         ("phase", "LowCardinality(String)"),
         ("error_type", "Nullable(String)"),
         ("source_event_time", "DateTime64(9, 'UTC')"),
         ("lease_recorded_at", "DateTime64(9, 'UTC')"),
         ("content_hash", "FixedString(64)")),
        "toYYYYMM(event_month)", "run_id, stream_id, source_event_time, record_id",
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
            ("assignment_command_count", "UInt32"),
            ("signal_count", "UInt32"),
            ("signal_evidence_node_count", "UInt32"),
            ("signal_source_count", "UInt32"),
            ("execution_count", "UInt32"),
            ("commission_count", "UInt32"),
            ("order_command_count", "UInt32"),
            ("order_transition_count", "UInt32"),
            ("account_snapshot_count", "UInt32"),
            ("position_snapshot_count", "UInt32"),
            ("event_hash", "FixedString(64)"),
            ("assignment_command_hash", "FixedString(64)"),
            ("signal_hash", "FixedString(64)"),
            ("signal_evidence_node_hash", "FixedString(64)"),
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
            ("backtest_cursor_count", "UInt32"),
            ("backtest_cursor_hash", "FixedString(64)"),
            ("backtest_market_authority_count", "UInt32"),
            ("backtest_market_authority_hash", "FixedString(64)"),
            ("backtest_progress_count", "UInt32"),
            ("backtest_progress_hash", "FixedString(64)"),
            ("prepared_v7_lease_count", "UInt32"),
            ("prepared_v7_lease_hash", "FixedString(64)"),
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
            ("intent_decision_count", "UInt32"),
            ("intent_decision_hash", "FixedString(64)"),
            ("intent_decision_reason_count", "UInt32"),
            ("intent_decision_reason_hash", "FixedString(64)"),
            ("portfolio_decision_count", "UInt32"),
            ("portfolio_decision_hash", "FixedString(64)"),
            ("portfolio_decision_reason_count", "UInt32"),
            ("portfolio_decision_reason_hash", "FixedString(64)"),
            ("portfolio_reservation_event_count", "UInt32"),
            ("portfolio_reservation_event_hash", "FixedString(64)"),
            ("portfolio_reconciliation_event_count", "UInt32"),
            ("portfolio_reconciliation_event_hash", "FixedString(64)"),
            ("source_cursor", "String"),
            ("status", "LowCardinality(String)"),
            ("committed_at", "DateTime64(6, 'UTC')"),
        ),
        "toYYYYMM(run_month)", "run_id, attempt_id, last_sequence, batch_id",
    ),
)

# Activation watch-set authority is normalized separately from execution
# events, but belongs to the same operator-installed journal surface.
ACTIVATION_TABLES = (
    TableContract("trading_activation_v1", (
        ("run_id", "String"), ("session_date", "Date"), ("run_plan_id", "String"),
        ("ticker", "String"), ("event_id", "String"),
        ("delivery_id", "String"), ("profile_id", "String"),
        ("book_id", "String"), ("signal_stream_id", "String"),
        ("event_time", "DateTime64(6, 'UTC')"),
        ("evidence_count", "UInt32"), ("field_evidence_count", "UInt32"),
        ("content_hash", "FixedString(64)"),
    ), "toYYYYMM(session_date)", "run_id, session_date, run_plan_id, ticker, event_id"),
    TableContract("trading_activation_evidence_v1", (
        ("run_id", "String"), ("session_date", "Date"), ("run_plan_id", "String"),
        ("ticker", "String"), ("event_id", "String"),
        ("field_key", "String"), ("value_kind", "LowCardinality(String)"),
        ("value_text", "Nullable(String)"), ("value_int", "Nullable(Int64)"),
        ("value_float", "Nullable(Float64)"),
        ("value_decimal", "Nullable(Decimal(38, 18))"),
        ("value_bool", "Nullable(UInt8)"), ("content_hash", "FixedString(64)"),
    ), "toYYYYMM(session_date)", "run_id, session_date, run_plan_id, ticker, event_id, field_key"),
    TableContract("trading_activation_field_evidence_v1", (
        ("run_id", "String"), ("session_date", "Date"), ("run_plan_id", "String"),
        ("ticker", "String"), ("event_id", "String"),
        ("field_key", "String"), ("field_ref", "String"),
        ("interval", "String"), ("aggregation", "String"),
        ("available_at", "Nullable(DateTime64(6, 'UTC'))"),
        ("null_reason", "Nullable(String)"),
        ("value_kind", "LowCardinality(String)"),
        ("value_text", "Nullable(String)"), ("value_int", "Nullable(Int64)"),
        ("value_float", "Nullable(Float64)"),
        ("value_decimal", "Nullable(Decimal(38, 18))"),
        ("value_bool", "Nullable(UInt8)"), ("content_hash", "FixedString(64)"),
    ), "toYYYYMM(session_date)", "run_id, session_date, run_plan_id, ticker, event_id, field_key"),
    TableContract("trading_activation_commit_v1", (
        ("run_id", "String"), ("session_date", "Date"), ("run_plan_id", "String"),
        ("ticker", "String"), ("event_id", "String"),
        ("parent_hash", "FixedString(64)"),
        ("evidence_hash", "FixedString(64)"),
        ("field_evidence_hash", "FixedString(64)"),
        ("committed_at", "DateTime64(6, 'UTC')"),
        ("content_hash", "FixedString(64)"),
    ), "toYYYYMM(session_date)", "run_id, session_date, run_plan_id, ticker, event_id"),
)
TABLES += ACTIVATION_TABLES


# Operator-only future fixed-Backtest terminal evidence. Keep these outside
# active TABLES until the grouped projector, writer, and cold reader are wired.
BACKTEST_TERMINAL_SNAPSHOT_V2_TABLES = (
    TableContract(
        "trading_backtest_account_snapshot_v2",
        (
            ("record_id", "UUID"), ("run_id", "String"),
            ("event_month", "Date"), ("batch_id", "UUID"),
            ("snapshot_id", "UUID"),
            ("account_id", "String"), ("currency", "String"),
            ("source_timestamp_ms", "UInt64"),
            ("net_liquidation", "Float64"),
            ("total_cash_value", "Float64"),
            ("buying_power", "Float64"),
            ("gross_position_value", "Float64"),
            ("available_funds", "Float64"),
            ("excess_liquidity", "Float64"),
            ("expected_position_count", "UInt32"),
            ("position_set_sha256", "FixedString(64)"),
            ("content_hash", "FixedString(64)"),
        ),
        "toYYYYMM(event_month)", "run_id, account_id, record_id",
    ),
    TableContract(
        "trading_backtest_position_snapshot_v2",
        (
            ("record_id", "UUID"), ("parent_snapshot_id", "UUID"),
            ("run_id", "String"), ("event_month", "Date"),
            ("batch_id", "UUID"), ("account_id", "String"),
            ("ordinal", "UInt32"), ("conid", "UInt64"),
            ("ticker", "String"), ("currency", "String"),
            ("asset_class", "String"),
            ("quantity", "Float64"), ("market_price", "Float64"),
            ("market_value", "Float64"), ("average_cost", "Float64"),
            ("average_price", "Float64"), ("realized_pnl", "Float64"),
            ("unrealized_pnl", "Float64"),
            ("content_hash", "FixedString(64)"),
        ),
        "toYYYYMM(event_month)",
        "run_id, parent_snapshot_id, ordinal, record_id",
    ),
    TableContract(
        "trading_backtest_terminal_commit_v2",
        (
            ("run_id", "String"), ("run_month", "Date"),
            ("attempt_id", "UUID"), ("batch_id", "UUID"),
            ("prior_v2_batch_id", "UUID"),
            ("prior_v2_sequence", "UInt64"),
            ("first_sequence", "UInt64"), ("last_sequence", "UInt64"),
            ("source_cursor", "String"),
            ("status", "LowCardinality(String)"),
            ("event_count", "UInt32"),
            ("event_hash", "FixedString(64)"),
            ("run_transition_count", "UInt32"),
            ("run_transition_hash", "FixedString(64)"),
            ("account_count", "UInt32"),
            ("account_hash", "FixedString(64)"),
            ("position_count", "UInt32"),
            ("position_hash", "FixedString(64)"),
            ("committed_at", "DateTime64(6, 'UTC')"),
            ("content_hash", "FixedString(64)"),
        ),
        "toYYYYMM(run_month)", "run_id, attempt_id, last_sequence, batch_id",
    ),
)

# Operator-only cutover for occupied, older V1 facts. Never ALTER the occupied
# V1 signal or commit tables: added defaults would change the canonical hash
# input of pre-existing rows. The V2 contract is the complete current typed
# shape, deliberately staged outside active TABLES/startup validation.
_JOURNAL_V1_BY_NAME = {table.name: table for table in TABLES}
VERSIONED_JOURNAL_V2_TABLES = tuple(
    TableContract(
        v2_name, _JOURNAL_V1_BY_NAME[v1_name].columns,
        _JOURNAL_V1_BY_NAME[v1_name].partition,
        _JOURNAL_V1_BY_NAME[v1_name].order,
    )
    for v1_name, v2_name in (
        ("trading_strategy_signal_v1", "trading_strategy_signal_v2"),
        ("trading_commit_v1", "trading_commit_v2"),
    )
)

# Exact occupied V1 signal shape from the pre-cursor contract at 8764dbc8,
# independently confirmed against deployed system.columns. This is read-only;
# it must not replace the active newer V1 contract or generate upgrade DDL.
LEGACY_STRATEGY_SIGNAL_V1 = TableContract(
    "trading_strategy_signal_v1",
    tuple((name, kind) for name, kind in
          _JOURNAL_V1_BY_NAME["trading_strategy_signal_v1"].columns
          if name not in {
              "evidence_node_count", "decision_assignment_id",
              "decision_reference_price", "decision_status",
              "decision_reason_detail",
          }),
    _JOURNAL_V1_BY_NAME["trading_strategy_signal_v1"].partition,
    _JOURNAL_V1_BY_NAME["trading_strategy_signal_v1"].order,
)
_LEGACY_COMMIT_NAMES = (
    "run_id", "run_month", "attempt_id", "batch_id", "prior_batch_id",
    "first_sequence", "last_sequence", "event_count", "signal_count",
    "signal_source_count", "execution_count", "commission_count",
    "order_command_count", "order_transition_count", "account_snapshot_count",
    "position_snapshot_count", "event_hash", "signal_hash",
    "signal_source_hash", "execution_hash", "commission_hash",
    "order_command_hash", "order_transition_hash", "account_snapshot_hash",
    "position_snapshot_hash", "intent_count", "intent_slice_count",
    "intent_hash", "intent_slice_hash", "order_context_count",
    "order_context_hash", "oms_group_state_count", "oms_order_state_count",
    "oms_broker_binding_count", "oms_warning_count", "oms_cancel_oca_count",
    "oms_group_state_hash", "oms_order_state_hash", "oms_broker_binding_hash",
    "oms_warning_hash", "oms_cancel_oca_hash", "intent_use_count",
    "intent_use_hash", "run_transition_count", "run_transition_hash",
    "operational_fault_count", "operational_fault_hash",
    "account_risk_state_count", "account_risk_state_hash",
    "account_risk_reason_count", "account_risk_reason_hash",
    "intent_decision_count", "intent_decision_hash",
    "intent_decision_reason_count", "intent_decision_reason_hash",
    "source_cursor", "status", "committed_at",
)
_CURRENT_COMMIT_TYPES = dict(_JOURNAL_V1_BY_NAME["trading_commit_v1"].columns)
LEGACY_COMMIT_V1 = TableContract(
    "trading_commit_v1",
    tuple((name, _CURRENT_COMMIT_TYPES[name]) for name in _LEGACY_COMMIT_NAMES),
    _JOURNAL_V1_BY_NAME["trading_commit_v1"].partition,
    _JOURNAL_V1_BY_NAME["trading_commit_v1"].order,
)


def versioned_journal_v2_ddl() -> tuple[str, ...]:
    """Staged replacement DDL only; active V1 schemas and hashes stay fixed."""
    return tuple(table.ddl() for table in VERSIONED_JOURNAL_V2_TABLES)


def versioned_journal_v2_contracts() -> tuple[TableContract, ...]:
    """Exact occupied V1, active shared, and replacement V2 table shapes."""
    replaced = {"trading_strategy_signal_v1", "trading_commit_v1"}
    active = tuple(table for table in TABLES if table.name not in replaced)
    return active + (LEGACY_STRATEGY_SIGNAL_V1, LEGACY_COMMIT_V1) + VERSIONED_JOURNAL_V2_TABLES


def versioned_journal_v2_preflight(client: Any) -> None:
    """Opt-in audit of the occupied V1 and replacement V2 writer layout.

    Legacy V1 facts remain readable but must not be writable under this
    principal; all other journal families retain their active contracts.
    """
    replaced = {"trading_strategy_signal_v1", "trading_commit_v1"}
    contracts = versioned_journal_v2_contracts()
    storage_preflight(client, tables=contracts)
    journal_permission_preflight(
        client,
        journal_tables=frozenset(table.name for table in contracts
                                 if table.name not in replaced),
        read_only_tables=frozenset(replaced),
    )


def fixed_backtest_v2_contracts() -> tuple[TableContract, ...]:
    """Exact occupied V1, active shared, and new fixed-V2 table shapes."""
    from src.trading_runtime.arte_backtest_definition import TABLES as DEFINITION_TABLES

    legacy = {"trading_strategy_signal_v1", "trading_commit_v1"}
    shared = tuple(table for table in TABLES if table.name not in legacy)
    return (shared + VERSIONED_JOURNAL_V2_TABLES
            + BACKTEST_TERMINAL_SNAPSHOT_V2_TABLES
            + DEFINITION_TABLES
            + (LEGACY_STRATEGY_SIGNAL_V1, LEGACY_COMMIT_V1))


def fixed_backtest_v2_preflight(client: Any) -> None:
    """Read-only, exact admission for one V2 running and terminal writer.

    The occupied V1 signal/commit rows remain immutable and readable. Shared
    typed facts, V2 signal/commit fences, and terminal facts are append-only.
    """
    legacy = {"trading_strategy_signal_v1", "trading_commit_v1"}
    contracts = fixed_backtest_v2_contracts()
    writable = tuple(table for table in contracts if table.name not in legacy)
    storage_preflight(client, tables=contracts)
    journal_permission_preflight(
        client,
        journal_tables=frozenset(table.name for table in writable),
        read_only_tables=frozenset(legacy),
    )


def backtest_terminal_snapshot_v2_ddl() -> tuple[str, ...]:
    """Staged DDL only; never executed by a runtime or active schema check."""
    return tuple(table.ddl() for table in BACKTEST_TERMINAL_SNAPSHOT_V2_TABLES)


def backtest_definition_ddl() -> tuple[str, ...]:
    """Operator-only normalized definition DDL; never run by Backtest."""
    from src.trading_runtime.arte_backtest_definition import TABLES as DEFINITION_TABLES

    return tuple(table.ddl() for table in DEFINITION_TABLES)


def schema_ddl() -> tuple[str, ...]:
    """Return DDL for a separately authorized installer, never run it here."""
    return tuple(table.ddl() for table in TABLES)


def long_momentum_parameter_upgrade_ddl() -> tuple[str, ...]:
    """Operator-only staged parameter tables; excluded from active TABLES."""
    from src.trading_runtime.arte_long_momentum_parameter_journal import PARAMETER_TABLES

    return tuple(table.ddl() for table in PARAMETER_TABLES)


def strategy_assignment_command_upgrade_ddl() -> tuple[str, ...]:
    """Operator-only additive typed assignment-command table and commit proof."""
    by_name = {table.name: table for table in TABLES}
    empty_hash = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    return (
        by_name["trading_strategy_assignment_command_v1"].ddl(),
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        "assignment_command_count UInt32 DEFAULT 0 AFTER event_count",
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        f"assignment_command_hash FixedString(64) DEFAULT '{empty_hash}' "
        "AFTER event_hash",
    )


def backtest_cursor_upgrade_ddl() -> tuple[str, ...]:
    """Operator-only additive typed Backtest cursor and commit-fence fields."""
    by_name = {table.name: table for table in TABLES}
    empty_hash = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    return (
        by_name["trading_backtest_cursor_v1"].ddl(),
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        "backtest_cursor_count UInt32 DEFAULT 0 AFTER intent_slice_hash",
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        f"backtest_cursor_hash FixedString(64) DEFAULT '{empty_hash}' "
        "AFTER backtest_cursor_count",
    )


def backtest_market_authority_upgrade_ddl() -> tuple[str, ...]:
    """Operator-only typed fixed-market authority and commit proof."""
    by_name = {table.name: table for table in TABLES}
    empty_hash = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    return (
        by_name["trading_backtest_market_authority_v1"].ddl(),
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        "backtest_market_authority_count UInt32 DEFAULT 0 AFTER backtest_cursor_hash",
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        f"backtest_market_authority_hash FixedString(64) DEFAULT '{empty_hash}' "
        "AFTER backtest_market_authority_count",
    )


def backtest_progress_upgrade_ddl() -> tuple[str, ...]:
    """Operator-only additive Backtest progress family and commit proof."""
    by_name = {table.name: table for table in TABLES}
    empty_hash = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    return (
        by_name["trading_backtest_progress_v1"].ddl(),
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        "backtest_progress_count UInt32 DEFAULT 0 AFTER backtest_cursor_hash",
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        f"backtest_progress_hash FixedString(64) DEFAULT '{empty_hash}' "
        "AFTER backtest_progress_count",
    )


def prepared_v7_lease_upgrade_ddl() -> tuple[str, ...]:
    """Operator-only additive typed Backtest lease evidence and commit proof."""
    by_name = {table.name: table for table in TABLES}
    empty_hash = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    return (
        by_name["trading_prepared_v7_lease_v1"].ddl(),
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        "prepared_v7_lease_count UInt32 DEFAULT 0 AFTER backtest_progress_hash",
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        f"prepared_v7_lease_hash FixedString(64) DEFAULT '{empty_hash}' "
        "AFTER prepared_v7_lease_count",
    )


def strategy_signal_evidence_upgrade_ddl() -> tuple[str, ...]:
    """Operator-only additive schema for bounded typed signal evidence trees."""
    by_name = {table.name: table for table in TABLES}
    empty_hash = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    return (
        by_name["trading_strategy_signal_evidence_node_v1"].ddl(),
        "ALTER TABLE arte.trading_strategy_signal_v1 ADD COLUMN IF NOT EXISTS "
        "evidence_node_count UInt32 DEFAULT 0 AFTER source_signal_count",
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        "signal_evidence_node_count UInt32 DEFAULT 0 AFTER signal_count",
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        f"signal_evidence_node_hash FixedString(64) DEFAULT '{empty_hash}' "
        "AFTER signal_hash",
    )


def strategy_signal_decision_upgrade_ddl(client: Any) -> tuple[str, ...]:
    """Operator-only DDL, available only for a quiesced, empty signal table.

    Adding even nullable columns changes the canonical content hash of older
    signal rows. A nonempty table requires a versioned family and reader.
    The operator must keep journal writers stopped through installation.
    """
    rows = _rows(client,
        "SELECT count() AS row_count FROM arte.trading_strategy_signal_v1 "
        "FORMAT JSONEachRow")
    if (len(rows) != 1 or type(rows[0].get("row_count")) not in (int, str)
            or not str(rows[0]["row_count"]).isdigit()
            or int(rows[0]["row_count"]) != 0):
        raise ValueError(
            "Strategy signal decision columns require an empty, quiesced table; "
            "nonempty history needs a versioned schema and reader"
        )
    names = (
        ("decision_assignment_id", "Nullable(String)"),
        ("decision_reference_price", "Nullable(Decimal(38, 10))"),
        ("decision_status", "Nullable(String)"),
        ("decision_reason_detail", "Nullable(String)"),
    )
    return tuple(
        "ALTER TABLE arte.trading_strategy_signal_v1 ADD COLUMN IF NOT EXISTS "
        f"{name} {kind} DEFAULT NULL AFTER "
        f"{'evidence_node_count' if index == 0 else names[index - 1][0]}"
        for index, (name, kind) in enumerate(names)
    )


def backtest_snapshot_anchor_upgrade_ddl() -> str:
    """Operator-only causal link between terminal events and account state."""
    return next(table.ddl() for table in TABLES
                if table.name == "trading_backtest_snapshot_anchor_v1")


def portfolio_policy_schema_upgrade_ddl() -> tuple[str, ...]:
    """Operator-only immutable policy catalog; no market or state table writes."""
    names = {"trading_portfolio_policy_v1", "trading_portfolio_policy_commit_v2"}
    names.update(table for table, _ in POLICY_ALLOWED_TABLES.values())
    return tuple(table.ddl() for table in TABLES if table.name in names)


def portfolio_snapshot_schema_upgrade_ddl() -> tuple[str, ...]:
    """Operator-only, normalized account recovery snapshot families."""
    policy_names = {"trading_portfolio_policy_v1", "trading_portfolio_policy_commit_v2"}
    policy_names.update(table for table, _ in POLICY_ALLOWED_TABLES.values())
    return tuple(table.ddl() for table in TABLES
                 if table.name.startswith("trading_portfolio_")
                 and table.name not in policy_names)


def portfolio_reconciliation_event_upgrade_ddl() -> tuple[str, ...]:
    """Operator-only typed sync event, late fence, and commit proof columns."""
    by_name = {table.name: table for table in TABLES}
    empty_hash = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    return (
        by_name["trading_portfolio_reconciliation_event_v1"].ddl(),
        by_name["trading_portfolio_sync_fence_v1"].ddl(),
        by_name["trading_portfolio_sync_snapshot_marker_v1"].ddl(),
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        "portfolio_reconciliation_event_count UInt32 DEFAULT 0 "
        "AFTER portfolio_reservation_event_hash",
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        f"portfolio_reconciliation_event_hash FixedString(64) DEFAULT '{empty_hash}' "
        "AFTER portfolio_reconciliation_event_count",
    )


def portfolio_snapshot_timestamp_upgrade_ddl() -> str:
    """Operator-only capture time; old unanchored revisions fail recovery."""
    return (
        "ALTER TABLE arte.trading_portfolio_snapshot_v1 "
        "ADD COLUMN IF NOT EXISTS snapshot_at Nullable(DateTime64(6, 'UTC')) "
        "AFTER snapshot_id"
    )


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


def intent_decision_upgrade_ddl() -> tuple[str, ...]:
    """Operator-only normalized rejection/deferral schema and commit counters."""
    by_name = {table.name: table for table in TABLES}
    empty_hash = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    return (
        by_name["trading_intent_decision_v1"].ddl(),
        by_name["trading_intent_decision_reason_v1"].ddl(),
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        "intent_decision_count UInt32 DEFAULT 0 AFTER account_risk_reason_hash",
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        f"intent_decision_hash FixedString(64) DEFAULT '{empty_hash}' AFTER intent_decision_count",
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        "intent_decision_reason_count UInt32 DEFAULT 0 AFTER intent_decision_hash",
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        f"intent_decision_reason_hash FixedString(64) DEFAULT '{empty_hash}' "
        "AFTER intent_decision_reason_count",
    )


def portfolio_admission_upgrade_ddl() -> tuple[str, ...]:
    """Operator-only typed Portfolio admission tables and sealed commit fields."""
    names = ("trading_portfolio_decision_v1",
             "trading_portfolio_decision_reason_v1",
             "trading_portfolio_reservation_event_v1")
    by_name = {table.name: table for table in TABLES}
    empty_hash = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    columns = (
        ("portfolio_decision_count", "UInt32", "0"),
        ("portfolio_decision_hash", "FixedString(64)", f"'{empty_hash}'"),
        ("portfolio_decision_reason_count", "UInt32", "0"),
        ("portfolio_decision_reason_hash", "FixedString(64)", f"'{empty_hash}'"),
        ("portfolio_reservation_event_count", "UInt32", "0"),
        ("portfolio_reservation_event_hash", "FixedString(64)", f"'{empty_hash}'"),
    )
    return (*tuple(by_name[name].ddl() for name in names),
            *(f"ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
              f"{name} {kind} DEFAULT {default}"
              for name, kind, default in columns))


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


def storage_preflight(client: Any, *, tables: tuple[TableContract, ...] = TABLES) -> None:
    """Verify the exact typed schema and physical placement without writes."""
    policies = _rows(client,
        "SELECT disks FROM system.storage_policies "
        "WHERE policy_name='live_market_ssd' FORMAT JSONEachRow")
    if len(policies) != 1 or policies[0].get("disks") != [STORAGE_POLICY]:
        raise ValueError("Typed journal requires an SSD-only live_market_ssd policy")
    names = ",".join(f"'{table.name}'" for table in tables)
    actual_tables = _rows(client,
        "SELECT name,engine,storage_policy,partition_key,sorting_key FROM system.tables "
        f"WHERE database='arte' AND name IN ({names}) FORMAT JSONEachRow")
    by_name = {row["name"]: row for row in actual_tables}
    if set(by_name) != {table.name for table in tables}:
        raise ValueError("Typed journal tables are missing")
    for table in tables:
        row = by_name[table.name]
        if (row["engine"], row["storage_policy"], row["partition_key"],
                row["sorting_key"]) != (
                    "MergeTree", STORAGE_POLICY, table.partition, table.order):
            raise ValueError(f"Typed journal layout differs: {table.name}")
    actual_columns = _rows(client,
        "SELECT table,name,type FROM system.columns WHERE database='arte' "
        f"AND table IN ({names}) ORDER BY table,position FORMAT JSONEachRow")
    for table in tables:
        columns = tuple((row["name"], row["type"])
                        for row in actual_columns if row["table"] == table.name)
        if columns != table.columns:
            raise ValueError(f"Typed journal columns differ: {table.name}")
    indexed = {table.name for table in tables
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
    indexed_names = ",".join(f"'{name}'" for name in sorted(indexed))
    missing_index_parts = _rows(client,
        "SELECT table,name FROM system.parts WHERE database='arte' "
        f"AND table IN ({indexed_names}) AND active "
        "AND secondary_indices_compressed_bytes=0 LIMIT 1 FORMAT JSONEachRow")
    if missing_index_parts:
        raise ValueError("Typed journal has active parts without materialized batch indexes")


def journal_permission_preflight(
    client: Any, *, journal_tables: frozenset[str] | None = None,
    read_only_tables: frozenset[str] = frozenset(),
) -> None:
    """Fail closed unless this principal can only read market and append journal."""
    journal = ({table.name for table in TABLES} if journal_tables is None
               else set(journal_tables))
    market = MARKET_READ_TABLES
    # The staged live-signal profile is an explicit, all-or-nothing extension
    # of this principal. Validate its physical tables before accepting grants.
    from src.backend.live_signal_journal_preflight import (
        LIVE_SIGNAL_TABLES, staged_live_signal_storage_preflight,
    )
    staged_names = {table.name for table in LIVE_SIGNAL_TABLES}
    grant_lines = client.execute("SHOW GRANTS").splitlines()
    if any(f"ON arte.{name} " in line for line in grant_lines
           for name in staged_names):
        staged_live_signal_storage_preflight(client)
        journal |= staged_names
    required = journal | market | read_only_tables
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
