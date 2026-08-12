from __future__ import annotations

import unittest

import numpy as np

from .embedding_supervision import (
    deterministic_stratified_split,
    evaluation_report,
    match_issuer_embedding,
    pool_embedding_chunks,
)
from .run_embedding_supervision import _fit_tuning_indexes


class EmbeddingSupervisionTests(unittest.TestCase):
    def test_chunk_pooling_is_ticker_balanced_and_normalized(self) -> None:
        rows = [
            {"source_id": "s1", "ticker": "A", "token_chunk_index": 0, "embedding": [1.0, 0.0]},
            {"source_id": "s1", "ticker": "A", "token_chunk_index": 1, "embedding": [1.0, 0.0]},
            {"source_id": "s1", "ticker": "B", "token_chunk_index": 0, "embedding": [0.0, 1.0]},
        ]
        article, issuer, report = pool_embedding_chunks(rows, embedding_dim=2)
        np.testing.assert_allclose(issuer[("s1", "A")], [1.0, 0.0])
        np.testing.assert_allclose(article["s1"], [2 ** -0.5, 2 ** -0.5])
        self.assertEqual(report["embedding_rows"], 3)

    def test_duplicate_logical_embedding_chunk_fails(self) -> None:
        row = {"source_id": "s1", "ticker": "A", "token_chunk_index": 0, "embedding": [1.0, 0.0]}
        with self.assertRaisesRegex(RuntimeError, "Duplicate"):
            pool_embedding_chunks([row, row], embedding_dim=2)

    def test_exchange_prefixed_gold_ticker_can_match_unique_database_ticker(self) -> None:
        expected = np.asarray([1.0, 0.0], dtype=np.float32)
        value, status = match_issuer_embedding("s1", "TSX:ABC", {("s1", "ABC"): expected})
        np.testing.assert_array_equal(value, expected)
        self.assertEqual(status, "normalized_ticker")

    def test_split_is_exact_deterministic_and_article_grouped(self) -> None:
        rows = [
            {
                "source_id": f"s{i:02d}",
                "authority_id": f"a{i % 2}",
                "article_forecast_eligible": bool(i % 3),
                "issuer_units": [{
                    "forecast_eligibility": "eligible" if i % 3 else "ineligible",
                    "sentiment": "positive" if i % 2 else "negative",
                    "concepts": [f"c{i % 4}"],
                }],
            }
            for i in range(20)
        ]
        left, report = deterministic_stratified_split(rows, seed="fixed", candidate_count=16)
        right, _ = deterministic_stratified_split(rows, seed="fixed", candidate_count=16)
        self.assertEqual(left, right)
        self.assertEqual(sum(value == "train" for value in left.values()), 15)
        self.assertEqual(sum(value == "validation" for value in left.values()), 5)
        self.assertEqual(report["effective_train_fraction"], 0.75)

    def test_internal_tuning_never_uses_official_validation_articles(self) -> None:
        article_metadata = [
            {
                "source_id": f"source-{index:02d}",
                "split": "train" if index < 15 else "validation",
            }
            for index in range(20)
        ]
        issuer_metadata = [
            {"source_id": row["source_id"], "split": row["split"]}
            for row in article_metadata
            for _ in range(2)
        ]
        article_fit, article_tuning, issuer_fit, issuer_tuning = _fit_tuning_indexes(
            article_metadata,
            issuer_metadata,
            seed=17,
            tuning_fraction=0.10,
        )
        tuning_sources = {
            article_metadata[index]["source_id"] for index in article_tuning
        }
        self.assertEqual(len(article_fit), 13)
        self.assertEqual(len(article_tuning), 2)
        self.assertEqual(len(issuer_fit), 26)
        self.assertEqual(len(issuer_tuning), 4)
        self.assertTrue(
            all(article_metadata[index]["split"] == "train" for index in article_tuning)
        )
        self.assertEqual(
            tuning_sources,
            {issuer_metadata[index]["source_id"] for index in issuer_tuning},
        )

    def test_evaluation_reports_every_binary_sentiment_and_concept_label(self) -> None:
        report = evaluation_report(
            article_truth=np.asarray([0, 1]),
            article_logits=np.asarray([-1.0, 1.0]),
            issuer_eligibility_truth=np.asarray([0, 1]),
            issuer_eligibility_logits=np.asarray([1.0, 1.0]),
            sentiment_truth=np.asarray([0, 1, 2, 3, -1]),
            sentiment_logits=np.eye(5, 4, dtype=np.float32),
            concept_truth=np.asarray([[1, 0], [0, 1]], dtype=np.uint8),
            concept_logits=np.asarray([[1.0, -1.0], [-1.0, 1.0]], dtype=np.float32),
            concept_labels=("one", "two"),
        )
        self.assertEqual(report["article_forecast_eligibility"]["accuracy"], 1.0)
        self.assertEqual(set(report["issuer_sentiment"]["per_label"]), {"positive", "negative", "neutral", "mixed"})
        self.assertEqual(set(report["issuer_concepts"]["per_label"]), {"one", "two"})
        self.assertEqual(report["issuer_concepts"]["subset_accuracy"], 1.0)


if __name__ == "__main__":
    unittest.main()
