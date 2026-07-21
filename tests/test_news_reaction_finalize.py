from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from pipelines.news.benzinga.news_reaction_finalize import (
    QUALITY_VERSION,
    ROBUST_STATS_VERSION,
    RepairUnit,
    SourceWatermarks,
    classification_metrics,
    extractor_command,
    feature_certification_sql,
    feature_repair_sql,
    merge_repair_ranges,
    parse_args,
    prediction_ctes,
    quality_overlay_insert_sql,
    reaction_repair_sql,
    review_sample_sql,
    review_instructions,
    robust_stats_insert_sql,
    validate_args,
    validate_certification_rows,
    write_review_sample,
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
        self.assertIn("f.phrase_id AS phrase_id", sql)
        self.assertIn("r.horizon_code AS horizon_code", sql)
        self.assertIn("r.publication_session AS publication_session", sql)
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
        self.assertIn("r.canonical_news_id AS canonical_news_id", sql)
        self.assertIn("ON f.canonical_news_id = r.canonical_news_id", sql)
        self.assertNotIn("AS f USING (canonical_news_id)", sql)
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

    def test_review_sample_uses_supported_stable_id_and_exports_body(self) -> None:
        sql = review_sample_sql(self.args, self.watermarks)
        self.assertIn("hex(sipHash128(s.canonical_news_id, s.ticker))", sql)
        self.assertIn("GROUP BY canonical_news_id, ticker", sql)
        self.assertIn("ORDER BY stratum_rank", sql)
        self.assertIn("AS hidden_answers", sql)
        self.assertNotIn("cityHash128", sql)

    def test_review_csv_is_article_level_and_hides_model_and_reaction_fields(self) -> None:
        row = {
            "review_id": "review-1",
            "canonical_news_id": "news-1",
            "ticker": "AAPL",
            "published_at_utc": "2026-01-02 15:00:00.000000000",
            "title": "Apple announces an update",
            "teaser": "Summary",
            "body_excerpt": "Article body",
            "provider_tags": ["company"],
            "channels": ["news"],
            "hidden_answers": [
                ["1m", "regular", ["product_launch"], 0.2, "positive", "neutral", 0.001],
                ["5m", "regular", ["product_launch"], 0.3, "positive", "positive", 0.02],
            ],
            "reviewer_sentiment": "",
            "reviewer_relevance": "",
            "reviewer_notes": "",
        }

        class FakeClient:
            def execute(self, _sql: str) -> str:
                return json.dumps(row)

        with tempfile.TemporaryDirectory() as temporary:
            sample_path = Path(temporary) / "human_review_sample.csv"
            self.assertEqual(write_review_sample(FakeClient(), self.args, self.watermarks, sample_path), 1)
            with sample_path.open(encoding="utf-8-sig", newline="") as handle:
                sample_rows = list(csv.DictReader(handle))
            with sample_path.with_name("human_review_sample_answer_key.csv").open(
                encoding="utf-8-sig", newline=""
            ) as handle:
                answer_rows = list(csv.DictReader(handle))

        self.assertEqual(len(sample_rows), 1)
        self.assertEqual(len(answer_rows), 2)
        for hidden_field in (
            "horizon_code", "publication_session", "phrase_ids", "sentiment_score",
            "predicted_class", "actual_class", "abnormal_target_return",
        ):
            self.assertNotIn(hidden_field, sample_rows[0])
            self.assertIn(hidden_field, answer_rows[0])
        self.assertEqual(answer_rows[0]["review_id"], sample_rows[0]["review_id"])

    def test_review_instructions_require_locked_blind_labels(self) -> None:
        contract = review_instructions(self.args)
        self.assertTrue(contract["model_outputs_hidden"])
        self.assertTrue(contract["future_price_reaction_hidden"])
        self.assertIn("locked", contract["answer_key_policy"].lower())

    def test_feature_certification_counts_outputs_independently(self) -> None:
        sql = feature_certification_sql(self.args, self.watermarks)
        self.assertIn("WITH source AS", sql)
        self.assertIn("outputs AS", sql)
        self.assertIn("FROM source\nLEFT JOIN outputs USING (chunk_start)", sql)
        self.assertIn("ifNull(outputs.output_rows, toUInt64(0))", sql)
        self.assertNotIn("countDistinct(tuple(f.canonical_news_id, f.phrase_id))", sql)

    def test_certification_metadata_must_match_final_audit(self) -> None:
        audit = {
            "feature_chunks": 1,
            "feature_rows": 3,
            "reaction_chunks": 1,
            "unique_label_rows": 20,
        }
        validate_certification_rows(
            [{"output_rows": 3}],
            [{"output_rows": 20}],
            audit,
        )
        with self.assertRaisesRegex(RuntimeError, "features certification metadata"):
            validate_certification_rows(
                [{"output_rows": 4}],
                [{"output_rows": 20}],
                audit,
            )


if __name__ == "__main__":
    unittest.main()
