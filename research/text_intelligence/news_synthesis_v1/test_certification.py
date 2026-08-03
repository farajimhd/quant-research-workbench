from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from research.text_intelligence.news_synthesis_v1.certification import (
    CertificationConfig,
    certify_document,
    render_review_packet,
)
from research.text_intelligence.news_synthesis_v1.contracts import validate_document
from research.text_intelligence.news_synthesis_v1.migration import migrate_record
from research.text_intelligence.news_synthesis_v1.registry import ConceptRegistry


class CertificationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.annotation = {
            "annotation_version": "news_semantic_ground_truth_annotation_v3",
            "sample_id": "NTEST",
            "source_id": "source-1",
            "source_timestamp": "2026-01-01T12:00:00Z",
            "source_text_sha256": "a" * 64,
            "content_role": "primary_event",
            "source_origin": "issuer_direct",
            "extraction_decision": "labeled",
            "issuer_units": [{
                "ticker": "ABC",
                "event_concepts": ["earnings"],
                "semantic_direction": "positive",
                "semantic_score": 1.0,
                "evidence_spans": [{"source_field": "rendered_text", "start": 0, "end": 25, "quote": "ABC reported record sales"}],
                "forecast_trigger_eligible": True,
                "reaction_study_eligible": True,
                "issuer_history_eligible": True,
            }],
        }
        self.article = {
            "sample_id": "NTEST",
            "source_id": "source-1",
            "source_timestamp": "2026-01-01T12:00:00Z",
            "source_text_sha256": "a" * 64,
            "publication": {"title": "ABC reported record sales", "provider_tickers": ["ABC"]},
            "rendered_product": {"text": "ABC reported record sales"},
            "point_in_time_issuer_candidates": [{"ticker": "ABC", "identity_evidence": ["ABC Corp"]}],
        }
        self.draft, _audit = migrate_record(self.annotation, self.article, ConceptRegistry.load())

    def test_review_packet_excludes_legacy_label_fields(self) -> None:
        packet = render_review_packet(self.article, self.draft)
        for legacy in ("semantic_direction", "content_role", "source_origin", "issuer_units"):
            self.assertNotIn(legacy, packet)

    def test_certification_is_v1_only_and_source_bound(self) -> None:
        self.draft["quality_flags"] = []
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            collection = root / "collection"
            article_root = collection / "blinded_articles"
            annotation_root = collection / "annotations_v3"
            article_root.mkdir(parents=True)
            annotation_root.mkdir()
            (article_root / "NTEST.json").write_text(json.dumps(self.article), encoding="utf-8")
            (annotation_root / "NTEST.json").write_text(json.dumps(self.annotation), encoding="utf-8")
            config = CertificationConfig(root / "draft.jsonl", (collection,), root / "out", expected_articles=1)
            certified = certify_document(config, "NTEST", self.draft, reviewer="Codex", review_notes="Source and V1 primitives reviewed.")
            self.assertNotIn("migration", certified)
            self.assertEqual(certified["certification"]["status"], "certified")
            self.assertTrue(validate_document(certified).valid)

    def test_unresolved_quality_flags_block_certification(self) -> None:
        self.draft["quality_flags"] = ["unresolved_identity"]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            collection = root / "collection"
            article_root = collection / "blinded_articles"
            annotation_root = collection / "annotations_v3"
            article_root.mkdir(parents=True)
            annotation_root.mkdir()
            (article_root / "NTEST.json").write_text(json.dumps(self.article), encoding="utf-8")
            (annotation_root / "NTEST.json").write_text(json.dumps(self.annotation), encoding="utf-8")
            config = CertificationConfig(root / "draft.jsonl", (collection,), root / "out", expected_articles=1)
            with self.assertRaisesRegex(RuntimeError, "unresolved quality flags"):
                certify_document(config, "NTEST", self.draft, reviewer="Codex", review_notes="Reviewed.")

    def test_source_evidence_mismatch_blocks_certification(self) -> None:
        self.draft["quality_flags"] = []
        self.draft["statements"][0]["evidence_spans"][0]["quote"] = "fabricated"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            collection = root / "collection"
            article_root = collection / "blinded_articles"
            annotation_root = collection / "annotations_v3"
            article_root.mkdir(parents=True)
            annotation_root.mkdir()
            (article_root / "NTEST.json").write_text(json.dumps(self.article), encoding="utf-8")
            (annotation_root / "NTEST.json").write_text(json.dumps(self.annotation), encoding="utf-8")
            config = CertificationConfig(root / "draft.jsonl", (collection,), root / "out", expected_articles=1)
            with self.assertRaisesRegex(RuntimeError, "evidence_mismatch"):
                certify_document(config, "NTEST", self.draft, reviewer="Codex", review_notes="Reviewed.")


if __name__ == "__main__":
    unittest.main()
