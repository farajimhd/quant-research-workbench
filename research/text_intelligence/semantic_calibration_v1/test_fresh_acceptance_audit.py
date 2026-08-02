from __future__ import annotations

import unittest

from .fresh_acceptance_audit import _normalized_unit, _slug, _unit_field_matches


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


if __name__ == "__main__":
    unittest.main()
