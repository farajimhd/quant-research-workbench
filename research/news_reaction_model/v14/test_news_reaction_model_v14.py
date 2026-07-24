from __future__ import annotations

import unittest
from dataclasses import replace

import torch

from research.news_reaction_model import v14
from research.news_reaction_model.v14.config import (
    ExperimentConfig,
    LoaderConfig,
    ModelConfig,
    default_run_name,
)
from research.news_reaction_model.v14.data import (
    _pack_top_weight_rows,
    make_dummy_batch,
    prepared_batch_sql,
    prepared_dataset_audit_sql,
    rows_to_batch,
)
from research.news_reaction_model.v14.evaluate import evaluation_batch_sql
from research.news_reaction_model.v14.losses import compute_loss
from research.news_reaction_model.v14.model import NewsReactionModelV14
from research.news_reaction_model.v14.opportunity import (
    OPPORTUNITY_CLASS_NAMES,
    opportunity_targets,
)
from research.news_reaction_model.v14.train import build_config, parse_args, validate_config


def small_loader(**changes: object) -> LoaderConfig:
    base = LoaderConfig(
        batch_size=4,
        query_batch_articles=4,
        shuffle_buffer_articles=4,
        word_vocab_size=32,
        char_vocab_size=32,
        numeric_vocab_size=16,
        numeric_dense_dim=6,
        stock_state_dim=8,
        max_word_tokens=4,
        max_char_tokens=3,
        max_numeric_tokens=2,
    )
    return replace(base, **changes)


def small_model(loader: LoaderConfig, **changes: object) -> ModelConfig:
    base = ModelConfig(
        word_vocab_size=loader.word_vocab_size,
        char_vocab_size=loader.char_vocab_size,
        numeric_vocab_size=loader.numeric_vocab_size,
        numeric_dense_dim=loader.numeric_dense_dim,
        stock_state_dim=loader.stock_state_dim,
        time_feature_dim=loader.time_feature_dim,
        d_model=24,
        hidden_dim=24,
        layers=1,
        attention_heads=4,
    )
    return replace(base, **changes)


class NewsReactionModelV14Tests(unittest.TestCase):
    def test_version_and_source_contract(self) -> None:
        loader = LoaderConfig()
        self.assertEqual(v14.MODEL_VERSION, "v14")
        self.assertEqual(loader.dataset_table, "news_reaction_stock_state_dataset_v7")
        self.assertEqual(
            loader.representation_name,
            "v6_tfidf_numeric_plus_point_in_time_stock_state_v1",
        )

    def test_top_weight_selection_is_bounded_and_deterministic(self) -> None:
        rows = [{
            "word_ids": [9, 1, 4, 7, 3],
            "word_weights": [0.5, 0.9, -0.9, 0.8, 0.9],
        }]
        ids, weights, mask = _pack_top_weight_rows(rows, "word", 3, 16)
        self.assertEqual(ids.tolist(), [[1, 3, 4]])
        self.assertEqual(mask.tolist(), [[True, True, True]])
        self.assertTrue(torch.allclose(weights, torch.tensor([[0.9, 0.9, -0.9]])))

    def test_top_weight_selection_uses_vocab_id_as_padding(self) -> None:
        ids, weights, mask = _pack_top_weight_rows(
            [{"char_ids": [2], "char_weights": [0.4]}], "char", 3, 8
        )
        self.assertEqual(ids.tolist(), [[2, 8, 8]])
        self.assertEqual(weights.tolist(), [[0.4000000059604645, 0.0, 0.0]])
        self.assertEqual(mask.tolist(), [[True, False, False]])

    def test_rows_to_batch_produces_fixed_token_sets_and_time(self) -> None:
        loader = small_loader()
        batch = make_dummy_batch(3, loader)
        self.assertEqual(tuple(batch.x["word_ids"].shape), (3, 4))
        self.assertEqual(tuple(batch.x["char_ids"].shape), (3, 3))
        self.assertEqual(tuple(batch.x["numeric_ids"].shape), (3, 2))
        self.assertEqual(tuple(batch.x["numeric_dense"].shape), (3, 6))
        self.assertEqual(tuple(batch.x["stock_state"].shape), (3, 8))
        self.assertEqual(tuple(batch.x["time_features"].shape), (3, loader.time_feature_dim))
        self.assertIn("publication_session", batch.identity)

    def test_model_has_actual_sparse_feature_tokens_and_horizon_queries(self) -> None:
        loader = small_loader()
        model = NewsReactionModelV14(small_model(loader))
        self.assertEqual(model.word_embedding.num_embeddings, loader.word_vocab_size + 1)
        self.assertEqual(model.char_embedding.num_embeddings, loader.char_vocab_size + 1)
        self.assertEqual(model.numeric_embedding.num_embeddings, loader.numeric_vocab_size + 1)
        self.assertEqual(model.token_attention.attention.num_heads, 4)
        self.assertEqual(model.horizon_queries.num_embeddings, len(v14.HORIZONS))

    def test_forward_and_v10_opportunity_loss_contract(self) -> None:
        loader = small_loader()
        batch = make_dummy_batch(4, loader)
        model = NewsReactionModelV14(small_model(loader))
        output = model(batch.x)
        self.assertEqual(set(output.logits), set(v14.HORIZONS))
        for logits in output.logits.values():
            self.assertEqual(tuple(logits.shape), (4, len(OPPORTUNITY_CLASS_NAMES)))
        result = compute_loss(output, batch)
        self.assertTrue(torch.isfinite(result.loss))
        self.assertGreater(sum(result.horizon_counts.values()), 0)

    def test_masked_sparse_padding_cannot_change_predictions(self) -> None:
        loader = small_loader()
        batch = make_dummy_batch(2, loader)
        model = NewsReactionModelV14(small_model(loader)).eval()
        changed = {key: value.clone() for key, value in batch.x.items()}
        changed["word_ids"][~changed["word_mask"]] = 5
        changed["word_weights"][~changed["word_mask"]] = 100.0
        with torch.no_grad():
            first = model(batch.x)
            second = model(changed)
        for horizon in v14.HORIZONS:
            self.assertTrue(torch.equal(first.logits[horizon], second.logits[horizon]))

    def test_sparse_token_order_has_no_positional_effect(self) -> None:
        loader = small_loader()
        batch = make_dummy_batch(2, loader)
        model = NewsReactionModelV14(small_model(loader)).eval()
        changed = {key: value.clone() for key, value in batch.x.items()}
        for prefix in ("word", "char", "numeric"):
            changed[f"{prefix}_ids"] = torch.flip(changed[f"{prefix}_ids"], dims=(1,))
            changed[f"{prefix}_weights"] = torch.flip(changed[f"{prefix}_weights"], dims=(1,))
            changed[f"{prefix}_mask"] = torch.flip(changed[f"{prefix}_mask"], dims=(1,))
        with torch.no_grad():
            first = model(batch.x)
            second = model(changed)
        for horizon in v14.HORIZONS:
            self.assertTrue(torch.allclose(first.logits[horizon], second.logits[horizon], atol=1e-6))

    def test_sql_reads_v7_sparse_features_not_openai_embeddings(self) -> None:
        loader = LoaderConfig()
        sql = prepared_batch_sql(loader, __import__("datetime").date(2026, 1, 1), __import__("datetime").date(2026, 2, 1), "1970-01-01", "", "", 10)
        self.assertIn("word_ids, word_weights, char_ids, char_weights", sql)
        self.assertIn("numeric_ids, numeric_weights, numeric_dense", sql)
        self.assertNotIn("openai_embedding", sql)
        audit = prepared_dataset_audit_sql(loader, "2019-01-01", "2027-01-01")
        self.assertIn("length(word_ids) != length(word_weights)", audit)
        self.assertIn("publication_session NOT IN", audit)

    def test_evaluation_uses_same_sparse_source_contract(self) -> None:
        loader = LoaderConfig()
        sql = evaluation_batch_sql(
            loader,
            __import__("datetime").date(2026, 1, 1),
            __import__("datetime").date(2026, 2, 1),
            "1970-01-01",
            "",
            "",
            10,
        )
        self.assertIn("p.word_ids, p.word_weights", sql)
        self.assertIn("p.numeric_ids, p.numeric_weights", sql)
        self.assertNotIn("openai_embedding", sql)

    def test_training_defaults_preserve_corrected_v10_schedule(self) -> None:
        args = parse_args([])
        config = build_config(args)
        self.assertEqual(config.train.epochs, 50)
        self.assertEqual(config.train.scheduler_restarts, 49)
        self.assertEqual(config.train.scheduler_cycle_decay, 0.98)
        self.assertEqual(config.loader.shuffle_buffer_articles, 32_768)
        self.assertEqual(config.train.wandb_project, "news-reaction-model-v3")
        validate_config(config)

    def test_attention_width_must_be_divisible_by_heads(self) -> None:
        args = parse_args(["--d-model", "25", "--attention-heads", "4"])
        with self.assertRaisesRegex(ValueError, "divisible"):
            validate_config(build_config(args))

    def test_default_run_name_exposes_token_budget(self) -> None:
        config = ExperimentConfig(loader=LoaderConfig(), model=ModelConfig())
        name = default_run_name(config)
        self.assertIn("tfidf-token-transformer", name)
        self.assertIn("-w256-c512-n64-", name)

    def test_three_class_targets_are_unchanged(self) -> None:
        returns = torch.zeros((1, len(v14.HORIZONS), 3), dtype=torch.float32)
        returns[:, :, 1] = 0.02
        returns[:, :, 2] = -0.005
        mask = torch.ones((1, len(v14.HORIZONS)), dtype=torch.bool)
        targets = opportunity_targets(returns, mask)
        self.assertTrue(all(value.tolist() == [1] for value in targets.values()))

    def test_missing_sparse_channels_still_use_dense_causal_tokens(self) -> None:
        loader = small_loader()
        rows = [{
            "source_id": "x",
            "ticker": "AAPL",
            "published_at_utc": "2025-01-02 15:00:00",
            "publication_session": "regular",
            "word_ids": [],
            "word_weights": [],
            "char_ids": [],
            "char_weights": [],
            "numeric_ids": [],
            "numeric_weights": [],
            "numeric_dense": [0.0] * loader.numeric_dense_dim,
            "stock_state": [0.0] * loader.stock_state_dim,
            "horizon_codes": ["1m"],
            "return_targets": [[0.0, 0.001, -0.001]],
        }]
        batch = rows_to_batch(rows, loader)
        output = NewsReactionModelV14(small_model(loader))(batch.x)
        self.assertTrue(torch.isfinite(output.logits["1m"]).all())


if __name__ == "__main__":
    unittest.main()
