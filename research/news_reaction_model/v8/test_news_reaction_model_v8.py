from __future__ import annotations

import base64
import dataclasses
import datetime as dt
import json
import struct
import tempfile
import unittest
from pathlib import Path

import torch

from research.news_reaction_model import v7, v8
from research.news_reaction_model.v7.config import ModelConfig as V7ModelConfig
from research.news_reaction_model.v7.config import TrainConfig as V7TrainConfig
from research.news_reaction_model.v7.ranges import RANGE_SPECS as V7_RANGE_SPECS
from research.news_reaction_model.v7.stock_state import contract_payload as v7_stock_state_contract
from research.news_reaction_model.v8.config import (
    OPENAI_EMBEDDING_DIM,
    OPENAI_EMBEDDING_MODEL,
    OPENAI_EMBEDDING_VERSION,
    OPENAI_TEXT_CONTRACT,
    LoaderConfig,
    ModelConfig,
    TrainConfig,
)
from research.news_reaction_model.v8.data import (
    make_dummy_batch,
    prepared_batch_sql,
    prepared_dataset_audit_sql,
    rows_to_batch,
)
from research.news_reaction_model.v8.evaluate import evaluation_batch_sql
from research.news_reaction_model.v8.inference import LiveFeatureEncoder, trade_plans
from research.news_reaction_model.v8.losses import compute_loss
from research.news_reaction_model.v8.model import NewsReactionModelV8
from research.news_reaction_model.v8.prepare_data import (
    build_v8_manifest,
    create_table_sql,
    load_or_create_v8_manifest,
    population_audit_sql,
)
from research.news_reaction_model.v8.ranges import RANGE_SPECS
from research.news_reaction_model.v8.stock_state import contract_payload as v8_stock_state_contract


class NewsReactionModelV8Tests(unittest.TestCase):
    def test_version_and_temporal_contract_are_copied_from_v7(self) -> None:
        self.assertEqual(v8.MODEL_VERSION, "v8")
        self.assertEqual(v8.HORIZONS, v7.HORIZONS)
        self.assertEqual(v8.SESSIONS, v7.SESSIONS)
        self.assertEqual(
            {key: dataclasses.asdict(value) for key, value in RANGE_SPECS.items()},
            {key: dataclasses.asdict(value) for key, value in V7_RANGE_SPECS.items()},
        )

    def test_training_defaults_are_unchanged_from_v7(self) -> None:
        v7_config = dataclasses.asdict(V7TrainConfig())
        v8_config = dataclasses.asdict(TrainConfig())
        for key in (
            "epochs", "max_samples", "learning_rate", "weight_decay", "grad_clip_norm",
            "ordinal_loss_weight", "scheduler", "scheduler_restarts", "scheduler_eta_min",
            "amp", "amp_dtype", "compile_model", "logging_samples", "validation_samples",
            "validation_max_batches", "checkpoint_latest_samples", "checkpoint_archive_samples",
            "evaluate_at_end", "wandb_project", "wandb_entity", "wandb_mode",
            "wandb_init_timeout", "seed",
        ):
            self.assertEqual(v8_config[key], v7_config[key], key)

    def test_model_and_head_defaults_are_unchanged_except_text_input(self) -> None:
        v7_config = V7ModelConfig()
        v8_config = ModelConfig()
        for key in ("stock_state_dim", "d_model", "hidden_dim", "layers", "dropout", "horizon_dim", "horizons"):
            self.assertEqual(getattr(v8_config, key), getattr(v7_config, key), key)
        model = NewsReactionModelV8(v8_config)
        self.assertEqual(model.chunk_position.num_embeddings, 2)
        for horizon in v8.HORIZONS:
            for target in ("ending", "high", "low"):
                self.assertEqual(
                    model.range_heads[horizon][target].out_features,
                    V7_RANGE_SPECS[horizon].classes,
                )

    def test_stock_state_contract_is_byte_for_byte_v7(self) -> None:
        self.assertEqual(v8_stock_state_contract(), v7_stock_state_contract())

    def test_openai_authority_is_explicit(self) -> None:
        config = LoaderConfig()
        self.assertEqual(config.openai_embedding_dim, 3072)
        self.assertEqual(config.embedding_version, OPENAI_EMBEDDING_VERSION)
        self.assertEqual(config.embedding_model, OPENAI_EMBEDDING_MODEL)
        self.assertEqual(config.embedding_text_contract, OPENAI_TEXT_CONTRACT)
        self.assertEqual(OPENAI_EMBEDDING_DIM, 3072)

    def test_batch_contains_only_openai_text_and_stock_state_channels(self) -> None:
        config = LoaderConfig(openai_embedding_dim=8, stock_state_dim=4)
        batch = make_dummy_batch(3, config)
        self.assertEqual(set(batch.x), {"openai_embedding", "stock_state", "channel_mask"})
        self.assertEqual(tuple(batch.x["openai_embedding"].shape), (3, 8))
        self.assertEqual(tuple(batch.x["stock_state"].shape), (3, 4))
        self.assertEqual(tuple(batch.x["channel_mask"].shape), (3, 2))

    def test_batch_rejects_wrong_embedding_dimension(self) -> None:
        config = LoaderConfig(openai_embedding_dim=8, stock_state_dim=4)
        row = {
            "source_id": "n1",
            "ticker": "AAPL",
            "published_at_utc": "2026-01-01 12:00:00",
            "openai_embedding": [0.1] * 7,
            "stock_state": [0.1] * 4,
            "horizon_codes": [],
            "return_targets": [],
        }
        with self.assertRaisesRegex(ValueError, "OpenAI embedding has shape"):
            rows_to_batch([row], config)

    def test_batch_decodes_lossless_float32_base64_transport(self) -> None:
        config = LoaderConfig(openai_embedding_dim=4, stock_state_dim=2)
        values = (0.125, -0.5, 1.25, 3.0)
        row = {
            "source_id": "n1",
            "ticker": "AAPL",
            "published_at_utc": "2026-01-01 12:00:00",
            "openai_embedding_b64": base64.b64encode(struct.pack("<4f", *values)).decode("ascii"),
            "stock_state": [0.1, 0.2],
            "horizon_codes": [],
            "return_targets": [],
        }
        batch = rows_to_batch([row], config)
        self.assertTrue(torch.allclose(batch.x["openai_embedding"][0], torch.tensor(values)))

    def test_batch_rejects_short_binary_transport_with_identity(self) -> None:
        config = LoaderConfig(openai_embedding_dim=4, stock_state_dim=2)
        row = {
            "source_id": "news-123",
            "ticker": "ZERO",
            "published_at_utc": "2026-01-01 12:00:00",
            "openai_embedding_b64": base64.b64encode(struct.pack("<3f", 0.0, 1.0, 2.0)).decode("ascii"),
            "stock_state": [0.1, 0.2],
            "horizon_codes": [],
            "return_targets": [],
        }
        with self.assertRaisesRegex(ValueError, "12 bytes instead of 16.*news-123 / ZERO"):
            rows_to_batch([row], config)

    def test_model_forward_and_loss_preserve_v7_output_contract(self) -> None:
        loader = LoaderConfig(openai_embedding_dim=16, stock_state_dim=8)
        model = NewsReactionModelV8(ModelConfig(
            openai_embedding_dim=16,
            stock_state_dim=8,
            d_model=16,
            hidden_dim=16,
            layers=1,
        ))
        batch = make_dummy_batch(4, loader)
        output = model(batch.x)
        self.assertEqual(tuple(output.article_embedding.shape), (4, 16))
        self.assertEqual(set(output.logits), set(v8.HORIZONS))
        self.assertTrue(torch.isfinite(compute_loss(output, batch).loss))
        plans = trade_plans(output)
        self.assertEqual(set(plans), set(v8.HORIZONS))

    def test_prepared_queries_do_not_request_tfidf_fields(self) -> None:
        config = LoaderConfig()
        batch_sql = prepared_batch_sql(
            config, dt.date(2026, 1, 1), dt.date(2026, 2, 1), "1970-01-01", "", "", 10
        )
        audit_sql = prepared_dataset_audit_sql(config, "2026-01-01", "2027-01-01")
        evaluation_sql = evaluation_batch_sql(
            config, dt.date(2026, 1, 1), dt.date(2026, 2, 1), "1970-01-01", "", "", 10
        )
        for sql in (batch_sql, evaluation_sql):
            self.assertIn("openai_embedding_b64", sql)
            self.assertIn("reinterpretAsString", sql)
            self.assertIn("rightPad", sql)
            self.assertIn("char(0)", sql)
            self.assertIn("stock_state", sql)
            self.assertNotIn("word_ids", sql)
            self.assertNotIn("char_ids", sql)
            self.assertNotIn("numeric_ids", sql)
        self.assertIn("length(openai_embedding) != 3072", audit_sql)

    def test_materialized_schema_contains_only_ablation_features(self) -> None:
        ddl = create_table_sql(LoaderConfig())
        self.assertIn("openai_embedding Array(Float32)", ddl)
        self.assertIn("stock_state Array(Float32)", ddl)
        self.assertIn("embedding_text_sha256 FixedString(64)", ddl)
        self.assertNotIn("word_ids", ddl)
        self.assertNotIn("numeric_dense", ddl)

    def test_population_preflight_joins_exact_identity_and_validates_contract(self) -> None:
        sql = population_audit_sql(LoaderConfig(), "2019-01-01", "2027-01-01")
        self.assertIn("USING (canonical_news_id, ticker, published_at_utc)", sql)
        self.assertIn(OPENAI_EMBEDDING_VERSION, sql)
        self.assertIn(OPENAI_EMBEDDING_MODEL, sql)
        self.assertIn(OPENAI_TEXT_CONTRACT, sql)
        self.assertIn("countIf(length(e.embedding) > 0) AS matched_embeddings", sql)

    def test_manifest_is_stable_and_records_only_the_ablation(self) -> None:
        config = LoaderConfig()
        expected = build_v8_manifest(config, "a" * 64)
        self.assertEqual(expected["source_v7_representation_sha256"], "a" * 64)
        self.assertEqual(
            expected["stock_state_contract"],
            json.loads(json.dumps(v7_stock_state_contract(), default=list)),
        )
        self.assertEqual(len(expected["stock_state_contract_sha256"]), 64)
        self.assertEqual(len(expected["range_contract_sha256"]), 64)
        self.assertEqual(
            expected["targets_heads_losses_and_training_contract"],
            "unchanged_from_v7",
        )
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "manifest.json"
            self.assertEqual(load_or_create_v8_manifest(path, expected), expected)
            self.assertEqual(load_or_create_v8_manifest(path, build_v8_manifest(config, "a" * 64)), expected)

    def test_live_encoder_requires_both_authoritative_inputs(self) -> None:
        config = LoaderConfig(openai_embedding_dim=8, stock_state_dim=85)
        encoder = LiveFeatureEncoder(config)
        transformed = encoder.transform([{
            "canonical_news_id": "n1",
            "ticker": "AAPL",
            "published_at_utc": "2026-01-01 12:00:00",
            "openai_embedding": [0.1] * 8,
            "stock_state": [0.2] * 85,
        }])
        self.assertEqual(len(transformed[0]["openai_embedding"]), 8)
        with self.assertRaisesRegex(ValueError, "OpenAI embedding"):
            encoder.transform([{
                "canonical_news_id": "n1",
                "ticker": "AAPL",
                "published_at_utc": "2026-01-01 12:00:00",
                "stock_state": [0.2] * 85,
            }])


if __name__ == "__main__":
    unittest.main()
