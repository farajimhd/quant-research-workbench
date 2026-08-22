from __future__ import annotations

import unittest
from datetime import UTC, datetime

from .provider_market_cap_analysis import (
    TimeIndex,
    TimedValue,
    cap_bucket,
    chars_bucket,
    contextual_paths,
    annotate_interactions,
    candidate_membership,
    market_cap_paths,
    summarize_article_caps,
)


class ProviderMarketCapAnalysisTests(unittest.TestCase):
    def test_market_cap_buckets_have_explicit_boundaries(self) -> None:
        self.assertEqual(cap_bucket(None), "missing")
        self.assertEqual(cap_bucket(49_999_999), "nano_lt_50m")
        self.assertEqual(cap_bucket(50_000_000), "micro_50m_300m")
        self.assertEqual(cap_bucket(300_000_000), "small_300m_2b")
        self.assertEqual(cap_bucket(2_000_000_000), "mid_2b_10b")
        self.assertEqual(cap_bucket(10_000_000_000), "large_10b_200b")
        self.assertEqual(cap_bucket(200_000_000_000), "mega_gte_200b")

    def test_time_index_is_strictly_prior(self) -> None:
        at = datetime(2026, 1, 2, 15, tzinfo=UTC)
        index = TimeIndex((TimedValue(at, 100.0, at, "test"),))
        self.assertIsNone(index.before(at))
        self.assertEqual(index.before(datetime(2026, 1, 2, 15, 0, 1, tzinfo=UTC)).value, 100.0)

    def test_multi_ticker_summary_preserves_missingness_and_cap_mix(self) -> None:
        summary = summarize_article_caps((
            {"market_cap": 40_000_000, "market_cap_bucket": "nano_lt_50m", "market_cap_source": "derived", "market_cap_age_days": 2.0},
            {"market_cap": 300_000_000_000, "market_cap_bucket": "mega_gte_200b", "market_cap_source": "provider_snapshot", "market_cap_age_days": 0.5},
        ), 3)
        self.assertEqual(summary["market_cap_coverage"], "partial")
        self.assertEqual(summary["market_cap_known_ticker_count"], 2)
        self.assertEqual(summary["market_cap_missing_fraction"], 1 / 3)
        self.assertEqual(summary["market_cap_min_bucket"], "nano_lt_50m")
        self.assertEqual(summary["market_cap_max_bucket"], "mega_gte_200b")
        self.assertTrue(summary["market_cap_contains_nano_micro"])

    def test_paths_include_market_cap_and_rendered_length_context(self) -> None:
        cap_row = {
            "market_cap_coverage": "complete", "market_cap_min_bucket": "small_300m_2b",
            "market_cap_max_bucket": "small_300m_2b", "market_cap_bucket_set": "small_300m_2b",
            "market_cap_all_same_bucket": True, "market_cap_contains_nano_micro": False,
            "market_cap_source_set": "derived_sec_shares_prior_close", "market_cap_max_age_bucket": "8_30d",
        }
        self.assertIn("market_cap_min_bucket=small_300m_2b", market_cap_paths(cap_row))
        self.assertEqual(chars_bucket(2501), "2501_5000")

        base = {
            "provider": "benzinga", "ticker_count": 1, "session_segment": "regular",
            "hour_et": 10, "weekday_et": "mon", "any_ticker_first_session": True,
            "all_tickers_first_session": True, "min_ticker_session_ordinal": 1,
            "min_seconds_since_previous_ticker_news": None, "any_ticker_news_within_5m": False,
            "any_ticker_news_within_30m": False, "human_certified": False,
            "authority_class": "provisional", "update_delay_seconds": 1,
            "provider_tags": (), "channels": ("markets",), "material_event": False,
            "rendered_chars": 2501, "question_title": False, "title_only": False,
            **{name: False for name in (
                "halt", "analyst_rating", "price_target", "earnings_preview", "why_moving",
                "list_or_screener", "market_recap", "technical_or_valuation", "short_interest",
                "index_or_listing", "macro",
            )},
        }
        self.assertIn("rendered_chars_bucket=2501_5000", contextual_paths(base))

    def test_interactions_retain_the_unconditioned_context_baseline(self) -> None:
        context = {
            "feature": "channel=markets", "support": 100, "eligible": 20,
            "eligible_rate": 0.2, "discovery_support": 40, "validation_support": 30, "final_support": 30,
            "discovery_eligible_rate": 0.2, "validation_eligible_rate": 0.2, "final_eligible_rate": 0.2,
        }
        interaction = {
            "feature": "market_cap_interaction=market_cap_min_bucket=nano_lt_50m && channel=markets",
            "support": 40, "eligible": 1, "eligible_rate": 0.025,
            "discovery_support": 15, "validation_support": 12, "final_support": 13,
            "discovery_eligible_rate": 0.0, "validation_eligible_rate": 0.0, "final_eligible_rate": 1 / 13,
        }
        row = annotate_interactions((interaction,), (context,))[0]
        self.assertEqual(row["context_support"], 100)
        self.assertEqual(row["context_eligible_rate"], 0.2)
        self.assertAlmostEqual(row["eligible_rate_delta_vs_context"], -0.175)

    def test_candidate_membership_deduplicates_overlapping_paths(self) -> None:
        row = {
            "source_id": "a", "published_at_text": "2026-01-02T15:00:00+00:00",
            "published_month": "2026-01", "split": "validation_2026_jan_apr", "label": "ineligible",
            "tickers": ("AAA",), "ticker_count": 1, "provider": "benzinga", "provider_tags": (),
            "channels": ("markets",), "rendered_chars": 1000, "session_segment": "regular",
            "hour_et": 10, "weekday_et": "fri", "any_ticker_first_session": True,
            "all_tickers_first_session": True, "min_ticker_session_ordinal": 1,
            "min_seconds_since_previous_ticker_news": None, "any_ticker_news_within_5m": False,
            "any_ticker_news_within_30m": False, "human_certified": False,
            "authority_class": "provisional", "update_delay_seconds": 1, "material_event": False,
            "question_title": False, "title_only": False, "market_cap_coverage": "complete",
            "market_cap_min_bucket": "micro_50m_300m", "market_cap_max_bucket": "micro_50m_300m",
            "market_cap_bucket_set": "micro_50m_300m", "market_cap_all_same_bucket": True,
            "market_cap_contains_nano_micro": True, "market_cap_source_set": "provider_snapshot",
            "market_cap_max_age_bucket": "lte_1d",
            **{name: False for name in (
                "halt", "analyst_rating", "price_target", "earnings_preview", "why_moving",
                "list_or_screener", "market_recap", "technical_or_valuation", "short_interest",
                "index_or_listing", "macro",
            )},
        }
        candidate = {
            "feature": "market_cap_interaction=market_cap_min_bucket=micro_50m_300m && channel=markets",
            "market_cap_feature": "market_cap_min_bucket=micro_50m_300m", "context_feature": "channel=markets",
            "candidate_grade": "high_precision_candidate", "opening_class": "opened_precision_path",
        }
        rows, summary = candidate_membership((row,), (candidate,))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["matched_candidate_count"], 1)
        self.assertEqual(summary["all_candidates"]["validation_2026_jan_apr"]["ineligible"], 1)


if __name__ == "__main__":
    unittest.main()
