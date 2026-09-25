from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from src.backend.signal_stream_runtime_service import _occurrence
from src.backend.signal_stream_typed_catalog import build_signal_field_catalog


AT = datetime(2026, 9, 24, 14, 0, tzinfo=UTC)


def _source():
    stream = {"signal_stream_id": "stream-1", "columns": ["price", "rule_set:up"]}
    columns = {
        "price": {"column_id": "price", "source_id": "market.last_price",
                  "source_kind": "data_field", "field_ref": "market.last_price@1s",
                  "value_type": "number", "source_path": "data-field://price@1",
                  "provenance": "computed", "query_plan_id": "plan-v1",
                  "available_at": "field output availability clock"},
        "rule_set:up": {"column_id": "rule_set:up", "source_id": "up",
                        "source_kind": "rule_set", "value_type": "boolean",
                        "source_path": "rule-set://up", "provenance": "derived",
                        "query_plan_id": "plan-v1",
                        "available_at": "candidate evaluation clock"},
    }
    row = {"ticker": "ABC", "price": 10.5, "rule_set:up": True}
    return stream, columns, row


def test_real_occurrence_producer_validates_pinned_closed_catalog() -> None:
    stream, columns, row = _source()
    catalog = build_signal_field_catalog(stream, columns,
                                         configuration_revision="configuration-1")
    occurrence = _occurrence(stream, row, columns, as_of=AT,
                             definition_revision="definition-1", typed_catalog=catalog)
    assert occurrence["evidence"] == {"price": 10.5, "rule_set:up": True}
    assert occurrence["field_evidence"]["market.last_price@1s"]["value"] == 10.5
    assert len(catalog.content_hash) == 64
    with pytest.raises(FrozenInstanceError):
        catalog.configuration_revision = "changed"


@pytest.mark.parametrize("column_id", ["event_id", "evidence", "field_evidence",
                                        "company_name", "matched_rule_set_ids"])
def test_reserved_parent_collision_fails_before_production(column_id) -> None:
    stream, columns, _ = _source()
    stream["columns"] = [column_id]
    columns[column_id] = {"column_id": column_id, "source_id": "x",
                          "value_type": "number"}
    with pytest.raises(ValueError, match="reserved"):
        build_signal_field_catalog(stream, columns,
                                   configuration_revision="configuration-1")


def test_non_scalar_and_catalog_drift_fail_typed_only() -> None:
    stream, columns, row = _source()
    catalog = build_signal_field_catalog(stream, columns,
                                         configuration_revision="configuration-1")
    row["price"] = {"unmodeled": 1}
    with pytest.raises(ValueError, match="evidence value"):
        _occurrence(stream, row, columns, as_of=AT,
                    definition_revision="definition-1", typed_catalog=catalog)
    # The deployed producer retains its existing behavior when no typed
    # catalog is supplied; only the inactive typed path rejects this shape.
    assert _occurrence(stream, row, columns, as_of=AT,
                       definition_revision="definition-1")["evidence"]["price"] == row["price"]
    row["price"] = 10.5
    changed = {**columns, "price": {**columns["price"], "value_type": "integer"}}
    with pytest.raises(ValueError, match="catalog drift"):
        _occurrence(stream, row, changed, as_of=AT,
                    definition_revision="definition-1", typed_catalog=catalog)


def test_unclosed_type_and_duplicate_field_instance_rejected() -> None:
    stream, columns, _ = _source()
    columns["price"]["value_type"] = "producer_defined"
    with pytest.raises(ValueError, match="not closed"):
        build_signal_field_catalog(stream, columns,
                                   configuration_revision="configuration-1")
    columns["price"]["value_type"] = "number"
    stream["columns"].append("price-duplicate")
    columns["price-duplicate"] = {**columns["price"], "column_id": "price-duplicate"}
    with pytest.raises(ValueError, match="duplicate.*instance"):
        build_signal_field_catalog(stream, columns,
                                   configuration_revision="configuration-1")


def test_catalog_rejects_unhashable_id_and_inexact_float64_integer() -> None:
    stream, columns, row = _source()
    stream["columns"] = [["price"]]
    with pytest.raises(ValueError, match="invalid typed Signal Stream catalog identity"):
        build_signal_field_catalog(stream, columns,
                                   configuration_revision="configuration-1")
    stream["columns"] = ["price"]
    catalog = build_signal_field_catalog(stream, columns,
                                         configuration_revision="configuration-1")
    row["price"] = 2**53 + 1
    with pytest.raises(ValueError, match="evidence value is invalid"):
        _occurrence(stream, row, columns, as_of=AT,
                    definition_revision="definition-1", typed_catalog=catalog)
