from __future__ import annotations

import dataclasses
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import torch

from research.bar_gpt.v2.config import DataConfig
from research.bar_gpt.v2.data import BarGPTBatch
from research.bar_gpt.v2.inference import BarGPTEncoder
from research.bar_gpt.v2.offline_shards import (
    OfflineBlockRef,
    OfflineShardDataset,
    _storage_contract_config,
    config_hash,
)
from research.bar_gpt.v2.profile_train import ProfileCandidate, _data as profile_data, parse_args as profile_args
from research.bar_gpt.v2.run_train import default_argv
from research.bar_gpt.v2.train import build_config, parse_args as train_args


def _write_manifest(root: Path, config: DataConfig) -> None:
    manifest = root / "manifest"
    manifest.mkdir(parents=True)
    (manifest / "build_plan.json").write_text(json.dumps({
        "contract_version": 12,
        "config_hash": config_hash(config),
        "storage_config": _storage_contract_config(config),
    }), encoding="utf-8")


class V12RuntimeAlignmentTest(unittest.TestCase):
    def test_production_launcher_hydrates_v12_storage_contract(self) -> None:
        storage = dataclasses.replace(DataConfig(), origin_bars_1s=4096)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_manifest(root, storage)
            args = train_args([*default_argv(), "--offline-shard-root", str(root), "--wandb-mode", "disabled"])
            config = build_config(args)
        self.assertEqual(config.data.loader_stream_contract_version, 13)
        self.assertEqual(config.data.origin_bars_1s, 4096)
        self.assertEqual(config.data.batch_size, 20)
        self.assertEqual(config.train.gradient_accumulation_steps, 2)
        self.assertEqual(config.data.loader_workers, 8)
        self.assertEqual(config.data.ready_queue_blocks, 64)
        self.assertEqual(config.data.worker_prefetch_batches, 1)

    def test_profiler_uses_same_manifest_authority(self) -> None:
        storage = dataclasses.replace(DataConfig(), origin_bars_1s=4096)
        candidate = ProfileCandidate(
            4096, 8, 1, 4, True, length_bucket_batches=16,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_manifest(root, storage)
            args = profile_args(["--offline-shard-root", str(root)])
            data = profile_data(args, candidate)
        self.assertEqual(data.loader_stream_contract_version, 13)
        self.assertEqual(data.origin_bars_1s, 4096)
        self.assertEqual(data.offline_length_bucket_batches, 16)

    def test_encoder_propagates_only_required_view_masks(self) -> None:
        class Capture(torch.nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.kwargs: dict[str, object] = {}

            def embed(self, views: dict[str, torch.Tensor], **kwargs: object):
                self.kwargs = kwargs
                return views["1s"], {}

        model = Capture()
        encoder = BarGPTEncoder(model, DataConfig())  # type: ignore[arg-type]
        batch = SimpleNamespace(
            views={"1s": torch.zeros(1, 2, 3), "1D": torch.zeros(1, 2, 3)},
            view_mask={"1s": torch.ones(1, 2, dtype=torch.bool), "1D": torch.tensor([[False, True]])},
            masked_context_views=("1D",),
            origin_indices=torch.zeros(1, 1, dtype=torch.long),
            asof_indices={"1D": torch.zeros(1, 1, dtype=torch.long)},
            origin_mask=torch.ones(1, 1, dtype=torch.bool),
        )
        encoder(batch)  # type: ignore[arg-type]
        masks = model.kwargs["view_masks"]
        self.assertEqual(tuple(masks), ("1D",))  # type: ignore[arg-type]

    def test_origin_count_never_reads_device_mask(self) -> None:
        class FailMask:
            def sum(self) -> object:
                raise AssertionError("origin_count synchronized the tensor")

        batch = object.__new__(BarGPTBatch)
        batch.valid_origin_count = 17
        batch.origin_mask = FailMask()  # type: ignore[assignment]
        self.assertEqual(batch.origin_count, 17)

    def test_length_bucketing_is_deterministic_and_batch_local(self) -> None:
        refs = tuple(
            OfflineBlockRef(
                unit_key="AAA:2019-01",
                session_index=0,
                block_index=index,
                origins=origins,
                ticker="AAA",
                local_date="2019-01-02",
                activity_regime=0,
                session_phase="regular",
                has_condition_target=False,
                unit_index=1,
                block_offset=index,
            )
            for index, origins in enumerate((4096, 512, 3000, 700, 4000, 600, 3200, 800))
        )
        dataset = OfflineShardDataset(
            (), seed=17, shuffle_units=True, block_refs=refs,
            batch_size=2, length_bucket_batches=4,
        )
        first = dataset._owned_block_refs(0, 1)
        second = dataset._owned_block_refs(0, 1)
        self.assertEqual(first, second)
        for left in range(0, len(first), 2):
            lengths = [ref.origins for ref in first[left:left + 2]]
            self.assertLessEqual(max(lengths) - min(lengths), 1500)

    def test_materialized_bucketing_uses_multiview_shapes(self) -> None:
        blocks = tuple(
            SimpleNamespace(
                origin_indices=torch.empty(4096, dtype=torch.long),
                views={
                    "1s": torch.empty(4096, 1),
                    "5s": torch.empty(length, 1),
                    "1D": torch.empty(calendar_length, 1),
                },
            )
            for length, calendar_length in (
                (100, 4), (900, 20), (102, 5), (902, 21),
                (300, 8), (700, 16), (302, 9), (702, 17),
            )
        )
        dataset = OfflineShardDataset(
            (), seed=17, shuffle_units=True, batch_size=2, length_bucket_batches=4,
        )
        ordered = dataset._bucket_order(blocks, worker_id=0)
        for left in range(0, len(ordered), 2):
            lengths = [int(block.views["5s"].shape[0]) for block in ordered[left:left + 2]]
            self.assertLessEqual(max(lengths) - min(lengths), 2)


if __name__ == "__main__":
    unittest.main()
