from __future__ import annotations

import io
import datetime as dt
import unittest
from copy import deepcopy

import torch
from rich.console import Console

from research.bar_gpt.v1.config import BarGPTConfig, DataConfig, TrainConfig
from research.bar_gpt.v1.data import PATHWAY_ID_BY_NAME, TIMEFRAME_US_BY_NAME, BarView, collate_examples
from research.bar_gpt.v1.loader import balanced_regime_stream, build_session_examples, held_out_tickers, month_units
from research.bar_gpt.v1.sampling import CoverageCursor, SESSION_PHASES, coverage_plan_summary, select_stratified_examples
from research.bar_gpt.v1.prefetch import DeviceBatchPrefetcher
from research.bar_gpt.v1.integration import PackedBarEmbeddingAdapter
from research.bar_gpt.v1.metrics import ValidationAccumulator
from research.bar_gpt.v1.features import MODEL_FEATURE_NAMES, project_stationary_features
from research.bar_gpt.v1.model import BarGPTV1
from research.bar_gpt.v1.linear_probe import fit_ridge_probes
from research.bar_gpt.v1.objectives import compute_loss
from research.bar_gpt.v1.progress import TrainingProgressState, TrainingReporter
from research.bar_gpt.v1.schema import FEATURE_INDEX, FEATURE_NAMES
from research.bar_gpt.v1.targets import TARGET_NAMES
from research.bar_gpt.v1.train import _advance_cursors, preflight
from research.bar_gpt.v1.profile_train import _parse_candidates
from research.bar_gpt.v1.run_build_conditions_1s import default_argv as condition_builder_argv
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

    def test_profiler_candidate_contract_is_explicit(self) -> None:
        candidates = _parse_candidates("256:1:8:4:1,512:2:4:4:0:1")
        self.assertEqual(candidates[0].origin_bars, 256)
        self.assertTrue(candidates[0].cuda_prefetch)
        self.assertFalse(candidates[1].cuda_prefetch)
        self.assertTrue(candidates[1].compile_model)

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
            model_parameters=12_345, max_samples=100_000, samples_seen=25_000, batches_seen=12,
            state="running", loss=0.123, origins_per_second=400.0, active_tickers="AAPL,SPY", active_dates="2025-01-02..2025-01-03",
            last_checkpoint="checkpoint_latest.pt",
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
        self.assertIn("25,000/100,000", rendered)

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
