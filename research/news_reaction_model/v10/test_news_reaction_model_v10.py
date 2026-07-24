from __future__ import annotations

import base64
import dataclasses
import datetime as dt
import random
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import torch

from research.mlops.checkpoints import AsyncCheckpointManager, CheckpointPolicy
from research.news_reaction_model import v9, v10
from research.news_reaction_model.v9.config import ModelConfig as V9ModelConfig
from research.news_reaction_model.v9.config import TrainConfig as V9TrainConfig
from research.news_reaction_model.v10.config import (
    ExperimentConfig,
    LoaderConfig,
    ModelConfig,
    TrainConfig,
)
from research.news_reaction_model.v10.data import (
    deterministic_buffered_batches,
    make_dummy_batch,
    prepared_batch_sql,
    prepared_dataset_audit_sql,
    rows_to_batch,
)
from research.news_reaction_model.v10.evaluate import (
    OpportunityLedger,
    evaluation_batch_sql,
    midpoint_proxy_pnl,
)
from research.news_reaction_model.v10.fit_diagnostic import fit_comparison
from research.news_reaction_model.v10.inference import (
    LiveFeatureEncoder,
    opportunity_predictions,
)
from research.news_reaction_model.v10.losses import compute_loss
from research.news_reaction_model.v10.metrics import TrainingLossAccumulator
from research.news_reaction_model.v10.model import (
    NewsReactionModelV10,
    NewsReactionOpportunityOutput,
)
from research.news_reaction_model.v10.memorization_test import (
    deterministic_subset_sql,
    slice_batch,
)
from research.news_reaction_model.v10.opportunity import (
    OPPORTUNITY_CLASSES,
    OPPORTUNITY_CLASS_NAMES,
    OPPORTUNITY_SPECS,
    OpportunityClass,
    opportunity_contract,
    opportunity_targets,
)
from research.news_reaction_model.v10.time_features import (
    TIME_FEATURE_DIM,
    encode_time_features,
)
from research.news_reaction_model.v10.train import (
    SampleCosineRestartScheduler,
    TrainingCursor,
    capture_rng_state,
    checkpoint_payload,
    restore,
    restore_rng_state,
    validate_config,
)


class NewsReactionModelV10Tests(unittest.TestCase):
    def test_v10_retains_v9_horizons_and_prepared_dataset(self) -> None:
        loader = LoaderConfig()
        self.assertEqual(v10.MODEL_VERSION, "v10")
        self.assertEqual(v10.HORIZONS, v9.HORIZONS)
        self.assertEqual(loader.dataset_table, "news_reaction_openai_stock_state_dataset_v8")
        self.assertEqual(loader.dataset_version, "news_reaction_openai_stock_state_dataset_v8")

    def test_corrected_v10_preserves_v9_capacity_while_adding_time_channel(self) -> None:
        v9_model = dataclasses.asdict(V9ModelConfig())
        v10_model = dataclasses.asdict(ModelConfig())
        self.assertEqual(v10_model.pop("time_feature_dim"), TIME_FEATURE_DIM)
        self.assertEqual(v10_model, v9_model)
        v9_train = dataclasses.asdict(V9TrainConfig())
        v10_train = dataclasses.asdict(TrainConfig())
        for key in (
            "epochs",
            "max_samples",
            "learning_rate",
            "weight_decay",
            "grad_clip_norm",
            "scheduler",
            "scheduler_restarts",
            "scheduler_eta_min",
            "amp",
            "amp_dtype",
            "compile_model",
            "logging_samples",
            "validation_samples",
            "validation_max_batches",
            "checkpoint_latest_samples",
            "checkpoint_archive_samples",
            "evaluate_at_end",
            "wandb_project",
            "wandb_entity",
            "wandb_mode",
            "wandb_init_timeout",
            "seed",
        ):
            self.assertEqual(v10_train[key], v9_train[key], key)
        self.assertNotIn("ordinal_loss_weight", v10_train)

    def test_explicit_long_run_accepts_50_epochs_but_not_more(self) -> None:
        config = ExperimentConfig()
        config.train.epochs = 50
        config.train.scheduler = "none"
        validate_config(config)
        config.train.epochs = 51
        with self.assertRaisesRegex(ValueError, "between 1 and 50"):
            validate_config(config)

    def test_epoch_restart_scheduler_decays_each_cycle_peak(self) -> None:
        parameter = torch.nn.Parameter(torch.zeros(()))
        optimizer = torch.optim.AdamW([parameter], lr=3e-4)
        train = TrainConfig(
            epochs=50,
            scheduler="cosine",
            scheduler_restarts=49,
            scheduler_eta_min=1e-6,
            scheduler_cycle_decay=0.98,
        )
        scheduler = SampleCosineRestartScheduler(
            optimizer,
            train,
            planned_samples=50_000,
        )
        self.assertEqual(scheduler.cycle, 1_000)
        scheduler.step(1_000)
        self.assertAlmostEqual(optimizer.param_groups[0]["lr"], 3e-4 * 0.98)
        scheduler.step(49_000)
        self.assertAlmostEqual(
            optimizer.param_groups[0]["lr"],
            3e-4 * (0.98 ** 49),
        )
        state = scheduler.state_dict()
        self.assertEqual(state["cycle_decay"], 0.98)

    def test_scheduler_cycle_decay_validation(self) -> None:
        config = ExperimentConfig()
        config.train.scheduler_cycle_decay = 0.0
        with self.assertRaisesRegex(ValueError, "cycle-decay"):
            validate_config(config)

    def test_opportunity_contract_has_exactly_three_classes(self) -> None:
        self.assertEqual(OPPORTUNITY_CLASSES, 3)
        self.assertEqual(
            OPPORTUNITY_CLASS_NAMES,
            (
                "no_meaningful_opportunity",
                "upside_dominant",
                "downside_dominant",
            ),
        )
        self.assertEqual(set(opportunity_contract()["rules"]), set(v10.HORIZONS))

    def test_opportunity_spec_assigns_two_sided_moves_by_larger_excursion(self) -> None:
        spec = OPPORTUNITY_SPECS["1m"]
        self.assertEqual(
            spec.classify(0.0004, -0.0004),
            int(OpportunityClass.NO_MEANINGFUL_OPPORTUNITY),
        )
        self.assertEqual(spec.classify(0.0100, -0.0020), int(OpportunityClass.UPSIDE_DOMINANT))
        self.assertEqual(spec.classify(0.0020, -0.0100), int(OpportunityClass.DOWNSIDE_DOMINANT))
        self.assertEqual(spec.classify(0.0100, -0.0090), int(OpportunityClass.UPSIDE_DOMINANT))
        self.assertEqual(spec.classify(0.0090, -0.0100), int(OpportunityClass.DOWNSIDE_DOMINANT))
        self.assertEqual(
            spec.classify(0.0100, -0.0100),
            int(OpportunityClass.NO_MEANINGFUL_OPPORTUNITY),
        )

    def test_tensor_targets_match_scalar_contract(self) -> None:
        returns = torch.zeros((5, len(v10.HORIZONS), 3), dtype=torch.float32)
        returns[0, :, 1:] = torch.tensor([0.0004, -0.0004])
        returns[1, :, 1:] = torch.tensor([0.0100, -0.0020])
        returns[2, :, 1:] = torch.tensor([0.0020, -0.0100])
        returns[3, :, 1:] = torch.tensor([0.0100, -0.0090])
        returns[4, :, 1:] = torch.tensor([0.0090, -0.0100])
        mask = torch.ones((5, len(v10.HORIZONS)), dtype=torch.bool)
        targets = opportunity_targets(returns, mask)
        self.assertEqual(
            targets["1m"].tolist(),
            [
                int(OpportunityClass.NO_MEANINGFUL_OPPORTUNITY),
                int(OpportunityClass.UPSIDE_DOMINANT),
                int(OpportunityClass.DOWNSIDE_DOMINANT),
                int(OpportunityClass.UPSIDE_DOMINANT),
                int(OpportunityClass.DOWNSIDE_DOMINANT),
            ],
        )

    def test_invalid_labels_are_masked(self) -> None:
        returns = torch.zeros((2, len(v10.HORIZONS), 3), dtype=torch.float32)
        mask = torch.ones((2, len(v10.HORIZONS)), dtype=torch.bool)
        mask[0, 0] = False
        returns[1, 0, 1] = float("nan")
        self.assertEqual(opportunity_targets(returns, mask)["1m"].tolist(), [-1, -1])

    def test_model_has_only_one_opportunity_head_per_horizon(self) -> None:
        model = NewsReactionModelV10(
            ModelConfig(
                openai_embedding_dim=16,
                stock_state_dim=8,
                d_model=16,
                hidden_dim=16,
                layers=1,
            )
        )
        self.assertFalse(hasattr(model, "range_heads"))
        self.assertEqual(set(model.opportunity_heads), set(v10.HORIZONS))
        for head in model.opportunity_heads.values():
            self.assertEqual(head.out_features, 3)

    def test_forward_and_loss_use_one_label_per_horizon(self) -> None:
        loader = LoaderConfig(openai_embedding_dim=16, stock_state_dim=8)
        model = NewsReactionModelV10(
            ModelConfig(
                openai_embedding_dim=16,
                stock_state_dim=8,
                d_model=16,
                hidden_dim=16,
                layers=1,
            )
        )
        batch = make_dummy_batch(4, loader)
        output = model(batch.x)
        self.assertEqual(tuple(output.article_embedding.shape), (4, 16))
        self.assertEqual(set(output.logits), set(v10.HORIZONS))
        self.assertTrue(all(tuple(logits.shape) == (4, 3) for logits in output.logits.values()))
        result = compute_loss(output, batch)
        self.assertTrue(torch.isfinite(result.loss))
        self.assertEqual(result.metrics["train/valid_labels"], 4 * len(v10.HORIZONS))

    def test_inference_opens_only_dominant_direction_positions(self) -> None:
        classes = torch.tensor(
            [
                int(OpportunityClass.NO_MEANINGFUL_OPPORTUNITY),
                int(OpportunityClass.UPSIDE_DOMINANT),
                int(OpportunityClass.DOWNSIDE_DOMINANT),
            ]
        )
        logits = torch.full((3, 3), -5.0)
        logits[torch.arange(3), classes] = 5.0
        output = NewsReactionOpportunityOutput(
            logits={horizon: logits.clone() for horizon in v10.HORIZONS},
            article_embedding=torch.zeros((3, 8)),
            profile={},
        )
        plan = opportunity_predictions(output)["1m"]
        self.assertEqual(plan["position"].tolist(), [0, 1, -1])
        self.assertEqual(plan["class"].tolist(), classes.tolist())

    def test_midpoint_pnl_proxy_is_signed_by_position(self) -> None:
        midpoint, pnl = midpoint_proxy_pnl(
            np.array([1, -1, 0]),
            np.array([0.06, 0.02, 0.10]),
            np.array([-0.02, -0.08, -0.10]),
            np.array([100.0, 50.0, 20.0]),
        )
        np.testing.assert_allclose(midpoint, [0.02, -0.03, 0.0])
        np.testing.assert_allclose(pnl, [2.0, 1.5, 0.0])

    def test_ledger_reports_three_class_quality_and_side_pnl(self) -> None:
        ledger = OpportunityLedger()
        ledger.add(
            predicted_class=np.array([1, 2, 0]),
            actual_class=np.array([1, 2, 0]),
            position=np.array([1, -1, 0]),
            pnl=np.array([2.0, -1.0, 0.0]),
        )
        summary = ledger.summary()
        self.assertEqual(summary["labels"], 3)
        self.assertEqual(summary["active"], 2)
        self.assertEqual(summary["abstained"], 1)
        self.assertEqual(summary["accuracy"], 1.0)
        self.assertEqual(summary["macro_f1"], 1.0)
        self.assertAlmostEqual(summary["one_share_pnl"], 1.0)

    def test_batch_decodes_lossless_openai_transport(self) -> None:
        config = LoaderConfig(openai_embedding_dim=4, stock_state_dim=2)
        values = (0.125, -0.5, 1.25, 3.0)
        row = {
            "source_id": "n1",
            "ticker": "AAPL",
            "published_at_utc": "2026-01-01 12:00:00",
            "publication_session": "premarket",
            "openai_embedding_b64": base64.b64encode(struct.pack("<4f", *values)).decode("ascii"),
            "stock_state": [0.1, 0.2],
            "horizon_codes": [],
            "return_targets": [],
        }
        batch = rows_to_batch([row], config)
        self.assertTrue(torch.allclose(batch.x["openai_embedding"][0], torch.tensor(values)))

    def test_live_encoder_requires_and_encodes_causal_publication_time(self) -> None:
        encoder = LiveFeatureEncoder(LoaderConfig(openai_embedding_dim=4, stock_state_dim=2))
        encoded = encoder.encode(
            [{
                "openai_embedding": [1, 2, 3, 4],
                "stock_state": [0.1, 0.2],
                "published_at_utc": "2026-01-01 14:31:00",
                "publication_session": "regular",
            }],
            device=torch.device("cpu"),
        )
        self.assertEqual(
            set(encoded),
            {"openai_embedding", "stock_state", "time_features", "channel_mask"},
        )
        self.assertEqual(tuple(encoded["time_features"].shape), (1, TIME_FEATURE_DIM))
        self.assertEqual(encoded["channel_mask"].tolist(), [[True, True, True]])
        with self.assertRaisesRegex(ValueError, "4-value"):
            encoder.encode(
                [{
                    "openai_embedding": [1, 2],
                    "stock_state": [0.1, 0.2],
                    "published_at_utc": "2026-01-01 14:31:00",
                    "publication_session": "regular",
                }],
                device=torch.device("cpu"),
            )

    def test_time_features_are_exchange_local_and_session_explicit(self) -> None:
        premarket = encode_time_features("2026-07-14 13:00:00", "premarket")
        regular = encode_time_features("2026-07-14 14:00:00", "regular")
        self.assertEqual(len(premarket), TIME_FEATURE_DIM)
        self.assertEqual(premarket[:4], [1.0, 0.0, 0.0, 0.0])
        self.assertEqual(regular[:4], [0.0, 1.0, 0.0, 0.0])
        self.assertNotEqual(premarket[4:], regular[4:])

    def test_loss_weights_each_horizon_equally_not_each_label(self) -> None:
        loader = LoaderConfig(openai_embedding_dim=4, stock_state_dim=2)
        batch = make_dummy_batch(4, loader)
        batch.label_mask.zero_()
        batch.label_mask[:, 0] = True
        batch.label_mask[0, 1] = True
        logits = {
            horizon: torch.zeros((4, 3), dtype=torch.float32)
            for horizon in v10.HORIZONS
        }
        logits[v10.HORIZONS[0]][:, 1] = 3.0
        logits[v10.HORIZONS[1]][0, 1] = -3.0
        result = compute_loss(
            NewsReactionOpportunityOutput(
                logits=logits,
                article_embedding=torch.zeros((4, 8)),
                profile={},
            ),
            batch,
        )
        targets = opportunity_targets(batch.return_targets, batch.label_mask)
        expected = torch.stack([
            torch.nn.functional.cross_entropy(
                logits[v10.HORIZONS[0]], targets[v10.HORIZONS[0]]
            ),
            torch.nn.functional.cross_entropy(
                logits[v10.HORIZONS[1]][:1], targets[v10.HORIZONS[1]][:1]
            ),
        ]).mean()
        self.assertTrue(torch.allclose(result.loss, expected))
        self.assertNotAlmostEqual(
            result.metrics["train/loss"],
            result.metrics["train/micro_log_loss"],
        )

    def test_training_accumulator_is_invariant_to_batch_partitioning(self) -> None:
        loader = LoaderConfig(openai_embedding_dim=4, stock_state_dim=2)
        batch = make_dummy_batch(6, loader)
        model = NewsReactionModelV10(
            ModelConfig(
                openai_embedding_dim=4,
                stock_state_dim=2,
                d_model=8,
                hidden_dim=8,
                layers=1,
                dropout=0.0,
            )
        )
        output = model(batch.x)
        whole = TrainingLossAccumulator()
        whole.add(compute_loss(output, batch))
        partitioned = TrainingLossAccumulator()
        for indices in (torch.tensor([0, 1]), torch.tensor([2, 3, 4, 5])):
            sliced = slice_batch(batch, indices)
            partitioned.add(compute_loss(model(sliced.x), sliced))
        whole_metrics = whole.compute("x")
        partitioned_metrics = partitioned.compute("x")
        self.assertEqual(set(whole_metrics), set(partitioned_metrics))
        for key in whole_metrics:
            self.assertAlmostEqual(
                whole_metrics[key],
                partitioned_metrics[key],
                places=6,
                msg=key,
            )

    def test_buffered_shuffle_is_deterministic_and_resume_exact(self) -> None:
        config = LoaderConfig(
            openai_embedding_dim=4,
            stock_state_dim=2,
            batch_size=3,
            query_batch_articles=4,
            shuffle_buffer_articles=6,
        )
        source_batches = [
            make_dummy_batch(3, config),
            make_dummy_batch(3, config),
            make_dummy_batch(2, config),
        ]
        next_id = 0
        for batch in source_batches:
            values = [f"id-{index}" for index in range(next_id, next_id + batch.sample_count)]
            batch.identity["canonical_news_id"] = values
            next_id += batch.sample_count

        class FakeDataset:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def iter_batches(self) -> object:
                yield from source_batches

            def stop(self) -> None:
                pass

        def identities(skip: int = 0, epoch: int = 2) -> list[str]:
            with mock.patch(
                "research.news_reaction_model.v10.data.ClickHouseNewsReactionDataset",
                FakeDataset,
            ):
                return [
                    identity
                    for shuffled in deterministic_buffered_batches(
                        config,
                        start="2019-01-01",
                        end_exclusive="2020-01-01",
                        epoch=epoch,
                        seed=17,
                        skip_articles=skip,
                    )
                    for identity in shuffled.identity["canonical_news_id"]
                ]

        full = identities()
        self.assertEqual(full, identities())
        self.assertEqual(full[3:], identities(skip=3))
        self.assertNotEqual(full, identities(epoch=3))

    def test_rng_state_round_trip_is_exact(self) -> None:
        random.seed(9)
        np.random.seed(9)
        torch.manual_seed(9)
        state = capture_rng_state()
        expected = (random.random(), np.random.random(), torch.rand(3))
        restore_rng_state(state)
        actual = (random.random(), np.random.random(), torch.rand(3))
        self.assertEqual(expected[0], actual[0])
        self.assertEqual(expected[1], actual[1])
        self.assertTrue(torch.equal(expected[2], actual[2]))

    def test_checkpoint_restores_exact_next_dropout_forward(self) -> None:
        loader = LoaderConfig(
            openai_embedding_dim=4,
            stock_state_dim=2,
            batch_size=2,
            shuffle_buffer_articles=4,
        )
        config = ExperimentConfig(
            loader=loader,
            model=ModelConfig(
                openai_embedding_dim=4,
                stock_state_dim=2,
                d_model=8,
                hidden_dim=8,
                layers=1,
                dropout=0.25,
            ),
            train=TrainConfig(
                epochs=2,
                scheduler="none",
                compile_model=False,
                amp=False,
                seed=23,
            ),
        )
        batch = make_dummy_batch(2, loader)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model = NewsReactionModelV10(config.model)
            optimizer = torch.optim.AdamW(model.parameters(), lr=config.train.learning_rate)
            scaler = torch.amp.GradScaler("cuda", enabled=False)
            manager = AsyncCheckpointManager(
                root / "source",
                root / "source.jsonl",
                CheckpointPolicy(archive_on_force=False),
            )
            window = TrainingLossAccumulator()
            epoch = TrainingLossAccumulator()
            cursor = TrainingCursor(
                samples_seen=2,
                epoch=0,
                epoch_articles_seen=2,
                next_log=50_000,
            )
            torch.manual_seed(config.train.seed)
            payload = checkpoint_payload(
                model,
                optimizer,
                None,
                scaler,
                manager,
                config,
                cursor,
                window,
                epoch,
                4,
                {"train/loss": 1.2},
                {"val/loss": 1.1},
            )
            checkpoint = root / "resume.pt"
            torch.save(payload, checkpoint)
            expected = model(batch.x).article_embedding.detach()
            manager.close()

            restored_model = NewsReactionModelV10(config.model)
            restored_optimizer = torch.optim.AdamW(
                restored_model.parameters(),
                lr=config.train.learning_rate,
            )
            restored_scaler = torch.amp.GradScaler("cuda", enabled=False)
            restored_manager = AsyncCheckpointManager(
                root / "restored",
                root / "restored.jsonl",
                CheckpointPolicy(archive_on_force=False),
            )
            restored_window = TrainingLossAccumulator()
            restored_epoch = TrainingLossAccumulator()
            restored = restore(
                str(checkpoint),
                restored_model,
                restored_optimizer,
                None,
                restored_scaler,
                restored_manager,
                restored_window,
                restored_epoch,
                torch.device("cpu"),
                config=config,
                train_articles=4,
                logging_samples=50_000,
            )
            actual = restored_model(batch.x).article_embedding.detach()
            restored_manager.close()
            self.assertEqual(restored.cursor, cursor)
            self.assertEqual(restored.last_train, {"train/loss": 1.2})
            self.assertEqual(restored.last_val, {"val/loss": 1.1})
            self.assertTrue(torch.equal(expected, actual))

    def test_prepared_and_evaluation_queries_reuse_v8_schema(self) -> None:
        config = LoaderConfig()
        batch_sql = prepared_batch_sql(
            config,
            dt.date(2026, 1, 1),
            dt.date(2026, 2, 1),
            "1970-01-01",
            "",
            "",
            10,
        )
        audit_sql = prepared_dataset_audit_sql(config, "2026-01-01", "2027-01-01")
        evaluation_sql = evaluation_batch_sql(
            config,
            dt.date(2026, 1, 1),
            dt.date(2026, 2, 1),
            "1970-01-01",
            "",
            "",
            10,
        )
        for sql in (batch_sql, evaluation_sql):
            self.assertIn("news_reaction_openai_stock_state_dataset_v8", sql)
            self.assertIn("openai_embedding_b64", sql)
            self.assertIn("stock_state", sql)
            self.assertNotIn("word_ids", sql)
        self.assertIn("length(openai_embedding) != 3072", audit_sql)

    def test_fit_comparison_reports_train_validation_gaps(self) -> None:
        training = {
            "metrics": {
                f"train/{metric}": value
                for metric, value in {
                    "accuracy": 0.8,
                    "balanced_accuracy": 0.7,
                    "macro_f1": 0.6,
                    "log_loss": 0.4,
                    "mean_confidence": 0.9,
                }.items()
            }
        }
        validation = {
            "metrics": {
                f"validation/{metric}": value
                for metric, value in {
                    "accuracy": 0.5,
                    "balanced_accuracy": 0.45,
                    "macro_f1": 0.4,
                    "log_loss": 0.8,
                    "mean_confidence": 0.6,
                }.items()
            }
        }
        comparison = fit_comparison(training, validation)
        self.assertAlmostEqual(comparison["train_minus_validation_accuracy"], 0.3)
        self.assertAlmostEqual(comparison["train_minus_validation_log_loss"], -0.4)

    def test_memorization_subset_is_deterministic_and_slices_all_tensors(self) -> None:
        config = LoaderConfig(openai_embedding_dim=4, stock_state_dim=2)
        sql = deterministic_subset_sql(
            config,
            start="2019-01-01",
            end_exclusive="2026-01-01",
            subset_size=10_000,
            subset_seed=17,
        )
        self.assertIn("cityHash64", sql)
        self.assertIn("toString(17)", sql)
        self.assertIn("LIMIT 10000", sql)
        self.assertIn("news_reaction_openai_stock_state_dataset_v8", sql)

        batch = make_dummy_batch(4, config)
        sliced = slice_batch(batch, torch.tensor([3, 1]))
        self.assertEqual(sliced.sample_count, 2)
        self.assertEqual(
            sliced.identity["canonical_news_id"],
            ["dummy-3", "dummy-1"],
        )
        self.assertEqual(tuple(sliced.x["openai_embedding"].shape), (2, 4))


if __name__ == "__main__":
    unittest.main()
