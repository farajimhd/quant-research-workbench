from __future__ import annotations

import unittest

from research.text_intelligence.scoped_labeling_v1.news_identity import IssuerIdentity, NewsIssuerResolver
from research.text_intelligence.semantic_label_authority_v1.schema import SemanticDocument

from .deterministic_v7 import classify_news_document_v7


class DeterministicV7Tests(unittest.TestCase):
    def test_market_wrap_beats_inner_regulatory_words(self) -> None:
        result = _classify(
            title="Wall Street Rockets As Bond Yields Drop, FDA Decision Eyed",
            text="Acme Corp (NASDAQ:ACME) announced an FDA submission in a broad market wrap.",
            channels=("markets", "news"),
        )
        self.assertEqual(result.content_role, "market_roundup")
        self.assertEqual(result.source_origin, "editorial_aggregation")
        self.assertTrue(all(not row["forecast_trigger_eligible"] for row in result.labels))

    def test_morning_movers_are_not_primary_events(self) -> None:
        result = _classify(
            title="Morning Market Gainers",
            text="Acme Corp (NASDAQ:ACME) shares rose 12% to $4.20 after no new company news.",
            channels=("movers",),
        )
        self.assertEqual(result.content_role, "mover_recap")
        self.assertEqual(result.source_origin, "editorial_aggregation")

    def test_earnings_schedule_is_preview(self) -> None:
        result = _classify(
            title="Earnings Scheduled For May 28, 2026",
            text="Acme Corp (NASDAQ:ACME) is expected to report earnings before the open.",
            channels=("previews",),
        )
        self.assertEqual(result.content_role, "preview")
        self.assertTrue(result.labels)
        self.assertTrue(result.labels[0]["issuer_history_context_eligible"])
        self.assertFalse(result.labels[0]["forecast_trigger_eligible"])

    def test_fda_approval_is_regulatory_primary(self) -> None:
        result = _classify(
            title="Acme Receives FDA Approval For New Treatment",
            text="Acme Corp (NASDAQ:ACME) received FDA approval for its new treatment.",
            channels=("fda", "news"),
        )
        self.assertEqual(result.content_role, "regulatory_event")
        self.assertEqual(result.source_origin, "regulatory_primary")

    def test_unlinked_background_ticker_is_not_emitted(self) -> None:
        acme = IssuerIdentity("ACME", "test:acme", ("Acme Corp",))
        other = IssuerIdentity("OTHR", "test:other", ("Other Corp",))
        document = SemanticDocument(
            corpus="news",
            source_id="test-news",
            timestamp="2026-01-02 14:00:00.000000000",
            title="Acme Announces Contract",
            text=(
                "Acme Corp (NASDAQ:ACME) announced a contract award. "
                "Other Corp (NASDAQ:OTHR) is a competitor."
            ),
            tickers=("ACME",),
            metadata={"provider_tags": (), "channels": ("contracts",), "author": ""},
        )
        result = classify_news_document_v7(
            document,
            issuer_resolver=NewsIssuerResolver((acme, other), article_tickers=("ACME",)),
        )
        self.assertEqual({row["ticker"] for row in result.labels}, {"ACME"})


def _classify(
    *,
    title: str,
    text: str,
    channels: tuple[str, ...] = (),
):
    identity = IssuerIdentity("ACME", "test:acme", ("Acme Corp",))
    document = SemanticDocument(
        corpus="news",
        source_id="test-news",
        timestamp="2026-01-02 14:00:00.000000000",
        title=title,
        text=text,
        tickers=("ACME",),
        metadata={
            "provider_tags": (),
            "channels": channels,
            "author": "",
            "issuer_identities": ({
                "ticker": "ACME", "issuer_id": "test:acme", "aliases": ("Acme Corp",),
            },),
        },
    )
    return classify_news_document_v7(
        document,
        issuer_resolver=NewsIssuerResolver((identity,), article_tickers=("ACME",)),
    )


if __name__ == "__main__":
    unittest.main()
