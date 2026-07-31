from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from .annotation_template import annotation_template
from .sampling import distribute_quota, select_candidates
from .schema import ANNOTATION_VERSION, validate_annotation
from .storage import assert_runtime_root, materialize_evidence_spans


class SemanticCalibrationSchemaTests(unittest.TestCase):
    def test_complete_positive_annotation_is_valid(self) -> None:
        item = {
            "sample_id": "N0001",
            "source_id": "source",
            "source_timestamp": "2026-01-01 12:00:00.000000000",
            "source_text_sha256": "a" * 64,
        }
        value = annotation_template(item)
        value["issuer_units"][0].update(
            {
                "ticker": "AAPL",
                "event_concepts": ["guidance.raise"],
                "evidence_quotes": ["raised guidance"],
                "evidence_spans": [
                    {
                        "source_field": "rendered_text",
                        "start": 0,
                        "end": 15,
                        "quote": "raised guidance",
                    }
                ],
                "positive_evidence_level": 3,
                "semantic_direction": "positive",
                "forecast_trigger_eligible": True,
                "reaction_evaluation_eligible": True,
                "annotation_confidence": 4,
                "semantic_rationale": "Forward guidance improved materially.",
            }
        )
        value["reviewer_confidence"] = 4
        result = validate_annotation(value, expected_item=item)
        self.assertTrue(result.valid, result.errors)

    def test_negative_confidence_is_rejected(self) -> None:
        item = {
            "sample_id": "N0001",
            "source_id": "source",
            "source_timestamp": "2026-01-01 12:00:00.000000000",
            "source_text_sha256": "a" * 64,
        }
        value = annotation_template(item)
        value["reviewer_confidence"] = -1
        result = validate_annotation(value, expected_item=item)
        self.assertIn("reviewer_confidence_must_be_0_to_4", result.errors)

    def test_runtime_root_rejects_repository(self) -> None:
        repository = Path(__file__).resolve().parents[3]
        with self.assertRaises(ValueError):
            assert_runtime_root(repository / "runtime")

    def test_quotes_are_materialized_to_exact_spans(self) -> None:
        annotation = {
            "issuer_units": [
                {
                    "ticker": "AAPL",
                    "evidence_quotes": ["raised guidance"],
                    "evidence_spans": [],
                }
            ]
        }
        item = {
            "publication": {"title": "Issuer raised guidance", "teaser": ""},
            "rendered_product": {"text": "Title: Issuer raised guidance"},
            "source_lanes": [],
        }
        result = materialize_evidence_spans(annotation, item)
        self.assertEqual(
            result["issuer_units"][0]["evidence_spans"],
            [
                {
                    "source_field": "title",
                    "start": 7,
                    "end": 22,
                    "quote": "raised guidance",
                }
            ],
        )

    def test_era_balancing_is_exact(self) -> None:
        rows = []
        for era_index, year in enumerate((2011, 2016, 2021, 2025)):
            for index in range(300):
                rows.append(
                    {
                        "source_id": f"{era_index}-{index}",
                        "source_timestamp": f"{year}-01-01 12:00:00",
                        "event": {"tickers": ["TST"]},
                        "v5_units": [
                            {
                                "content_role": "primary_event",
                                "semantic_direction": "positive",
                                "event_concepts": [f"concept.{index % 20}"],
                            }
                        ],
                    }
                )
        selected = select_candidates(rows, sample_size=1_000, rare_supplement=100)
        counts = {}
        for row in selected:
            year = int(row["source_timestamp"][:4])
            key = 2010 if year < 2015 else 2015 if year < 2020 else 2020 if year < 2024 else 2024
            counts[key] = counts.get(key, 0) + 1
        self.assertEqual(counts, {2010: 250, 2015: 250, 2020: 250, 2024: 250})
        self.assertEqual(distribute_quota(10, ("a", "b", "c")), {"a": 4, "b": 3, "c": 3})


if __name__ == "__main__":
    unittest.main()
