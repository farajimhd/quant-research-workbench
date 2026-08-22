from __future__ import annotations

import unittest

from .provider_filter_merged_path_analysis import (
    EXPECTED_RESIDUAL_PATHS,
    EXPECTED_SEMANTIC_PATHS,
    expected_direction,
    merge_path_catalogs,
)


class ProviderFilterMergedPathAnalysisTests(unittest.TestCase):
    def test_catalog_merge_preserves_distinct_authority(self) -> None:
        semantic = [
            {
                "feature": f"tag=semantic-{index}",
                "category": "tag",
                "semantic_label": "likely_ineligible",
                "support": "100",
                "eligible_rate": "0.01",
            }
            for index in range(EXPECTED_SEMANTIC_PATHS)
        ]
        residual = [
            {
                "feature": f"channel=residual-{index}",
                "category": "channel",
                "path_class": "stable_eligible",
                "support": "300",
                "eligible_rate": "0.99",
            }
            for index in range(EXPECTED_RESIDUAL_PATHS)
        ]

        merged = merge_path_catalogs(semantic, residual)

        self.assertEqual(len(merged), 1_132)
        self.assertEqual(expected_direction(merged[0]), "ineligible")
        self.assertEqual(expected_direction(merged[-1]), "eligible")

    def test_catalog_merge_rejects_cross_catalog_overlap(self) -> None:
        semantic = [
            {
                "feature": f"tag=semantic-{index}",
                "category": "tag",
                "semantic_label": "likely_eligible",
                "support": "100",
                "eligible_rate": "0.5",
            }
            for index in range(EXPECTED_SEMANTIC_PATHS)
        ]
        residual = [
            {
                "feature": "tag=semantic-0" if index == 0 else f"channel=residual-{index}",
                "category": "channel",
                "path_class": "stable_mixed",
                "support": "300",
                "eligible_rate": "0.5",
            }
            for index in range(EXPECTED_RESIDUAL_PATHS)
        ]

        with self.assertRaisesRegex(ValueError, "overlap"):
            merge_path_catalogs(semantic, residual)


if __name__ == "__main__":
    unittest.main()
