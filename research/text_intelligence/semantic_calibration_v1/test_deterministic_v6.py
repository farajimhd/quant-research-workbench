from __future__ import annotations

import unittest

from research.text_intelligence.scoped_labeling_v1.news_identity import (
    IssuerIdentity,
    NewsIssuerResolver,
)
from research.text_intelligence.semantic_label_authority_v1.schema import (
    SemanticDocument,
)

from .deterministic_v6 import classify_news_document_v6


class DeterministicV6Tests(unittest.TestCase):
    def test_analyst_action_is_context_not_forecast_trigger(self) -> None:
        result = _classify(
            title="Morgan Stanley Maintains Overweight on Acme, Raises Price Target",
            text=(
                "Morgan Stanley analyst maintains Acme Corp (NASDAQ:ACME) at "
                "Overweight and raises the price target from $20 to $25."
            ),
        )
        self.assertEqual(result.content_role, "analyst_event")
        self.assertEqual(result.source_origin, "analyst_research")
        self.assertTrue(result.labels)
        self.assertTrue(all(not row["forecast_trigger_eligible"] for row in result.labels))
        self.assertEqual(
            result.labels[0]["classification"]["semantic_direction"],
            "positive",
        )

    def test_dilutive_offering_is_negative_and_triggering(self) -> None:
        result = _classify(
            title="Acme Announces Underwritten Public Offering",
            text=(
                "Acme Corp (NASDAQ:ACME) announced an underwritten public "
                "offering of 10 million common shares."
            ),
            tags=("press release",),
        )
        self.assertEqual(result.content_role, "primary_event")
        self.assertEqual(result.source_origin, "issuer_direct")
        self.assertEqual(
            result.labels[0]["classification"]["semantic_direction"],
            "negative",
        )
        self.assertTrue(result.labels[0]["forecast_trigger_eligible"])

    def test_conflicting_clinical_and_financing_evidence_is_mixed(self) -> None:
        result = _classify(
            title="Acme Reports Positive Phase 3 Results And Public Offering",
            text=(
                "Acme Corp (NASDAQ:ACME) reported positive phase 3 clinical "
                "results that met the primary endpoint and announced an "
                "underwritten public offering of common shares."
            ),
        )
        self.assertEqual(
            result.labels[0]["classification"]["semantic_direction"],
            "mixed",
        )

    def test_bare_context_heading_does_not_create_an_issuer_label(self) -> None:
        result = _classify(
            title="Mid-Afternoon Market Update",
            text="52-week highs: Acme Corp (NASDAQ:ACME).",
        )
        self.assertEqual(result.content_role, "market_roundup")
        self.assertFalse(result.labels)
        self.assertEqual(result.extraction_decision, "non_issuer_market_content")


def _classify(
    *,
    title: str,
    text: str,
    tags: tuple[str, ...] = (),
):
    identity = IssuerIdentity(
        ticker="ACME",
        issuer_id="test:acme",
        aliases=("Acme Corp",),
    )
    document = SemanticDocument(
        corpus="news",
        source_id="test-news",
        timestamp="2026-01-02 14:00:00.000000000",
        title=title,
        text=text,
        tickers=("ACME",),
        metadata={
            "provider_tags": tags,
            "channels": (),
            "author": "",
            "issuer_identities": ({
                "ticker": "ACME",
                "issuer_id": "test:acme",
                "aliases": ("Acme Corp",),
            },),
        },
    )
    return classify_news_document_v6(
        document,
        issuer_resolver=NewsIssuerResolver(
            (identity,),
            article_tickers=("ACME",),
        ),
    )


if __name__ == "__main__":
    unittest.main()
