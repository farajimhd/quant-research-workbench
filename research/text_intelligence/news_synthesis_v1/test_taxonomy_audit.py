from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from .taxonomy_audit import AuditConfig, audit_gold_authority, render_markdown


class TaxonomyAuditTest(unittest.TestCase):
    def test_paired_authority_is_audited_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root) / "collection"
            annotation_root = root / "annotations_v3"
            article_root = root / "blinded_articles"
            annotation_root.mkdir(parents=True)
            article_root.mkdir(parents=True)
            identity = {
                "sample_id": "N0001",
                "source_id": "source-1",
                "source_timestamp": "2026-01-01 14:00:00.000000000",
                "source_text_sha256": "abc",
            }
            annotation = {
                **identity,
                "extraction_decision": "labeled",
                "content_role": "analyst_event",
                "source_origin": "analyst_research",
                "issuer_units": [
                    {
                        "ticker": "AAA",
                        "issuer_role": "analyst_subject",
                        "evidence_scope": "ticker_specific",
                        "event_concepts": ["rating_upgrade"],
                        "evidence_quotes": ["upgraded"],
                        "modality": "opinion",
                        "time_orientation": "forward",
                        "semantic_direction": "positive",
                        "forecast_trigger_eligible": False,
                        "reaction_evaluation_eligible": False,
                        "issuer_history_context_eligible": True,
                        "analyst_context_eligible": True,
                        "analyst_evaluation_eligible": True,
                        "analyst_opinions": [{"rating_action": "upgraded"}],
                    }
                ],
                "ticker_dispositions": [{"ticker": "AAA", "disposition": "labeled_issuer_unit"}],
            }
            article = {**identity, "publication": {"title": "AAA upgraded"}}
            (annotation_root / "N0001.json").write_text(json.dumps(annotation), encoding="utf-8")
            (article_root / "N0001.json").write_text(json.dumps(article), encoding="utf-8")

            result = audit_gold_authority(
                AuditConfig(collection_roots=(root,), output_root=Path(raw_root) / "out", expected_articles=1)
            )

            self.assertEqual(result["source"]["articles"], 1)
            self.assertEqual(result["source"]["issuer_units"], 1)
            self.assertEqual(result["contract_findings"]["decision_unit_inconsistencies"], [])
            self.assertIn("2,000-Gold Taxonomy Audit", render_markdown(result))


if __name__ == "__main__":
    unittest.main()
