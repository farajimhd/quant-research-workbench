from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path

from research.text_intelligence.semantic_label_authority_v1.schema import (
    SemanticDocument,
)

from .news_extractor import extract_news_units
from .pipeline import classify_news_document, classify_sec_document
from .sec_extractor import extract_sec_units
from .persistence import assert_certification, bounded_period_ranges
from .schema import SCOPED_LABELING_VERSION


class ScopedLabelingTests(unittest.TestCase):
    def test_persistence_windows_are_bounded_and_exact(self) -> None:
        self.assertEqual(
            bounded_period_ranges("2026-07-01", "2026-07-12", 7),
            [
                ("2026-07-01", "2026-07-08"),
                ("2026-07-08", "2026-07-12"),
            ],
        )

    def test_persistence_requires_matching_clean_certification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(
                json.dumps(
                    {
                        "labeling_version": SCOPED_LABELING_VERSION,
                        "news_audits": 5,
                        "sec_audits": 5,
                        "review_attention": 0,
                    }
                ),
                encoding="utf-8",
            )
            assert_certification(path)
            path.write_text("{}", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                assert_certification(path)

    def test_roundup_creates_ticker_specific_observations(self) -> None:
        text = """Title: 42 Stocks Moving in Wednesday's Pre-Market Session
Body:
- Cancer Genetics, Inc. (NASDAQ:CGIX) shares rose 200.7% to $17.35 in pre-market trading after reporting a $10 million private placement.
- Other Corp (NYSE:OTHR) shares fell 12.5% to $4.20 after lowering guidance.
"""
        units = extract_news_units(
            source_id="19582725",
            title="42 Stocks Moving in Wednesday's Pre-Market Session",
            text=text,
            tickers=("CGIX", "OTHR"),
        )
        self.assertEqual(len(units), 2)
        self.assertEqual(units[0].tickers, ("CGIX",))
        self.assertEqual(units[0].observed_reaction.direction, "up")
        self.assertEqual(units[0].observed_reaction.move_pct, 200.7)
        self.assertEqual(units[0].observed_reaction.resulting_price, 17.35)
        self.assertEqual(
            units[0].reported_catalyst,
            "reporting a $10 million private placement",
        )

    def test_roundup_context_can_never_be_reaction_target(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="news-roundup",
            timestamp="2026-07-28T12:00:00Z",
            title="50 Biggest Movers From Friday",
            text=(
                "Body:\n- Example Corp (NASDAQ:EXMP) shares rose 25% "
                "to $5.00 after receiving FDA approval."
            ),
            tickers=("EXMP",),
            metadata={"channels": ["Movers"], "author": "Benzinga"},
        )
        labels = classify_news_document(document)
        self.assertEqual(len(labels), 1)
        self.assertFalse(labels[0].forecast_trigger_eligible)
        self.assertFalse(labels[0].reaction_evaluation_eligible)
        self.assertTrue(labels[0].issuer_history_context_eligible)
        self.assertIn(
            "regulatory.fda_approval",
            {
                f"{item['family']}.{item['subtype']}"
                for item in labels[0].semantic["labels"]
            },
        )

    def test_multi_ticker_unscoped_prose_is_not_assigned(self) -> None:
        units = extract_news_units(
            source_id="multi",
            title="Technology shares move",
            text="Body: Several technology companies moved in active trading.",
            tickers=("AAAA", "BBBB"),
        )
        self.assertFalse(units)

    def test_single_ticker_article_is_one_document_unit(self) -> None:
        units = extract_news_units(
            source_id="single",
            title="Example raises guidance",
            text=(
                "Body: Example announced that it raised guidance.\n"
                "Revenue also increased year over year."
            ),
            tickers=("EXMP",),
        )
        self.assertEqual(len(units), 1)
        self.assertIn("Revenue also increased", units[0].text)
        self.assertEqual(units[0].role, "primary_or_editorial_document")

    def test_multi_ticker_scoped_passage_is_context_only(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="multi-scoped",
            timestamp="2026-07-28T12:00:00Z",
            title="Two healthcare companies report updates",
            text=(
                "Example Corp (NASDAQ:EXMP) received FDA approval.\n"
                "Other Corp (NYSE:OTHR) announced a public offering."
            ),
            tickers=("EXMP", "OTHR"),
            metadata={"author": "Editorial Desk"},
        )
        labels = classify_news_document(document)
        self.assertEqual(len(labels), 2)
        self.assertTrue(all(not item.forecast_trigger_eligible for item in labels))
        self.assertTrue(all(item.issuer_history_context_eligible for item in labels))

    def test_sec_extractor_ignores_signature_and_keeps_event(self) -> None:
        text = """ITEM 1.01
The registrant entered into a registered direct offering for $25 million.
SIGNATURES
Pursuant to the requirements of the Securities Exchange Act, the registrant signed this report.
"""
        units = extract_sec_units(
            source_id="sec-1",
            title="8-K",
            text=text,
            ticker="EXMP",
            metadata={"document_role": "primary_document"},
        )
        self.assertEqual(len(units), 1)
        self.assertIn("registered direct offering", units[0].text)
        self.assertNotIn("signed this report", units[0].text)

    def test_sec_labels_only_relevant_units(self) -> None:
        document = SemanticDocument(
            corpus="sec",
            source_id="sec-2",
            timestamp="2026-07-28T12:00:00Z",
            title="Example 8-K EX-99.1",
            text=(
                "BUSINESS UPDATE\n"
                "The company announced a registered direct offering.\n"
                "FORWARD-LOOKING STATEMENTS\n"
                "These statements involve risks and uncertainties."
            ),
            tickers=("EXMP",),
            metadata={
                "form_type": "8-K",
                "document_type": "EX-99.1",
                "document_role": "press_release_exhibit",
                "text_kind": "press_release_exhibit",
                "accepted_at_utc": "2026-07-28T12:00:00Z",
            },
        )
        labels = classify_sec_document(document)
        self.assertEqual(len(labels), 1)
        self.assertIn(
            "financing.registered_direct",
            labels[0].classification["event_concepts"],
        )

    def test_generic_purchase_order_disclosure_is_not_contract_award(self) -> None:
        document = SemanticDocument(
            corpus="sec",
            source_id="sec-background",
            timestamp="2026-07-28T12:00:00Z",
            title="Annual report",
            text="Purchase orders are used in the ordinary course of business.",
            tickers=("EXMP",),
            metadata={
                "form_type": "10-K",
                "document_type": "10-K",
                "document_role": "primary_document",
                "text_kind": "primary_document",
                "accepted_at_utc": "2026-07-28T12:00:00Z",
            },
        )
        labels = classify_sec_document(document)
        concepts = {
            concept
            for label in labels
            for concept in label.classification["event_concepts"]
        }
        self.assertNotIn("contract_order.award", concepts)

    def test_form_four_exercise_price_is_not_financing_event(self) -> None:
        document = SemanticDocument(
            corpus="sec",
            source_id="form-4",
            timestamp="2026-07-28T12:00:00Z",
            title="Example Form 4",
            text=(
                "Common Stock\nTransaction code M\n"
                "Warrant conversion or exercise price $2.50\n"
                "Performance Shares"
            ),
            tickers=("EXMP",),
            metadata={
                "form_type": "4",
                "document_type": "4",
                "document_role": "primary_document",
                "text_kind": "primary_document",
                "accepted_at_utc": "2026-07-28T12:00:00Z",
            },
        )
        labels = classify_sec_document(document)
        self.assertTrue(labels)
        self.assertTrue(all(
            item.classification["content_role"] == "ownership_transaction"
            for item in labels
        ))
        self.assertTrue(all(not item.forecast_trigger_eligible for item in labels))
        self.assertNotIn(
            "financing.warrant",
            {
                concept
                for item in labels
                for concept in item.classification["event_concepts"]
            },
        )


if __name__ == "__main__":
    unittest.main()
