from __future__ import annotations

import io
import hashlib
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import asdict
from pathlib import Path

import torch

from research.bar_gpt.v2.config import TrainConfig
from research.bar_gpt.v2.full_chunk_training import (
    FULL_CHUNK_CONTRACT_VERSION,
    build_epoch_chunk_plan,
    load_epoch_chunk_plan,
    load_full_training_refs,
    write_epoch_chunk_plan,
)
from research.bar_gpt.v2.model_discovery import DISCOVERY_CONTRACT_VERSION
from research.bar_gpt.v2.offline_shards import OfflineBlockRef, OfflineShardUnit
from research.bar_gpt.v2.progress import TrainingProgressState, TrainingReporter
from research.bar_gpt.v2.run_train_full_chunks import (
    DEFAULT_MODEL_SIZE,
    parse_args as parse_full_args,
    trainer_argv,
)
from research.bar_gpt.v2.train import (
    _chunk_repetition_complete,
    _outer_early_stopping_update,
    parse_args as parse_train_args,
)
from research.mlops.metrics import AsyncJsonlMetricLogger
from research.mlops.schedulers import EpochChunkCosineScheduler


def _ref(ticker: str, index: int, *, origins: int = 10) -> OfflineBlockRef:
    return OfflineBlockRef(
        unit_key=f"{ticker}:2026-01",
        session_index=index,
        block_index=0,
        origins=origins,
        ticker=ticker,
        local_date=f"2026-01-{index + 1:02d}",
        activity_regime=1 + index % 2,
        session_phase=("open", "mid", "close")[index % 3],
        has_condition_target=False,
        unit_index=index,
        block_offset=index,
    )


class FullChunkPlanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.training_refs = tuple(_ref("TRN", index) for index in range(10))
        self.monitor_pool = tuple(
            _ref(ticker, index)
            for ticker in ("AAA", "BBB", "CCC")
            for index in range(8)
        )

    def test_epoch_plan_distributes_complete_blocks_without_a_small_tail(self) -> None:
        plan = build_epoch_chunk_plan(
            epoch=0,
            seed=17,
            training_blocks=10,
            training_origins=100,
            training_refs=self.training_refs,
            target_chunk_origins=30,
            validation_origins=30,
            monitor_pool=self.monitor_pool,
        )
        self.assertEqual(plan.contract_version, FULL_CHUNK_CONTRACT_VERSION)
        self.assertEqual(plan.chunk_count, 4)
        self.assertEqual(sum(chunk.target_blocks for chunk in plan.chunks), 10)
        planned_indices = tuple(
            index for chunk in plan.chunks for index in chunk.training_ref_indices
        )
        self.assertEqual(sorted(planned_indices), list(range(10)))
        self.assertLessEqual(
            max(chunk.target_blocks for chunk in plan.chunks)
            - min(chunk.target_blocks for chunk in plan.chunks),
            1,
        )
        self.assertTrue(all(chunk.validation_origins >= 30 for chunk in plan.chunks))
        self.assertTrue(
            all(
                {ref.ticker for ref in chunk.validation_refs}
                == {"AAA", "BBB", "CCC"}
                for chunk in plan.chunks
            )
        )

    def test_monitor_samples_and_training_shuffle_change_by_epoch(self) -> None:
        first = build_epoch_chunk_plan(
            epoch=0,
            seed=17,
            training_blocks=100,
            training_origins=1_000,
            training_refs=tuple(
                _ref("TRN", index, origins=10)
                for index in range(100)
            ),
            target_chunk_origins=300,
            validation_origins=30,
            monitor_pool=self.monitor_pool,
        )
        second = build_epoch_chunk_plan(
            epoch=1,
            seed=17,
            training_blocks=100,
            training_origins=1_000,
            training_refs=tuple(
                _ref("TRN", index, origins=10)
                for index in range(100)
            ),
            target_chunk_origins=300,
            validation_origins=30,
            monitor_pool=self.monitor_pool,
        )
        self.assertNotEqual(first.shuffle_seed, second.shuffle_seed)
        self.assertNotEqual(first.plan_hash, second.plan_hash)
        self.assertNotEqual(
            first.chunks[0].validation_hash,
            second.chunks[0].validation_hash,
        )

    def test_epoch_plan_round_trip_and_tamper_detection(self) -> None:
        plan = build_epoch_chunk_plan(
            epoch=2,
            seed=17,
            training_blocks=10,
            training_origins=100,
            training_refs=self.training_refs,
            target_chunk_origins=30,
            validation_origins=30,
            monitor_pool=self.monitor_pool,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "epoch.json"
            write_epoch_chunk_plan(plan, path)
            restored = load_epoch_chunk_plan(path)
            self.assertEqual(restored, plan)
            value = json.loads(path.read_text(encoding="utf-8"))
            value["training_blocks"] += 1
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "hash mismatch"):
                load_epoch_chunk_plan(path)

    def test_full_training_index_uses_storage_ticker_order(self) -> None:
        ticker_order = ("BBB", "AAA")
        keys = (
            "BBB:2020-01",
            "AAA:2020-01",
            "BBB:2020-02",
            "AAA:2020-02",
        )
        refs = tuple(
            OfflineBlockRef(
                unit_key=key,
                session_index=0,
                block_index=0,
                origins=10,
                ticker=key.split(":", 1)[0],
                local_date=key.split(":", 1)[1] + "-03",
                activity_regime=1,
                session_phase="regular_open",
                has_condition_target=False,
                unit_index=index,
                block_offset=0,
            )
            for index, key in enumerate(keys)
        )
        units = tuple(
            OfflineShardUnit(
                unit_key=key,
                path=Path(f"{key}.pt"),
                sessions=1,
                blocks=1,
                origins=10,
                stable_unit_index=index,
                condition_positive_counts=(0, 0, 0, 0),
            )
            for index, key in enumerate(sorted(keys))
        )
        unit_hash = hashlib.sha256("\n".join(keys).encode("utf-8")).hexdigest()
        catalog_digest = hashlib.sha256()
        for ref in refs:
            identity = (
                f"{ref.unit_key}|{ref.session_index}|{ref.block_index}|"
                f"{ref.ticker}|{ref.local_date}|{ref.block_offset}"
            )
            catalog_digest.update(identity.encode("utf-8"))
            catalog_digest.update(b"\n")
        manifest = {
            "full_chunk_training": {
                "training_blocks": 4,
                "training_origins": 40,
                "training_catalog_hash": catalog_digest.hexdigest(),
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "full_catalog_chunks_v2.json"
            cache = manifest_path.parent / "full_catalog_index_v1" / "training.jsonl"
            cache.parent.mkdir(parents=True)
            rows = [{
                "contract_version": DISCOVERY_CONTRACT_VERSION,
                "unit_keys_hash": unit_hash,
            }]
            # Completion order may differ from the authoritative catalog order.
            rows.extend(
                {
                    "unit_key": ref.unit_key,
                    "refs": [asdict(ref)],
                }
                for ref in reversed(refs)
            )
            cache.write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n",
                encoding="utf-8",
            )
            restored = load_full_training_refs(
                manifest_path=manifest_path,
                units=units,
                manifest=manifest,
                ticker_order=ticker_order,
            )
        self.assertEqual(restored, refs)

    def test_train_config_rejects_invalid_chunk_controls(self) -> None:
        with self.assertRaisesRegex(ValueError, "chunk and validation"):
            TrainConfig(chunk_target_origins=0).validate()
        with self.assertRaisesRegex(ValueError, "max_chunk_epochs"):
            TrainConfig(max_chunk_epochs=0).validate()
        with self.assertRaisesRegex(ValueError, "chunk early-stopping patience"):
            TrainConfig(chunk_early_stopping_patience=0).validate()
        with self.assertRaisesRegex(ValueError, "patience"):
            TrainConfig(outer_early_stopping_patience=-1).validate()

    def test_chunk_repetition_completes_only_at_its_block_boundary(self) -> None:
        self.assertFalse(
            _chunk_repetition_complete(
                blocks_seen=2,
                repetition_start_blocks=0,
                target_blocks=3,
            )
        )
        self.assertTrue(
            _chunk_repetition_complete(
                blocks_seen=3,
                repetition_start_blocks=0,
                target_blocks=3,
            )
        )

    def test_outer_early_stopping_uses_relative_improvement_and_patience(self) -> None:
        best, reference, stale, stopped = _outer_early_stopping_update(
            observed_loss=10.0,
            best_loss=float("inf"),
            reference_loss=float("inf"),
            epochs_without_improvement=0,
            patience=2,
            minimum_relative_delta=0.001,
        )
        self.assertEqual((best, reference, stale, stopped), (10.0, 10.0, 0, False))
        best, reference, stale, stopped = _outer_early_stopping_update(
            observed_loss=9.995,
            best_loss=best,
            reference_loss=reference,
            epochs_without_improvement=stale,
            patience=2,
            minimum_relative_delta=0.001,
        )
        self.assertEqual(best, 9.995)
        self.assertEqual(reference, 10.0)
        self.assertEqual((stale, stopped), (1, False))
        _best, _reference, stale, stopped = _outer_early_stopping_update(
            observed_loss=10.1,
            best_loss=best,
            reference_loss=reference,
            epochs_without_improvement=stale,
            patience=2,
            minimum_relative_delta=0.001,
        )
        self.assertEqual((stale, stopped), (2, True))

    def test_chunk_cosine_spans_all_repetitions_and_restarts_per_chunk(self) -> None:
        parameter = torch.nn.Parameter(torch.ones(()))
        optimizer = torch.optim.SGD((parameter,), lr=1e-3)
        scheduler = EpochChunkCosineScheduler(
            optimizer,
            minimum_lr=1e-4,
            epoch_decay=0.95,
        )
        scheduler.start_chunk(
            epoch=0,
            start_samples=0,
            chunk_samples=2_000_000,
            samples_seen=0,
        )
        self.assertAlmostEqual(optimizer.param_groups[0]["lr"], 1e-3)
        scheduler.step(samples_seen=100_000)
        self.assertGreater(optimizer.param_groups[0]["lr"], 9e-4)
        # Crossing ten nominal 100K-origin repetitions does not restart the
        # cycle: this is the midpoint of one 20-repetition chunk schedule.
        scheduler.step(samples_seen=1_000_000)
        self.assertAlmostEqual(optimizer.param_groups[0]["lr"], 5.5e-4)
        scheduler.step(samples_seen=2_000_000)
        self.assertAlmostEqual(optimizer.param_groups[0]["lr"], 1e-4)
        scheduler.start_chunk(
            epoch=0,
            start_samples=2_000_000,
            chunk_samples=1_600_000,
            samples_seen=2_000_000,
        )
        self.assertAlmostEqual(optimizer.param_groups[0]["lr"], 1e-3)
        scheduler.step(samples_seen=3_600_000)
        scheduler.start_chunk(
            epoch=1,
            start_samples=3_600_000,
            chunk_samples=2_000_000,
            samples_seen=3_600_000,
        )
        expected_epoch_one_peak = 1e-3 * 0.95
        self.assertAlmostEqual(
            optimizer.param_groups[0]["lr"], expected_epoch_one_peak
        )
        restored_optimizer = torch.optim.SGD(
            (torch.nn.Parameter(torch.ones(())),), lr=1e-3
        )
        restored = EpochChunkCosineScheduler(
            restored_optimizer,
            minimum_lr=1e-4,
            epoch_decay=0.95,
        )
        restored.load_state_dict(scheduler.state_dict())
        self.assertEqual(restored.state_dict(), scheduler.state_dict())


class FullChunkLauncherTest(unittest.TestCase):
    def test_defaults_to_medium_and_preserves_samples_seen_as_training_clock(self) -> None:
        args = parse_full_args([])
        self.assertEqual(args.model_size, DEFAULT_MODEL_SIZE)
        self.assertEqual(args.run_stamp, "production")
        argv = trainer_argv(
            args,
            resolved_manifest=Path(r"D:\runtime\full_catalog_chunks_v2.json"),
        )
        self.assertEqual(argv.count("--batch-size"), 1)
        self.assertEqual(argv.count("--wandb-project"), 1)
        parsed = parse_train_args(
            argv
        )
        self.assertTrue(parsed.full_chunk_training)
        self.assertEqual(parsed.d_model, 512)
        self.assertEqual(parsed.n_layers, 12)
        self.assertEqual(parsed.batch_size, 10)
        self.assertEqual(parsed.gradient_accumulation_steps, 4)
        self.assertEqual(parsed.epochs, 10)
        self.assertEqual(parsed.chunk_target_origins, 30_000_000)
        self.assertEqual(parsed.chunk_validation_origins, 1_000_000)
        self.assertEqual(parsed.max_chunk_epochs, 20)
        self.assertEqual(parsed.chunk_early_stopping_patience, 1)
        self.assertEqual(parsed.scheduler_mode, "epoch-chunk-cosine")
        self.assertEqual(parsed.cosine_restart_decay, 0.95)

    def test_text_reporter_prints_epoch_chunk_and_sample_progress(self) -> None:
        state = TrainingProgressState(
            run_name="test",
            device="cpu",
            precision="float32",
            output_dir="runtime",
            model_parameters=1,
            max_samples=1_000,
            full_chunk_training=True,
            chunk_index=2,
            chunk_count=4,
            chunk_start_blocks=10,
            chunk_block_budget=5,
        )
        reporter = TrainingReporter(state, layout="text")
        stream = io.StringIO()
        with redirect_stdout(stream):
            reporter.update(
                {
                    "train/samples_seen": 123,
                    "train/blocks_seen": 13,
                    "train/loss": 1.0,
                }
            )
        rendered = stream.getvalue()
        self.assertIn("chunk=2/4", rendered)
        self.assertIn("chunk_blocks=3/5", rendered)
        self.assertIn("run_origins=123/1,000", rendered)

    def test_wandb_and_jsonl_use_cumulative_origins_as_step(self) -> None:
        class FakeRun:
            def __init__(self) -> None:
                self.calls: list[tuple[dict[str, float], int]] = []

            def log(self, metrics: dict[str, float], *, step: int) -> None:
                self.calls.append((metrics, step))

        fake = FakeRun()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.jsonl"
            logger = AsyncJsonlMetricLogger(path, fake)
            logger.log({"chunk/index": 3.0}, step=91_000_123)
            logger.close()
            row = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(row["step"], 91_000_123)
        self.assertEqual(fake.calls, [({"chunk/index": 3.0}, 91_000_123)])


if __name__ == "__main__":
    unittest.main()
