from __future__ import annotations

import unittest

from .direct_trading_sentiment_audit import (
    build_benchmark_identity_snapshot,
    compare_manifests,
)


class DirectTradingSentimentAuditTests(unittest.TestCase):
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
