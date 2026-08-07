from __future__ import annotations

import unittest
from pathlib import Path
from datetime import date

from .contracts import validate_document
from .backfill import _source_revision
from .engine import (
    IssuerIdentity,
    IssuerIdentityIndex,
    NewsSynthesisEngine,
    _sentiment_term_present,
)
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

    def test_provider_candidate_can_scope_a_supported_single_issuer_event(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-provider-event",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Prices 750K shares at $67 per share",
            "text": "Prices 750K shares at $67 per share.",
            "tickers": ["AAA"],
        })
        self.assertEqual([row["ticker"] for row in document["entities"]], ["AAA"])
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "negative")
        self.assertIn(
            "provider_candidate_only",
            document["entities"][0]["identity_evidence"],
        )

    def test_contract_termination_dominates_reactive_mitigation(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-contract-loss",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha receives contract termination notice from Beta",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) received a termination notice "
                "for its customer contract. The company is reducing costs associated "
                "with the lost customer and adapting operations. Management said, "
                "'We remain confident in our platform.'"
            ),
            "tickers": ["AAA"],
        })
        view = document["issuer_views"][0]
        self.assertEqual(view["composite_sentiment"], "negative")
        self.assertEqual(view["negative_strength"], 4)
        self.assertNotIn(
            "analyst.issuer_assessment",
            {row["concept_leaf"] for row in document["statements"]},
        )

    def test_polarity_cues_match_word_families_not_inner_substrings(self) -> None:
        cases = (
            ("terminate", "contract termination notice", True),
            ("cancel", "agreement cancellation", True),
            ("received", "company receives an award", True),
            ("decline", "revenue declined", True),
            ("accept", "the FDA accepted the application", True),
            ("order", "the customer orders twelve aircraft", True),
            ("weakness", "unremediated material weaknesses", True),
            ("win", "following the announcement", False),
            ("advantage", "competitive disadvantage", False),
        )
        for cue, text, expected in cases:
            with self.subTest(cue=cue, text=text):
                self.assertEqual(_sentiment_term_present(cue, text), expected)

    def test_exchange_prefixed_provider_identifier_is_canonicalized(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-prefixed-provider",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha announces a public offering",
            "text": "Alpha announces a public offering.",
            "tickers": ["NYSE:AAA"],
        })
        self.assertEqual([row["ticker"] for row in document["entities"]], ["AAA"])
        self.assertEqual(len(document["issuer_views"]), 1)

    def test_leading_article_and_corporate_suffix_alias_variants_compose(self) -> None:
        engine = NewsSynthesisEngine(IssuerIdentityIndex((
            IssuerIdentity("AAA", "issuer:aaa", "The Alpha", ("The Alpha",)),
            IssuerIdentity("BBB", "issuer:bbb", "Beta Holdings", ("Beta Holdings",)),
        )))
        document = engine.synthesize({
            "source_id": "news-grammatical-alias",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha Co paused its share repurchase program following a merger with Beta Holdings",
            "text": "Alpha Co paused its share repurchase program following a merger with Beta Holdings.",
            "tickers": ["AAA", "BBB"],
        })
        participants = {row["entity_id"] for row in document["participations"]}
        alpha = next(row for row in document["entities"] if row["ticker"] == "AAA")
        self.assertIn(alpha["entity_id"], participants)

    def test_unrelated_single_word_alias_does_not_override_provider_scope(self) -> None:
        engine = NewsSynthesisEngine(IssuerIdentityIndex((
            IssuerIdentity("AAA", "issuer:aaa", "Alpha Therapeutics", ("Alpha Therapeutics",)),
            IssuerIdentity("FRO", "issuer:fro", "Frontline", ("Frontline",)),
        )))
        document = engine.synthesize({
            "source_id": "news-provider-alias-scope",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha workforce reduction",
            "text": "The company announced a workforce reduction affecting frontline roles.",
            "tickers": ["AAA"],
        })
        self.assertEqual({row["ticker"] for row in document["entities"]}, {"AAA"})

    def test_generic_compact_event_language_is_covered_by_concept_families(self) -> None:
        cases = (
            ("NuCo Q2 EPS (0.04) Up From (0.11) YoY", "earnings.performance"),
            ("NuCo files for mixed shelf offering up to $75M", "capital.financing"),
            ("NuCo reports follow-on contract win for $5.5M", "commercial.contract"),
            ("NuCo closes sale of hardware business for $175M", "corporate_transaction.asset_sale"),
            ("NuCo receives non-compliance letter for late filing", "listing.market_structure"),
        )
        for index, (text, expected) in enumerate(cases):
            with self.subTest(expected=expected):
                document = self.engine.synthesize({
                    "source_id": f"news-compact-family-{index}",
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": text,
                    "text": text,
                    "tickers": ["AAA"],
                })
                self.assertIn(
                    expected,
                    {row["concept_leaf"] for row in document["statements"]},
                )
                self.assertTrue(document["issuer_views"])

    def test_acquisition_funding_and_operational_scale_are_constructive(self) -> None:
        cases = (
            "Alpha will use the proceeds to fund its pending acquisition.",
            "The transaction will increase Alpha's operational scale and add development inventory.",
        )
        for index, text in enumerate(cases):
            with self.subTest(text=text):
                document = self.engine.synthesize({
                    "source_id": f"news-acquisition-benefit-{index}",
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": text,
                    "text": text,
                    "tickers": ["AAA"],
                })
                self.assertEqual(
                    document["issuer_views"][0]["composite_sentiment"],
                    "positive",
                )

    def test_narrowed_loss_is_positive_earnings_direction(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-narrowed-loss",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha narrowed its net loss",
            "text": "Alpha Therapeutics narrowed its net loss to $10 million from $20 million.",
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")

    def test_failed_listing_compliance_is_not_positive_regain(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-failed-compliance",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha receives delisting determination",
            "text": "Alpha Therapeutics receives a delisting determination; the company has not regained compliance.",
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "negative")

    def test_minimum_bid_deficiency_is_negative(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-minimum-bid-deficiency",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha no longer meets Nasdaq minimum bid requirement",
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) no longer meets the minimum bid listing requirement.",
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "negative")

    def test_reverse_stock_split_to_regain_compliance_is_negative(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-reverse-stock-split",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha announces 1-for-20 reverse stock split",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) approved a 1-for-20 reverse stock split. "
                "The company expects the split will increase the market price per share in order "
                "to regain compliance with Nasdaq's minimum bid listing requirement."
            ),
            "tickers": ["AAA"],
        })
        view = document["issuer_views"][0]
        self.assertEqual(view["composite_sentiment"], "negative")
        self.assertEqual(view["negative_strength"], 3)
        concepts = {row["concept_leaf"] for row in document["statements"]}
        self.assertNotIn("commercial.demand_condition", concepts)
        self.assertNotIn("capital.financing", concepts)

    def test_achieved_listing_compliance_recovery_remains_positive(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-regained-listing-compliance",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha regains Nasdaq compliance",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) has regained compliance with Nasdaq listing "
                "requirements following a previously completed reverse stock split."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")

    def test_granted_listing_extension_to_regain_compliance_is_positive(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-listing-extension",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha receives Nasdaq compliance extension",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) received a letter from Nasdaq granting an "
                "additional 180 calendar day period to regain minimum bid compliance."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")

    def test_rating_endpoint_is_not_double_counted_as_issuer_assessment(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-rating-endpoint",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Analyst downgrades Alpha from Positive to Neutral",
            "text": "An analyst downgrades Alpha Therapeutics Inc (NASDAQ:AAA) from Positive to Neutral.",
            "tickers": ["AAA"],
        })
        concepts = [row["concept_leaf"] for row in document["statements"]]
        self.assertIn("analyst.rating_action", concepts)
        self.assertNotIn("analyst.issuer_assessment", concepts)
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "negative")

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

    def test_offering_brokerage_boilerplate_does_not_override_issuer_origin(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-secondary-offering", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha Therapeutics Announces Secondary Public Offering And Repurchase",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) announced a secondary public offering by a selling stockholder. "
                "The company will repurchase $5 million of shares. "
                "The underwriters may sell shares through brokers in one or more brokerage transactions."
            ),
            "tickers": ["AAA"],
        })
        envelope = document["envelope"]
        self.assertEqual(envelope["information_origin"]["value"], "issuer")
        self.assertEqual(envelope["communication_purpose"]["value"], "report")
        self.assertIn("announce", envelope["information_origin"]["evidence"][0]["quote"].lower())
        self.assertTrue(any(
            row["product"] == "forecast_trigger" and row["eligible"]
            for row in document["eligibility"]
        ))

    def test_why_moving_followup_preserves_editorial_and_cited_source_origins(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-why-moving-sec", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Why Did Alpha Shares Surge After Hours?",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) shares surged after an SEC filing "
                "disclosed that an investor acquired a material stake."
            ),
            "tickers": ["AAA"],
        })
        envelope = document["envelope"]
        self.assertEqual(envelope["communication_purpose"]["value"], "explain_move")
        self.assertEqual(envelope["information_origin"]["value"], "mixed")
        fields = {row["source_field"] for row in envelope["information_origin"]["evidence"]}
        self.assertIn("title", fields)

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

    def test_guidance_comparison_drives_sentiment_and_not_analyst_attribution(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-guidance-vs-consensus",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha reaffirms FY2027 outlook",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) reaffirms FY2027 EPS $2.65 "
                "vs $2.70 analyst estimate and core sales growth 5.5%."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["envelope"]["communication_purpose"]["value"], "report")
        self.assertEqual(document["envelope"]["information_origin"]["value"], "issuer")
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "negative")
        self.assertTrue(next(
            row["eligible"] for row in document["eligibility"]
            if row["product"] == "forecast_trigger"
        ))
        guidance = next(row for row in document["statements"] if row["concept_leaf"] == "guidance.issued")
        comparison = next(row for row in guidance["typed_facts"] if row["fact_type"] == "estimate_comparison")
        self.assertEqual(comparison["metric"], "eps")
        self.assertEqual(comparison["relation"], "below")
        self.assertNotIn(
            "financial.operating_performance",
            {row["concept_leaf"] for row in document["statements"]},
        )

    def test_coordinated_outlook_and_rendered_table_rows_form_one_guidance_package(self) -> None:
        title = (
            "Alpha sees Q3 EPS $0.35-$0.40 vs $0.42 est.; "
            "FY23 EPS $1.40-$1.45 vs $1.57 est."
        )
        text = (
            f"Title: {title}\n"
            "Q3 2023 Outlook:\n"
            "Columns: Metric; Projection\n"
            "Metric=Net Sales growth versus Q3 2022; Projection=3-5%\n"
            "Metric=Adjusted Diluted EPS; Projection=$0.35-$0.40\n"
            "Full Year 2023 Outlook:\n"
            "Metric=Net Sales growth versus 2022; Projection=6-8%\n"
            "Metric=Adjusted Diluted EPS; Projection=$1.40-$1.45"
        )
        document = self.engine.synthesize({
            "source_id": "news-coordinated-outlook",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": title,
            "text": text,
            "tickers": ["AAA"],
        })
        guidance = [
            row for row in document["statements"]
            if row["concept_leaf"] == "guidance.issued"
        ]
        comparisons = [
            fact for row in guidance for fact in row["typed_facts"]
            if fact["fact_type"] == "estimate_comparison"
        ]
        self.assertEqual([fact["relation"] for fact in comparisons], ["below", "below"])
        self.assertEqual([fact["horizon"] for fact in comparisons], ["Q3", "FY23"])
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "negative")
        self.assertEqual(document["issuer_views"][0]["positive_strength"], 1)
        self.assertNotIn(
            "financial.operating_performance",
            {row["concept_leaf"] for row in document["statements"]},
        )
        table_quotes = [row["evidence_spans"][0]["quote"] for row in guidance]
        self.assertTrue(any("Net Sales growth" in quote and "Projection=3-5%" in quote for quote in table_quotes))
        self.assertTrue(all(row["time_relation"] == "forward" for row in guidance))

    def test_two_consistent_guidance_horizons_control_package_direction(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-guidance-package-dominance",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha results and outlook",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) reports EPS fell and missed estimates; "
                "the company sees Q3 EPS $2.10 vs $2.00 est.; "
                "FY27 EPS $4.20 vs $4.00 est."
            ),
            "tickers": ["AAA"],
        })
        view = document["issuer_views"][0]
        self.assertEqual(view["positive_strength"], 3)
        self.assertEqual(view["negative_strength"], 3)
        self.assertEqual(view["composite_sentiment"], "positive")

    def test_guidance_range_above_consensus_is_positive(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-guidance-range-above-consensus",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha raises outlook",
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) sees FY27 adjusted EPS $2.10-$2.30 vs $2.00 est.",
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")
        guidance = next(row for row in document["statements"] if row["concept_leaf"] == "guidance.issued")
        comparison = next(row for row in guidance["typed_facts"] if row["fact_type"] == "estimate_comparison")
        self.assertEqual(comparison["relation"], "above")

    def test_guidance_range_spanning_consensus_is_neutral(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-guidance-range-spans-consensus",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha provides sales outlook",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) sees Q4 sales "
                "$270.0 million-$295.0 million vs $290.4 million estimate."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "neutral")
        guidance = next(row for row in document["statements"] if row["concept_leaf"] == "guidance.issued")
        comparison = next(row for row in guidance["typed_facts"] if row["fact_type"] == "estimate_comparison")
        self.assertEqual(comparison["subject_lower_value"], "270000000")
        self.assertEqual(comparison["subject_upper_value"], "295000000")
        self.assertEqual(comparison["relation"], "in_line")

    def test_reported_numeric_comparisons_split_metrics_and_drive_direction(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-reported-comparisons",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha reports Q2 EPS $0.40 vs $0.35 est., Sales $28M vs $30M est.",
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) reports Q2 EPS $0.40 vs $0.35 est., Sales $28M vs $30M est.",
            "tickers": ["AAA"],
        })
        earnings = [row for row in document["statements"] if row["concept_leaf"] == "earnings.performance"]
        self.assertEqual(len(earnings), 2)
        relations = [
            fact["relation"] for row in earnings for fact in row["typed_facts"]
            if fact["fact_type"] == "estimate_comparison"
        ]
        self.assertEqual(relations, ["above", "below"])
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "mixed")

    def test_negative_parenthesized_result_beating_estimate_is_positive(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-negative-eps-beat",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha reports Q2 EPS $(0.06) vs $(0.08) estimate",
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) reports Q2 EPS $(0.06) vs $(0.08) estimate.",
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")

    def test_guidance_cut_controls_unrelated_higher_impact_language(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-guidance-cut-higher-impact",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha cuts guidance, anticipates higher impact from inventory rebalancing",
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) cuts guidance and anticipates a higher impact from inventory rebalancing.",
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "negative")

    def test_forward_decline_is_not_positive_growth_guidance(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-negative-growth-guidance",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha sees sales growth in a decline of 1% to 0%",
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) sees sales growth in a decline of 1% to 0%.",
            "tickers": ["AAA"],
        })
        self.assertNotEqual(document["issuer_views"][0]["composite_sentiment"], "positive")

    def test_debt_offering_is_neutral_while_equity_offering_is_negative(self) -> None:
        debt = self.engine.synthesize({
            "source_id": "news-debt-offering",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha announces $300 million senior notes offering",
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) announces a $300 million senior notes offering.",
            "tickers": ["AAA"],
        })
        equity = self.engine.synthesize({
            "source_id": "news-equity-offering",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha will offer 3 million shares of common stock",
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) will offer 3 million shares of common stock.",
            "tickers": ["AAA"],
        })
        self.assertEqual(debt["issuer_views"][0]["composite_sentiment"], "neutral")
        self.assertEqual(equity["issuer_views"][0]["composite_sentiment"], "negative")

    def test_share_combination_to_regain_compliance_is_negative(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-share-combination",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha announces 1-for-20 share combination to regain Nasdaq compliance",
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) announces a 1-for-20 share combination to regain Nasdaq compliance.",
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "negative")

    def test_clinical_primary_endpoint_outcomes_are_directional(self) -> None:
        positive = self.engine.synthesize({
            "source_id": "news-endpoint-met", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha study met its primary endpoint",
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) Phase 3 study met its primary endpoint.",
            "tickers": ["AAA"],
        })
        negative = self.engine.synthesize({
            "source_id": "news-endpoint-missed", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha study did not demonstrate its primary endpoint",
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) Phase 2 study did not demonstrate a statistically significant response on the primary endpoint.",
            "tickers": ["AAA"],
        })
        self.assertEqual(positive["issuer_views"][0]["composite_sentiment"], "positive")
        self.assertEqual(negative["issuer_views"][0]["composite_sentiment"], "negative")

    def test_fiscal_year_results_are_not_mistaken_for_forward_guidance(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-fiscal-year-results",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha FY24 EPS down year over year",
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) FY24 EPS $0.55 down from $0.90 year over year.",
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "negative")
        self.assertIn("earnings.performance", {row["concept_leaf"] for row in document["statements"]})

    def test_reaffirmation_without_directional_change_is_neutral(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-reaffirmed-guidance",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha reaffirms outlook",
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) reaffirmed its full-year EPS guidance.",
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "neutral")

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

    def test_related_links_and_ingestion_metadata_do_not_create_claims(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-boilerplate", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha profile",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) develops research tools. "
                "Related Links: Alpha Falls On Earnings Miss. "
                "Also Read: Alpha Acquisition Rumors. "
                "Source [external:1] Free Stock Analysis."
            ),
            "tickers": ["AAA"],
        })
        concepts = {row["concept_leaf"] for row in document["statements"]}
        self.assertNotIn("market.price_move_observed", concepts)
        self.assertNotIn("earnings.performance", concepts)
        self.assertNotIn("corporate_transaction.acquisition", concepts)

    def test_estimate_revision_requires_a_change(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-estimates", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha analyst note",
            "text": (
                "Analysts estimate Alpha Therapeutics Inc (NASDAQ:AAA) will earn $1.20. "
                "The research firm is raising its EPS estimates for the next two years."
            ),
            "tickers": ["AAA"],
        })
        revisions = [row for row in document["statements"] if row["concept_leaf"] == "estimate.revision"]
        self.assertEqual(len(revisions), 1)
        self.assertIn("raising", revisions[0]["evidence_spans"][0]["quote"])

    def test_causal_mover_headline_is_explain_move(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-mover-purpose", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha Shares Jump After Product Approval",
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) shares jumped after regulators approved its product.",
            "tickers": ["AAA"],
        })
        self.assertEqual(document["envelope"]["communication_purpose"]["value"], "explain_move")

    def test_macro_and_multi_asset_market_headlines_are_market_overviews(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-market-overview", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Stock Futures Fall As Investors Await Jobless Claims",
            "text": "S&P 500 futures fell while investors awaited the weekly jobless claims report.",
        })
        self.assertEqual(document["envelope"]["document_structure"]["value"], "market_overview")
        self.assertEqual(document["envelope"]["communication_purpose"]["value"], "recap")

    def test_analyst_rating_and_target_shorthand_are_supported(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-analyst-shorthand", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Research firm maintains Equal-Weight and lowers PO",
            "text": "The analyst maintains Alpha Therapeutics Inc (NASDAQ:AAA) at Equal-Weight and lowers its PO to $18.",
            "tickers": ["AAA"],
        })
        concepts = {row["concept_leaf"] for row in document["statements"]}
        self.assertIn("analyst.rating_action", concepts)
        self.assertIn("analyst.price_target_action", concepts)

    def test_earnings_schedule_requires_calendar_evidence_not_forecast(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-earnings-schedule", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha earnings preview",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) will release earnings results after market close on Tuesday. "
                "An analyst expects the company will report a strong earnings beat."
            ),
            "tickers": ["AAA"],
        })
        schedules = [row for row in document["statements"] if row["concept_leaf"] == "earnings.release_schedule"]
        self.assertEqual(len(schedules), 1)
        self.assertIn("Tuesday", schedules[0]["evidence_spans"][0]["quote"])

    def test_quantified_cost_reduction_is_cost_efficiency(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-cost-efficiency", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha efficiency update",
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) reduced operating expenses and expects $12 million in annualized savings.",
            "tickers": ["AAA"],
        })
        concepts = {row["concept_leaf"] for row in document["statements"]}
        self.assertIn("operations.cost_efficiency", concepts)

    def test_quantified_job_creation_is_workforce_event(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-workforce", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha expands seasonal workforce",
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) is creating 600 seasonal jobs and will convert qualified employees into full-time roles.",
            "tickers": ["AAA"],
        })
        concepts = {row["concept_leaf"] for row in document["statements"]}
        self.assertIn("operations.workforce", concepts)

    def test_opposing_financing_and_credit_evidence_derives_mixed_sentiment(self) -> None:
        cases = (
            (
                "Alpha prices convertible senior notes to fund capital return",
                "Alpha Therapeutics Inc (NASDAQ:AAA) prices $1.3 billion of convertible senior notes and will use the proceeds to fund capital return.",
                {"capital.financing", "capital.return"},
            ),
            (
                "Alpha credit update",
                "Alpha Therapeutics Inc (NASDAQ:AAA) reports card delinquencies up to 1.59%, while credit-card write-offs are down to 1.73%.",
                {"financial.credit_quality"},
            ),
            (
                "Alpha refinancing",
                "Alpha Therapeutics Inc (NASDAQ:AAA) prices convertible senior notes to repurchase older convertible bonds.",
                {"capital.financing", "capital.structure"},
            ),
        )
        for index, (title, text, expected_concepts) in enumerate(cases):
            with self.subTest(title=title):
                document = self.engine.synthesize({
                    "source_id": f"news-mixed-capital-{index}",
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": title,
                    "text": text,
                    "tickers": ["AAA"],
                })
                concepts = {row["concept_leaf"] for row in document["statements"]}
                self.assertTrue(expected_concepts <= concepts)
                self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "mixed")

    def test_arbitration_and_authorization_tradeoffs_derive_mixed_sentiment(self) -> None:
        arbitration = self.engine.synthesize({
            "source_id": "news-arbitration",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha files arbitration seeking damages",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) files arbitration seeking $250 million in damages. "
                "The company was placed at a competitive disadvantage by discriminatory treatment."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(arbitration["issuer_views"][0]["composite_sentiment"], "mixed")

        authorization = self.engine.synthesize({
            "source_id": "news-authorization",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha authorization update",
            "text": (
                "The FDA issued a letter of authorization to authorize use of Alpha Therapeutics Inc (NASDAQ:AAA) vaccine; "
                "the FDA revised conditions of authorization to include myocarditis and pericarditis reporting requirements."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(authorization["issuer_views"][0]["composite_sentiment"], "mixed")

    def test_complete_response_letter_dominates_partial_approval_and_mitigation(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-complete-response-letter",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha receives Complete Response Letter from FDA for approval supplement",
            "text": (
                "The FDA approved Alpha Therapeutics Inc (NASDAQ:AAA)'s drug product supplement. "
                "The FDA issued a CRL for the drug substance supplement concerning chemistry, "
                "manufacturing and controls. The CRL did not request additional safety or efficacy "
                "studies, and management believes the comments are addressable."
            ),
            "tickers": ["AAA"],
        })
        view = document["issuer_views"][0]
        self.assertEqual(view["composite_sentiment"], "negative")
        self.assertEqual(view["positive_strength"], 2)
        self.assertEqual(view["negative_strength"], 4)

    def test_adverse_regulatory_response_variants_are_strong_negative_events(self) -> None:
        cases = (
            "The FDA issued a CRL to Alpha Therapeutics Inc (NASDAQ:AAA) for its application.",
            "The FDA issued a refuse-to-file letter for Alpha Therapeutics Inc (NASDAQ:AAA)'s application.",
            "The FDA placed Alpha Therapeutics Inc (NASDAQ:AAA)'s study on clinical hold.",
            "The FDA did not approve Alpha Therapeutics Inc (NASDAQ:AAA)'s application.",
        )
        for index, text in enumerate(cases):
            with self.subTest(text=text):
                document = self.engine.synthesize({
                    "source_id": f"news-adverse-regulatory-response-{index}",
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": "Alpha regulatory update",
                    "text": text,
                    "tickers": ["AAA"],
                })
                view = document["issuer_views"][0]
                self.assertEqual(view["composite_sentiment"], "negative")
                self.assertEqual(view["negative_strength"], 4)

    def test_regulatory_approval_remains_positive(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-regulatory-approval",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "FDA approves Alpha application",
            "text": "The FDA approved Alpha Therapeutics Inc (NASDAQ:AAA)'s application.",
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")

    def test_lifted_clinical_hold_is_a_positive_regulatory_resolution(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-lifted-clinical-hold",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "FDA lifts clinical hold on Alpha study",
            "text": "The FDA lifted the clinical hold on Alpha Therapeutics Inc (NASDAQ:AAA)'s study.",
            "tickers": ["AAA"],
        })
        view = document["issuer_views"][0]
        self.assertEqual(view["composite_sentiment"], "positive")
        self.assertEqual(view["positive_strength"], 3)

    def test_canonical_aliases_bind_statements_and_shared_transaction_context(self) -> None:
        engine = NewsSynthesisEngine(IssuerIdentityIndex((
            IssuerIdentity("AAA", "issuer:aaa", "Alpha Holdings", ("Alpha Legacy Corporation",), "NYSE"),
            IssuerIdentity("BBB", "issuer:bbb", "Beta Holdings", ("Beta Industries",), "NYSE"),
        )))
        document = engine.synthesize({
            "source_id": "news-alias-transaction",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha Legacy buys Beta Industries",
            "text": (
                "Alpha Legacy Corporation will combine with Beta Industries. "
                "The two companies expect to complete the amalgamation after approval."
            ),
            "tickers": ["AAA", "BBB"],
        })
        self.assertEqual({row["ticker"] for row in document["entities"]}, {"AAA", "BBB"})
        participated = {row["entity_id"] for row in document["participations"]}
        self.assertEqual(participated, {row["entity_id"] for row in document["entities"]})

    def test_same_issuer_alias_can_resolve_multiple_provider_supported_securities(self) -> None:
        engine = NewsSynthesisEngine(IssuerIdentityIndex((
            IssuerIdentity("AAA", "issuer:alpha", "Alpha Air", ("Alpha Air",), "OTC", security_id="security:aaa"),
            IssuerIdentity("AAB", "issuer:alpha", "Alpha Air", ("Alpha Air",), "OTC", security_id="security:aab"),
        )))
        document = engine.synthesize({
            "source_id": "news-multiple-securities",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha Air doubles its aircraft fleet",
            "text": "Alpha Air doubles its aircraft fleet and expands capacity.",
            "tickers": ["AAA", "AAB"],
        })
        self.assertEqual({row["ticker"] for row in document["entities"]}, {"AAA", "AAB"})

    def test_plural_shareholder_vote_is_governance_statement(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-shareholder-vote",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha shareholders reject proposal",
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) shareholders reject a proposal before the annual meeting.",
            "tickers": ["AAA"],
        })
        concepts = {row["concept_leaf"] for row in document["statements"]}
        self.assertIn("governance.shareholder_vote", concepts)

    def test_index_replacement_assigns_opposite_sentiment_by_issuer_role(self) -> None:
        engine = NewsSynthesisEngine(IssuerIdentityIndex((
            IssuerIdentity("AAA", "issuer:alpha", "Alpha Retail", ("Alpha Retail",), "NYSE"),
            IssuerIdentity("BBB", "issuer:beta", "Beta Pharma", ("Beta Pharma",), "NYSE"),
        )))
        document = engine.synthesize({
            "source_id": "news-index-replacement",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": (
                "Alpha Retail To Join Nasdaq-100 Index Beginning August 10, "
                "Replacing Beta Pharma Following The Scheduled Index Rebalance"
            ),
            "text": (
                "Alpha Retail To Join Nasdaq-100 Index Beginning August 10, "
                "Replacing Beta Pharma Following The Scheduled Index Rebalance"
            ),
            "tickers": ["AAA", "BBB"],
        })
        views = {
            next(row["ticker"] for row in document["entities"] if row["entity_id"] == view["entity_id"]): view
            for view in document["issuer_views"]
        }
        self.assertEqual(views["AAA"]["composite_sentiment"], "positive")
        self.assertEqual(views["BBB"]["composite_sentiment"], "negative")
        self.assertEqual(document["envelope"]["communication_purpose"]["value"], "report")
        self.assertTrue(all(
            row["eligible"]
            for row in document["eligibility"]
            if row["product"] in {"forecast_trigger", "reaction_study"}
        ))

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

    def test_prior_period_metric_values_drive_realized_result_direction(self) -> None:
        cases = (
            ("Alpha Q1 sales $8.675B vs $7.16B in same quarter last year", "positive"),
            ("Alpha Q1 EPS $2.14 down from $7.90 year over year", "negative"),
        )
        for index, (headline, expected) in enumerate(cases):
            with self.subTest(headline=headline):
                document = self.engine.synthesize({
                    "source_id": f"news-period-comparison-{index}",
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": headline,
                    "text": f"Alpha Therapeutics Inc (NASDAQ:AAA) reports {headline.split(' ', 1)[1]}.",
                    "tickers": ["AAA"],
                })
                self.assertEqual(document["issuer_views"][0]["composite_sentiment"], expected)

    def test_explicit_clinical_outcomes_and_first_dosing_are_directional(self) -> None:
        cases = (
            ("Phase 2 study did not demonstrate a significant dose-response relationship on the primary endpoint", "negative"),
            ("Phase 3 study met its primary safety and efficacy endpoints", "positive"),
            ("Alpha doses first subject in a Phase 1 study", "positive"),
        )
        for index, (outcome, expected) in enumerate(cases):
            with self.subTest(outcome=outcome):
                document = self.engine.synthesize({
                    "source_id": f"news-clinical-outcome-{index}",
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": outcome,
                    "text": f"Alpha Therapeutics Inc (NASDAQ:AAA) reports: {outcome}.",
                    "tickers": ["AAA"],
                })
                self.assertEqual(document["issuer_views"][0]["composite_sentiment"], expected)

    def test_takeover_bid_roles_use_canonical_aliases(self) -> None:
        engine = NewsSynthesisEngine(IssuerIdentityIndex((
            IssuerIdentity("AAA", "issuer:aaa", "Alpha Pharmaceuticals International", ("Alpha",), "NYSE"),
            IssuerIdentity("BBB", "issuer:bbb", "BetaBio Incorporated", ("BetaBio",), "NYSE"),
        )))
        document = engine.synthesize({
            "source_id": "news-raised-takeover-bid",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha raises BetaBio takeover bid",
            "text": "Alpha raises its takeover offer for BetaBio above $200 per share.",
            "tickers": ["AAA", "BBB"],
        })
        ticker_by_entity = {row["entity_id"]: row["ticker"] for row in document["entities"]}
        acquisition = next(row for row in document["statements"] if row["concept_leaf"] == "corporate_transaction.acquisition")
        sentiments = {
            ticker_by_entity[row["entity_id"]]: (row["semantic_role"], row["semantic_sentiment"])
            for row in document["participations"]
            if row["statement_id"] == acquisition["statement_id"]
        }
        self.assertEqual(sentiments["AAA"], ("acquirer", "negative"))
        self.assertEqual(sentiments["BBB"], ("target", "positive"))

    def test_dotted_initials_preserve_settlement_costs_in_one_clause(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-settlement-cost",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": (
                "Alpha enters settlement agreement with H.C. Wainwright including "
                "$0.84M cash payment and warrant issuance"
            ),
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) enters settlement agreement "
                "with H.C. Wainwright including $0.84M cash payment and warrant issuance."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "negative")
        self.assertTrue(any(
            "H.C. Wainwright" in span["quote"] and "cash payment" in span["quote"]
            for statement in document["statements"]
            for span in statement["evidence_spans"]
        ))

    def test_beat_and_miss_metric_clauses_are_split_before_sentiment(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-beat-miss-package",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha Q3 EPS $0.33 beats $0.12 estimate, revenues $1.12B misses $1.131B estimate",
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) Q3 EPS $0.33 beats $0.12 estimate, revenues $1.12B misses $1.131B estimate.",
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "mixed")


if __name__ == "__main__":
    unittest.main()
