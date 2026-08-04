from __future__ import annotations

import unittest
from pathlib import Path
from datetime import date

from .contracts import validate_document
from .backfill import _source_revision
from .engine import IssuerIdentity, IssuerIdentityIndex, NewsSynthesisEngine
from .facts import extract_typed_facts
from .storage import persistence_row


class NewsSynthesisEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = NewsSynthesisEngine(IssuerIdentityIndex((
            IssuerIdentity("AAA", "issuer:aaa", "Alpha Therapeutics", ("Alpha Therapeutics Inc",), "NASDAQ"),
            IssuerIdentity("BBB", "issuer:bbb", "Beta Holdings", ("Beta Holdings Corp",), "NYSE"),
        )))

    def test_extracts_evidence_facts_identity_sentiment_and_eligibility(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-1", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha Therapeutics wins $25 million contract",
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) was awarded a $25 million contract on August 3 at 8:00 ET.",
            "tickers": ["AAA"], "rendered_text_hash": "revision-1",
        })
        self.assertTrue(validate_document(document).valid)
        self.assertEqual(document["entities"][0]["ticker"], "AAA")
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")
        facts = document["statements"][0]["typed_facts"]
        self.assertTrue(any(row["fact_type"] == "money" for row in facts))
        self.assertTrue(any(row["fact_type"] == "date" for row in facts))
        self.assertTrue(any(row["product"] == "forecast_trigger" and row["eligible"] for row in document["eligibility"]))
        row = persistence_row(document)
        self.assertEqual(row["forecast_tickers"], ["AAA"])

    def test_provider_ticker_without_text_evidence_is_not_an_entity(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-2", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Broad market update", "text": "Inflation declined during the month.",
            "tickers": ["AAA"],
        })
        self.assertEqual(document["entities"], [])
        self.assertIn("unresolved_identity", document["quality_flags"])

    def test_production_package_has_no_prior_labeler_dependency(self) -> None:
        package_root = Path(__file__).parent
        production_modules = ("engine.py", "storage.py", "backfill.py", "synthesis.py", "facts.py")
        forbidden = ("scoped_labeling_v1", "news_reaction_deterministic", "news_classification")
        for name in production_modules:
            source = (package_root / name).read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, source, f"{name} imports or references prior authority {token}")

    def test_multi_issuer_sentiment_is_scoped_by_sentence(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-3", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha to acquire Beta",
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) agreed to acquire Beta Holdings Corp (NYSE:BBB). Beta Holdings shares rose after the agreement.",
            "tickers": ["AAA", "BBB"],
        })
        self.assertEqual({row["ticker"] for row in document["entities"]}, {"AAA", "BBB"})
        self.assertEqual(document["envelope"]["document_structure"]["value"], "multi_subject_digest")

    def test_prelisting_identity_and_unrendered_text_fail_closed(self) -> None:
        engine = NewsSynthesisEngine(IssuerIdentityIndex((
            IssuerIdentity("NEW", "issuer:new", "New Company", ("New Company",), "NASDAQ", list_date=date(2026, 8, 5)),
        )))
        document = engine.synthesize({
            "source_id": "news-4", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "New Company (NASDAQ:NEW) announces offering", "tickers": ["NEW"],
            "render_status": "unrendered",
        })
        self.assertEqual(document["entities"][0]["identity_status"], "not_tradable_as_of")
        self.assertEqual(document["envelope"]["text_availability"]["value"], "unrendered")
        self.assertIn("unrendered_text", document["quality_flags"])
        self.assertFalse(any(row["eligible"] for row in document["eligibility"] if row["product"] == "forecast_trigger"))

    def test_typed_facts_support_symbols_and_do_not_split_identifiers(self) -> None:
        facts = extract_typed_facts([{
            "source_field": "rendered_text",
            "start": 0,
            "end": 76,
            "quote": "Form X-17A-5 cites £2.5 million and a 10–20% range at 08:31 ET.",
        }])
        self.assertTrue(any(row.get("currency") == "GBP" for row in facts))
        self.assertTrue(any(row["fact_type"] == "percentage_range" for row in facts))
        self.assertTrue(any(row["fact_type"] == "time" for row in facts))
        self.assertFalse(any(row["fact_type"] == "number" and row["raw"] in {"17", "5"} for row in facts))

    def test_backfill_revision_changes_when_semantic_source_metadata_changes(self) -> None:
        row = {
            "source_id": "news-1",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "source_revision_key": "source-r1",
            "rendered_text_hash": "render-r1",
            "title": "Alpha update",
            "tickers": ["AAA"],
            "channels": ["news"],
            "provider_tags": ["company"],
            "content_quality_flags": [],
            "quality_flags": [],
            "render_status": "rendered",
        }
        changed = {**row, "provider_tags": ["company", "earnings"]}
        self.assertNotEqual(_source_revision([row]), _source_revision([changed]))

    def test_statement_spans_preserve_decimal_values_and_issuer_scope(self) -> None:
        text = (
            "Alpha Therapeutics Inc (NASDAQ:AAA) agreed to acquire Beta Holdings Corp "
            "(NYSE:BBB) for $15.25 per share. Inflation declined during the month. "
            "Beta Holdings Corp was downgraded to Hold from Buy."
        )
        document = self.engine.synthesize({
            "source_id": "news-5",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha to acquire Beta",
            "text": text,
            "tickers": ["AAA", "BBB"],
        })
        acquisition = next(row for row in document["statements"] if row["concept_leaf"] == "corporate_transaction.acquisition")
        quote = acquisition["evidence_spans"][0]["quote"]
        self.assertIn("$15.25", quote)
        self.assertEqual(text[acquisition["evidence_spans"][0]["start"]:acquisition["evidence_spans"][0]["end"]], quote)
        analyst = next(row for row in document["statements"] if row["concept_leaf"] == "analyst.rating_action")
        parts = [row for row in document["participations"] if row["statement_id"] == analyst["statement_id"]]
        self.assertEqual([row["entity_id"] for row in parts], [next(row["entity_id"] for row in document["entities"] if row["ticker"] == "BBB")])
        self.assertEqual(parts[0]["semantic_sentiment"], "negative")


if __name__ == "__main__":
    unittest.main()
