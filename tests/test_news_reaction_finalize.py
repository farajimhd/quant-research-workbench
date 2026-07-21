from __future__ import annotations

import unittest

from pipelines.news.benzinga.news_reaction_finalize import (
    QUALITY_VERSION,
    ROBUST_STATS_VERSION,
    RepairUnit,
    SourceWatermarks,
    classification_metrics,
    extractor_command,
    feature_repair_sql,
    merge_repair_ranges,
    parse_args,
    prediction_ctes,
    quality_overlay_insert_sql,
    reaction_repair_sql,
    robust_stats_insert_sql,
    validate_args,
)


class NewsReactionFinalizeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.args = parse_args([])
        self.watermarks = SourceWatermarks(
            captured_at_utc="2026-07-21 12:00:00.000000",
            news_max_published_at_utc="2026-07-21 11:00:00.000000000",
            news_max_updated_at_utc="2026-07-21 11:01:00.000000000",
            ticker_max_updated_at_utc="2026-07-21 11:01:00.000000000",
            event_max_timestamp_utc="2026-07-17 23:59:59.000000",
            event_max_date="2026-07-17",
            event_table="events_2026",
            news_settled_end_exclusive="2026-07-19",
            event_complete_end_exclusive="2026-07-17",
            stable_end_exclusive="2026-07-17",
        )

    def test_default_split_is_2019_2025_training_and_2026_holdout(self) -> None:
        self.assertEqual(validate_args(self.args), ("watermarks", "repair", "quality", "stats", "evaluate"))
        self.assertEqual(self.args.stats_end_date, self.args.holdout_start_date)
        self.assertFalse(self.args.execute)

    def test_repair_plans_compare_source_freshness_and_exact_key_counts(self) -> None:
        feature_sql = feature_repair_sql(self.args, self.watermarks)
        reaction_sql = reaction_repair_sql(self.args, self.watermarks)
        self.assertIn("source_newer_than_checkpoint", feature_sql)
        self.assertIn("text_hash_changed", feature_sql)
        self.assertIn("source_pairs * 10", reaction_sql)
        self.assertIn("label_count_mismatch", reaction_sql)
        self.assertNotIn("intraday_base_bars", reaction_sql)

    def test_repair_ranges_merge_only_overlapping_or_adjacent_units(self) -> None:
        units = [
            RepairUnit("reactions", "2019-01-01", "2019-01-02", 1, 0, "completed", ("label_count_mismatch",)),
            RepairUnit("reactions", "2019-01-02", "2019-01-03", 1, 0, "completed", ("label_count_mismatch",)),
            RepairUnit("reactions", "2019-01-05", "2019-01-06", 1, 0, "completed", ("label_count_mismatch",)),
        ]
        self.assertEqual(
            merge_repair_ranges(units),
            [("2019-01-01", "2019-01-03"), ("2019-01-05", "2019-01-06")],
        )

    def test_extractor_repair_command_uses_exact_events_and_never_exposes_password(self) -> None:
        self.args.password = "do-not-leak"
        command = extractor_command(self.args, "reactions", "2019-01-01", "2019-01-03")
        rendered = " ".join(command)
        self.assertIn("--stages reactions", rendered)
        self.assertIn("--replace-existing", command)
        self.assertIn("--events-table events", rendered)
        self.assertNotIn("do-not-leak", rendered)
        self.assertNotIn("--password", command)

    def test_quality_overlay_excludes_split_overlap_and_extreme_returns(self) -> None:
        sql = quality_overlay_insert_sql(self.args, self.watermarks)
        self.assertIn("market_stock_split_v1", sql)
        self.assertIn("execution_date BETWEEN", sql)
        self.assertIn("corporate_action_overlap", sql)
        self.assertIn("extreme_return_outlier", sql)
        self.assertIn("eligible_for_statistics", sql)
        self.assertIn(QUALITY_VERSION, sql)

    def test_robust_stats_use_presence_rows_and_trimmed_distributions(self) -> None:
        sql = robust_stats_insert_sql(self.args)
        self.assertIn("trimmed_mean_target_return", sql)
        self.assertIn("quantileTDigestIf(0.5)", sql)
        self.assertIn("corporate_action_excluded_count", sql)
        self.assertIn("eligible_for_statistics = 1", sql)
        self.assertIn(ROBUST_STATS_VERSION, sql)
        self.assertNotIn("occurrence_count", sql)

    def test_holdout_predictions_use_only_pre_2026_statistics(self) -> None:
        sql = prediction_ctes(self.args, self.watermarks)
        self.assertIn("news_phrase_event_reaction_stats_v4", sql)
        self.assertIn("2026-01-01", sql)
        self.assertIn("positive_probability - negative_probability", sql)
        self.assertIn("groupUniqArray(phrase_id)", sql)

    def test_holdout_metrics_report_balanced_accuracy_and_macro_f1(self) -> None:
        metrics = classification_metrics(
            {
                "negative": {"negative": 8, "neutral": 1, "positive": 1},
                "neutral": {"negative": 1, "neutral": 8, "positive": 1},
                "positive": {"negative": 1, "neutral": 1, "positive": 8},
            }
        )
        self.assertAlmostEqual(metrics["balanced_accuracy"], 0.8)
        self.assertAlmostEqual(metrics["macro_f1"], 0.8)


if __name__ == "__main__":
    unittest.main()
