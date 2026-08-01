from __future__ import annotations

import unittest

from research.text_intelligence.scoped_labeling_v1.news_identity import IssuerIdentity, NewsIssuerResolver
from research.text_intelligence.semantic_label_authority_v1.schema import SemanticDocument

from .deterministic_v8 import classify_news_document_v8


class DeterministicV8Tests(unittest.TestCase):
    def test_retains_event_bearing_mover_passage_without_making_it_triggerable(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="mover-1",
            timestamp="2026-01-02T22:00:00Z",
            title="8 Stocks Moving In Friday's After-Hours Session",
            text=(
                "Title: 8 Stocks Moving In Friday's After-Hours Session\n"
                "Source [provider_body:0] https://example.test\n"
                "Example Corp (NASDAQ:EXM) shares are down 10 percent following "
                "a fourth quarter earnings and sales miss."
            ),
            tickers=("EXM",),
            metadata={"issuer_identities": ({"ticker": "EXM", "issuer_id": "issuer:exm", "aliases": ("Example Corp",)},)},
        )
        result = classify_news_document_v8(
            document,
            issuer_resolver=NewsIssuerResolver(
                (IssuerIdentity("EXM", "issuer:exm", ("Example Corp",)),),
                article_tickers=("EXM",),
            ),
        )
        self.assertEqual(result.content_role, "mover_recap")
        self.assertEqual(len(result.labels), 1)
        self.assertEqual(result.labels[0]["classification"]["semantic_direction"], "negative")
        self.assertFalse(result.labels[0]["forecast_trigger_eligible"])
        self.assertTrue(result.labels[0]["issuer_history_context_eligible"])

    def test_uses_high_precision_provider_metadata(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="preview-1",
            timestamp="2026-01-02T12:00:00Z",
            title="Earnings Outlook For Example Corp",
            text="Example Corp (NASDAQ:EXM) is expected to report earnings tomorrow.",
            tickers=("EXM",),
            metadata={
                "provider_tags": ("bzi-ep",),
                "issuer_identities": ({"ticker": "EXM", "issuer_id": "issuer:exm", "aliases": ("Example Corp",)},),
            },
        )
        result = classify_news_document_v8(document)
        self.assertEqual(result.content_role, "preview")
        self.assertEqual(result.source_origin, "automated_summary")


if __name__ == "__main__":
    unittest.main()
