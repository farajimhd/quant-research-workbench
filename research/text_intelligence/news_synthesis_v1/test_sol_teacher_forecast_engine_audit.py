from __future__ import annotations

from .sol_teacher_forecast_engine_audit import prediction_context


def test_prediction_context_extracts_only_matching_issuer() -> None:
    document = {
        "entities": [
            {"entity_id": "e1", "ticker": "NYSE:ABC"},
            {"entity_id": "e2", "ticker": "XYZ"},
        ],
        "issuer_views": [
            {"entity_id": "e1", "composite_sentiment": "negative", "statement_ids": ["s1"]},
            {"entity_id": "e2", "composite_sentiment": "positive", "statement_ids": ["s2"]},
        ],
        "statements": [
            {"statement_id": "s1", "concept_leaf": "capital.structure"},
            {"statement_id": "s2", "concept_leaf": "guidance.issued"},
        ],
        "participations": [
            {"entity_id": "e1", "statement_id": "s1"},
            {"entity_id": "e2", "statement_id": "s2"},
        ],
        "eligibility": [
            {"entity_id": "e1", "product": "forecast_trigger", "eligible": True},
            {"entity_id": "e2", "product": "forecast_trigger", "eligible": False},
        ],
    }
    context = prediction_context(document, "NYSE:ABC")
    assert context["predicted_sentiment"] == "negative"
    assert context["predicted_forecast_eligible"] is True
    assert [row["statement_id"] for row in context["statements"]] == ["s1"]
    assert [row["statement_id"] for row in context["participations"]] == ["s1"]


def test_prediction_context_returns_missing_for_absent_ticker() -> None:
    assert prediction_context({"entities": []}, "ABC") == {
        "predicted_sentiment": "missing"
    }
