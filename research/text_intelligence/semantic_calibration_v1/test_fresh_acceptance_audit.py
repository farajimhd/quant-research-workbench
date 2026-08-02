from __future__ import annotations

import unittest

from .fresh_acceptance_audit import (
    _metadata_payload,
    _normalized_unit,
    _slug,
    _source_text_sections,
    _unit_field_matches,
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
        self.assertTrue(unit["forecast_trigger_eligible"])

    def test_missing_unit_is_explicit_and_not_silently_neutral(self) -> None:
        unit = _normalized_unit(None)
        self.assertEqual(unit["semantic_direction"], "not predicted")
        self.assertEqual(unit["event_concepts"], ())
        human = _normalized_unit(None, missing_direction="not an issuer unit")
        self.assertEqual(human["semantic_direction"], "not an issuer unit")

    def test_slug_is_bounded_and_portable(self) -> None:
        slug = _slug("A/B: Very Long Headline? " * 10)
        self.assertLessEqual(len(slug), 78)
        self.assertNotIn("/", slug)
        self.assertTrue(slug.endswith(".audit"))

    def test_unit_comparison_treats_two_absences_as_match(self) -> None:
        self.assertTrue(
            _unit_field_matches(False, False, "not an issuer unit", "not predicted")
        )
        self.assertFalse(
            _unit_field_matches(False, True, "not an issuer unit", "neutral")
        )
        self.assertFalse(_unit_field_matches(True, False, "positive", "not predicted"))
        self.assertTrue(_unit_field_matches(True, True, "positive", "positive"))

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


if __name__ == "__main__":
    unittest.main()
