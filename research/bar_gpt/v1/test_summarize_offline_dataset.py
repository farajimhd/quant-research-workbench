from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import torch
from rich.console import Console

from research.bar_gpt.v1.summarize_offline_dataset import (
    BoundedDistribution,
    _context_counts,
    _decode_feature,
    _padding_statistics,
    _render,
    _select_sample,
    parse_args,
)


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
            origin_indices=torch.tensor([3, 4, 5]),
            asof_indices={"5s": torch.tensor([-1, 1, 2])},
            origin_timestamps_us=torch.tensor([10, 20, 30]) * 1_000_000,
            view_available_at_us={
                "1s": torch.tensor([0, 0, 8, 18, 28, 38]) * 1_000_000,
                "5s": torch.tensor([0, 15, 25]) * 1_000_000,
            },
        )
        rows = torch.arange(3)
        one_second, one_second_stale = _context_counts(block, "1s", rows, 2)
        coarse, coarse_stale = _context_counts(block, "5s", rows, 2)
        self.assertEqual(one_second.tolist(), [1, 2, 2])
        self.assertEqual(one_second_stale.tolist(), [2.0, 2.0, 2.0])
        self.assertEqual(coarse.tolist(), [0, 1, 2])
        self.assertTrue(torch.isnan(coarse_stale[0]))
        self.assertEqual(coarse_stale[1:].tolist(), [5.0, 5.0])

    def test_sample_includes_earliest_shard_per_ticker(self) -> None:
        rows = [
            {"unit_key": "AAA:2020-02", "status": "complete"},
            {"unit_key": "AAA:2020-01", "status": "complete"},
            {"unit_key": "BBB:2021-02", "status": "complete"},
            {"unit_key": "BBB:2021-01", "status": "complete"},
            {"unit_key": "CCC:2020-01", "status": "covered_empty"},
        ]
        selected = _select_sample(rows, 2, seed=17)
        self.assertEqual({row["unit_key"] for row in selected}, {"AAA:2020-01", "BBB:2021-01"})
        self.assertEqual(selected, _select_sample(rows, 2, seed=17))

    def test_decoding_and_padding_statistics_are_readable(self) -> None:
        decoded, unit, _transform = _decode_feature("trade_close_return", torch.asinh(torch.tensor([0.01])))
        self.assertEqual(unit, "bps")
        self.assertAlmostEqual(float(decoded[0]), 1.0, places=5)
        padding = _padding_statistics([4096, 512], [2], seed=17)[0]
        self.assertAlmostEqual(padding["valid_fraction"], 0.5625)
        self.assertAlmostEqual(padding["padding_fraction"], 0.4375)

    def test_cli_and_compact_render(self) -> None:
        args = parse_args(["--sample-shards", "2", "--batch-sizes", "8,16"])
        self.assertEqual(args.sample_shards, 2)
        self.assertEqual(args.batch_sizes, (8, 16))
        report = {
            "inventory": {"origins": 100, "blocks": 4, "bytes": 1024},
            "sampling": {"sampled_shards": 2, "sampled_blocks": 2, "sampled_origins": 20},
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


if __name__ == "__main__":
    unittest.main()
