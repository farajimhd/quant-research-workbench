from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .codex_2026 import compare_labels, synthetic_dry_run
from .pipeline import _binary_metrics, _sentiment, normalize_source
from .prompt import build_messages
from .schema import SCHEMA_VERSION, TRANSPORT_SCHEMA, canonicalize_output, validate_output


def test_normalize_source_removes_structural_marker_not_content() -> None:
    row = {"source_id": "s", "source_schema": "x", "source_lineage": {}, "source_record": {"publication": {"title": "Acme raises guidance", "provider": "wire", "published_at_utc": "2026-01-01T00:00:00Z"}, "rendered_product": {"text": "Title: Acme raises guidance\nSource [provider_body:0] https://example.test\nAcme expects revenue to rise."}}}
    sample = normalize_source(row)
    assert [s["text"] for s in sample["normalized_sentences"]] == ["Title: Acme raises guidance", "Acme expects revenue to rise."]


def test_schema_accepts_neutral_material_event() -> None:
    payload = {"schema_version": SCHEMA_VERSION, "issuers": [{"issuer_name": "Acme", "ticker": "ACME", "exchange": None, "identity_source": "explicit_text", "identity_confidence_probability": 0.99, "forecast_relevance_probability": 0.9, "positive_implication_probability": 0.1, "negative_implication_probability": 0.1, "event_tags": ["capital_structure"], "issuer_roles": ["primary_subject"], "time_scope": "current", "claim_source": "issuer", "evidence_sentence_ids": [1]}], "unresolved_issuer_mentions": []}
    assert validate_output(payload, [1]) == []


def test_messages_do_not_include_gold_source_id() -> None:
    sample = {"source_id": "secret-id", "published_at_utc": "2026-01-01Z", "normalized_sentences": [{"sentence_id": 1, "text": "Acme acted."}], "metadata": {}}
    encoded = json.dumps(build_messages("system", sample))
    assert "secret-id" not in encoded


def test_metrics_and_sentiment() -> None:
    assert _binary_metrics([True, False], [True, True])["accuracy"] == 0.5
    assert _sentiment({"positive_implication_probability": 0.9, "negative_implication_probability": 0.8}) == "mixed"


def test_transport_schema_omits_unsupported_unique_items() -> None:
    assert "uniqueItems" not in json.dumps(TRANSPORT_SCHEMA)


def test_canonicalize_output_changes_only_order_and_duplicates() -> None:
    payload = {"schema_version": SCHEMA_VERSION, "issuers": [{"issuer_name": "Beta", "event_tags": ["legal", "earnings", "legal"], "issuer_roles": ["target", "primary_subject"], "evidence_sentence_ids": [2, 1, 2]}, {"issuer_name": "Alpha", "event_tags": [], "issuer_roles": [], "evidence_sentence_ids": [1]}], "unresolved_issuer_mentions": ["Zed", "Able", "Zed"]}
    fixed = canonicalize_output(payload)
    assert [row["issuer_name"] for row in fixed["issuers"]] == ["Alpha", "Beta"]
    assert fixed["issuers"][1]["event_tags"] == ["earnings", "legal"]
    assert fixed["unresolved_issuer_mentions"] == ["Able", "Zed"]


def test_strict_validator_rejects_extra_and_missing_fields() -> None:
    payload = {"schema_version": SCHEMA_VERSION, "issuers": [], "unresolved_issuer_mentions": [], "extra": True}
    assert "unexpected output fields" in " ".join(validate_output(payload, [1]))


def test_agreement_uses_discrete_thresholds_not_probability_equality() -> None:
    base = {
        "schema_version": SCHEMA_VERSION,
        "issuers": [{
            "issuer_name": "Acme", "ticker": "ACME", "exchange": None,
            "identity_source": "explicit_text", "identity_confidence_probability": 0.99,
            "forecast_relevance_probability": 0.51,
            "positive_implication_probability": 0.81,
            "negative_implication_probability": 0.10,
            "event_tags": ["earnings"], "issuer_roles": ["primary_subject"],
            "time_scope": "current", "claim_source": "issuer", "evidence_sentence_ids": [1],
        }],
        "unresolved_issuer_mentions": [],
    }
    other = json.loads(json.dumps(base))
    other["issuers"][0]["forecast_relevance_probability"] = 0.93
    other["issuers"][0]["positive_implication_probability"] = 0.55
    other["issuers"][0]["evidence_sentence_ids"] = [2]
    result = compare_labels(base, other)
    assert result["discrete_agreement"] is True
    assert result["evidence_disagreements"]
    assert any(row["tolerance_band"] == ">0.25" for row in result["probability_differences"])


def test_synthetic_restart_safe_controller_path() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        result = synthetic_dry_run(Path(temporary))
        assert result["first_ingest"]["status"] == "validated"
        assert result["restart_ingest"]["status"] == "already_validated"
        assert result["validation"]["status"] == "complete"
