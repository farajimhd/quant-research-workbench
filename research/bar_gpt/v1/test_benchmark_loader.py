from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from research.bar_gpt.v1.benchmark_loader import _make_data_config, _percentile, parse_args
from research.bar_gpt.v1.benchmark_offline_loader import (
    _allocate_unit_batches,
    _fixed_workloads,
    _ints,
    _workload_hash,
    parse_args as parse_offline_args,
)
from research.bar_gpt.v1.offline_shards import OfflineBlockRef, OfflineShardUnit
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
        self.assertEqual(args.measured_batches, 64)
        self.assertEqual(args.repeats, 3)

    def test_offline_loader_allocates_one_fixed_full_batch_workload(self) -> None:
        units = tuple(
            OfflineShardUnit(
                unit_key=f"T{index}:2020-01", path=Path(f"T{index}.pt"), sessions=1,
                blocks=96, origins=1000, stable_unit_index=index,
                condition_positive_counts=(0, 0, 0, 0),
            )
            for index in range(6)
        )
        allocation = _allocate_unit_batches(
            units, batches=8, batch_size=32, minimum_units=4, seed=17,
        )
        self.assertGreaterEqual(len(allocation), 4)
        self.assertEqual(sum(count for _unit, count in allocation), 8)
        self.assertTrue(all(count * 32 <= unit.blocks for unit, count in allocation))
        self.assertEqual(
            allocation,
            _allocate_unit_batches(units, batches=8, batch_size=32, minimum_units=4, seed=17),
        )

    def test_workload_hash_is_order_independent_but_identity_sensitive(self) -> None:
        def ref(offset: int) -> OfflineBlockRef:
            return OfflineBlockRef(
                unit_key="AAA:2020-01", session_index=0, block_index=offset, origins=10,
                ticker="AAA", local_date="2020-01-02", activity_regime=0,
                session_phase="open", has_condition_target=False, unit_index=7, block_offset=offset,
            )

        self.assertEqual(_workload_hash((ref(1), ref(2))), _workload_hash((ref(2), ref(1))))
        self.assertNotEqual(_workload_hash((ref(1), ref(2))), _workload_hash((ref(1), ref(3))))

    def test_fixed_workloads_are_disjoint_full_batches_on_shared_units(self) -> None:
        units = tuple(
            OfflineShardUnit(
                unit_key=f"T{index}:2020-01", path=Path(f"T{index}.pt"), sessions=1,
                blocks=96, origins=1000, stable_unit_index=index,
                condition_positive_counts=(0, 0, 0, 0),
            )
            for index in range(4)
        )

        def refs(unit: OfflineShardUnit) -> tuple[OfflineBlockRef, ...]:
            return tuple(
                OfflineBlockRef(
                    unit_key=unit.unit_key, session_index=0, block_index=offset, origins=10,
                    ticker=unit.unit_key.partition(":")[0], local_date="2020-01-02",
                    activity_regime=0, session_phase="open", has_condition_target=False,
                    unit_index=unit.stable_unit_index, block_offset=offset,
                )
                for offset in range(unit.blocks)
            )

        with patch("research.bar_gpt.v1.benchmark_offline_loader._unit_block_refs", side_effect=refs):
            warmup, measured = _fixed_workloads(
                units, warmup_batches=2, measured_batches=4, batch_size=16,
                minimum_units=4, seed=17,
            )
        self.assertEqual(len(warmup), 32)
        self.assertEqual(len(measured), 64)
        warmup_ids = {(ref.unit_index, ref.block_offset) for ref in warmup}
        measured_ids = {(ref.unit_index, ref.block_offset) for ref in measured}
        self.assertTrue(warmup_ids.isdisjoint(measured_ids))
        self.assertEqual({ref.unit_key for ref in measured}, {unit.unit_key for unit in units})


if __name__ == "__main__":
    unittest.main()
