from __future__ import annotations

import unittest

from research.bar_gpt.v1.benchmark_loader import _make_data_config, _percentile, parse_args
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
        self.assertEqual(DEFAULT_ARGS["--loader-workers"], "4")
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


if __name__ == "__main__":
    unittest.main()
