from __future__ import annotations

import unittest

from research.text_intelligence.news_synthesis_v1.contracts import validate_document
from research.text_intelligence.news_synthesis_v1.migration import migrate_record
from research.text_intelligence.news_synthesis_v1.registry import ConceptRegistry


class MigrationTest(unittest.TestCase):
    def test_mixed_sentiment_is_decomposed_and_requires_review(self) -> None:
        text = "Title: Buyer acquires Target\nBuyer acquires Target for $25 million."
        quote = "Buyer acquires Target for $25 million."
        start = text.index(quote)
        annotation = {
            "annotation_version": "news_semantic_ground_truth_annotation_v3",
            "sample_id": "NTEST",
            "source_id": "abc",
            "source_timestamp": "2026-01-01 12:00:00.000000000",
            "source_text_sha256": "a" * 64,
            "extraction_decision": "labeled",
            "content_role": "primary_event",
            "source_origin": "issuer_direct",
            "issuer_units": [
                {
                    "ticker": "BUY",
                    "issuer_role": "acquirer",
                    "event_concepts": ["acquisition"],
                    "evidence_spans": [
                        {"source_field": "rendered_text", "start": start, "end": start + len(quote), "quote": quote}
                    ],
                    "modality": "confirmed",
                    "time_orientation": "current",
                    "semantic_direction": "mixed",
                    "positive_evidence_level": 2,
                    "negative_evidence_level": 1,
                    "forecast_trigger_eligible": True,
                    "reaction_evaluation_eligible": True,
                    "issuer_history_context_eligible": True,
                    "analyst_evaluation_eligible": False,
                }
            ],
        }
        article = {
            "sample_id": "NTEST",
            "source_id": "abc",
            "source_timestamp": "2026-01-01 12:00:00.000000000",
            "source_text_sha256": "a" * 64,
            "publication": {"title": "Buyer acquires Target", "content_quality_flags": []},
            "point_in_time_issuer_candidates": [{"ticker": "BUY", "identity_evidence": ["issuer_alias:buyer", "symbol:BUY"]}],
            "rendered_product": {"text": text, "quality_flags": []},
        }
        document, audit = migrate_record(annotation, article, ConceptRegistry.load())
        self.assertEqual(audit["status"], "review_required")
        self.assertEqual({row["semantic_sentiment"] for row in document["participations"]}, {"positive", "negative"})
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")
        self.assertEqual(len(document["issuer_views"][0]["positive_statement_ids"]), 1)
        self.assertEqual(len(document["issuer_views"][0]["negative_statement_ids"]), 1)
        self.assertEqual(
            document["synthesis"]["renderer_version"],
            "news_synthesis_renderer_v1",
        )
        self.assertEqual(
            document["envelope"]["production_method"]["value"], "unknown"
        )
        self.assertTrue(validate_document(document).valid)

    def test_context_mention_cannot_inherit_sentiment(self) -> None:
        text = "Title: Context\nContext mentions ACME."
        quote = "Context mentions ACME."
        start = text.index(quote)
        annotation = {
            "annotation_version": "news_semantic_ground_truth_annotation_v3",
            "sample_id": "NTEST2",
            "source_id": "def",
            "source_timestamp": "2026-01-01 12:00:00.000000000",
            "source_text_sha256": "b" * 64,
            "extraction_decision": "labeled",
            "content_role": "editorial_analysis",
            "source_origin": "editorial_original",
            "issuer_units": [
                {
                    "ticker": "ACME", "issuer_role": "mentioned_subject",
                    "event_concepts": ["business_update"],
                    "evidence_spans": [{"source_field": "rendered_text", "start": start, "end": start + len(quote), "quote": quote}],
                    "modality": "confirmed", "time_orientation": "historical",
                    "semantic_direction": "positive", "positive_evidence_level": 3,
                }
            ],
        }
        article = {
            "sample_id": "NTEST2",
            "source_id": "def",
            "source_timestamp": "2026-01-01 12:00:00.000000000",
            "source_text_sha256": "b" * 64,
            "publication": {"title": "Context", "content_quality_flags": []},
            "point_in_time_issuer_candidates": [{"ticker": "ACME", "identity_evidence": ["symbol:ACME"]}],
            "rendered_product": {"text": text, "quality_flags": []},
        }
        document, _ = migrate_record(annotation, article, ConceptRegistry.load())
        participation = document["participations"][0]
        self.assertEqual(participation["semantic_role"], "none")
        self.assertEqual(participation["discourse_role"], "context_mention")
        self.assertEqual(participation["semantic_sentiment"], "neutral")
        self.assertEqual(participation["sentiment_strength"], 0)

    def test_registry_reports_resolution_provenance(self) -> None:
        registry = ConceptRegistry.load()
        self.assertEqual(
            registry.resolve("completed asset sale"),
            ("corporate_transaction.asset_sale", "heuristic"),
        )
        self.assertEqual(
            registry.resolve("short percent of float"),
            ("market.short_interest_observed", "heuristic"),
        )
        self.assertEqual(
            registry.resolve("listing.minimum_bid_deficiency"),
            ("listing.market_structure", "exact_alias"),
        )
        self.assertEqual(
            registry.resolve("unknown_new_concept"),
            ("unclassified.semantic_claim", "fallback"),
        )

    def test_source_identity_mismatch_fails_closed(self) -> None:
        annotation = {
            "annotation_version": "news_semantic_ground_truth_annotation_v3",
            "sample_id": "N1", "source_id": "A", "source_timestamp": "T",
            "source_text_sha256": "a" * 64,
        }
        article = {
            "sample_id": "N1", "source_id": "B", "source_timestamp": "T",
            "source_text_sha256": "a" * 64,
        }
        with self.assertRaisesRegex(RuntimeError, "source_id"):
            migrate_record(annotation, article, ConceptRegistry.load())


if __name__ == "__main__":
    unittest.main()
