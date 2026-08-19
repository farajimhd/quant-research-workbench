from __future__ import annotations

import unittest
from pathlib import Path

from research.text_intelligence.news_synthesis_v1.engine import ENGINE_VERSION, IssuerIdentity, IssuerIdentityIndex, NewsSynthesisEngine
from src.backend.news_synthesis import load_news_synthesis, presentation_payload, synthesis_summary


class NewsSynthesisPresentationTests(unittest.TestCase):
    def test_active_news_consumers_have_no_retired_authority_fallback(self) -> None:
        root = Path(__file__).resolve().parents[2]
        checks = {
            root / "src/backend/app.py": ("news_classification", "news_rules_v1", "news_semantic_label_v2"),
            root / "src/backend/canvas_preview_service.py": ("news_classification", "news_rules_v1", "scoped_text_labels_v5"),
            root / "frontend/src/app/components/NewsContainers.tsx": ("scoped_labels", "scoped_summary", "news_rules_v1", "prior_context_eligible", "followup_eligible"),
            root / "services/news-hypothesis/src/news_hypothesis/contextual.py": ("news_semantic_label_v2",),
        }
        for path, forbidden in checks.items():
            source = path.read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, source, f"{path.name} retains retired News authority {token}")

    def test_v1_document_maps_to_canvas_contract(self) -> None:
        engine = NewsSynthesisEngine(IssuerIdentityIndex((IssuerIdentity("AAA", "issuer:aaa", "Alpha Corp", ("Alpha Corp",)),)))
        document = engine.synthesize({"source_id": "n1", "source_timestamp": "2026-08-03T12:00:00Z", "title": "Alpha wins contract", "text": "Alpha Corp (NASDAQ:AAA) was awarded a contract.", "tickers": ["AAA"]})
        payload = presentation_payload(document)
        self.assertEqual(payload["article_fields"]["news_kind"], "company")
        self.assertTrue(payload["summary"]["forecast_trigger_eligible"])
        self.assertEqual(payload["summary"]["engine_version"], ENGINE_VERSION)
        self.assertNotIn("labels", payload)
        self.assertIs(payload["document"], document)

    def test_loader_queries_the_single_current_engine_authority(self) -> None:
        queries: list[str] = []

        def query_rows(sql: str) -> list[dict[str, object]]:
            queries.append(sql)
            return []

        self.assertEqual(
            load_news_synthesis(["n1"], query_rows=query_rows, quote=lambda value: f"'{value}'"),
            {},
        )
        self.assertEqual(len(queries), 1)
        self.assertIn(f"engine_version='{ENGINE_VERSION}'", queries[0])
        self.assertNotIn("news_synthesis_engine_v1", queries[0])

    def test_ticker_summary_uses_v1_issuer_view_and_products(self) -> None:
        engine = NewsSynthesisEngine(IssuerIdentityIndex((IssuerIdentity("AAA", "issuer:aaa", "Alpha Corp", ("Alpha Corp",)),)))
        document = engine.synthesize({"source_id": "n1", "source_timestamp": "2026-08-03T12:00:00Z", "title": "Alpha wins contract", "text": "Alpha Corp (NASDAQ:AAA) was awarded a contract.", "tickers": ["AAA"]})

        summary = synthesis_summary(document, ticker="AAA")

        self.assertEqual(summary["composite_sentiment"], "positive")
        self.assertIn("analyst_evaluation_eligible", summary)
        self.assertNotIn("prior_primary_context_eligible", summary)
        self.assertNotIn("episode_followup_eligible", summary)


if __name__ == "__main__":
    unittest.main()
