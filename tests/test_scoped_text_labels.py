from __future__ import annotations

import json
import unittest

from src.backend.scoped_text_labels import (
    SCOPED_LABELING_VERSION,
    load_scoped_news_labels,
    scoped_news_summary,
)


class ScopedTextLabelPresentationTests(unittest.TestCase):
    def test_loader_groups_issuer_scoped_rows_without_exposing_sql_details(self) -> None:
        queries: list[str] = []

        def query_rows(sql: str) -> list[dict]:
            queries.append(sql)
            return [
                {
                    "source_id": "article-1",
                    "unit_id": "unit-a",
                    "ticker": "AAA",
                    "unit_role": "issuer",
                    "event_id": "event-1",
                    "event_tickers": ["AAA", "BBB"],
                    "issuer_role": "acquirer",
                    "evidence_scope": "whole_document",
                    "semantic_evidence_text": "AAA agreed to acquire BBB.",
                    "content_role": "corporate_transaction",
                    "source_origin": "editorial",
                    "event_concepts": ["acquisition"],
                    "semantic_direction": "positive",
                    "semantic_score": 0.7,
                    "forecast_trigger_eligible": 1,
                    "reaction_evaluation_eligible": 1,
                    "issuer_history_context_eligible": 1,
                    "classification_json": json.dumps({
                        "confidence": 0.93,
                        "episode_followup_eligible": False,
                        "issuer_relationship": "transaction_party",
                        "modality": "confirmed",
                        "prior_primary_context_eligible": True,
                        "quality_flags": ["issuer_context_direction_v4"],
                        "scope": "single_ticker",
                        "semantic_direction_basis": ["Transaction.Acquisition:Positive"],
                        "source_subtype": "reported_transaction",
                        "source_type": "news",
                        "time_orientation": "current",
                    }),
                    "labeling_version": SCOPED_LABELING_VERSION,
                }
            ]

        grouped = load_scoped_news_labels(
            ["article-1", "article-1"],
            query_rows=query_rows,
            quote=lambda value: f"'{value}'",
            source_end="2026-07-31T16:00:00+00:00",
            source_start="2026-07-28T16:00:00+00:00",
            ticker="aaa",
        )

        self.assertEqual(list(grouped), ["article-1"])
        label = grouped["article-1"][0]
        self.assertEqual(set(label), {
            "confidence", "content_role", "episode_followup_eligible",
            "event_concepts", "event_id", "event_tickers", "evidence_scope",
            "forecast_trigger_eligible", "issuer_history_context_eligible",
            "issuer_relationship", "issuer_role", "labeling_version", "modality",
            "prior_primary_context_eligible", "quality_flags",
            "reaction_evaluation_eligible", "scope", "semantic_direction",
            "semantic_direction_basis", "semantic_evidence_text", "semantic_score",
            "source_origin", "source_subtype", "source_type", "ticker",
            "time_orientation", "unit_id", "unit_role",
        })
        self.assertEqual(label["ticker"], "AAA")
        self.assertEqual(label["modality"], "confirmed")
        self.assertEqual(
            label["semantic_direction_basis"],
            ["Transaction.Acquisition:Positive"],
        )
        self.assertEqual(label["source_type"], "news")
        self.assertEqual(
            label["source_subtype"], "reported_transaction"
        )
        self.assertEqual(
            label["issuer_relationship"], "transaction_party"
        )
        self.assertTrue(label["prior_primary_context_eligible"])
        self.assertIn("scoped_text_labels_v5", queries[0])
        self.assertIn(SCOPED_LABELING_VERSION, queries[0])
        self.assertIn("PREWHERE corpus='news' AND ticker='AAA'", queries[0])
        self.assertIn("source_timestamp >= parseDateTime64BestEffort", queries[0])
        self.assertIn("source_timestamp <= parseDateTime64BestEffort", queries[0])
        self.assertIn("LIMIT 1 BY corpus,ticker,source_timestamp,source_id,unit_id,labeling_version", queries[0])
        self.assertNotIn("scoped_text_labels_v5 FINAL", queries[0])

    def test_summary_selects_requested_ticker_and_preserves_all_eligibility(self) -> None:
        labels = [
            {
                "ticker": "AAA",
                "content_role": "editorial_analysis",
                "source_origin": "editorial",
                "semantic_direction": "negative",
                "semantic_score": -0.4,
                "event_concepts": ["margin_pressure"],
                "forecast_trigger_eligible": True,
                "reaction_evaluation_eligible": True,
                "issuer_history_context_eligible": True,
                "prior_primary_context_eligible": True,
                "episode_followup_eligible": False,
            },
            {
                "ticker": "BBB",
                "content_role": "corporate_transaction",
                "source_origin": "company",
                "semantic_direction": "positive",
                "semantic_score": 0.8,
                "event_concepts": ["acquisition"],
                "forecast_trigger_eligible": True,
                "reaction_evaluation_eligible": True,
                "issuer_history_context_eligible": True,
            },
        ]

        summary = scoped_news_summary(labels, ticker="AAA")

        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual(summary["semantic_direction"], "negative")
        self.assertEqual(summary["event_concepts"], ["margin_pressure"])
        self.assertEqual(summary["issuer_count"], 1)
        self.assertTrue(summary["prior_primary_context_eligible"])
        self.assertFalse(summary["episode_followup_eligible"])


if __name__ == "__main__":
    unittest.main()
