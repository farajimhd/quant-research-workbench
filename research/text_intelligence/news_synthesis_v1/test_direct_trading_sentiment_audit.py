from __future__ import annotations

import unittest

from .direct_trading_sentiment_audit import (
    article_source,
    build_benchmark_identity_snapshot,
    certified_direct_trading_units,
    compare_manifests,
    prediction_sentiments,
)


class DirectTradingSentimentAuditTests(unittest.TestCase):
    def test_prediction_sentiments_reads_ticker_bound_issuer_views(self) -> None:
        self.assertEqual(prediction_sentiments({
            "entities": [{"entity_id": "security:AAA", "ticker": "AAA"}],
            "issuer_views": [{"entity_id": "security:AAA", "composite_sentiment": "positive"}],
        }), {"AAA": "positive"})

    def test_certified_v1_document_is_the_sentiment_and_eligibility_authority(self) -> None:
        document = {
            "entities": [{"entity_id": "security:AAA", "ticker": "AAA"}],
            "issuer_views": [{
                "entity_id": "security:AAA",
                "composite_sentiment": "negative",
                "positive_strength": 1,
                "negative_strength": 4,
            }],
            "eligibility": [
                {"entity_id": "security:AAA", "product": "forecast_trigger", "eligible": True},
                {"entity_id": "security:AAA", "product": "analyst_evaluation", "eligible": False},
            ],
        }
        self.assertEqual(certified_direct_trading_units(document), [{
            "entity_id": "security:AAA",
            "ticker": "AAA",
            "semantic_direction": "negative",
            "positive_evidence_level": 1,
            "negative_evidence_level": 4,
            "forecast_trigger_eligible": True,
            "analyst_evaluation_eligible": False,
        }])

    def test_article_source_adds_evaluation_scope_without_replacing_provider_tickers(self) -> None:
        source = article_source(
            {
                "source_id": "source-1",
                "source_timestamp": "2026-08-03T12:00:00Z",
                "publication": {"provider_tickers": ["AAA"]},
                "rendered_product": {"text": "Issuer update"},
            },
            additional_tickers=("BBB", "AAA"),
        )
        self.assertEqual(source["tickers"], ["AAA", "BBB"])

    def test_snapshot_accepts_reviewed_candidate_identifier_without_article_metadata(self) -> None:
        index, snapshot = build_benchmark_identity_snapshot(
            (),
            supplemental_tickers=("VRNT",),
        )
        self.assertEqual([row["ticker"] for row in snapshot["identities"]], ["VRNT"])
        supported = index.supported_candidates(
            candidates=("VRNT",),
            timestamp="2026-08-03T12:00:00Z",
        )
        self.assertEqual([row["ticker"] for row in supported], ["VRNT"])

    def test_snapshot_uses_reviewed_identity_without_sentiment_or_eligibility(self) -> None:
        index, snapshot = build_benchmark_identity_snapshot(
            (),
            reviewed_entities=({
                "ticker": "CWY.AX",
                "display_name": "Cleanaway Waste Management",
                "identity_status": "resolved",
                "identity_evidence": ["source_name:Cleanaway"],
                "composite_sentiment": "positive",
                "forecast_trigger_eligible": True,
            },),
        )
        self.assertEqual([row["ticker"] for row in snapshot["identities"]], ["CWY.AX"])
        self.assertEqual(
            snapshot["identities"][0]["aliases"],
            ["Cleanaway", "Cleanaway Waste Management"],
        )
        resolved = index.resolve(
            text="Cleanaway is trialing an electric garbage truck.",
            candidates=("CWY.AX",),
            timestamp="2019-12-16T16:19:13Z",
        )
        self.assertEqual([row["ticker"] for row in resolved], ["CWY.AX"])
        self.assertNotIn("composite_sentiment", snapshot["identities"][0])
        self.assertNotIn("forecast_trigger_eligible", snapshot["identities"][0])

    def test_reviewed_identity_does_not_rewrite_existing_source_identity(self) -> None:
        article = {
            "publication": {
                "title": "Existing Corp update",
                "provider_tickers": ["AAA"],
            },
            "rendered_product": {"text": "Existing Corp update"},
            "point_in_time_issuer_candidates": [{
                "ticker": "AAA",
                "identity_evidence": ["issuer_alias:existing corp"],
            }],
        }
        _index, snapshot = build_benchmark_identity_snapshot(
            (article,),
            reviewed_entities=({
                "ticker": "AAA",
                "display_name": "Replacement Name",
                "identity_status": "resolved",
                "identity_evidence": ["source_name:replacement"],
            },),
        )
        self.assertEqual(snapshot["identities"][0]["aliases"], ["existing corp"])

    def test_snapshot_repairs_provider_candidates_and_preserves_shared_issuer(self) -> None:
        article = {
            "publication": {
                "title": "Urban One files a shelf offering",
                "teaser": "",
                "provider_tickers": ["UONE", "UONEK"],
            },
            "rendered_product": {
                "text": "Urban One files a shelf offering.",
            },
            "point_in_time_issuer_candidates": [
                {"ticker": "UONE", "identity_evidence": ["issuer_alias:urban one"]},
                {"ticker": "UONEK", "identity_evidence": ["issuer_alias:urban one"]},
            ],
        }
        index, snapshot = build_benchmark_identity_snapshot((article,))
        resolved = index.resolve(
            text="Urban One files a shelf offering.",
            candidates=("UONE", "UONEK"),
            timestamp="2026-08-03T12:00:00Z",
        )
        self.assertEqual({row["ticker"] for row in resolved}, {"UONE", "UONEK"})
        issuer_ids = {row["issuer_id"] for row in snapshot["identities"]}
        self.assertEqual(len(issuer_ids), 1)

    def test_snapshot_normalizes_exchange_prefixed_provider_identifier(self) -> None:
        article = {
            "publication": {
                "title": "Raytheon buys a business",
                "teaser": "",
                "provider_tickers": ["NYSE:RTN"],
            },
            "rendered_product": {"text": "Raytheon buys a business."},
            "point_in_time_issuer_candidates": [
                {"ticker": "RTN", "identity_evidence": ["issuer_alias:raytheon"]}
            ],
        }
        _index, snapshot = build_benchmark_identity_snapshot((article,))
        self.assertEqual(
            [row["ticker"] for row in snapshot["identities"]],
            ["RTN"],
        )

    def test_manifest_comparison_uses_record_derived_legacy_missing_count(self) -> None:
        previous = {
            "version": "v1",
            "population": {
                "certified_news": 1045,
                "distinct_direct_trading_news": 491,
                "direct_trading_issuer_units": 564,
                "exact_sentiment_matches": 266,
                "sentiment_mismatches": 298,
            },
            "records": [
                {"predicted_sentiment": "missing"},
                {"predicted_sentiment": "negative"},
            ],
        }
        current = {
            "version": "v2",
            "population": {
                "certified_news": 1045,
                "distinct_direct_trading_news": 491,
                "direct_trading_issuer_units": 564,
                "exact_sentiment_matches": 300,
                "sentiment_mismatches": 264,
                "missing_sentiments": 0,
            },
        }
        result = compare_manifests(previous, current)
        self.assertTrue(result["population_identity_equal"])
        self.assertEqual(result["metrics"]["missing_sentiments"]["before"], 1)
        self.assertEqual(result["metrics"]["missing_sentiments"]["after"], 0)

    def test_comparison_reports_identity_level_error_transitions(self) -> None:
        previous = {
            "version": "old",
            "population": {
                "certified_news": 2,
                "distinct_direct_trading_news": 2,
                "direct_trading_issuer_units": 3,
                "exact_sentiment_matches": 0,
                "sentiment_mismatches": 3,
            },
            "records": [
                {"sample_id": "N1", "ticker": "AAA", "predicted_sentiment": "missing"},
                {"sample_id": "N2", "ticker": "BBB", "predicted_sentiment": "negative"},
                {"sample_id": "N2", "ticker": "CCC", "predicted_sentiment": "missing"},
            ],
        }
        current = {
            "version": "new",
            "population": {
                "certified_news": 2,
                "distinct_direct_trading_news": 2,
                "direct_trading_issuer_units": 3,
                "exact_sentiment_matches": 1,
                "sentiment_mismatches": 2,
                "missing_sentiments": 1,
            },
            "records": [
                {"sample_id": "N1", "ticker": "AAA", "predicted_sentiment": "positive"},
                {"sample_id": "N3", "ticker": "DDD", "predicted_sentiment": "missing"},
            ],
        }
        transitions = compare_manifests(previous, current)["identity_transitions"]
        self.assertEqual(transitions["fixed_errors"], 2)
        self.assertEqual(transitions["new_errors"], 1)
        self.assertEqual(transitions["missing_recovered_correct"], 1)
        self.assertEqual(transitions["missing_recovered_wrong_direction"], 1)
        self.assertEqual(transitions["newly_missing"], 1)


if __name__ == "__main__":
    unittest.main()
