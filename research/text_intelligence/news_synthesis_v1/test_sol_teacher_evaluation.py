from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from .contracts import validate_document
from .sol_teacher_evaluation import (
    _evaluation_scope_tickers,
    _reusable_converted_document,
    compare_eligible_directions,
    convert_sol_teacher_label,
    write_json_atomic,
)


class SolTeacherEvaluationTests(unittest.TestCase):
    def test_evaluation_scope_uses_eligible_identity_without_direction(self) -> None:
        document = convert_sol_teacher_label(
            self._article(), self._label(direction="negative")
        )
        self.assertEqual(_evaluation_scope_tickers(document), ("AAA",))

    def test_conversion_preserves_mixed_direction_and_current_eligibility(self) -> None:
        article = self._article()
        label = self._label(direction="mixed")

        document = convert_sol_teacher_label(article, label)

        self.assertTrue(validate_document(document).valid)
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "mixed")
        eligible = {
            row["product"]: row["eligible"] for row in document["eligibility"]
        }
        self.assertEqual(
            eligible,
            {
                "forecast_trigger": True,
                "reaction_study": True,
                "issuer_history": True,
                "analyst_evaluation": True,
            },
        )
        self.assertEqual(document["migration"]["status"], "review_required")
        self.assertIn("sol_teacher_derived_unreviewed", document["quality_flags"])

    def test_conversion_rejects_duplicate_teacher_instrument_units(self) -> None:
        article = self._article()
        label = self._label(direction="positive")
        label["labels"].append(dict(label["labels"][0]))

        with self.assertRaisesRegex(RuntimeError, "duplicate Sol instrument"):
            convert_sol_teacher_label(article, label)

    def test_converted_cache_is_invalidated_by_changed_source_identity(self) -> None:
        article = self._article()
        label = self._label(direction="positive")
        document = convert_sol_teacher_label(article, label)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "S00001.json"
            write_json_atomic(path, document)
            changed = dict(article)
            changed["source_id"] = "source-2"

            self.assertIsNone(
                _reusable_converted_document(path, changed, label)
            )

    def test_teacher_direction_is_not_reinterpreted_by_current_aggregation_policy(self) -> None:
        article = self._article()
        article["rendered_product"]["text"] = (
            "Title: Alpha prices IPO\nAlpha Corp (NASDAQ:AAA) prices its IPO."
        )
        label = self._label(direction="neutral")
        label["labels"][0]["classification"]["event_concepts"] = ["financing"]

        document = convert_sol_teacher_label(article, label)

        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "neutral")
        self.assertEqual(document["issuer_views"][0]["positive_strength"], 0)
        self.assertEqual(document["issuer_views"][0]["negative_strength"], 0)

    def test_market_observation_only_is_neutral_and_not_forecast_eligible(self) -> None:
        article = self._article()
        label = self._label(direction="positive")
        label["labels"][0]["classification"]["event_concepts"] = ["market_reaction"]

        document = convert_sol_teacher_label(article, label)

        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "neutral")
        self.assertTrue(all(
            row["semantic_sentiment"] == "neutral" and row["sentiment_strength"] == 0
            for row in document["participations"]
        ))
        forecast = next(
            row for row in document["eligibility"]
            if row["product"] == "forecast_trigger"
        )
        self.assertFalse(forecast["eligible"])
        self.assertEqual(forecast["blocking_flags"], ["market_observation_only"])

    def test_direction_comparison_uses_gold_eligible_scope_and_tracks_missing(self) -> None:
        gold = convert_sol_teacher_label(self._article(), self._label(direction="negative"))
        prediction = {
            "entities": [{"entity_id": "prediction:AAA", "ticker": "AAA"}],
            "issuer_views": [{
                "entity_id": "prediction:AAA",
                "composite_sentiment": "negative",
            }],
            "eligibility": [{
                "entity_id": "prediction:AAA",
                "product": "forecast_trigger",
                "eligible": True,
            }],
        }

        comparison = compare_eligible_directions(
            {"S00001": gold},
            {"S00001": prediction},
            missing_label_ids=("S00002", "S00003", "S00004"),
        )

        self.assertEqual(comparison["missing_teacher_label_articles"], 3)
        self.assertEqual(comparison["forecast_eligible"]["units"], 1)
        self.assertEqual(comparison["forecast_eligible"]["exact"], 1)
        self.assertEqual(comparison["forecast_eligible"]["predicted_eligible_units"], 1)
        self.assertEqual(comparison["forecast_eligible"]["end_to_end_exact"], 1)
        self.assertEqual(comparison["analyst_eligible"]["exact"], 1)
        self.assertEqual(comparison["union_eligible"]["units"], 1)

    def test_direction_only_and_end_to_end_accuracy_are_separate(self) -> None:
        gold = convert_sol_teacher_label(self._article(), self._label(direction="negative"))
        prediction = {
            "entities": [{"entity_id": "prediction:AAA", "ticker": "AAA"}],
            "issuer_views": [{
                "entity_id": "prediction:AAA",
                "composite_sentiment": "negative",
            }],
            "eligibility": [],
        }

        metrics = compare_eligible_directions(
            {"S00001": gold}, {"S00001": prediction}
        )["forecast_eligible"]

        self.assertEqual(metrics["exact"], 1)
        self.assertEqual(metrics["predicted_eligible_units"], 0)
        self.assertEqual(metrics["end_to_end_exact"], 0)

    @staticmethod
    def _article() -> dict:
        text = "Title: Alpha reports results\nAlpha Corp (NASDAQ:AAA) raised guidance."
        return {
            "sample_id": "S00001",
            "source_id": "source-1",
            "source_timestamp": "2026-08-07T12:00:00Z",
            "source_text_sha256": "a" * 64,
            "publication": {
                "title": "Alpha reports results",
                "provider_tickers": ["AAA"],
            },
            "point_in_time_issuer_candidates": [{
                "canonical_instrument_id": "AAA",
                "display_symbol": "AAA",
                "instrument_type": "us_equity_or_fund",
                "identity_evidence": ["issuer_alias:alpha corp"],
            }],
            "rendered_product": {
                "text": text,
                "source_count": 1,
                "quality_flags": [],
            },
        }

    @staticmethod
    def _label(*, direction: str) -> dict:
        return {
            "sample_id": "S00001",
            "source_id": "source-1",
            "extraction_decision": "labeled",
            "content_role": "analyst_event",
            "source_origin": "analyst_research",
            "teacher_label_version": "news_sol_teacher_labels_v1",
            "teacher_corpus_version": "news_sol_teacher_corpus_v1",
            "labels": [{
                "ticker": "AAA",
                "canonical_instrument_id": "AAA",
                "classification": {
                    "content_role": "analyst_event",
                    "source_origin": "analyst_research",
                    "event_concepts": ["analyst_action", "guidance"],
                    "semantic_direction": direction,
                },
                "forecast_trigger_eligible": True,
                "reaction_evaluation_eligible": True,
                "issuer_history_context_eligible": True,
            }],
        }


if __name__ == "__main__":
    unittest.main()
