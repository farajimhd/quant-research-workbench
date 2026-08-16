from __future__ import annotations

import unittest

from src.backend.trading_configuration_service import _default_draft
from src.trading_runtime.watchlist_resolver import (
    classify_watchlist_row,
    resolve_watchlist_membership,
)


class WatchlistResolverTest(unittest.TestCase):
    def setUp(self) -> None:
        self.discovery = _default_draft()["market_discovery"]
        self.watchlists = {
            row["watchlist_id"]: row for row in self.discovery["watchlists"]
        }

    def test_default_catalog_contains_requested_templates_and_columns(self) -> None:
        expected = {
            "top-penny-gainers",
            "top-small-cap-gainers",
            "top-mid-cap-gainers",
            "top-large-cap-gainers",
            "top-penny-volume-gainers",
            "top-small-cap-volume-gainers",
            "top-mid-cap-volume-gainers",
            "top-large-cap-volume-gainers",
            "price-or-volume-squeeze",
            "vwap-breakout",
            "news-bullish-sentiment",
            "news-bearish-sentiment",
            "sec-bullish-sentiment",
            "sec-bearish-sentiment",
            "fundamental-bullish",
            "fundamental-bearish",
            "past-upcoming-ipos",
            "stock-splits",
        }
        self.assertTrue(expected.issubset(self.watchlists))
        for watchlist_id in expected:
            self.assertEqual(self.watchlists[watchlist_id]["maximum_size"], 10)
            self.assertEqual(
                self.watchlists[watchlist_id]["membership_expiry"],
                "end_of_trading_day",
            )
            self.assertTrue(self.watchlists[watchlist_id]["columns"])
        column_ids = {row["column_id"] for row in self.discovery["column_catalog"]}
        self.assertTrue(
            {
                "market_cap",
                "float_shares",
                "short_interest",
                "short_interest_pct",
                "days_to_cover",
                "shares_outstanding",
                "float_quality",
                "short_volume",
                "short_volume_pct",
                "fails_to_deliver",
                "ftd_value",
                "reg_sho_threshold",
                "borrow_status",
                "borrow_shares",
                "borrow_fee",
                "fundamental_trajectory",
                "ipo_event",
                "split_event",
            }.issubset(column_ids)
        )
        field_sources = {
            row["source_id"] for row in self.discovery["field_catalog"]
        }
        self.assertTrue(
            all(
                row.get("source_kind") == "rule_set" or row["source_id"] in field_sources
                for row in self.discovery["column_catalog"]
            )
        )

    def test_cap_and_float_classifications_use_exact_boundaries(self) -> None:
        self.assertEqual(
            classify_watchlist_row({"market_cap": 1_999_999_999, "float_shares": 499_999})[
                "market_cap_category"
            ],
            "Small Cap",
        )
        boundary = classify_watchlist_row(
            {"market_cap": 2_000_000_000, "float_shares": 100_000_000}
        )
        self.assertEqual(boundary["market_cap_category"], "Mid Cap")
        self.assertEqual(boundary["float_category"], "Broad Float")
        self.assertEqual(
            classify_watchlist_row({"market_cap": 10_000_000_000, "float_shares": 2_000_000})[
                "market_cap_category"
            ],
            "Large Cap",
        )

    def test_small_cap_gainers_filter_rank_and_limit(self) -> None:
        watchlist = {**self.watchlists["top-small-cap-gainers"], "maximum_size": 2}
        candidates = [
            {"ticker": "AAA", "market_cap": 1_000_000_000, "change_pct": 4.0},
            {"ticker": "BBB", "market_cap": 500_000_000, "change_pct": 9.0},
            {"ticker": "CCC", "market_cap": 8_000_000_000, "change_pct": 12.0},
            {"ticker": "DDD", "market_cap": 300_000_000, "change_pct": -1.0},
            {"ticker": "EEE", "market_cap": 900_000_000, "change_pct": 6.0},
        ]
        resolved = resolve_watchlist_membership(
            watchlist, self.discovery["rule_sets"], candidates
        )
        self.assertEqual([row["ticker"] for row in resolved], ["BBB", "EEE"])

    def test_bearish_sentiment_ranks_most_negative_first(self) -> None:
        watchlist = {**self.watchlists["news-bearish-sentiment"], "enabled": True}
        resolved = resolve_watchlist_membership(
            watchlist,
            self.discovery["rule_sets"],
            [
                {"ticker": "AAA", "news_labeled": True, "news_sentiment_score": -0.4},
                {"ticker": "BBB", "news_labeled": True, "news_sentiment_score": -0.8},
                {"ticker": "CCC", "news_labeled": False, "news_sentiment_score": -0.9},
            ],
        )
        self.assertEqual([row["ticker"] for row in resolved], ["BBB", "AAA"])

    def test_added_reference_fields_are_executable_rule_and_ranking_sources(self) -> None:
        watchlist = {
            **self.watchlists["core-candidates"],
            "inclusion_rule_sets": ["short-volume-screen"],
            "ranking_field": "reference.ftd_value",
            "maximum_size": 2,
        }
        rule_sets = [
            *self.discovery["rule_sets"],
            {
                "rule_set_id": "short-volume-screen",
                "enabled": True,
                "operator": "all",
                "conditions": [
                    {
                        "enabled": True,
                        "left_source_id": "reference.short_volume_pct",
                        "comparator": "greater_or_equal",
                        "value": 40,
                    }
                ],
            },
        ]
        resolved = resolve_watchlist_membership(
            watchlist,
            rule_sets,
            [
                {"ticker": "AAA", "short_volume_pct": 45, "ftd_value": 10_000},
                {"ticker": "BBB", "short_volume_pct": 55, "ftd_value": 50_000},
                {"ticker": "CCC", "short_volume_pct": 20, "ftd_value": 100_000},
            ],
        )
        self.assertEqual([row["ticker"] for row in resolved], ["BBB", "AAA"])

    def test_pending_integration_templates_are_disabled_and_fail_closed(self) -> None:
        for watchlist_id in {
            "news-bullish-sentiment",
            "news-bearish-sentiment",
            "sec-bullish-sentiment",
            "sec-bearish-sentiment",
        }:
            watchlist = self.watchlists[watchlist_id]
            self.assertEqual(watchlist["availability"], "integration_pending")
            self.assertFalse(watchlist["enabled"])
            self.assertEqual(
                resolve_watchlist_membership(
                    watchlist,
                    self.discovery["rule_sets"],
                    [{"ticker": "SHOULD_NOT_PASS"}],
                ),
                [],
            )
        self.assertEqual(self.watchlists["past-upcoming-ipos"]["availability"], "available")
        self.assertTrue(self.watchlists["past-upcoming-ipos"]["enabled"])

    def test_missing_evidence_fails_closed_and_manual_inclusion_is_explicit(self) -> None:
        watchlist = {
            **self.watchlists["top-penny-gainers"],
            "manual_inclusions": ["MANUAL"],
        }
        resolved = resolve_watchlist_membership(
            watchlist,
            self.discovery["rule_sets"],
            [{"ticker": "MISSING"}],
        )
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["ticker"], "MANUAL")
        self.assertIn("evidence unavailable", resolved[0]["membership_reason"])


if __name__ == "__main__":
    unittest.main()
