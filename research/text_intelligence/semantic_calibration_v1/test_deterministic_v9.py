from __future__ import annotations

import unittest

from research.text_intelligence.scoped_labeling_v1.news_identity import IssuerIdentity, NewsIssuerResolver
from research.text_intelligence.scoped_labeling_v1.schema import NEWS_EXTRACTOR_VERSION
from research.text_intelligence.semantic_label_authority_v1.schema import SemanticDocument

from .deterministic_v9 import _recalibrate_direction, classify_news_document_v9
from .deterministic_v9_config import CALIBRATION_SPLIT_SHA256, CALIBRATION_VERSION
from .teacher_split_v9 import normalized_headline_template


class DeterministicV9Tests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
