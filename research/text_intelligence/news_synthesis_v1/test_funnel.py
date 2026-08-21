from __future__ import annotations

import unittest

from .funnel import NewsSynthesisFunnel
from .storage import funnel_persistence_row


class _ExplodingEngine:
    def synthesize(self, source):  # pragma: no cover - failure proves fast route regressed
        raise AssertionError("context-only route must not invoke full synthesis")


class _SemanticEngine:
    def synthesize(self, source):
        return {
            "entities": [{
                "entity_id": "security:ACME", "entity_kind": "security", "ticker": "ACME",
                "identity_status": "resolved",
            }],
            "issuer_views": [{"entity_id": "security:ACME", "composite_sentiment": "positive"}],
            "eligibility": [
                {"entity_id": "security:ACME", "product": "forecast_trigger", "eligible": True},
                {"entity_id": "security:ACME", "product": "analyst_evaluation", "eligible": False},
            ],
        }


class FunnelTests(unittest.TestCase):
    def test_context_family_skips_full_engine_and_preserves_context(self) -> None:
        funnel = NewsSynthesisFunnel(_ExplodingEngine())
        result = funnel.process({
            "source_id": "n1",
            "source_timestamp": "2026-08-21T12:00:00Z",
            "provider": "benzinga",
            "provider_tags": ["halts"],
            "tickers": ["ACME"],
            "title": "ACME Trading Halted",
        })
        self.assertEqual(result["final"]["lane"], "context_only")
        self.assertEqual(result["final"]["analysis_depth"], "fast_context")
        self.assertTrue(result["final"]["context_preserved"])
        self.assertIsNone(result["synthesis_document"])

    def test_forecast_candidate_runs_semantic_engine(self) -> None:
        funnel = NewsSynthesisFunnel(_SemanticEngine())
        result = funnel.process({
            "source_id": "n2",
            "source_timestamp": "2026-08-21T12:00:00Z",
            "provider": "benzinga",
            "tickers": ["ACME"],
            "title": "ACME Raises Guidance",
        })
        self.assertEqual(result["final"]["lane"], "forecast_event")
        self.assertEqual(result["final"]["forecast_eligibility"], "eligible")
        self.assertIsNotNone(result["synthesis_document"])

    def test_missing_source_fields_returns_explicit_insufficient(self) -> None:
        result = NewsSynthesisFunnel(_ExplodingEngine()).process({"source_id": "n3"})
        self.assertEqual(result["final"]["lane"], "insufficient_information")

    def test_funnel_decision_has_durable_persistence_shape(self) -> None:
        result = NewsSynthesisFunnel(_ExplodingEngine()).process({
            "source_id": "n4", "source_timestamp": "2026-08-21T12:00:00Z",
            "provider": "benzinga", "provider_tags": ["halts"],
            "tickers": ["ACME"], "title": "ACME Trading Halted",
        })
        row = funnel_persistence_row(result)
        self.assertEqual(row["canonical_news_id"], "n4")
        self.assertEqual(row["final_lane"], "context_only")
        self.assertEqual(row["forecast_eligibility"], "ineligible")
        self.assertIn("ACME", row["ticker_labels_json"])
