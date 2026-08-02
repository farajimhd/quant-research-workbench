from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from .comparison import (
    CollectionItem,
    canonical_concept_family,
    compare_article_fields,
    compare_issuer_units,
    evaluate_predictions,
)


class CanonicalConceptFamilyTests(unittest.TestCase):
    def test_precise_human_concepts_map_to_broad_families(self) -> None:
        self.assertEqual(canonical_concept_family("guidance.raise"), "guidance")
        self.assertEqual(canonical_concept_family("analyst.price_target_raise"), "analyst_action")
        self.assertEqual(canonical_concept_family("registered_direct_offering"), "financing")
        self.assertEqual(canonical_concept_family("clinical.fda_approval"), "regulatory")

    def test_unknown_concepts_are_excluded_from_family_scoring(self) -> None:
        self.assertEqual(canonical_concept_family("unmapped.precise_judgment"), "")


class IssuerComparisonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.human = {
            "AYTU": {
                "semantic_direction": "neutral",
                "event_concepts": [],
                "forecast_trigger_eligible": False,
                "reaction_evaluation_eligible": False,
                "issuer_history_context_eligible": True,
            }
        }

    def test_binary_false_false_is_true_negative_not_diff(self) -> None:
        predicted = {
            "AYTU": {
                **self.human["AYTU"],
                "issuer_history_context_eligible": False,
            }
        }
        outcomes = {
            row.dimension: row for row in compare_issuer_units(self.human, predicted)
        }
        self.assertEqual(outcomes["forecast_trigger_eligible"].category, "TN")
        self.assertEqual(outcomes["forecast_trigger_eligible"].status, "match")
        self.assertIn(
            "eligibility.forecast_trigger_eligible",
            outcomes["forecast_trigger_eligible"].metrics,
        )

    def test_article_comparison_matches_evaluator_extraction_semantics(self) -> None:
        truth = {
            "extraction_decision": "labeled",
            "content_role": "regulatory_event",
            "source_origin": "editorial_original",
        }
        prediction = {
            "extraction_decision": "labeled",
            "content_role": "regulatory_event",
            "source_origin": "editorial_original",
            "labels": [],
        }
        outcomes = {
            row.dimension: row for row in compare_article_fields(truth, prediction)
        }
        self.assertEqual(outcomes["extraction_presence"].category, "FN")
        self.assertEqual(outcomes["extraction_presence"].status, "diff")
        self.assertEqual(outcomes["extraction_decision"].status, "match")

    def test_forecast_direction_is_dependency_gated(self) -> None:
        outcomes = {
            row.dimension: row for row in compare_issuer_units(self.human, {})
        }
        forecast = outcomes["forecast_direction"]
        self.assertEqual(forecast.category, "NOT SCORED")
        self.assertEqual(forecast.reason, "human_forecast_ineligible")
        self.assertFalse(forecast.scored)

    def test_missing_human_issuer_is_not_given_fabricated_field_labels(self) -> None:
        predicted = {
            "NDAQ": {
                "semantic_direction": "neutral",
                "event_concepts": [],
                "forecast_trigger_eligible": False,
                "reaction_evaluation_eligible": False,
                "issuer_history_context_eligible": False,
            }
        }
        outcomes = {
            row.dimension: row
            for row in compare_issuer_units({}, predicted, ticker_universe={"NDAQ"})
        }
        self.assertEqual(outcomes["issuer_presence"].category, "FP")
        self.assertEqual(outcomes["semantic_direction"].status, "not_scored")
        self.assertEqual(
            outcomes["forecast_trigger_eligible"].status, "not_scored"
        )

    def test_extra_actionable_issuer_is_end_to_end_false_positive(self) -> None:
        predicted = {
            "NDAQ": {
                "semantic_direction": "positive",
                "event_concepts": ["analyst.price_target_raise"],
                "forecast_trigger_eligible": True,
                "reaction_evaluation_eligible": True,
                "issuer_history_context_eligible": True,
            }
        }
        outcomes = {
            row.dimension: row for row in compare_issuer_units({}, predicted)
        }
        forecast = outcomes["forecast_trigger_eligible"]
        self.assertEqual(forecast.category, "FP")
        self.assertEqual(forecast.status, "diff")
        self.assertEqual(
            forecast.metrics,
            ("eligibility_end_to_end.forecast_trigger_eligible",),
        )

    def test_canonical_concept_outcome_exposes_set_counts(self) -> None:
        predicted = {
            "AYTU": {
                **self.human["AYTU"],
                "event_concepts": ["capital.buyback"],
            }
        }
        outcomes = {
            row.dimension: row
            for row in compare_issuer_units(
                self.human, predicted, canonical_concepts=True
            )
        }
        self.assertEqual(outcomes["event_concepts"].category, "TP=0 FP=1 FN=0")
        self.assertEqual(outcomes["event_concepts"].status, "diff")

    def test_evaluator_uses_shared_binary_and_error_outcomes(self) -> None:
        item = CollectionItem(
            sample_id="NTEST",
            split="fresh_acceptance",
            blinded={},
            truth={
                "extraction_decision": "labeled",
                "content_role": "regulatory_event",
                "source_origin": "editorial_original",
                "issuer_units": [{"ticker": "AYTU", **self.human["AYTU"]}],
            },
        )
        prediction = {
            "sample_id": "NTEST",
            "extraction_decision": "labeled",
            "content_role": "regulatory_event",
            "source_origin": "editorial_original",
            "labels": [
                {
                    "ticker": "AYTU",
                    "classification": {
                        "semantic_direction": "neutral",
                        "event_concepts": [],
                        "forecast_trigger_eligible": False,
                        "reaction_evaluation_eligible": False,
                        "issuer_history_context_eligible": False,
                    },
                }
            ],
        }
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            (path / "NTEST.json").write_text(json.dumps(prediction), encoding="utf-8")
            report = evaluate_predictions([item], prediction_dir=path)
        forecast = report["eligibility"]["forecast_trigger_eligible"]
        self.assertEqual(forecast["true_negative"], 1)
        self.assertEqual(forecast["false_positive"], 0)
        self.assertEqual(report["error_articles"], 1)
        self.assertIn(
            "issuer_history_context_eligible:AYTU:FN",
            report["errors"][0]["errors"],
        )


if __name__ == "__main__":
    unittest.main()
