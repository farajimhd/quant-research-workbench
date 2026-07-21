from __future__ import annotations

import tempfile
import unittest
import datetime as dt
from pathlib import Path

from pipelines.news.benzinga.news_reaction_deterministic_v2 import (
    EVENT_DICTIONARY_VERSION,
    LANGUAGE_VERSION,
    MODEL_VERSION,
    PREDICTION_VERSION,
    RELEVANCE_VERSION,
    SCALE_VERSION,
    compose_language,
    calibration_metrics,
    confusion_metrics,
    ddl_statements,
    empirical_bayes_effect,
    identity_alias_insert_sql,
    load_review_labels,
    parse_args,
    relevance_insert_sql,
    shrunken_robust_scale,
    required_sources,
    validate_args,
)
from pipelines.news.benzinga.news_reaction_event_dictionary_v2 import EVENT_RULES


class DeterministicNewsV2Tests(unittest.TestCase):
    def test_versions_are_separate_from_v1(self) -> None:
        for version in (RELEVANCE_VERSION, EVENT_DICTIONARY_VERSION, LANGUAGE_VERSION, SCALE_VERSION, MODEL_VERSION, PREDICTION_VERSION):
            self.assertIn("v2", version)
            self.assertNotEqual(version, "news_phrase_presence_v1")

    def test_dictionary_is_unique_and_covers_required_families(self) -> None:
        self.assertEqual(len(EVENT_RULES), len({rule.event_id for rule in EVENT_RULES}))
        families = {rule.family for rule in EVENT_RULES}
        self.assertTrue({"earnings", "guidance", "financing", "regulatory_clinical", "legal_compliance", "analyst_action"} <= families)
        self.assertTrue(any(rule.event_id == "clinical_no_safety_concern" for rule in EVENT_RULES))

    def test_language_composition_collapses_correlated_family_evidence(self) -> None:
        result = compose_language([
            ("guidance", 1, 0.7, "raise_outlook"),
            ("guidance", 1, 0.4, "raise_guidance_duplicate"),
            ("earnings", -1, 0.6, "revenue_miss"),
        ])
        self.assertAlmostEqual(result["positive_mass"], 0.7)
        self.assertAlmostEqual(result["negative_mass"], 0.6)
        self.assertEqual(result["language_class"], "mixed")
        self.assertEqual(result["positive_evidence_ids"], ["raise_outlook"])

    def test_language_composition_keeps_positive_and_negative_separate(self) -> None:
        positive = compose_language([("regulatory_clinical", 1, 1.0, "fda_approval")])
        negative = compose_language([("guidance", -1, 0.9, "guidance_cut")])
        self.assertEqual(positive["language_class"], "positive")
        self.assertEqual(negative["language_class"], "negative")

    def test_opposing_analyst_actions_form_mixed_language(self) -> None:
        result = compose_language([
            ("analyst_action", 1, 0.22, "analyst_maintains_positive"),
            ("analyst_action", -1, 0.24, "price_target_lower"),
        ])
        self.assertEqual(result["language_class"], "mixed")

    def test_empirical_bayes_effect_shrinks_small_samples(self) -> None:
        posterior, effects, reliability = empirical_bayes_effect((0, 0, 2), (0.25, 0.5, 0.25), prior_strength=60)
        self.assertAlmostEqual(sum(posterior), 1.0)
        self.assertLess(reliability, 0.04)
        self.assertGreater(effects[2], 0)
        self.assertLess(effects[0], 0)

    def test_robust_scale_shrinks_toward_global(self) -> None:
        sparse = shrunken_robust_scale(0.10, 0.02, 5, 120)
        dense = shrunken_robust_scale(0.10, 0.02, 5_000, 120)
        self.assertLess(abs(sparse - 0.02), abs(dense - 0.02))

    def test_confusion_metrics_include_macro_scores(self) -> None:
        metrics = confusion_metrics([
            {"actual_class": "positive", "predicted_class": "positive", "sample_count": 8},
            {"actual_class": "negative", "predicted_class": "positive", "sample_count": 2},
            {"actual_class": "negative", "predicted_class": "negative", "sample_count": 5},
            {"actual_class": "neutral", "predicted_class": "neutral", "sample_count": 5},
        ], ("negative", "neutral", "positive"))
        self.assertEqual(metrics["sample_count"], 20)
        self.assertEqual(metrics["accuracy"], 0.9)
        self.assertIn("macro_f1", metrics)

    def test_confusion_metrics_count_unrecognized_predictions_as_misses(self) -> None:
        metrics = confusion_metrics([
            {"actual_class": "positive", "predicted_class": "positive", "sample_count": 8},
            {"actual_class": "positive", "predicted_class": "not_applicable", "sample_count": 2},
        ], ("negative", "neutral", "positive"))
        self.assertEqual(metrics["sample_count"], 10)
        self.assertEqual(metrics["accuracy"], 0.8)
        self.assertEqual(metrics["prediction_coverage"], 0.8)
        self.assertEqual(metrics["unexpected_predictions"], {"not_applicable": 2})
        self.assertEqual(metrics["per_class"]["positive"]["recall"], 0.8)

    def test_calibration_metrics_are_support_weighted(self) -> None:
        metrics = calibration_metrics([
            {"horizon_code": "1m", "sample_count": 90, "empirical_accuracy": 0.5, "mean_confidence": 0.4},
            {"horizon_code": "1m", "sample_count": 10, "empirical_accuracy": 0.0, "mean_confidence": 0.8},
        ])
        self.assertEqual(metrics["sample_count"], 100)
        self.assertAlmostEqual(metrics["accuracy"], 0.45)
        self.assertAlmostEqual(metrics["mean_confidence"], 0.44)
        self.assertAlmostEqual(metrics["expected_calibration_error"], 0.17)

    def test_default_plan_is_safe_and_bounded(self) -> None:
        args = parse_args([])
        stages = validate_args(args)
        self.assertFalse(args.execute)
        self.assertEqual(args.workers, 2)
        self.assertEqual(stages, ("extract", "scale", "train", "predict", "evaluate"))

    def test_loaded_legacy_news_table_is_the_text_authority(self) -> None:
        args = parse_args([])
        sources = required_sources(args)
        self.assertNotIn("benzinga_news_text_v1", sources)
        self.assertTrue({"body_text", "external_text", "pdf_text"} <= sources[args.normalized_table])

    def test_relevance_uses_us_nonderivative_point_in_time_aliases(self) -> None:
        args = parse_args([])
        identity_sql = identity_alias_insert_sql(args)
        sql = relevance_insert_sql(args, dt.date(2026, 1, 1), dt.date(2026, 2, 1))
        self.assertIn("listing.currency_code = 'USD'", identity_sql)
        self.assertIn("sym.instrument_type IN", identity_sql)
        self.assertIn("arrayFilter(identity ->", sql)
        self.assertIn("entity_in_headline", sql)
        self.assertNotIn("argMax(issuer_alias", sql)

    def test_certified_reviewer_columns_are_imported(self) -> None:
        text = (
            "review_id,canonical_news_id,ticker,published_at_utc,reviewer_sentiment,reviewer_relevance\n"
            "review-1,news-1,AAPL,2026-07-14 13:41:00.000000000,positive,company_specific\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "review.csv"
            path.write_text(text, encoding="utf-8")
            digest, rows = load_review_labels(path)
        self.assertEqual(len(digest), 64)
        self.assertEqual(rows[0]["sentiment_label"], "positive")
        self.assertEqual(rows[0]["relevance_label"], "company_specific")

    def test_ddl_only_creates_v2_targets(self) -> None:
        args = parse_args([])
        sql = "\n".join(ddl_statements(args)).lower()
        self.assertIn("news_ticker_relevance_v2", sql)
        self.assertIn("news_reaction_predictions_v2", sql)
        self.assertNotIn("drop table", sql)
        self.assertNotIn("news_phrase_reaction_stats_v3", sql)


if __name__ == "__main__":
    unittest.main()
