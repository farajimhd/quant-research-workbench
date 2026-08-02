from __future__ import annotations

import io
import unittest

import torch
from rich.console import Console

from research.bar_gpt.v1.config import BarGPTConfig, DataConfig, TrainConfig
from research.bar_gpt.v1.data import PATHWAY_ID_BY_NAME, TIMEFRAME_US_BY_NAME, BarView, collate_examples
from research.bar_gpt.v1.loader import balanced_regime_stream, build_session_examples, held_out_tickers
from research.bar_gpt.v1.model import BarGPTV1
from research.bar_gpt.v1.linear_probe import fit_ridge_probes
from research.bar_gpt.v1.objectives import compute_loss
from research.bar_gpt.v1.progress import TrainingProgressState, TrainingReporter
from research.bar_gpt.v1.schema import FEATURE_INDEX, FEATURE_NAMES
from research.bar_gpt.v1.train import preflight


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
            ticker="AAA", local_date="2026-01-02", session=session_view(), calendar_views={}, config=self.data_config()
        ))
        self.assertGreater(len(examples), 1)
        first = examples[0]
        self.assertEqual(first.raw_views["1s"].shape[0], 7)  # context plus origins, future halo is target-only
        self.assertEqual(first.origin_indices.tolist(), [4, 5, 6])
        self.assertEqual(first.target_support.shape[0], 9)
        self.assertEqual(first.asof_indices["5s"].tolist(), [0, 0, 0])
        self.assertTrue(torch.all(first.asof_indices["1h"] == -1))

    def test_collated_batch_runs_complete_mixed_objective(self) -> None:
        examples = list(build_session_examples(
            ticker="AAA", local_date="2026-01-02", session=session_view(), calendar_views={}, config=self.data_config()
        ))[:2]
        batch = collate_examples(examples).to("cpu")
        self.assertEqual(batch.horizon_targets.shape, (2, 3, 2, 14))
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

    def test_holdout_and_regime_resampling_are_deterministic(self) -> None:
        tickers = tuple(f"T{index:02d}" for index in range(20))
        self.assertEqual(held_out_tickers(tickers, 0.2, 17), held_out_tickers(tickers, 0.2, 17))
        self.assertEqual(len(held_out_tickers(tickers, 0.2, 17)), 4)
        examples = list(build_session_examples(
            ticker="AAA", local_date="2026-01-02", session=session_view(), calendar_views={}, config=self.data_config()
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
                        return "id_symbol_interval_v1\nmarket_ticker_event_entity_v1\n"
                    return (
                        "bar_gpt_1s_bars_v2_cohort_2tb_split_adjusted\n"
                        "bar_gpt_1s_adjustment_manifest_v2_cohort_2tb\n"
                        "bar_gpt_daily_sessions_v3_sip_adjusted\n"
                        "bar_gpt_daily_sessions_manifest_v3_sip_adjusted\n"
                    )
                if "system.columns" in query:
                    return "local_date\n"
                if "daily_sessions_manifest_v3" in query:
                    return "2019-01-01\t2020-03-01\n"
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
