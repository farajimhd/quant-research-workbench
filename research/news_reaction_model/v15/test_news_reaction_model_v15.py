from __future__ import annotations

import base64
import datetime as dt
import gc
import json
import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np
import torch

from research.news_reaction_model import v15
from research.news_reaction_model.v15.config import (
    ExperimentConfig,
    LoaderConfig,
    ModelConfig,
)
from research.news_reaction_model.v15.context import (
    CONTEXT_FEATURE_DIM,
    CONTEXT_LOOKBACK_DAYS,
    CONTEXT_SIZE,
    build_context_feature,
    context_contract,
    normalized_context_metadata,
)
from research.news_reaction_model.v15.data import (
    PreparedNewsReactionDataset,
    audit_prepared_dataset,
    deterministic_buffered_batches,
    make_dummy_batch,
    rows_to_batch,
)
from research.news_reaction_model.v15.evaluate import midpoint_proxy_pnl
from research.news_reaction_model.v15.inference import LiveFeatureEncoder
from research.news_reaction_model.v15.losses import compute_loss
from research.news_reaction_model.v15.model import NewsReactionModelV15
from research.news_reaction_model.v15.prepared import (
    close_arrays,
    create_arrays,
    write_json_atomic,
)
from research.news_reaction_model.v15.prepare_data import (
    build_representation_sha256,
    decode_targets,
    month_ranges,
)
from research.news_reaction_model.v15.train import validate_config


def tiny_configs(root: Path | None = None) -> tuple[LoaderConfig, ModelConfig]:
    loader = LoaderConfig(
        prepared_dataset_root=root or Path("unused"),
        openai_embedding_dim=8,
        stock_state_dim=4,
        batch_size=2,
        query_batch_articles=2,
        shuffle_buffer_articles=4,
    )
    model = ModelConfig(
        openai_embedding_dim=8,
        stock_state_dim=4,
        context_size=CONTEXT_SIZE,
        context_feature_dim=CONTEXT_FEATURE_DIM,
        attention_heads=2,
        d_model=8,
        hidden_dim=8,
        layers=1,
        dropout=0.0,
    )
    return loader, model


class NewsReactionModelV15Tests(unittest.TestCase):
    def test_version_preserves_v12_task_and_adds_fixed_context_contract(self) -> None:
        loader = LoaderConfig()
        self.assertEqual(v15.MODEL_VERSION, "v15")
        self.assertEqual(
            v15.HORIZONS,
            (
                "1m",
                "5m",
                "10m",
                "30m",
                "1h",
                "2h",
                "3h",
                "premarket_close",
                "regular_close",
                "extended_close",
            ),
        )
        self.assertEqual(loader.context_size, 4)
        self.assertEqual(loader.context_lookback_days, 7)
        self.assertEqual(loader.context_feature_dim, 49)
        self.assertEqual(
            context_contract()["strict_predecessor"],
            "prior published_at_utc must be strictly less than current published_at_utc",
        )

    def test_context_exposes_only_reactions_available_before_current_news(self) -> None:
        current = dt.datetime(2026, 7, 14, 14, 0, tzinfo=dt.timezone.utc)
        metadata = normalized_context_metadata(
            prior_published_at_utc=current - dt.timedelta(hours=2),
            current_published_at_utc=current,
            prior_publication_session="premarket",
            current_publication_session="regular",
            prior_reaction_session_index=10,
            current_reaction_session_index=10,
        )
        values = np.asarray(
            [[0.01, 0.02, -0.01], [0.03, 0.04, -0.02]],
            dtype=np.float32,
        )
        feature, available = build_context_feature(
            prior_returns=values,
            prior_horizon_codes=("1m", "5m"),
            available_at_by_horizon={
                "1m": current - dt.timedelta(seconds=1),
                "5m": current,
            },
            current_published_at_utc=current,
            metadata=metadata,
        )
        self.assertEqual(feature.shape, (CONTEXT_FEATURE_DIM,))
        self.assertTrue(available[0])
        self.assertFalse(available[1])
        np.testing.assert_allclose(feature[:3], values[0])
        np.testing.assert_allclose(feature[3:6], 0.0)

    def test_same_timestamp_is_not_a_valid_predecessor(self) -> None:
        current = dt.datetime(2026, 7, 14, 14, 0, tzinfo=dt.timezone.utc)
        with self.assertRaisesRegex(ValueError, "strictly earlier"):
            normalized_context_metadata(
                prior_published_at_utc=current,
                current_published_at_utc=current,
                prior_publication_session="regular",
                current_publication_session="regular",
                prior_reaction_session_index=10,
                current_reaction_session_index=10,
            )

    def test_model_uses_context_but_cold_start_is_stable(self) -> None:
        loader, model_config = tiny_configs()
        torch.manual_seed(3)
        model = NewsReactionModelV15(model_config).eval()
        batch = make_dummy_batch(2, loader)
        cold = {key: value.clone() for key, value in batch.x.items()}
        cold["prior_context_mask"].zero_()
        cold["prior_openai_embeddings"].zero_()
        cold["prior_context_features"].zero_()
        with torch.no_grad():
            first = model(cold)
            second = model(cold)
            contextual = model(batch.x)
        self.assertTrue(torch.equal(first.article_embedding, second.article_embedding))
        self.assertFalse(torch.equal(first.article_embedding[1], contextual.article_embedding[1]))
        self.assertTrue(torch.equal(first.article_embedding[0], contextual.article_embedding[0]))

    def test_forward_and_loss_cover_all_v12_horizons(self) -> None:
        loader, model_config = tiny_configs()
        batch = make_dummy_batch(4, loader)
        output = NewsReactionModelV15(model_config)(batch.x)
        self.assertEqual(set(output.logits), set(v15.HORIZONS))
        self.assertTrue(all(value.shape == (4, 3) for value in output.logits.values()))
        self.assertTrue(torch.isfinite(compute_loss(output, batch).loss))

    def test_rows_and_live_encoder_accept_explicit_fixed_context(self) -> None:
        loader, _ = tiny_configs()
        vector = [float(index) for index in range(loader.openai_embedding_dim)]
        prior_embeddings = np.zeros(
            (loader.context_size, loader.openai_embedding_dim), dtype=np.float32
        )
        prior_features = np.zeros(
            (loader.context_size, loader.context_feature_dim), dtype=np.float32
        )
        prior_embeddings[0] = 1.0
        prior_features[0, -1] = 1.0
        row = {
            "source_id": "n1",
            "ticker": "AAPL",
            "published_at_utc": "2026-07-14 14:00:00",
            "publication_session": "regular",
            "openai_embedding_b64": base64.b64encode(
                struct.pack("<8f", *vector)
            ).decode("ascii"),
            "stock_state": [0.1] * loader.stock_state_dim,
            "prior_openai_embeddings": prior_embeddings,
            "prior_context_features": prior_features,
            "prior_context_mask": [True, False, False, False],
        }
        batch = rows_to_batch([row], loader)
        self.assertEqual(batch.x["prior_context_mask"].tolist(), [[True, False, False, False]])
        encoded = LiveFeatureEncoder(loader).encode([row], device=torch.device("cpu"))
        self.assertEqual(set(encoded), set(batch.x))

    def test_prepared_arrays_preserve_indices_and_deterministic_shuffle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            loader, _ = tiny_configs(root)
            rows = 6
            arrays = create_arrays(loader, rows)
            for index in range(rows):
                arrays["openai_embedding"][index, index % loader.openai_embedding_dim] = 1
                arrays["stock_state"][index] = 0.1
                arrays["time_features"][index] = 0.2
                arrays["return_targets"][index] = 0.001
                arrays["label_mask"][index] = True
                arrays["canonical_news_id"][index] = f"n{index}".encode()
                arrays["ticker"][index] = b"AAPL"
                timestamp = f"2025-01-0{index + 1} 12:00:00"
                arrays["published_at_utc"][index] = timestamp.encode()
                arrays["published_at_us"][index] = int(
                    dt.datetime(
                        2025, 1, index + 1, 12, tzinfo=dt.timezone.utc
                    ).timestamp()
                    * 1_000_000
                )
                arrays["publication_session"][index] = b"regular"
                if index:
                    arrays["context_indices"][index, 0] = index - 1
                    arrays["context_mask"][index, 0] = True
            for array in arrays.values():
                array.flush()
            write_json_atomic(
                root / "manifest.json",
                {
                    "status": "complete",
                    "dataset_version": loader.prepared_dataset_version,
                    "rows": rows,
                    "representation_sha256": "test",
                },
            )
            audit = audit_prepared_dataset(loader, "2025-01-01", "2025-02-01")
            self.assertEqual(audit["context_articles"], 5)
            dataset = PreparedNewsReactionDataset(
                loader, start="2025-01-01", end_exclusive="2025-02-01"
            )
            loaded = next(dataset.iter_batches())
            self.assertEqual(loaded.x["prior_context_mask"][1, 0].item(), True)
            first = [
                value
                for batch in deterministic_buffered_batches(
                    loader,
                    start="2025-01-01",
                    end_exclusive="2025-02-01",
                    epoch=1,
                    seed=17,
                )
                for value in batch.identity["canonical_news_id"]
            ]
            second = [
                value
                for batch in deterministic_buffered_batches(
                    loader,
                    start="2025-01-01",
                    end_exclusive="2025-02-01",
                    epoch=1,
                    seed=17,
                )
                for value in batch.identity["canonical_news_id"]
            ]
            self.assertEqual(first, second)
            self.assertCountEqual(first, [f"n{index}" for index in range(rows)])
            dataset.stop()
            close_arrays(arrays)
            del loaded, dataset
            gc.collect()

    def test_target_mapping_and_representation_hash_are_deterministic(self) -> None:
        loader, _ = tiny_configs()
        row = {
            "canonical_news_id": "n1",
            "horizon_codes": ["5m", "1m"],
            "return_targets": [[0.1, 0.2, -0.1], [0.01, 0.02, -0.01]],
        }
        values, mask, codes, source = decode_targets(row, loader)
        self.assertEqual(codes, ("5m", "1m"))
        self.assertTrue(mask[:2].all())
        np.testing.assert_allclose(values[0], source[1])
        first = build_representation_sha256(
            loader,
            source_representation_sha256="source",
            source_rows_count=6,
        )
        second = build_representation_sha256(
            loader,
            source_representation_sha256="source",
            source_rows_count=6,
        )
        self.assertEqual(first, second)

    def test_config_rejects_context_or_attention_drift(self) -> None:
        config = ExperimentConfig()
        validate_config(config)
        config.loader.context_size = CONTEXT_SIZE + 1
        with self.assertRaisesRegex(ValueError, "fixed"):
            validate_config(config)
        config = ExperimentConfig()
        config.model.attention_heads = 5
        with self.assertRaisesRegex(ValueError, "divisible"):
            validate_config(config)

    def test_month_ranges_cover_requested_period_once(self) -> None:
        self.assertEqual(
            month_ranges("2025-12-15", "2026-02-03"),
            [
                (dt.date(2025, 12, 15), dt.date(2026, 1, 1)),
                (dt.date(2026, 1, 1), dt.date(2026, 2, 1)),
                (dt.date(2026, 2, 1), dt.date(2026, 2, 3)),
            ],
        )

    def test_midpoint_proxy_remains_v12_evaluation_contract(self) -> None:
        midpoint, pnl = midpoint_proxy_pnl(
            np.array([1, -1, 0]),
            np.array([0.06, 0.02, 0.10]),
            np.array([-0.02, -0.08, -0.10]),
            np.array([100.0, 50.0, 20.0]),
        )
        np.testing.assert_allclose(midpoint, [0.02, -0.03, 0.0])
        np.testing.assert_allclose(pnl, [2.0, 1.5, 0.0])


if __name__ == "__main__":
    unittest.main()
