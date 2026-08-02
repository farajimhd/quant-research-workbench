from __future__ import annotations

import unittest

from research.text_intelligence.scoped_labeling_v1.news_identity import IssuerIdentity, NewsIssuerResolver
from research.text_intelligence.semantic_label_authority_v1.schema import SemanticDocument

from .deterministic_v9 import classify_news_document_v9
from .deterministic_v9_config import CALIBRATION_SPLIT_SHA256, CALIBRATION_VERSION
from .teacher_split_v9 import normalized_headline_template


class DeterministicV9Tests(unittest.TestCase):
    def test_template_normalization_removes_ticker_numbers_and_money(self) -> None:
        left = normalized_headline_template("AAPL Raises Target From $200 To $250", ("AAPL",))
        right = normalized_headline_template("MSFT Raises Target From $300 To $350", ("MSFT",))
        self.assertEqual(left, right)
        self.assertIn("<money>", left)

    def test_analyst_downgrade_is_negative_context_not_forecast_trigger(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="analyst-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="Broker Downgrades Example Corp To Sell, Cuts Price Target",
            text=(
                "Broker analyst downgrades Example Corp (NASDAQ:EXM) to Sell from Hold "
                "and lowers the price target to $8 from $12 due to weakening demand."
            ),
            tickers=("EXM",),
            metadata={
                "channels": ("downgrades", "price target"),
                "issuer_identities": ({
                    "ticker": "EXM", "issuer_id": "issuer:exm", "aliases": ("Example Corp",),
                },),
            },
        )
        resolver = NewsIssuerResolver(
            (IssuerIdentity("EXM", "issuer:exm", ("Example Corp",)),),
            article_tickers=("EXM",),
        )
        result = classify_news_document_v9(document, issuer_resolver=resolver)
        self.assertEqual(result.calibration_version, CALIBRATION_VERSION)
        self.assertEqual(len(CALIBRATION_SPLIT_SHA256), 64)
        self.assertEqual(result.content_role, "analyst_event")
        self.assertEqual(result.labels[0]["classification"]["semantic_direction"], "negative")
        self.assertFalse(result.labels[0]["forecast_trigger_eligible"])
        self.assertTrue(result.labels[0]["issuer_history_context_eligible"])


if __name__ == "__main__":
    unittest.main()
