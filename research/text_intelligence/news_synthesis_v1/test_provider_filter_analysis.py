from __future__ import annotations

import unittest
from datetime import UTC, datetime

from .provider_filter_analysis import (
    analysis_split,
    attach_ticker_history,
    candidate_grade,
    extract_title,
    feature_names,
    mutual_information_binary,
    seconds_bucket,
    session_segment,
    text_flags,
    wilson_interval,
)


class ProviderFilterAnalysisTests(unittest.TestCase):
    def test_temporal_split_and_session_are_explicit(self) -> None:
        self.assertEqual(analysis_split(datetime(2025, 12, 1, tzinfo=UTC)), "discovery_2025")
        self.assertEqual(analysis_split(datetime(2026, 4, 30, tzinfo=UTC)), "validation_2026_jan_apr")
        self.assertEqual(analysis_split(datetime(2026, 5, 1, tzinfo=UTC)), "final_2026_may_aug")
        self.assertEqual(session_segment(datetime(2026, 5, 1, 13, 0, tzinfo=UTC)), "premarket")

    def test_text_features_keep_halt_and_material_override_separate(self) -> None:
        text = "Title: Trading Halted Pending News\nThe company announced FDA approval of its therapy."
        flags = text_flags(text)
        self.assertTrue(flags["halt"])
        self.assertTrue(flags["material_event"])
        self.assertEqual(extract_title(text), "Trading Halted Pending News")

    def test_ticker_history_uses_only_prior_rows(self) -> None:
        rows = [
            {
                "source_id": "a", "published_at_utc": datetime(2026, 1, 2, 14, 0, tzinfo=UTC),
                "session_date": "2026-01-02", "tickers": ("AAA",),
            },
            {
                "source_id": "b", "published_at_utc": datetime(2026, 1, 2, 14, 2, tzinfo=UTC),
                "session_date": "2026-01-02", "tickers": ("AAA", "BBB"),
            },
        ]
        summary = attach_ticker_history(rows)
        self.assertTrue(rows[0]["all_tickers_first_session"])
        self.assertTrue(rows[1]["any_ticker_first_session"])
        self.assertFalse(rows[1]["all_tickers_first_session"])
        self.assertEqual(rows[1]["min_seconds_since_previous_ticker_news"], 120)
        self.assertEqual(summary["ticker_links"], 3)

    def test_equal_timestamp_rows_do_not_observe_each_other(self) -> None:
        timestamp = datetime(2026, 1, 2, 14, 0, tzinfo=UTC)
        rows = [
            {"source_id": "a", "published_at_utc": timestamp, "session_date": "2026-01-02", "tickers": ("AAA",)},
            {"source_id": "b", "published_at_utc": timestamp, "session_date": "2026-01-02", "tickers": ("AAA",)},
        ]
        attach_ticker_history(rows)
        self.assertIsNone(rows[0]["min_seconds_since_previous_ticker_news"])
        self.assertIsNone(rows[1]["min_seconds_since_previous_ticker_news"])
        self.assertTrue(rows[0]["all_tickers_first_session"])
        self.assertTrue(rows[1]["all_tickers_first_session"])

    def test_feature_path_does_not_reject_material_halt(self) -> None:
        row = {
            "provider": "benzinga", "ticker_count": 1, "session_segment": "regular",
            "hour_et": 10, "weekday_et": "mon", "any_ticker_first_session": True,
            "all_tickers_first_session": True, "min_ticker_session_ordinal": 1,
            "min_seconds_since_previous_ticker_news": None, "any_ticker_news_within_5m": False,
            "any_ticker_news_within_30m": False, "human_certified": False,
            "authority_class": "provisional", "update_delay_seconds": 1,
            "provider_tags": ("halts",), "channels": (), "halt": True,
            "material_event": True, "question_title": False, "title_only": False,
            **{name: False for name in (
                "analyst_rating", "price_target", "earnings_preview", "why_moving",
                "list_or_screener", "market_recap", "technical_or_valuation",
                "short_interest", "index_or_listing", "macro",
            )},
        }
        features = feature_names(row)
        self.assertNotIn("rule:halt_without_material_override", features)

    def test_statistics_are_bounded_and_informative(self) -> None:
        low, high = wilson_interval(1, 100)
        self.assertLess(low, 0.01)
        self.assertGreater(high, 0.01)
        self.assertGreater(mutual_information_binary(1, 99, 50, 150), 0)
        self.assertEqual(seconds_bucket(299), "lt_5m")

    def test_candidate_grade_requires_every_forward_period(self) -> None:
        row = {
            "discovery_support": 1000, "validation_support": 100, "final_support": 100,
            "discovery_eligible_rate": 0.001, "validation_eligible_rate": 0.005,
            "final_eligible_rate": 0.01,
        }
        self.assertEqual(candidate_grade(row), "high_precision_candidate")
        row["final_support"] = 10
        self.assertEqual(candidate_grade(row), "insufficient_forward_support")

    def test_decisive_contract_is_binary(self) -> None:
        from .provider_filter_analysis import DECISIVE_LABELS

        self.assertEqual(DECISIVE_LABELS, {"eligible", "ineligible"})


if __name__ == "__main__":
    unittest.main()
