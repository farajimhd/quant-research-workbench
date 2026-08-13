from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from research.bar_gpt.v2.config import TrainConfig
from research.bar_gpt.v2.full_chunk_training import (
    FULL_CHUNK_CONTRACT_VERSION,
    build_epoch_chunk_plan,
    load_epoch_chunk_plan,
    write_epoch_chunk_plan,
)
from research.bar_gpt.v2.offline_shards import OfflineBlockRef
from research.bar_gpt.v2.progress import TrainingProgressState, TrainingReporter
from research.bar_gpt.v2.run_train_full_chunks import (
    DEFAULT_MODEL_SIZE,
    parse_args as parse_full_args,
    trainer_argv,
)
from research.bar_gpt.v2.train import (
    _chunk_boundary_due,
    _outer_early_stopping_update,
    parse_args as parse_train_args,
)
from research.mlops.metrics import AsyncJsonlMetricLogger


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
            target_chunk_origins=30,
            monitor_origins=30,
            monitor_pool=self.monitor_pool,
        )
        self.assertEqual(plan.contract_version, FULL_CHUNK_CONTRACT_VERSION)
        self.assertEqual(plan.chunk_count, 4)
        self.assertEqual(sum(chunk.target_blocks for chunk in plan.chunks), 10)
        self.assertLessEqual(
            max(chunk.target_blocks for chunk in plan.chunks)
            - min(chunk.target_blocks for chunk in plan.chunks),
            1,
        )
        self.assertTrue(all(chunk.monitor_origins >= 30 for chunk in plan.chunks))
        self.assertTrue(
            all(
                {ref.ticker for ref in chunk.monitor_refs}
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
            target_chunk_origins=300,
            monitor_origins=30,
            monitor_pool=self.monitor_pool,
        )
        second = build_epoch_chunk_plan(
            epoch=1,
            seed=17,
            training_blocks=100,
            training_origins=1_000,
            target_chunk_origins=300,
            monitor_origins=30,
            monitor_pool=self.monitor_pool,
        )
        self.assertNotEqual(first.shuffle_seed, second.shuffle_seed)
        self.assertNotEqual(first.plan_hash, second.plan_hash)
        self.assertNotEqual(
            first.chunks[0].monitor_hash,
            second.chunks[0].monitor_hash,
        )

    def test_epoch_plan_round_trip_and_tamper_detection(self) -> None:
        plan = build_epoch_chunk_plan(
            epoch=2,
            seed=17,
            training_blocks=10,
            training_origins=100,
            target_chunk_origins=30,
            monitor_origins=30,
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

    def test_train_config_rejects_invalid_chunk_controls(self) -> None:
        with self.assertRaisesRegex(ValueError, "chunk and monitor"):
            TrainConfig(chunk_target_origins=0).validate()
        with self.assertRaisesRegex(ValueError, "patience"):
            TrainConfig(outer_early_stopping_patience=-1).validate()

    def test_chunk_boundary_never_splits_or_preempts_the_last_chunk(self) -> None:
        plan = build_epoch_chunk_plan(
            epoch=0,
            seed=17,
            training_blocks=10,
            training_origins=100,
            target_chunk_origins=30,
            monitor_origins=30,
            monitor_pool=self.monitor_pool,
        )
        self.assertFalse(
            _chunk_boundary_due(
                blocks_seen=2,
                chunk_start_blocks=0,
                chunk_index=0,
                plan=plan,
            )
        )
        self.assertTrue(
            _chunk_boundary_due(
                blocks_seen=3,
                chunk_start_blocks=0,
                chunk_index=0,
                plan=plan,
            )
        )
        self.assertFalse(
            _chunk_boundary_due(
                blocks_seen=10,
                chunk_start_blocks=8,
                chunk_index=plan.chunk_count - 1,
                plan=plan,
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


class FullChunkLauncherTest(unittest.TestCase):
    def test_defaults_to_medium_and_preserves_samples_seen_as_training_clock(self) -> None:
        args = parse_full_args([])
        self.assertEqual(args.model_size, DEFAULT_MODEL_SIZE)
        argv = trainer_argv(
            args,
            resolved_manifest=Path(r"D:\runtime\full_catalog_chunks_v1.json"),
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
        self.assertEqual(parsed.chunk_monitor_origins, 1_000_000)

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
