from __future__ import annotations

import unittest

from pipelines.sec.edgar.sec_taxonomy import normalize_title, semantic_label, title_match_metrics


class SecTaxonomyTest(unittest.TestCase):
    def test_title_normalization_removes_layout_noise(self) -> None:
        self.assertEqual(normalize_title(" Annual  Report -- (PDF) "), "annual report")

    def test_ordered_word_distance_rewards_compact_order(self) -> None:
        close = title_match_metrics("annual report registered investment company", "annual report of registered management investment company")
        far = title_match_metrics("annual report registered investment company", "company application annual investment report registered")
        self.assertGreater(close["ordered_coverage"], far["ordered_coverage"])
        self.assertGreater(close["score"], far["score"])
        self.assertLessEqual(close["score"], 1.0)

    def test_structured_fund_dataset_is_not_text_embedded(self) -> None:
        label = semantic_label("N-PX", "Annual report of proxy voting record")
        self.assertFalse(label.embedding_enabled)
        self.assertEqual(label.input_strategy, "structured_extraction_only")

    def test_current_report_is_high_impact_candidate(self) -> None:
        label = semantic_label("8-K", "Current report")
        self.assertEqual(label.impact_score, 5)
        self.assertTrue(label.embedding_enabled)


if __name__ == "__main__":
    unittest.main()
