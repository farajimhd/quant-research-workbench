from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from .fresh_acceptance_v2_audit_review import record_audit_reviews


class FreshAcceptanceV2AuditReviewTest(unittest.TestCase):
    def test_records_explicit_review_and_refreshes_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for folder in (
                "blinded_articles",
                "annotations_v3",
                "evaluation/v9_predictions",
                "article_audits/articles",
            ):
                (root / folder).mkdir(parents=True)
            for path, value in (
                (root / "blinded_articles/N1101.json", {"sample_id": "N1101"}),
                (root / "annotations_v3/N1101.json", {"sample_id": "N1101"}),
                (root / "evaluation/v9_predictions/N1101.json", {"sample_id": "N1101"}),
            ):
                path.write_text(json.dumps(value), encoding="utf-8")
            (root / "article_audits/articles/N1101_case.audit.md").write_text("# audit", encoding="utf-8")
            result = record_audit_reviews(root, [{
                "sample_id": "N1101",
                "gold_status": "pass",
                "v9_status": "fix_required",
                "metadata_status": "pass",
                "source_status": "pass",
                "issue_codes": ["v9_direction_missed"],
                "proposed_fix_families": ["direction_rules"],
                "notes": "Gold is supported; V9 misses explicit negative guidance.",
            }])
            self.assertEqual(result["state"]["reviewed_count"], 1)
            self.assertEqual(result["state"]["v9_fixes_required"], 1)
            self.assertTrue((root / "manual_audit_review_v1/N1101.json").is_file())

            second = record_audit_reviews(
                root,
                [{
                    "sample_id": "N1101",
                    "gold_status": "pass",
                    "v9_status": "pass",
                    "metadata_status": "pass",
                    "source_status": "pass",
                    "issue_codes": [],
                    "proposed_fix_families": [],
                    "notes": "Post-fix review confirms the certified prediction.",
                }],
                review_name="manual_audit_review_v2_postfix",
                contract="news_fresh_acceptance_v2_manual_audit_review_v2",
            )
            self.assertEqual(second["state"]["review_name"], "manual_audit_review_v2_postfix")
            self.assertTrue(
                (root / "manual_audit_review_v2_postfix/N1101.json").is_file()
            )


if __name__ == "__main__":
    unittest.main()
