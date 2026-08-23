from __future__ import annotations

import unittest

from .structured_metadata_rf import (
    _feature_row,
    normalize_category,
    ordinal_bin,
    recency_bin,
    rendered_chars_bin,
    ticker_count_bin,
)


class StructuredMetadataRFTests(unittest.TestCase):
    def test_category_normalization_is_bounded_and_deterministic(self) -> None:
        self.assertEqual(normalize_category("  Analyst   Ratings "), "analyst ratings")
        long_value = "x" * 300
        self.assertEqual(normalize_category(long_value), normalize_category(long_value))
        self.assertLess(len(normalize_category(long_value)), 300)

    def test_declared_bins_have_stable_boundaries(self) -> None:
        self.assertEqual(ticker_count_bin(11), "gt_10")
        self.assertEqual(rendered_chars_bin(1001), "1001_2500")
        self.assertEqual(recency_bin(3601), "1_4h")
        self.assertEqual(ordinal_bin(None), "missing")

    def test_feature_row_excludes_exact_ticker_identity_and_tfidf(self) -> None:
        row = {
            "source_id": "secret", "published_at_text": "2025-01-02T15:00:00+00:00",
            "provider": "Benzinga", "provider_tags": ["Halts"], "channels": ["News"],
            "content_quality_flags": [], "tickers": ["AAPL"], "ticker_count": 1,
            "rendered_chars": 100, "session_segment": "regular", "hour_et": 10,
            "weekday_et": "thu", "label": "eligible",
        }
        cap = {
            "market_cap_coverage": "complete", "market_cap_min_bucket": "large_10b_200b",
            "market_cap_max_bucket": "large_10b_200b", "market_cap_bucket_set": "large_10b_200b",
            "market_cap_source_set": "provider_snapshot", "market_cap_max_age_bucket": "lte_1d",
            "market_cap_tickers": [{"market_cap_bucket": "large_10b_200b"}],
        }
        active = {family: values for family, values in {
            "provider": {"benzinga"}, "tag": {"halts"}, "channel": {"news"}, "quality": set(),
            "session_segment": {"regular"}, "hour_et": {"10"}, "weekday_et": {"thu"}, "month": {"1"},
            "ticker_count_bin": {"1"}, "rendered_chars_bin": {"lte_200"}, "min_recency_bin": {"missing"},
            "min_ordinal_bin": {"missing"}, "market_cap_coverage": {"complete"},
            "market_cap_min_bucket": {"large_10b_200b"}, "market_cap_max_bucket": {"large_10b_200b"},
            "market_cap_bucket_set": {"large_10b_200b"}, "market_cap_source_set": {"provider_snapshot"},
            "market_cap_age_bucket": {"lte_1d"},
        }.items()}
        features = _feature_row(row, cap, active=active, historical={})
        self.assertFalse(any("aapl" in name.casefold() for name in features))
        self.assertFalse(any("tfidf" in name.casefold() for name in features))
        self.assertNotIn("source_id", features)
        # 15:00 UTC is 10:00 ET on this winter date; the cyclical hour must
        # follow the explicit ET authority rather than the UTC timestamp.
        self.assertAlmostEqual(features["numeric:hour_sin"], 0.5, places=2)


if __name__ == "__main__":
    unittest.main()
