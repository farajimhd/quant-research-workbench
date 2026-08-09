from __future__ import annotations

from .forecast_trigger_eligibility_audit import (
    binary_metrics,
    certified_forecast_units,
    eligibility_gate_trace,
    predicted_forecast_units,
)


def test_binary_metrics_reports_all_required_scores() -> None:
    rows = [
        {"confusion": "TP"},
        {"confusion": "FN"},
        {"confusion": "FP"},
        {"confusion": "TN"},
        {"confusion": "TN"},
    ]
    metrics = binary_metrics(rows)
    assert metrics["confusion"] == {"TP": 1, "FN": 1, "FP": 1, "TN": 2}
    assert metrics["precision"] == 0.5
    assert metrics["recall"] == 0.5
    assert metrics["specificity"] == 2 / 3
    assert metrics["f1"] == 0.5
    assert metrics["balanced_accuracy"] == (0.5 + 2 / 3) / 2
    assert metrics["raw_accuracy"] == 3 / 5


def test_certified_units_include_resolved_negative_entities() -> None:
    document = {
        "sample_id": "N1",
        "entities": [
            {"entity_id": "e1", "entity_kind": "security", "ticker": "AAA", "identity_status": "resolved"},
            {"entity_id": "e2", "entity_kind": "security", "ticker": "BBB", "identity_status": "resolved"},
            {"entity_id": "e3", "entity_kind": "security", "ticker": "CCC", "identity_status": "unresolved"},
        ],
        "eligibility": [
            {"entity_id": "e1", "product": "forecast_trigger", "eligible": True},
            {"entity_id": "e2", "product": "forecast_trigger", "eligible": False},
        ],
    }
    rows = certified_forecast_units(document)
    assert [(row["ticker"], row["gold_forecast_eligible"]) for row in rows] == [
        ("AAA", True),
        ("BBB", False),
    ]


def test_predicted_units_preserve_gate_reasons() -> None:
    document = {
        "entities": [
            {"entity_id": "e1", "ticker": "AAA"},
            {"entity_id": "e2", "ticker": "BBB"},
        ],
        "eligibility": [
            {
                "entity_id": "e1",
                "product": "forecast_trigger",
                "eligible": False,
                "reasons": ["no_current_event"],
                "blocking_flags": [],
            },
            {"entity_id": "e2", "product": "issuer_history", "eligible": True},
        ],
    }
    rows = predicted_forecast_units(document)
    assert set(rows) == {"AAA"}
    assert rows["AAA"][0]["reasons"] == ["no_current_event"]


def test_gate_trace_is_issuer_specific() -> None:
    document = {
        "entities": [
            {"entity_id": "e1", "entity_kind": "security", "ticker": "AAA", "identity_status": "resolved"},
            {"entity_id": "e2", "entity_kind": "security", "ticker": "BBB", "identity_status": "resolved"},
        ],
        "statements": [
            {"statement_id": "s1", "statement_kind": "event", "concept_leaf": "product.milestone", "time_relation": "current"},
        ],
        "participations": [
            {"statement_id": "s1", "entity_id": "e1", "semantic_role": "affected_subject", "semantic_sentiment": "positive"},
        ],
        "envelope": {
            "communication_purpose": {"value": "report"},
            "information_origin": {"value": "company"},
        },
        "eligibility": [
            {"entity_id": "e1", "product": "forecast_trigger", "eligible": True, "reasons": ["eligible_under:forecast_trigger"]},
            {"entity_id": "e2", "product": "forecast_trigger", "eligible": False, "reasons": ["insufficient_trustworthy_evidence"]},
        ],
        "quality_flags": [],
    }
    first = eligibility_gate_trace(document, "e1")
    second = eligibility_gate_trace(document, "e2")
    assert first["current_event_or_forward_guidance"] is True
    assert first["positive_or_negative_implication"] is True
    assert second["current_event_or_forward_guidance"] is False
    assert second["positive_or_negative_implication"] is False
