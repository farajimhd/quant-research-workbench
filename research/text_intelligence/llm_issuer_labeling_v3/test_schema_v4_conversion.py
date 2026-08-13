from __future__ import annotations

from .convert_consolidated_gold_v1 import convert_article
from .schema_v4 import SCHEMA_VERSION, validate_output


def _legacy_article(*, authority_id: str = "manual_certification_v1") -> dict:
    return {
        "source_id": "source-1",
        "source_timestamp": "2026-01-01T00:00:00Z",
        "authority_id": authority_id,
        "authority_version": "legacy-v1",
        "certification_level": "legacy",
        "partition": "development",
        "usage_policy": "model_development_allowed",
        "article_forecast_eligible": True,
        "source_hashes": {"text": "abc"},
        "lineage": {"source": "legacy"},
        "issuer_units": [
            {
                "unit_id": "source-1::ACME",
                "entity_id": "security:ACME",
                "ticker": "ACME",
                "forecast_eligibility": "eligible",
                "sentiment": "positive",
                "concepts": ["guidance.issued"],
            }
        ],
    }


def test_v4_removes_evidence_and_derives_article_eligibility() -> None:
    converted = convert_article(_legacy_article())
    labels = converted["labels"]
    assert labels["schema_version"] == SCHEMA_VERSION
    assert labels["article_forecast_eligible"] is True
    assert "evidence_sentence_ids" not in labels["issuers"][0]
    assert labels["issuers"][0]["forecast_relevance_probability"] == 1.0
    assert labels["issuers"][0]["event_tags"] == ["guidance"]
    assert validate_output(labels, allow_legacy_nulls=True) == []


def test_fresh_v4_output_is_evidence_free_and_valid() -> None:
    labels = {
        "schema_version": SCHEMA_VERSION,
        "article_forecast_eligible": True,
        "issuers": [
            {
                "issuer_name": "Acme",
                "ticker": "ACME",
                "exchange": None,
                "identity_source": "explicit_text",
                "identity_confidence_probability": 0.99,
                "forecast_relevance_probability": 0.9,
                "positive_implication_probability": 0.8,
                "negative_implication_probability": 0.1,
                "event_tags": ["guidance"],
                "issuer_roles": ["primary_subject"],
                "time_scope": "forward",
                "claim_source": "issuer",
            }
        ],
        "unresolved_issuer_mentions": [],
    }
    assert validate_output(labels) == []


def test_legacy_unavailable_fields_remain_null() -> None:
    row = _legacy_article()
    row["issuer_units"][0]["forecast_eligibility"] = "ineligible"
    row["issuer_units"][0]["sentiment"] = "not_applicable"
    row["issuer_units"][0]["concepts"] = []
    converted = convert_article(row)
    issuer = converted["labels"]["issuers"][0]
    assert converted["labels"]["article_forecast_eligible"] is False
    assert issuer["positive_implication_probability"] is None
    assert issuer["negative_implication_probability"] is None
    assert issuer["event_tags"] is None
    assert issuer["issuer_roles"] is None


def test_sol_eligibility_warning_is_explicit() -> None:
    converted = convert_article(
        _legacy_article(authority_id="sol_teacher_forecast_reviewed_gold_v2")
    )
    assert "not_independently_reviewed" in converted["conversion_lineage"][
        "eligibility_authority_warning"
    ]


def test_fresh_v4_output_rejects_legacy_nulls() -> None:
    labels = convert_article(_legacy_article())["labels"]
    assert validate_output(labels)


def test_no_ticker_units_retain_legacy_entity_identity() -> None:
    row = _legacy_article()
    row["issuer_units"][0]["ticker"] = ""
    converted = convert_article(row)
    assert converted["labels"]["issuers"][0]["issuer_name"] == "security:ACME"
    fields = converted["conversion_lineage"]["field_authority"][0]["fields"]
    assert fields["issuer_name"].startswith("legacy_entity_identifier_substituted")
