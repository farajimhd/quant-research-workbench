from __future__ import annotations

import io
import dataclasses
import datetime as dt
import http.client
import json
import multiprocessing as mp
import os
import threading
import time
import tempfile
import unittest
from contextlib import redirect_stdout
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import torch
from rich.console import Console
from rich.text import Text
from research.mlops.checkpoints import AsyncCheckpointManager, CheckpointPolicy

from research.bar_gpt.v1.audit_offline_shards import audit_shard
from research.bar_gpt.v1.config import BAR_GPT_WANDB_PROJECT, BarGPTConfig, DataConfig, ExperimentConfig, TrainConfig
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
    build_session_examples as _build_session_examples,
    frame_to_dense_window,
    held_out_tickers,
    month_units,
    origin_window_schedule,
    origin_windows_query,
    _merge_rolling_daily_view,
    validation_block_plan,
    worker_ticker_shards,
)
from research.bar_gpt.v1.sampling import CoverageCursor, SESSION_PHASES, coverage_plan_summary, select_stratified_examples
from research.bar_gpt.v1.shard_data_audit import (
    AuditBlockRef,
    LoadedAuditSample,
    PHYSICAL_HORIZON_FLOAT_ATOL_BY_TARGET,
    _labeled_tensor_comparison,
    compare_loaded_to_clickhouse,
    selected_autoregressive_targets,
    selected_targets,
    target_diagnostics,
)
from research.bar_gpt.v1.prefetch import DeviceBatchPrefetcher
from research.bar_gpt.v1.integration import PackedBarEmbeddingAdapter
from research.bar_gpt.v1.metrics import ValidationAccumulator
from research.bar_gpt.v1.features import MODEL_FEATURE_NAMES, project_stationary_features
from research.bar_gpt.v1.model import BarGPTV1
from research.bar_gpt.v1.linear_probe import fit_ridge_probes
from research.bar_gpt.v1.objectives import _weighted_mean, compute_loss
from research.bar_gpt.v1.offline_shards import (
    BuildRunLog,
    OFFLINE_SHARD_BUILD_STREAM_CONTRACT_VERSION,
    OFFLINE_SHARD_CONTRACT_VERSION,
    DEFAULT_OUTPUT_ROOT,
    ShardBuildReporter,
    assert_shard_catalog_writable,
    _storage_contract_config,
    _partition_tickers,
    _process_exit_detail,
    _resolve_cpu_threads_per_worker,
    _resolve_max_concurrent_pages,
    _ticker_worker_main,
    collate_compiled_blocks,
    compile_prepared_unit,
    compile_session,
    compile_unit,
    config_hash,
    discover_offline_units,
    load_shard,
    load_shard_storage_config,
    main as offline_shards_main,
    make_offline_dataloader,
    materialize_block,
    OfflineShardDataset,
    shard_path,
    shard_catalog_lock_path,
    shard_compatibility_hash,
    stable_unit_index,
    validate_origin_context,
    write_unit,
)
from research.bar_gpt.v1.lock_offline_shard_catalog import lock_catalog
from research.bar_gpt.v1.progress import TrainingProgressState, TrainingReporter, _format_value, _ratio_markup
from research.bar_gpt.v1.schema import FEATURE_INDEX, FEATURE_NAMES
from research.bar_gpt.v1.targets import (
    DIRECTION_TARGET_COUNT,
    DIRECTION_TARGET_NAMES,
    TARGET_NAMES,
    build_next_bar_targets,
    build_physical_horizon_targets,
)
from research.bar_gpt.v1.train import (
    _DeferredUpdateLossBuffer,
    _advance_cursors,
    _batch_eligibility_metrics,
    _checkpoint_policy,
    checkpoint_payload,
    _condition_certification_coverage,
    _forward,
    _loaders,
    _mask_inactive_condition_targets,
    _assert_finite_before_step,
    PreparedValidationBatches,
    _validation_milestones,
    _validation_checkpoint_due,
    _wandb_metric_key,
    _resolved_warmup_samples,
    restore_checkpoint,
    _resume_data_contract,
    _preserve_training_prefetch_during_validation,
    ReusableValidationBatches,
    _training_prefetcher,
    build_config as build_training_config,
    parse_args as parse_training_args,
    preflight,
    sequential_coverage_counts,
    validate,
)
from research.bar_gpt.v1.profile_train import MODEL_SIZE_PRESETS, ProfileReporter, _model_config, _parse_candidates, _sdpa_backend, parse_args as parse_profile_args
from research.bar_gpt.v1.run_build_conditions_1s import default_argv as condition_builder_argv
from research.bar_gpt.v1.run_build_offline_dataset import (
    commands as offline_dataset_commands,
    parse_args as parse_offline_dataset_args,
)
from research.bar_gpt.v1.run_profile_train import DEFAULT_ARGS as profile_launcher_args
from research.bar_gpt.v1.run_pilot_offline_shards import commands as pilot_commands, parse_args as parse_pilot_args
from research.bar_gpt.v1.overfit_pilot import _limit_ar_transitions, _limit_block_origins, _score_direction_gate
from research.bar_gpt.v1.run_train import DEFAULT_ARGS as training_launcher_args
from research.bar_gpt.v1.run_train_model_comparison import (
    COMPARISON_RUNS,
    DEFAULT_WANDB_MODE,
    _launcher_command as comparison_launcher_command,
    comparison_run_name,
    trainer_argv as comparison_trainer_argv,
)
from research.bar_gpt.v1.run_profile_model_performance import parse_args as parse_performance_profile_args, profiler_argv
from research.mlops.metrics import AsyncJsonlMetricLogger
from research.mlops.schedulers import SampleCosineRestartScheduler, SampleWarmupCosineScheduler


def _abrupt_process_exit_for_test() -> None:
    os._exit(23)


def session_view(length: int = 24, *, start_second: int = 100_000) -> BarView:
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
    starts = (torch.arange(length, dtype=torch.long) + int(start_second)) * 1_000_000
    return BarView(raw, starts, starts + 1_000_000, starts + 1_000_000)


def build_session_examples(*args, **kwargs):
    """Give synthetic tests a complete causal intraday warmup by default."""
    config = kwargs.get("config")
    if config is not None and "prior_session" not in kwargs:
        warmup = int(config.intraday_warmup_bars_1s)
        session = kwargs.get("session")
        if session is None and args:
            session = args[0]
        first_start_second = int(session.bar_start_us[0].item()) // 1_000_000
        kwargs["prior_session"] = session_view(
            warmup,
            start_second=first_start_second - warmup - 1,
        )
    return _build_session_examples(*args, **kwargs)


class LoaderTrainerContractTest(unittest.TestCase):
    def test_condition_certification_composes_disjoint_complete_ticker_artifacts(self) -> None:
        rows = "\n".join((
            "intraday_condition_bars_by_time_ticker:tickers=AAPL,MSFT\t731\t731",
            "intraday_condition_bars_by_time_ticker:tickers=GOOGL\t731\t731",
            "intraday_condition_bars_by_time_ticker:tickers=INCOMPLETE\t730\t730",
        ))

        covered, artifacts = _condition_certification_coverage(
            rows,
            condition_table="intraday_condition_bars_by_time_ticker",
            expected_days=731,
        )

        self.assertEqual(covered, {"AAPL", "MSFT", "GOOGL"})
        self.assertEqual(artifacts, 2)

    def test_training_rejects_single_ticker_even_though_shard_builds_allow_it(self) -> None:
        args = parse_training_args(["--tickers", "GOOGL"])

        with self.assertRaisesRegex(ValueError, "training requires at least two tickers"):
            build_training_config(args)

    def test_rolling_daily_cache_replaces_overlap_and_is_bounded(self) -> None:
        first = session_view(3)
        later = session_view(2)
        merged = _merge_rolling_daily_view(
            (["2026-01-01", "2026-01-02", "2026-01-03"], first),
            (["2026-01-03", "2026-01-04"], later),
            max_rows=3,
        )
        assert merged is not None
        dates, view = merged
        self.assertEqual(dates, ["2026-01-02", "2026-01-03", "2026-01-04"])
        self.assertEqual(view.features.shape[0], 3)
        # The incoming overlap is authoritative, rather than a duplicated
        # calendar bar that would distort weekly/monthly aggregation.
        self.assertTrue(torch.equal(view.features[1], later.features[0]))

    def test_cpu_finite_check_rejects_nonfinite_before_optimizer(self) -> None:
        with self.assertRaisesRegex(FloatingPointError, "non-finite training values"):
            _assert_finite_before_step(
                [torch.tensor([True, False])],
                ("loss", "train/loss"),
                ["[(AAA, 2026-01-02)]"],
                device=torch.device("cpu"),
            )

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
        self.assertTrue(torch.equal(retained.features, torch.cat((first.features[4:], second.features))))
        self.assertTrue(torch.equal(retained.bar_start_us, torch.cat((first.bar_start_us[4:], second.bar_start_us))))
        self.assertTrue(torch.equal(retained.bar_end_us, torch.cat((first.bar_end_us[4:], second.bar_end_us))))
        self.assertTrue(torch.equal(
            retained.available_at_us, torch.cat((first.available_at_us[4:], second.available_at_us)),
        ))
        self.assertEqual(len(cache._chunks), 1)
        self.assertIs(cache._chunks[0], retained)

    def test_offline_builder_runtime_parallelism_is_worker_aware(self) -> None:
        self.assertEqual(_resolve_cpu_threads_per_worker(workers=32, requested=0, logical_cpus=512), 8)
        self.assertEqual(_resolve_cpu_threads_per_worker(workers=40, requested=0, logical_cpus=512), 8)
        self.assertEqual(_resolve_cpu_threads_per_worker(workers=49, requested=0, logical_cpus=512), 6)
        self.assertEqual(_resolve_cpu_threads_per_worker(workers=49, requested=3, logical_cpus=512), 3)
        self.assertEqual(_resolve_cpu_threads_per_worker(workers=32, requested=0, logical_cpus=64), 2)
        self.assertEqual(_resolve_max_concurrent_pages(workers=32, prefetch_pages=4, requested=0), 32)
        self.assertEqual(_resolve_max_concurrent_pages(workers=4, prefetch_pages=4, requested=0), 16)
        self.assertEqual(_resolve_max_concurrent_pages(workers=49, prefetch_pages=4, requested=12), 12)
        self.assertEqual(_process_exit_detail(-1_073_741_801)["meaning"], "STATUS_NO_MEMORY")
        self.assertEqual(_process_exit_detail(-1_073_741_523)["meaning"], "STATUS_COMMITMENT_LIMIT")

    def test_offline_ticker_partition_balances_planned_blocks(self) -> None:
        partitions = _partition_tickers(
            ("AAA", "BBB", "CCC", "DDD"), 2,
            {"AAA": 10, "BBB": 9, "CCC": 2, "DDD": 1},
        )
        self.assertEqual(partitions, [("AAA", "DDD"), ("BBB", "CCC")])
        self.assertEqual({ticker for partition in partitions for ticker in partition}, {"AAA", "BBB", "CCC", "DDD"})

    def test_offline_ticker_worker_spawn_path_releases_process_at_ticker_boundary(self) -> None:
        context = mp.get_context("spawn")
        events = context.Queue(maxsize=8)
        stop = context.Event()
        gate = context.BoundedSemaphore(1)
        config = dataclasses.replace(
            self.data_config(),
            tickers=("AAA", "BBB"),
            start_date="2026-01-01",
            end_date="2026-02-01",
            validation_start_date="2026-02-01",
            validation_slices=(),
        )
        with tempfile.TemporaryDirectory() as directory:
            fault_path = Path(directory) / "worker-fatal.log"
            process = context.Process(
                target=_ticker_worker_main,
                args=(
                    0, "AAA", config, directory, frozenset({"AAA:2026-01"}),
                    True, events, stop, 1, gate, 0, str(fault_path),
                ),
            )
            process.start()
            process.join(timeout=20)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            self.assertEqual(process.exitcode, 0)
            self.assertFalse(list(Path(directory).rglob("*.pt")))
            self.assertIn("worker_completed", fault_path.read_text(encoding="utf-8"))

    def test_offline_build_log_persists_traceback_and_abrupt_exit(self) -> None:
        context = mp.get_context("spawn")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_log = BuildRunLog(root, arguments={"workers": 2, "output_root": root})
            reporter = ShardBuildReporter(
                total=2, completed=0, root=root, workers=2, layout="text", refresh=60.0,
                worker_totals=(1, 1), worker_block_totals=(10, 10), run_log=run_log,
            )
            text_output = io.StringIO()
            with redirect_stdout(text_output):
                reporter.event(("failure", 0, "RuntimeError", "caught failure", "complete traceback detail"))
                reporter.event((
                    "process_exit", 0, "AAA", 111, 1,
                    str(run_log.worker_fault_path(0, "AAA")), "fetching", "AAA:2026-01",
                ))
            self.assertEqual(reporter.failures, 1)

            process = context.Process(target=_abrupt_process_exit_for_test)
            process.start()
            process.join(timeout=20)
            self.assertFalse(process.is_alive())
            self.assertEqual(process.exitcode, 23)
            with redirect_stdout(text_output):
                reporter.event((
                    "process_exit", 1, "BBB", int(process.pid or -1), int(process.exitcode),
                    str(run_log.worker_fault_path(1, "BBB")), "assembling", "BBB:2026-02",
                    {"worker_private_bytes": 123_456},
                ))
            self.assertEqual(reporter.failures, 2)
            self.assertEqual(reporter.state, "failed")
            self.assertIn("BBB exited 23", reporter.worker_state[1][1])
            self.assertNotIn("\x1b", text_output.getvalue())
            self.assertIn("failures=2", text_output.getvalue())

            compact_output = io.StringIO()
            reporter._console = Console(
                file=compact_output, width=72, height=18, force_terminal=False, color_system=None,
            )
            reporter._console.print(reporter._render())
            compact = " ".join(compact_output.getvalue().split())
            self.assertIn("failed", compact)
            self.assertIn("BBB exited with code 23", compact)
            run_log.finalize(status="failed", exit_code=1)

            events = [json.loads(line) for line in run_log.events_path.read_text(encoding="utf-8").splitlines()]
            caught = next(item for item in events if item["event"] == "worker_exception")
            self.assertEqual(caught["traceback"], "complete traceback detail")
            abrupt = [item for item in events if item["event"] == "worker_process_exit"][-1]
            self.assertEqual(abrupt["exit_code"], 23)
            self.assertEqual(abrupt["last_state"], "assembling")
            self.assertEqual(abrupt["last_memory"]["worker_private_bytes"], 123_456)
            summary = json.loads(run_log.summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "failed")
            self.assertEqual(summary["exit_code"], 1)
            latest = json.loads(run_log.latest_path.read_text(encoding="utf-8"))
            self.assertEqual(latest["run_id"], run_log.run_id)
            self.assertEqual(latest["status"], "failed")

    def test_offline_controller_exception_is_durable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "research.bar_gpt.v1.offline_shards._run_main",
                side_effect=RuntimeError("preflight failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "preflight failed"):
                    offline_shards_main(["--execute", "--output-root", directory])
            run_directories = [
                path for path in (Path(directory) / "manifest" / "build_runs").iterdir()
                if path.is_dir()
            ]
            self.assertEqual(len(run_directories), 1)
            summary = json.loads((run_directories[0] / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "failed")
            events = [
                json.loads(line)
                for line in (run_directories[0] / "events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            failure = next(item for item in events if item["event"] == "controller_exception")
            self.assertEqual(failure["message"], "preflight failed")
            self.assertIn("RuntimeError: preflight failed", failure["traceback"])

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

    def test_reusable_validation_batches_reiterate_without_materializing(self) -> None:
        first = {"batch": 1}
        second = {"batch": 2}
        loader = torch.utils.data.DataLoader([first, second], batch_size=None, num_workers=0)
        streamed = ReusableValidationBatches(loader)
        try:
            self.assertEqual(list(streamed), [first, second])
            self.assertEqual(list(streamed), [first, second])
            self.assertTrue(streamed.ready)
            self.assertEqual(streamed.batch_count, 0)
            self.assertFalse(hasattr(streamed, "_batches"))
        finally:
            streamed.close()

    def test_prefetcher_can_release_iterator_without_stopping_reusable_workers(self) -> None:
        class OwnedIterator:
            def __init__(self) -> None:
                self.stopped = 0

            def __iter__(self):
                return self

            def __next__(self):
                raise StopIteration

            def _shutdown_workers(self) -> None:
                self.stopped += 1

        owned = OwnedIterator()
        prefetcher = DeviceBatchPrefetcher(
            owned,
            torch.device("cpu"),
            enabled=False,
            close_iterator=False,
        )
        prefetcher.close()
        self.assertEqual(owned.stopped, 0)

    def test_offline_loader_can_retain_validation_workers(self) -> None:
        dataset = object.__new__(OfflineShardDataset)
        data = DataConfig(loader_workers=2, batch_size=1, worker_prefetch_batches=1)
        loader = make_offline_dataloader(
            dataset,
            data,
            drop_last=False,
            persistent_workers=True,
        )
        self.assertTrue(loader.persistent_workers)
        self.assertEqual(loader.prefetch_factor, 1)

    def test_offline_training_prefetch_is_preserved_during_validation(self) -> None:
        offline_loader = SimpleNamespace(dataset=object.__new__(OfflineShardDataset))
        live_loader = SimpleNamespace(dataset=object())
        self.assertTrue(_preserve_training_prefetch_during_validation(offline_loader))
        self.assertFalse(_preserve_training_prefetch_during_validation(live_loader))

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

        class Gate:
            def __init__(self) -> None:
                self.acquired = 0
                self.released = 0

            def acquire(self) -> None:
                self.acquired += 1

            def release(self) -> None:
                self.released += 1

        gate = Gate()
        client = ArrowStreamClient(
            ClickHouseBarStreamConfig(
                "http://localhost:8123", "", "", retry_attempts=2,
                retry_initial_seconds=0, retry_max_seconds=0,
            ),
            query_gate=gate,
        )
        with patch("research.bar_gpt.v1.loader.request.urlopen", side_effect=responses), patch(
            "pyarrow.ipc.open_stream", side_effect=(partial_stream(None), iter(("complete",)))
        ):
            with client.record_batches("SELECT 1 FORMAT ArrowStream") as batches:
                self.assertEqual(list(batches), ["complete"])
        self.assertTrue(all(response.closed for response in responses))
        self.assertEqual(gate.acquired, 2)
        self.assertEqual(gate.released, 2)

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
        self.assertTrue(bool(torch.all(example.asof_indices["1D"] == -1)))
        batch = collate_examples([example])
        self.assertNotIn("1D", batch.autoregressive_mask)
        metrics = _batch_eligibility_metrics(batch)
        self.assertNotIn("train/context_available_1D", metrics)

    def test_calendar_context_grows_from_explicit_unavailable_history_without_lookahead(self) -> None:
        config = self.data_config()
        base = 1_800_000_000_000_000
        current_raw = session_view(24)
        current = BarView(
            current_raw.features,
            current_raw.bar_start_us + base,
            current_raw.bar_end_us + base,
            current_raw.available_at_us + base,
        )
        empty = next(build_session_examples(
            ticker="AAA", local_date="2019-01-02", session=current,
            daily=None, split_actions=(), config=config,
        ))
        for name in ("1D", "1W", "1MO"):
            self.assertEqual(empty.raw_views[name].shape[0], 1)
            self.assertTrue(torch.all(empty.raw_views[name] == 0))
            self.assertTrue(torch.all(empty.asof_indices[name] == -1))
        validate_origin_context(empty, config)
        exposed_partial = dataclasses.replace(
            empty,
            asof_indices={**empty.asof_indices, "1D": torch.zeros_like(empty.asof_indices["1D"])},
        )
        with self.assertRaisesRegex(RuntimeError, "partial calendar context"):
            validate_origin_context(exposed_partial, config)

        daily_raw = session_view(3)
        daily_starts = torch.tensor([base - 86_400_000_000, base, base + 86_400_000_000])
        daily = BarView(
            daily_raw.features,
            daily_starts,
            daily_starts + 1_000_000,
            daily_starts + 1_000_000,
        )
        partial = next(build_session_examples(
            ticker="AAA", local_date="2019-01-02", session=current,
            daily=(("2019-01-01", "2019-01-02", "2019-01-03"), daily),
            split_actions=(), config=config,
        ))
        for name in ("1D", "1W", "1MO"):
            self.assertEqual(partial.raw_views[name].shape[0], 1)
            self.assertTrue(torch.all(partial.asof_indices[name] == -1))
        complete_config = dataclasses.replace(
            config,
            calendar_context_bars=(("1D", 1), ("1W", 1), ("1MO", 1)),
            daily_context_bars=1,
        )
        complete = next(build_session_examples(
            ticker="AAA", local_date="2019-01-02", session=current,
            daily=(("2019-01-01", "2019-01-02", "2019-01-03"), daily),
            split_actions=(), config=complete_config,
        ))
        for name in ("1D", "1W", "1MO"):
            self.assertTrue(torch.all(complete.asof_indices[name] >= 0))
        validate_origin_context(complete, complete_config)

    def test_default_calendar_context_becomes_available_at_90_52_24_bars(self) -> None:
        config = self.data_config()
        day = dt.date(2026, 1, 1)
        dates: list[str] = []
        while len(dates) < config.calendar_warmup_daily_bars:
            if day.weekday() < 5:
                dates.append(day.isoformat())
            day -= dt.timedelta(days=1)
        dates.reverse()
        example = next(build_session_examples(
            ticker="AAA",
            local_date="2026-01-02",
            session=session_view(),
            daily=(dates, session_view(len(dates), start_second=0)),
            split_actions=(),
            config=config,
        ))
        for name, count in config.calendar_context_by_name.items():
            self.assertEqual(example.raw_views[name].shape[0], count)
            self.assertTrue(torch.all(example.asof_indices[name] >= count - 1), name)
        validate_origin_context(example, config)

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
            clock_start_second=72000 - config.intraday_warmup_bars_1s,
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
                origin_count_limit=config.origin_bars_1s,
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
            intraday_context_bars=(
                ("1s", 4), ("5s", 1), ("10s", 1), ("30s", 1),
                ("1m", 1), ("5m", 1), ("30m", 1), ("1h", 1),
            ),
            origin_bars_1s=3,
            min_origins_per_block=1,
            batch_size=2,
            loader_workers=0,
        )

    def test_default_intraday_context_contract_derives_28800_second_warmup(self) -> None:
        config = DataConfig()
        self.assertEqual(config.intraday_context_by_name, {
            "1s": 720,
            "5s": 360,
            "10s": 360,
            "30s": 240,
            "1m": 240,
            "5m": 96,
            "30m": 16,
            "1h": 8,
        })
        self.assertEqual(config.intraday_warmup_bars_1s, 28_800)
        self.assertEqual(config.attention_window_by_name["1s"], 721)
        self.assertEqual(config.calendar_context_by_name, {"1D": 90, "1W": 52, "1MO": 24})
        self.assertEqual(config.calendar_warmup_daily_bars, 500)
        config.validate()

    def test_every_origin_has_exact_context_independent_of_block_boundaries(self) -> None:
        base = self.data_config()
        session = session_view(13)

        def contexts(origin_bars: int) -> dict[tuple[int, str], torch.Tensor]:
            config = dataclasses.replace(base, origin_bars_1s=origin_bars)
            result: dict[tuple[int, str], torch.Tensor] = {}
            for example in build_session_examples(
                ticker="AAA",
                local_date="2026-01-02",
                session=session,
                daily=None,
                split_actions=(),
                config=config,
                include_incomplete_horizons=True,
            ):
                for position, timestamp in enumerate(example.origin_timestamps_us.tolist()):
                    base_index = int(example.origin_indices[position])
                    result[(timestamp, "1s")] = example.raw_view_start_us["1s"][
                        base_index - config.context_bars_1s:base_index + 1
                    ].clone()
                    self.assertEqual(result[(timestamp, "1s")].numel(), config.context_bars_1s + 1)
                    for name, count in config.intraday_context_by_name.items():
                        if name == "1s":
                            continue
                        end = int(example.asof_indices[name][position]) + 1
                        result[(timestamp, name)] = example.raw_view_start_us[name][end - count:end].clone()
                        self.assertEqual(result[(timestamp, name)].numel(), count)
            return result

        three_origin_blocks = contexts(3)
        five_origin_blocks = contexts(5)
        self.assertEqual(set(three_origin_blocks), set(five_origin_blocks))
        for key in three_origin_blocks:
            self.assertTrue(torch.equal(three_origin_blocks[key], five_origin_blocks[key]), key)

    def test_decoder_stack_receptive_field_does_not_exceed_configured_window(self) -> None:
        model = BarGPTV1(BarGPTConfig(
            d_model=32,
            n_layers=3,
            n_heads=4,
            n_kv_heads=2,
            horizon_rank=8,
            timeframe_fourier_dim=8,
            dropout=0.0,
        )).eval()
        features = torch.randn(1, 12, model.config.feature_dim)
        outside = features.clone()
        outside[:, 6] += 100.0  # Last token's five-token context starts at index 7.
        inside = features.clone()
        inside[:, 7] += 100.0
        with torch.no_grad():
            expected = model.encode(features, 1_000_000, 0, attention_window=5)[:, -1]
            outside_result = model.encode(outside, 1_000_000, 0, attention_window=5)[:, -1]
            inside_result = model.encode(inside, 1_000_000, 0, attention_window=5)[:, -1]
        self.assertTrue(torch.allclose(expected, outside_result, atol=1e-6, rtol=1e-6))
        self.assertFalse(torch.allclose(expected, inside_result, atol=1e-5, rtol=1e-5))

    def test_physical_target_audit_tolerance_is_bounded_by_target_semantics(self) -> None:
        stored = torch.zeros((1, 1, len(TARGET_NAMES)), dtype=torch.float32)
        rebuilt = stored.clone()
        rebuilt[..., 0] = 2e-5  # Approximately 0.002 bp near zero after inverse scaling.
        volume_index = TARGET_NAMES.index("log_trade_volume")
        rebuilt[..., volume_index] = 2e-5  # Volume retains the strict default tolerance.
        result = _labeled_tensor_comparison(
            stored,
            rebuilt,
            TARGET_NAMES,
            atol_by_field=PHYSICAL_HORIZON_FLOAT_ATOL_BY_TARGET,
        )
        self.assertEqual(result["mismatched"], 1)
        self.assertEqual(result["outside_tolerance_by_field"], {"log_trade_volume": 1})

        rebuilt[..., 0] = 6e-5
        result = _labeled_tensor_comparison(
            stored,
            rebuilt,
            TARGET_NAMES,
            atol_by_field=PHYSICAL_HORIZON_FLOAT_ATOL_BY_TARGET,
        )
        self.assertEqual(result["outside_tolerance_by_field"]["trade_open_return"], 1)

    def test_session_rollup_and_targets_are_causal_and_nonredundant(self) -> None:
        examples = list(build_session_examples(
            ticker="AAA", local_date="2026-01-02", session=session_view(), daily=None,
            split_actions=(), config=self.data_config()
        ))
        self.assertGreater(len(examples), 1)
        first = examples[0]
        self.assertEqual(first.raw_views["1s"].shape[0], 7)  # context plus origins, future halo is target-only
        self.assertEqual(first.origin_indices.tolist(), [4, 5, 6])
        self.assertEqual(first.target_support.shape[0], 28)
        self.assertTrue(torch.all(first.asof_indices["5s"] >= 0))
        self.assertTrue(torch.all(first.asof_indices["1h"] >= 0))
        batch = collate_examples([first])
        # The overnight transition into the first current-session origin is
        # not a contiguous next-bar target; the following two transitions are.
        self.assertEqual(int(batch.autoregressive_mask["1s"].any(dim=-1).sum()), 3)
        self.assertNotIn("1D", batch.autoregressive_mask)

    def test_offline_shard_round_trip_preserves_compiled_targets_without_context_duplication(self) -> None:
        config = dataclasses.replace(
            self.data_config(),
            calendar_context_bars=(("1D", 1), ("1W", 1), ("1MO", 1)),
            daily_context_bars=1,
        )
        daily = session_view(1, start_second=0)
        examples = list(build_session_examples(
            ticker="AAA", local_date="2026-01-02", session=session_view(),
            daily=(("2026-01-01",), daily),
            split_actions=(), config=config,
        ))
        for offset, example in enumerate(examples):
            example.unit_index = 7
            example.block_offset = offset
        payload = compile_unit(examples, config, "AAA:2026-01")
        pipelined = compile_prepared_unit([compile_session(examples)], config, "AAA:2026-01")
        self.assertEqual(pipelined["counts"], payload["counts"])
        self.assertTrue(torch.equal(
            pipelined["sessions"][0]["views"]["1s"]["features"],
            payload["sessions"][0]["views"]["1s"]["features"],
        ))
        session = payload["sessions"][0]
        shared_rows = int(session["views"]["1s"]["features"].shape[0])
        repeated_rows = sum(int(example.raw_views["1s"].shape[0]) for example in examples)
        self.assertLess(shared_rows, repeated_rows)
        with tempfile.TemporaryDirectory() as directory:
            evidence = write_unit(Path(directory), payload, certify_hash=True)
            shard = load_shard(Path(evidence["path"]), verify_sha256=evidence["sha256"])
            compiled = [materialize_block(shard, 0, index) for index in range(len(examples))]
            first_ref = AuditBlockRef(
                unit_key="AAA:2026-01",
                session_index=0,
                block_index=0,
                ticker="AAA",
                local_date="2026-01-02",
                block_offset=0,
            )
            sample = LoadedAuditSample(
                ref=first_ref,
                shard=shard,
                session=shard["sessions"][0],
                stored_block=shard["sessions"][0]["blocks"][0],
                block=compiled[0],
            )
            comparison = compare_loaded_to_clickhouse(sample, examples[0], data_config=config)
            self.assertTrue(comparison["match"], comparison["failed"])
            self.assertEqual(len(selected_targets(sample, 0, config)), len(config.horizons_us))
            self.assertEqual(len(selected_autoregressive_targets(sample, 0)), len(TIMEFRAME_US_BY_NAME) - 3)
            self.assertEqual(set(target_diagnostics(sample, config)), {f"{value // 1_000_000}s" for value in config.horizons_us})
            cached_batch = collate_compiled_blocks(
                compiled, horizons_us=config.horizons_us, base_timeframe_us=config.base_timeframe_us,
            ).to("cpu", non_blocking=False)
            audit = audit_shard(
                Path(evidence["path"]).with_suffix(".json"),
                verify_sha256=True,
                require_calendar_context=True,
            )
            self.assertEqual(audit["status"], "passed")
            self.assertEqual(audit["unit_key"], "AAA:2026-01")
            self.assertEqual(audit["origins"], sum(item.origin_indices.numel() for item in examples))
            self.assertTrue(audit["feature_coverage"]["1s"]["all_finite"])
            self.assertEqual(audit["feature_coverage"]["1s"]["columns_present"], len(MODEL_FEATURE_NAMES))
        live_batch = collate_examples(examples).to("cpu", non_blocking=False)
        for name in live_batch.views:
            self.assertTrue(torch.equal(cached_batch.views[name], live_batch.views[name]), name)
        for name in live_batch.autoregressive_targets:
            self.assertTrue(torch.equal(cached_batch.autoregressive_targets[name], live_batch.autoregressive_targets[name]), name)
            self.assertTrue(torch.equal(cached_batch.autoregressive_mask[name], live_batch.autoregressive_mask[name]), name)
        self.assertTrue(torch.equal(cached_batch.horizon_targets, live_batch.horizon_targets))
        self.assertTrue(torch.equal(cached_batch.horizon_mask, live_batch.horizon_mask))

    def test_overfit_population_is_bounded_and_direction_gate_is_independent(self) -> None:
        config = self.data_config()
        examples = list(build_session_examples(
            ticker="AAA", local_date="2026-01-02", session=session_view(), daily=None,
            split_actions=(), config=config,
        ))
        compiled = materialize_block(compile_unit(examples, config, "AAA:2026-01"), 0, 0)
        limited = _limit_block_origins(compiled, 2)
        self.assertEqual(limited.origin_indices.numel(), 2)
        self.assertEqual(limited.horizon_targets.shape[0], 2)
        self.assertLessEqual(limited.views["1s"].shape[0], compiled.views["1s"].shape[0])
        batch = collate_compiled_blocks(
            [limited], horizons_us=config.horizons_us, base_timeframe_us=config.base_timeframe_us,
        )
        _limit_ar_transitions(batch, 2, 0.0)
        self.assertTrue(all(int(mask[..., 0].sum()) <= 2 for mask in batch.autoregressive_mask.values()))

        metrics = {
            "after_trade_open_direction/balanced_accuracy_5s": 0.95,
            "after_trade_open_direction_quality/mcc_5s": 0.90,
            "after_ar_trade_open_return_direction_balanced/balanced_accuracy_1s": 0.91,
            "after_ar_trade_open_return_direction_mcc/mcc_1s": 0.85,
        }
        kwargs = {
            "namespace": "after",
            "physical_support": {"trade_open_return/5s": {"total": 40, "up": 20, "down": 20}},
            "ar_support": {"1s/trade_open_return": {"total": 40, "up": 20, "down": 20}},
            "minimum_examples": 32,
            "minimum_class_examples": 8,
            "minimum_balanced": 0.90,
            "minimum_mcc": 0.80,
            "minimum_ar_views": 0,
        }
        passed, records, violations = _score_direction_gate(metrics, **kwargs)
        self.assertTrue(passed)
        self.assertTrue(all(record["passed"] for record in records))
        self.assertFalse(violations)
        metrics["after_trade_open_direction/balanced_accuracy_5s"] = 0.5
        failed, _records, violations = _score_direction_gate(metrics, **kwargs)
        self.assertFalse(failed)
        self.assertTrue(violations)

    def test_strict_2026_audit_rejects_masked_calendar_context(self) -> None:
        config = self.data_config()
        examples = list(build_session_examples(
            ticker="AAA",
            local_date="2026-01-02",
            session=session_view(),
            daily=None,
            split_actions=(),
            config=config,
        ))
        with tempfile.TemporaryDirectory() as directory:
            evidence = write_unit(
                Path(directory),
                compile_unit(examples, config, "AAA:2026-01"),
                certify_hash=True,
            )
            sidecar = Path(evidence["path"]).with_suffix(".json")
            self.assertEqual(audit_shard(sidecar, verify_sha256=True)["status"], "passed")
            with self.assertRaisesRegex(RuntimeError, "required calendar context is unavailable"):
                audit_shard(sidecar, verify_sha256=True, require_calendar_context=True)

    def test_pilot_launcher_builds_two_2019_shards_and_one_2026_context_shard(self) -> None:
        args = parse_pilot_args([
            "--execute", "--force-rebuild", "--tickers", "AAPL,GOOGL",
            "--start-date", "2019-01-01", "--end-date", "2019-02-01",
        ])
        pilot_condition, build, audit, context_condition, context_build, context_audit = pilot_commands(args)
        self.assertIn("research.bar_gpt.v1.run_build_conditions_1s", pilot_condition)
        self.assertIn("research.bar_gpt.v1.run_build_conditions_1s", context_condition)
        self.assertEqual(build[build.index("--max-shards") + 1], "2")
        self.assertIn("--execute", build)
        self.assertIn("--force-rebuild", build)
        self.assertIn("offline_shards_v5_pilot", " ".join(build))
        self.assertIn("research.bar_gpt.v1.audit_offline_shards", audit)
        self.assertIn("--verify-sha256", audit)
        self.assertEqual(context_build[context_build.index("--tickers") + 1], "AAPL")
        self.assertEqual(context_build[context_build.index("--start-date") + 1], "2026-01-02")
        self.assertEqual(context_build[context_build.index("--end-date") + 1], "2026-01-03")
        self.assertEqual(context_build[context_build.index("--max-shards") + 1], "1")
        self.assertIn("--require-calendar-context", context_audit)

    def test_complete_offline_dataset_launcher_owns_disjoint_ranges(self) -> None:
        args = parse_offline_dataset_args(["--execute", "--workers", "32"])
        stages = offline_dataset_commands(args)
        self.assertEqual([label for label, _command in stages], [
            "2019-2021 condition authority",
            "2019-2021 training shards",
            "2026 condition authority",
            "2026 validation shards",
        ])
        train_conditions = stages[0][1]
        train_shards = stages[1][1]
        validation_conditions = stages[2][1]
        validation_shards = stages[3][1]
        self.assertEqual(train_conditions[train_conditions.index("--start-date") + 1], "2019-01-01")
        self.assertEqual(train_conditions[train_conditions.index("--end-date") + 1], "2022-01-01")
        self.assertEqual(train_shards[train_shards.index("--selection") + 1], "all")
        self.assertEqual(train_shards[train_shards.index("--workers") + 1], "32")
        self.assertIn("--execute", train_shards)
        self.assertEqual(validation_conditions[validation_conditions.index("--start-date") + 1], "2026-01-01")
        self.assertEqual(validation_conditions[validation_conditions.index("--end-date") + 1], "2026-08-01")
        self.assertEqual(validation_shards[validation_shards.index("--selection") + 1], "all")
        self.assertIn("--execute", validation_shards)
        self.assertNotIn("run_pilot_offline_shards", " ".join(item for _label, command in stages for item in command))

    def test_immutable_catalog_blocks_execute_before_build_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_path = root / "manifest" / "catalog.json"
            catalog_path.parent.mkdir(parents=True)
            catalog_path.write_text(json.dumps({
                "contract_version": OFFLINE_SHARD_CONTRACT_VERSION,
                "config_hash": "abc123",
                "counts": {
                    "units": 7, "complete": 6, "covered_empty": 1,
                    "bytes": 123, "blocks": 45, "origins": 67,
                },
            }), encoding="utf-8")
            proposed = lock_catalog(root, reason="test completed catalog", execute=False)
            self.assertFalse(shard_catalog_lock_path(root).exists())
            locked = lock_catalog(root, reason="test completed catalog", execute=True)
            self.assertEqual(locked["catalog_sha256"], proposed["catalog_sha256"])
            self.assertEqual(locked["policy"]["future_cohorts"], "require_new_output_root")
            with self.assertRaisesRegex(RuntimeError, "catalog is immutable"):
                assert_shard_catalog_writable(root)
            with patch("research.bar_gpt.v1.offline_shards.BuildRunLog") as build_log:
                with self.assertRaisesRegex(RuntimeError, "different --output-root"):
                    offline_shards_main(["--execute", "--output-root", str(root)])
                build_log.assert_not_called()
            # Re-locking the same unchanged catalog is idempotent.
            self.assertEqual(
                lock_catalog(root, reason="ignored on existing lock", execute=True),
                locked,
            )

    def test_offline_shard_identity_excludes_loader_batch_and_selection_settings(self) -> None:
        base = self.data_config()
        loader_variant = dataclasses.replace(
            base,
            batch_size=16,
            loader_workers=8,
            pin_memory=False,
            tickers=("AAA",),
            start_date="2026-01-01",
            end_date="2026-02-01",
            validation_start_date="2026-01-01",
        )
        geometry_variant = dataclasses.replace(base, origin_bars_1s=base.origin_bars_1s + 1)
        stream_variant = dataclasses.replace(base, loader_stream_contract_version=5)
        fetch_variant = dataclasses.replace(base, clickhouse_query_days=3, clickhouse_prefetch_pages=2)
        self.assertEqual(config_hash(base), config_hash(loader_variant))
        self.assertNotEqual(config_hash(base), config_hash(stream_variant))
        self.assertEqual(config_hash(base), config_hash(fetch_variant))
        self.assertNotEqual(config_hash(base), config_hash(geometry_variant))
        production = dataclasses.replace(DataConfig(), origin_bars_1s=4096)
        production_hash = config_hash(production)
        self.assertEqual(len(production_hash), 64)
        self.assertNotEqual(production_hash, "8851851ee01c20414c44c665e8f94ccf79d8e3aaa197fc4c4184eb377b97f619")
        self.assertEqual(shard_compatibility_hash(production), production_hash)
        self.assertEqual(OFFLINE_SHARD_BUILD_STREAM_CONTRACT_VERSION, 7)
        self.assertEqual(OFFLINE_SHARD_CONTRACT_VERSION, 5)
        self.assertEqual(DEFAULT_OUTPUT_ROOT, Path(r"D:\TradingML\runtimes\bar_gpt\v1\offline_shards_v5"))
        self.assertEqual(
            shard_path(DEFAULT_OUTPUT_ROOT, "AAA:2019-01"),
            DEFAULT_OUTPUT_ROOT / "tickers" / "AAA" / "2019" / "2019-01.pt",
        )
        self.assertEqual(
            shard_path(DEFAULT_OUTPUT_ROOT, "AAA:2026-01"),
            DEFAULT_OUTPUT_ROOT / "tickers" / "AAA" / "2026" / "2026-01.pt",
        )

    def test_shard_storage_config_is_reconstructed_from_certified_build_manifest(self) -> None:
        certified = dataclasses.replace(DataConfig(), origin_bars_1s=4096)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest" / "build_plan.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({
                "config_hash": shard_compatibility_hash(certified),
                "storage_config": _storage_contract_config(certified),
            }), encoding="utf-8")
            loaded = load_shard_storage_config(root)
            self.assertEqual(loaded.origin_bars_1s, 4096)
            self.assertEqual(shard_compatibility_hash(loaded), shard_compatibility_hash(certified))
            value = json.loads(manifest.read_text(encoding="utf-8"))
            value["config_hash"] = "0" * 64
            manifest.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "storage hash mismatch"):
                load_shard_storage_config(root)

    def test_offline_discovery_reports_missing_condition_count_metadata(self) -> None:
        builder_config = self.data_config()
        runtime_config = builder_config
        examples = list(build_session_examples(
            ticker="AAA", local_date="2026-01-02", session=session_view(), daily=None,
            split_actions=(), config=builder_config,
        ))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = write_unit(
                root, compile_unit(examples, builder_config, "AAA:2026-01"), certify_hash=True,
            )
            sidecar = Path(evidence["path"]).with_suffix(".json")
            value = json.loads(sidecar.read_text(encoding="utf-8"))
            value.pop("condition_positive_counts")
            sidecar.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "missing_condition_positive_counts=1"):
                discover_offline_units(
                    root, runtime_config, tickers=("AAA",),
                    start_date="2026-01-01", end_date="2026-02-01",
                )

    def test_offline_loader_owns_batch_size_and_validation_selection(self) -> None:
        config = self.data_config()
        examples = list(build_session_examples(
            ticker="AAA", local_date="2026-01-02", session=session_view(), daily=None,
            split_actions=(), config=config,
        ))
        later_examples = list(build_session_examples(
            ticker="AAA", local_date="2026-02-02", session=session_view(), daily=None,
            split_actions=(), config=config,
        ))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_unit(root, compile_unit(examples, config, "AAA:2026-01"), certify_hash=True)
            write_unit(root, compile_unit(later_examples, config, "AAA:2026-02"), certify_hash=True)
            units = discover_offline_units(
                root, config, tickers=("AAA",),
                start_date="2026-01-01", end_date="2026-03-01",
            )
            single_config = dataclasses.replace(config, batch_size=1, loader_workers=0, pin_memory=False)
            triple_config = dataclasses.replace(config, batch_size=3, loader_workers=0, pin_memory=False)
            single = next(iter(make_offline_dataloader(
                OfflineShardDataset(units, seed=7, shuffle_units=False), single_config, drop_last=False,
            )))
            triple = next(iter(make_offline_dataloader(
                OfflineShardDataset(units, seed=7, shuffle_units=False), triple_config, drop_last=False,
            )))
            self.assertEqual(len(single.tickers), 1)
            self.assertEqual(len(triple.tickers), 3)
            self.assertEqual(single.views["1s"].shape[1:], triple.views["1s"].shape[1:])
            validation = list(make_offline_dataloader(
                OfflineShardDataset(
                    units, seed=7, shuffle_units=False,
                    validation_slices=(("AAA", "2026-01-01", "2026-03-01"),),
                    blocks_per_validation_slice=2,
                ),
                single_config,
                drop_last=False,
            ))
            self.assertEqual(len(validation), 2)
            selected = tuple((batch.unit_indices, batch.block_offsets) for batch in validation)
            repeated = list(make_offline_dataloader(
                OfflineShardDataset(
                    units, seed=7, shuffle_units=False,
                    validation_slices=(("AAA", "2026-01-01", "2026-03-01"),),
                    blocks_per_validation_slice=2,
                ),
                single_config,
                drop_last=False,
            ))
            self.assertEqual(
                selected,
                tuple((batch.unit_indices, batch.block_offsets) for batch in repeated),
            )
            worker_config = dataclasses.replace(
                config, batch_size=2, loader_workers=2, worker_prefetch_batches=2,
                pin_memory=False, persistent_workers=False,
            )
            worker_loader = make_offline_dataloader(
                OfflineShardDataset(units, seed=7, shuffle_units=False),
                worker_config,
                drop_last=False,
            )
            worker_iterator = iter(worker_loader)
            try:
                worker_batch = next(worker_iterator)
                self.assertGreaterEqual(len(worker_batch.tickers), 1)
            finally:
                shutdown = getattr(worker_iterator, "_shutdown_workers", None)
                if callable(shutdown):
                    shutdown()
            validation_worker_loader = make_offline_dataloader(
                OfflineShardDataset(
                    units, seed=7, shuffle_units=False,
                    validation_slices=(("AAA", "2026-01-01", "2026-03-01"),),
                    blocks_per_validation_slice=2,
                ),
                worker_config,
                drop_last=False,
            )
            validation_worker_iterator = iter(validation_worker_loader)
            try:
                worker_validation = list(validation_worker_iterator)
            finally:
                shutdown = getattr(validation_worker_iterator, "_shutdown_workers", None)
                if callable(shutdown):
                    shutdown()
            self.assertEqual(sum(len(batch.tickers) for batch in worker_validation), 2)

    def test_offline_training_preserves_prefetch_across_validation(self) -> None:
        runtime_data = dataclasses.replace(
            self.data_config(),
            loader_stream_contract_version=7,
            tickers=("AAA", "BBB", "CCC"),
            start_date="2026-01-01",
            end_date="2026-03-01",
            validation_start_date="2026-01-01",
            validation_slices=(("CCC", "2026-01-01", "2026-03-01"),),
            validation_blocks_per_slice=2,
            batch_size=2,
            loader_workers=2,
            ready_queue_blocks=4,
            worker_prefetch_batches=2,
            pin_memory=False,
            persistent_workers=False,
            balance_activity_regimes=False,
        )
        builder_data = runtime_data
        model_config = BarGPTConfig(
            d_model=32, n_layers=1, n_heads=4, n_kv_heads=2,
            horizon_rank=8, timeframe_fourier_dim=8,
        )
        experiment = ExperimentConfig(
            model=model_config,
            data=runtime_data,
            train=TrainConfig(amp=False, cuda_prefetch=False, validation_batches=16),
        )

        def compiled_examples(ticker: str, local_date: str, key: str):
            examples = list(build_session_examples(
                ticker=ticker,
                local_date=local_date,
                session=session_view(),
                daily=None,
                split_actions=(),
                config=builder_data,
            ))
            for offset, example in enumerate(examples):
                example.unit_index = stable_unit_index(key)
                example.block_offset = offset
            return examples

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for ticker in ("AAA", "BBB"):
                key = f"{ticker}:2026-01"
                write_unit(
                    root,
                    compile_unit(compiled_examples(ticker, "2026-01-02", key), builder_data, key),
                    certify_hash=True,
                )
            for month, local_date in (("01", "2026-01-02"), ("02", "2026-02-02")):
                key = f"CCC:2026-{month}"
                write_unit(
                    root,
                    compile_unit(compiled_examples("CCC", local_date, key), builder_data, key),
                    certify_hash=True,
                )

            train_units = discover_offline_units(
                root, runtime_data, tickers=("AAA", "BBB"),
                start_date="2026-01-01", end_date="2026-02-01",
            )
            validation_units = discover_offline_units(
                root, runtime_data, tickers=("CCC",),
                start_date="2026-01-01", end_date="2026-03-01",
            )
            args = SimpleNamespace(dummy_data=False, data_source="offline")
            train_loader, validation_loader = _loaders(
                experiment,
                args,
                offline_train_units=train_units,
                offline_validation_units=validation_units,
            )
            device = torch.device("cpu")
            model = BarGPTV1(model_config).to(device)
            optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
            cursors: dict[int, CoverageCursor] = {}
            training = _training_prefetcher(train_loader, experiment, device)
            reusable_validation_loader = make_offline_dataloader(
                validation_loader.dataset,
                dataclasses.replace(
                    runtime_data,
                    worker_prefetch_batches=1,
                    persistent_workers=True,
                ),
                drop_last=False,
                persistent_workers=True,
            )
            validation_cache = ReusableValidationBatches(reusable_validation_loader)
            try:
                for _ in range(16):
                    batch = next(training)
                    optimizer.zero_grad(set_to_none=True)
                    _output, result = _forward(model, batch, experiment)
                    result.loss.backward()
                    optimizer.step()
                    cursors = _advance_cursors(cursors, batch, latest_per_worker=True)
                    if len(cursors) == 2:
                        break
                self.assertEqual(set(cursors), {0, 1})
                self.assertTrue(_preserve_training_prefetch_during_validation(train_loader))
                metrics = validate(model, validation_cache, experiment, device)
                self.assertEqual(metrics["validation_data/batches"], 1.0)
                self.assertGreater(metrics["validation_data/origins"], 0.0)
                first_validation_pids = tuple(
                    worker.pid for worker in reusable_validation_loader._iterator._workers
                )
                repeated_metrics = validate(model, validation_cache, experiment, device)
                second_validation_pids = tuple(
                    worker.pid for worker in reusable_validation_loader._iterator._workers
                )
                self.assertEqual(repeated_metrics["validation_data/origins"], metrics["validation_data/origins"])
                self.assertEqual(second_validation_pids, first_validation_pids)
                self.assertTrue(model.training)
                resumed_batch = next(training)
                for worker, unit, block in zip(
                    resumed_batch.worker_ids,
                    resumed_batch.unit_indices,
                    resumed_batch.block_offsets,
                    strict=True,
                ):
                    prior = cursors[int(worker)]
                    self.assertTrue(
                        int(unit) != prior.unit_index or int(block) > prior.block_offset
                    )
                optimizer.zero_grad(set_to_none=True)
                _output, resumed_result = _forward(model, resumed_batch, experiment)
                resumed_result.loss.backward()
                optimizer.step()
            finally:
                training.close()
                validation_cache.close()

    def test_offline_reporter_tracks_known_worker_totals(self) -> None:
        reporter = ShardBuildReporter(
            total=5, completed=0, root=Path("D:/runtime"), workers=2,
            layout="text", refresh=60.0, worker_totals=(2, 3), worker_block_totals=(75, 45),
        )
        reporter.event(("worker", 0, "starting", "AAA", 2, 75))
        reporter.event(("block", 0, "AAA:2026-01", "2026-01-02", 8, 8))
        reporter.event(("session", 0, "AAA:2026-01", "2026-01-02", 1, 8))
        self.assertEqual(reporter.worker_progress[0], [0, 2, 8, 75, 1, 8])
        self.assertEqual(reporter.compiled_work_blocks, 8)
        reporter.event(("session", 0, "AAA:2026-01", "2026-01-03", 2, 30))
        reporter.event(("session", 0, "BBB:2026-01", "2026-01-02", 1, 45))
        self.assertEqual(reporter.worker_progress[0][2], 45)
        self.assertEqual(reporter.compiled_work_blocks, 45)

    def test_offline_reporter_never_renders_zero_total_and_corrects_completed_total(self) -> None:
        reporter = ShardBuildReporter(
            total=2, completed=0, root=Path("D:/runtime"), workers=2,
            layout="rich", refresh=60.0,
            worker_totals=(1, 1), worker_block_totals=(8, 0),
        )
        reporter.event(("worker", 1, "starting", "AAPL", 1, 0))
        reporter.event(("session", 1, "AAPL:2019-01", "2019-01-02", 1, 8))
        output = io.StringIO()
        reporter._console = Console(file=output, width=140, height=40, force_terminal=False, color_system=None)
        reporter._console.print(reporter._render())
        self.assertIn("8/? blocks", output.getvalue())
        self.assertNotIn("8/0 blocks", output.getvalue())
        reporter.event(("worker", 1, "completed", ""))
        self.assertEqual(reporter.worker_progress[1][3], 8)
        self.assertEqual(reporter.total_work_blocks, 16)

    def test_sequential_coverage_explicitly_includes_holdout_and_derived_warmup(self) -> None:
        config = dataclasses.replace(
            DataConfig(),
            tickers=("AAPL",),
            start_date="2019-01-01",
            end_date="2019-02-01",
            validation_start_date="2019-02-01",
            validation_slices=(),
            origin_bars_1s=4096,
        )

        class FakeStream:
            def __init__(self, _config) -> None:
                pass

            def read_identity_intervals(self, tickers, **_kwargs):
                test_case.assertEqual(tickers, ("AAPL",))
                return {"AAPL": (TickerInterval("AAPL", "AAPL", "2000-01-01", "2100-01-01"),)}

        class FakeClient:
            @staticmethod
            def query_tsv(_sql: str) -> str:
                return "AAPL\t2019-01-02"

        test_case = self
        with patch("research.bar_gpt.v1.train.ArrowStreamClient", FakeStream):
            sessions, blocks, origins, units, _plan = sequential_coverage_counts(
                FakeClient(), config, tickers=("AAPL",),
            )
        self.assertEqual((sessions, blocks, origins), (1, 8, 28_800))
        self.assertEqual(units["AAPL:2019-01"], (8, 28_800))

    def test_sequential_session_emits_tail_origins_with_unavailable_horizons_masked(self) -> None:
        config = self.data_config()
        examples = list(build_session_examples(
            ticker="AAA", local_date="2026-01-02", session=session_view(11), daily=None,
            split_actions=(), config=config, include_incomplete_horizons=True,
            session_conditions=torch.zeros((11, 4)),
        ))
        self.assertEqual(sum(item.origin_indices.numel() for item in examples), 11)
        last = examples[-1]
        batch = collate_examples([last]).to("cpu", non_blocking=False)
        self.assertFalse(bool(batch.horizon_mask[0, -1, -1].any()))

    def test_intraday_autoregressive_target_uses_next_sparse_bar_across_clock_gap(self) -> None:
        raw = session_view(4).features
        starts = torch.tensor([0, 1_000_000, 86_400_000_000, 86_401_000_000])
        targets = build_next_bar_targets(raw, bar_start_us=starts)
        self.assertTrue(bool(targets.mask[0].any()))
        self.assertTrue(bool(targets.mask[1].any()))
        self.assertTrue(bool(targets.mask[2].any()))

    def test_target_builders_vectorize_blocks_with_exact_single_block_results(self) -> None:
        raw = session_view(12).features
        starts = torch.arange(12, dtype=torch.long) * 1_000_000
        next_single = build_next_bar_targets(raw, bar_start_us=starts)
        next_batch = build_next_bar_targets(
            torch.stack((raw, raw)),
            bar_start_us=torch.stack((starts, starts)),
        )
        self.assertTrue(torch.equal(next_batch.values[0], next_single.values))
        self.assertTrue(torch.equal(next_batch.mask[0], next_single.mask))
        origins = torch.tensor([4, 5, 6], dtype=torch.long)
        horizons = torch.tensor([1_000_000, 2_000_000], dtype=torch.long)
        physical_single = build_physical_horizon_targets(raw, origins, horizons)
        physical_batch = build_physical_horizon_targets(
            torch.stack((raw, raw)), torch.stack((origins, origins)), horizons
        )
        self.assertTrue(torch.equal(physical_batch.values[0], physical_single.values))
        self.assertTrue(torch.equal(physical_batch.mask[0], physical_single.mask))

    def test_prior_session_halo_enables_first_premarket_origins_without_overlap(self) -> None:
        prior = session_view(self.data_config().intraday_warmup_bars_1s)
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
        condition_index = int(torch.searchsorted(
            examples[-1].target_condition_available_at_us,
            examples[-1].origin_timestamps_us[0],
            right=True,
        ))
        examples[-1].target_condition_flags[condition_index, 0] = 1
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

    def test_restart_scheduler_applies_warmup_then_restarts_and_resumes(self) -> None:
        parameter = torch.nn.Parameter(torch.tensor(1.0))
        optimizer = torch.optim.AdamW([parameter], lr=3e-4)
        scheduler = SampleCosineRestartScheduler(
            optimizer,
            warmup_samples=100,
            cycle_samples=1_000,
            minimum_lr=3e-5,
            restart_decay=0.98,
        )
        self.assertAlmostEqual(optimizer.param_groups[0]["lr"], 3e-5)
        scheduler.step(50)
        self.assertAlmostEqual(optimizer.param_groups[0]["lr"], 1.65e-4)
        scheduler.step(100)
        self.assertAlmostEqual(optimizer.param_groups[0]["lr"], 3e-4)
        scheduler.step(600)
        self.assertAlmostEqual(optimizer.param_groups[0]["lr"], 1.65e-4)
        scheduler.step(1_100)
        expected_restart_peak = 3e-5 + (3e-4 - 3e-5) * 0.98
        self.assertAlmostEqual(optimizer.param_groups[0]["lr"], expected_restart_peak)

        state = scheduler.state_dict()
        resumed_optimizer = torch.optim.AdamW([torch.nn.Parameter(torch.tensor(1.0))], lr=3e-4)
        resumed = SampleCosineRestartScheduler(
            resumed_optimizer,
            warmup_samples=100,
            cycle_samples=1_000,
            minimum_lr=3e-5,
            restart_decay=0.98,
        )
        resumed.load_state_dict(state)
        self.assertEqual(resumed.samples_seen, 1_100)
        self.assertAlmostEqual(resumed_optimizer.param_groups[0]["lr"], expected_restart_peak)

    def test_long_run_defaults_use_profiled_shape_and_fractional_warmup(self) -> None:
        self.assertEqual(training_launcher_args["--data-source"], "offline")
        self.assertTrue(training_launcher_args["--offline-shard-root"].endswith("offline_shards_v5"))
        self.assertEqual(training_launcher_args["--start-date"], "2019-01-01")
        self.assertEqual(training_launcher_args["--origin-bars-1s"], "4096")
        self.assertEqual(training_launcher_args["--offline-train-end-date"], "2022-01-01")
        self.assertEqual(training_launcher_args["--batch-size"], "32")
        self.assertEqual(training_launcher_args["--validation-blocks-per-slice"], "2")
        self.assertEqual(training_launcher_args["--gradient-accumulation-steps"], "1")
        self.assertEqual(training_launcher_args["--epochs"], "1")
        self.assertEqual(training_launcher_args["--checkpoint-validation-evaluations"], "1")
        self.assertNotIn("--checkpoint-latest-samples", training_launcher_args)
        self.assertEqual(training_launcher_args["--wandb-project"], BAR_GPT_WANDB_PROJECT)
        self.assertEqual(training_launcher_args["--loader-workers"], "16")
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
        config = TrainConfig(checkpoint_validation_evaluations=3)
        policy = _checkpoint_policy(config)
        self.assertFalse(policy.save_best_train)
        self.assertTrue(policy.save_best_val)
        self.assertEqual(policy.latest_steps, 1)
        self.assertEqual(policy.archive_steps, 0)
        self.assertEqual(policy.clock_name, "validation_evaluation")
        self.assertFalse(_validation_checkpoint_due(0, 1))
        self.assertTrue(_validation_checkpoint_due(1, 1))
        self.assertTrue(_validation_checkpoint_due(3, 3))
        self.assertFalse(_validation_checkpoint_due(4, 3))

    def test_checkpoint_disk_write_runs_on_background_worker(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def blocking_save(_payload: object, _path: Path) -> None:
            started.set()
            if not release.wait(timeout=5):
                raise TimeoutError("test did not release checkpoint writer")

        policy = CheckpointPolicy(
            latest_steps=1,
            archive_steps=0,
            save_best_train=False,
            save_best_val=False,
            archive_on_force=False,
        )
        with tempfile.TemporaryDirectory() as directory, patch(
            "research.mlops.checkpoints.atomic_torch_save", side_effect=blocking_save
        ):
            root = Path(directory)
            manager = AsyncCheckpointManager(root / "checkpoints", root / "manifest.jsonl", policy)
            manager.maybe_save(step=1, payload={"weight": torch.ones(1)}, force=True)
            self.assertTrue(started.wait(timeout=2))
            self.assertTrue(manager.worker.is_alive())
            release.set()
            manager.close(wait=True, timeout=5)
            self.assertFalse(manager.worker.is_alive())

    def test_checkpoint_worker_failure_is_raised_to_training(self) -> None:
        failed = threading.Event()

        def failing_save(_payload: object, _path: Path) -> None:
            failed.set()
            raise OSError("disk unavailable")

        policy = CheckpointPolicy(
            latest_steps=1, archive_steps=0,
            save_best_train=False, save_best_val=False,
            archive_on_force=False,
        )
        with tempfile.TemporaryDirectory() as directory, patch(
            "research.mlops.checkpoints.atomic_torch_save", side_effect=failing_save
        ):
            root = Path(directory)
            manager = AsyncCheckpointManager(root / "checkpoints", root / "manifest.jsonl", policy)
            self.assertTrue(manager.maybe_save(step=1, payload={"weight": torch.ones(1)}, force=True))
            self.assertTrue(failed.wait(timeout=2))
            manager.worker.join(timeout=2)
            with self.assertRaisesRegex(RuntimeError, "asynchronous checkpoint writer failed"):
                manager.close(wait=True, timeout=2)

    def test_validation_checkpoint_roundtrip_restores_training_state(self) -> None:
        model_config = BarGPTConfig(
            d_model=32, n_layers=1, n_heads=4, n_kv_heads=2,
            horizon_rank=8, timeframe_fourier_dim=8,
        )
        config = ExperimentConfig(
            model=model_config,
            data=self.data_config(),
            train=TrainConfig(amp=False, cuda_prefetch=False),
        )
        model = BarGPTV1(model_config)
        optimizer = torch.optim.AdamW(model.parameters(), lr=3e-4)
        scheduler = SampleCosineRestartScheduler(
            optimizer, warmup_samples=10, cycle_samples=100,
            minimum_lr=3e-5, restart_decay=0.98,
        )
        scaler = torch.amp.GradScaler("cuda", enabled=False)
        expected = {key: value.detach().clone() for key, value in model.state_dict().items()}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = AsyncCheckpointManager(
                root / "checkpoints", root / "checkpoint_manifest.jsonl", _checkpoint_policy(config.train)
            )
            payload = checkpoint_payload(
                model, optimizer, scaler, scheduler, manager, config,
                samples_seen=131_072, batches_seen=1, optimizer_steps=1,
                blocks_seen=32, units_seen={"AAA:2019-01"}, condition_blocks_seen=2,
                epoch=0, epoch_start_samples=0,
                data_cursors={0: CoverageCursor(11, 7)}, plan_hash="plan-1",
                last_checkpoint_samples=131_072, validation_evaluations_completed=1,
                wandb_run_id="wandb-1", validation_runs_in_epoch=1,
                last_validation_samples=131_072,
            )
            manager.maybe_save(
                step=1, payload=payload,
                train_metrics={"train/loss": 1.0},
                val_metrics={"validation_loss/total": 1.0},
                force=True,
            )
            manager.close(wait=True, timeout=10)
            self.assertFalse(manager.worker.is_alive())
            with torch.no_grad():
                for parameter in model.parameters():
                    parameter.zero_()
            restored = restore_checkpoint(
                str(root / "checkpoints" / "checkpoint_latest.pt"),
                model, optimizer, scaler, scheduler, torch.device("cpu"), config, "plan-1",
            )
        self.assertEqual(restored["samples_seen"], 131_072)
        self.assertEqual(restored["validation_evaluations_completed"], 1)
        self.assertEqual(restored["last_checkpoint_samples"], 131_072)
        for key, value in model.state_dict().items():
            torch.testing.assert_close(value, expected[key])

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
        self.assertEqual(args[args.index("--start-date") + 1], "2019-01-01")
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
        self.assertEqual(set(output.autoregressive_direction_logits), set(output.autoregressive))
        self.assertEqual(output.horizon_direction_logits.shape, (*batch.horizon_targets.shape[:-1], DIRECTION_TARGET_COUNT))
        self.assertEqual(set(output.latent_predictions), set(batch.views))
        self.assertNotIn("1MO", output.autoregressive)
        loss = compute_loss(output, batch, TrainConfig(), model_config.quantiles)
        self.assertTrue(torch.isfinite(loss.loss))
        self.assertIn("train/loss_ar_direction", loss.metrics)
        self.assertIn("train/loss_horizon_direction", loss.metrics)
        loss.loss.backward()
        self.assertGreater(float(sum(parameter.grad.abs().sum() for parameter in model.parameters() if parameter.grad is not None)), 0.0)
        self.assertGreater(float(model.autoregressive_direction_head.weight.grad.abs().sum()), 0.0)
        self.assertGreater(float(model.horizon_direction_head.weight.grad.abs().sum()), 0.0)
        self.assertTrue(bool(torch.all(model.horizon_direction_head.weight.grad.abs().sum(dim=1) > 0)))
        accumulator = ValidationAccumulator(self.data_config().horizons_us, model_config.quantiles)
        accumulator.update(output, batch, loss)
        validation = accumulator.finalize()
        self.assertEqual(validation["validation_data/origins"], 6.0)
        self.assertIn("validation_trade_close_return_error/mae_bps_1s", validation)
        self.assertIn("validation_trade_close_direction/balanced_accuracy_1s", validation)
        self.assertIn("validation_trade_close_direction_quality/mcc_1s", validation)
        self.assertIn("validation_trade_close_direction/accuracy_1s", validation)
        self.assertIn("validation_trade_close_direction_quality/neutral_fraction_1s", validation)
        self.assertIn("validation_ar_direction_balanced/balanced_accuracy_5s", validation)
        self.assertIn("validation_ar_direction_mcc/mcc_5s", validation)
        self.assertIn("validation_ar_direction_neutral/neutral_fraction_1s", validation)
        self.assertEqual(validation["validation_condition_halt_pause/positives_1s"], 0.0)
        grouped_counts: dict[str, int] = {}
        for key in validation:
            group = key.split("/", 1)[0]
            grouped_counts[group] = grouped_counts.get(group, 0) + 1
        self.assertLessEqual(max(grouped_counts.values()), 16)

    def test_endpoint_mae_is_reported_in_inverse_transformed_basis_points(self) -> None:
        examples = list(build_session_examples(
            ticker="AAA", local_date="2026-01-02", session=session_view(), daily=None,
            split_actions=(), config=self.data_config()
        ))[:1]
        batch = collate_examples(examples).to("cpu")
        model_config = BarGPTConfig(d_model=32, n_layers=1, n_heads=4, n_kv_heads=2, horizon_rank=8)
        output = BarGPTV1(model_config)(
            batch.views,
            timeframe_us=TIMEFRAME_US_BY_NAME,
            pathway_ids=PATHWAY_ID_BY_NAME,
            base_view="1s",
            origin_indices=batch.origin_indices,
            asof_indices=batch.asof_indices,
            horizon_ids=torch.arange(len(self.data_config().horizons_us)),
        )
        assert output.horizon_quantiles is not None and batch.horizon_targets is not None
        median_index = model_config.quantiles.index(0.5)
        batch.horizon_targets[..., 0] = 0.0
        output.horizon_quantiles[..., 0, median_index] = torch.asinh(torch.tensor(0.01))
        loss = compute_loss(output, batch, TrainConfig(), model_config.quantiles)
        accumulator = ValidationAccumulator(
            self.data_config().horizons_us,
            model_config.quantiles,
            include_condition_metrics=False,
            include_ranking_metrics=False,
            include_confidence_metrics=False,
        )
        accumulator.update(output, batch, loss)
        metrics = accumulator.finalize()
        self.assertAlmostEqual(metrics["validation_trade_open_return_error/mae_bps_1s"], 1.0, places=5)

    def test_deferred_update_losses_preserve_each_update_and_async_logging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logger = AsyncJsonlMetricLogger(Path(directory) / "metrics.jsonl")
            buffer = _DeferredUpdateLossBuffer()
            buffer.append(
                {"train/loss": torch.tensor(8.0), "train/gradient_norm": torch.tensor(3.0)},
                origins=4,
                step=4,
                metadata={"train/optimizer_steps": 1.0},
            )
            buffer.append(
                {"train/loss": torch.tensor(20.0), "train/gradient_norm": torch.tensor(5.0)},
                origins=4,
                step=8,
                metadata={"train/optimizer_steps": 2.0},
            )
            buffer.flush(logger)
            logger.close()
            rows = [json.loads(line) for line in (Path(directory) / "metrics.jsonl").read_text().splitlines()]
        self.assertEqual([row["step"] for row in rows], [4, 8])
        self.assertEqual([row["train/loss"] for row in rows], [2.0, 5.0])
        self.assertEqual([row["train/gradient_norm"] for row in rows], [3.0, 5.0])

    def test_wandb_metric_categories_are_first_level_and_bounded(self) -> None:
        self.assertEqual(_wandb_metric_key("train/loss"), "train_loss/total")
        self.assertEqual(_wandb_metric_key("train/loss_horizon"), "train_loss/horizon")
        self.assertEqual(_wandb_metric_key("train/loader_wait_seconds"), "train_runtime/loader_wait_seconds")
        self.assertEqual(_wandb_metric_key("val/loss"), "validation_loss/total")
        self.assertEqual(TrainConfig().training_metrics_interval_samples, 8_388_608)

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
        self.assertEqual(candidates[0].model_size, "current")
        self.assertTrue(candidates[0].cuda_prefetch)
        self.assertFalse(candidates[1].cuda_prefetch)
        self.assertTrue(candidates[1].compile_model)
        joint = _parse_candidates("xlarge:4096:2:1:16:1:0")
        self.assertEqual(joint[0].model_size, "xlarge")
        self.assertEqual(joint[0].microbatch, 2)
        launcher_candidates = profile_launcher_args[profile_launcher_args.index("--candidates") + 1]
        parsed = _parse_candidates(launcher_candidates)
        self.assertEqual({item.model_size for item in parsed}, {"current", "medium", "large", "xlarge"})
        self.assertNotIn("small", {item.model_size for item in parsed})
        self.assertEqual({item.workers for item in parsed}, {16})
        self.assertEqual({item.microbatch for item in parsed if item.model_size == "current"}, {8, 16, 24, 32})
        self.assertEqual({item.microbatch for item in parsed if item.model_size == "xlarge"}, {1, 2, 4, 8})
        self.assertEqual(profile_launcher_args[profile_launcher_args.index("--target-effective-blocks") + 1], "32")
        resolved = {name: _model_config(_parse_candidates(f"{name}:4096:1:1:16:1:0")[0]) for name in MODEL_SIZE_PRESETS}
        self.assertEqual((resolved["current"].d_model, resolved["current"].n_layers), (384, 8))
        self.assertEqual((resolved["xlarge"].d_model, resolved["xlarge"].n_layers), (1024, 16))
        self.assertEqual({config.dropout for config in resolved.values()}, {0.08})

    def test_performance_profiler_uses_selected_training_shape_and_readable_output(self) -> None:
        launcher_args = parse_performance_profile_args(["--model-size", "medium", "--progress-layout", "text"])
        resolved = profiler_argv(launcher_args)
        candidates = _parse_candidates(resolved[resolved.index("--candidates") + 1])
        self.assertEqual(len(candidates), 1)
        self.assertEqual((candidates[0].model_size, candidates[0].microbatch, candidates[0].accumulation), ("medium", 16, 2))
        profile_args = parse_profile_args(resolved)
        stream = io.StringIO()
        with redirect_stdout(stream):
            ProfileReporter("text").configuration(profile_args, candidates, torch.device("cpu"))
        rendered = stream.getvalue()
        self.assertIn("W&B                 disabled", rendered)
        self.assertIn("SDPA kernel audit", rendered)
        self.assertIn("Scheduler", rendered)
        self.assertIn("linear warm-up 1.0% from LR 3e-05 to 0.0003", rendered)
        self.assertNotIn("Scheduler warning", rendered)
        self.assertIn("medium", rendered)
        disabled_args = parse_profile_args([*resolved, "--no-sdpa-audit"])
        disabled_stream = io.StringIO()
        with redirect_stdout(disabled_stream):
            ProfileReporter("text").configuration(disabled_args, candidates, torch.device("cpu"))
        self.assertIn("SDPA kernel audit   disabled", disabled_stream.getvalue())

    def test_profiler_classifies_concrete_sdpa_forward_backends_only(self) -> None:
        self.assertEqual(_sdpa_backend("aten::_scaled_dot_product_flash_attention"), "flash")
        self.assertEqual(_sdpa_backend("aten::_scaled_dot_product_efficient_attention"), "memory_efficient")
        self.assertEqual(_sdpa_backend("aten::_scaled_dot_product_cudnn_attention"), "cudnn")
        self.assertEqual(_sdpa_backend("aten::_scaled_dot_product_attention_math"), "math")
        self.assertIsNone(_sdpa_backend("aten::scaled_dot_product_attention"))
        self.assertIsNone(_sdpa_backend("aten::_scaled_dot_product_flash_attention_backward"))

    def test_training_launcher_uses_selected_worker_owned_profile(self) -> None:
        self.assertEqual(training_launcher_args["--origin-bars-1s"], "4096")
        self.assertEqual(training_launcher_args["--batch-size"], "32")
        self.assertEqual(training_launcher_args["--gradient-accumulation-steps"], "1")
        self.assertEqual(training_launcher_args["--loader-workers"], "16")
        self.assertEqual(training_launcher_args["--ready-queue-blocks"], "1024")
        self.assertEqual(training_launcher_args["--worker-prefetch-batches"], "8")
        self.assertEqual(training_launcher_args["--wandb-mode"], "online")

    def test_offline_training_loads_runtime_secrets_before_wandb_initialization(self) -> None:
        source = Path(__file__).with_name("train.py").read_text(encoding="utf-8")
        environment_load = source.index(
            "load_env_files(discover_clickhouse_env_files(), verbose=True)"
        )
        config_build = source.index("config = build_config(args)", environment_load)
        wandb_init = source.index("wandb_run = init_wandb(", config_build)
        self.assertLess(environment_load, config_build)
        self.assertLess(config_build, wandb_init)
        self.assertEqual(
            source.count("load_env_files(discover_clickhouse_env_files(), verbose=True)"),
            1,
        )

    def test_comparison_launcher_uses_online_wandb_without_normal_cli_noise(self) -> None:
        self.assertEqual(DEFAULT_WANDB_MODE, "online")
        parsed = parse_training_args(comparison_trainer_argv("current", run_stamp="fixed"))
        self.assertEqual(parsed.wandb_mode, "online")
        normal = comparison_launcher_command(
            "current", run_stamp="fixed", wandb_mode=DEFAULT_WANDB_MODE, execute=True
        )
        self.assertNotIn("--wandb-mode", normal)
        offline = comparison_launcher_command(
            "current", run_stamp="fixed", wandb_mode="offline", execute=True
        )
        self.assertEqual(offline[offline.index("--wandb-mode") + 1], "offline")

    def test_one_epoch_comparison_runs_match_profiled_winners(self) -> None:
        expected = {
            "current": (384, 8, 8, 4, 32, 1),
            "medium": (512, 12, 8, 4, 16, 2),
            "large": (768, 12, 12, 4, 8, 4),
            "xlarge": (1024, 16, 16, 8, 8, 4),
        }
        names = set()
        for model_size, values in expected.items():
            args = comparison_trainer_argv(model_size, run_stamp="fixed", wandb_mode="offline")
            parsed = parse_training_args(args)
            actual = (
                parsed.d_model,
                parsed.n_layers,
                parsed.n_heads,
                parsed.n_kv_heads,
                parsed.batch_size,
                parsed.gradient_accumulation_steps,
            )
            self.assertEqual(actual, values)
            self.assertEqual(parsed.epochs, 1)
            self.assertEqual(parsed.offline_train_end_date, "2022-01-01")
            self.assertEqual(parsed.wandb_project, BAR_GPT_WANDB_PROJECT)
            self.assertEqual(COMPARISON_RUNS[model_size].effective_blocks, 32)
            names.add(comparison_run_name(model_size, "fixed"))
        self.assertEqual(len(names), 4)

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
                if "intraday_base_bars_build_status" in query and "GROUP BY artifact_name" in query:
                    return "intraday_condition_bars_by_time_ticker:tickers=AAA,BBB,CCC\t60\t60\n"
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

    def test_training_dashboard_keeps_a_fixed_complete_schema(self) -> None:
        ratio = Text.from_markup(_ratio_markup(12, 34))
        self.assertEqual(ratio.plain, "12/34")
        ratio_styles = {str(span.style) for span in ratio.spans}
        self.assertTrue({"bold bright_cyan", "bold bright_yellow", "bold bright_magenta"} <= ratio_styles)
        rate = Text.from_markup(_format_value(65_114, "rate"))
        self.assertEqual(rate.plain, "65,114 origins/s")
        self.assertTrue(any(str(span.style) == "dim" and rate.plain[span.start:span.end] == "origins/s" for span in rate.spans))

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
        reporter.update(
            {
                "train/loss": 0.123,
                "train/loss_autoregressive": 0.08,
                "train/gradient_norm": 0.75,
                "train/condition_positive_rate": 0.01,
                "train/amp_scale": 1.0,
            }
        )
        reporter.validation(
            {
                "validation_loss/total": 0.2,
                "validation_return/mae_bps_macro": 7.5,
                "validation_direction/balanced_accuracy_macro": 0.56,
                "validation_direction/mcc_macro": 0.12,
                "validation_data/origins": 802_816,
            }
        )
        output = io.StringIO()
        reporter._console = Console(file=output, width=160, height=42, force_terminal=False, color_system=None)
        reporter.messages.append("12:00:00 source certified")
        reporter._console.print(reporter._render())
        rendered_lines = output.getvalue().splitlines()
        self.assertTrue(rendered_lines)
        self.assertTrue(all(len(line) >= 159 for line in rendered_lines))
        self.assertTrue(any(line.startswith("│State") for line in rendered_lines))
        objective_line = next(line for line in rendered_lines if "Total" in line and "Gradient norm" in line)
        self.assertIn(objective_line.index("Gradient norm"), range(40, 45))
        self.assertIn(objective_line.index("Total loss"), range(80, 85))
        rendered = " ".join(output.getvalue().split())
        self.assertIn("running", rendered)
        self.assertIn("AAPL,SPY", rendered)
        self.assertIn("checkpoint_latest", rendered)
        self.assertIn("Epoch 2/3", rendered)
        self.assertIn("25,000/100,000", rendered)
        self.assertIn("75/300", rendered)
        self.assertIn("125,000/300,000", rendered)
        self.assertIn("cosine", rendered)
        for heading in (
            "Progress and ETA",
            "Training loss and metrics",
            "Validation scorecard",
            "Optimization and runtime",
            "Data and durability",
            "Recent events",
            "Trade OHLC MAE",
            "Trade balanced",
            "MCC",
            "Trade rank",
            "Overall speed",
            "Training loss and metrics",
        ):
            self.assertIn(heading, output.getvalue())

        empty_state = TrainingProgressState(
            run_name="empty", device="cuda", precision="bf16", output_dir="-",
            model_parameters=0, max_samples=0,
        )
        empty_reporter = TrainingReporter(empty_state, layout="rich")
        empty_output = io.StringIO()
        empty_reporter._console = Console(
            file=empty_output, width=160, height=42, force_terminal=False, color_system=None
        )
        empty_reporter._console.print(empty_reporter.render())
        self.assertIn("Training loss and metrics", empty_output.getvalue())
        self.assertIn("Validation scorecard", empty_output.getvalue())
        self.assertIn("Latent prediction", empty_output.getvalue())
        self.assertIn("Trade rank", empty_output.getvalue())

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
