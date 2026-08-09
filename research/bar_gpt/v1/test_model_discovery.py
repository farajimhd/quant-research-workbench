from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import torch

from research.bar_gpt.v1.model_discovery import (
    ARCHITECTURE_GRID,
    DISCOVERY_WANDB_PROJECT,
    _balanced_sample,
    _held_out_panel,
    _ranking_key,
)
from research.bar_gpt.v1.offline_shards import OfflineBlockRef, OfflineShardDataset, OfflineShardUnit
from research.bar_gpt.v1.train import _wandb_metric_key, parse_args
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
        self.assertEqual(len(ARCHITECTURE_GRID), 8)
        self.assertEqual({item.microbatch * item.accumulation for item in ARCHITECTURE_GRID}, {32})

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

    def test_metric_namespaces_remain_separate_at_wandb_first_level(self) -> None:
        for namespace in ("monitor", "validation", "locked_test"):
            key = f"{namespace}_loss/total"
            self.assertEqual(_wandb_metric_key(key), key)
        self.assertNotEqual(DISCOVERY_WANDB_PROJECT, "bar gpt")

    def test_discovery_trainer_accepts_exhaustive_validation_and_single_cosine(self) -> None:
        args = parse_args(("--validation-batches", "0", "--scheduler-mode", "single-cosine"))
        self.assertEqual(args.validation_batches, 0)
        self.assertEqual(args.scheduler_mode, "single-cosine")

    def test_ranking_is_quality_first(self) -> None:
        better_loss = {"validation_loss/total": 0.2, "validation_direction/mcc_macro": 0.0}
        better_mcc = {"validation_loss/total": 0.3, "validation_direction/mcc_macro": 0.8}
        self.assertLess(_ranking_key(better_loss), _ranking_key(better_mcc))

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
        with patch("research.bar_gpt.v1.offline_shards.load_shard", return_value={}), patch(
            "research.bar_gpt.v1.offline_shards.materialize_block", side_effect=materialize
        ):
            blocks = list(dataset)
        self.assertEqual([item.block_offset for item in blocks], [1, 2])
        self.assertEqual({item.worker_id for item in blocks}, {0})

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


if __name__ == "__main__":
    unittest.main()
