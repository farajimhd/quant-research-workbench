"""Central typed selection contracts for future app strategy configurations.

Programme rules/sizing and portfolio policies keep their existing authorities.
These relations own only configuration identity and ordered app selections.
They are staged source contracts, never runtime DDL or strategy-owned tables.
"""
from .arte_journal_schema import TableContract


SELECTION = TableContract(
    "trading_strategy_configuration_v1",
    (("configuration_id", "UUID"), ("label", "String"),
     ("run_plan_id", "String"), ("strategy_id", "String"),
     ("strategy_revision", "UInt32"), ("program_release_id", "UUID"),
     ("program_release_hash", "FixedString(64)"),
     ("oms_limit_offset_bps", "Float64"),
     ("account_count", "UInt32"), ("account_hash", "FixedString(64)"),
     ("mode_count", "UInt32"), ("mode_hash", "FixedString(64)"),
     ("mandate_count", "UInt32"), ("mandate_hash", "FixedString(64)"),
     ("group_count", "UInt32"), ("group_hash", "FixedString(64)"),
     ("group_member_count", "UInt32"), ("group_member_hash", "FixedString(64)"),
     ("supervisor_count", "UInt32"), ("supervisor_hash", "FixedString(64)"),
     ("content_hash", "FixedString(64)"),
     ("published_at", "DateTime64(6, 'UTC')")),
    "tuple()", "configuration_id")

ACCOUNT = TableContract(
    "trading_strategy_configuration_account_v1",
    (("configuration_id", "UUID"), ("ordinal", "UInt32"),
     ("account_key", "String"), ("account_class", "String"),
     ("session_key", "String"), ("base_currency", "String"),
     ("enabled", "UInt8"), ("policy_hash", "FixedString(64)"),
     ("strategy_allocation", "Float64")),
    "tuple()", "configuration_id,ordinal")

MODE = TableContract(
    "trading_strategy_configuration_account_mode_v1",
    (("configuration_id", "UUID"), ("account_key", "String"),
     ("ordinal", "UInt32"), ("mode", "LowCardinality(String)")),
    "tuple()", "configuration_id,account_key,ordinal")

MANDATE = TableContract(
    "trading_strategy_configuration_mandate_v1",
    (("configuration_id", "UUID"), ("ordinal", "UInt32"),
     ("mandate_id", "String"), ("run_plan_id", "String"),
     ("account_key", "String"), ("enabled", "UInt8"),
     ("maximum_cash_fraction", "Float64"), ("maximum_planned_risk_fraction", "Float64"),
     ("maximum_positions", "UInt32"), ("assignment_mode", "String"),
     ("allocation_weight", "Float64"), ("maximum_action_authority", "String"),
     ("allow_replacement", "UInt8"), ("minimum_replacement_improvement_pct", "Float64")),
    "tuple()", "configuration_id,ordinal")

GROUP = TableContract(
    "trading_strategy_configuration_group_v1",
    (("configuration_id", "UUID"), ("ordinal", "UInt32"),
     ("group_id", "String"), ("maximum_gross_exposure", "Float64"),
     ("maximum_ticker_exposure", "Float64")),
    "tuple()", "configuration_id,ordinal")

GROUP_MEMBER = TableContract(
    "trading_strategy_configuration_group_member_v1",
    (("configuration_id", "UUID"), ("group_id", "String"),
     ("ordinal", "UInt32"), ("account_key", "String")),
    "tuple()", "configuration_id,group_id,ordinal")

SUPERVISOR = TableContract(
    "trading_strategy_configuration_supervisor_v1",
    (("configuration_id", "UUID"), ("mode", "LowCardinality(String)"),
     ("enabled", "UInt8")),
    "tuple()", "configuration_id,mode")

CHILDREN = (ACCOUNT, MODE, MANDATE, GROUP, GROUP_MEMBER, SUPERVISOR)
TABLES = (*CHILDREN, SELECTION)


def ddl():
    """Reviewable operator migration, with the immutable selection seal last."""
    return tuple(table.ddl() for table in TABLES)
