from __future__ import annotations

import unittest

from research.text_intelligence.semantic_label_authority_v1.schema import (
    SemanticDocument,
)
from research.text_intelligence.semantic_label_authority_v1.labeler import (
    label_document,
)

from .authority import classify_document
from .evaluation import Reaction, reaction_direction, summarize


def news(
    text: str,
    *,
    title: str = "",
    tickers: tuple[str, ...] = ("EXMP",),
    **metadata,
) -> SemanticDocument:
    return SemanticDocument(
        corpus="news",
        source_id="news-1",
        timestamp="2026-07-28T12:00:00Z",
        title=title,
        text=text,
        tickers=tickers,
        metadata=metadata,
    )


class ClassificationAuthorityTests(unittest.TestCase):
    def test_direct_release_requires_source_and_event_evidence(self) -> None:
        result = classify_document(
            news(
                "The company announced today that it raised guidance.",
                title="Issuer Raises Revenue Guidance",
                links=["https://www.businesswire.com/news/example"],
                author="Benzinga Newsdesk",
                channels=["Guidance"],
            )
        )
        self.assertEqual(result.source_origin, "issuer_direct")
        self.assertEqual(result.issuer_relationship, "direct_announcement")
        self.assertEqual(result.content_role, "primary_event")
        self.assertTrue(result.forecast_trigger_eligible)
        self.assertTrue(result.reaction_evaluation_eligible)
        self.assertIn("guidance.raise", result.event_concepts)

    def test_single_ticker_editorial_is_not_company_news(self) -> None:
        result = classify_document(
            news(
                "The writer discusses valuation and recent price movement.",
                title="What Investors Should Know About EXMP",
                author="Editorial Desk",
                channels=["Trading Ideas"],
            )
        )
        self.assertEqual(result.source_origin, "editorial_original")
        self.assertEqual(
            result.issuer_relationship,
            "unrelated_or_ambiguous",
        )
        self.assertFalse(result.forecast_trigger_eligible)

    def test_roundup_remains_followup_even_with_event_words(self) -> None:
        result = classify_document(
            news(
                "EXMP raised guidance while other companies announced offerings.",
                title="50 Biggest Movers From Friday",
                author="Benzinga",
                channels=["Movers"],
                tickers=("EXMP", "ABCD"),
            )
        )
        self.assertEqual(result.content_role, "market_roundup")
        self.assertEqual(result.source_origin, "editorial_aggregation")
        self.assertTrue(result.episode_followup_eligible)
        self.assertFalse(result.forecast_trigger_eligible)
        self.assertFalse(result.reaction_evaluation_eligible)
        self.assertEqual(result.semantic_direction, "neutral")
        self.assertEqual(result.semantic_score, 0.0)
        self.assertFalse(result.event_concepts)
        self.assertIn(
            "ticker_scoped_semantics_required",
            result.quality_flags,
        )

    def test_sec_source_type_is_not_overwritten_by_semantics(self) -> None:
        result = classify_document(
            SemanticDocument(
                corpus="sec",
                source_id="doc-1",
                timestamp="2026-07-28T12:00:00Z",
                title="Form 8-K",
                text="The registrant announced a registered direct offering.",
                metadata={
                    "form_type": "8-K",
                    "document_type": "EX-99.1",
                    "document_role": "press_release_exhibit",
                    "text_kind": "press_release_exhibit",
                    "accepted_at_utc": "2026-07-28T12:00:00Z",
                },
            )
        )
        self.assertEqual(result.source_type, "8-K")
        self.assertIn("EX-99.1", result.source_subtype)
        self.assertEqual(result.source_origin, "regulatory_primary")
        self.assertIn("financing.registered_direct", result.event_concepts)
        self.assertTrue(result.reaction_evaluation_eligible)

    def test_reaction_direction_uses_excursion_dominance_and_noise(self) -> None:
        self.assertEqual(reaction_direction(0.001, -0.001, 0.5), "neutral")
        self.assertEqual(reaction_direction(0.03, -0.01, 0.5), "positive")
        self.assertEqual(reaction_direction(0.01, -0.04, 0.5), "negative")

    def test_runtime_classification_can_skip_discovery_only_ngrams(self) -> None:
        document = news(
            "The company announced today that it raised guidance.",
            links=["https://www.businesswire.com/news/example"],
            channels=["Guidance"],
        )
        semantic = label_document(
            document,
            include_discovery_evidence=False,
        )
        result = classify_document(document, semantic_result=semantic)
        self.assertFalse(semantic.keywords)
        self.assertFalse(semantic.candidates)
        self.assertIn("guidance.raise", result.event_concepts)

    def test_summary_excludes_context_only_reaction_links(self) -> None:
        eligible = classify_document(
            news(
                "The company announced today that it raised guidance.",
                links=["https://www.businesswire.com/news/example"],
                channels=["Guidance"],
            )
        ).as_dict()
        context = classify_document(
            news(
                "EXMP raised guidance while other companies announced offerings.",
                title="50 Biggest Movers From Friday",
                channels=["Movers"],
                tickers=("EXMP", "ABCD"),
            )
        ).as_dict()
        rows = [
            {
                "corpus": "news",
                "source_id": "eligible",
                "classification": eligible,
            },
            {
                "corpus": "news",
                "source_id": "context",
                "classification": context,
            },
        ]
        reactions = [
            Reaction(
                corpus="news",
                source_id="eligible",
                ticker="EXMP",
                published_at_utc="2026-07-28T12:00:00Z",
                anchor_price=10.0,
                terminal_return=0.01,
                high_return=0.02,
                low_return=-0.005,
                observation_count=10,
                source="test",
            ),
            Reaction(
                corpus="news",
                source_id="context",
                ticker="EXMP",
                published_at_utc="2026-07-28T12:00:00Z",
                anchor_price=10.0,
                terminal_return=-0.01,
                high_return=0.005,
                low_return=-0.02,
                observation_count=10,
                source="test",
            ),
        ]
        payload = summarize(rows, reactions)
        result = payload["reaction"]["news"]
        self.assertEqual(result["eligible_documents"], 1)
        self.assertEqual(result["context_only_documents_excluded"], 1)
        self.assertEqual(result["reaction_links"], 1)
        self.assertEqual(result["exact_direction_agreement"], 1.0)


if __name__ == "__main__":
    unittest.main()
