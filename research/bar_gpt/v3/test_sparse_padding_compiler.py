from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import torch

from research.bar_gpt.v3.config import DataConfig
from research.bar_gpt.v3.loader import ClickHouseBarStreamConfig
from research.bar_gpt.v3.offline_shards import _merge_view
from research.bar_gpt.v3.offline_shards import shard_compatibility_hash
from research.bar_gpt.v3.schema import FEATURE_NAMES
from research.bar_gpt.v3.shard_data_audit import _complete_sidecars, reconstruct_clickhouse_example


def _example(
    *,
    name: str,
    mask: tuple[bool, ...],
    real_starts: tuple[int, ...],
) -> SimpleNamespace:
    missing = len(mask) - len(real_starts)
    if missing < 0 or tuple(mask) != (False,) * missing + (True,) * len(real_starts):
        raise ValueError("test context must be one masked prefix followed by real bars")
    starts = torch.tensor((0,) * missing + real_starts, dtype=torch.long)
    raw = torch.zeros((len(mask), len(FEATURE_NAMES)), dtype=torch.float32)
    if real_starts:
        raw[missing:, FEATURE_NAMES.index("source_event_count")] = 1
    return SimpleNamespace(
        ticker="AAPL",
        local_date="2019-01-02",
        raw_views={name: raw},
        raw_view_mask={name: torch.tensor(mask, dtype=torch.bool)},
        raw_view_start_us={name: starts},
        raw_view_end_us={name: starts.clone()},
        raw_view_available_at_us={name: starts.clone()},
    )


class SparsePaddingCompilerTest(unittest.TestCase):
    def test_ticker_filtered_audit_discovery_reads_only_requested_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for ticker in ("AAPL", "MSFT"):
                sidecar = root / "tickers" / ticker / "2020" / "2020-01.json"
                sidecar.parent.mkdir(parents=True, exist_ok=True)
                sidecar.write_text(
                    json.dumps({"status": "complete", "unit_key": f"{ticker}:2020-01"}),
                    encoding="utf-8",
                )

            selected = _complete_sidecars(root, ("AAPL",))

        self.assertEqual([path.parts[-3] for path in selected], ["AAPL"])

    def test_shrinking_missing_prefix_uses_one_canonical_padding_region(self) -> None:
        first = _example(
            name="30s",
            mask=(False, False, False, True, True),
            real_starts=(100, 130),
        )
        second = _example(
            name="30s",
            mask=(False, False, True, True, True),
            real_starts=(100, 130, 160),
        )

        shared, slices, _patches = _merge_view((first, second), "30s")

        self.assertEqual(shared["mask"].tolist(), [False, False, False, True, True, True])
        self.assertEqual(shared["start_us"].tolist(), [0, 0, 0, 100, 130, 160])
        self.assertEqual(slices, [(0, 5), (1, 5)])
        for example, (start, length) in zip((first, second), slices, strict=True):
            self.assertTrue(
                torch.equal(shared["mask"][start : start + length], example.raw_view_mask["30s"])
            )
            self.assertTrue(
                torch.equal(shared["start_us"][start : start + length], example.raw_view_start_us["30s"])
            )

    def test_entirely_unavailable_view_remains_padding_not_bars(self) -> None:
        first = _example(name="1D", mask=(False, False, False, False), real_starts=())
        second = _example(name="1D", mask=(False, False, False, False), real_starts=())

        shared, slices, _patches = _merge_view((first, second), "1D")

        self.assertEqual(slices, [(0, 4), (0, 4)])
        self.assertFalse(bool(shared["mask"].any()))
        self.assertTrue(bool(torch.all(shared["features"] == 0)))
        self.assertTrue(bool(torch.all(shared["start_us"] == 0)))

    def test_clickhouse_reconstruction_uses_direct_event_authority(self) -> None:
        config = DataConfig(origin_bars_1s=4)
        rebuilt = SimpleNamespace(local_date="2019-01-03", block_offset=7)
        sample = SimpleNamespace(
            ref=SimpleNamespace(
                session_index=1,
                unit_key="AAPL:2019-01",
                ticker="AAPL",
                local_date="2019-01-03",
                block_offset=7,
            ),
            shard={
                "config_hash": shard_compatibility_hash(config),
                "sessions": [
                    {"local_date": "2019-01-02"},
                    {"local_date": "2019-01-03"},
                ],
            },
        )

        dataset_kwargs = {}

        class FakeDirectDataset:
            def __init__(self, **kwargs) -> None:
                dataset_kwargs.update(kwargs)

            def __iter__(self):
                yield rebuilt

        with patch(
            "research.bar_gpt.v3.shard_data_audit.DirectEventShardDataset",
            FakeDirectDataset,
        ):
            observed = reconstruct_clickhouse_example(
                sample,
                data_config=config,
                stream_config=ClickHouseBarStreamConfig(
                    url="http://localhost:8123", user="default", password=""
                ),
            )
        self.assertIs(observed, rebuilt)
        resolved = dataset_kwargs["data_config"]
        self.assertEqual(resolved.start_date, "2019-01-02")
        self.assertEqual(resolved.end_date, "2019-01-04")

    def test_clickhouse_reconstruction_replays_frozen_catalog_history_without_emitting_it(self) -> None:
        config = DataConfig(origin_bars_1s=4)
        rebuilt = SimpleNamespace(local_date="2020-12-21", block_offset=37)
        sample = SimpleNamespace(
            ref=SimpleNamespace(
                session_index=1,
                unit_key="AAPL:2020-12",
                ticker="AAPL",
                local_date="2020-12-21",
                block_offset=37,
            ),
            shard={
                "config_hash": shard_compatibility_hash(config),
                "sessions": [
                    {"local_date": "2020-12-18"},
                    {"local_date": "2020-12-21"},
                ],
            },
        )
        dataset_kwargs = {}

        class FakeDirectDataset:
            def __init__(self, **kwargs) -> None:
                dataset_kwargs.update(kwargs)

            def __iter__(self):
                yield rebuilt

        with patch(
            "research.bar_gpt.v3.shard_data_audit.DirectEventShardDataset",
            FakeDirectDataset,
        ):
            observed = reconstruct_clickhouse_example(
                sample,
                data_config=config,
                stream_config=ClickHouseBarStreamConfig(
                    url="http://localhost:8123", user="default", password=""
                ),
                catalog_start_date="2019-01-01",
            )
        self.assertIs(observed, rebuilt)
        self.assertEqual(dataset_kwargs["data_config"].start_date, "2019-01-01")
        self.assertEqual(dataset_kwargs["emit_start_date"], "2020-12-18")


if __name__ == "__main__":
    unittest.main()
