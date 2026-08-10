from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from .gold_label_consolidation import (
    _normalize_forecast_unit,
    _normalize_manual_units,
    _normalize_sol_unit,
    _write_dataset,
    validate_consolidated_gold,
)


class GoldLabelConsolidationTest(unittest.TestCase):
    def test_manual_normalization_preserves_missing_eligible_sentiment(self) -> None:
        document = {
            "sample_id": "N1",
            "source_id": "source-1",
            "entities": [{
                "entity_id": "security:AAA",
                "entity_kind": "security",
                "ticker": "AAA",
                "identity_status": "resolved",
            }],
            "issuer_views": [],
            "eligibility": [{
                "entity_id": "security:AAA",
                "product": "forecast_trigger",
                "eligible": True,
                "reasons": ["eligible_under:forecast_trigger"],
            }],
        }
        units = _normalize_manual_units(document)
        self.assertEqual(units[0]["forecast_eligibility"], "eligible")
        self.assertEqual(units[0]["sentiment"], "unknown")
        self.assertEqual(units[0]["normalization_status"], "missing_eligible_sentiment")

    def test_sol_normalization_marks_every_direction_unit_eligible(self) -> None:
        unit = _normalize_sol_unit({
            "unit_id": "S1::AAA",
            "source_id": "source-1",
            "ticker": "AAA",
            "entity_id": "sol-teacher-security:AAA",
            "gold_sentiment": "neutral",
            "concepts": ["earnings.performance"],
        })
        self.assertEqual(unit["forecast_eligibility"], "eligible")
        self.assertEqual(unit["sentiment"], "neutral")

    def test_forecast_normalization_rejects_unresolved_gold(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Unresolved forecast gold unit"):
            _normalize_forecast_unit({
                "ticker": "AAA",
                "identity_status": "resolved_focal_issuer",
                "forecast_eligibility": "policy_uncertain",
                "sentiment": "policy_uncertain",
            }, "source-1")

    def test_dataset_writer_and_validator_preserve_sealed_policy(self) -> None:
        authority = {
            "authority_id": "test-authority",
            "authority_version": "test-v1",
            "certification_level": "test",
            "partition": "sealed_test",
            "usage_policy": "final_evaluation_only",
            "articles": 1,
            "root_relative_path": "test",
            "manifest_relative_path": "test/manifest.json",
            "manifest_sha256": "a" * 64,
        }
        unit = {
            "unit_id": "source-1::security:AAA",
            "authority_unit_id": "S1::AAA",
            "ticker": "AAA",
            "entity_id": "security:AAA",
            "entity_kind": "security",
            "identity_status": "resolved",
            "forecast_eligibility": "eligible",
            "sentiment": "positive",
            "reason_codes": [],
            "concepts": [],
            "gold_resolution": "test",
            "normalization_status": "complete",
        }
        record = {
            "source_id": "source-1",
            "source_timestamp": "2026-01-01T00:00:00Z",
            "authority_article_id": "S1",
            "authority_id": "test-authority",
            "authority_version": "test-v1",
            "certification_level": "test",
            "partition": "sealed_test",
            "usage_policy": "final_evaluation_only",
            "source_hashes": {"source_text_sha256": "b" * 64},
            "article_forecast_eligible": True,
            "issuer_units": [unit],
            "lineage": {
                "source_relative_path": "test/data.json",
                "source_artifact_sha256": "c" * 64,
                "authority_manifest_sha256": "a" * 64,
            },
            "raw_gold_payload": {},
        }
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_dataset(root, [(authority, iter([record]))])
            validation = validate_consolidated_gold(root)
            self.assertEqual(validation["status"], "pass")
            self.assertEqual(validation["sealed_test_articles"], 1)


if __name__ == "__main__":
    unittest.main()
