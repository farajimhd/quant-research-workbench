from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from .fresh_acceptance_audit import (
    GatewaySourceEvidence,
    _gateway_retained_record_section,
    _metadata_payload,
    _normalized_unit,
    _payload_hash_verification_method,
    _provider_payload_hash,
    _raw_provider_article_id,
    _raw_provider_payload_section,
    _resolve_raw_artifact_path,
    _slug,
    _source_text_sections,
    _unit_field_outcome,
    _unit_presence_outcome,
)


class FreshAcceptanceAuditTests(unittest.TestCase):
    def test_normalized_unit_uses_benchmark_concept_families(self) -> None:
        unit = _normalized_unit(
            {
                "semantic_direction": "positive",
                "event_concepts": ["capital.buyback", "capital_return.buyback"],
                "forecast_trigger_eligible": True,
                "reaction_evaluation_eligible": True,
                "issuer_history_context_eligible": False,
            }
        )
        self.assertEqual(unit["event_concepts"], ("capital_return",))
        self.assertEqual(unit["semantic_direction"], "positive")
        self.assertEqual(unit["forecast_direction"], "positive")
        self.assertTrue(unit["forecast_trigger_eligible"])

    def test_missing_unit_is_explicit_and_not_silently_neutral(self) -> None:
        unit = _normalized_unit(None)
        self.assertEqual(unit["semantic_direction"], "not predicted")
        self.assertEqual(unit["forecast_direction"], "not applicable")
        self.assertEqual(unit["event_concepts"], ())
        human = _normalized_unit(None, missing_direction="not an issuer unit")
        self.assertEqual(human["semantic_direction"], "not an issuer unit")

    def test_slug_is_bounded_and_portable(self) -> None:
        slug = _slug("A/B: Very Long Headline? " * 10)
        self.assertLessEqual(len(slug), 78)
        self.assertNotIn("/", slug)
        self.assertTrue(slug.endswith(".audit"))

    def test_unit_presence_is_scored_once_and_absent_human_fields_are_na(self) -> None:
        self.assertEqual(_unit_presence_outcome(False, False), "match")
        self.assertEqual(_unit_presence_outcome(False, True), "diff")
        self.assertEqual(_unit_presence_outcome(True, False), "diff")
        self.assertEqual(_unit_presence_outcome(True, True), "match")
        self.assertEqual(
            _unit_field_outcome(
                False,
                field="forecast_trigger_eligible",
                truth_value="not applicable",
                predicted_value=False,
            ),
            "not_applicable",
        )

    def test_missing_prediction_compares_field_values_like_benchmark(self) -> None:
        self.assertEqual(
            _unit_field_outcome(
                True,
                field="event_concepts",
                truth_value=(),
                predicted_value=(),
            ),
            "match",
        )
        self.assertEqual(
            _unit_field_outcome(
                True,
                field="forecast_trigger_eligible",
                truth_value=False,
                predicted_value=False,
            ),
            "match",
        )
        self.assertEqual(
            _unit_field_outcome(
                True,
                field="semantic_direction",
                truth_value="neutral",
                predicted_value="not predicted",
            ),
            "diff",
        )

    def test_ineligible_unit_has_semantic_but_no_forecast_direction(self) -> None:
        unit = _normalized_unit(
            {
                "semantic_direction": "neutral",
                "event_concepts": [],
                "forecast_trigger_eligible": False,
                "reaction_evaluation_eligible": False,
                "issuer_history_context_eligible": True,
            }
        )
        self.assertEqual(unit["semantic_direction"], "neutral")
        self.assertEqual(unit["forecast_direction"], "not applicable")

    def test_metadata_payload_preserves_metadata_without_duplicating_text(self) -> None:
        payload = _metadata_payload(
            {
                "source_id": "source-1",
                "publication": {"title": "Title", "provider_tags": ["news"]},
                "source_lanes": [
                    {"source_kind": "provider_body", "source_ordinal": 0, "text": "body"}
                ],
                "rendered_product": {"text": "packed", "source_count": 1},
            }
        )
        self.assertEqual(payload["source_id"], "source-1")
        self.assertEqual(payload["publication"]["provider_tags"], ["news"])
        self.assertNotIn("text", payload["source_lanes"][0])
        self.assertEqual(payload["rendered_product"], {"source_count": 1})

    def test_source_text_sections_include_every_lane_in_ordinal_order(self) -> None:
        rendered = _source_text_sections(
            {
                "publication": {"title": "Original title", "teaser": "Original teaser"},
                "source_lanes": [
                    {
                        "source_kind": "external",
                        "source_ordinal": 2,
                        "text": "second body",
                    },
                    {
                        "source_kind": "provider_body",
                        "source_ordinal": 0,
                        "text": "first <body>",
                    },
                ]
            }
        )
        self.assertIn("Original title", rendered)
        self.assertIn("Original teaser", rendered)
        self.assertLess(rendered.index("provider_body:0"), rendered.index("external:2"))
        self.assertIn("first &lt;body&gt;", rendered)
        self.assertIn("second body", rendered)

    def test_source_text_sections_make_missing_body_explicit(self) -> None:
        rendered = _source_text_sections(
            {"publication": {"title": "Headline", "teaser": "Wire teaser"}}
        )
        self.assertIn("Headline", rendered)
        self.assertIn("Wire teaser", rendered)
        self.assertIn("No separate original source-body lane", rendered)

    def test_original_provider_payload_is_rendered_without_derivation(self) -> None:
        payload = {
            "id": 123,
            "published": "2020-01-02T03:04:05Z",
            "title": "Exact <provider> title",
            "unknown_provider_field": {"nested": [1, 2]},
        }
        rendered = _raw_provider_payload_section(
            GatewaySourceEvidence(
                retained_record={},
                raw_payload=payload,
                resolved_raw_artifact_path="raw.json",
                retained_payload_hash=_provider_payload_hash(payload),
                raw_artifact_byte_hash=_provider_payload_hash(payload),
                hash_verification_method="canonical_json_utf8",
            )
        )
        self.assertIn("unknown_provider_field", rendered)
        self.assertIn("Exact &lt;provider&gt; title", rendered)

    def test_raw_provider_identity_supports_historical_and_current_fields(self) -> None:
        self.assertEqual(_raw_provider_article_id({"benzinga_id": 123}), "123")
        self.assertEqual(_raw_provider_article_id({"id": 456.0}), "456")

    def test_hash_verification_supports_both_gateway_serialization_contracts(self) -> None:
        payload = {"id": 1, "title": "café"}
        ascii_hash = hashlib.blake2b(
            json.dumps(payload, sort_keys=True).encode("utf-8"), digest_size=16
        ).hexdigest()
        raw_bytes = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        raw_hash = hashlib.blake2b(raw_bytes, digest_size=16).hexdigest()
        self.assertEqual(
            _payload_hash_verification_method(
                payload,
                retained_hash=ascii_hash,
                raw_artifact_byte_hash=raw_hash,
            ),
            "canonical_json_ascii_escaped",
        )
        self.assertEqual(
            _payload_hash_verification_method(
                payload,
                retained_hash=raw_hash,
                raw_artifact_byte_hash=raw_hash,
            ),
            "exact_utf8_artifact_bytes",
        )

    def test_retained_gateway_record_includes_all_text_and_metadata_fields(self) -> None:
        record = {
            "provider_article_id": "123",
            "raw_payload_hash": "abc",
            "body_text": "body <text>",
            "external_text": "external",
            "pdf_text": "pdf",
            "normalized_full_text": "packed",
        }
        rendered = _gateway_retained_record_section(record)
        for field in (
            "provider_article_id",
            "raw_payload_hash",
            "body_text",
            "external_text",
            "pdf_text",
            "normalized_full_text",
        ):
            self.assertIn(field, rendered)
        self.assertIn("body &lt;text&gt;", rendered)

    def test_raw_path_map_resolves_the_retained_gateway_path(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "raw" / "2020" / "article.json"
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps({"id": 1}), encoding="utf-8")
            resolved = _resolve_raw_artifact_path(
                r"D:\market-data\raw\2020\article.json",
                [(r"D:\market-data", root)],
            )
            self.assertEqual(resolved, target)


if __name__ == "__main__":
    unittest.main()
