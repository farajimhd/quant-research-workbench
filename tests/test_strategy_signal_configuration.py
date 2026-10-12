from dataclasses import asdict
from datetime import datetime, timezone

import pytest

from src.backend.strategy_signal_configuration import restore_signal_configuration
from src.backend.signal_stream_typed_catalog import build_signal_field_catalog
from src.backend.signal_stream_runtime_service import _occurrence
from src.trading_runtime.watchlist_resolver import evaluate_rule_sets_frame
from src.trading_runtime import strategy_signal_configuration_schema as schema
from tests.test_signal_stream_typed_catalog import _source
from tests.test_strategy_configuration_selection import ID


def rows():
    stream, columns, _ = _source()
    from src.backend.data_field_contracts import build_data_field_catalog
    data_fields = build_data_field_catalog([], [columns["price"]])
    price_ref = data_fields[0]["outputs"][0]["field_ref"]
    columns["price"]["field_ref"] = price_ref
    catalogue = build_signal_field_catalog(stream, columns, configuration_revision="configuration-1")
    condition = dict(configuration_id=ID, rule_set_id="up", ordinal=0, enabled=1,
        comparator="greater_than", left_field_ref=price_ref, left_source_id="price",
        left_interval="", left_aggregation="", left_value_selection="latest",
        right_field_ref="", right_source_id="", right_interval="", right_aggregation="",
        right_value_selection="latest", value_type="number", number_value=10.,
        integer_value=None, boolean_value=None, text_value=None)
    return {
        schema.STREAM.name: [dict(configuration_id=ID, ordinal=0, signal_stream_id="stream-1",
            name="Stream", revision=1, enabled=1, source_type="core_scan", source_id="core",
            occurrence_source="rule_evaluator", inclusion_operator="all", trigger_policy="false_to_true",
            rearm_policy="after_false", cooldown_ms=0)],
        schema.FIELD.name: [dict(configuration_id=ID, signal_stream_id="stream-1", **asdict(binding))
                            for binding in catalogue.bindings],
        schema.STREAM_FIELD.name: [dict(configuration_id=ID, signal_stream_id="stream-1", ordinal=index,
                                       column_id=column) for index, column in enumerate(stream["columns"])],
        schema.STREAM_RULE.name: [dict(configuration_id=ID, signal_stream_id="stream-1", ordinal=0, rule_set_id="up")],
        schema.RULE_SET.name: [dict(configuration_id=ID, ordinal=0, rule_set_id="up", name="Up", enabled=1, operator="all")],
        schema.CONDITION.name: [condition],
    }


def test_typed_stream_rows_reuse_native_polars_compiler_and_occurrence_catalogue():
    configuration, catalogs = restore_signal_configuration(rows(), configuration_id=ID,
                                                           configuration_revision="configuration-1")
    candidates = [{"ticker": "ABC", "price": 12.}, {"ticker": "XYZ", "price": 9.}]
    assert evaluate_rule_sets_frame(configuration["rule_sets"], candidates)["up"] == [True, False]
    source = catalogs["stream-1"]
    occurrence = _occurrence(source.stream, {"ticker": "ABC", "price": 12., "rule_set:up": True},
        dict(source.columns), as_of=datetime(2026, 10, 12, 14, tzinfo=timezone.utc),
        definition_revision="definition-1", typed_catalog=source.catalog)
    assert occurrence["evidence"] == {"price": 12., "rule_set:up": True}
    assert source.catalog.bindings[0].instance_ref == configuration["data_fields"][0]["outputs"][0]["field_ref"]


@pytest.mark.parametrize("failure", ["owner", "duplicate", "orphan", "ordinal", "literal", "nan", "instance", "selection", "right", "rule_field"])
def test_typed_stream_reconstruction_rejects_invalid_relations_and_literals(failure):
    source = rows()
    if failure == "owner": source[schema.FIELD.name][0]["configuration_id"] = "00000000-0000-0000-0000-000000000063"
    if failure == "duplicate": source[schema.FIELD.name].append(source[schema.FIELD.name][0])
    if failure == "orphan": source[schema.STREAM_RULE.name][0]["rule_set_id"] = "missing"
    if failure == "ordinal": source[schema.STREAM_FIELD.name][0]["ordinal"] = 8
    if failure == "literal": source[schema.CONDITION.name][0]["integer_value"] = 10
    if failure == "nan": source[schema.CONDITION.name][0]["number_value"] = float("nan")
    if failure == "instance": source[schema.FIELD.name][0]["instance_ref"] = "other"
    if failure == "selection": source[schema.STREAM_FIELD.name] = []
    if failure == "right":
        source[schema.CONDITION.name][0]["number_value"] = None
        source[schema.CONDITION.name][0]["value_type"] = "none"
    if failure == "rule_field": source[schema.FIELD.name][1]["source_id"] = "missing"
    with pytest.raises(ValueError):
        restore_signal_configuration(source, configuration_id=ID, configuration_revision="configuration-1")


def test_signed_numeric_literals_are_preserved_for_native_polars_rules():
    source = rows()
    source[schema.CONDITION.name][0]["number_value"] = -2.5
    configuration, _ = restore_signal_configuration(source, configuration_id=ID, configuration_revision="configuration-1")
    assert configuration["rule_sets"][0]["conditions"][0]["value"] == -2.5
    assert evaluate_rule_sets_frame(configuration["rule_sets"], [{"price": -2.}, {"price": -3.}])["up"] == [True, False]


def test_native_transition_clock_normalization_preserves_instant_and_rejects_naive_clock():
    from datetime import timedelta
    from src.backend.signal_stream_runtime_service import _freeze_transition
    at = datetime(2026, 10, 12, 14, 0, 0, 123456, tzinfo=timezone(timedelta(hours=2)))
    assert _freeze_transition({"available_at": at})["available_at"] == "2026-10-12T12:00:00.123456+00:00"
    with pytest.raises(ValueError, match="timezone-aware"):
        _freeze_transition({"available_at": at.replace(tzinfo=None)})
