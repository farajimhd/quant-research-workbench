"""Centrally owned normalized contracts for future columnar strategy releases.

Staged operator installation only. Strategies cannot define tables, install
schemas or serialize opaque payloads. Existing releases retain their schemas.
"""
from .arte_journal_schema import TableContract


RELEASE = TableContract(
    "trading_columnar_strategy_release_v1",
    (("strategy_number", "UInt32"), ("release_id", "UUID"),
     ("strategy_id", "String"), ("source_winner_hash", "FixedString(64)"),
     ("source_generation", "UInt32"), ("source_candidate", "UInt32"),
     ("evaluation_interval_ms", "UInt32"),
     ("entry_cash", "Decimal(38, 10)"), ("add_cash", "Decimal(38, 10)"),
     ("stop_fraction", "Float64"), ("target_fraction", "Float64"),
     ("reduce_fraction", "Float64"), ("trail_fraction", "Float64"),
     ("add_minimum_profit", "Float64"), ("reduce_minimum_profit", "Float64"),
     ("maximum_adds", "UInt8"), ("cooldown_seconds", "UInt32"),
     ("swing_left", "UInt8"), ("swing_right", "UInt8"),
     ("universe_contract_hash", "FixedString(64)"),
     ("field_count", "UInt32"), ("field_hash", "FixedString(64)"),
     ("rule_count", "UInt32"), ("rule_hash", "FixedString(64)"),
     ("node_count", "UInt32"), ("node_hash", "FixedString(64)"),
     ("code_hash", "FixedString(64)"), ("content_hash", "FixedString(64)"),
     ("published_at", "DateTime64(6, 'UTC')")),
    "intDiv(strategy_number, 100)", "strategy_number,release_id")
FIELD = TableContract(
    "trading_columnar_strategy_field_v1",
    (("release_id", "UUID"), ("field_id", "UInt32"), ("field_name", "String"),
     ("unit", "LowCardinality(String)"), ("product", "String"),
     ("product_revision", "FixedString(64)"), ("resolution_ms", "UInt32")),
    "tuple()", "release_id,field_id")
RULE = TableContract(
    "trading_rule_program_v1",
    (("release_id", "UUID"), ("stage", "LowCardinality(String)"),
     ("position_open", "UInt8"), ("output_node", "UInt32"),
     ("minimum_position_age_seconds", "UInt32"), ("node_count", "UInt8")),
    "tuple()", "release_id,stage,position_open")
NODE = TableContract(
    "trading_rule_program_node_v1",
    (("release_id", "UUID"), ("stage", "LowCardinality(String)"),
     ("position_open", "UInt8"), ("node_id", "UInt32"),
     ("operation", "UInt8"), ("left_node", "Nullable(UInt32)"),
     ("right_node", "Nullable(UInt32)"), ("field_id", "Nullable(UInt32)"),
     ("window", "UInt16"), ("literal", "Float64"),
     ("literal_unit", "LowCardinality(String)")),
    "tuple()", "release_id,stage,position_open,node_id")
UNIVERSE = TableContract(
    "trading_volume_universe_contract_v1",
    (("release_id", "UUID"), ("resolution_ms", "UInt32"),
     ("volume_window_seconds", "UInt32"), ("top_count", "UInt16"),
     ("minimum_price", "Decimal(38, 10)"), ("maximum_price", "Decimal(38, 10)"),
     ("price_filter_before_volume", "UInt8"), ("include_current_completed_bucket", "UInt8"),
     ("retain_held_positions", "UInt8"), ("tie_break_contract", "String"),
     ("source_contract_hash", "FixedString(64)")),
    "tuple()", "release_id")
BROKER = TableContract(
    "trading_backtest_broker_contract_v1",
    (("run_id", "String"), ("liquidity_resolution_ms", "UInt32"),
     ("liquidity_product", "String"), ("liquidity_plan_hash", "FixedString(64)"),
     ("commission_per_share", "Decimal(38, 10)"),
     ("minimum_commission", "Decimal(38, 10)"),
     ("market_slippage_bps", "Decimal(38, 10)"),
     ("activation_delay_us", "UInt32"),
     ("liquidity_participation", "Float64"),
     ("marketable_liquidity_participation", "Float64"),
     ("content_hash", "FixedString(64)")),
    "tuple()", "run_id")

TABLES = (RELEASE, FIELD, RULE, NODE, UNIVERSE, BROKER)


def ddl() -> tuple[str, ...]:
    """Reviewable operator-only migration; no runtime DDL or writes."""
    return tuple(table.ddl() for table in TABLES)
