from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import torch
from rich.console import Console

from research.bar_gpt.v3.summarize_offline_dataset import (
    BoundedDistribution,
    PreparedShardSample,
    _context_counts,
    _decode_feature,
    _iter_prepared_shards,
    _padding_statistics,
    _prepare_shard_sample,
    _render,
    _select_sample,
    _stable_seed,
    _update_autoregressive_distributions,
    parse_args,
)
from research.bar_gpt.v3.targets import AUTOREGRESSIVE_TARGET_NAMES


class OfflineDatasetSummaryTest(unittest.TestCase):
    def test_distribution_is_bounded_and_preserves_exact_moments(self) -> None:
        stats = BoundedDistribution(capacity=3, seed=17)
        stats.update(torch.tensor([-2.0, 0.0, 2.0, float("nan")]))
        stats.update(torch.tensor([4.0, 6.0]))
        result = stats.summary()
        self.assertEqual(result["finite"], 5)
        self.assertEqual(result["nonfinite"], 1)
        self.assertEqual(result["reservoir"], 3)
        self.assertAlmostEqual(result["mean"], 2.0)
        self.assertAlmostEqual(result["zero_rate"], 0.2)
        self.assertAlmostEqual(result["std"], (8.0**0.5))

    def test_context_counts_respect_masked_left_prefix(self) -> None:
        block = SimpleNamespace(
            view_mask={
                "1s": torch.tensor([False, False, True, True, True, True]),
                "5s": torch.tensor([False, True, True]),
            },
            origin_indices=torch.tensor([2, 3, 4, 5]),
            asof_indices={"5s": torch.tensor([-1, -1, 1, 2])},
            origin_timestamps_us=torch.tensor([5, 10, 20, 30]) * 1_000_000,
            view_available_at_us={
                "1s": torch.tensor([0, 0, 8, 18, 28, 38]) * 1_000_000,
                "5s": torch.tensor([0, 15, 25]) * 1_000_000,
            },
        )
        rows = torch.arange(4)
        one_second, one_second_stale = _context_counts(block, "1s", rows, 2)
        coarse, coarse_stale = _context_counts(block, "5s", rows, 2)
        self.assertEqual(one_second.tolist(), [0, 1, 2, 2])
        self.assertTrue(torch.isnan(one_second_stale[0]))
        self.assertEqual(one_second_stale[1:].tolist(), [2.0, 2.0, 2.0])
        self.assertEqual(coarse.tolist(), [0, 0, 1, 2])
        self.assertTrue(torch.isnan(coarse_stale[0]))
        self.assertTrue(torch.isnan(coarse_stale[1]))
        self.assertEqual(coarse_stale[2:].tolist(), [5.0, 5.0])

    def test_sample_is_proportional_by_year_then_stable_hash(self) -> None:
        rows = [
            *({"unit_key": f"T{index:02d}:2020-01", "status": "complete"} for index in range(8)),
            *({"unit_key": f"U{index:02d}:2021-01", "status": "complete"} for index in range(4)),
            {"unit_key": "EMPTY:2020-01", "status": "covered_empty"},
        ]
        selected = _select_sample(rows, 6, seed=17)
        by_year = {year: 0 for year in ("2020", "2021")}
        for row in selected:
            by_year[str(row["unit_key"])[-7:-3]] += 1
        self.assertEqual(by_year, {"2020": 4, "2021": 2})
        expected_2020 = sorted(
            (row for row in rows if ":2020-" in row["unit_key"] and row["status"] == "complete"),
            key=lambda row: _stable_seed(17, "sample-unit", row["unit_key"]),
        )[:4]
        self.assertEqual(
            {row["unit_key"] for row in selected if ":2020-" in row["unit_key"]},
            {row["unit_key"] for row in expected_2020},
        )
        self.assertEqual(selected, _select_sample(rows, 6, seed=17))

    def test_decoding_and_padding_statistics_are_readable(self) -> None:
        decoded, unit, _transform = _decode_feature("trade_close_return", torch.asinh(torch.tensor([0.01])))
        self.assertEqual(unit, "bps")
        self.assertAlmostEqual(float(decoded[0]), 1.0, places=5)
        padding = _padding_statistics([4096, 512], [2], seed=17)[0]
        self.assertAlmostEqual(padding["valid_fraction"], 0.5625)
        self.assertAlmostEqual(padding["padding_fraction"], 0.4375)
        lengths = [1, 100, 2, 99, 3, 98, 4, 97]
        bucketed = _padding_statistics(lengths, [2], seed=17, length_bucket_batches=4)[0]
        unbucketed = _padding_statistics(lengths, [2], seed=17, length_bucket_batches=1)[0]
        self.assertGreater(bucketed["valid_fraction"], unbucketed["valid_fraction"])

    def test_cli_and_compact_render(self) -> None:
        args = parse_args(["--sample-shards", "2", "--batch-sizes", "8,16"])
        self.assertEqual(args.sample_shards, 2)
        self.assertEqual(args.batch_sizes, (8, 16))
        self.assertEqual(args.workers, 8)
        self.assertEqual(args.length_bucket_batches, 4)
        report = {
            "inventory": {"origins": 100, "blocks": 4, "bytes": 1024},
            "sampling": {"sampled_shards": 2, "sampled_blocks": 2, "sampled_origins": 20, "workers": 2},
            "context": [{
                "view": "1s", "configured_bars": 720, "full_context_rate": 0.5,
                "partial_context_rate": 0.5, "empty_context_rate": 0.0, "available_p50": 700.0,
            }],
            "padding": [{
                "batch_size": 8, "valid_fraction": 0.8, "padding_fraction": 0.2,
                "simulated_batches": 3,
            }],
            "integrity_findings": {},
        }
        console = Console(record=True, width=80, force_terminal=False)
        with tempfile.TemporaryDirectory() as directory:
            _render(report, Path(directory), console)
        rendered = console.export_text()
        self.assertIn("Dataset summary completed", rendered)
        self.assertIn("historical-context coverage", rendered)
        self.assertIn("Integrity findings: none", rendered)

    def test_preparation_workers_are_concurrent_but_results_remain_ordered(self) -> None:
        barrier = threading.Barrier(2)

        def prepare(row: dict[str, object], _config: object, _args: object) -> PreparedShardSample:
            barrier.wait(timeout=2.0)
            return PreparedShardSample(
                unit_key=str(row["unit_key"]), ticker=str(row["unit_key"]).split(":")[0],
                year="2021", block_lengths=[], blocks=[], integrity_findings={},
            )

        rows = [{"unit_key": "AAA:2021-01"}, {"unit_key": "BBB:2021-01"}]
        with patch("research.bar_gpt.v3.summarize_offline_dataset._prepare_shard_sample", side_effect=prepare):
            values = list(_iter_prepared_shards(rows, object(), SimpleNamespace(workers=2)))
        self.assertEqual([value.unit_key for value in values], ["AAA:2021-01", "BBB:2021-01"])

    def test_shard_preparation_failure_identifies_unit(self) -> None:
        row = {"unit_key": "BROKEN:2021-01", "tensor_path": "missing.pt"}
        with patch("research.bar_gpt.v3.summarize_offline_dataset.load_shard", side_effect=OSError("disk error")):
            with self.assertRaisesRegex(RuntimeError, "BROKEN:2021-01.*disk error"):
                _prepare_shard_sample(row, object(), SimpleNamespace())

    def test_autoregressive_merge_updates_every_target(self) -> None:
        target_count = len(AUTOREGRESSIVE_TARGET_NAMES)
        distributions = {}
        target_meta = {}
        _update_autoregressive_distributions(
            view="1s",
            values=torch.arange(2 * target_count, dtype=torch.float32).reshape(2, target_count),
            mask=torch.ones((2, target_count), dtype=torch.bool),
            distributions=distributions,
            target_meta=target_meta,
            capacity=8,
            seed=17,
        )
        self.assertEqual(len(distributions), target_count)
        self.assertTrue(all(distribution.total == 2 for distribution in distributions.values()))


if __name__ == "__main__":
    unittest.main()
