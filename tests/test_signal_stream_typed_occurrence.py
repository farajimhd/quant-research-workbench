from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

import pytest

from src.backend.canvas_preview_service import _attach_compact_news_intelligence
from src.backend.signal_stream_runtime_service import _occurrence
from src.backend.signal_stream_typed_catalog import build_signal_field_catalog
from src.backend.signal_stream_typed_occurrence import (
    TABLES, project_typed_occurrence, restore_typed_occurrence,
)
from tests.test_signal_stream_typed_catalog import _source


def _fixture():
    stream, columns, row = _source()
    stream["inclusion_rule_sets"] = ["rule-2", "rule-1"]
    row.update(news_ai_review=92.0, news_ai_review_state="complete",
               sec_review_status="reviewed")
    catalog = build_signal_field_catalog(stream, columns,
                                         configuration_revision="configuration-1")
    occurrence = _occurrence(stream, row, columns,
                             as_of=datetime(2026, 9, 24, 14, 0, tzinfo=UTC),
                             definition_revision="definition-1", typed_catalog=catalog)
    return stream, columns, catalog, occurrence


def test_producer_occurrence_exact_cold_roundtrip_and_named_tables() -> None:
    stream, columns, catalog, occurrence = _fixture()
    rows = project_typed_occurrence(occurrence, catalog, stream, columns)
    assert restore_typed_occurrence(deepcopy(rows), catalog, stream, columns) == occurrence
    assert [row["rule_set_id"] for row in rows["rules"]] == ["rule-2", "rule-1"]
    assert rows["parent"]["news_ai_review_present"] is True
    assert rows["parent"]["news_ai_reaction_present"] is False
    assert all("storage_policy = 'live_market_ssd'" in table.ddl() for table in TABLES)
    assert all("JSON" not in table.ddl() and "payload" not in table.ddl()
               and " Array(" not in table.ddl() and " Map(" not in table.ddl()
               for table in TABLES)


def test_optional_numeric_int_from_real_producer_retains_python_type() -> None:
    stream, columns, catalog, occurrence = _fixture()
    occurrence = _occurrence(
        stream, {**_source()[2], "news_ai_review": 92}, columns,
        as_of=datetime(2026, 9, 24, 14, 0, tzinfo=UTC),
        definition_revision="definition-1", typed_catalog=catalog)
    rows = project_typed_occurrence(occurrence, catalog, stream, columns)
    assert rows["parent"]["news_ai_review_is_int"] is True
    assert rows["parent"]["news_ai_review_int"] == 92
    restored = restore_typed_occurrence(deepcopy(rows), catalog, stream, columns)
    assert type(restored["news_ai_review"]) is int
    assert restored == occurrence


def test_canvas_news_projection_to_occurrence_to_typed_cold_readback() -> None:
    stream, columns, row = _source()
    _attach_compact_news_intelligence(row, {
        "canonical_news_id": "news-1", "title": "Issuer update",
        "communication_purpose": "announce", "information_origin": "issuer",
        "synthesis_direction": "positive",
        "ai_state": {"review": {"status": "complete", "labels": {"issuers": [{
            "forecast_relevance_probability": 0.92,
            "positive_implication_probability": 0.8,
            "negative_implication_probability": 0.1,
        }]}}},
    })
    catalog = build_signal_field_catalog(stream, columns,
                                         configuration_revision="configuration-1")
    occurrence = _occurrence(stream, row, columns,
                             as_of=datetime(2026, 9, 24, 14, 0, tzinfo=UTC),
                             definition_revision="definition-1", typed_catalog=catalog)
    rows = project_typed_occurrence(occurrence, catalog, stream, columns)
    assert restore_typed_occurrence(deepcopy(rows), catalog, stream, columns) == occurrence


@pytest.mark.parametrize("family", ["rules", "columns", "fields"])
def test_missing_child_fails_closed(family) -> None:
    stream, columns, catalog, occurrence = _fixture()
    rows = project_typed_occurrence(occurrence, catalog, stream, columns)
    rows[family].pop()
    with pytest.raises(ValueError):
        restore_typed_occurrence(rows, catalog, stream, columns)


def test_unmodeled_optional_shape_fails_before_projection() -> None:
    stream, columns, catalog, occurrence = _fixture()
    with pytest.raises(ValueError, match="wrong type"):
        project_typed_occurrence({**occurrence, "news_ai_reaction": {"unknown": 1}},
                                 catalog, stream, columns)
    with pytest.raises(ValueError, match="unmodeled"):
        project_typed_occurrence({**occurrence, "extra": 1}, catalog, stream, columns)
