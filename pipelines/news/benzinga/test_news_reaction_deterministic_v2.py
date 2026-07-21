from __future__ import annotations

import unittest

from pipelines.news.benzinga.news_reaction_deterministic_v2 import (
    EVENT_DICTIONARY_VERSION,
    LANGUAGE_VERSION,
    MODEL_VERSION,
    PREDICTION_VERSION,
    RELEVANCE_VERSION,
    compose_language,
    confusion_metrics,
    ddl_statements,
    empirical_bayes_effect,
    parse_args,
    shrunken_robust_scale,
    validate_args,
)
from pipelines.news.benzinga.news_reaction_event_dictionary_v2 import EVENT_RULES


class DeterministicNewsV2Tests(unittest.TestCase):
    def test_versions_are_separate_from_v1(self) -> None:
        for version in (RELEVANCE_VERSION, EVENT_DICTIONARY_VERSION, LANGUAGE_VERSION, MODEL_VERSION, PREDICTION_VERSION):
            self.assertTrue(version.endswith("v2"))

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

    def test_default_plan_is_safe_and_bounded(self) -> None:
        args = parse_args([])
        stages = validate_args(args)
        self.assertFalse(args.execute)
        self.assertEqual(args.workers, 2)
        self.assertEqual(stages, ("extract", "scale", "train", "predict", "evaluate"))

    def test_ddl_only_creates_v2_targets(self) -> None:
        args = parse_args([])
        sql = "\n".join(ddl_statements(args)).lower()
        self.assertIn("news_ticker_relevance_v2", sql)
        self.assertIn("news_reaction_predictions_v2", sql)
        self.assertNotIn("drop table", sql)
        self.assertNotIn("news_phrase_reaction_stats_v3", sql)


if __name__ == "__main__":
    unittest.main()
