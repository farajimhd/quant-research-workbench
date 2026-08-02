from __future__ import annotations

import unittest

import numpy as np

from .comparison import CollectionItem
from .news_v10 import (
    V10_VERSION,
    ForestConfig,
    _forest,
    _prediction_matches_item,
    source_candidates,
    unit_text,
)


class NewsV10Tests(unittest.TestCase):
    def test_prediction_cache_requires_exact_v10_and_source_identity(self) -> None:
        item = CollectionItem(
            sample_id="N1001",
            split="fresh_acceptance",
            blinded={"source_id": "source-1"},
            truth={},
        )
        prediction = {
            "version": V10_VERSION,
            "sample_id": "N1001",
            "split": "fresh_acceptance",
            "source_id": "source-1",
        }
        self.assertTrue(_prediction_matches_item(prediction, item))
        self.assertFalse(
            _prediction_matches_item({**prediction, "source_id": "source-2"}, item)
        )
        self.assertFalse(
            _prediction_matches_item({**prediction, "version": "stale"}, item)
        )

    def test_target_identity_is_masked_from_unit_features(self) -> None:
        source = {
            "publication": {
                "title": "Apple raises guidance",
                "teaser": "Apple expects stronger demand.",
                "provider_tickers": ["AAPL"],
                "channels": ["guidance"],
                "provider_tags": ["earnings"],
            },
            "point_in_time_issuer_candidates": [{
                "ticker": "AAPL",
                "identity_evidence": ["issuer_alias:Apple", "symbol:AAPL"],
            }],
            "rendered_product": {
                "text": "Apple Inc. (NASDAQ:AAPL) raised full-year guidance."
            },
        }
        candidate = source_candidates(source)[0]
        result = unit_text(source, candidate)
        self.assertIn("TARGET_ENTITY", result)
        self.assertNotIn("AAPL", result)
        self.assertNotIn("Apple", result)

    def test_forest_is_bootstrap_bagged_and_bounded(self) -> None:
        config = ForestConfig(article_trees=7, max_leaf_nodes=31, workers=1)
        model = _forest(7, config, balanced=True)
        self.assertTrue(model.bootstrap)
        self.assertEqual(model.max_samples, 0.8)
        self.assertEqual(model.max_leaf_nodes, 31)
        self.assertEqual(model.n_estimators, 7)

    def test_forest_can_fit_bounded_numeric_features(self) -> None:
        config = ForestConfig(article_trees=5, max_leaf_nodes=7, workers=1)
        model = _forest(5, config, balanced=True)
        x = np.asarray([[0.0, 1.0], [1.0, 0.0], [0.1, 0.9], [0.9, 0.1]])
        model.fit(x, np.asarray([0, 1, 0, 1]))
        self.assertEqual(model.predict(x).shape, (4,))
        self.assertEqual(model.classes_.tolist(), [0, 1])


if __name__ == "__main__":
    unittest.main()
