"""Shared named configuration relations for the current Signal Stream resolver.

These are staged operator contracts, not strategy-owned tables. Programmes
retain their existing market-only DAG authority; these rows select the app's
scanner/stream evidence and its native Polars rule-set compiler.
"""
from .arte_journal_schema import TableContract


STREAM = TableContract(
    "trading_strategy_configuration_signal_stream_v1",
    (("configuration_id", "UUID"), ("ordinal", "UInt32"),
     ("signal_stream_id", "String"), ("name", "String"), ("revision", "UInt32"),
     ("enabled", "UInt8"), ("source_type", "String"), ("source_id", "String"),
     ("occurrence_source", "String"), ("inclusion_operator", "String"),
     ("trigger_policy", "String"), ("rearm_policy", "String"), ("cooldown_ms", "UInt32")),
    "tuple()", "configuration_id,ordinal")

FIELD = TableContract(
    "trading_strategy_configuration_signal_field_v1",
    (("configuration_id", "UUID"), ("signal_stream_id", "String"), ("column_id", "String"),
     ("source_kind", "String"), ("source_id", "String"), ("source_path", "String"),
     ("provenance", "String"), ("query_plan_id", "String"),
     ("available_at_contract", "String"), ("field_ref", "String"),
     ("instance_ref", "String"), ("value_type", "String"),
     ("interval", "String"), ("aggregation", "String")),
    "tuple()", "configuration_id,signal_stream_id,column_id")

STREAM_FIELD = TableContract(
    "trading_strategy_configuration_signal_stream_field_v1",
    (("configuration_id", "UUID"), ("signal_stream_id", "String"),
     ("ordinal", "UInt32"), ("column_id", "String")),
    "tuple()", "configuration_id,signal_stream_id,ordinal")

STREAM_RULE = TableContract(
    "trading_strategy_configuration_signal_stream_rule_v1",
    (("configuration_id", "UUID"), ("signal_stream_id", "String"),
     ("ordinal", "UInt32"), ("rule_set_id", "String")),
    "tuple()", "configuration_id,signal_stream_id,ordinal")

RULE_SET = TableContract(
    "trading_strategy_configuration_signal_rule_set_v1",
    (("configuration_id", "UUID"), ("ordinal", "UInt32"),
     ("rule_set_id", "String"), ("name", "String"),
     ("enabled", "UInt8"), ("operator", "String")),
    "tuple()", "configuration_id,ordinal")

CONDITION = TableContract(
    "trading_strategy_configuration_signal_condition_v1",
    (("configuration_id", "UUID"), ("rule_set_id", "String"),
     ("ordinal", "UInt32"), ("enabled", "UInt8"), ("comparator", "String"),
     ("left_field_ref", "String"), ("left_source_id", "String"),
     ("left_interval", "String"), ("left_aggregation", "String"),
     ("left_value_selection", "String"), ("right_field_ref", "String"),
     ("right_source_id", "String"), ("right_interval", "String"),
     ("right_aggregation", "String"), ("right_value_selection", "String"),
     ("value_type", "String"), ("number_value", "Nullable(Float64)"),
     ("integer_value", "Nullable(Int64)"), ("boolean_value", "Nullable(UInt8)"),
     ("text_value", "Nullable(String)")),
    "tuple()", "configuration_id,rule_set_id,ordinal")

TABLES = (STREAM, FIELD, STREAM_FIELD, STREAM_RULE, RULE_SET, CONDITION)


def ddl():
    """Return explicit typed DDL; never execute schema changes."""
    return tuple(table.ddl() for table in TABLES)
