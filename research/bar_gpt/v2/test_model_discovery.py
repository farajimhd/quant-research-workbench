from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import torch

from research.bar_gpt.v2.model_discovery import (
    ARCHITECTURE_GRID,
    DISCOVERY_WANDB_PROJECT,
    DISCOVERY_ORIGIN_BARS_1S,
    DISCOVERY_EPOCHS,
    DISCOVERY_TRAIN_ORIGINS_PER_EPOCH,
    _balanced_sample,
    _balanced_units,
    enumerate_block_refs,
    _held_out_panel,
    _ranking_key,
    _resume_if_available,
    _trainer_command,
    parse_args as parse_discovery_args,
    discovery_data_config,
    discovery_storage_config,
    discovery_shard_compatibility_hash,
)
from research.bar_gpt.v2.model_discovery_final_validation import (
    FINAL_VALIDATION_WANDB_PROJECT,
    ArchitectureCheckpoint,
    evaluation_command,
    resolve_architecture_checkpoints,
)
from research.bar_gpt.v2.offline_shards import OfflineBlockRef, OfflineShardDataset, OfflineShardUnit
from research.bar_gpt.v2.offline_shards import shard_compatibility_hash
from research.bar_gpt.v2.train import _wandb_metric_key, parse_args
from research.mlops.schedulers import SampleWarmupCosineScheduler


def ref(ticker: str, day: str, offset: int, origins: int = 100) -> OfflineBlockRef:
    return OfflineBlockRef(
        unit_key=f"{ticker}:{day[:7]}",
        session_index=0,
        block_index=offset,
        origins=origins,
        ticker=ticker,
        local_date=day,
        activity_regime=offset % 3,
        session_phase="regular_midday",
        has_condition_target=offset % 5 == 0,
        unit_index=abs(hash((ticker, day[:7]))) % 1_000_000,
        block_offset=offset,
    )


class ModelDiscoveryContractTest(unittest.TestCase):
    def test_architecture_grid_keeps_effective_batch_fixed(self) -> None:
        self.assertEqual(len(ARCHITECTURE_GRID), 7)
        self.assertEqual({item.microbatch * item.accumulation for item in ARCHITECTURE_GRID}, {32})
        selected = {item.name: (item.microbatch, item.accumulation) for item in ARCHITECTURE_GRID}
        self.assertEqual(selected["anchor_384x8"], (16, 2))
        self.assertEqual(selected["medium_512x12"], (8, 4))
        self.assertNotIn("xlarge_1024x16", {item.name for item in ARCHITECTURE_GRID})
        self.assertEqual(DISCOVERY_TRAIN_ORIGINS_PER_EPOCH, 100_000_000)
        self.assertEqual(DISCOVERY_EPOCHS * DISCOVERY_TRAIN_ORIGINS_PER_EPOCH, 200_000_000)

    def test_discovery_training_uses_production_loader_shape(self) -> None:
        self.assertEqual(parse_discovery_args(()).workers, 8)
        command = _trainer_command(
            ARCHITECTURE_GRID[0],
            shard_root=Path("shards"),
            manifest_path=Path("panels.json"),
            output_root=Path("output"),
            project="project",
            wandb_mode="disabled",
            workers=12,
            seed=17,
            run_name="run",
        )
        self.assertEqual(command[command.index("--loader-workers") + 1], "12")
        self.assertEqual(command[command.index("--ready-queue-blocks") + 1], "64")
        self.assertEqual(command[command.index("--worker-prefetch-batches") + 1], "1")
        self.assertEqual(command[command.index("--offline-length-bucket-batches") + 1], "16")

    def test_fixed_panel_sampling_is_deterministic_and_date_disjoint(self) -> None:
        refs = tuple(
            ref(ticker, f"2026-01-{day:02d}", offset)
            for ticker in ("AAA", "BBB", "CCC")
            for day in range(2, 9)
            for offset in range(4)
        )
        first = _balanced_sample(refs, target_origins=1_000, seed=17, label="train")
        repeated = _balanced_sample(refs, target_origins=1_000, seed=17, label="train")
        self.assertEqual(first, repeated)
        used: set[tuple[str, str]] = set()
        monitor = _held_out_panel(refs, target_origins=600, seed=17, label="monitor", used_dates=used)
        validation = _held_out_panel(refs, target_origins=600, seed=17, label="validation", used_dates=used)
        monitor_dates = {(item.ticker, item.local_date) for item in monitor}
        validation_dates = {(item.ticker, item.local_date) for item in validation}
        self.assertFalse(monitor_dates & validation_dates)

    def test_monitor_reserves_a_sparse_tickers_only_date_for_validation(self) -> None:
        refs = (
            ref("AAA", "2026-01-02", 0),
            ref("AAA", "2026-01-05", 1),
            ref("BBB", "2026-01-02", 0),
        )
        used: set[tuple[str, str]] = set()
        monitor = _held_out_panel(
            refs,
            target_origins=100,
            seed=17,
            label="monitor",
            used_dates=used,
            reserve_dates_per_ticker=1,
            require_every_ticker=False,
        )
        validation = _held_out_panel(
            refs,
            target_origins=200,
            seed=17,
            label="validation",
            used_dates=used,
        )
        self.assertEqual({item.ticker for item in monitor}, {"AAA"})
        self.assertEqual({item.ticker for item in validation}, {"AAA", "BBB"})
        self.assertFalse(
            {(item.ticker, item.local_date) for item in monitor}
            & {(item.ticker, item.local_date) for item in validation}
        )

    def test_shard_preselection_is_ticker_balanced_and_time_spread(self) -> None:
        units = tuple(
            OfflineShardUnit(
                unit_key=f"{ticker}:2020-{month:02d}",
                path=Path(f"{ticker}-{month}.pt"),
                sessions=1,
                blocks=1,
                origins=100,
                stable_unit_index=month,
                condition_positive_counts=(0, 0, 0, 0),
            )
            for ticker in ("AAA", "BBB")
            for month in range(1, 13)
        )
        selected = _balanced_units(units, units_per_ticker=3, seed=17, label="train")
        self.assertEqual(len(selected), 6)
        for ticker in ("AAA", "BBB"):
            months = sorted(int(unit.unit_key[-2:]) for unit in selected if unit.unit_key.startswith(ticker))
            self.assertTrue(1 <= months[0] <= 4)
            self.assertTrue(5 <= months[1] <= 8)
            self.assertTrue(9 <= months[2] <= 12)

    def test_block_index_cache_avoids_reopening_completed_shards(self) -> None:
        unit = OfflineShardUnit(
            unit_key="AAA:2020-01",
            path=Path("unused.pt"),
            sessions=1,
            blocks=1,
            origins=100,
            stable_unit_index=7,
            condition_positive_counts=(0, 0, 0, 0),
        )
        shard = {
            "sessions": [{
                "ticker": "AAA",
                "local_date": "2020-01-02",
                "blocks": [{
                    "origin_indices": torch.arange(100),
                    "activity_regime": 1,
                    "session_phase": "regular_midday",
                    "has_condition_target": False,
                    "unit_index": 7,
                    "block_offset": 0,
                }],
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "index.jsonl"
            with patch("research.bar_gpt.v2.model_discovery.load_shard", return_value=shard):
                first = enumerate_block_refs((unit,), label="test", cache_path=cache)
            with patch(
                "research.bar_gpt.v2.model_discovery.load_shard",
                side_effect=AssertionError("cache should avoid shard reopen"),
            ):
                repeated = enumerate_block_refs((unit,), label="test", cache_path=cache)
        self.assertEqual(first, repeated)

    def test_metric_namespaces_remain_separate_at_wandb_first_level(self) -> None:
        for namespace in ("monitor", "validation", "locked_test"):
            key = f"{namespace}_loss/total"
            self.assertEqual(_wandb_metric_key(key), key)
        self.assertNotEqual(DISCOVERY_WANDB_PROJECT, "bar gpt")

    def test_discovery_trainer_accepts_exhaustive_validation_and_single_cosine(self) -> None:
        args = parse_args(("--validation-batches", "0", "--scheduler-mode", "single-cosine"))
        self.assertEqual(args.validation_batches, 0)
        self.assertEqual(args.scheduler_mode, "single-cosine")

    def test_discovery_uses_the_certified_4096_origin_shard_contract(self) -> None:
        config = discovery_data_config()
        self.assertEqual(config.origin_bars_1s, DISCOVERY_ORIGIN_BARS_1S)
        self.assertEqual(
            shard_compatibility_hash(config),
            "6705a58f9002e6c44829a25fc3016c722df173486f18925fd74b448905901be7",
        )

    def test_manifest_hash_survives_runtime_condition_target_masking(self) -> None:
        config = discovery_data_config()
        certified_hash = discovery_shard_compatibility_hash(config)
        config.condition_target_active = (False, True, False, True)
        storage_config = discovery_storage_config(config)
        self.assertEqual(storage_config.condition_target_active, (True, True, True, True))
        self.assertEqual(config.condition_target_active, (False, True, False, True))
        self.assertEqual(shard_compatibility_hash(storage_config), certified_hash)
        self.assertEqual(discovery_shard_compatibility_hash(config), certified_hash)
        self.assertNotEqual(shard_compatibility_hash(config), certified_hash)

    def test_ranking_is_quality_first(self) -> None:
        better_loss = {"validation_loss/total": 0.2, "validation_trade_summary/mcc_macro": 0.0}
        better_mcc = {"validation_loss/total": 0.3, "validation_trade_summary/mcc_macro": 0.8}
        self.assertLess(_ranking_key(better_loss), _ranking_key(better_mcc))

    def test_ranking_prefers_close_return_class_mcc_over_legacy_extrema_mix(self) -> None:
        close_skill = {
            "validation_loss/total": 0.2,
            "validation_close_return_class_summary/mcc_macro": 0.4,
            "validation_trade_summary/mcc_macro": -0.5,
        }
        extrema_skill = {
            "validation_loss/total": 0.2,
            "validation_close_return_class_summary/mcc_macro": 0.1,
            "validation_trade_summary/mcc_macro": 0.9,
        }
        self.assertLess(_ranking_key(close_skill), _ranking_key(extrema_skill))

    def test_explicit_block_stream_is_identity_checked_and_resumable(self) -> None:
        refs = tuple(ref("AAA", "2026-01-02", offset) for offset in range(3))
        unit = OfflineShardUnit(
            unit_key="AAA:2026-01",
            path=Path("unused.pt"),
            sessions=1,
            blocks=3,
            origins=300,
            stable_unit_index=refs[0].unit_index,
            condition_positive_counts=(0, 0, 0, 0),
        )

        def materialize(_shard, _session_index, block_index):
            item = refs[block_index]
            return SimpleNamespace(
                ticker=item.ticker,
                local_date=item.local_date,
                origin_indices=torch.arange(item.origins),
                unit_index=item.unit_index,
                block_offset=item.block_offset,
                worker_id=-1,
            )

        cursor = SimpleNamespace(unit_index=refs[0].unit_index, block_offset=refs[0].block_offset)
        dataset = OfflineShardDataset(
            (unit,),
            seed=17,
            shuffle_units=False,
            resume_cursors={0: cursor},
            block_refs=refs,
        )
        with patch("research.bar_gpt.v2.offline_shards.load_shard", return_value={}), patch(
            "research.bar_gpt.v2.offline_shards.materialize_block", side_effect=materialize
        ):
            blocks = list(dataset)
        self.assertEqual([item.block_offset for item in blocks], [1, 2])
        self.assertEqual({item.worker_id for item in blocks}, {0})

    def test_explicit_block_workers_own_whole_shards_with_contiguous_locality(self) -> None:
        refs = tuple(
            ref(ticker, "2026-01-02", offset)
            for offset in range(5)
            for ticker in ("AAA", "BBB", "CCC", "DDD")
        )
        units = tuple(
            OfflineShardUnit(
                unit_key=f"{ticker}:2026-01",
                path=Path(f"{ticker}.pt"),
                sessions=1,
                blocks=5,
                origins=500,
                stable_unit_index=ref(ticker, "2026-01-02", 0).unit_index,
                condition_positive_counts=(0, 0, 0, 0),
            )
            for ticker in ("AAA", "BBB", "CCC", "DDD")
        )
        dataset = OfflineShardDataset(
            units,
            seed=17,
            shuffle_units=True,
            block_refs=refs,
        )
        first = tuple(dataset._owned_block_refs(worker, 2) for worker in range(2))
        repeated = tuple(dataset._owned_block_refs(worker, 2) for worker in range(2))
        self.assertEqual(first, repeated)
        self.assertCountEqual((item for owned in first for item in owned), refs)
        owners: dict[str, set[int]] = {}
        for worker, owned in enumerate(first):
            unit_runs: list[str] = []
            for item in owned:
                owners.setdefault(item.unit_key, set()).add(worker)
                if not unit_runs or unit_runs[-1] != item.unit_key:
                    unit_runs.append(item.unit_key)
            self.assertEqual(len(unit_runs), len(set(unit_runs)))
        self.assertTrue(all(len(worker_ids) == 1 for worker_ids in owners.values()))

        dataset.epoch = 1
        second_epoch = tuple(dataset._owned_block_refs(worker, 2) for worker in range(2))
        self.assertNotEqual(second_epoch, first)
        self.assertCountEqual((item for owned in second_epoch for item in owned), refs)

    def test_single_cosine_resume_rejects_a_different_schedule_contract(self) -> None:
        parameter = torch.nn.Parameter(torch.tensor(1.0))
        optimizer = torch.optim.AdamW((parameter,), lr=3e-4)
        first = SampleWarmupCosineScheduler(
            optimizer,
            warmup_samples=4_000_000,
            total_samples=400_000_000,
            minimum_lr=3e-5,
        )
        state = first.state_dict()
        second_parameter = torch.nn.Parameter(torch.tensor(1.0))
        second_optimizer = torch.optim.AdamW((second_parameter,), lr=3e-4)
        second = SampleWarmupCosineScheduler(
            second_optimizer,
            warmup_samples=4_000_000,
            total_samples=200_000_000,
            minimum_lr=3e-5,
        )
        with self.assertRaisesRegex(RuntimeError, "scheduler configuration"):
            second.load_state_dict(state)

    def test_campaign_reuses_latest_checkpoint_after_interruption(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_root = Path(directory)
            checkpoint = run_root / "checkpoints" / "checkpoint_latest.pt"
            checkpoint.parent.mkdir(parents=True)
            checkpoint.touch()
            command = _resume_if_available(["python", "train"], run_root)
            self.assertEqual(command[-2:], ["--resume-checkpoint", str(checkpoint)])

    def test_final_validation_resolves_completed_and_interrupted_architecture_checkpoints(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            campaign_id = "20260809-145210"
            completed_run = "discovery-architecture-anchor_384x8-complete"
            expected_wide = f"discovery-architecture-width_1024x12-{campaign_id}"
            for run_name in (completed_run, expected_wide):
                checkpoint = root / "runs" / run_name / "checkpoints" / "checkpoint_latest.pt"
                checkpoint.parent.mkdir(parents=True)
                checkpoint.touch()
            resolved = resolve_architecture_checkpoints(
                discovery_root=root,
                campaign_state={
                    "campaign_id": campaign_id,
                    "runs": {"architecture/anchor_384x8": completed_run},
                },
                architecture_names=("anchor_384x8", "width_1024x12"),
            )
        self.assertEqual([item.run_name for item in resolved], [completed_run, expected_wide])
        self.assertEqual([item.batch_size for item in resolved], [16, 8])

    def test_final_validation_command_uses_separate_panel_namespace_and_project(self) -> None:
        item = ArchitectureCheckpoint("width_512x8", "source", Path("checkpoint.pt"), 16)
        command = evaluation_command(
            item,
            manifest_path=Path("manifest.json"),
            shard_root=Path("shards"),
            output_root=Path("output"),
            run_name="evaluation",
            target_training_origins=200_000_000,
            workers=16,
            wandb_project=FINAL_VALIDATION_WANDB_PROJECT,
            wandb_entity="entity",
            wandb_mode="online",
        )
        self.assertEqual(command[command.index("--panel") + 1], "validation")
        self.assertEqual(command[command.index("--namespace") + 1], "final_validation")
        self.assertEqual(command[command.index("--target-training-origins") + 1], "200000000")
        self.assertEqual(command[command.index("--batch-size") + 1], "16")
        self.assertEqual(command[command.index("--wandb-project") + 1], FINAL_VALIDATION_WANDB_PROJECT)


if __name__ == "__main__":
    unittest.main()
