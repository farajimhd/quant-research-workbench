from __future__ import annotations

import unittest

from research.bar_gpt.v1.benchmark_loader import _make_data_config, _percentile, parse_args
from research.bar_gpt.v1.benchmark_offline_loader import _ints, parse_args as parse_offline_args
from research.bar_gpt.v1.run_benchmark_loader import DEFAULT_ARGS


class LoaderBenchmarkContractTest(unittest.TestCase):
    def test_percentile_uses_linear_interpolation(self) -> None:
        values = [0.001, 0.002, 0.003, 0.004]
        self.assertAlmostEqual(_percentile(values, 0.50), 0.0025)
        self.assertAlmostEqual(_percentile(values, 0.95), 0.00385)
        self.assertEqual(_percentile([], 0.95), 0.0)

    def test_launcher_defaults_are_bounded_and_production_shaped(self) -> None:
        self.assertEqual(DEFAULT_ARGS["--start-date"], "2025-10-01")
        self.assertEqual(DEFAULT_ARGS["--end-date"], "2026-01-01")
        self.assertEqual(DEFAULT_ARGS["--loader-workers"], "8")
        self.assertEqual(DEFAULT_ARGS["--ready-queue-blocks"], "64")
        self.assertEqual(DEFAULT_ARGS["--worker-prefetch-batches"], "2")
        self.assertEqual(DEFAULT_ARGS["--clickhouse-max-threads-per-worker"], "1")
        self.assertEqual(DEFAULT_ARGS["--origin-fetch-candidate-blocks"], "16")
        self.assertEqual(DEFAULT_ARGS["--origin-emit-blocks-per-chunk"], "16")
        self.assertEqual(DEFAULT_ARGS["--batch-size"], "2")
        self.assertGreater(int(DEFAULT_ARGS["--measured-batches"]), int(DEFAULT_ARGS["--warmup-batches"]))

    def test_validation_benchmark_maps_requested_interval_exactly(self) -> None:
        args = parse_args(
            [
                "--split",
                "validation",
                "--start-date",
                "2026-01-01",
                "--end-date",
                "2026-08-01",
                "--loader-workers",
                "0",
                "--no-pin-memory",
            ]
        )
        config = _make_data_config(args)
        self.assertEqual(config.start_date, "2026-01-01")
        self.assertEqual(config.validation_start_date, "2026-01-01")
        self.assertEqual(config.end_date, "2026-08-01")
        self.assertEqual(config.loader_workers, 0)
        self.assertFalse(config.pin_memory)

    def test_invalid_benchmark_lengths_fail_before_io(self) -> None:
        args = parse_args(["--warmup-batches", "-1"])
        with self.assertRaisesRegex(ValueError, "warmup-batches"):
            _make_data_config(args)

    def test_offline_loader_grid_is_v12_production_focused(self) -> None:
        args = parse_offline_args([])
        candidate_count = (
            len(_ints(args.workers))
            * len(_ints(args.worker_prefetch))
            * len(_ints(args.host_cache_batches))
            * len(_ints(args.length_bucket_batches))
        )
        self.assertEqual(candidate_count, 12)
        self.assertIn(16, _ints(args.workers))
        self.assertIn(2, _ints(args.worker_prefetch))
        self.assertIn(4, _ints(args.host_cache_batches))
        self.assertEqual(_ints(args.length_bucket_batches), (4,))


if __name__ == "__main__":
    unittest.main()
