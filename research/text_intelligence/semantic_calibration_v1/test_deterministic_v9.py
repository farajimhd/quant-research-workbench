from __future__ import annotations

import unittest

from research.text_intelligence.scoped_labeling_v1.news_identity import (
    ISSUER_IDENTITY_AUTHORITY_VERSION,
    IssuerIdentity,
    NewsIssuerResolver,
)
from research.text_intelligence.scoped_labeling_v1.schema import NEWS_EXTRACTOR_VERSION
from research.text_intelligence.semantic_label_authority_v1.schema import SemanticDocument

from .deterministic_v9 import _recalibrate_direction, classify_news_document_v9
from .deterministic_v9_config import (
    CALIBRATION_SPLIT_SHA256,
    CALIBRATION_VERSION,
    DETERMINISTIC_V9_VERSION,
)
from .run_deterministic_news_v9 import prediction_is_current
from .teacher_split_v9 import normalized_headline_template


class DeterministicV9Tests(unittest.TestCase):
    def test_compact_earnings_wire_is_automated_summary(self) -> None:
        document = SemanticDocument(
            corpus="news", source_id="automated-results-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="Example Q3 EPS $0.33 Beats $0.12 Estimate, Sales $1.1B Miss $1.2B Estimate",
            text="", tickers=("EXM",),
        )
        resolver = NewsIssuerResolver((IssuerIdentity("EXM", "issuer:exm", ("Example",)),))
        result = classify_news_document_v9(document, issuer_resolver=resolver)
        self.assertEqual(result.content_role, "automated_summary")
        self.assertEqual(result.source_origin, "automated_summary")

    def test_macro_stat_wire_is_market_roundup(self) -> None:
        document = SemanticDocument(
            corpus="news", source_id="macro-stat-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="US January Durable Goods Orders (4.0%) vs (1.0%) Est",
            text="", tickers=(),
        )
        result = classify_news_document_v9(document)
        self.assertEqual(result.content_role, "market_roundup")
        self.assertEqual(result.extraction_decision, "non_issuer_market_content")

    def test_regulatory_filing_title_precedes_editorial_fallback(self) -> None:
        document = SemanticDocument(
            corpus="news", source_id="regulatory-title-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="Example 8-K Shows Company Received SEC Inquiry Letter",
            text="Example Corp. (NASDAQ:EXM) received an SEC inquiry letter.",
            tickers=("EXM",),
        )
        resolver = NewsIssuerResolver((IssuerIdentity("EXM", "issuer:exm", ("Example Corp",)),))
        self.assertEqual(
            classify_news_document_v9(document, issuer_resolver=resolver).content_role,
            "regulatory_event",
        )

    def test_cached_prediction_requires_current_calibration(self) -> None:
        prediction = {
            "version": DETERMINISTIC_V9_VERSION,
            "calibration_version": CALIBRATION_VERSION,
            "scope_extractor_version": NEWS_EXTRACTOR_VERSION,
            "identity_resolution": {
                "authority_version": ISSUER_IDENTITY_AUTHORITY_VERSION,
            },
        }
        self.assertTrue(prediction_is_current(prediction))
        stale = dict(prediction, calibration_version="stale-calibration")
        self.assertFalse(prediction_is_current(stale))

    def test_unique_title_lead_alias_recovers_title_only_event(self) -> None:
        document = SemanticDocument(
            corpus="news", source_id="title-lead-v9",
            timestamp="2021-04-05T20:01:43Z",
            title="Verint Sees Closing $200M Investment From Funds Advised By Apax",
            text="Title: Verint Sees Closing $200M Investment From Funds Advised By Apax",
            tickers=(),
        )
        resolver = NewsIssuerResolver((
            IssuerIdentity("VRNT", "issuer:vrnt", ("Verint Systems Inc", "Verint")),
        ))
        result = classify_news_document_v9(document, issuer_resolver=resolver)
        self.assertEqual([label["ticker"] for label in result.labels], ["VRNT"])

    def test_generated_title_prefix_is_not_an_issuer_subject(self) -> None:
        resolver = NewsIssuerResolver((
            IssuerIdentity("OPCH", "issuer:opch", ("Option Care Health Inc",)),
            IssuerIdentity("BBLC", "issuer:bblc", ("Blockchain Loyalty Corp",)),
        ))
        self.assertEqual(
            resolver.resolve_title_lead_subjects("Option Alert: Large Call Block"),
            (),
        )
        self.assertEqual(
            resolver.resolve_title_lead_subjects("Blockchain Meets The Stratosphere"),
            (),
        )

    def test_generic_industry_title_is_not_an_issuer_subject(self) -> None:
        resolver = NewsIssuerResolver((
            IssuerIdentity("MJNA", "issuer:mjna", ("Medical Marijuana Inc",)),
        ))
        self.assertEqual(
            resolver.resolve_title_lead_subjects("Medical Marijuana Sales Begin"),
            (),
        )

    def test_unverified_regulatory_story_is_editorial_aggregation(self) -> None:
        document = SemanticDocument(
            corpus="news", source_id="reported-halt-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="Example Shares Halted On Circuit Breaker",
            text="Benzinga reports Example Corp. (NASDAQ:EXM) remains halted.",
            tickers=("EXM",),
        )
        resolver = NewsIssuerResolver((IssuerIdentity("EXM", "issuer:exm", ("Example Corp",)),))
        result = classify_news_document_v9(document, issuer_resolver=resolver)
        self.assertEqual(result.source_origin, "editorial_aggregation")

    def test_positive_rating_with_large_target_cut_is_mixed(self) -> None:
        document = SemanticDocument(
            corpus="news", source_id="rating-target-conflict-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="Broker Maintains Overweight on Example, Lowers Price Target to $42",
            text=("Broker maintains Example Corp. (NASDAQ:EXM) at Overweight and "
                  "lowers the price target from $102 to $42."),
            tickers=("EXM",),
        )
        resolver = NewsIssuerResolver((IssuerIdentity("EXM", "issuer:exm", ("Example Corp",)),))
        label = classify_news_document_v9(document, issuer_resolver=resolver).labels[0]
        self.assertEqual(label["classification"]["semantic_direction"], "mixed")

    def test_investor_social_commentary_is_editorial_analysis(self) -> None:
        document = SemanticDocument(
            corpus="news", source_id="investor-commentary-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="Fund Manager Warns Investors About Example Demand",
            text=("Investor Jane Doe of Future Fund took to X and said Example Corp. "
                  "(NASDAQ:EXM) demand remains weak."),
            tickers=("EXM",),
        )
        resolver = NewsIssuerResolver((IssuerIdentity("EXM", "issuer:exm", ("Example Corp",)),))
        result = classify_news_document_v9(document, issuer_resolver=resolver)
        self.assertEqual(result.content_role, "editorial_analysis")
        self.assertEqual(result.source_origin, "editorial_original")

    def test_same_issuer_share_classes_are_emitted_from_one_resolved_event(self) -> None:
        document = SemanticDocument(
            corpus="news", source_id="share-classes-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="Alphabet Reports Quarterly Revenue Growth",
            text="Alphabet Inc. reported quarterly revenue growth.",
            tickers=("GOOG", "GOOGL"),
        )
        resolver = NewsIssuerResolver(
            (
                IssuerIdentity("GOOG", "issuer:alphabet", ("Alphabet Inc",)),
                IssuerIdentity("GOOGL", "issuer:alphabet", ("Alphabet Inc",)),
            ),
            article_tickers=("GOOG", "GOOGL"),
        )
        labels = classify_news_document_v9(document, issuer_resolver=resolver).labels
        self.assertEqual({label["ticker"] for label in labels}, {"GOOG", "GOOGL"})

    def test_compact_positive_earnings_wire_remains_trigger_eligible(self) -> None:
        document = SemanticDocument(
            corpus="news", source_id="compact-results-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="Example Reports Q4 EPS Beat, Revenue Rises 24%",
            text="", tickers=("EXM",),
        )
        resolver = NewsIssuerResolver(
            (IssuerIdentity("EXM", "issuer:exm", ("Example",)),),
            article_tickers=("EXM",),
        )
        result = classify_news_document_v9(document, issuer_resolver=resolver)
        self.assertEqual(result.content_role, "automated_summary")
        self.assertTrue(result.labels)
        self.assertEqual(result.labels[0]["classification"]["semantic_direction"], "positive")
        self.assertTrue(result.labels[0]["forecast_trigger_eligible"])

    def test_usda_approval_is_positive_regulatory_event(self) -> None:
        document = SemanticDocument(
            corpus="news", source_id="usda-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="USDA Approves Example Crop Treatment",
            text="The USDA approved Example Corp. crop treatment.",
            tickers=("EXM",),
        )
        resolver = NewsIssuerResolver(
            (IssuerIdentity("EXM", "issuer:exm", ("Example Corp",)),),
            article_tickers=("EXM",),
        )
        result = classify_news_document_v9(document, issuer_resolver=resolver)
        self.assertEqual(result.labels[0]["classification"]["semantic_direction"], "positive")
        self.assertIn("regulatory", result.labels[0]["classification"]["event_concepts"])

    def test_economic_calendar_is_preview(self) -> None:
        document = SemanticDocument(
            corpus="news", source_id="calendar-v9",
            timestamp="2026-01-02T12:00:00Z",
            title="Economic Calendar For Friday",
            text="Economic releases scheduled for Friday.", tickers=(),
        )
        self.assertEqual(classify_news_document_v9(document).content_role, "preview")

    def test_reported_earlier_offering_is_followup_and_not_trigger(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="reported-earlier-v9",
            timestamp="2026-01-02T14:00:00Z",
            title=(
                "Reported Earlier, Example Prices Public Offering Of 6.5M "
                "Shares At $10 Each"
            ),
            text=(
                "Example Corp. (NASDAQ:EXM) priced an upsized public offering "
                "of 6.5 million common shares at $10 each."
            ),
            tickers=("EXM",),
        )
        resolver = NewsIssuerResolver(
            (IssuerIdentity("EXM", "issuer:exm", ("Example Corp",)),),
            article_tickers=("EXM",),
        )
        result = classify_news_document_v9(document, issuer_resolver=resolver)
        self.assertEqual(result.content_role, "why_moving_followup")
        self.assertFalse(result.labels[0]["forecast_trigger_eligible"])
        self.assertFalse(result.labels[0]["reaction_evaluation_eligible"])

    def test_reported_weekday_event_is_followup(self) -> None:
        document = SemanticDocument(
            corpus="news", source_id="reported-weekday-v9",
            timestamp="2026-01-05T14:00:00Z",
            title="Reported Sunday, Example Initiates Chapter 11 Process",
            text="Example Corp. (NASDAQ:EXM) initiated a prepackaged Chapter 11 process.",
            tickers=("EXM",),
        )
        resolver = NewsIssuerResolver((IssuerIdentity("EXM", "issuer:exm", ("Example Corp",)),))
        result = classify_news_document_v9(document, issuer_resolver=resolver)
        self.assertEqual(result.content_role, "why_moving_followup")
        self.assertFalse(result.labels[0]["forecast_trigger_eligible"])

    def test_single_character_provider_symbol_is_not_plain_prose_identity(self) -> None:
        resolver = NewsIssuerResolver(
            (IssuerIdentity("I", "issuer:i", ("Intelsat",)),),
            article_tickers=("I",),
        )
        self.assertEqual(resolver.resolve("I expect ordinary results.", linked_tickers=("I",)), ())

    def test_provider_linked_leading_brand_alias_resolves(self) -> None:
        resolver = NewsIssuerResolver(
            (IssuerIdentity("NVS", "issuer:nvs", ("Novartis International AG",)),),
            article_tickers=("NVS",),
        )
        matches = resolver.resolve(
            "A lawsuit was filed against Novartis Sandoz unit.", linked_tickers=("NVS",)
        )
        self.assertEqual([match.ticker for match in matches], ["NVS"])

    def test_maintained_rating_and_raised_target_are_separate_actions(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="maintained-target-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="Broker Maintains Buy On Example, Raises Price Target To $25",
            text=(
                "Broker analyst maintains Example Corp. (NASDAQ:EXM) at Buy "
                "and raises the price target from $20 to $25."
            ),
            tickers=("EXM",),
        )
        resolver = NewsIssuerResolver(
            (IssuerIdentity("EXM", "issuer:exm", ("Example Corp",)),),
            article_tickers=("EXM",),
        )
        label = classify_news_document_v9(document, issuer_resolver=resolver).labels[0]
        concepts = set(label["classification"]["event_concepts"])
        self.assertIn("analyst.rating_maintained", concepts)
        self.assertIn("analyst.price_target_raised", concepts)
        self.assertNotIn("analyst.rating_upgrade", concepts)

    def test_single_named_merger_target_gets_target_role_and_positive_consideration(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="single-target-v9",
            timestamp="2026-01-02T14:00:00Z",
            title=(
                "Example Entered Definitive Merger Agreement To Be Acquired "
                "For $7.33 Per Share In Cash"
            ),
            text=(
                "Example Health (NYSE:EXM) entered into a definitive merger "
                "agreement to be acquired by an affiliate for $7.33 per share "
                "in cash."
            ),
            tickers=("EXM",),
        )
        resolver = NewsIssuerResolver(
            (IssuerIdentity("EXM", "issuer:exm", ("Example Health",)),),
            article_tickers=("EXM",),
        )
        label = classify_news_document_v9(document, issuer_resolver=resolver).labels[0]
        self.assertEqual(label["issuer_role"], "target")
        self.assertEqual(label["classification"]["semantic_direction"], "positive")

    def test_new_signed_deal_is_not_suppressed_by_separate_withdrawal(self) -> None:
        result = _recalibrate_direction(
            {
                "semantic_score_raw": 0.7,
                "deterministic_direction_evidence": ("ma_signed:+0.70",),
            },
            issuer_role="acquirer",
            evidence_text=(
                "The issuer withdrew its prior acquisition offer and entered "
                "into a definitive merger agreement with another target."
            ),
        )
        self.assertNotIn(
            "ma_signed:suppressed_inactive_transaction", result["matched_rules"]
        )
        self.assertIn("ma_signed:+0.70", result["matched_rules"])
        self.assertIn("ma_withdrawal_acquirer:-0.75", result["matched_rules"])

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

    def test_syndicated_analyst_blog_is_editorial_not_primary_event(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="zacks-blog-v9",
            timestamp="2010-10-19T19:27:41Z",
            title="AIG's AIA IPO Closes In Advance - Analyst Blog",
            text=(
                "American International Group Inc. (NYSE:AIG) decided to close "
                "the AIA initial public offering early. Zacks Investment Research."
            ),
            tickers=("AIG",),
            metadata={
                "issuer_identities": ({
                    "ticker": "AIG",
                    "issuer_id": "issuer:aig",
                    "aliases": ("American International Group",),
                },),
            },
        )
        resolver = NewsIssuerResolver(
            (IssuerIdentity("AIG", "issuer:aig", ("American International Group",)),),
            article_tickers=("AIG",),
        )
        result = classify_news_document_v9(document, issuer_resolver=resolver)
        self.assertEqual(result.content_role, "editorial_analysis")
        self.assertEqual(result.source_origin, "editorial_original")
        self.assertTrue(result.labels)
        self.assertFalse(result.labels[0]["forecast_trigger_eligible"])
        self.assertFalse(result.labels[0]["reaction_evaluation_eligible"])
        self.assertTrue(result.labels[0]["issuer_history_context_eligible"])

    def test_current_primary_event_overrides_conservative_context_unit_role(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="current-primary-context-role-v9",
            timestamp="2021-03-01T13:00:00Z",
            title=(
                "GoldMining's Subsidiary, Gold Royalty, Further Increases Size "
                "of Proposed Initial Public Offering From 12M Units To 16M Units"
            ),
            text=(
                "GoldMining's Subsidiary, Gold Royalty, Further Increases Size "
                "of Proposed Initial Public Offering From 12M Units To 16M Units"
            ),
            tickers=("GLDG",),
            metadata={
                "channels": ("press releases",),
                "issuer_identities": ({
                    "ticker": "GLDG",
                    "issuer_id": "issuer:gldg",
                    "aliases": ("GoldMining Inc.",),
                },),
            },
        )
        resolver = NewsIssuerResolver(
            (IssuerIdentity("GLDG", "issuer:gldg", ("GoldMining Inc.",)),),
            article_tickers=("GLDG",),
        )
        result = classify_news_document_v9(document, issuer_resolver=resolver)
        self.assertEqual(result.content_role, "primary_event")
        self.assertTrue(result.labels[0]["forecast_trigger_eligible"])
        self.assertNotIn(
            "context_only_unit_role:ticker_scoped_editorial_context",
            result.labels[0]["classification"]["eligibility_basis"],
        )

    def test_market_primer_preview_channel_is_preview(self) -> None:
        document = SemanticDocument(
            corpus="news", source_id="primer-preview-v9",
            timestamp="2026-01-02T14:00:00Z", title="Market Primer: Earnings Ahead",
            text=("Five issuers are expected to report. A is expected to report. "
                  "B is expected to report. C is expected to report. "
                  "D is expected to report. E is expected to report."),
            tickers=(), metadata={"channels": ("previews",)},
        )
        result = classify_news_document_v9(document)
        self.assertEqual(result.content_role, "preview")

    def test_selling_stockholder_secondary_is_not_issuer_dilution(self) -> None:
        document = SemanticDocument(
            corpus="news", source_id="holder-secondary-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="Example Announces Buyback And Secondary Offering",
            text=("Example Corp. (NASDAQ:EXM) announced a share repurchase program. "
                  "The secondary offering consists solely of shares offered by an existing selling stockholder."),
            tickers=("EXM",),
        )
        resolver = NewsIssuerResolver((IssuerIdentity("EXM", "issuer:exm", ("Example Corp",)),))
        label = classify_news_document_v9(document, issuer_resolver=resolver).labels[0]
        self.assertEqual(label["classification"]["semantic_direction"], "mixed")
        self.assertTrue(label["forecast_trigger_eligible"])

    def test_current_event_is_not_retrospective_from_later_program_reference(self) -> None:
        document = SemanticDocument(
            corpus="news", source_id="current-with-history-v9",
            timestamp="2026-01-02T14:00:00Z", title="Example Announces New Share Repurchase Program",
            text=("Example Corp. (NASDAQ:EXM) announced a new $20 million share repurchase program. "
                  "It also referenced its previously announced credit agreement."),
            tickers=("EXM",),
        )
        resolver = NewsIssuerResolver((IssuerIdentity("EXM", "issuer:exm", ("Example Corp",)),))
        label = classify_news_document_v9(document, issuer_resolver=resolver).labels[0]
        self.assertTrue(label["forecast_trigger_eligible"])
        self.assertNotIn("retrospective_or_republished_event", label["classification"]["eligibility_basis"])

    def test_parent_subsidiary_ipo_is_not_parent_dilution(self) -> None:
        document = SemanticDocument(
            corpus="news", source_id="subsidiary-ipo-v9",
            timestamp="2026-01-02T14:00:00Z", title="Example Subsidiary Completes IPO",
            text="Example Corp. (NYSE:EXM) said its subsidiary completed an initial public offering of shares.",
            tickers=("EXM",),
        )
        resolver = NewsIssuerResolver((IssuerIdentity("EXM", "issuer:exm", ("Example Corp",)),))
        label = classify_news_document_v9(document, issuer_resolver=resolver).labels[0]
        self.assertNotEqual(label["classification"]["semantic_direction"], "negative")

    def test_trading_halt_is_negative_for_affected_security(self) -> None:
        document = SemanticDocument(
            corpus="news", source_id="halt-v9", timestamp="2026-01-02T14:00:00Z",
            title="Exchange Suspends Trading In Example",
            text="Trading in Example Corp. (NASDAQ:EXM) was halted pending additional information.",
            tickers=("EXM",),
        )
        resolver = NewsIssuerResolver((IssuerIdentity("EXM", "issuer:exm", ("Example Corp",)),))
        label = classify_news_document_v9(document, issuer_resolver=resolver).labels[0]
        self.assertEqual(label["classification"]["semantic_direction"], "negative")

    def test_strategic_investment_with_board_rights_is_mixed(self) -> None:
        document = SemanticDocument(
            corpus="news", source_id="strategic-investment-v9",
            timestamp="2026-01-02T14:00:00Z", title="Investor Makes Strategic Investment In Example",
            text=("Example Corp. (NASDAQ:EXM) received a strategic investment through newly issued shares "
                  "and named the investor's nominee to its board of directors."),
            tickers=("EXM",),
        )
        resolver = NewsIssuerResolver((IssuerIdentity("EXM", "issuer:exm", ("Example Corp",)),))
        label = classify_news_document_v9(document, issuer_resolver=resolver).labels[0]
        self.assertEqual(label["classification"]["semantic_direction"], "mixed")

    def test_nasdaq_venue_comment_assigns_halt_to_affected_security(self) -> None:
        title = (
            "Nasdaq Comments On Aytu Bioscience Trading Halt, Tells Benzinga "
            "Stock Remains Halted Solely On Circuit Breaker Amid Recently-New "
            "Circuit Breaker Rules, Highlights Circuit Breaker Halt Is Automated"
        )
        document = SemanticDocument(
            corpus="news",
            source_id="venue-actor-v9",
            timestamp="2017-12-15T15:52:09Z",
            title=title,
            text=f"Title: {title}",
            tickers=("AYTU", "NDAQ"),
            metadata={
                "channels": ("news", "exclusives", "trading ideas"),
                "issuer_identities": (
                    {"ticker": "AYTU", "issuer_id": "issuer:aytu", "aliases": ()},
                    {"ticker": "NDAQ", "issuer_id": "issuer:ndaq", "aliases": ("Nasdaq",)},
                ),
            },
        )
        resolver = NewsIssuerResolver(
            (
                IssuerIdentity("AYTU", "issuer:aytu", ()),
                IssuerIdentity("NDAQ", "issuer:ndaq", ("Nasdaq",)),
            ),
            article_tickers=("AYTU", "NDAQ"),
        )

        result = classify_news_document_v9(document, issuer_resolver=resolver)

        self.assertEqual(result.content_role, "regulatory_event")
        self.assertEqual(result.as_dict()["scope_extractor_version"], NEWS_EXTRACTOR_VERSION)
        self.assertEqual([label["ticker"] for label in result.labels], ["AYTU"])
        self.assertIn(
            "venue_actor_disambiguated_from_listed_issuer",
            result.labels[0]["classification"]["quality_flags"],
        )

    def test_nasdaq_issuer_news_still_resolves_ndaq(self) -> None:
        title = "Nasdaq Inc Reports Quarterly Results And Higher Revenue"
        text = (
            "Nasdaq, Inc. (NASDAQ:NDAQ) reports quarterly results and says "
            "revenue increased from the prior year."
        )
        document = SemanticDocument(
            corpus="news",
            source_id="nasdaq-issuer-v9",
            timestamp="2026-01-02T14:00:00Z",
            title=title,
            text=text,
            tickers=("NDAQ",),
            metadata={
                "issuer_identities": ({
                    "ticker": "NDAQ", "issuer_id": "issuer:ndaq", "aliases": ("Nasdaq", "Nasdaq Inc"),
                },),
            },
        )
        resolver = NewsIssuerResolver(
            (IssuerIdentity("NDAQ", "issuer:ndaq", ("Nasdaq", "Nasdaq Inc")),),
            article_tickers=("NDAQ",),
        )

        result = classify_news_document_v9(document, issuer_resolver=resolver)

        self.assertEqual([label["ticker"] for label in result.labels], ["NDAQ"])
        self.assertNotIn(
            "venue_actor_disambiguated_from_listed_issuer",
            result.labels[0]["classification"]["quality_flags"],
        )

    def test_complete_identity_does_not_turn_index_change_into_analysis(self) -> None:
        title = (
            "SolarWinds To Replace SunPower In S&P SmallCap 600, Effective "
            "Prior To The Opening Of Trading"
        )
        document = SemanticDocument(
            corpus="news",
            source_id="index-change-v9",
            timestamp="2024-08-06T21:30:39Z",
            title=title,
            text=(
                f"Title: {title}\nSunPower has filed for Chapter 11 bankruptcy "
                "and is no longer eligible for continued inclusion."
            ),
            tickers=("SPWR", "SWI"),
        )
        resolver = NewsIssuerResolver(
            (
                IssuerIdentity("SPWR", "issuer:spwr", ("SunPower",)),
                IssuerIdentity("SWI", "issuer:swi", ("SolarWinds",)),
            )
        )
        result = classify_news_document_v9(document, issuer_resolver=resolver)
        self.assertEqual(result.content_role, "primary_event")

    def test_analyst_ratings_channel_remains_analyst_with_complete_identity(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="analyst-report-v9",
            timestamp="2022-08-25T17:41:19Z",
            title="SBEV: 2Q Results; Wholesale Wins Should Pave The Way",
            text=(
                "Splash Beverage Group reported results. Our price target of "
                "$5 remains unchanged and we continue to be bullish."
            ),
            tickers=(),
            metadata={"channels": ("analyst ratings",)},
        )
        resolver = NewsIssuerResolver(
            (IssuerIdentity("SBEV", "issuer:sbev", ("Splash Beverage Group",)),)
        )
        result = classify_news_document_v9(document, issuer_resolver=resolver)
        self.assertEqual(result.content_role, "analyst_event")

    def test_shared_acquisition_keeps_transaction_concept_for_each_issuer(self) -> None:
        title = (
            "United Rentals To No Longer Pursue Acquisition Of H&E Equipment "
            "Services; Plans To Restart Its Share Repurchase Program"
        )
        document = SemanticDocument(
            corpus="news",
            source_id="shared-ma-v9",
            timestamp="2025-02-18T12:19:44Z",
            title=title,
            text=(
                f"Title: {title}\nUnder the merger agreement, H&E is required "
                "to pay a termination fee to United Rentals if H&E terminates "
                "the agreement to enter into an acquisition proposal."
            ),
            tickers=("HEES", "HRI", "URI"),
        )
        resolver = NewsIssuerResolver(
            (
                IssuerIdentity("HEES", "issuer:hees", ("H&E Equipment Services",)),
                IssuerIdentity("HRI", "issuer:hri", ("Herc Holdings",)),
                IssuerIdentity("URI", "issuer:uri", ("United Rentals",)),
            )
        )
        result = classify_news_document_v9(document, issuer_resolver=resolver)
        labels = {label["ticker"]: label for label in result.labels}
        self.assertEqual(set(labels), {"HEES", "URI"})
        self.assertIn(
            "ma_transaction", labels["HEES"]["classification"]["event_concepts"]
        )
        self.assertNotIn(
            "capital_return", labels["HEES"]["classification"]["event_concepts"]
        )
        self.assertIn(
            "ma_transaction", labels["URI"]["classification"]["event_concepts"]
        )
        self.assertIn(
            "capital_return", labels["URI"]["classification"]["event_concepts"]
        )
        self.assertNotIn(
            "share repurchase",
            str(labels["HEES"]["semantic_evidence_text"]).casefold(),
        )
        self.assertIn(
            "share repurchase",
            str(labels["URI"]["semantic_evidence_text"]).casefold(),
        )
        self.assertEqual(labels["HEES"]["issuer_role"], "target")
        self.assertEqual(labels["URI"]["issuer_role"], "acquirer")
        self.assertEqual(
            labels["HEES"]["classification"]["semantic_direction"], "positive"
        )
        self.assertEqual(
            labels["URI"]["classification"]["semantic_direction"], "mixed"
        )
        self.assertIn(
            "ma_replacement_proposal_target:+0.85",
            labels["HEES"]["classification"]["deterministic_direction_evidence"],
        )
        self.assertIn(
            "ma_withdrawal_acquirer:-0.75",
            labels["URI"]["classification"]["deterministic_direction_evidence"],
        )
        self.assertIn(
            "ma_signed:suppressed_inactive_transaction",
            labels["URI"]["classification"]["deterministic_direction_evidence"],
        )

    def test_roundup_is_context_only_even_with_real_issuer_events(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="roundup-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="30 Stocks Moving In Friday's Pre-Market Session",
            text=(
                "Example Corp. (NASDAQ:EXM) shares rose after the company "
                "reported better-than-expected earnings and raised guidance."
            ),
            tickers=("EXM",),
        )
        resolver = NewsIssuerResolver(
            (IssuerIdentity("EXM", "issuer:exm", ("Example Corp",)),)
        )
        result = classify_news_document_v9(document, issuer_resolver=resolver)
        self.assertEqual(result.content_role, "mover_recap")
        self.assertEqual(result.labels[0]["classification"]["semantic_direction"], "positive")
        self.assertFalse(result.labels[0]["forecast_trigger_eligible"])
        self.assertFalse(result.labels[0]["reaction_evaluation_eligible"])
        self.assertTrue(result.labels[0]["issuer_history_context_eligible"])

    def test_roundup_keeps_event_issuer_but_drops_price_only_context(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="roundup-provider-omission-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="Mid-Morning Market Update",
            text=(
                "Example Holdings reported weak quarterly earnings and lowered "
                "its full-year outlook. Other Corp. (NASDAQ:OTH) shares were "
                "unchanged after an ordinary trading session."
            ),
            tickers=("OTH",),
        )
        resolver = NewsIssuerResolver(
            (
                IssuerIdentity("EXM", "issuer:exm", ("Example Holdings",)),
                IssuerIdentity("OTH", "issuer:oth", ("Other Corp",)),
            )
        )
        result = classify_news_document_v9(document, issuer_resolver=resolver)
        self.assertEqual({label["ticker"] for label in result.labels}, {"EXM"})
        example = next(label for label in result.labels if label["ticker"] == "EXM")
        self.assertFalse(example["forecast_trigger_eligible"])
        self.assertTrue(example["issuer_history_context_eligible"])

    def test_nested_and_envelope_eligibility_are_identical(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="eligibility-contract-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="Broker Downgrades Example To Sell",
            text="Broker analyst downgraded Example Corp. (NASDAQ:EXM) to Sell.",
            tickers=("EXM",),
        )
        resolver = NewsIssuerResolver(
            (IssuerIdentity("EXM", "issuer:exm", ("Example Corp",)),)
        )
        label = classify_news_document_v9(document, issuer_resolver=resolver).labels[0]
        classification = label["classification"]
        self.assertEqual(
            classification["forecast_trigger_eligible"],
            label["forecast_trigger_eligible"],
        )
        self.assertEqual(
            classification["reaction_evaluation_eligible"],
            label["reaction_evaluation_eligible"],
        )

    def test_editorial_report_of_regulatory_event_is_not_regulatory_primary(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="reported-regulatory-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="Example Shares Fall After FDA Places Trial On Hold",
            text=(
                "Benzinga reports that the FDA placed Example Corp. "
                "(NASDAQ:EXM) on clinical hold."
            ),
            tickers=("EXM",),
        )
        resolver = NewsIssuerResolver(
            (IssuerIdentity("EXM", "issuer:exm", ("Example Corp",)),)
        )
        result = classify_news_document_v9(document, issuer_resolver=resolver)
        self.assertEqual(result.content_role, "why_moving_followup")
        self.assertEqual(result.source_origin, "editorial_original")

    def test_top_downgrades_is_an_analyst_aggregation_not_market_roundup(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="top-downgrades-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="Top 5 Downgrades For Friday",
            text="A broker downgraded Example Corp. (NASDAQ:EXM) to Sell.",
            tickers=("EXM",),
            metadata={"provider_tags": ["top downgrades"]},
        )
        resolver = NewsIssuerResolver(
            (IssuerIdentity("EXM", "issuer:exm", ("Example Corp",)),)
        )
        result = classify_news_document_v9(document, issuer_resolver=resolver)
        self.assertEqual(result.content_role, "analyst_event")

    def test_earnings_season_etf_article_is_preview(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="etf-preview-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="Three ETFs For The Coming Earnings Season",
            text="Investors can watch Example ETF (NYSE:EXM) before results arrive.",
            tickers=("EXM",),
        )
        resolver = NewsIssuerResolver(
            (IssuerIdentity("EXM", "issuer:exm", ("Example ETF",)),)
        )
        self.assertEqual(
            classify_news_document_v9(document, issuer_resolver=resolver).content_role,
            "preview",
        )

    def test_ev_insights_byline_is_not_automated_content(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="ev-insights-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="Example And Partner Form Electric Vehicle Joint Venture",
            text=(
                "Example Corp. (NASDAQ:EXM) announced a joint venture to "
                "produce electric vehicles."
            ),
            tickers=("EXM",),
            metadata={"author": "Benzinga EV Insights", "channels": ["news"]},
        )
        resolver = NewsIssuerResolver(
            (IssuerIdentity("EXM", "issuer:exm", ("Example Corp",)),)
        )
        result = classify_news_document_v9(document, issuer_resolver=resolver)
        self.assertNotEqual(result.content_role, "automated_summary")
        self.assertNotEqual(result.source_origin, "automated_summary")

    def test_ordinary_expense_amount_does_not_trigger_legal_payment(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="expense-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="Example Reports Revenue Beat And Guides Below Consensus",
            text=(
                "Example Corp. (NASDAQ:EXM) beat revenue estimates. It reported "
                "operating expenses of $42 million and guides below consensus."
            ),
            tickers=("EXM",),
        )
        resolver = NewsIssuerResolver(
            (IssuerIdentity("EXM", "issuer:exm", ("Example Corp",)),)
        )
        label = classify_news_document_v9(document, issuer_resolver=resolver).labels[0]
        evidence = label["classification"]["deterministic_direction_evidence"]
        self.assertNotIn("precedence:material_legal_payment", evidence)

    def test_numeric_guidance_range_below_estimate_is_negative(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="numeric-guidance-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="Example Sees Q3 EPS $0.35-$0.40 Vs $0.42 Est.",
            text="Example Corp. (NASDAQ:EXM) sees Q3 EPS $0.35-$0.40 vs $0.42 estimate.",
            tickers=("EXM",),
        )
        resolver = NewsIssuerResolver(
            (IssuerIdentity("EXM", "issuer:exm", ("Example Corp",)),)
        )
        label = classify_news_document_v9(document, issuer_resolver=resolver).labels[0]
        self.assertEqual(label["classification"]["semantic_direction"], "negative")

    def test_dividend_increase_preserves_adverse_earnings_component(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="mixed-dividend-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="Example Earnings Decline, Raises Dividend",
            text=(
                "Example Corp. (NASDAQ:EXM) reported revenue declined 18% and "
                "earnings decreased 30%, but raised its quarterly dividend by 3%."
            ),
            tickers=("EXM",),
        )
        resolver = NewsIssuerResolver(
            (IssuerIdentity("EXM", "issuer:exm", ("Example Corp",)),)
        )
        label = classify_news_document_v9(document, issuer_resolver=resolver).labels[0]
        self.assertEqual(label["classification"]["semantic_direction"], "mixed")

    def test_primary_endpoint_failure_overrides_positive_boilerplate(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="endpoint-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="Example Reports Phase 3 Trial Results",
            text=(
                "Example Bio (NASDAQ:EXM) said the treatment was well tolerated, "
                "but the Phase 3 study did not meet its primary endpoint."
            ),
            tickers=("EXM",),
        )
        resolver = NewsIssuerResolver(
            (IssuerIdentity("EXM", "issuer:exm", ("Example Bio",)),)
        )
        label = classify_news_document_v9(document, issuer_resolver=resolver).labels[0]
        self.assertEqual(label["classification"]["semantic_direction"], "negative")
        self.assertIn("clinical.failure", label["classification"]["event_concepts"])

    def test_prelisting_symbol_is_resolved_but_not_tradable(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="ipo-v9",
            timestamp="2014-01-02T14:00:00Z",
            title='Example Corp Files For IPO; Ticker Will Be "EXM"',
            text=(
                'Example Corp. filed a registration statement for its initial public '
                'offering and plans to list under ticker symbol "EXM".'
            ),
            tickers=(),
        )
        resolver = NewsIssuerResolver(())
        result = classify_news_document_v9(document, issuer_resolver=resolver)
        self.assertEqual([label["ticker"] for label in result.labels], ["EXM"])
        self.assertIn(
            "listing_market_structure.ipo",
            result.labels[0]["classification"]["event_concepts"],
        )
        self.assertFalse(result.labels[0]["forecast_trigger_eligible"])

    def test_ordinary_board_appointment_is_history_only(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="board-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="Example Elects Two Directors To Its Board",
            text="Example Corp. (NASDAQ:EXM) elected two directors to its board.",
            tickers=("EXM",),
        )
        resolver = NewsIssuerResolver(
            (IssuerIdentity("EXM", "issuer:exm", ("Example Corp",)),)
        )
        label = classify_news_document_v9(document, issuer_resolver=resolver).labels[0]
        self.assertEqual(label["classification"]["semantic_direction"], "neutral")
        self.assertFalse(label["forecast_trigger_eligible"])
        self.assertFalse(label["reaction_evaluation_eligible"])
        self.assertTrue(label["issuer_history_context_eligible"])

    def test_no_security_content_is_not_identity_failure(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="macro-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="Continuing Jobless Claims Match Consensus",
            text="Continuing jobless claims were 2.87 million versus 2.87 million expected.",
            tickers=(),
        )
        result = classify_news_document_v9(document)
        self.assertEqual(result.extraction_decision, "non_issuer_market_content")

    def test_observed_price_move_does_not_define_event_direction(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="reaction-separation-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="Example Reports Earnings Miss As Shares Rise",
            text=(
                "Example Corp. (NASDAQ:EXM) reported EPS of $0.20 versus $0.30 "
                "expected. Shares rose 12% after hours."
            ),
            tickers=("EXM",),
        )
        resolver = NewsIssuerResolver(
            (IssuerIdentity("EXM", "issuer:exm", ("Example Corp",)),)
        )
        label = classify_news_document_v9(document, issuer_resolver=resolver).labels[0]
        self.assertEqual(label["classification"]["semantic_direction"], "negative")

    def test_related_link_does_not_contaminate_current_event(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="related-link-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="Example Reports Customer Growth",
            text=(
                "Example Corp. (NASDAQ:EXM) reported customers grew 20%.\n"
                "Related Link: Other Company Announces Dilutive Public Offering"
            ),
            tickers=("EXM",),
        )
        resolver = NewsIssuerResolver(
            (IssuerIdentity("EXM", "issuer:exm", ("Example Corp",)),)
        )
        label = classify_news_document_v9(document, issuer_resolver=resolver).labels[0]
        self.assertNotIn("financing", label["classification"]["event_concepts"])

    def test_generated_benzinga_neuro_disclosure_sets_automated_role(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="neuro-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="Fund Buys Example Shares After Results",
            text=(
                "Example Corp. (NASDAQ:EXM) reported quarterly results. "
                "This story was generated using Benzinga Neuro and edited by Staff."
            ),
            tickers=("EXM",),
        )
        resolver = NewsIssuerResolver(
            (IssuerIdentity("EXM", "issuer:exm", ("Example Corp",)),)
        )
        result = classify_news_document_v9(document, issuer_resolver=resolver)
        self.assertEqual(result.content_role, "automated_summary")
        self.assertEqual(result.source_origin, "automated_summary")

    def test_explicit_analyst_action_precedes_price_followup_wording(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="analyst-price-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="Example Shares Plummet After Broker Downgrades Stock To Sell",
            text="Broker downgraded Example Corp. (NASDAQ:EXM) to Sell.",
            tickers=("EXM",),
        )
        resolver = NewsIssuerResolver(
            (IssuerIdentity("EXM", "issuer:exm", ("Example Corp",)),)
        )
        result = classify_news_document_v9(document, issuer_resolver=resolver)
        self.assertEqual(result.content_role, "analyst_event")

    def test_successful_virologic_response_is_positive_clinical_event(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="clinical-response-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="Example Announces Phase 3 Results",
            text=(
                "Example Bio (NASDAQ:EXM) announced Phase 3 trial results. "
                "Ninety-five percent of patients achieved sustained virologic response."
            ),
            tickers=("EXM",),
            metadata={"channels": ("press releases",)},
        )
        resolver = NewsIssuerResolver(
            (IssuerIdentity("EXM", "issuer:exm", ("Example Bio",)),)
        )
        label = classify_news_document_v9(document, issuer_resolver=resolver).labels[0]
        self.assertEqual(label["classification"]["semantic_direction"], "positive")
        self.assertTrue(label["forecast_trigger_eligible"])

    def test_buyback_authorization_is_primary_not_regulatory(self) -> None:
        document = SemanticDocument(
            corpus="news", source_id="buyback-authorization-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="Example Board Authorizes $500M Share Repurchase",
            text="Example Corp. (NASDAQ:EXM) authorized a $500 million share repurchase.",
            tickers=("EXM",), metadata={"channels": ("press releases",)},
        )
        resolver = NewsIssuerResolver((IssuerIdentity("EXM", "issuer:exm", ("Example Corp",)),))
        result = classify_news_document_v9(document, issuer_resolver=resolver)
        self.assertEqual(result.content_role, "primary_event")
        self.assertEqual(result.labels[0]["classification"]["semantic_direction"], "positive")

    def test_guidance_only_wire_is_primary_not_automated_result(self) -> None:
        document = SemanticDocument(
            corpus="news", source_id="guidance-only-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="Example Sees Q4 Sales $270M-$295M Vs $290M Est",
            text="Example Corp. (NASDAQ:EXM) sees Q4 sales of $270M-$295M versus $290M estimate.",
            tickers=("EXM",), metadata={"author": "Benzinga Newsdesk"},
        )
        resolver = NewsIssuerResolver((IssuerIdentity("EXM", "issuer:exm", ("Example Corp",)),))
        result = classify_news_document_v9(document, issuer_resolver=resolver)
        self.assertEqual(result.content_role, "primary_event")

    def test_macro_stat_with_provider_etf_is_nonissuer_market_content(self) -> None:
        document = SemanticDocument(
            corpus="news", source_id="macro-etf-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="EIA Crude Oil Inventories Fell 2M Barrels Vs 1M Est",
            text="EIA crude oil inventories fell by 2 million barrels.",
            tickers=("USO",),
        )
        resolver = NewsIssuerResolver((IssuerIdentity("USO", "issuer:uso", ("United States Oil Fund",)),))
        result = classify_news_document_v9(document, issuer_resolver=resolver)
        self.assertEqual(result.content_role, "market_roundup")
        self.assertEqual(result.extraction_decision, "non_issuer_market_content")
        self.assertEqual(result.labels, ())

    def test_mover_row_without_catalyst_detail_is_context_only(self) -> None:
        document = SemanticDocument(
            corpus="news", source_id="mover-row-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="12 Stocks Moving In Thursday's After-Market Session",
            text="Example Corp. (NASDAQ:EXM) shares declined 3%. The company's Q4 earnings came out today.",
            tickers=("EXM",), metadata={"author": "Benzinga Insights"},
        )
        resolver = NewsIssuerResolver((IssuerIdentity("EXM", "issuer:exm", ("Example Corp",)),))
        result = classify_news_document_v9(document, issuer_resolver=resolver)
        self.assertEqual(result.content_role, "mover_recap")
        self.assertFalse(result.labels[0]["forecast_trigger_eligible"])
        self.assertTrue(result.labels[0]["issuer_history_context_eligible"])

    def test_product_acronym_does_not_become_linked_issuer(self) -> None:
        document = SemanticDocument(
            corpus="news", source_id="product-acronym-v9",
            timestamp="2011-01-07T14:00:00Z",
            title="Goodrich Delivers Sonar Composite Dome",
            text="Goodrich Corporation (NYSE:GR) delivered its Sonar Composite Dome (SCD) for the Navy.",
            tickers=("GR", "SCD"),
        )
        resolver = NewsIssuerResolver((
            IssuerIdentity("GR", "issuer:gr", ("Goodrich Corporation",)),
            IssuerIdentity("SCD", "issuer:scd", ("LMP Capital and Income Fund",)),
        ))
        result = classify_news_document_v9(document, issuer_resolver=resolver)
        self.assertEqual([row["ticker"] for row in result.labels], ["GR"])

    def test_title_lead_plural_trade_name_resolves_provider_issuer(self) -> None:
        document = SemanticDocument(
            corpus="news", source_id="title-plural-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="Havertys September Comp Sales Up 10%",
            text="Havertys September comp sales rose 10%.",
            tickers=("HVT",),
        )
        resolver = NewsIssuerResolver((IssuerIdentity("HVT", "issuer:hvt", ("Haverty Furniture Companies",)),))
        result = classify_news_document_v9(document, issuer_resolver=resolver)
        self.assertEqual([row["ticker"] for row in result.labels], ["HVT"])

    def test_filing_title_uses_regulatory_primary_origin(self) -> None:
        document = SemanticDocument(
            corpus="news", source_id="filing-origin-v9",
            timestamp="2026-01-02T14:00:00Z",
            title="Example Files Form 8-K Regarding Credit Agreement",
            text="Example Corp. (NASDAQ:EXM) filed a Form 8-K regarding its credit agreement.",
            tickers=("EXM",),
        )
        resolver = NewsIssuerResolver((IssuerIdentity("EXM", "issuer:exm", ("Example Corp",)),))
        result = classify_news_document_v9(document, issuer_resolver=resolver)
        self.assertEqual(result.content_role, "regulatory_event")
        self.assertEqual(result.source_origin, "regulatory_primary")

    def test_unlinked_crypto_name_does_not_resolve_listed_issuer_alias(self) -> None:
        document = SemanticDocument(
            corpus="news", source_id="crypto-alias-v9",
            timestamp="2021-08-04T23:04:17Z",
            title="Dogecoin Won't Be Put Down If It Can Regain This Key Level",
            text=("Dogecoin was trading higher while Bitcoin held support. "
                  "A senator expects a cryptocurrency bill to pass."),
            tickers=(),
        )
        resolver = NewsIssuerResolver((
            IssuerIdentity("DOGP", "issuer:dogp", ("Dogecoin Cash Inc", "Dogecoin")),
        ))
        result = classify_news_document_v9(document, issuer_resolver=resolver)
        self.assertEqual(result.extraction_decision, "non_issuer_market_content")
        self.assertEqual(result.content_role, "editorial_analysis")
        self.assertEqual(result.labels, ())

    def test_concatenated_group_legal_name_supports_title_brand(self) -> None:
        document = SemanticDocument(
            corpus="news", source_id="group-brand-v9",
            timestamp="2024-10-24T21:05:48Z",
            title="Aptar's Board Authorized A $500M Common Stock Repurchase",
            text="Aptar's board authorized the repurchase of common stock.",
            tickers=("ATR",),
        )
        resolver = NewsIssuerResolver((
            IssuerIdentity("ATR", "issuer:atr", ("AptarGroup Inc",)),
        ))
        result = classify_news_document_v9(document, issuer_resolver=resolver)
        self.assertEqual([row["ticker"] for row in result.labels], ["ATR"])
        self.assertEqual(result.content_role, "primary_event")

    def test_pt_abbreviation_is_an_explicit_analyst_action(self) -> None:
        document = SemanticDocument(
            corpus="news", source_id="pt-abbreviation-v9",
            timestamp="2013-01-30T14:12:36Z",
            title="BMO Capital Markets Raises PT to $59 on Graco Following Earnings",
            text="BMO reiterated its Market Perform rating and raised its price target.",
            tickers=("GGG",), metadata={"channels": ("analyst color",)},
        )
        resolver = NewsIssuerResolver((IssuerIdentity("GGG", "issuer:ggg", ("Graco",)),))
        result = classify_news_document_v9(document, issuer_resolver=resolver)
        self.assertEqual(result.content_role, "analyst_event")
        self.assertFalse(result.labels[0]["forecast_trigger_eligible"])

    def test_government_terrestrial_authorization_is_regulatory(self) -> None:
        document = SemanticDocument(
            corpus="news", source_id="terrestrial-auth-v9",
            timestamp="2023-02-27T22:44:54Z",
            title="Spain Grants Globalstar Terrestrial Authorization",
            text="Spain grants Globalstar terrestrial authorization.",
            tickers=("GSAT",),
        )
        resolver = NewsIssuerResolver((IssuerIdentity("GSAT", "issuer:gsat", ("Globalstar",)),))
        result = classify_news_document_v9(document, issuer_resolver=resolver)
        self.assertEqual(result.content_role, "regulatory_event")
        self.assertEqual([row["ticker"] for row in result.labels], ["GSAT"])


if __name__ == "__main__":
    unittest.main()
