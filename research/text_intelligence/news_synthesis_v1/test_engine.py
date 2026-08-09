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
from .synthesis import _is_active_regulatory_blocker, derive_issuer_views
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
        self.assertEqual(row["analyst_tickers"], [])

    def test_provider_ticker_without_text_evidence_is_not_an_entity(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-2", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Broad market update", "text": "Inflation declined during the month.",
            "tickers": ["AAA"],
        })
        self.assertEqual(document["entities"], [])
        self.assertIn("unresolved_identity", document["quality_flags"])

    def test_provider_candidate_without_local_identity_remains_fail_closed(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-provider-fallback",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Unfamiliar issuer obtains industry certification",
            "text": "Unfamiliar issuer obtains industry certification.",
            "tickers": ["AAA"],
        })
        self.assertEqual([row["ticker"] for row in document["entities"]], ["AAA"])
        self.assertEqual(document["issuer_views"], [])
        self.assertTrue(document["eligibility"])
        self.assertTrue(all(not row["eligible"] for row in document["eligibility"]))

    def test_strict_local_fallback_uses_same_span_resolved_subject(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-strict-local-fallback",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha opens developer connectivity",
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) opened developer app connectivity.",
            "tickers": ["AAA"],
        })
        view = document["issuer_views"][0]
        self.assertEqual(view["composite_sentiment"], "positive")
        self.assertEqual(view["positive_strength"], 1)
        fallback = next(
            row for row in document["statements"]
            if row["concept_leaf"] == "operations.business_update"
        )
        self.assertEqual(
            fallback["evidence_spans"][0]["quote"],
            "Alpha Therapeutics Inc (NASDAQ:AAA) opened developer app connectivity.",
        )

    def test_strict_local_fallback_does_not_bind_transaction_counterparty(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-strict-local-counterparty",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Beta extends broadcast deal with Alpha",
            "text": (
                "Beta Holdings Corp (NYSE:BBB) extended a broadcast deal with "
                "Alpha Therapeutics Inc (NASDAQ:AAA)."
            ),
            "tickers": ["AAA", "BBB"],
        })
        views = {
            next(row["ticker"] for row in document["entities"] if row["entity_id"] == view["entity_id"]): view
            for view in document["issuer_views"]
        }
        self.assertEqual(views["BBB"]["composite_sentiment"], "positive")
        self.assertNotIn("AAA", views)

    def test_in_scope_provider_candidates_cannot_remain_statement_unbound(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-multi-provider-coverage",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha wins contract involving Beta",
            "text": "Alpha Therapeutics Inc wins a contract involving Beta Holdings Corp.",
            "tickers": ["AAA", "BBB"],
        })
        views_by_ticker = {
            next(entity["ticker"] for entity in document["entities"] if entity["entity_id"] == view["entity_id"]): view
            for view in document["issuer_views"]
        }
        self.assertEqual(set(views_by_ticker), {"AAA", "BBB"})
        self.assertTrue(all(view["statement_ids"] for view in views_by_ticker.values()))

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

    def test_evaluation_target_is_not_provider_identity_evidence(self) -> None:
        engine = NewsSynthesisEngine(IssuerIdentityIndex((
            IssuerIdentity(
                "NSS",
                "issuer:northstar",
                "Northstar Systems Holdings",
                ("Northstar Systems Holdings",),
                "NYSE",
            ),
        )))
        document = engine.synthesize({
            "source_id": "news-evaluation-target",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Northstar wins major customer contract",
            "text": "Northstar wins a major customer contract. It expects deployment next quarter.",
            "evaluation_target_tickers": ["NSS"],
        })
        self.assertEqual([row["ticker"] for row in document["entities"]], ["NSS"])
        self.assertEqual(
            document["entities"][0]["identity_evidence"],
            ["evaluation_target_only"],
        )
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")

    def test_unmentioned_evaluation_target_cannot_trigger_single_subject_backfill(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-unmentioned-evaluation-target",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Unrelated company wins major contract",
            "text": "Unrelated company wins a major customer contract. It expects deployment next quarter.",
            "evaluation_target_tickers": ["AAA"],
        })
        self.assertEqual([row["ticker"] for row in document["entities"]], ["AAA"])
        self.assertEqual(document["issuer_views"], [])
        self.assertEqual(document["entities"][0]["identity_evidence"], ["evaluation_target_only"])

    def test_unscoped_one_word_alias_does_not_create_entity_from_ordinary_prose(self) -> None:
        engine = NewsSynthesisEngine(IssuerIdentityIndex((
            IssuerIdentity("TOT", "issuer:total", "Total SE", ("Total",), "NYSE"),
        )))
        document = engine.synthesize({
            "source_id": "news-generic-one-word-alias",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Industry costs decline",
            "text": "The total cost declined during the quarter.",
        })
        self.assertEqual(document["entities"], [])

    def test_provider_scope_preserves_distinctive_one_word_brand_resolution(self) -> None:
        engine = NewsSynthesisEngine(IssuerIdentityIndex((
            IssuerIdentity("DBX", "issuer:dropbox", "Dropbox Inc", ("Dropbox",), "NASDAQ"),
        )))
        document = engine.synthesize({
            "source_id": "news-provider-one-word-brand",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Dropbox launches new service",
            "text": "Dropbox launches a new enterprise service.",
            "tickers": ["DBX"],
        })
        self.assertEqual([row["ticker"] for row in document["entities"]], ["DBX"])
        self.assertIn(
            "provider_candidate_supported",
            document["entities"][0]["identity_evidence"],
        )

    def test_sparse_investor_and_preclinical_presentations_are_neutral_events(self) -> None:
        for text in (
            "Alpha Therapeutics releases an investor presentation.",
            "Alpha Therapeutics highlights presentation of preclinical data at a conference.",
        ):
            with self.subTest(text=text):
                document = self.engine.synthesize({
                    "source_id": text,
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": text,
                    "text": text,
                    "tickers": ["AAA"],
                })
                self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "neutral")
                self.assertIn(
                    "corporate.communication_event",
                    {row["concept_leaf"] for row in document["statements"]},
                )

    def test_board_election_inflections_create_neutral_governance_view(self) -> None:
        for verb in ("elected", "electing"):
            text = f"Jordan Smith was {verb} to the Alpha Therapeutics board."
            document = self.engine.synthesize({
                "source_id": f"news-board-{verb}",
                "source_timestamp": "2026-08-03T12:00:00Z",
                "title": text,
                "text": text,
                "tickers": ["AAA"],
            })
            self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "neutral")

    def test_unveils_new_solution_is_product_milestone(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-new-solution",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha unveils new cloud solution",
            "text": "Alpha Therapeutics unveils a new cloud solution for hospitals.",
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")

    def test_announced_completed_asset_acquisitions_are_not_facility_closures(self) -> None:
        text = (
            "Alpha Therapeutics announces $297 million of acquisitions and "
            "$91 million of executed purchase agreements, resulting in an "
            "expanded portfolio. "
            "Alpha Therapeutics announced today the closing of $297 million of investments "
            "in medical office facilities."
        )
        document = self.engine.synthesize({
            "source_id": "news-completed-asset-acquisitions",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": (
                "Alpha announces $297 million of acquisitions and executed "
                "purchase agreements"
            ),
            "text": text,
            "tickers": ["AAA"],
        })
        view = document["issuer_views"][0]
        self.assertEqual(view["composite_sentiment"], "positive")
        self.assertFalse(any(
            row["semantic_sentiment"] == "negative"
            for row in document["participations"]
        ))

    def test_seller_is_not_acquirer_in_acquisition_by_another_party(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-acquisition-seller-role",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": (
                "Alpha Therapeutics announces acquisition by Beta Group of "
                "the shares held by Alpha Therapeutics"
            ),
            "text": (
                "Alpha Therapeutics announced the acquisition by Beta Group "
                "of capital held by Alpha Therapeutics. Terms were not disclosed."
            ),
            "tickers": ["AAA"],
        })
        self.assertNotEqual(
            document["issuer_views"][0]["composite_sentiment"],
            "positive",
        )

    def test_offering_proceeds_for_prior_acquisition_are_not_a_new_acquisition(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-offering-funds-prior-acquisition",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha Therapeutics announces public offering",
            "text": (
                "Alpha Therapeutics announced a public offering of 25 million "
                "common shares. Alpha expects to use the net proceeds to fund "
                "its previously announced acquisition."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(
            document["issuer_views"][0]["composite_sentiment"],
            "negative",
        )

    def test_historical_crl_does_not_override_current_accepted_complete_response(self) -> None:
        text = (
            "Alpha Therapeutics filed its complete response to the FDA's November 25, "
            "2023 Complete Response Letter. On August 10, 2026, the FDA acknowledged "
            "receipt and considers this a complete response."
        )
        document = self.engine.synthesize({
            "source_id": "news-accepted-complete-response",
            "source_timestamp": "2026-08-11T12:00:00Z",
            "title": "FDA accepts complete response submission",
            "text": text,
            "tickers": ["AAA"],
        })
        view = document["issuer_views"][0]
        self.assertEqual(view["composite_sentiment"], "positive")
        adverse = [
            row for row in document["statements"]
            if row["concept_leaf"] == "clinical.regulatory_milestone"
            and any(
                fact.get("outcome_class") == "adverse"
                for fact in row["typed_facts"]
            )
        ]
        self.assertTrue(adverse)
        adverse_ids = {row["statement_id"] for row in adverse}
        self.assertFalse(any(
            row["statement_id"] in adverse_ids
            and row["semantic_sentiment"] == "negative"
            and row["sentiment_strength"] > 1
            for row in document["participations"]
        ))

    def test_current_rejection_referencing_old_crl_remains_current(self) -> None:
        text = (
            "The FDA has issued a response letter. The letter stated that the "
            "FDA did not consider the resubmitted application a complete response "
            "to deficiencies identified in the FDA's October 2020 Complete "
            "Response Letter. The FDA will not begin substantive review."
        )
        document = self.engine.synthesize({
            "source_id": "news-current-rejection-old-crl-reference",
            "source_timestamp": "2022-02-22T12:00:00Z",
            "title": "FDA rejects Alpha Therapeutics application again",
            "text": text,
            "tickers": ["AAA"],
        })
        self.assertEqual(
            document["issuer_views"][0]["composite_sentiment"],
            "negative",
        )

    def test_response_letter_not_approvable_is_adverse(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-response-letter-not-approvable",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": (
                "Alpha Therapeutics informed FDA issued response letter "
                "indicating application is not approvable"
            ),
            "text": (
                "Alpha Therapeutics was informed that the FDA issued a response "
                "letter indicating the application is not approvable."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(
            document["issuer_views"][0]["composite_sentiment"],
            "negative",
        )

    def test_statistically_significant_immune_response_has_adverse_override(self) -> None:
        cases = (
            ("Alpha reports statistically significant immune response data.", "positive"),
            ("Alpha reports statistically significant adverse immune response data.", "negative"),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                document = self.engine.synthesize({
                    "source_id": text,
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": text,
                    "text": text,
                    "tickers": ["AAA"],
                })
                self.assertEqual(document["issuer_views"][0]["composite_sentiment"], expected)

    def test_planned_priority_review_request_is_weak_positive(self) -> None:
        text = "Alpha Therapeutics plans to seek FDA priority review for its sNDA."
        document = self.engine.synthesize({
            "source_id": "news-priority-review-plan",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": text,
            "text": text,
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")
        self.assertEqual(document["issuer_views"][0]["positive_strength"], 1)

    def test_first_pilot_unit_shipment_is_product_milestone(self) -> None:
        text = "Alpha Therapeutics ships first pilot units to hospital production teams."
        document = self.engine.synthesize({
            "source_id": "news-first-pilot-units",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": text,
            "text": text,
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")

    def test_product_update_and_office_opening_have_typed_sparse_views(self) -> None:
        cases = (
            ("Alpha Therapeutics provides an update on AlphaCare.", "neutral"),
            ("Alpha Therapeutics opens an office in Qatar.", "positive"),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                document = self.engine.synthesize({
                    "source_id": text,
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": text,
                    "text": text,
                    "tickers": ["AAA"],
                })
                self.assertEqual(document["issuer_views"][0]["composite_sentiment"], expected)

    def test_named_product_market_unavailability_uses_local_negative_fallback(self) -> None:
        text = "Alpha Therapeutics says Alpha AI will not be available in China."
        document = self.engine.synthesize({
            "source_id": "news-product-unavailable",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": text,
            "text": text,
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "negative")

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

    def test_exchange_prefixed_identity_authority_does_not_duplicate_canonical_security(self) -> None:
        engine = NewsSynthesisEngine(IssuerIdentityIndex((
            IssuerIdentity(
                "TSX:AAA",
                "issuer:aaa",
                "Alpha Copper",
                ("Alpha Copper", "TSX:AAA"),
                "TSX",
            ),
        )))
        text = "Alpha Copper (TSX:AAA) announced a public offering."
        document = engine.synthesize({
            "source_id": "news-prefixed-identity-authority",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": text,
            "text": text,
            "tickers": ["TSX:AAA"],
        })
        self.assertEqual([row["ticker"] for row in document["entities"]], ["TSX:AAA"])
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

    def test_candidate_scoped_public_brand_aliases_bind_long_legal_names(self) -> None:
        index = IssuerIdentityIndex((
            IssuerIdentity("AAA", "issuer:aaa", "Alpha Budget Group", ("Alpha Budget Group",)),
            IssuerIdentity("BBB", "issuer:bbb", "Bravo Global Holdings", ("Bravo Global Holdings",)),
            IssuerIdentity("CCC", "issuer:ccc", "Ceta Pharmaceutical Industries", ("Ceta Pharmaceutical Industries",)),
        ))

        entities = index.resolve(
            text="Watch Alpha, Bravo and Ceta shares.",
            candidates=("AAA", "BBB", "CCC"),
            timestamp="2026-08-03T12:00:00Z",
        )

        self.assertEqual({row["ticker"] for row in entities}, {"AAA", "BBB", "CCC"})
        self.assertTrue(all(
            any(value.startswith("candidate_alias:") for value in row["identity_evidence"])
            for row in entities
        ))

    def test_candidate_scoped_brand_alias_never_resolves_outside_provider_scope(self) -> None:
        index = IssuerIdentityIndex((
            IssuerIdentity("AAA", "issuer:aaa", "Alpha Budget Group", ("Alpha Budget Group",)),
            IssuerIdentity("BBB", "issuer:bbb", "Bravo Systems", ("Bravo Systems",)),
        ))

        entities = index.resolve(
            text="Alpha announced an update.",
            candidates=("BBB",),
            timestamp="2026-08-03T12:00:00Z",
        )

        self.assertNotIn("AAA", {row["ticker"] for row in entities})

    def test_candidate_scoped_alias_rejects_noisy_leading_modifier(self) -> None:
        index = IssuerIdentityIndex((
            IssuerIdentity(
                "WMT",
                "issuer:wmt",
                "Meanwhile Wal Mart Stores",
                ("Meanwhile Wal Mart Stores", "Walmart"),
            ),
        ))

        entities = index.resolve(
            text="Meanwhile, retail demand improved.",
            candidates=("WMT",),
            timestamp="2026-08-03T12:00:00Z",
        )

        self.assertEqual(entities, [])

    def test_title_only_attributed_assessment_language_is_directional(self) -> None:
        cases = (
            ("Hearing Northstar Securities out in defense of Alpha Therapeutics", "positive"),
            ("Northstar discusses its Alpha Therapeutics short thesis", "negative"),
            ("Northstar Research challenges Alpha Therapeutics; says $AAA is scamming customers", "negative"),
        )
        for index, (title, expected) in enumerate(cases):
            with self.subTest(title=title):
                document = self.engine.synthesize({
                    "source_id": f"news-attributed-assessment-{index}",
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": title,
                    "text": title,
                    "tickers": ["AAA"],
                    "render_status": "title_only",
                    "quality_flags": ["no_renderable_sources"],
                })
                self.assertEqual(document["issuer_views"][0]["composite_sentiment"], expected)
                self.assertEqual(document["envelope"]["information_origin"]["value"], "analyst")
                self.assertTrue(any(
                    row["product"] == "analyst_evaluation" and row["eligible"]
                    for row in document["eligibility"]
                ))

    def test_attributed_profitability_and_position_assessments_remain_two_sided(self) -> None:
        title = (
            "Northstar's Lee tells the media Alpha projects are unlikely to be profitable, "
            "but Alpha is in a great position to lead the market."
        )
        document = self.engine.synthesize({
            "source_id": "news-two-sided-attributed-assessment",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": title,
            "text": title,
            "tickers": ["AAA"],
            "render_status": "title_only",
        })

        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "mixed")
        self.assertEqual(
            {row["semantic_sentiment"] for row in document["participations"]},
            {"positive", "negative"},
        )

    def test_issuer_self_description_is_not_recast_as_analyst_assessment(self) -> None:
        title = "Alpha says it is in a great position to serve customers"
        document = self.engine.synthesize({
            "source_id": "news-issuer-self-description",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": title,
            "text": title,
            "tickers": ["AAA"],
        })

        self.assertNotIn(
            "analyst.issuer_assessment",
            {row["concept_leaf"] for row in document["statements"]},
        )
        self.assertNotEqual(document["envelope"]["information_origin"]["value"], "analyst")

    def test_published_displacement_evidence_binds_affected_provider_candidates(self) -> None:
        engine = NewsSynthesisEngine(IssuerIdentityIndex((
            IssuerIdentity("AAA", "issuer:aaa", "Alpha Rental Group", ("Alpha Rental Group",)),
            IssuerIdentity("BBB", "issuer:bbb", "Bravo Global Holdings", ("Bravo Global Holdings",)),
            IssuerIdentity("NSS", "issuer:nss", "Northstar Securities", ("Northstar Securities",)),
        )))
        title = (
            "Northstar Securities publishes evidence that sharing models are already "
            "hitting rental cars; watch Alpha and Bravo shares"
        )
        document = engine.synthesize({
            "source_id": "news-displacement-assessment",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": title,
            "text": title,
            "tickers": ["AAA", "BBB"],
            "render_status": "title_only",
        })
        views = {
            next(entity["ticker"] for entity in document["entities"] if entity["entity_id"] == view["entity_id"]): view["composite_sentiment"]
            for view in document["issuer_views"]
        }

        self.assertEqual(views["AAA"], "negative")
        self.assertEqual(views["BBB"], "negative")
        self.assertNotIn("NSS", views)

    def test_judicial_invalidation_of_named_regulatory_limit_is_weak_positive_event(self) -> None:
        title = "Watch Alpha and Beta after court called the regulator's limitations arbitrary"
        document = self.engine.synthesize({
            "source_id": "news-judicial-regulatory-limit",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": title,
            "text": title,
            "tickers": ["AAA", "BBB"],
            "render_status": "title_only",
        })
        views = {
            next(entity["ticker"] for entity in document["entities"] if entity["entity_id"] == view["entity_id"]): view["composite_sentiment"]
            for view in document["issuer_views"]
        }

        self.assertEqual(views, {"AAA": "positive", "BBB": "positive"})
        self.assertTrue(all(
            row["product"] != "forecast_trigger" or row["eligible"]
            for row in document["eligibility"]
        ))

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

    def test_structural_digest_lede_blocks_cross_issuer_forecast_continuity(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-structural-digest", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Daily Biotech Pulse",
            "text": (
                "Here is a roundup of the top developments. "
                "Alpha Therapeutics Inc (NASDAQ:AAA) reported positive Phase 3 results. "
                "Beta Holdings Corp (NYSE:BBB) was also among today's headlines."
            ),
            "tickers": ["AAA", "BBB"],
        })
        self.assertEqual(document["envelope"]["document_structure"]["value"], "multi_subject_digest")
        self.assertEqual(document["envelope"]["communication_purpose"]["value"], "recap")
        eligibility = {
            next(entity["ticker"] for entity in document["entities"] if entity["entity_id"] == row["entity_id"]): row["eligible"]
            for row in document["eligibility"]
            if row["product"] == "forecast_trigger"
        }
        self.assertFalse(eligibility["BBB"])

    def test_operational_analyst_homonyms_do_not_change_document_origin(self) -> None:
        bodies = (
            "The company maintains minimum bid compliance after its current filing.",
            "The company initiates Phase 3 enrollment after regulatory clearance.",
            "The design upgrade needs additional manufacturing capacity.",
            "The primary scientific analysis showed a statistically significant benefit.",
        )
        for index, body in enumerate(bodies):
            with self.subTest(body=body):
                document = self.engine.synthesize({
                    "source_id": f"news-operational-homonym-{index}",
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": "Alpha reports current operating update",
                    "text": f"Alpha Therapeutics Inc (NASDAQ:AAA) announced today. {body}",
                    "tickers": ["AAA"],
                })
                self.assertNotEqual(document["envelope"]["information_origin"]["value"], "analyst")
                self.assertEqual(document["envelope"]["communication_purpose"]["value"], "report")

    def test_earnings_schedule_list_is_preview_reference_material(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-earnings-list", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Earnings Scheduled For Tuesday",
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) is scheduled to report after market close.",
            "tickers": ["AAA"],
        })
        self.assertEqual(document["envelope"]["document_structure"]["value"], "reference_list")
        self.assertEqual(document["envelope"]["communication_purpose"]["value"], "preview")
        self.assertFalse(next(
            row["eligible"] for row in document["eligibility"]
            if row["product"] == "forecast_trigger"
        ))

    def test_market_section_grammar_restores_recap_without_generic_macro_scan(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-market-sections", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Afternoon trading update",
            "text": (
                "U.S. stocks were trading higher. Leading and Lagging Sectors. "
                "Equities Trading UP: Alpha Therapeutics Inc (NASDAQ:AAA) reported strong results."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["envelope"]["document_structure"]["value"], "market_overview")
        self.assertEqual(document["envelope"]["communication_purpose"]["value"], "recap")

    def test_explicit_research_genres_remain_analysis_and_analyst_origin(self) -> None:
        for title in (
            "Updated Research Report on Alpha - Analyst Blog",
            "Alpha - Bear of the Day",
        ):
            with self.subTest(title=title):
                document = self.engine.synthesize({
                    "source_id": "news-research-genre", "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": title,
                    "text": (
                        "Zacks Equity Research assigns Alpha Therapeutics Inc (NASDAQ:AAA) "
                        "an Underperform view and a $12 price target."
                    ),
                    "tickers": ["AAA"],
                })
                self.assertEqual(document["envelope"]["communication_purpose"]["value"], "analyze")
                self.assertEqual(document["envelope"]["information_origin"]["value"], "analyst")

    def test_stock_tumbles_as_headline_is_explain_move(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-tumbles", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha Stock Tumbles As Founders Depart",
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) shares fell after its founders departed.",
            "tickers": ["AAA"],
        })
        self.assertEqual(document["envelope"]["communication_purpose"]["value"], "explain_move")

    def test_operating_sales_up_is_not_a_stock_move(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-sales-up", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha December Sales Up 25.5%",
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) reported December sales rose 25.5%.",
            "tickers": ["AAA"],
        })
        self.assertEqual(document["envelope"]["communication_purpose"]["value"], "report")

    def test_ticker_suffixed_bare_percentage_title_is_stock_move(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-bare-mover", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha Therapeutics Up 3% (AAA)",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) shares rose after reporting results "
                "and updating its annual guidance."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["envelope"]["communication_purpose"]["value"], "explain_move")

    def test_ticker_suffixed_operating_percentage_is_not_stock_move(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-bare-operating", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha December Sales Up 25.5% (AAA)",
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) reported December sales rose 25.5%.",
            "tickers": ["AAA"],
        })
        self.assertEqual(document["envelope"]["communication_purpose"]["value"], "report")

    def test_after_market_close_is_not_a_market_overview(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-after-close", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha Announces Reverse Split Effective After Market Close",
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) announced an approved reverse split.",
            "tickers": ["AAA"],
        })
        self.assertEqual(document["envelope"]["document_structure"]["value"], "single_subject")
        self.assertEqual(document["envelope"]["communication_purpose"]["value"], "report")

    def test_issuer_earnings_call_is_not_analyst_origin_only(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-issuer-call", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha Q2 FY2026 Earnings Call Transcript",
            "text": (
                "Management reports Alpha Therapeutics Inc (NASDAQ:AAA) revenue increased 20%. "
                "Analyst: Can you discuss guidance? The company expects further growth."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["envelope"]["communication_purpose"]["value"], "report")
        self.assertEqual(document["envelope"]["information_origin"]["value"], "mixed")

    def test_credit_rating_paragraph_does_not_define_document_as_analyst_origin(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-credit-rating", "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha Receives Benefits Grace Period Amid Sharp Decline",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) received a contribution grace period. "
                "Rating service Moody's Investors Service downgraded the company's debt last month."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["envelope"]["communication_purpose"]["value"], "report")
        self.assertNotEqual(document["envelope"]["information_origin"]["value"], "analyst")

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
            "source_timestamp": "2023-08-03T12:00:00Z",
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

    def test_hypothetical_solution_success_in_risk_inventory_is_not_a_product_milestone(self) -> None:
        doc = self.engine.synthesize({
            "source_id": "news-hypothetical-solution-risk",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha risk factors",
            "text": (
                "Alpha Therapeutics risks include the degree of its success at introducing "
                "new or improved products and solutions that gain market share."
            ),
            "tickers": ["AAA"],
        })
        self.assertFalse(
            any(row["concept_leaf"] == "product.milestone" for row in doc["statements"])
        )

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

    def test_parenthetical_consensus_guidance_rows_emit_comparisons(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-parenthetical-guidance-comparisons",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha issues Q1 outlook below consensus",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) sees Q1 revenue of "
                "$91 million-$96 million (consensus $101.3 million), adjusted "
                "EPS of $0.47-$0.53 (consensus $0.58)."
            ),
            "tickers": ["AAA"],
        })
        comparisons = [
            fact
            for row in document["statements"]
            if row["concept_leaf"] == "guidance.issued"
            for fact in row["typed_facts"]
            if fact["fact_type"] == "estimate_comparison"
        ]
        self.assertEqual({fact["metric"] for fact in comparisons}, {"revenue", "eps"})
        self.assertTrue(all(fact["relation"] == "below" for fact in comparisons))
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "negative")

    def test_per_share_filler_and_prior_guidance_ranges_are_structured(self) -> None:
        cases = (
            (
                "Alpha expects Q4 earnings of 73 cents to 77 cents per share, "
                "versus analysts' estimates of 78 cents per share.",
                "consensus_estimate",
            ),
            (
                "Alpha now expects FY adjusted earnings of $4.50 to $4.70 per share, "
                "versus earlier forecast of $4.60 to $5.00 per share.",
                "management_guidance",
            ),
        )
        for index, (text, comparator_role) in enumerate(cases):
            with self.subTest(comparator_role=comparator_role):
                document = self.engine.synthesize({
                    "source_id": f"news-guidance-comparator-{index}",
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": "Alpha issues outlook",
                    "text": f"Alpha Therapeutics Inc (NASDAQ:AAA) {text}",
                    "tickers": ["AAA"],
                })
                comparisons = [
                    fact
                    for row in document["statements"]
                    if row["concept_leaf"] == "guidance.issued"
                    for fact in row["typed_facts"]
                    if fact["fact_type"] == "estimate_comparison"
                    and fact["comparator_role"] == comparator_role
                ]
                self.assertTrue(comparisons)
                self.assertTrue(all(fact["relation"] == "below" for fact in comparisons))
                self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "negative")

    def test_adjacent_analyst_metric_list_is_bounded_guidance_context(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-adjacent-guidance-comparator",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha issues weak forecast",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) expects Q4 adjusted earnings "
                "of $0.75-$0.95 per share and revenue of $4.7 billion-$5.7 billion. "
                "Analysts projected earnings of $1.08 per share and revenue of "
                "$6.13 billion."
            ),
            "tickers": ["AAA"],
        })
        comparisons = [
            fact
            for row in document["statements"]
            if row["concept_leaf"] == "guidance.issued"
            for fact in row["typed_facts"]
            if fact["fact_type"] == "estimate_comparison"
        ]
        self.assertEqual({fact["metric"] for fact in comparisons}, {"eps", "revenue"})
        self.assertTrue(all(fact["relation"] == "below" for fact in comparisons))
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "negative")
        guidance = next(
            row for row in document["statements"]
            if row["concept_leaf"] == "guidance.issued"
            and any(
                fact["fact_type"] == "estimate_comparison"
                for fact in row["typed_facts"]
            )
        )
        evidence_text = " ".join(
            span["quote"] for span in guidance["evidence_spans"]
        )
        self.assertIn("Analysts projected", evidence_text)

    def test_adjacent_guidance_context_does_not_cross_paragraph_boundary(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-guidance-comparator-new-paragraph",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha provides outlook",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) expects Q4 earnings of $0.75-$0.95.\n\n"
                "Analysts projected earnings of $1.08."
            ),
            "tickers": ["AAA"],
        })
        self.assertFalse(any(
            fact["fact_type"] == "estimate_comparison"
            for row in document["statements"]
            if row["concept_leaf"] == "guidance.issued"
            for fact in row["typed_facts"]
        ))

    def test_forecast_loss_comparison_uses_signed_economics(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-guidance-loss-comparison",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha forecast disappoints",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) forecast a loss of 9 cents "
                "to 11 cents per share for Q1. Analysts expected a loss of 1 cent "
                "per share."
            ),
            "tickers": ["AAA"],
        })
        comparisons = [
            fact
            for row in document["statements"]
            if row["concept_leaf"] == "guidance.issued"
            for fact in row["typed_facts"]
            if fact["fact_type"] == "estimate_comparison"
        ]
        self.assertTrue(comparisons)
        self.assertEqual(comparisons[0]["metric"], "eps_loss")
        self.assertEqual(comparisons[0]["relation"], "below")
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "negative")

    def test_authored_guidance_relation_must_agree_with_numeric_bounds(self) -> None:
        facts = extract_typed_facts([{
            "source_field": "rendered_text",
            "start": 0,
            "end": 42,
            "quote": "EPS of $2.10 above consensus of $2.20",
        }])
        self.assertFalse(any(
            fact["fact_type"] == "estimate_comparison" for fact in facts
        ))

    def test_bare_versus_period_token_is_not_consensus(self) -> None:
        facts = extract_typed_facts([{
            "source_field": "rendered_text",
            "start": 0,
            "end": 30,
            "quote": "Sales growth 2.7% versus 3Q19",
        }])
        self.assertFalse(any(
            fact["fact_type"] == "estimate_comparison" for fact in facts
        ))

    def test_trailing_range_scale_applies_to_both_endpoints(self) -> None:
        facts = extract_typed_facts([{
            "source_field": "rendered_text",
            "start": 0,
            "end": 70,
            "quote": (
                "Revenue guidance $3.93-$3.98 billion versus prior guidance "
                "$3.986-$4.08 billion"
            ),
        }], estimate_subject_role="issuer_guidance")
        comparison = next(
            fact for fact in facts
            if fact["fact_type"] == "estimate_comparison"
            and fact["comparator_role"] == "management_guidance"
        )
        self.assertEqual(comparison["subject_lower_value"], "3930000000")
        self.assertEqual(comparison["subject_upper_value"], "3980000000")
        self.assertEqual(comparison["relation"], "below")

    def test_previously_seen_guidance_range_is_management_comparator(self) -> None:
        facts = extract_typed_facts([{
            "source_field": "rendered_text",
            "start": 0,
            "end": 48,
            "quote": "FY EBITDA $147-$162M, had seen $180-$250M",
        }], estimate_subject_role="issuer_guidance")
        comparison = next(
            fact for fact in facts
            if fact["fact_type"] == "estimate_comparison"
        )
        self.assertEqual(comparison["comparator_role"], "management_guidance")
        self.assertEqual(comparison["relation"], "below")

    def test_realized_copular_result_comparison_is_structured(self) -> None:
        facts = extract_typed_facts([{
            "source_field": "rendered_text",
            "start": 0,
            "end": 48,
            "quote": "Sales were $28.04M versus estimates $28.72M",
        }], estimate_subject_role="reported_result")
        comparison = next(
            fact for fact in facts if fact["fact_type"] == "estimate_comparison"
        )
        self.assertEqual(comparison["metric"], "sales")
        self.assertEqual(comparison["relation"], "below")

    def test_explicit_guidance_metric_is_not_realized_performance(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-guidance-not-realized",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha issues disappointing guidance",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) EPS guidance of $0.20-$0.21 "
                "fell short of consensus $0.24."
            ),
            "tickers": ["AAA"],
        })
        self.assertFalse(any(
            row["concept_leaf"] in {
                "earnings.performance", "financial.operating_performance"
            }
            for row in document["statements"]
        ))

    def test_adjacent_realized_beat_and_miss_form_mixed_package(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-adjacent-realized-tradeoff",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha reports quarterly results",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) reported Q2 adjusted EPS "
                "$0.17 versus estimates $0.12. Sales were $28.04 million versus "
                "estimates $28.72 million."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "mixed")

    def test_adverse_guidance_controls_adjacent_realized_tradeoff(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-adverse-guidance-controls-results",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha posts sales miss and weak guidance",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) reported Q4 EPS $1.95 "
                "versus estimates $1.67. Sales were $13.48 billion versus "
                "estimates $13.62 billion. The company guided Q1 earnings "
                "$0.35-$0.40 versus consensus $0.49."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "negative")

    def test_clinical_hold_lifecycle_distinguishes_resolution_from_request(self) -> None:
        cases = (
            ("FDA removed the clinical hold on Alpha's trial.", "positive"),
            ("FDA placed Alpha's trial on clinical hold.", "negative"),
            ("Alpha requested removal of the clinical hold.", "negative"),
        )
        for index, (event, expected) in enumerate(cases):
            with self.subTest(event=event):
                document = self.engine.synthesize({
                    "source_id": f"news-hold-lifecycle-{index}",
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": event,
                    "text": f"Alpha Therapeutics Inc (NASDAQ:AAA) {event}",
                    "tickers": ["AAA"],
                })
                self.assertEqual(document["issuer_views"][0]["composite_sentiment"], expected)

    def test_reclassified_adverse_diagnosis_offsets_unresolved_hold(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-clinical-reassessment-with-hold",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": (
                "Alpha investigator concluded reported case was not a case of "
                "myelodysplastic syndrome and revised the diagnosis to anemia"
            ),
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) said the investigator concluded "
                "the reported case was not a case of myelodysplastic syndrome and "
                "revised the diagnosis to anemia. Alpha continues to seek removal of "
                "the clinical hold."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "mixed")

    def test_issuer_challenge_to_active_regulatory_hold_is_mixed(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-regulatory-challenge-with-hold",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha files complaint against FDA over clinical hold",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) filed a complaint against the FDA "
                "requesting that the court lift the partial clinical hold imposed on "
                "Alpha's study."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "mixed")

    def test_complete_class_two_response_supersedes_prior_crl(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-class-two-response-accepted",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "FDA accepts Alpha response as complete Class 2 resubmission",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) said FDA acknowledged its full "
                "response to the Complete Response Letter as a complete Class 2 response. "
                "The submission addresses issues raised in the prior CRL."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")

    def test_rejected_investigation_petition_differs_from_rejected_application(self) -> None:
        cases = (
            ("NHTSA rejects petition to open investigation into Alpha vehicles", "positive"),
            ("FDA rejects Alpha's application", "negative"),
        )
        for index, (title, expected) in enumerate(cases):
            with self.subTest(title=title):
                document = self.engine.synthesize({
                    "source_id": f"news-rejection-object-{index}",
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": title,
                    "text": f"Alpha Therapeutics Inc (NASDAQ:AAA). {title}.",
                    "tickers": ["AAA"],
                })
                self.assertEqual(document["issuer_views"][0]["composite_sentiment"], expected)

    def test_concluded_investigation_without_charges_is_positive(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-investigation-concluded",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Investigation concludes without charges",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) said the SEC will recommend no "
                "charges and both investigations have concluded for the company."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")

    def test_completed_convertible_note_repayment_is_deleveraging(self) -> None:
        cases = (
            ("Alpha repaid the final payment on its convertible notes", "positive"),
            ("Alpha issued convertible notes", "negative"),
        )
        for index, (title, expected) in enumerate(cases):
            with self.subTest(title=title):
                document = self.engine.synthesize({
                    "source_id": f"news-debt-transition-{index}",
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": title,
                    "text": f"Alpha Therapeutics Inc (NASDAQ:AAA) {title}.",
                    "tickers": ["AAA"],
                })
                self.assertEqual(document["issuer_views"][0]["composite_sentiment"], expected)

    def test_lawsuit_dismissal_uses_defendant_or_plaintiff_role(self) -> None:
        cases = (
            ("Class action lawsuit against Alpha was dismissed without prejudice", "positive"),
            ("Court dismissed Alpha Therapeutics' lawsuit", "negative"),
        )
        for index, (title, expected) in enumerate(cases):
            with self.subTest(title=title):
                document = self.engine.synthesize({
                    "source_id": f"news-dismissal-role-{index}",
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": title,
                    "text": f"Alpha Therapeutics Inc (NASDAQ:AAA). {title}.",
                    "tickers": ["AAA"],
                })
                self.assertEqual(document["issuer_views"][0]["composite_sentiment"], expected)

    def test_settlement_cash_direction_controls_issuer_sentiment(self) -> None:
        cases = (
            ("Alpha will receive a $38 million settlement payment", "positive"),
            ("Alpha will pay a $38 million settlement penalty", "negative"),
        )
        for index, (title, expected) in enumerate(cases):
            with self.subTest(title=title):
                document = self.engine.synthesize({
                    "source_id": f"news-settlement-role-{index}",
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": title,
                    "text": f"Alpha Therapeutics Inc (NASDAQ:AAA) {title}.",
                    "tickers": ["AAA"],
                })
                self.assertEqual(document["issuer_views"][0]["composite_sentiment"], expected)

    def test_non_us_regulatory_approval_authority_is_supported(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-sfda-approval",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha receives SFDA approval for its device",
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) obtained State Food and Drug Administration approval for its device.",
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")

    def test_structured_legal_action_morphology_and_disposition(self) -> None:
        cases = (
            ("Alpha won an IP rights case against a challenger", "positive"),
            ("Department of Labor sues Alpha for gender pay discrimination", "negative"),
            ("Prosecution office investigates Alpha for provider interactions", "negative"),
            ("SEC charged Alpha with fraud and Alpha agreed to pay a civil penalty", "negative"),
            ("Patent case remanded for trial; Alpha intends to defend the claims", "negative"),
            ("Court dismissed as moot Alpha's action against a fund", "neutral"),
        )
        for index, (title, expected) in enumerate(cases):
            with self.subTest(title=title):
                document = self.engine.synthesize({
                    "source_id": f"news-legal-action-{index}",
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": title,
                    "text": f"Alpha Therapeutics Inc (NASDAQ:AAA). {title}.",
                    "tickers": ["AAA"],
                })
                self.assertEqual(document["issuer_views"][0]["composite_sentiment"], expected)

    def test_clinical_publication_and_program_data_patterns(self) -> None:
        cases = (
            "Alpha will present positive therapy data from its clinical program",
            "Publication of data shows Alpha-101 induces a cellular process linked to prevention and treatment of disease",
        )
        for index, title in enumerate(cases):
            with self.subTest(title=title):
                document = self.engine.synthesize({
                    "source_id": f"news-clinical-publication-{index}",
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": title,
                    "text": f"Alpha Therapeutics Inc (NASDAQ:AAA). {title}.",
                    "tickers": ["AAA"],
                })
                self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")

    def test_biosimilar_comparability_uses_sponsor_and_incumbent_roles(self) -> None:
        engine = NewsSynthesisEngine(IssuerIdentityIndex((
            IssuerIdentity("AAA", "issuer:aaa", "Alpha Therapeutics", ("Alpha",), "NASDAQ"),
            IssuerIdentity("BBB", "issuer:bbb", "Bravo Holdings", ("Bravo",), "NYSE"),
        )))
        document = engine.synthesize({
            "source_id": "news-biosimilar-role",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "FDA staff says data shows Alpha's biosimilar is highly similar to Bravo's therapy",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) and Bravo Holdings (NYSE:BBB). "
                "FDA staff says data shows Alpha's biosimilar is highly similar to Bravo's therapy."
            ),
            "tickers": ["AAA", "BBB"],
        })
        sentiments = {
            next(row["ticker"] for row in document["entities"] if row["entity_id"] == view["entity_id"]): view["composite_sentiment"]
            for view in document["issuer_views"]
        }
        self.assertEqual(sentiments, {"AAA": "positive", "BBB": "negative"})

    def test_insider_purchase_and_sale_morphology(self) -> None:
        cases = (
            ("Alpha president purchased 1,000 shares", "positive"),
            ("Alpha insider sold 225,000 shares", "negative"),
        )
        for index, (title, expected) in enumerate(cases):
            with self.subTest(title=title):
                document = self.engine.synthesize({
                    "source_id": f"news-insider-position-{index}",
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": title,
                    "text": f"Alpha Therapeutics Inc (NASDAQ:AAA). {title}.",
                    "tickers": ["AAA"],
                })
                self.assertEqual(document["issuer_views"][0]["composite_sentiment"], expected)

    def test_warrant_registration_ads_pricing_and_block_trade(self) -> None:
        cases = (
            ("Alpha registers 9.7 million shares issuable on warrant exercise", "negative"),
            ("Alpha prices 27.4 million ADS at $10.50 per ADS", "neutral"),
            ("Notable block trades in Alpha totaling 2.56 million shares", "neutral"),
        )
        for index, (title, expected) in enumerate(cases):
            with self.subTest(title=title):
                document = self.engine.synthesize({
                    "source_id": f"news-market-financing-{index}",
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": title,
                    "text": f"Alpha Therapeutics Inc (NASDAQ:AAA). {title}.",
                    "tickers": ["AAA"],
                })
                self.assertEqual(document["issuer_views"][0]["composite_sentiment"], expected)

    def test_estimated_unbenchmarked_ads_ipo_range_is_neutral(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-estimated-ads-ipo",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha says estimated U.S. IPO price will be between $13-$14 per ADS",
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) says estimated U.S. IPO price will be between $13-$14 per ADS.",
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "neutral")

    def test_focal_below_consensus_guidance_controls_realized_beat(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-focal-weak-guidance",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha posts earnings beat, issues weak Q4 forecast",
            "text": (
                "Title: Alpha posts earnings beat, issues weak Q4 forecast\n"
                "Alpha Therapeutics Inc (NASDAQ:AAA) reported Q3 EPS of $1.20 "
                "versus consensus of $0.80. For Q4, Alpha projects earnings of "
                "$0.73 to $0.77 per share versus analysts' estimates of $0.78."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "negative")

    def test_nonfocal_small_guidance_miss_does_not_force_negative(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-nonfocal-guidance-miss",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha reports record results and provides outlook",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) reported Q3 EPS of $2.00 "
                "versus consensus of $1.00 and record revenue. Alpha sees Q4 "
                "EPS of $2.09 versus consensus of $2.10."
            ),
            "tickers": ["AAA"],
        })
        self.assertNotEqual(document["issuer_views"][0]["composite_sentiment"], "negative")

    def test_coordinated_reported_metrics_preserve_opposite_directions(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-coordinated-reported-metrics",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": (
                "Alpha Q1 EPS $(0.84) Down From $(0.02) YoY, Sales $58.0M "
                "Beat $57.2M Estimate, Adj. EBITDA Loss $3.8M vs Gain $4.6M YoY"
            ),
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) reported quarterly results.",
            "tickers": ["AAA"],
        })
        view = document["issuer_views"][0]
        self.assertTrue(view["positive_statement_ids"])
        self.assertTrue(view["negative_statement_ids"])
        self.assertEqual(view["composite_sentiment"], "mixed")

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

    def test_analyst_estimate_package_uses_benchmarks_and_operating_rationale(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-analyst-estimate-package",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Research firm increases EPS estimate on Alpha",
            "text": (
                "The research firm is out with its report today on Alpha Therapeutics Inc "
                "(NASDAQ:AAA), increasing its 2026 EPS estimate. In a note to clients, the firm "
                "writes, 'We are slightly raising our 2026E ongoing EPS estimate by two pennies "
                "to $1.67, below consensus $1.69 and at the low end of management's $1.67-$1.70 "
                "range as we remain cautious on gross margin pressure from escalating input costs.' "
                "The firm maintains Hold and a $20 price target."
            ),
            "tickers": ["AAA"],
        })
        view = document["issuer_views"][0]
        self.assertEqual(view["composite_sentiment"], "negative")
        self.assertEqual(view["positive_strength"], 1)
        self.assertEqual(view["negative_strength"], 2)
        concepts = {statement["concept_leaf"] for statement in document["statements"]}
        self.assertNotIn("earnings.performance", concepts)
        self.assertIn("estimate.revision", concepts)
        self.assertIn("financial.margin", concepts)
        facts = [
            fact
            for statement in document["statements"]
            for fact in statement["typed_facts"]
        ]
        self.assertTrue(any(
            fact.get("fact_type") == "estimate_comparison"
            and fact.get("subject_role") == "analyst_estimate"
            and fact.get("relation") == "below"
            for fact in facts
        ))
        self.assertTrue(any(
            fact.get("fact_type") == "estimate_range_position"
            and fact.get("position") == "low_end"
            for fact in facts
        ))
        self.assertTrue(any(
            fact.get("fact_type") == "operating_risk"
            and fact.get("risk_type") == "margin_pressure"
            for fact in facts
        ))

    def test_external_estimate_increase_is_not_issuer_guidance(self) -> None:
        text = (
            "A research firm is increasing its 2026 EPS estimate for "
            "Alpha Therapeutics Inc (NASDAQ:AAA) to $1.67."
        )
        document = self.engine.synthesize({
            "source_id": "news-external-estimate-not-guidance",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": text,
            "text": text,
            "tickers": ["AAA"],
        })
        concepts = {row["concept_leaf"] for row in document["statements"]}
        self.assertIn("estimate.revision", concepts)
        self.assertNotIn("guidance.issued", concepts)

    def test_analyst_estimate_above_consensus_remains_positive(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-analyst-estimate-above-consensus",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Research firm raises Alpha estimate",
            "text": (
                "The analyst is raising Alpha Therapeutics Inc (NASDAQ:AAA)'s EPS estimate "
                "to $2.10, above consensus $2.00."
            ),
            "tickers": ["AAA"],
        })
        view = document["issuer_views"][0]
        self.assertEqual(view["composite_sentiment"], "positive")
        self.assertEqual(view["positive_strength"], 2)

    def test_actual_reported_eps_is_not_suppressed_as_an_estimate(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-actual-eps-with-estimate-context",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha reports results",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) reports Q2 EPS of $1.20, above consensus $1.10, "
                "and management discusses its full-year estimate."
            ),
            "tickers": ["AAA"],
        })
        self.assertIn(
            "earnings.performance",
            {statement["concept_leaf"] for statement in document["statements"]},
        )

    def test_profit_outlook_below_analyst_estimate_is_negative_guidance(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-profit-outlook-below-estimate",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha forecast misses estimates",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) announced a Q2 profit forecast that fell short "
                "of the Street view. The company's Q2 profit outlook is at $0.57-$0.59 a share, "
                "while analyst estimates stand at $0.61 a share."
            ),
            "tickers": ["AAA"],
        })
        view = document["issuer_views"][0]
        self.assertEqual(view["composite_sentiment"], "negative")
        guidance = [
            statement
            for statement in document["statements"]
            if statement["concept_leaf"] == "guidance.issued"
        ]
        self.assertTrue(guidance)
        self.assertTrue(any(
            fact.get("fact_type") == "estimate_comparison"
            and fact.get("relation") == "below"
            for statement in guidance
            for fact in statement["typed_facts"]
        ))

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

    def test_complete_response_letter_dominates_pas_component_approval(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-complete-response-letter-pas",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha receives Complete Response Letter from FDA",
            "text": (
                "The FDA approved Alpha Therapeutics Inc (NASDAQ:AAA)'s drug product PAS submission. "
                "The FDA issued a CRL for its separate drug substance PAS submission."
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

    def test_advisory_panel_withheld_endorsement_is_adverse(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-advisory-endorsement-withheld",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "FDA advisers question Alpha application",
            "text": (
                "FDA advisers concluded that Alpha Therapeutics Inc (NASDAQ:AAA)'s data "
                "lacks the requisite reliability to endorse approval. The agency is awaiting "
                "a final decision."
            ),
            "tickers": ["AAA"],
        })
        view = document["issuer_views"][0]
        self.assertEqual(view["composite_sentiment"], "negative")
        self.assertEqual(view["negative_strength"], 4)

    def test_counterparty_bankruptcy_is_not_issuer_insolvency(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-counterparty-bankruptcy",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha completes acquisition",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) acquired a portfolio from Beta Logistics, "
                "which filed for Chapter 11 bankruptcy protection last year."
            ),
            "tickers": ["AAA"],
        })
        self.assertNotEqual(document["issuer_views"][0]["composite_sentiment"], "negative")

    def test_safe_harbor_risk_inventory_is_not_live_adverse_evidence(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-safe-harbor-risk-inventory",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha reports results",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) reported quarterly results above consensus. "
                "Forward-looking risks include the possibility anticipated synergies may not be "
                "realized, dependence on new product development, challenges relating to compliance, "
                "and the impact of substantial indebtedness."
            ),
            "tickers": ["AAA"],
        })
        view = document["issuer_views"][0]
        self.assertEqual(view["composite_sentiment"], "positive")
        self.assertEqual(view["negative_strength"], 0)

    def test_product_scoped_regulatory_setback_and_separate_clearance_are_mixed(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-product-scoped-regulatory-package",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha receives regulatory decision",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) Receives NSE Letter From FDA "
                "For 12-Lead ECG Synthesis Software; Evaluating Launch Of FDA-Cleared "
                "3d ECG System; To Work With FDA To Resolve NSE Issue"
            ),
            "tickers": ["AAA"],
        })
        view = document["issuer_views"][0]
        self.assertEqual(view["composite_sentiment"], "mixed")
        self.assertEqual(view["positive_strength"], 3)
        self.assertEqual(view["negative_strength"], 4)
        regulatory_facts = [
            fact
            for statement in document["statements"]
            for fact in statement["typed_facts"]
            if fact["fact_type"] == "regulatory_decision"
        ]
        self.assertEqual(
            {fact["outcome"] for fact in regulatory_facts},
            {"not_substantially_equivalent", "clearance_granted"},
        )
        self.assertTrue(any(
            fact.get("subject_raw") == "12-Lead ECG Synthesis Software"
            for fact in regulatory_facts
        ))
        launch = next(
            statement
            for statement in document["statements"]
            if statement["concept_leaf"] == "product.milestone"
        )
        self.assertEqual(launch["epistemic_status"], "expected")
        self.assertEqual(launch["time_relation"], "forward")

    def test_regulatory_outcome_is_recognized_in_either_authority_order(self) -> None:
        cases = (
            "The FDA found Alpha Therapeutics Inc (NASDAQ:AAA)'s cardiac software not substantially equivalent.",
            "Alpha Therapeutics Inc (NASDAQ:AAA) received a not substantially equivalent determination from FDA for its cardiac software.",
        )
        for index, text in enumerate(cases):
            with self.subTest(text=text):
                document = self.engine.synthesize({
                    "source_id": f"news-regulatory-order-{index}",
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": "Alpha regulatory update",
                    "text": text,
                    "tickers": ["AAA"],
                })
                view = document["issuer_views"][0]
                self.assertEqual(view["composite_sentiment"], "negative")
                self.assertEqual(view["negative_strength"], 4)

    def test_nse_without_medical_regulator_is_not_a_clinical_decision(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-nonmedical-nse",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "NSE launches a new index",
            "text": "NSE launches a new index for listed companies.",
            "tickers": ["AAA"],
        })
        self.assertNotIn(
            "clinical.regulatory_milestone",
            {statement["concept_leaf"] for statement in document["statements"]},
        )

    def test_regulatory_submission_is_a_positive_completed_milestone(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-regulatory-submission",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha reports regulatory submission",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) reports a deal with Health Canada "
                "for regulatory submission of its blood system."
            ),
            "tickers": ["AAA"],
        })
        view = document["issuer_views"][0]
        self.assertEqual(view["composite_sentiment"], "positive")
        self.assertEqual(view["positive_strength"], 2)

    def test_competitor_regulatory_approval_is_not_assigned_to_primary_setback(self) -> None:
        engine = NewsSynthesisEngine(IssuerIdentityIndex((
            IssuerIdentity("AAA", "issuer:aaa", "Alpha Therapeutics", ("Alpha",), "NYSE"),
            IssuerIdentity("BBB", "issuer:bbb", "Beta Therapeutics", ("Beta",), "NASDAQ"),
        )))
        document = engine.synthesize({
            "source_id": "news-competitor-regulatory-outcomes",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha setback benefits Beta",
            "text": (
                "The FDA issued a Complete Response Letter for Alpha Therapeutics (NYSE:AAA). "
                "The setback benefits Beta Therapeutics (NASDAQ:BBB), which secured FDA approval "
                "for its rival drug. Alpha will attempt to secure FDA approval again."
            ),
            "tickers": ["AAA", "BBB"],
        })
        views = {
            next(entity["ticker"] for entity in document["entities"] if entity["entity_id"] == view["entity_id"]): view
            for view in document["issuer_views"]
        }
        self.assertEqual(views["AAA"]["composite_sentiment"], "negative")
        self.assertEqual(views["BBB"]["composite_sentiment"], "positive")

    def test_provider_scoped_named_partner_shares_favorable_regulatory_milestone(self) -> None:
        engine = NewsSynthesisEngine(IssuerIdentityIndex((
            IssuerIdentity("AAA", "issuer:aaa", "Alpha Therapeutics", ("Alpha Therapeutics",)),
            IssuerIdentity("BBB", "issuer:bbb", "Beta Holdings", ("Beta Holdings",)),
        )))
        text = "AlphaBio Partner Beta Holdings Announces FDA Approval for New Treatment"
        document = engine.synthesize({
            "source_id": "named-regulatory-partner",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": text,
            "text": text,
            "tickers": ["AAA", "BBB"],
        })
        views = {
            next(
                entity["ticker"]
                for entity in document["entities"]
                if entity["entity_id"] == view["entity_id"]
            ): view["composite_sentiment"]
            for view in document["issuer_views"]
        }
        self.assertEqual(views, {"AAA": "positive", "BBB": "positive"})

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

    def test_directional_title_is_a_complete_evidence_lane(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-directional-title-lane",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha Raises Low End of Prelim. FY26 Earnings Outlook by $0.05",
            "text": "Source: wire service.",
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")
        self.assertTrue(any(
            statement["concept_leaf"] == "guidance.issued"
            and statement["evidence_spans"][0]["source_field"] == "title"
            and "FY26 Earnings Outlook" in statement["evidence_spans"][0]["quote"]
            for statement in document["statements"]
        ))

    def test_positive_event_grammar_families_are_not_neutralized(self) -> None:
        cases = (
            ("Takeover chatter in Alpha", "corporate_transaction.acquisition"),
            ("Alpha granted an in-person meeting with FDA", "clinical.regulatory_milestone"),
            ("Alpha Phase 3 study met its primary safety and efficacy endpoints", "clinical.trial_result"),
            ("Alpha partnering with Beta to commercialize a service", "commercial.partnership"),
            ("Alpha announces deal with Beta for providing 300 systems", "commercial.contract"),
            ("Alpha revenues $17.3M vs $16.49M estimate", "financial.operating_performance"),
            ("Alpha September comp sales up 10%", "financial.operating_performance"),
            ("Alpha obtained a $4.9M loan related to the Paycheck Protection Program", "financial.liquidity"),
            ("Alpha granted a patent for a new therapeutic", "legal.proceeding"),
            ("Alpha launches a diagnostic panel", "product.milestone"),
            ("Alpha delivers its 24th system", "product.milestone"),
            ("Alpha continues its sale process in an expedited manner", "strategy.strategic_alternatives"),
        )
        for index, (headline, expected_concept) in enumerate(cases):
            with self.subTest(headline=headline):
                document = self.engine.synthesize({
                    "source_id": f"news-positive-grammar-{index}",
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": headline,
                    "text": headline,
                    "tickers": ["AAA"],
                })
                self.assertEqual(
                    document["issuer_views"][0]["composite_sentiment"],
                    "positive",
                )
                self.assertIn(
                    expected_concept,
                    {statement["concept_leaf"] for statement in document["statements"]},
                )

    def test_transaction_role_grammar_handles_punctuation_and_rumors(self) -> None:
        engine = NewsSynthesisEngine(IssuerIdentityIndex((
            IssuerIdentity("AAA", "issuer:aaa", "A P Global", (), "NYSE"),
            IssuerIdentity("BBB", "issuer:bbb", "BetaBio Pharmaceuticals", (), "NYSE"),
        )))
        document = engine.synthesize({
            "source_id": "news-punctuated-acquirer",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "A&P Global will buy BetaBio, while takeover chatter continues in BetaBio",
            "text": "A&P Global will buy BetaBio, while takeover chatter continues in BetaBio.",
            "tickers": ["AAA", "BBB"],
        })
        ticker_by_entity = {row["entity_id"]: row["ticker"] for row in document["entities"]}
        roles = {
            ticker_by_entity[row["entity_id"]]: row["semantic_role"]
            for row in document["participations"]
            if ticker_by_entity[row["entity_id"]] in {"AAA", "BBB"}
        }
        self.assertEqual(roles["AAA"], "acquirer")
        self.assertEqual(roles["BBB"], "target")

    def test_positive_repairs_do_not_reverse_adverse_or_nondirectional_events(self) -> None:
        cases = (
            (
                "Alpha received a subpoena requesting documents and information",
                "negative",
                "legal.proceeding",
                "product.milestone",
            ),
            (
                "Alpha announces launch of a secondary public offering of common shares",
                "negative",
                "capital.financing",
                "product.milestone",
            ),
        )
        for index, (headline, expected, required, forbidden) in enumerate(cases):
            with self.subTest(headline=headline):
                document = self.engine.synthesize({
                    "source_id": f"news-positive-repair-control-{index}",
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": headline,
                    "text": headline,
                    "tickers": ["AAA"],
                })
                concepts = {row["concept_leaf"] for row in document["statements"]}
                self.assertEqual(document["issuer_views"][0]["composite_sentiment"], expected)
                self.assertIn(required, concepts)
                self.assertNotIn(forbidden, concepts)

        executive = self.engine.synthesize({
            "source_id": "news-executive-introduction-control",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha introduces its new CFO",
            "text": "Alpha introduces its new CFO.",
            "tickers": ["AAA"],
        })
        self.assertEqual(executive["issuer_views"][0]["composite_sentiment"], "neutral")

    def test_future_dated_new_partnership_is_not_treated_as_historical(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-future-partnership",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha partnering with Beta to begin commercializing a service in 2027",
            "text": "Alpha partnering with Beta to begin commercializing a service in 2027.",
            "tickers": ["AAA", "BBB"],
        })
        self.assertTrue(all(
            view["composite_sentiment"] == "positive"
            for view in document["issuer_views"]
        ))

        historical = self.engine.synthesize({
            "source_id": "news-historical-partnership",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha workforce update",
            "text": "Alpha launched a joint venture with Beta in 2019.",
            "tickers": ["AAA", "BBB"],
        })
        self.assertTrue(all(
            view["composite_sentiment"] == "neutral"
            for view in historical["issuer_views"]
        ))

    def test_rumored_bid_is_positive_for_target_not_bidder(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-rumored-bid-roles",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha bid for Beta could be imminent",
            "text": "Alpha Therapeutics bid for Beta Holdings could be imminent.",
            "tickers": ["AAA", "BBB"],
        })
        ticker_by_entity = {row["entity_id"]: row["ticker"] for row in document["entities"]}
        views = {
            ticker_by_entity[row["entity_id"]]: row["composite_sentiment"]
            for row in document["issuer_views"]
        }
        self.assertEqual(views["AAA"], "neutral")
        self.assertEqual(views["BBB"], "positive")

    def test_offer_service_through_acquisition_is_not_a_takeover_offer(self) -> None:
        text = (
            "Alpha Therapeutics Inc (NASDAQ:AAA) will offer a tax service through "
            "the acquisition of Beta Holdings Corp (NYSE:BBB). Alpha also cut its guidance."
        )
        document = self.engine.synthesize({
            "source_id": "service-offer-through-acquisition",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": text,
            "text": text,
            "tickers": ["AAA", "BBB"],
        })
        alpha = next(entity for entity in document["entities"] if entity["ticker"] == "AAA")
        acquisition_roles = [
            row["semantic_role"]
            for row in document["participations"]
            if row["entity_id"] == alpha["entity_id"]
            and next(
                statement["concept_leaf"]
                for statement in document["statements"]
                if statement["statement_id"] == row["statement_id"]
            ) == "corporate_transaction.acquisition"
        ]
        self.assertNotIn("target", acquisition_roles)

    def test_named_issuer_guidance_does_not_propagate_to_acquisition_counterparty(self) -> None:
        text = (
            "Alpha Therapeutics Inc (NASDAQ:AAA) raised guidance after acquiring "
            "Beta Holdings Corp (NYSE:BBB). The company now projects EPS of $2.50."
        )
        document = self.engine.synthesize({
            "source_id": "guidance-subject-vs-counterparty",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": text,
            "text": text,
            "tickers": ["AAA", "BBB"],
        })
        guidance_entity_ids = {
            row["entity_id"]
            for row in document["participations"]
            if next(
                statement["concept_leaf"]
                for statement in document["statements"]
                if statement["statement_id"] == row["statement_id"]
            ) == "guidance.issued"
        }
        alpha = next(entity for entity in document["entities"] if entity["ticker"] == "AAA")
        beta = next(entity for entity in document["entities"] if entity["ticker"] == "BBB")
        self.assertIn(alpha["entity_id"], guidance_entity_ids)
        self.assertNotIn(beta["entity_id"], guidance_entity_ids)

    def test_dotted_company_initials_preserve_downgrade_and_entity_binding(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-dotted-company-name",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Analyst downgrade",
            "text": (
                "Analysts downgraded J. C. Alpha Therapeutics Inc "
                "(NASDAQ:AAA) from outperform to perform."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "negative")

    def test_abbreviated_revenue_guidance_compares_with_consensus(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-abbreviated-revenue-guidance",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha sees FY revenue below consensus",
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) sees FY26 Rev. $112M-$118M vs $128.56M est.",
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "negative")

    def test_news_pending_halt_is_neutral_without_dropping_the_label(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-pending-halt",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha shares halted on news pending",
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) shares halted on code news pending.",
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "neutral")

    def test_confirmed_reorganization_plan_is_positive_despite_bankruptcy_noun(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-reorganization-confirmed",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha reorganization plan confirmed",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) said its joint plan of reorganization "
                "was confirmed by the bankruptcy court."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")

    def test_evidence_package_dominance_uses_overall_directional_value(self) -> None:
        entities = [{"entity_id": "security:AAA", "entity_kind": "security"}]
        statements = [
            {
                "statement_id": f"s{index}",
                "concept_leaf": "earnings.performance",
                "evidence_spans": [{"quote": quote}],
            }
            for index, quote in enumerate(("profit fell", "sales fell", "margin fell", "guidance fell", "orders rose"), 1)
        ]
        participations = [
            {
                "statement_id": f"s{index}",
                "entity_id": "security:AAA",
                "semantic_sentiment": "negative" if index < 5 else "positive",
                "sentiment_strength": 3 if index < 5 else 2,
            }
            for index in range(1, 6)
        ]
        self.assertEqual(
            derive_issuer_views(entities, participations, statements=statements)[0]["composite_sentiment"],
            "negative",
        )

        balanced = derive_issuer_views(
            entities,
            participations[:2] + [participations[-1]],
            statements=statements,
        )
        self.assertEqual(balanced[0]["composite_sentiment"], "negative")

        repeated_weak_positive = derive_issuer_views(
            entities,
            [
                {
                    "statement_id": f"s{index}",
                    "entity_id": "security:AAA",
                    "semantic_sentiment": "positive" if index < 5 else "negative",
                    "sentiment_strength": 2 if index < 5 else 3,
                }
                for index in range(1, 6)
            ],
            statements=statements,
        )
        self.assertEqual(
            repeated_weak_positive[0]["composite_sentiment"],
            "positive",
        )

    def test_active_regulatory_blocker_controls_unrelated_positive_packages(self) -> None:
        entities = [{"entity_id": "security:AAA", "entity_kind": "security"}]
        statements = [
            {
                "statement_id": "s1",
                "statement_kind": "event",
                "time_relation": "current",
                "concept_leaf": "clinical.regulatory_milestone",
                "evidence_spans": [{
                    "quote": "Title: FDA issued a refusal-to-file letter"
                }],
            },
            {
                "statement_id": "s2",
                "statement_kind": "event",
                "time_relation": "current",
                "concept_leaf": "guidance.issued",
                "evidence_spans": [{"quote": "guidance remains unchanged"}],
            },
            {
                "statement_id": "s3",
                "statement_kind": "assessment",
                "time_relation": "current",
                "concept_leaf": "strategy.operational_priority",
                "evidence_spans": [{"quote": "management remains confident"}],
            },
            {
                "statement_id": "s4",
                "statement_kind": "assessment",
                "time_relation": "current",
                "concept_leaf": "clinical.regulatory_milestone",
                "evidence_spans": [{
                    "quote": "The study used an FDA-approved vaccine as comparator."
                }],
            },
        ]
        participations = [
            {"statement_id": "s1", "entity_id": "security:AAA", "semantic_sentiment": "negative", "sentiment_strength": 4},
            {"statement_id": "s2", "entity_id": "security:AAA", "semantic_sentiment": "positive", "sentiment_strength": 3},
            {"statement_id": "s3", "entity_id": "security:AAA", "semantic_sentiment": "positive", "sentiment_strength": 2},
            {"statement_id": "s4", "entity_id": "security:AAA", "semantic_sentiment": "positive", "sentiment_strength": 3},
        ]
        view = derive_issuer_views(entities, participations, statements=statements)[0]
        self.assertEqual(view["composite_sentiment"], "negative")

    def test_prior_crl_in_resubmission_context_is_not_a_new_active_blocker(self) -> None:
        statement = {
            "concept_leaf": "clinical.regulatory_milestone",
            "evidence_spans": [{
                "quote": (
                    "The company was granted an FDA meeting regarding its NDA "
                    "resubmission following the earlier Complete Response Letter."
                )
            }],
        }
        self.assertFalse(_is_active_regulatory_blocker(statement))

    def test_repeated_renderings_of_one_metric_do_not_create_dominance(self) -> None:
        entities = [{"entity_id": "security:AAA", "entity_kind": "security"}]
        statements = [
            {
                "statement_id": "s1",
                "concept_leaf": "earnings.performance",
                "evidence_spans": [{"quote": "Alpha revenue rose 10%"}],
            },
            {
                "statement_id": "s2",
                "concept_leaf": "financial.operating_performance",
                "evidence_spans": [{"quote": "Quarterly sales increased ten percent"}],
            },
            {
                "statement_id": "s3",
                "concept_leaf": "earnings.performance",
                "evidence_spans": [{"quote": "Alpha EPS missed estimates"}],
            },
        ]
        participations = [
            {
                "statement_id": statement_id,
                "entity_id": "security:AAA",
                "semantic_sentiment": direction,
                "sentiment_strength": 2,
            }
            for statement_id, direction in (
                ("s1", "positive"),
                ("s2", "positive"),
                ("s3", "negative"),
            )
        ]
        self.assertEqual(
            derive_issuer_views(entities, participations, statements=statements)[0]["composite_sentiment"],
            "mixed",
        )

    def test_secondary_holder_offering_without_issuer_proceeds_is_neutral(self) -> None:
        document = self.engine.synthesize({
            "source_id": "secondary-holder-sale",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Selling shareholder offers Alpha shares",
            "text": (
                "A selling shareholder is offering shares of Alpha Therapeutics Inc "
                "(NASDAQ:AAA). The company will not receive any proceeds from the offering."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "neutral")

    def test_mixed_primary_secondary_offering_is_not_neutralized(self) -> None:
        document = self.engine.synthesize({
            "source_id": "mixed-primary-secondary-sale",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha and selling holders offer shares",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) is offering newly issued shares, "
                "and existing shareholders are offering additional shares. The company "
                "will receive proceeds from the newly issued shares but will not receive "
                "proceeds from shares sold by existing shareholders."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "negative")

    def test_company_possessive_shareholders_remain_secondary_sellers(self) -> None:
        document = self.engine.synthesize({
            "source_id": "possessive-secondary-sellers",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha announces secondary offering",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) announces a secondary offering. "
                "The company's shareholders are selling shares, and the company will "
                "not receive any proceeds from the offering."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "neutral")

    def test_unrelated_secondary_qualifier_does_not_neutralize_primary_offering(self) -> None:
        document = self.engine.synthesize({
            "source_id": "local-financing-qualifier-scope",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha launches public offering",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) is offering newly issued shares. "
                "Existing shareholders of Gamma are selling shares, and that company "
                "will not receive proceeds."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "negative")

    def test_secondary_proceeds_qualifier_order_and_intervening_context_do_not_change_direction(self) -> None:
        cases = (
            (
                "The company will not receive any proceeds from the offering. "
                "A selling shareholder is offering shares of Alpha Therapeutics Inc "
                "(NASDAQ:AAA)."
            ),
            (
                "A selling shareholder is offering shares of Alpha Therapeutics Inc "
                "(NASDAQ:AAA). The registration covers 2 million shares. "
                "The company will not receive any proceeds from the offering."
            ),
        )
        for index, text in enumerate(cases):
            with self.subTest(index=index):
                document = self.engine.synthesize({
                    "source_id": f"secondary-package-{index}",
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": "Selling shareholder offers Alpha shares",
                    "text": text,
                    "tickers": ["AAA"],
                })
                self.assertEqual(
                    document["issuer_views"][0]["composite_sentiment"],
                    "neutral",
                )

    def test_secondary_proceeds_predicate_accepts_issuer_alias_and_receive_no(self) -> None:
        document = self.engine.synthesize({
            "source_id": "secondary-explicit-issuer-proceeds",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Selling shareholder offers Alpha shares",
            "text": (
                "A selling shareholder is offering shares of Alpha Therapeutics Inc "
                "(NASDAQ:AAA). Alpha Therapeutics will receive no proceeds from the sale."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "neutral")

    def test_secondary_proceeds_predicate_accepts_registrant_synonym(self) -> None:
        document = self.engine.synthesize({
            "source_id": "secondary-registrant-proceeds",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Selling shareholder offers Alpha shares",
            "text": (
                "A selling shareholder is offering shares of Alpha Therapeutics Inc "
                "(NASDAQ:AAA). The registrant will receive no proceeds from the sale."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "neutral")

    def test_foreign_named_subject_does_not_supply_issuer_proceeds_qualifier(self) -> None:
        for index, qualifier in enumerate((
            "Gamma stated it will not receive proceeds from the sale.",
            "Separately, Gamma confirmed it will not receive proceeds from the sale.",
        )):
            with self.subTest(index=index):
                document = self.engine.synthesize({
                    "source_id": f"foreign-proceeds-subject-{index}",
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": "Selling shareholder offers Alpha shares",
                    "text": (
                        "A selling shareholder is offering shares of Alpha Therapeutics Inc "
                        f"(NASDAQ:AAA). {qualifier}"
                    ),
                    "tickers": ["AAA"],
                })
                self.assertEqual(
                    document["issuer_views"][0]["composite_sentiment"],
                    "negative",
                )

    def test_primary_and_secondary_financing_legs_remain_separate(self) -> None:
        document = self.engine.synthesize({
            "source_id": "separate-primary-secondary-legs",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha and selling holder offer shares",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) is offering newly issued shares. "
                "A selling shareholder is offering additional shares. The company will "
                "not receive proceeds from shares sold by the selling shareholder."
            ),
            "tickers": ["AAA"],
        })
        financing_parts = [
            row
            for row in document["participations"]
            if next(
                statement for statement in document["statements"]
                if statement["statement_id"] == row["statement_id"]
            )["concept_leaf"] == "capital.financing"
        ]
        self.assertIn("negative", {row["semantic_sentiment"] for row in financing_parts})
        self.assertIn("neutral", {row["semantic_sentiment"] for row in financing_parts})
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "negative")

    def test_historical_same_concept_body_does_not_suppress_current_headline(self) -> None:
        document = self.engine.synthesize({
            "source_id": "distinct-financing-events",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha launches public offering",
            "text": (
                "Last year, Alpha Therapeutics Inc (NASDAQ:AAA) completed a private placement."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "negative")

    def test_conflicting_financing_ordinals_prevent_headline_deduplication(self) -> None:
        document = self.engine.synthesize({
            "source_id": "distinct-ordinal-offerings",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha launches second public offering",
            "text": (
                "Earlier today Alpha Therapeutics Inc (NASDAQ:AAA) completed its first "
                "public offering."
            ),
            "tickers": ["AAA"],
        })
        financing = [
            statement for statement in document["statements"]
            if statement["concept_leaf"] == "capital.financing"
        ]
        self.assertEqual(len(financing), 2)

    def test_conflicting_financing_identity_features_prevent_headline_deduplication(self) -> None:
        cases = (
            (
                "Alpha launches another public offering",
                "Alpha Therapeutics Inc (NASDAQ:AAA) completed a separate public offering.",
            ),
            (
                "Alpha reopens public offering",
                "Alpha Therapeutics Inc (NASDAQ:AAA) closed a public offering.",
            ),
            (
                "Alpha offers 20 million shares",
                "Alpha Therapeutics Inc (NASDAQ:AAA) offers 50 million shares.",
            ),
            (
                "Alpha offers 20-million-share placement",
                "Alpha Therapeutics Inc (NASDAQ:AAA) offers 50-million-share placement.",
            ),
        )
        for index, (title, text) in enumerate(cases):
            with self.subTest(index=index):
                document = self.engine.synthesize({
                    "source_id": f"distinct-financing-feature-{index}",
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": title,
                    "text": text,
                    "tickers": ["AAA"],
                })
                financing = [
                    statement for statement in document["statements"]
                    if statement["concept_leaf"] == "capital.financing"
                ]
                self.assertEqual(len(financing), 2)

    def test_safe_harbor_risks_do_not_create_current_events(self) -> None:
        document = self.engine.synthesize({
            "source_id": "safe-harbor-only",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha forward-looking statement notice",
            "text": (
                "Forward-looking statements involve risks and uncertainties that may cause "
                "actual results to differ materially from expectations."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["statements"], [])
        self.assertEqual(document["issuer_views"], [])

    def test_substantive_plan_subject_to_risk_is_not_safe_harbor(self) -> None:
        document = self.engine.synthesize({
            "source_id": "substantive-risk-qualified-plan",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha plans product launch",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) plans to launch its new product "
                "subject to regulatory risks. Forward-looking statements involve risks "
                "and uncertainties that may cause actual results to differ materially."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")

    def test_historical_background_does_not_drive_current_composite(self) -> None:
        views = derive_issuer_views(
            [{"entity_id": "security:AAA", "entity_kind": "security"}],
            [
                {
                    "statement_id": "s1",
                    "entity_id": "security:AAA",
                    "semantic_sentiment": "negative",
                    "sentiment_strength": 3,
                },
                {
                    "statement_id": "s2",
                    "entity_id": "security:AAA",
                    "semantic_sentiment": "positive",
                    "sentiment_strength": 2,
                },
            ],
            statements=[
                {
                    "statement_id": "s1",
                    "statement_kind": "event",
                    "time_relation": "historical",
                    "concept_leaf": "legal.proceeding",
                    "evidence_spans": [{"quote": "Previously, Alpha lost the case."}],
                },
                {
                    "statement_id": "s2",
                    "statement_kind": "event",
                    "time_relation": "current",
                    "concept_leaf": "legal.proceeding",
                    "evidence_spans": [{"quote": "Alpha won the appeal."}],
                },
            ],
        )
        self.assertEqual(views[0]["composite_sentiment"], "positive")

    def test_issuer_leading_historical_statement_does_not_drive_composite(self) -> None:
        views = derive_issuer_views(
            [{"entity_id": "security:AAA", "entity_kind": "security"}],
            [{
                "statement_id": "s1",
                "entity_id": "security:AAA",
                "semantic_sentiment": "negative",
                "sentiment_strength": 3,
            }],
            statements=[{
                "statement_id": "s1",
                "statement_kind": "event",
                "time_relation": "historical",
                "concept_leaf": "legal.proceeding",
                "evidence_spans": [{"quote": "Alpha previously lost the case."}],
            }],
        )
        self.assertEqual(views[0]["composite_sentiment"], "neutral")

    def test_current_event_with_subordinate_history_remains_directional(self) -> None:
        document = self.engine.synthesize({
            "source_id": "current-event-with-history",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha regains Nasdaq compliance",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) has regained compliance with Nasdaq "
                "requirements following a previously completed reverse stock split."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")

    def test_leading_history_overrides_reported_period_comparison(self) -> None:
        document = self.engine.synthesize({
            "source_id": "historical-comparison-background",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha business update",
            "text": (
                "Previously, Alpha Therapeutics Inc (NASDAQ:AAA) reported net loss "
                "$2 million versus $5 million in the same quarter last year."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "neutral")

    def test_non_temporal_after_clause_does_not_reactivate_history(self) -> None:
        views = derive_issuer_views(
            [{"entity_id": "security:AAA", "entity_kind": "security"}],
            [{
                "statement_id": "s1",
                "entity_id": "security:AAA",
                "semantic_sentiment": "negative",
                "sentiment_strength": 3,
            }],
            statements=[{
                "statement_id": "s1",
                "statement_kind": "event",
                "time_relation": "historical",
                "concept_leaf": "legal.proceeding",
                "evidence_spans": [{
                    "quote": "Alpha said after markets closed that it previously lost the case."
                }],
            }],
        )
        self.assertEqual(views[0]["composite_sentiment"], "neutral")

    def test_current_main_result_after_historical_loss_remains_current(self) -> None:
        document = self.engine.synthesize({
            "source_id": "current-profit-after-history",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha reports profit",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) now reports net income after "
                "losses last year."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")

    def test_explicitly_old_result_year_is_historical_as_of_source(self) -> None:
        document = self.engine.synthesize({
            "source_id": "old-result-year",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha business background",
            "text": (
                "In 2024, Alpha Therapeutics Inc (NASDAQ:AAA) reported revenue of "
                "$100 million, up from $90 million in 2023."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "neutral")

    def test_current_release_of_prior_period_results_remains_current(self) -> None:
        document = self.engine.synthesize({
            "source_id": "current-prior-period-release",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha reports full-year results",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) reported full-year 2025 revenue "
                "of $100 million versus $90 million in 2024."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")

    def test_the_following_results_is_not_a_temporal_subordinate_clause(self) -> None:
        document = self.engine.synthesize({
            "source_id": "following-results-grammar",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha reports results",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) reported the following historically "
                "adjusted results: revenue increased 20 percent."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")

    def test_historical_guidance_is_not_active_forward_guidance(self) -> None:
        document = self.engine.synthesize({
            "source_id": "historical-guidance",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha business background",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) previously cut full-year revenue "
                "guidance to $90 million."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "neutral")

    def test_historical_guidance_variants_are_not_active(self) -> None:
        cases = (
            "Alpha Therapeutics Inc (NASDAQ:AAA) earlier cut revenue guidance to $90 million.",
            "Alpha Therapeutics Inc (NASDAQ:AAA) had expected revenue of $90 million.",
            "Alpha Therapeutics Inc (NASDAQ:AAA) expected revenue of $90 million in 2024.",
        )
        for index, text in enumerate(cases):
            with self.subTest(index=index):
                document = self.engine.synthesize({
                    "source_id": f"historical-guidance-variant-{index}",
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": "Alpha business background",
                    "text": text,
                    "tickers": ["AAA"],
                })
                self.assertEqual(
                    document["issuer_views"][0]["composite_sentiment"],
                    "neutral",
                )

    def test_nonleading_old_metric_year_is_historical_background(self) -> None:
        document = self.engine.synthesize({
            "source_id": "possessive-old-metric-year",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha business background",
            "text": (
                "Alpha Therapeutics Inc's 2024 revenue was $100 million, up from "
                "$90 million in 2023 (NASDAQ:AAA)."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "neutral")

    def test_old_metric_year_grammar_variants_are_historical(self) -> None:
        cases = (
            "Alpha Therapeutics Inc (NASDAQ:AAA) revenue for 2024 was $100 million, up from $90 million.",
            "Alpha Therapeutics Inc (NASDAQ:AAA) FY2024 revenue was $100 million, up from $90 million.",
        )
        for index, text in enumerate(cases):
            with self.subTest(index=index):
                document = self.engine.synthesize({
                    "source_id": f"old-metric-year-variant-{index}",
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": "Alpha business background",
                    "text": text,
                    "tickers": ["AAA"],
                })
                self.assertEqual(
                    document["issuer_views"][0]["composite_sentiment"],
                    "neutral",
                )

    def test_historically_high_current_result_remains_current(self) -> None:
        document = self.engine.synthesize({
            "source_id": "historically-high-current-result",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha reports high revenue",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) reported historically high "
                "revenue of $100 million."
            ),
            "tickers": ["AAA"],
        })
        result_statement = next(
            statement for statement in document["statements"]
            if statement["concept_leaf"] == "earnings.performance"
        )
        self.assertEqual(result_statement["time_relation"], "current")

    def test_leading_subordinate_history_preserves_current_main_event(self) -> None:
        document = self.engine.synthesize({
            "source_id": "leading-history-current-contract",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha wins replacement contract",
            "text": (
                "After previously losing a contract, Alpha Therapeutics Inc "
                "(NASDAQ:AAA) won a replacement contract."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")

    def test_historical_modifier_does_not_suppress_current_event(self) -> None:
        cases = (
            (
                "Alpha offering closes",
                "Alpha Therapeutics Inc (NASDAQ:AAA) closed a previously announced public offering.",
                "negative",
            ),
            (
                "Alpha receives approval",
                "FDA approved Alpha Therapeutics Inc (NASDAQ:AAA) for previously treated patients.",
                "positive",
            ),
            (
                "Alpha reports sales growth",
                "Alpha Therapeutics Inc (NASDAQ:AAA) reported sales increased 8% from the same period last year.",
                "positive",
            ),
        )
        for index, (title, text, expected) in enumerate(cases):
            with self.subTest(index=index):
                document = self.engine.synthesize({
                    "source_id": f"scoped-history-{index}",
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": title,
                    "text": text,
                    "tickers": ["AAA"],
                })
                self.assertEqual(
                    document["issuer_views"][0]["composite_sentiment"],
                    expected,
                )

    def test_signed_distribution_agreement_is_extracted(self) -> None:
        document = self.engine.synthesize({
            "source_id": "signed-distribution-agreement",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha signs distribution agreement",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) has signed an exclusive "
                "distribution agreement for its diagnostic platform."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")

    def test_vaccine_prevention_percentage_is_positive_trial_evidence(self) -> None:
        document = self.engine.synthesize({
            "source_id": "vaccine-prevention-result",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha vaccine prevents disease",
            "text": (
                "Alpha Therapeutics Inc's (NASDAQ:AAA) vaccine prevented 70% "
                "of infections among trial participants."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")

    def test_compact_event_grammar_families_create_issuer_views(self) -> None:
        cases = (
            (
                "Alpha secures liquidity",
                "Alpha Therapeutics Inc (NASDAQ:AAA) entered into a five-year revolving credit facility.",
                "positive",
            ),
            (
                "Alpha selected for services",
                "Alpha Therapeutics Inc (NASDAQ:AAA) was selected to provide design consultancy services.",
                "positive",
            ),
            (
                "Alpha results top expectations",
                "Alpha Therapeutics Inc (NASDAQ:AAA) posted stronger-than-expected results.",
                "positive",
            ),
            (
                "Alpha vulnerability exploited",
                "An actively exploited security vulnerability affects Alpha Therapeutics Inc (NASDAQ:AAA).",
                "negative",
            ),
            (
                "Alpha sells unit",
                "Alpha Therapeutics Inc (NASDAQ:AAA) plans to sell its logistics unit.",
                "neutral",
            ),
            (
                "Alpha records deliveries",
                "Alpha Therapeutics Inc (NASDAQ:AAA) reported record vehicle deliveries.",
                "positive",
            ),
            (
                "Alpha opens trial site",
                "Alpha Therapeutics Inc (NASDAQ:AAA) opened a Phase 1 clinical trial site.",
                "positive",
            ),
            (
                "Alpha stops trial",
                "Alpha Therapeutics Inc (NASDAQ:AAA) shut down its Phase 3 clinical trial.",
                "negative",
            ),
        )
        for index, (title, source_text, expected) in enumerate(cases):
            with self.subTest(index=index):
                document = self.engine.synthesize({
                    "source_id": f"compact-event-family-{index}",
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": title,
                    "text": source_text,
                    "tickers": ["AAA"],
                })
                self.assertEqual(
                    document["issuer_views"][0]["composite_sentiment"],
                    expected,
                )

    def test_single_subject_continuity_attaches_all_issuer_events(self) -> None:
        document = self.engine.synthesize({
            "source_id": "single-subject-continuity",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha announces capital plan",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) announced a reverse stock split. "
                "The company also authorized a $500 million share repurchase program "
                "and plans $400 million of acquisitions."
            ),
            "tickers": ["AAA"],
        })
        concepts = {
            statement["concept_leaf"]
            for statement in document["statements"]
            if statement["statement_id"] in document["issuer_views"][0]["statement_ids"]
        }
        self.assertIn("listing.market_structure", concepts)
        self.assertIn("capital.return", concepts)
        self.assertIn("corporate_transaction.acquisition", concepts)
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "mixed")

    def test_transaction_mechanical_reverse_split_is_neutral(self) -> None:
        document = self.engine.synthesize({
            "source_id": "transaction-mechanical-reverse-split",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha returns capital",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) announced a synthetic share "
                "repurchase that combines a capital repayment with a reverse stock split."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")

    def test_document_access_free_of_charge_is_not_loss_exposure(self) -> None:
        document = self.engine.synthesize({
            "source_id": "free-document-access",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha wins patent",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) received a patent. Copies of "
                "the filing are available free of charge at the SEC website."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")

    def test_document_access_without_charge_and_background_ipo_are_not_events(self) -> None:
        text = (
            "The FDA granted approval to Alpha Therapeutics Inc (NASDAQ:AAA). "
            "The proxy statement may be obtained without charge. Risk factors are "
            "discussed in Alpha's registration statement for the initial public "
            "offering filed with the SEC."
        )
        document = self.engine.synthesize({
            "source_id": "administrative-charge-and-background-ipo",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": text,
            "text": text,
            "tickers": ["AAA"],
        })
        concepts = {row["concept_leaf"] for row in document["statements"]}
        self.assertNotIn("financial.loss_exposure", concepts)
        self.assertNotIn("capital.financing", concepts)
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")

    def test_credit_workout_and_macro_assumptions_are_not_corporate_distress_or_guidance_cuts(self) -> None:
        text = (
            "Alpha Bank Inc (NASDAQ:AAA) restructured a borrower loan with credit "
            "enhancements and does not expect any losses. Management does not assume "
            "any Federal Reserve rate cuts in its outlook. The bank was quick to "
            "downgrade the internal credit rating and redeemed expensive capital."
        )
        document = self.engine.synthesize({
            "source_id": "bank-credit-workout-semantics",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": text,
            "text": text,
            "tickers": ["AAA"],
        })
        concepts = {row["concept_leaf"] for row in document["statements"]}
        self.assertNotIn("guidance.issued", concepts)
        self.assertNotIn("analyst.rating_action", concepts)
        self.assertNotIn("strategy.valuation_assessment", concepts)
        directional_business_updates = [
            row
            for row in document["participations"]
            if next(
                statement["concept_leaf"]
                for statement in document["statements"]
                if statement["statement_id"] == row["statement_id"]
            ) == "operations.business_update"
            and row["semantic_sentiment"] != "neutral"
        ]
        self.assertFalse(directional_business_updates)

    def test_cash_asset_sale_is_positive_monetization(self) -> None:
        document = self.engine.synthesize({
            "source_id": "cash-asset-sale",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha sells operations",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) plans to sell its logistics "
                "operations for $825 million in cash plus earnout consideration."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")

    def test_restructuring_support_distress_dominates_mitigation(self) -> None:
        document = self.engine.synthesize({
            "source_id": "distress-restructuring-support",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha enters restructuring support agreement",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) entered a pre-negotiated "
                "restructuring support agreement expected to reduce debt and improve liquidity."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "negative")

    def test_narrowing_net_loss_period_comparison_is_positive(self) -> None:
        document = self.engine.synthesize({
            "source_id": "narrowing-loss",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha narrows quarterly loss",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) reported net loss $2 million "
                "versus $5 million in the same quarter last year."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")

    def test_quarter_token_is_not_used_as_reported_metric_value(self) -> None:
        document = self.engine.synthesize({
            "source_id": "quarter-token-not-value",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha reiterates guidance",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) expects to report $9.4M "
                "in revenue for Q1, up from $1.8M year over year."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")

    def test_article_local_ticker_alias_binds_headline_to_explicit_share_class(self) -> None:
        engine = NewsSynthesisEngine(IssuerIdentityIndex((
            IssuerIdentity("AAA", "issuer:a", "AAA", (), "NASDAQ"),
            IssuerIdentity("BBB", "issuer:b", "BBB", (), "NASDAQ"),
        )))
        document = engine.synthesize({
            "source_id": "news-local-share-class-alias",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Beta Foods Q3 EPS down from last year",
            "text": (
                "Title: Beta Foods Q3 EPS down from last year\n"
                "Beta Foods (NASDAQ:BBB) reported quarterly earnings of $2.14 per share."
            ),
            "tickers": ["AAA", "BBB"],
        })
        ticker_by_entity = {row["entity_id"]: row["ticker"] for row in document["entities"]}
        views = {
            ticker_by_entity[row["entity_id"]]: row["composite_sentiment"]
            for row in document["issuer_views"]
        }
        self.assertEqual(views["BBB"], "negative")

    def test_subsidiary_ipo_is_positive_for_parent_and_dilutive_for_issuer(self) -> None:
        engine = NewsSynthesisEngine(IssuerIdentityIndex((
            IssuerIdentity("IACI", "issuer:iac", "IACI", (), "NASDAQ"),
            IssuerIdentity("MTCH", "issuer:match", "Match Group", ("Match Group",), "NASDAQ"),
            IssuerIdentity("BANK", "issuer:bank", "Example Bank", ("Example Bank",), "NYSE"),
        )))
        document = engine.synthesize({
            "source_id": "news-subsidiary-ipo-roles",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Match Group prices IPO",
            "text": (
                "IAC and Match Group (NASDAQ:MTCH), a wholly-owned subsidiary of IAC, "
                "announced the pricing of Match Group's initial public offering of shares. "
                "Example Bank acted as bookrunner."
            ),
            "tickers": ["IACI", "MTCH", "BANK"],
        })
        ticker_by_entity = {row["entity_id"]: row["ticker"] for row in document["entities"]}
        views = {
            ticker_by_entity[row["entity_id"]]: row["composite_sentiment"]
            for row in document["issuer_views"]
        }
        self.assertEqual(views["IACI"], "positive")
        self.assertEqual(views["MTCH"], "negative")
        self.assertEqual(views["BANK"], "neutral")

    def test_compact_earnings_esp_is_not_an_observed_share_price_move(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-earnings-esp-not-price",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha earnings preview",
            "text": "Alpha Therapeutics has an AAA +3.81% Earnings ESP ahead of results.",
            "tickers": ["AAA"],
        })
        self.assertNotIn(
            "market.price_move_observed",
            {row["concept_leaf"] for row in document["statements"]},
        )

    def test_equal_prior_period_eps_is_neutral_with_zero_strength(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-equal-prior-period-eps",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha earnings preview",
            "text": (
                "Alpha Therapeutics is expected to report EPS of $0.39 "
                "versus $0.39 a year ago."
            ),
            "tickers": ["AAA"],
        })
        validation = validate_document(document)
        self.assertTrue(validation.valid, validation.issues)
        neutral_rows = [
            row
            for row in document["participations"]
            if row["semantic_sentiment"] == "neutral"
        ]
        self.assertTrue(neutral_rows)
        self.assertTrue(all(
            row["semantic_sentiment"] == "neutral"
            for row in document["participations"]
        ))
        self.assertTrue(all(
            row["sentiment_strength"] == 0
            for row in document["participations"]
        ))
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "neutral")

    def test_negated_analyst_recommendations_are_negative(self) -> None:
        for text in (
            "The analyst is no longer bullish on Alpha Therapeutics.",
            "The analyst isn't a buyer of Alpha Therapeutics.",
            "The analyst is not willing to recommend Alpha Therapeutics.",
        ):
            with self.subTest(text=text):
                document = self.engine.synthesize({
                    "source_id": "news-negated-analyst-recommendation",
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": text,
                    "text": text,
                    "tickers": ["AAA"],
                })
                self.assertEqual(
                    document["issuer_views"][0]["composite_sentiment"],
                    "negative",
                )

    def test_absent_cost_savings_are_negative_not_efficiency(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-absent-cost-savings",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha lacks savings",
            "text": "Alpha Therapeutics reported a lack of meaningful cost savings.",
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "negative")

    def test_minimum_bid_deficiency_is_negative(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-minimum-bid-deficiency",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha receives minimum bid deficiency notice",
            "text": "Alpha Therapeutics received a Nasdaq minimum bid deficiency notice.",
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "negative")

    def test_executive_death_controls_same_sentence_interim_appointment(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-executive-death",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha announces passing of founder and CEO",
            "text": (
                "Alpha Therapeutics announced the passing of its founder and CEO "
                "and named an interim CEO."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "negative")

    def test_explicit_operating_expansion_is_positive(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-clinic-expansion",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha expands clinic network with Palm Beach location",
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) expands its clinic network and is now accepting patients.",
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")

    def test_headline_quarter_beat_without_earnings_noun_is_positive(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-fq3-beat",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha Posts FQ3 Beat on Expense Control",
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) posts FQ3 beat on expense control.",
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")
        self.assertIn("earnings.performance", {row["concept_leaf"] for row in document["statements"]})

    def test_solvency_distress_language_is_negative(self) -> None:
        cases = (
            "Alpha Therapeutics Inc (NASDAQ:AAA) says there is substantial doubt about its ability to continue.",
            "Alpha Therapeutics Inc (NASDAQ:AAA) seeks creditor protection to pursue a restructuring plan.",
            "Alpha Therapeutics Inc (NASDAQ:AAA) reports a financing shortfall and cuts planned trials.",
        )
        for index, text in enumerate(cases):
            with self.subTest(text=text):
                document = self.engine.synthesize({
                    "source_id": f"news-solvency-language-{index}",
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": text,
                    "text": text,
                    "tickers": ["AAA"],
                })
                self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "negative")

    def test_agreement_expansion_and_selected_delivery_are_positive_contract_events(self) -> None:
        cases = (
            "Alpha Therapeutics Inc (NASDAQ:AAA) will expand its sales agreement with Beta.",
            "Alpha Therapeutics Inc (NASDAQ:AAA) has been selected by Beta to deliver a satellite earth station.",
        )
        for index, text in enumerate(cases):
            with self.subTest(text=text):
                document = self.engine.synthesize({
                    "source_id": f"news-contract-form-{index}",
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": text,
                    "text": text,
                    "tickers": ["AAA"],
                })
                self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")
                self.assertIn("commercial.contract", {row["concept_leaf"] for row in document["statements"]})

    def test_agreement_customer_is_neutral_while_renewing_supplier_is_positive(self) -> None:
        engine = NewsSynthesisEngine(IssuerIdentityIndex((
            IssuerIdentity("AAA", "issuer:aaa", "Alpha", ("Alpha",), "NASDAQ"),
            IssuerIdentity("BBB", "issuer:bbb", "Beta Systems", ("Beta Systems",), "NASDAQ"),
        )))
        document = engine.synthesize({
            "source_id": "news-role-aware-renewal",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha has renewed its services agreement with Beta Systems",
            "text": "Alpha (NASDAQ:AAA) has renewed its services agreement with Beta Systems (NASDAQ:BBB).",
            "tickers": ["AAA", "BBB"],
        })
        tickers = {row["entity_id"]: row["ticker"] for row in document["entities"]}
        views = {tickers[row["entity_id"]]: row for row in document["issuer_views"]}
        self.assertEqual(views["AAA"]["composite_sentiment"], "positive")
        self.assertEqual(views["BBB"]["composite_sentiment"], "neutral")

    def test_metric_expansion_is_not_operating_expansion(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-margin-loss-expanded",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha reports quarterly results",
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) said operating margin loss expanded 250 basis points.",
            "tickers": ["AAA"],
        })
        operations = {
            row["statement_id"]: row
            for row in document["participations"]
            if row["entity_id"].endswith("AAA")
        }
        statement_by_id = {row["statement_id"]: row for row in document["statements"]}
        self.assertFalse(any(
            row["semantic_sentiment"] == "positive"
            and statement_by_id[sid]["concept_leaf"] == "operations.business_update"
            for sid, row in operations.items()
        ))

    def test_historical_or_remediation_crl_context_is_not_a_new_adverse_decision(self) -> None:
        cases = (
            "Alpha Therapeutics Inc (NASDAQ:AAA) prepares to respond to the complete response letter.",
            "Following a complete response letter received last year, Alpha Therapeutics Inc (NASDAQ:AAA) continues to work to address remaining questions.",
        )
        for index, text in enumerate(cases):
            with self.subTest(text=text):
                document = self.engine.synthesize({
                    "source_id": f"news-crl-context-{index}",
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": text,
                    "text": text,
                    "tickers": ["AAA"],
                })
                self.assertNotEqual(document["issuer_views"][0]["composite_sentiment"], "negative")

    def test_transaction_closing_is_not_operational_shutdown(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-transaction-closing",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha announces transaction closing",
            "text": "Alpha Therapeutics Inc (NASDAQ:AAA) announced the closing of its business combination.",
            "tickers": ["AAA"],
        })
        self.assertNotEqual(document["issuer_views"][0]["composite_sentiment"], "negative")

    def test_current_expansion_of_previously_announced_collaboration_remains_current(self) -> None:
        text = (
            "Alpha Therapeutics Inc (NASDAQ:AAA) has expanded its previously-announced "
            "manufacturing collaboration with Beta Systems."
        )
        document = self.engine.synthesize({
            "source_id": "news-current-collaboration-expansion",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": text,
            "text": text,
            "tickers": ["AAA"],
        })
        self.assertTrue(any(row["time_relation"] == "current" for row in document["statements"]))

    def test_regulatory_waiting_period_extension_is_not_a_positive_contract(self) -> None:
        text = (
            "Alpha Therapeutics Inc (NASDAQ:AAA) said the regulatory waiting period "
            "has been extended several times by agreement with the FTC."
        )
        document = self.engine.synthesize({
            "source_id": "news-waiting-period-extension",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": text,
            "text": text,
            "tickers": ["AAA"],
        })
        self.assertFalse(any(
            row["semantic_sentiment"] == "positive"
            for row in document["participations"]
        ))

    def test_anaphoric_business_model_sustainability_risk_is_negative(self) -> None:
        document = self.engine.synthesize({
            "source_id": "news-business-model-risk",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha finds a market niche",
            "text": (
                "Alpha Therapeutics Inc (NASDAQ:AAA) has found a niche in the market. "
                "However, concerns over the sustainability of this business model remain."
            ),
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "negative")

    def test_dilutive_financing_with_explicit_liquidity_or_operating_use_is_mixed(self) -> None:
        cases = (
            "Alpha Therapeutics Inc (NASDAQ:AAA) closed a private placement of warrants for $20 million. The net proceeds will fund working capital and expansion of its operations.",
            "Alpha Therapeutics Inc (NASDAQ:AAA) launched a public offering of convertible preferred stock. Under the transaction, Alpha would obtain immediate access to $15 million in liquidity.",
            "Alpha Therapeutics Inc (NASDAQ:AAA) will pursue a strategic investment in lieu of an offering of units while a strategic investor is expected to make a $50 million investment to purchase warrants.",
        )
        for index, text in enumerate(cases):
            with self.subTest(text=text):
                document = self.engine.synthesize({
                    "source_id": f"news-financing-tradeoff-{index}",
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": text,
                    "text": text,
                    "tickers": ["AAA"],
                })
                self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "mixed")

    def test_benchmarked_earnings_direction_dominates_unbenchmarked_period_moves(self) -> None:
        cases = (
            (
                "Alpha Therapeutics Inc (NASDAQ:AAA) missed EPS estimates and revenue missed expectations. "
                "Revenue increased 6 percent from last year.",
                "negative",
            ),
            (
                "Alpha Therapeutics Inc (NASDAQ:AAA) beat EPS expectations and revenue beat estimates. "
                "Revenue fell 9 percent from last year.",
                "positive",
            ),
        )
        for index, (text, expected) in enumerate(cases):
            with self.subTest(text=text):
                document = self.engine.synthesize({
                    "source_id": f"news-benchmarked-package-{index}",
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": text,
                    "text": text,
                    "tickers": ["AAA"],
                })
                self.assertEqual(document["issuer_views"][0]["composite_sentiment"], expected)

    def test_launched_or_rolling_product_functionality_is_current_positive(self) -> None:
        text = (
            "Alpha Therapeutics Inc (NASDAQ:AAA) made its new photo uploader available "
            "to most users and is rolling out the feature globally."
        )
        document = self.engine.synthesize({
            "source_id": "news-live-product-rollout",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha rolls out new photo uploader",
            "text": text,
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")

    def test_in_charge_of_business_is_not_a_financial_charge(self) -> None:
        text = (
            "Alpha Therapeutics Inc (NASDAQ:AAA) will retain full ownership and be "
            "exclusively in charge of its fast-growing battery business."
        )
        document = self.engine.synthesize({
            "source_id": "news-responsibility-idiom",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha retains battery business",
            "text": text,
            "tickers": ["AAA"],
        })
        self.assertNotIn(
            "financial.loss_exposure",
            {row["concept_leaf"] for row in document["statements"]},
        )

    def test_regulator_or_distributor_product_return_is_adverse_despite_in_charge_idiom(self) -> None:
        text = (
            "Alpha Therapeutics Inc (NASDAQ:AAA) received notice from the commission, "
            "the corporation in charge of wholesale distribution, that Alpha's products "
            "sold to the distributor will be returned to the company."
        )
        document = self.engine.synthesize({
            "source_id": "news-product-return",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Distributor returns Alpha products",
            "text": text,
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "negative")

    def test_global_workforce_reduction_is_negative(self) -> None:
        text = "Alpha Therapeutics Inc (NASDAQ:AAA) is reducing its global workforce by 10 percent."
        document = self.engine.synthesize({
            "source_id": "news-workforce-reduction",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha reduces global workforce",
            "text": text,
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "negative")

    def test_hypothetical_stake_acquisition_is_not_a_current_positive_event(self) -> None:
        text = (
            "Alpha Therapeutics Inc (NASDAQ:AAA) said that if a minority stake is "
            "acquired, hopefully the investor would support its strategy."
        )
        document = self.engine.synthesize({
            "source_id": "news-hypothetical-stake",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha discusses possible minority stake",
            "text": text,
            "tickers": ["AAA"],
        })
        self.assertNotEqual(document["issuer_views"][0]["composite_sentiment"], "positive")

    def test_incorrect_prior_guidance_raise_does_not_offset_current_cut(self) -> None:
        text = (
            "Alpha Therapeutics Inc (NASDAQ:AAA) cut FY2026 sales guidance. "
            "A prior headline had indicated the company raised guidance; this was incorrect."
        )
        document = self.engine.synthesize({
            "source_id": "news-guidance-correction",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha cuts FY2026 sales guidance",
            "text": text,
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "negative")

    def test_raise_word_outside_forecast_semantics_is_not_guidance(self) -> None:
        text = (
            "Alpha Therapeutics Inc (NASDAQ:AAA) intends to address concerns the SEC "
            "has raised regarding historical stock sales."
        )
        document = self.engine.synthesize({
            "source_id": "news-raised-concerns",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha responds to SEC concerns",
            "text": text,
            "tickers": ["AAA"],
        })
        self.assertNotIn(
            "guidance.issued",
            {row["concept_leaf"] for row in document["statements"]},
        )

    def test_realized_lower_revenue_fragment_is_not_guidance(self) -> None:
        text = (
            "Alpha Therapeutics Inc (NASDAQ:AAA) reported quarterly results. "
            "Corporate revenue fell 40 percent from a year ago due to lower revenue, "
            "partially offset by lower operating expenses."
        )
        document = self.engine.synthesize({
            "source_id": "news-realized-lower-revenue",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha reports quarterly results",
            "text": text,
            "tickers": ["AAA"],
        })
        self.assertNotIn(
            "guidance.issued",
            {row["concept_leaf"] for row in document["statements"]},
        )

    def test_current_forecast_revision_remains_forward_with_earlier_baseline(self) -> None:
        text = (
            "Alpha Therapeutics Inc (NASDAQ:AAA) now projects sales at the low end "
            "of its earlier guidance range."
        )
        document = self.engine.synthesize({
            "source_id": "news-current-guidance-revision",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha revises sales projection",
            "text": text,
            "tickers": ["AAA"],
        })
        guidance = [row for row in document["statements"] if row["concept_leaf"] == "guidance.issued"]
        self.assertTrue(guidance)
        self.assertTrue(all(row["time_relation"] == "forward" for row in guidance))

    def test_negative_comparable_store_sales_outlook_is_adverse_guidance(self) -> None:
        text = (
            "Alpha Therapeutics Inc (NASDAQ:AAA) units are expected to comp negative "
            "25 to 35 percent in Q3 and negative 10 percent to flat in Q4."
        )
        document = self.engine.synthesize({
            "source_id": "news-negative-comps-guidance",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha gives quarterly comparable-sales outlook",
            "text": text,
            "tickers": ["AAA"],
        })
        guidance = [row for row in document["statements"] if row["concept_leaf"] == "guidance.issued"]
        self.assertTrue(guidance)
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "negative")

    def test_guides_comp_sales_growth_is_not_realized_performance(self) -> None:
        text = (
            "Alpha Therapeutics Inc (NASDAQ:AAA) guides comparable-store sales growth "
            "of 2 to 4 percent for Q2."
        )
        document = self.engine.synthesize({
            "source_id": "news-guides-comp-growth",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha guides Q2 comparable sales",
            "text": text,
            "tickers": ["AAA"],
        })
        concepts = {row["concept_leaf"] for row in document["statements"]}
        self.assertIn("guidance.issued", concepts)
        self.assertNotIn("financial.operating_performance", concepts)
        self.assertNotIn("earnings.performance", concepts)

    def test_absent_clinical_data_is_not_product_availability_and_nice_rejection_is_adverse(self) -> None:
        text = (
            "NICE does not recommend Alpha Therapeutics Inc's (NASDAQ:AAA) treatment. "
            "No clinical data is available evaluating maintenance after consolidation therapy."
        )
        document = self.engine.synthesize({
            "source_id": "news-nice-rejection",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "NICE does not recommend Alpha treatment",
            "text": text,
            "tickers": ["AAA"],
        })
        self.assertNotIn(
            "product.milestone",
            {row["concept_leaf"] for row in document["statements"]},
        )
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "negative")

    def test_orderly_requested_auditor_transition_is_neutral(self) -> None:
        text = (
            "At the request of Alpha Therapeutics Inc (NASDAQ:AAA), the former auditor "
            "resigned as auditor and the board appointed the successor auditor as the "
            "new auditor. There were no disagreements."
        )
        document = self.engine.synthesize({
            "source_id": "news-orderly-auditor-transition",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Alpha appoints successor auditor",
            "text": text,
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "neutral")

    def test_navigation_slogan_and_software_ad_are_not_issuer_events(self) -> None:
        text = (
            "Alpha Therapeutics Inc (NASDAQ:AAA) was mentioned in a market roundup. "
            "Take charge. Lease Management Software Manage the entire lease lifecycle "
            "efficiently, from acquisition to termination."
        )
        document = self.engine.synthesize({
            "source_id": "news-software-ad-navigation",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": "Market roundup",
            "text": text,
            "tickers": ["AAA"],
        })
        self.assertFalse(any(
            row["semantic_sentiment"] != "neutral"
            for row in document["participations"]
        ))

    def test_complete_response_letter_is_not_an_issuer_alias(self) -> None:
        engine = NewsSynthesisEngine(IssuerIdentityIndex((
            IssuerIdentity("AAA", "issuer:aaa", "Alpha Therapeutics", ("Alpha Therapeutics",), "NASDAQ"),
            IssuerIdentity("CRL", "issuer:crl", "Charles River Laboratories", ("Complete Response Letter",), "NYSE"),
        )))
        text = "The FDA issued a Complete Response Letter regarding Alpha Therapeutics (NASDAQ:AAA)."
        document = engine.synthesize({
            "source_id": "news-crl-alias-collision",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": text,
            "text": text,
            "tickers": ["AAA"],
        })
        self.assertNotIn("CRL", {row["ticker"] for row in document["entities"]})

    def test_filed_or_accepted_regulatory_response_controls_historical_crl_context(self) -> None:
        cases = (
            "Alpha Therapeutics filed its complete response submission with the FDA. The FDA had issued a Complete Response Letter in 2022.",
            "The FDA accepts Alpha Therapeutics' complete response submission. The 2022 Complete Response Letter requested additional information.",
        )
        for index, text in enumerate(cases):
            with self.subTest(text=text):
                document = self.engine.synthesize({
                    "source_id": f"regulatory-response-resolution-{index}",
                    "source_timestamp": "2026-08-03T12:00:00Z",
                    "title": text,
                    "text": text,
                    "tickers": ["AAA"],
                })
                self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "positive")

    def test_withdrawn_regulatory_application_is_adverse_despite_eventual_approval_language(self) -> None:
        text = (
            "Alpha Therapeutics Inc (NASDAQ:AAA) voluntarily withdrew its New Drug "
            "Application after the FDA recommended an additional Phase 3 study to obtain approval."
        )
        document = self.engine.synthesize({
            "source_id": "withdrawn-regulatory-application",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": text,
            "text": text,
            "tickers": ["AAA"],
        })
        self.assertEqual(document["issuer_views"][0]["composite_sentiment"], "negative")

    def test_navigation_restructuring_and_ipo_prospectus_provenance_are_not_live_events(self) -> None:
        text = (
            "Alpha Therapeutics Inc (NASDAQ:AAA) announced a product acquisition. "
            "Now read: Another issuer restructures debt. Information about the directors "
            "is set forth in Alpha's final prospectus for its initial public offering filed with the SEC."
        )
        document = self.engine.synthesize({
            "source_id": "navigation-and-ipo-provenance",
            "source_timestamp": "2026-08-03T12:00:00Z",
            "title": text,
            "text": text,
            "tickers": ["AAA"],
        })
        concepts = {row["concept_leaf"] for row in document["statements"]}
        self.assertNotIn("capital.financing", concepts)
        self.assertFalse(any(
            row["semantic_sentiment"] == "negative"
            and next(
                statement["concept_leaf"]
                for statement in document["statements"]
                if statement["statement_id"] == row["statement_id"]
            ) == "operations.business_update"
            for row in document["participations"]
        ))


if __name__ == "__main__":
    unittest.main()
