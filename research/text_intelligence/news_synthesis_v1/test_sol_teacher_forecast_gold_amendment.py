from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from .sol_teacher_evaluation import load_json, write_json_atomic
from .sol_teacher_forecast_gold_amendment import amend_reviewed_audit_gold


class GoldAmendmentTests(unittest.TestCase):
    def test_amendment_is_validated_and_preserves_base_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = root / "base"
            base.mkdir()
            write_json_atomic(base / "reviewed_audit_set.json", {
                "version": "v1", "article_ids": ["S1"], "articles": [],
                "units": [{"unit_id": "S1::AAA", "gold_sentiment": "positive",
                           "gold_review_sha256": "old"}], "balance": {},
            })
            write_json_atomic(base / "manifest.json", {"version": "v1"})
            amendments = root / "amendments.json"
            write_json_atomic(amendments, [{
                "unit_id": "S1::AAA", "expected_direction": "positive",
                "corrected_direction": "neutral", "prediction_blind": True,
                "dominant_evidence": "Only a presentation was announced.",
                "rationale": "No result direction was disclosed.", "confidence": "high",
            }])
            manifest = amend_reviewed_audit_gold(base, amendments, root / "output")
            reviewed = load_json(root / "output" / "reviewed_audit_set.json")
            self.assertEqual(reviewed["units"][0]["gold_sentiment"], "neutral")
            self.assertEqual(manifest["population"]["amendments"], 1)
            self.assertEqual(
                reviewed["units"][0]["gold_resolution"],
                "post_audit_source_review_correction",
            )


if __name__ == "__main__":
    unittest.main()
