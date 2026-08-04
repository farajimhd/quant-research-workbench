from __future__ import annotations

import io
import datetime as dt
import http.client
import threading
import time
import unittest
from contextlib import redirect_stdout
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import torch
from rich.console import Console

from research.bar_gpt.v1.config import BarGPTConfig, DataConfig, TrainConfig
from research.bar_gpt.v1.data import FixedBucketHistoryCache, PATHWAY_ID_BY_NAME, TIMEFRAME_US_BY_NAME, BarView, collate_examples
from research.bar_gpt.v1.loader import (
    ArrowStreamClient,
    BarGPTSequentialDataset,
    ClickHouseBarStreamConfig,
    OriginWindow,
    SequentialBlockPlan,
    SequentialSessionPlan,
    TickerInterval,
    balanced_regime_stream,
    build_session_examples,
    frame_to_dense_window,
    held_out_tickers,
    month_units,
    origin_window_schedule,
    origin_windows_query,
    validation_block_plan,
    worker_ticker_shards,
)
from research.bar_gpt.v1.sampling import CoverageCursor, SESSION_PHASES, coverage_plan_summary, select_stratified_examples
from research.bar_gpt.v1.prefetch import DeviceBatchPrefetcher
from research.bar_gpt.v1.integration import PackedBarEmbeddingAdapter
from research.bar_gpt.v1.metrics import ValidationAccumulator
from research.bar_gpt.v1.features import MODEL_FEATURE_NAMES, project_stationary_features
from research.bar_gpt.v1.model import BarGPTV1
from research.bar_gpt.v1.linear_probe import fit_ridge_probes
from research.bar_gpt.v1.objectives import _weighted_mean, compute_loss
from research.bar_gpt.v1.progress import TrainingProgressState, TrainingReporter
from research.bar_gpt.v1.schema import FEATURE_INDEX, FEATURE_NAMES
from research.bar_gpt.v1.targets import TARGET_NAMES, build_next_bar_targets, build_physical_horizon_targets
from research.bar_gpt.v1.train import (
    _advance_cursors,
    _batch_eligibility_metrics,
    _checkpoint_policy,
    _mask_inactive_condition_targets,
    PreparedValidationBatches,
    _validation_milestones,
    _resolved_warmup_samples,
    _resume_data_contract,
    preflight,
)
from research.bar_gpt.v1.profile_train import _parse_candidates
from research.bar_gpt.v1.run_build_conditions_1s import default_argv as condition_builder_argv
from research.bar_gpt.v1.run_profile_train import DEFAULT_ARGS as profile_launcher_args
from research.bar_gpt.v1.run_train import DEFAULT_ARGS as training_launcher_args
from research.mlops.schedulers import SampleWarmupCosineScheduler


def session_view(length: int = 24) -> BarView:
    raw = torch.zeros((length, len(FEATURE_NAMES)), dtype=torch.float32)
    price = 100.0 + torch.arange(length) * 0.01
    for prefix, offset in (("trade", 0.0), ("bid", -0.01), ("ask", 0.01)):
        raw[:, FEATURE_INDEX[f"{prefix}_present"]] = 1
        for field in ("open", "high", "low", "close"):
            raw[:, FEATURE_INDEX[f"{prefix}_{field}"]] = price + offset
        raw[:, FEATURE_INDEX[f"{prefix}_size_sum"]] = 10
        raw[:, FEATURE_INDEX[f"{prefix}_size_squared_sum"]] = 100
        raw[:, FEATURE_INDEX[f"{prefix}_price_size_sum"]] = (price + offset) * 10
        raw[:, FEATURE_INDEX[f"{prefix}_event_count"]] = 1
    raw[:, FEATURE_INDEX["quote_pair_present"]] = 1
    raw[:, FEATURE_INDEX["quote_pair_count"]] = 1
    raw[:, FEATURE_INDEX["spread_close"]] = 0.02
    raw[:, FEATURE_INDEX["spread_sum"]] = 0.02
    raw[:, FEATURE_INDEX["midpoint_close"]] = price
    raw[:, FEATURE_INDEX["midpoint_sum"]] = price
    raw[:, FEATURE_INDEX["microprice_close"]] = price
    raw[:, FEATURE_INDEX["microprice_sum"]] = price
    raw[:, FEATURE_INDEX["source_event_count"]] = 3
    starts = torch.arange(length, dtype=torch.long) * 1_000_000
    return BarView(raw, starts, starts + 1_000_000, starts + 1_000_000)


class LoaderTrainerContractTest(unittest.TestCase):
    def test_fixed_bucket_history_cache_evicts_old_rows(self) -> None:
        first = session_view(5)
        second = BarView(
            first.features[:3],
            first.bar_start_us[:3] + 10_000_000,
            first.bar_end_us[:3] + 10_000_000,
            first.available_at_us[:3] + 10_000_000,
        )
        cache = FixedBucketHistoryCache(max_rows=4)
        cache.append(first)
        retained = cache.append(second)
        self.assertEqual(cache.rows, 4)
        self.assertEqual(int(retained.bar_start_us[0]), int(first.bar_start_us[4]))
        self.assertEqual(int(retained.bar_start_us[-1]), int(second.bar_start_us[-1]))

    def test_validation_milestones_start_early_and_end_at_epoch(self) -> None:
        milestones = _validation_milestones(
            epoch_origins=7_563_836_672,
            runs_per_epoch=4,
            explicit_interval=0,
            initial_samples=33_554_432,
        )
        self.assertEqual(milestones[0], 33_554_432)
        self.assertEqual(milestones[-1], 7_563_836_672)
        self.assertEqual(len(milestones), 4)
        self.assertEqual(
            milestones[1:3],
            (round(7_563_836_672 / 3), round(2 * 7_563_836_672 / 3)),
        )

    def test_fixed_validation_batches_materialize_once_and_reiterate(self) -> None:
        first = {"batch": 1}
        second = {"batch": 2}
        loader = torch.utils.data.DataLoader([first, second], batch_size=None, num_workers=0)
        cached = PreparedValidationBatches(loader)
        try:
            self.assertEqual(list(cached), [first, second])
            self.assertEqual(list(cached), [first, second])
            self.assertTrue(cached.ready)
            self.assertEqual(cached.batch_count, 2)
        finally:
            cached.close()

    def test_inactive_condition_channels_are_loss_ineligible(self) -> None:
        batch = SimpleNamespace(horizon_mask=torch.ones((1, 2, 3, 12), dtype=torch.bool))
        _mask_inactive_condition_targets(batch, (False, False, False, True))
        self.assertFalse(bool(batch.horizon_mask[..., -4:-1].any()))
        self.assertTrue(bool(batch.horizon_mask[..., -1].all()))
        self.assertTrue(bool(batch.horizon_mask[..., :-4].all()))

    def test_arrow_retry_discards_partial_attempt_before_exposing_rows(self) -> None:
        class Response:
            def __init__(self) -> None:
                self.closed = False

            def close(self) -> None:
                self.closed = True

        responses = [Response(), Response()]

        def partial_stream(_response: object):
            yield "partial"
            raise http.client.IncompleteRead(b"partial", 100)

        client = ArrowStreamClient(
            ClickHouseBarStreamConfig(
                "http://localhost:8123", "", "", retry_attempts=2,
                retry_initial_seconds=0, retry_max_seconds=0,
            )
        )
        with patch("research.bar_gpt.v1.loader.request.urlopen", side_effect=responses), patch(
            "pyarrow.ipc.open_stream", side_effect=(partial_stream(None), iter(("complete",)))
        ):
            with client.record_batches("SELECT 1 FORMAT ArrowStream") as batches:
                self.assertEqual(list(batches), ["complete"])
        self.assertTrue(all(response.closed for response in responses))

    def test_global_block_plan_resume_and_cursor_order_are_exact(self) -> None:
        sessions = (
            SequentialSessionPlan(0, "AAA", "2025-01-01", "2025-02-01", "2025-01-02", None, 4, 2, 0, 0),
            SequentialSessionPlan(1, "BBB", "2025-01-01", "2025-02-01", "2025-01-02", None, 4, 2, 0, 2),
        )
        plan = SequentialBlockPlan(sessions, (0, 2), (0, 2), (2, 2), 4, 12)
        self.assertEqual(plan.locate(2)[:2], (sessions[1], 0))
        self.assertEqual(plan.resume_global_index(CoverageCursor(0, 1)), 2)
        first = SimpleNamespace(worker_ids=(0,), unit_indices=(0,), block_offsets=(0,))
        second = SimpleNamespace(worker_ids=(0,), unit_indices=(0,), block_offsets=(1,))
        cursor = _advance_cursors({}, first, plan)
        cursor = _advance_cursors(cursor, second, plan)
        self.assertEqual(cursor, {0: CoverageCursor(0, 1)})
        with self.assertRaisesRegex(RuntimeError, "order violation"):
            _advance_cursors(cursor, first, plan)

    def test_indexed_dataset_fetches_a_bounded_chunk_and_emits_global_order(self) -> None:
        config = self.data_config()
        config.batch_size = 1
        config.origin_fetch_candidate_blocks = 2
        config.origin_emit_blocks_per_chunk = 2
        config.validation_slices = (("CCC", "2026-01-01", "2026-01-02"),)
        sessions = (
            SequentialSessionPlan(
                0, "AAA", "2025-01-01", "2025-02-01", "2025-01-03", "2025-01-02",
                0, 2, 0, 0,
            ),
        )
        plan = SequentialBlockPlan(sessions, (0,), (0,), (2,), 2, 6)

        class FakeClient(ArrowStreamClient):
            fetches = 0
            session_ranges: list[tuple[str, str]] = []
            condition_ranges: list[tuple[str, str]] = []

            def read_identity_intervals(self, tickers, **_kwargs):
                return {"AAA": (TickerInterval("AAA", "AAA", "2019-01-01", "9999-12-31"),)}

            def read_split_actions(self, _intervals, **_kwargs):
                return {"AAA": ()}

            def read_daily_view(self, **_kwargs):
                return None

            def read_condition_views(self, *, start_date, end_date, **_kwargs):
                self.condition_ranges.append((start_date, end_date))
                return {}

            def iter_session_views(self, *, start_date, end_date, **_kwargs):
                self.fetches += 1
                self.session_ranges.append((start_date, end_date))
                yield "2025-01-02", frame_to_dense_window(
                    None,
                    ticker="AAA",
                    local_date="2025-01-02",
                    clock_start_second=20 * 3600 - 20,
                    clock_end_second=20 * 3600,
                )
                yield "2025-01-03", frame_to_dense_window(
                    None,
                    ticker="AAA",
                    local_date="2025-01-03",
                    clock_start_second=4 * 3600,
                    clock_end_second=4 * 3600 + 20,
                )

            def read_origin_windows(self, *, windows, context_bars, right_support_bars, **_kwargs):
                self.fetches += 1
                result = []
                for window in windows:
                    elapsed = window.origin_bucket - 4 * 3600
                    prior_rows = max(0, context_bars - elapsed)
                    left = max(4 * 3600, window.origin_bucket - context_bars)
                    visible = int(window.origin_count or config.origin_bars_1s)
                    right = min(20 * 3600, window.origin_bucket + visible + right_support_bars)
                    current = frame_to_dense_window(
                        None, ticker="AAA", local_date=window.local_date,
                        clock_start_second=left, clock_end_second=right,
                    )
                    prior = (
                        frame_to_dense_window(
                            None, ticker="AAA", local_date=window.prior_date,
                            clock_start_second=20 * 3600 - prior_rows, clock_end_second=20 * 3600,
                        )
                        if prior_rows and window.prior_date is not None else None
                    )
                    result.append((current, prior))
                return result

        dataset = BarGPTSequentialDataset(
            data_config=config,
            stream_config=ClickHouseBarStreamConfig("http://localhost:8123", "", ""),
            plan=plan,
        )
        fake = FakeClient(dataset.stream_config)
        dataset._runtime = {
            "client": fake, "intervals": {}, "actions": {}, "daily": {},
            "condition_key": None, "conditions": {}, "examples": {},
        }
        first = dataset[0]
        second = dataset[1]
        self.assertEqual((first.unit_index, first.block_offset), (0, 0))
        self.assertEqual((second.unit_index, second.block_offset), (0, 1))
        self.assertEqual(first.origin_indices.numel(), 3)
        self.assertEqual(fake.fetches, 1)
        # The initial read ends at the requested session, not the end of its
        # ticker-month; conditions are bounded to the predecessor/current pair.
        self.assertEqual(fake.session_ranges, [("2024-12-20", "2025-01-04")])
        self.assertEqual(fake.condition_ranges, [("2025-01-02", "2025-01-04")])

    def test_validation_plan_uses_bounded_sequential_blocks(self) -> None:
        config = self.data_config()
        config.validation_slices = (("CCC", "2026-01-01", "2026-01-08"),)
        config.validation_blocks_per_slice = 2

        class FakeClient:
            def __init__(self, _config) -> None:
                pass

            def read_identity_intervals(self, tickers, **_kwargs):
                return {tickers[0]: (TickerInterval(tickers[0], tickers[0], "2019-01-01", "9999-12-31"),)}

            def read_daily_view(self, **_kwargs):
                dates = ["2025-12-31", "2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"]
                return dates, session_view(len(dates))

            def read_split_actions(self, intervals, **_kwargs):
                return {next(iter(intervals)): ()}

        with patch("research.bar_gpt.v1.loader.ArrowStreamClient", FakeClient):
            plan = validation_block_plan(
                data_config=config,
                stream_config=ClickHouseBarStreamConfig("http://localhost:8123", "", ""),
            )
        self.assertEqual(plan.total_blocks, 2)
        self.assertEqual(plan.total_origins, 6)
        self.assertEqual([item.ticker for item in plan.sessions], ["CCC", "CCC"])
        self.assertTrue(all(item.block_count == 1 for item in plan.sessions))

    def test_calendar_context_is_present_while_calendar_ar_loss_is_event_timed(self) -> None:
        config = self.data_config()
        daily_raw = session_view(3)
        base = 1_700_000_000_000_000
        current_raw = session_view(24)
        current = BarView(
            current_raw.features,
            current_raw.bar_start_us + base,
            current_raw.bar_end_us + base,
            current_raw.available_at_us + base,
        )
        daily_starts = torch.tensor([base - 21 * 86_400_000_000, base - 14 * 86_400_000_000, base - 7 * 86_400_000_000])
        daily = BarView(
            daily_raw.features,
            daily_starts,
            daily_starts + 1_000_000,
            daily_starts + 1_000_000,
        )
        example = next(
            build_session_examples(
                ticker="AAA",
                local_date="2026-03-02",
                session=current,
                daily=(["2023-10-20", "2023-10-27", "2023-11-03"], daily),
                split_actions=(),
                config=config,
            )
        )
        self.assertEqual(example.raw_views["1D"].shape[0], 3)
        self.assertTrue(bool(torch.all(example.asof_indices["1D"] >= 0)))
        batch = collate_examples([example])
        self.assertNotIn("1D", batch.autoregressive_mask)
        metrics = _batch_eligibility_metrics(batch)
        self.assertNotIn("train/context_available_1D", metrics)

    def test_origin_schedule_is_bounded_phase_spread_and_condition_first(self) -> None:
        dates = ["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07"]
        flags = torch.zeros((16 * 3600, 4), dtype=torch.float32)
        flags[5 * 3600, 0] = 1
        windows = origin_window_schedule(
            dates=dates,
            start_date="2025-01-02",
            end_date="2025-02-01",
            count=16,
            context_bars=2048,
            origin_bars=512,
            right_support_bars=3600,
            conditions_by_date={"2025-01-03": flags},
            seed=17,
        )
        self.assertEqual(len(windows), 16)
        self.assertEqual(windows[0].local_date, "2025-01-03")
        self.assertEqual(len({(item.local_date, item.origin_bucket) for item in windows}), 16)
        self.assertTrue(all(4 * 3600 <= item.origin_bucket <= 18 * 3600 + 51 * 60 + 28 for item in windows))

    def test_origin_query_reads_only_context_origin_and_target_support(self) -> None:
        config = ClickHouseBarStreamConfig("http://localhost:8123", "", "")
        sql = origin_windows_query(
            config,
            ticker="META",
            windows=(OriginWindow("2025-01-03", 4 * 3600, "2025-01-02"),),
            source_intervals=(TickerInterval("META", "META", "2020-01-01", "9999-12-31"),),
            context_bars=2048,
            origin_bars=512,
            right_support_bars=3600,
        )
        self.assertIn("b.bucket_index>=14400 AND b.bucket_index<18512", sql)
        self.assertIn("b.bucket_index>=69952 AND b.bucket_index<72000", sql)
        self.assertIn("PREWHERE (b.ticker='META'", sql)
        self.assertNotIn("local_date>=", sql)

    def test_bounded_window_builds_exactly_one_causal_example(self) -> None:
        config = DataConfig(
            tickers=("AAPL", "MSFT"),
            validation_slices=(("MSFT", "2026-01-01", "2026-01-02"),),
        )
        prior = frame_to_dense_window(
            None,
            ticker="AAPL",
            local_date="2025-01-02",
            clock_start_second=72000 - config.context_bars_1s,
            clock_end_second=72000,
        )
        session = frame_to_dense_window(
            None,
            ticker="AAPL",
            local_date="2025-01-03",
            clock_start_second=14400,
            clock_end_second=14400 + config.origin_bars_1s + config.right_support_bars_1s,
        )
        examples = list(
            build_session_examples(
                ticker="AAPL",
                local_date="2025-01-03",
                session=session,
                prior_session=prior,
                session_conditions=torch.zeros((session.features.shape[0], 4)),
                prior_conditions=torch.zeros((prior.features.shape[0], 4)),
                daily=None,
                split_actions=(),
                config=config,
            )
        )
        self.assertEqual(len(examples), 1)
        self.assertEqual(examples[0].origin_indices.numel(), config.origin_bars_1s)
        self.assertEqual(
            examples[0].target_support.shape[0],
            config.context_bars_1s + config.origin_bars_1s + config.right_support_bars_1s,
        )
    def data_config(self) -> DataConfig:
        return DataConfig(
            tickers=("AAA", "BBB", "CCC"),
            horizons_us=(1_000_000, 2_000_000),
            maximum_target_horizon_us=2_000_000,
            context_bars_1s=4,
            origin_bars_1s=3,
            min_origins_per_block=1,
            batch_size=2,
            loader_workers=0,
        )

    def test_session_rollup_and_targets_are_causal_and_nonredundant(self) -> None:
        examples = list(build_session_examples(
            ticker="AAA", local_date="2026-01-02", session=session_view(), daily=None,
            split_actions=(), config=self.data_config()
        ))
        self.assertGreater(len(examples), 1)
        first = examples[0]
        self.assertEqual(first.raw_views["1s"].shape[0], 7)  # context plus origins, future halo is target-only
        self.assertEqual(first.origin_indices.tolist(), [4, 5, 6])
        self.assertEqual(first.target_support.shape[0], 9)
        self.assertEqual(first.asof_indices["5s"].tolist(), [0, 0, 0])
        self.assertTrue(torch.all(first.asof_indices["1h"] == -1))
        batch = collate_examples([first])
        self.assertEqual(int(batch.autoregressive_mask["1s"].any(dim=-1).sum()), 3)
        self.assertNotIn("1D", batch.autoregressive_mask)

    def test_sequential_session_emits_tail_origins_with_unavailable_horizons_masked(self) -> None:
        config = self.data_config()
        examples = list(build_session_examples(
            ticker="AAA", local_date="2026-01-02", session=session_view(11), daily=None,
            split_actions=(), config=config, include_incomplete_horizons=True,
        ))
        self.assertEqual(sum(item.origin_indices.numel() for item in examples), 7)
        last = examples[-1]
        batch = collate_examples([last]).to("cpu", non_blocking=False)
        self.assertFalse(bool(batch.horizon_mask[0, -1, -1].any()))

    def test_intraday_autoregressive_target_masks_session_gap(self) -> None:
        raw = session_view(4).features
        starts = torch.tensor([0, 1_000_000, 86_400_000_000, 86_401_000_000])
        targets = build_next_bar_targets(raw, bar_start_us=starts, expected_step_us=1_000_000)
        self.assertTrue(bool(targets.mask[0].any()))
        self.assertFalse(bool(targets.mask[1].any()))
        self.assertTrue(bool(targets.mask[2].any()))

    def test_prior_session_halo_enables_first_premarket_origins_without_overlap(self) -> None:
        prior = session_view(8)
        current = session_view(12)
        offset = 86_400_000_000
        current = BarView(
            current.features,
            current.bar_start_us + offset,
            current.bar_end_us + offset,
            current.available_at_us + offset,
        )
        examples = list(build_session_examples(
            ticker="AAA", local_date="2026-01-03", session=current, prior_session=prior,
            daily=None, split_actions=(), config=self.data_config(),
        ))
        self.assertTrue(examples)
        self.assertEqual(examples[0].origin_indices.tolist(), [4, 5, 6])
        self.assertLess(int(prior.available_at_us[-1]), int(current.bar_start_us[0]))

    def test_epoch_units_are_month_major_with_deterministic_ticker_shuffle(self) -> None:
        first = month_units("2025-01-15", "2025-03-02", ("AAA", "BBB", "CCC"), seed=17)
        second = month_units("2025-01-15", "2025-03-02", ("AAA", "BBB", "CCC"), seed=17)
        self.assertEqual(first, second)
        self.assertEqual([(unit.start_date, unit.end_date) for unit in first[::3]], [
            ("2025-01-15", "2025-02-01"), ("2025-02-01", "2025-03-01"), ("2025-03-01", "2025-03-02"),
        ])

    def test_coverage_plan_is_one_complete_sampled_epoch(self) -> None:
        plan = coverage_plan_summary(
            start_date="2020-01-01", end_date="2026-01-01",
            training_tickers=tuple(f"T{index}" for index in range(90)),
            blocks_per_unit=16, origin_bars=512, epochs=1, seed=17,
        )
        self.assertEqual(plan.months, 72)
        self.assertEqual(plan.units, 6_480)
        self.assertEqual(plan.expected_blocks, 103_680)
        self.assertEqual(plan.expected_origins, 53_084_160)

    def test_coverage_plan_uses_exact_sequential_totals(self) -> None:
        plan = coverage_plan_summary(
            start_date="2020-01-01", end_date="2020-02-01",
            training_tickers=("AAA",), blocks_per_unit=16, origin_bars=512,
            epochs=2, seed=17, coverage_mode="sequential", sessions_per_epoch=20,
            sequential_blocks_per_epoch=2_260, sequential_origins_per_epoch=1_152_000,
        )
        self.assertEqual(plan.sessions_per_epoch, 20)
        self.assertEqual(plan.expected_blocks, 4_520)
        self.assertEqual(plan.expected_origins, 2_304_000)

    def test_stratified_selector_covers_phases_and_keeps_condition_block(self) -> None:
        base = list(build_session_examples(
            ticker="AAA", local_date="2026-01-02", session=session_view(80), daily=None,
            split_actions=(), config=self.data_config(),
        ))[:5]
        hours = (13, 15, 18, 20, 22)  # UTC winter hours map to the five New York phases.
        examples = []
        for index, (example, hour) in enumerate(zip(base, hours, strict=True)):
            item = deepcopy(example)
            timestamp = int(dt.datetime(2026, 1, 2, hour, tzinfo=dt.timezone.utc).timestamp() * 1_000_000)
            item.origin_timestamps_us[:] = timestamp
            item.activity_regime = index % 3
            examples.append(item)
        examples[-1].target_condition_flags[6, 0] = 1
        first = select_stratified_examples(examples, limit=5, seed=7, balance_activity_regimes=True)
        second = select_stratified_examples(examples, limit=5, seed=7, balance_activity_regimes=True)
        self.assertEqual([item.local_date + item.session_phase for item in first], [item.local_date + item.session_phase for item in second])
        self.assertEqual(set(item.session_phase for item in first), set(SESSION_PHASES))
        self.assertTrue(any(item.has_condition_target for item in first))

    def test_sample_scheduler_warms_then_decays_and_resumes(self) -> None:
        parameter = torch.nn.Parameter(torch.tensor(1.0))
        optimizer = torch.optim.AdamW([parameter], lr=3e-4)
        scheduler = SampleWarmupCosineScheduler(
            optimizer, warmup_samples=100, total_samples=1_000, minimum_lr=3e-5,
        )
        self.assertAlmostEqual(optimizer.param_groups[0]["lr"], 3e-5)
        scheduler.step(100)
        self.assertAlmostEqual(optimizer.param_groups[0]["lr"], 3e-4)
        scheduler.step(1_000)
        self.assertAlmostEqual(optimizer.param_groups[0]["lr"], 3e-5)

    def test_long_run_defaults_use_profiled_shape_and_fractional_warmup(self) -> None:
        self.assertEqual(training_launcher_args["--origin-bars-1s"], "4096")
        self.assertEqual(training_launcher_args["--batch-size"], "16")
        self.assertEqual(training_launcher_args["--gradient-accumulation-steps"], "2")
        self.assertEqual(training_launcher_args["--loader-workers"], "12")
        config = TrainConfig(warmup_samples=0, warmup_fraction=0.01)
        self.assertEqual(_resolved_warmup_samples(config, 7_563_836_672), 75_638_367)
        config.warmup_samples = 12_345
        self.assertEqual(_resolved_warmup_samples(config, 7_563_836_672), 12_345)

    def test_zero_activity_horizon_volume_remains_finite_after_large_prefix(self) -> None:
        raw = session_view(64).features
        raw[:, FEATURE_INDEX["trade_size_sum"]] = 0
        raw[:, FEATURE_INDEX["trade_event_count"]] = 0
        raw[0, FEATURE_INDEX["trade_size_sum"]] = 1e20
        raw[0, FEATURE_INDEX["trade_event_count"]] = 1e10
        targets = build_physical_horizon_targets(
            raw,
            torch.tensor([20, 21, 22], dtype=torch.long),
            torch.tensor([5_000_000], dtype=torch.long),
        )
        volume_index = TARGET_NAMES.index("log_trade_volume")
        count_index = TARGET_NAMES.index("log_trade_count")
        self.assertTrue(torch.all(torch.isfinite(targets.values[targets.mask])))
        torch.testing.assert_close(targets.values[:, 0, volume_index], torch.zeros(3))
        torch.testing.assert_close(targets.values[:, 0, count_index], torch.zeros(3))

    def test_weighted_mean_excludes_invalid_nonfinite_cells_before_multiplication(self) -> None:
        loss = torch.tensor([[float("nan"), 2.0], [float("inf"), 4.0]])
        mask = torch.tensor([[False, True], [False, True]])
        value = _weighted_mean(loss, mask, torch.ones(2))
        self.assertEqual(float(value), 3.0)
        empty = _weighted_mean(loss, torch.zeros_like(mask), torch.ones(2))
        self.assertTrue(torch.isfinite(empty))
        self.assertEqual(float(empty), 0.0)

    def test_checkpoint_policy_selects_on_validation_not_training_loss(self) -> None:
        config = TrainConfig(checkpoint_latest_samples=123, checkpoint_archive_samples=456)
        policy = _checkpoint_policy(config)
        self.assertFalse(policy.save_best_train)
        self.assertTrue(policy.save_best_val)
        self.assertEqual(policy.latest_steps, 123)
        self.assertEqual(policy.archive_steps, 456)

    def test_resume_contract_allows_loader_tuning_but_not_sampling_changes(self) -> None:
        base = {
            "loader_workers": 2,
            "ready_queue_blocks": 2,
            "clickhouse_max_threads_per_worker": 2,
            "pin_memory": True,
            "persistent_workers": True,
            "origin_fetch_candidate_blocks": 4,
            "origin_emit_blocks_per_chunk": 2,
            "context_bars_1s": 2048,
        }
        tuned_queue = {**base, "ready_queue_blocks": 8}
        self.assertEqual(_resume_data_contract(base), _resume_data_contract(tuned_queue))
        changed_workers = {**base, "loader_workers": 4}
        self.assertNotEqual(_resume_data_contract(base), _resume_data_contract(changed_workers))
        changed_retry = {**base, "clickhouse_retry_attempts": 9}
        self.assertEqual(_resume_data_contract(base), _resume_data_contract(changed_retry))
        changed_sampling = {**base, "origin_fetch_candidate_blocks": 8}
        self.assertNotEqual(_resume_data_contract(base), _resume_data_contract(changed_sampling))

    def test_legacy_map_loader_checkpoint_cannot_resume_worker_owned_stream(self) -> None:
        legacy = _resume_data_contract({"context_bars_1s": 720})
        worker_owned = _resume_data_contract(
            {"context_bars_1s": 720, "loader_stream_contract_version": 2}
        )
        self.assertEqual(legacy["loader_stream_contract_version"], 1)
        self.assertNotEqual(legacy, worker_owned)

    def test_exchange_clock_encoding_is_absolute_not_window_relative(self) -> None:
        raw = session_view(3).features
        timestamps = torch.as_tensor(
            [
                int(dt.datetime(2026, 1, 2, 9, tzinfo=dt.timezone.utc).timestamp() * 1_000_000),
                int(dt.datetime(2026, 1, 2, 17, tzinfo=dt.timezone.utc).timestamp() * 1_000_000),
                int(dt.datetime(2026, 1, 3, 1, tzinfo=dt.timezone.utc).timestamp() * 1_000_000),
            ],
            dtype=torch.long,
        )
        projected = project_stationary_features(raw, timestamps, timeframe_us=1_000_000)
        sine = projected[:, MODEL_FEATURE_NAMES.index("session_progress_sin")]
        cosine = projected[:, MODEL_FEATURE_NAMES.index("session_progress_cos")]
        self.assertTrue(torch.allclose(sine, torch.zeros_like(sine), atol=1e-5))
        self.assertTrue(torch.allclose(cosine, torch.tensor([1.0, -1.0, 1.0]), atol=1e-5))
        midday_only = project_stationary_features(raw[1:2], timestamps[1:2], timeframe_us=1_000_000)
        self.assertAlmostEqual(float(midday_only[0, MODEL_FEATURE_NAMES.index("session_progress_cos")]), -1.0, places=5)

    def test_condition_builder_is_sparse_resumable_and_uses_runtime_authority(self) -> None:
        args = condition_builder_argv()
        self.assertIn("conditions-only", args)
        self.assertIn("--replace-existing", args)
        output_index = args.index("--output-root") + 1
        self.assertEqual(args[output_index], r"D:\TradingML\runtimes\bar_gpt\v1\build_conditions_1s")
        self.assertEqual(DataConfig().condition_status_table, "intraday_base_bars_build_status")

    def test_collated_batch_runs_complete_mixed_objective(self) -> None:
        examples = list(build_session_examples(
            ticker="AAA", local_date="2026-01-02", session=session_view(), daily=None,
            split_actions=(), config=self.data_config()
        ))[:2]
        batch = collate_examples(examples).to("cpu")
        self.assertEqual(batch.horizon_targets.shape, (2, 3, 2, len(TARGET_NAMES)))
        model_config = BarGPTConfig(d_model=32, n_layers=1, n_heads=4, n_kv_heads=2, horizon_rank=8)
        model = BarGPTV1(model_config)
        output = model(
            batch.views,
            timeframe_us=TIMEFRAME_US_BY_NAME,
            pathway_ids=PATHWAY_ID_BY_NAME,
            base_view="1s",
            origin_indices=batch.origin_indices,
            asof_indices=batch.asof_indices,
            horizon_ids=torch.arange(2),
        )
        self.assertEqual(set(output.autoregressive), {"1s", "5s", "10s", "30s", "1m", "5m", "30m", "1h"})
        self.assertEqual(set(output.latent_predictions), set(batch.views))
        self.assertNotIn("1MO", output.autoregressive)
        loss = compute_loss(output, batch, TrainConfig(), model_config.quantiles)
        self.assertTrue(torch.isfinite(loss.loss))
        loss.loss.backward()
        self.assertGreater(float(sum(parameter.grad.abs().sum() for parameter in model.parameters() if parameter.grad is not None)), 0.0)
        accumulator = ValidationAccumulator(self.data_config().horizons_us, model_config.quantiles)
        accumulator.update(output, batch, loss)
        validation = accumulator.finalize()
        self.assertEqual(validation["val/origins"], 6.0)
        self.assertIn("val/horizon_1s_median_mae", validation)
        self.assertEqual(validation["val/horizon_1s_halt_pause_within_horizon_positives"], 0.0)

    def test_cursor_advances_per_worker_only_after_consumed_batch(self) -> None:
        examples = list(build_session_examples(
            ticker="AAA", local_date="2026-01-02", session=session_view(), daily=None,
            split_actions=(), config=self.data_config()
        ))[:2]
        examples[0].worker_id = examples[1].worker_id = 2
        examples[0].unit_index = examples[1].unit_index = 11
        examples[0].block_offset, examples[1].block_offset = 3, 4
        batch = collate_examples(examples)
        cursors = _advance_cursors({2: CoverageCursor(10, 15)}, batch)
        self.assertEqual(cursors[2], CoverageCursor(11, 4))

    def test_cpu_prefetch_and_packed_adapter_preserve_contract(self) -> None:
        examples = list(build_session_examples(
            ticker="AAA", local_date="2026-01-02", session=session_view(), daily=None,
            split_actions=(), config=self.data_config()
        ))[:2]
        loader = torch.utils.data.DataLoader(examples, batch_size=2, collate_fn=collate_examples)
        batch, _wait = DeviceBatchPrefetcher(loader, torch.device("cpu"), enabled=True).next()
        self.assertEqual(batch.origin_count, 6)
        adapter = PackedBarEmbeddingAdapter(8, 5)
        values, valid = adapter(torch.randn(2, 3, 8), torch.tensor([[True, True, False], [True, False, False]]))
        self.assertEqual(values.shape, (2, 3, 5))
        self.assertTrue(torch.equal(valid, torch.tensor([[True, True, False], [True, False, False]])))

    def test_host_cache_never_holds_ready_batch_behind_slow_successor(self) -> None:
        examples = list(build_session_examples(
            ticker="AAA", local_date="2026-01-02", session=session_view(), daily=None,
            split_actions=(), config=self.data_config()
        ))[:2]
        batch = collate_examples(examples)
        release = threading.Event()

        def source():
            yield batch
            release.wait(timeout=2.0)
            yield batch

        prefetcher = DeviceBatchPrefetcher(source(), torch.device("cpu"), enabled=False, host_cache_batches=2)
        started = time.perf_counter()
        first, _wait = prefetcher.next()
        self.assertLess(time.perf_counter() - started, 0.2)
        self.assertEqual(first.origin_count, batch.origin_count)
        release.set()
        second, _wait = prefetcher.next()
        self.assertEqual(second.origin_count, batch.origin_count)
        prefetcher.close()

    def test_prefetch_close_releases_owned_loader_iterator(self) -> None:
        examples = list(build_session_examples(
            ticker="AAA", local_date="2026-01-02", session=session_view(), daily=None,
            split_actions=(), config=self.data_config()
        ))[:2]
        batch = collate_examples(examples)

        class OwnedIterator:
            def __init__(self) -> None:
                self.shutdown = False
                self.returned = False

            def __iter__(self):
                return self

            def __next__(self):
                if self.returned:
                    raise StopIteration
                self.returned = True
                return batch

            def _shutdown_workers(self) -> None:
                self.shutdown = True

        owned = OwnedIterator()
        prefetcher = DeviceBatchPrefetcher(owned, torch.device("cpu"), enabled=False)
        prefetcher.close()
        self.assertTrue(owned.shutdown)

    def test_profiler_candidate_contract_is_explicit(self) -> None:
        candidates = _parse_candidates("256:1:8:4:1,512:2:4:4:0:1")
        self.assertEqual(candidates[0].origin_bars, 256)
        self.assertTrue(candidates[0].cuda_prefetch)
        self.assertFalse(candidates[1].cuda_prefetch)
        self.assertTrue(candidates[1].compile_model)
        launcher_candidates = profile_launcher_args[profile_launcher_args.index("--candidates") + 1]
        parsed = _parse_candidates(launcher_candidates)
        shapes = [(item.origin_bars, item.microbatch, item.accumulation) for item in parsed]
        self.assertIn((4096, 16, 2), shapes)
        self.assertEqual({item.workers for item in parsed}, {12, 16, 24})

    def test_training_launcher_uses_selected_worker_owned_profile(self) -> None:
        self.assertEqual(training_launcher_args["--origin-bars-1s"], "4096")
        self.assertEqual(training_launcher_args["--batch-size"], "16")
        self.assertEqual(training_launcher_args["--gradient-accumulation-steps"], "2")
        self.assertEqual(training_launcher_args["--loader-workers"], "12")
        self.assertEqual(training_launcher_args["--ready-queue-blocks"], "128")

    def test_holdout_and_regime_resampling_are_deterministic(self) -> None:
        tickers = tuple(f"T{index:02d}" for index in range(20))
        self.assertEqual(held_out_tickers(tickers, 0.2, 17), held_out_tickers(tickers, 0.2, 17))
        self.assertEqual(len(held_out_tickers(tickers, 0.2, 17)), 4)
        examples = list(build_session_examples(
            ticker="AAA", local_date="2026-01-02", session=session_view(), daily=None,
            split_actions=(), config=self.data_config()
        ))
        for index, example in enumerate(examples):
            example.activity_regime = index % 3
        first = [item.activity_regime for item in balanced_regime_stream(iter(examples), buffer_size=6, seed=3)]
        second = [item.activity_regime for item in balanced_regime_stream(iter(examples), buffer_size=6, seed=3)]
        self.assertEqual(first, second)

    def test_ticker_worker_shards_are_disjoint_complete_and_deterministic(self) -> None:
        tickers = tuple(f"T{index:02d}" for index in range(23))
        first = worker_ticker_shards(tickers, workers=8, seed=17)
        second = worker_ticker_shards(tickers, workers=8, seed=17)
        self.assertEqual(first, second)
        flattened = [ticker for shard in first for ticker in shard]
        self.assertEqual(set(flattened), set(tickers))
        self.assertEqual(len(flattened), len(set(flattened)))

    def test_preflight_requires_continuous_certified_source_coverage(self) -> None:
        class FakeClient:
            def __init__(self, messages: str) -> None:
                self.messages = messages

            def query_tsv(self, query: str) -> str:
                if "system.tables" in query:
                    if "q_live" in query:
                        return (
                            "id_symbol_interval_v1\nmarket_ticker_event_entity_v1\nmarket_ticker_event_v1\n"
                            "market_stock_split_v1\n"
                        )
                    return (
                        "bar_gpt_1s_bars_v1_cohort_2tb\n"
                        "bar_gpt_1s_build_manifest_v1_cohort_2tb\n"
                        "bar_gpt_1s_build_manifest_v1_identity_aliases\n"
                        "daily_session_bars_by_symbol_time_v1\n"
                        "daily_session_bars_manifest_v1\n"
                        "intraday_condition_bars_by_time_ticker\n"
                        "intraday_base_bars_build_status\n"
                    )
                if "system.columns" in query:
                    return "local_date\n"
                if "daily_session_bars_manifest_v1" in query:
                    return "2019-01-01\t2020-03-01\n"
                if "current_ticker" in query:
                    return ""
                if "intraday_base_bars_build_status" in query:
                    return "60\n"
                if "intraday_condition_bars_by_time_ticker" in query and "countIf" in query:
                    return "10\t2\t2\t3\t3\n"
                return self.messages

        config = self.data_config()
        config.start_date = "2020-01-01"
        config.end_date = "2020-03-01"
        evidence = preflight(
            FakeClient("certified range [2020-01-01,2020-02-01)\ncertified range [2020-02-01,2020-03-01)\n"),  # type: ignore[arg-type]
            config,
        )
        self.assertEqual(evidence["certified_end"], "2020-03-01")
        with self.assertRaisesRegex(RuntimeError, "not continuously certified"):
            preflight(
                FakeClient("certified range [2020-01-01,2020-02-01)\ncertified range [2020-02-10,2020-03-01)\n"),  # type: ignore[arg-type]
                config,
            )

    def test_compact_terminal_keeps_status_current_work_and_durability(self) -> None:
        state = TrainingProgressState(
            run_name="smoke", device="cuda", precision="bf16", output_dir=r"D:\TradingML\runtimes\bar_gpt\v1\train\smoke",
            model_parameters=12_345, max_samples=300_000, samples_seen=125_000, batches_seen=12,
            epochs_total=3, epoch_index=2, epoch_start_origins=100_000,
            epoch_origin_budget=100_000, epoch_origins_seen=25_000,
            state="running", loss=0.123, origins_per_second=400.0, active_tickers="AAPL,SPY", active_dates="2025-01-02..2025-01-03",
            last_checkpoint="checkpoint_latest.pt", origin_bars=4_096,
            warmup_samples=30_000, schedule_samples=300_000,
            current_unit_ticker="AAPL", current_unit_month="2025-01",
            current_unit_block=75, current_unit_blocks=300,
        )
        reporter = TrainingReporter(state, layout="rich")
        output = io.StringIO()
        reporter._console = Console(file=output, width=72, force_terminal=False, color_system=None)
        reporter.messages.append("12:00:00 source certified")
        reporter._console.print(reporter._render())
        rendered = " ".join(output.getvalue().split())
        self.assertIn("running", rendered)
        self.assertIn("AAPL,SPY", rendered)
        self.assertIn("checkpoint_latest.pt", rendered)
        self.assertIn("Epoch 2/3 origins", rendered)
        self.assertIn("25,000/100,000", rendered)
        self.assertIn("AAPL 2025-01 ticker-month blocks", rendered)
        self.assertIn("75/300", rendered)
        self.assertIn("125,000/300,000 origins", rendered)
        self.assertIn("cosine", rendered)

        normal_output = io.StringIO()
        reporter._console = Console(file=normal_output, width=120, height=40, force_terminal=False, color_system=None)
        reporter._console.print(reporter._render())
        self.assertIn("Objectives", normal_output.getvalue())
        self.assertIn("Current work and durability", normal_output.getvalue())

        short_output = io.StringIO()
        reporter._console = Console(file=short_output, width=72, height=18, force_terminal=False, color_system=None)
        reporter._console.print(reporter._render())
        short_rendered = " ".join(short_output.getvalue().split())
        self.assertIn("running", short_rendered)
        self.assertIn("source certified", short_rendered)

        text_output = io.StringIO()
        with redirect_stdout(text_output):
            TrainingReporter(state, layout="text").refresh(force=True)
        self.assertNotIn("\x1b", text_output.getvalue())
        self.assertIn("unit=AAPL:2025-01", text_output.getvalue())

        failed = TrainingReporter(state, layout="none")
        failed.__exit__(RuntimeError, RuntimeError("CUDA out of memory"), None)
        self.assertEqual(state.state, "failed")
        self.assertEqual(state.last_message, "CUDA out of memory")

    def test_frozen_ridge_probe_recovers_linear_embedding_signal(self) -> None:
        torch.manual_seed(5)
        train_x = torch.randn(200, 8)
        test_x = torch.randn(80, 8)
        coefficient = torch.randn(8)
        train_y = torch.stack((train_x @ coefficient, train_x @ (-coefficient)), dim=1)
        test_y = torch.stack((test_x @ coefficient, test_x @ (-coefficient)), dim=1)
        train_mask = torch.ones_like(train_y, dtype=torch.bool)
        test_mask = torch.ones_like(test_y, dtype=torch.bool)
        probe, metrics = fit_ridge_probes(train_x, train_y, train_mask, test_x, test_y, test_mask, ridge=1e-4)
        self.assertEqual(probe["weights"].shape, (2, 9))
        self.assertGreater(min(row["r2"] for row in metrics), 0.999)


if __name__ == "__main__":
    unittest.main()
