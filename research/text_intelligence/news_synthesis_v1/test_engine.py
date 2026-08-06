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
        self.assertEqual(document["envelope"]["document_structure"]["value"], "single_subject")

    def test_envelope_uses_genre_and_source_metadata_not_issuer_count(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-envelope", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha reports earnings while Beta shares trade higher",
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) reported revenue increased. Beta Holdings Corp (NYSE:BBB) shares traded higher.",
            "tickers": ["AAA", "BBB"], "author": "benzinga neuro",
        })
        self.assertEqual(document["envelope"]["document_structure"]["value"], "single_subject")
        self.assertEqual(document["envelope"]["information_origin"]["value"], "issuer")
        self.assertEqual(document["envelope"]["production_method"]["value"], "automated")

    def test_high_frequency_concepts_are_source_bound_without_generic_fallback(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-concepts", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha update",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) reported net income increased. "
                "The Federal Reserve said monetary policy may ease. "
                "Alpha shares rallied in the broader market."
            ),
            "tickers": ["AAA"],
        })
        concepts = {row["concept_leaf"] for row in document["statements"]}
        self.assertIn("financial.operating_performance", concepts)
        self.assertIn("macro.policy_outlook", concepts)
        self.assertIn("market.price_move_observed", concepts)
        self.assertNotIn("unclassified.semantic_claim", concepts)

    def test_analyst_action_is_analysis_without_false_issuer_origin(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-analyst", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Research Firm Maintains Buy on Alpha, Raises Price Target to $35",
            "text": "Research Firm maintains a Buy rating on Alpha Therapeutics Inc (NASDAQ:AAA) and raises its price target to $35.",
            "tickers": ["AAA"],
        })
        self.assertEqual(document["envelope"]["information_origin"]["value"], "analyst")
        self.assertEqual(document["envelope"]["communication_purpose"]["value"], "analyze")

    def test_market_quotes_guidance_and_semicolon_clauses_are_atomic(self) -> None:
        text = (
            "Alpha Therapeutics Inc (NASDAQ:AAA) closed at $17.25; "
            "the company sees revenue growth of 15% next year."
        )
        document = self.engine.synthesize({
            "source_id": "news-atomic", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha market and outlook update", "text": text, "tickers": ["AAA"],
        })
        concepts = {row["concept_leaf"] for row in document["statements"]}
        self.assertIn("market.price_move_observed", concepts)
        self.assertIn("guidance.issued", concepts)
        self.assertTrue(all(";" not in row["evidence_spans"][0]["quote"].rstrip(";") for row in document["statements"]))

    def test_generic_nouns_and_external_forecasts_do_not_create_issuer_events(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-negative-rules", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha profile",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) provides a platform for researchers. "
                "Analysts expect revenue growth and hope the company will provide positive guidance."
            ),
            "tickers": ["AAA"],
        })
        concepts = {row["concept_leaf"] for row in document["statements"]}
        self.assertNotIn("product.milestone", concepts)
        self.assertNotIn("guidance.issued", concepts)

    def test_paragraph_local_subject_inheritance_is_issuer_scoped(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-discourse", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha results",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) reported quarterly results. Revenues increased 18% year over year.\n"
                "The broader market remained volatile."
            ),
            "tickers": ["AAA"],
        })
        operating = next(row for row in document["statements"] if row["concept_leaf"] == "financial.operating_performance")
        issuer_id = next(row["entity_id"] for row in document["entities"] if row["ticker"] == "AAA")
        self.assertTrue(any(row["statement_id"] == operating["statement_id"] and row["entity_id"] == issuer_id for row in document["participations"]))
        market = next(row for row in document["statements"] if row["concept_leaf"] == "market.context")
        self.assertFalse(any(row["statement_id"] == market["statement_id"] for row in document["participations"]))

    def test_currency_observation_is_not_equity_price_move(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-currency", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Dollar update", "text": "The U.S. Dollar Index is trading at 97.81, down 0.76.",
        })
        concepts = {row["concept_leaf"] for row in document["statements"]}
        self.assertIn("market.currency_move_observed", concepts)
        self.assertNotIn("market.price_move_observed", concepts)

    def test_inflected_moves_and_compact_guidance_are_detected(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-compact-language", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha sees FY27 adjusted EPS $2.10-$2.30 vs $2.00 est.",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) provided fiscal-year guidance projecting revenue growth. "
                "AAA shares fell 4.2% after the update."
            ),
            "tickers": ["AAA"],
        })
        concepts = {row["concept_leaf"] for row in document["statements"]}
        self.assertIn("guidance.issued", concepts)
        self.assertIn("market.price_move_observed", concepts)
        self.assertNotIn("financial.operating_performance", concepts)

    def test_background_business_and_unqualified_demand_do_not_create_events(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-background-language", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha company profile",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) is a company that provides research services. "
                "Demand Holdings is trading lower. Agencies may choose an ordering method."
            ),
            "tickers": ["AAA"],
        })
        concepts = {row["concept_leaf"] for row in document["statements"]}
        self.assertNotIn("operations.business_update", concepts)
        self.assertNotIn("commercial.demand_condition", concepts)

    def test_product_disclosure_and_qualified_customer_demand_are_detected(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-product-demand", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha reveals new device",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) revealed a new medical device. "
                "Management reported strong customer demand for the product."
            ),
            "tickers": ["AAA"],
        })
        concepts = {row["concept_leaf"] for row in document["statements"]}
        self.assertIn("product.milestone", concepts)
        self.assertIn("commercial.demand_condition", concepts)

    def test_headline_moves_closing_prices_and_lower_demand_are_detected(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-price-forms", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha Falls After Outlook Update",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) is selling off by 2.9% today. "
                "Alpha closed Tuesday at $18.40. Management cited lower-than-expected customer demand."
            ),
            "tickers": ["AAA"],
        })
        concepts = {row["concept_leaf"] for row in document["statements"]}
        self.assertIn("market.price_move_observed", concepts)
        self.assertIn("commercial.demand_condition", concepts)

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
