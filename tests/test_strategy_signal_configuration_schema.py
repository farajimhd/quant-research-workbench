from dataclasses import fields

from src.trading_runtime import strategy_signal_configuration_schema as schema
from src.backend.signal_stream_typed_catalog import FieldBinding


def test_shared_signal_field_relation_covers_existing_native_catalogue_contract():
    assert {name for name, _ in schema.FIELD.columns} - {"configuration_id", "signal_stream_id"} == {
        field.name for field in fields(FieldBinding)}
    assert {name for name, _ in schema.STREAM_FIELD.columns} == {
        "configuration_id", "signal_stream_id", "ordinal", "column_id"}
    assert {name for name, _ in schema.STREAM_RULE.columns} == {
        "configuration_id", "signal_stream_id", "ordinal", "rule_set_id"}


def test_rule_literals_and_relations_are_defined_and_operational_storage_is_explicit():
    conditions = dict(schema.CONDITION.columns)
    assert conditions["number_value"] == "Nullable(Float64)"
    assert conditions["integer_value"] == "Nullable(Int64)"
    assert conditions["boolean_value"] == "Nullable(UInt8)"
    assert conditions["text_value"] == "Nullable(String)"
    for table, statement in zip(schema.TABLES, schema.ddl(), strict=True):
        assert "live_market_ssd" in statement
        assert not any(name in {"payload", "json", "blob", "child_key"} for name, _ in table.columns)
        assert not any(kind.startswith(("JSON", "Array", "Map", "Object")) for _, kind in table.columns)
