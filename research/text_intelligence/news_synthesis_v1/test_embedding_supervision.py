from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

import numpy as np

from .embedding_supervision import (
    deterministic_stratified_split,
    evaluation_report,
    match_issuer_embedding,
    pool_embedding_chunks,
)
from .engine import IssuerIdentity, IssuerIdentityIndex
from .run_embedding_supervision import _fit_tuning_indexes
from .run_tfidf_supervision_v2 import _train_args as _v2_train_args
from .run_tfidf_supervision_v3 import _train_args as _v3_train_args
from .tfidf_supervision import fit_tfidf_vocabulary, transform_tfidf
from .tfidf_supervision_v2 import (
    fit_v2_vocabulary,
    normalize_financial_text,
    parse_qwen_news_document,
    tfidf_v2_feature_counts,
)
from .tfidf_supervision_v3 import (
    anonymize_issuer_mentions,
    economic_relation_features,
    fit_v3_vocabulary,
    issuer_local_clauses,
    point_in_time_aliases,
    tfidf_v3_feature_counts,
)


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

    def test_tfidf_vocabulary_is_fit_only_from_supplied_training_text(self) -> None:
        terms, idf, report = fit_tfidf_vocabulary(
            ["alpha revenue grows", "alpha margin grows", "beta revenue falls"],
            max_features=20,
            min_document_frequency=1,
            max_document_fraction=1.0,
        )
        vocabulary = {term: index for index, term in enumerate(terms)}
        vector = transform_tfidf("alpha unseen validation phrase", vocabulary, idf)
        self.assertEqual(report["training_documents"], 3)
        self.assertNotIn("u:unseen", vocabulary)
        self.assertGreater(float(np.linalg.norm(vector)), 0.0)

    def test_tfidf_v2_preserves_fields_and_excludes_renderer_headers(self) -> None:
        parsed = parse_qwen_news_document(
            "NEWS\nprovider: wire\nticker: ABC\ntitle: Revenue Rose 20%\n"
            "teaser: Guidance raised\nchannels: earnings\ntags: results\nBODY\n"
            "ABC reported growth.\nEXTERNAL_TEXT\nExternal appendix."
        )
        self.assertEqual(parsed["title"], "Revenue Rose 20%")
        self.assertEqual(parsed["body"], "ABC reported growth.")
        self.assertEqual(parsed["external"], "External appendix.")
        features = tfidf_v2_feature_counts(
            "NEWS\nprovider: wire\nticker: ABC\ntitle: Revenue Rose 20%\n"
            "teaser: Guidance raised\nchannels: earnings\ntags: results\nBODY\n"
            "ABC reported growth.",
            ticker="ABC",
        )
        self.assertIn("title_word|u:revenue", features)
        self.assertIn("title_word|u:<percent>", features)
        self.assertIn("structural|focality:ticker_in_content", features)
        self.assertFalse(any("provider" in term for term in features))

    def test_tfidf_v2_normalizes_financial_quantities(self) -> None:
        normalized = normalize_financial_text("Sales rose 12.5% to $3.2 million in 2025.")
        self.assertIn("<percent>", normalized)
        self.assertIn("<money>", normalized)
        self.assertIn("<year>", normalized)
        self.assertNotIn("12.5", normalized)

    def test_tfidf_v2_vocabulary_is_training_only_and_budgeted_by_field(self) -> None:
        documents = [
            ("AAA", "NEWS\nticker: AAA\ntitle: Alpha grows\nteaser: Margin up\nBODY\nAAA revenue grows."),
            ("BBB", "NEWS\nticker: BBB\ntitle: Beta falls\nteaser: Margin down\nBODY\nBBB revenue falls."),
        ]
        terms, _, report = fit_v2_vocabulary(
            documents,
            min_document_frequency=1,
            budgets={
                "title_word": 3,
                "teaser_word": 2,
                "body_word": 3,
                "supplemental_word": 0,
                "title_char": 2,
                "teaser_char": 2,
                "local_word": 2,
                "structural": 4,
            },
        )
        self.assertLessEqual(len(terms), 18)
        self.assertTrue(report["training_only_vocabulary"])
        self.assertFalse(any("validation_only" in term for term in terms))

    def test_tfidf_v3_uses_point_in_time_aliases_for_local_clauses(self) -> None:
        identity_index = IssuerIdentityIndex(
            (
                IssuerIdentity(
                    ticker="ABC",
                    issuer_id="issuer-1",
                    security_id="security-1",
                    display_name="Alpha Biotech Corporation",
                    aliases=("Alpha Biotech Corporation", "Alpha Biotech"),
                    list_date=date(2020, 1, 1),
                    delisted_date=date(2025, 1, 1),
                ),
            )
        )
        aliases = point_in_time_aliases(
            identity_index,
            ticker="ABC",
            published_at_utc="2024-06-01 12:00:00",
        )
        clauses = issuer_local_clauses(
            "A peer reported results. Alpha Biotech raised guidance. It expects growth.",
            aliases=aliases,
        )
        self.assertIn("alpha biotech", {value.lower() for value in aliases})
        self.assertEqual(
            clauses,
            ("Alpha Biotech raised guidance.", "It expects growth."),
        )
        self.assertEqual(
            anonymize_issuer_mentions("Alpha Biotech raised guidance.", aliases=aliases),
            "<issuer> raised guidance.",
        )
        self.assertEqual(
            point_in_time_aliases(
                identity_index,
                ticker="ABC",
                published_at_utc="2019-06-01 12:00:00",
            ),
            ("ABC",),
        )

    def test_tfidf_v3_structures_numeric_and_economic_relationships(self) -> None:
        features = economic_relation_features(
            "Revenue grew 12% and EPS of 1.20 beat the consensus estimate of 1.00. "
            "The company raised guidance and repaid debt."
        )
        self.assertIn("economic_relation|change:increase:10_to_25", features)
        self.assertIn("economic_relation|comparison:beat", features)
        self.assertIn("economic_relation|numeric_actual_vs_estimate:above", features)
        self.assertIn("economic_relation|guidance:raised", features)
        self.assertIn("economic_relation|financing:debt_reduction", features)

    def test_tfidf_v3_adds_features_without_gold_or_prediction_inputs(self) -> None:
        text = (
            "NEWS\nticker: ABC\npublished_at_utc: 2024-06-01 12:00:00\n"
            "title: Alpha Biotech raises guidance\nteaser: Revenue beat estimates\n"
            "BODY\nAlpha Biotech revenue rose 8%."
        )
        features = tfidf_v3_feature_counts(
            text,
            ticker="ABC",
            aliases=("ABC", "Alpha Biotech"),
        )
        self.assertIn("issuer_clause_word|u:<issuer>", features)
        self.assertNotIn("issuer_clause_word|u:alpha", features)
        self.assertIn("economic_relation|guidance:raised", features)
        self.assertFalse(any("gold" in term or "prediction" in term for term in features))

    def test_tfidf_v3_vocabulary_is_training_only_and_feature_only(self) -> None:
        documents = [
            (
                "ABC",
                "NEWS\nticker: ABC\ntitle: Alpha raises guidance\nBODY\nAlpha revenue beat estimates.",
                ("ABC", "Alpha"),
            )
        ]
        terms, _, report = fit_v3_vocabulary(
            documents,
            min_document_frequency=1,
            budgets={
                "title_word": 2,
                "teaser_word": 0,
                "body_word": 2,
                "supplemental_word": 0,
                "title_char": 0,
                "teaser_char": 0,
                "local_word": 0,
                "structural": 2,
                "issuer_clause_word": 3,
                "issuer_clause_char": 0,
                "economic_relation": 3,
            },
        )
        self.assertTrue(report["training_only_vocabulary"])
        self.assertTrue(report["feature_only_change_from_v2"])
        self.assertFalse(report["supervised_feature_selection"])
        self.assertTrue(any(term.startswith("issuer_clause_word|") for term in terms))

    def test_tfidf_v3_keeps_v2_model_and_training_configuration(self) -> None:
        v2 = vars(_v2_train_args(Path("v2-data"), Path("v2-run"), 8))
        v3 = vars(_v3_train_args(Path("v3-data"), Path("v3-run"), 8))
        v2.pop("data_root")
        v2.pop("run_root")
        v3.pop("data_root")
        v3.pop("run_root")
        self.assertEqual(v2, v3)


if __name__ == "__main__":
    unittest.main()
