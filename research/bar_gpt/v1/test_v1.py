from __future__ import annotations

import argparse
import datetime as dt
import io
import unittest
from pathlib import Path
from unittest.mock import Mock

import torch
import polars as pl
from rich.console import Console

from research.bar_gpt.v1.build_1s import (
    BuildReporter,
    _query_tsv,
    _show_create_raw,
    create_target_table_sql,
    insert_one_second_sql,
    ticker_fingerprint,
)
from research.bar_gpt.v1.cohort import (
    BAR_GPT_COHORT_2TB,
    BAR_GPT_COHORT_2TB_MANIFEST_TABLE,
    BAR_GPT_COHORT_2TB_SHA256,
    BAR_GPT_COHORT_2TB_TABLE,
    BAR_GPT_IDENTITY_QUARANTINE,
    BAR_GPT_SOURCE_ALIAS_MANIFEST_TABLE,
    BAR_GPT_SOURCE_ALIAS_TICKERS,
    BAR_GPT_TRAINING_TICKERS,
)
from research.bar_gpt.v1.config import BarGPTConfig, DataConfig
from research.bar_gpt.v1.data import BarView, causal_asof_indices, densify_one_second_view, horizon_target_indices, rollup_intraday_view
from research.bar_gpt.v1.features import MODEL_FEATURE_NAMES, project_stationary_features
from research.bar_gpt.v1.model import BarGPTV1
from research.bar_gpt.v1.loader import (
    ClickHouseBarStreamConfig,
    TickerInterval,
    daily_range_query,
    daily_session_frame_to_view,
    daily_tickers_range_query,
    ticker_range_query,
)
from research.bar_gpt.v1.schema import FEATURE_INDEX, FEATURE_NAMES
from research.bar_gpt.v1.targets import (
    AVAILABILITY_TARGET_COUNT,
    CONTINUOUS_TARGET_COUNT,
    TARGET_NAMES,
    build_physical_horizon_targets,
)
from pipelines.market_sip.events.clickhouse_build_intraday_base_bars import insert_intraday_condition_bars_sql, parse_args as parse_intraday_args
from research.bar_gpt.v1.run_build_1s import main as launcher_main
from research.bar_gpt.v1.run_build_1s import parse_args as parse_launcher_args
from research.bar_gpt.v1.run_build_1s_aliases import parse_args as parse_alias_launcher_args


def builder_args() -> argparse.Namespace:
    return argparse.Namespace(
        database="market_sip_compact",
        target_table="bar_gpt_1s_bars_v1",
        events_table_base="events",
        storage_policy="ssd_policy",
        max_threads=2,
        max_memory_usage="4G",
        max_bytes_before_external_group_by="1G",
    )


class BuilderSqlTest(unittest.TestCase):
    def test_condition_sidecar_materializes_only_flagged_event_seconds(self) -> None:
        args = parse_intraday_args([
            "--date", "2020-01-02", "--artifact-mode", "conditions-only",
            "--resolutions", "1s", "--tickers", "AAPL",
        ])
        sql = insert_intraday_condition_bars_sql(
            args=args, dates=[dt.date(2020, 1, 2)], resolutions_us=(1_000_000,)
        )
        self.assertIn("WHERE `condition_halt_pause_flag_event` > 0 OR", sql)
        self.assertNotIn("WITH FILL", sql.upper())
        self.assertNotIn("numbers(", sql)

    def test_canonical_two_tb_cohort_is_unique_and_fingerprinted(self) -> None:
        self.assertEqual(len(BAR_GPT_COHORT_2TB), 100)
        self.assertEqual(len(set(BAR_GPT_COHORT_2TB)), 100)
        self.assertEqual(BAR_GPT_COHORT_2TB_SHA256, "bb04a7c59d341d62d2fbf7758efa8ac175ae5ff4ba8400972f2517cd3896432c")
        self.assertEqual(ticker_fingerprint(tuple(sorted(BAR_GPT_COHORT_2TB))), BAR_GPT_COHORT_2TB_SHA256)
        for representative in ("SPY", "AAPL", "UVXY", "COIN", "XBIO", "ATOS"):
            self.assertIn(representative, BAR_GPT_COHORT_2TB)

    def test_launcher_and_training_data_default_to_canonical_cohort(self) -> None:
        args, extra = parse_launcher_args([])
        self.assertFalse(extra)
        self.assertEqual(tuple(args.tickers.split(",")), BAR_GPT_COHORT_2TB)
        self.assertEqual(args.target_table, BAR_GPT_COHORT_2TB_TABLE)
        self.assertEqual(args.manifest_table, BAR_GPT_COHORT_2TB_MANIFEST_TABLE)
        self.assertEqual(DataConfig().one_second_table, BAR_GPT_COHORT_2TB_TABLE)
        self.assertEqual(DataConfig().tickers, BAR_GPT_TRAINING_TICKERS)
        self.assertEqual(BAR_GPT_IDENTITY_QUARANTINE, ("GOOGL", "MOGO"))

    def test_custom_tickers_cannot_contaminate_canonical_tables(self) -> None:
        with self.assertRaisesRegex(SystemExit, "Custom --tickers require custom"):
            launcher_main(["--tickers", "AAPL"])

    def test_alias_builder_has_separate_manifest_and_raw_source_tickers(self) -> None:
        args, extra = parse_alias_launcher_args([])
        self.assertFalse(extra)
        self.assertEqual(args.start_date, "2019-01-01")
        self.assertEqual(BAR_GPT_SOURCE_ALIAS_TICKERS, ("FB",))
        self.assertEqual(DataConfig().alias_manifest_table, BAR_GPT_SOURCE_ALIAS_MANIFEST_TABLE)

    def test_metadata_queries_use_unescaped_tsv(self) -> None:
        class FakeClient:
            query = ""

            def execute(self, query: str) -> str:
                self.query = query
                return "built_at\tDateTime64(3, 'UTC')\n"

        client = FakeClient()
        rows = _query_tsv(client, "SELECT name, type FROM system.columns")  # type: ignore[arg-type]
        self.assertTrue(client.query.endswith("FORMAT TSVRaw"))
        self.assertEqual(rows, [["built_at", "DateTime64(3, 'UTC')"]])

    def test_show_create_uses_unescaped_raw_format(self) -> None:
        class FakeClient:
            query = ""

            def execute(self, query: str, *, query_id: str | None = None) -> str:
                self.query = query
                return "SETTINGS storage_policy = 'live_market_ssd'\n"

        client = FakeClient()
        ddl = _show_create_raw(client, "market_sip_compact", "bar_gpt_1s_bars_v1")  # type: ignore[arg-type]
        self.assertTrue(client.query.endswith("FORMAT TSVRaw"))
        self.assertIn("storage_policy = 'live_market_ssd'", ddl)

    def test_table_contract_uses_requested_policy_and_key(self) -> None:
        sql = create_target_table_sql(builder_args())
        self.assertIn("storage_policy = 'ssd_policy'", sql)
        self.assertIn("PARTITION BY toYYYYMM(local_date)", sql)
        self.assertIn("ORDER BY (ticker, local_date, bucket_index)", sql)
        self.assertIn("ReplacingMergeTree(built_at)", sql)

    def test_insert_is_one_second_only_and_scans_source_once(self) -> None:
        import datetime as dt

        sql = insert_one_second_sql(builder_args(), dt.date(2026, 7, 24), ("AAPL", "MSFT"))
        self.assertNotIn("arrayJoin", sql)
        self.assertNotIn("label_resolution_us", sql)
        self.assertEqual(sql.count("FROM `market_sip_compact`.`events_2026`"), 1)
        self.assertIn("microprice", sql)
        self.assertIn("queue_imbalance", sql)
        self.assertIn("ticker IN ('AAPL', 'MSFT')", sql)

    def test_training_query_is_ordered_incremental_arrow(self) -> None:
        sql = ticker_range_query(
            ClickHouseBarStreamConfig(url="http://localhost:8123", user="default", password=""),
            ticker="aapl",
            start_date="2026-07-01",
            end_date="2026-08-01",
        )
        self.assertIn("ticker = 'AAPL'", sql)
        self.assertIn("ORDER BY ticker, local_date, bucket_index", sql)
        self.assertIn("max_bytes_before_external_sort = 1073741824", sql)
        self.assertIn("optimize_read_in_order = 1", sql)
        self.assertTrue(sql.strip().endswith("FORMAT ArrowStream"))
        daily_sql = daily_range_query(
            ClickHouseBarStreamConfig(url="http://localhost:8123", user="default", password=""),
            ticker="aapl",
            start_date="2025-01-01",
            end_date="2026-01-01",
        )
        self.assertIn("source_ticker = 'AAPL'", daily_sql)
        self.assertIn("session_kind", daily_sql)
        self.assertIn("available_at_us", daily_sql)
        batched_daily_sql = daily_tickers_range_query(
            ClickHouseBarStreamConfig(url="http://localhost:8123", user="default", password=""),
            tickers=("AAPL", "MSFT"),
            start_date="2025-01-01",
            end_date="2026-01-01",
        )
        self.assertIn("source_ticker = 'AAPL'", batched_daily_sql)
        self.assertIn("source_ticker = 'MSFT'", batched_daily_sql)
        self.assertIn("ORDER BY ticker, local_date, bar_start_us", batched_daily_sql)

    def test_daily_context_uses_the_resolved_point_in_time_source_timeline(self) -> None:
        sql = daily_tickers_range_query(
            ClickHouseBarStreamConfig(url="http://localhost:8123", user="default", password=""),
            tickers=("META",),
            start_date="2019-01-01",
            end_date="2024-01-01",
            intervals_by_ticker={
                "META": (
                    TickerInterval("META", "FB", "2012-05-18", "2022-06-09"),
                    TickerInterval("META", "META", "2022-06-09", "9999-12-31"),
                )
            },
        )

        self.assertIn("source_ticker = 'FB'", sql)
        self.assertIn("session_date < toDate('2022-06-09')", sql)
        self.assertIn("source_ticker = 'META'", sql)
        self.assertIn("session_date >= toDate('2022-06-09')", sql)
        self.assertNotIn("canonical_ticker", sql)

    def test_daily_context_fails_closed_on_overlapping_reused_ticker(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "overlaps canonical identities"):
            daily_tickers_range_query(
                ClickHouseBarStreamConfig(url="http://localhost:8123", user="default", password=""),
                tickers=("CURRENT_A", "CURRENT_B"),
                start_date="2020-01-01",
                end_date="2021-01-01",
                intervals_by_ticker={
                    "CURRENT_A": (TickerInterval("CURRENT_A", "REUSED", "2019-01-01", "2020-07-01"),),
                    "CURRENT_B": (TickerInterval("CURRENT_B", "REUSED", "2020-06-01", "9999-12-31"),),
                },
            )


class BuildReporterTest(unittest.TestCase):
    @staticmethod
    def reporter() -> BuildReporter:
        reporter = BuildReporter(
            report_path=Path(r"D:\TradingML\runtimes\bar_gpt\v1\build_1s\test\build.jsonl"),
            total_days=2_922,
            interactive=True,
        )
        reporter.event = Mock()  # type: ignore[method-assign]
        return reporter

    def test_compact_render_preserves_operational_state_and_throughput(self) -> None:
        reporter = self.reporter()
        reporter.update(stage="building", day="2024-08-01", unit="batch_00127_abcdef012345")
        reporter.completed_days = 1_500
        reporter.skipped_units = 314
        reporter.record_unit_complete(output_rows=1_250_000, source_events=39_875_221, seconds=8.0)
        output = io.StringIO()
        console = Console(file=output, width=60, force_terminal=False, color_system=None)
        reporter._console = console
        console.print(reporter._render())
        rendered = output.getvalue()
        compact_text = " ".join(rendered.split())
        self.assertIn("building", compact_text)
        self.assertIn("batch_00127_abcdef012345", compact_text)
        self.assertIn("1,250,000 rows in 8.0s", compact_text)
        self.assertIn("source events 39,875,221", compact_text)
        self.assertIn("51%", compact_text)

    def test_interruption_and_failure_remain_visible(self) -> None:
        reporter = self.reporter()
        reporter.mark_interrupted("Cancellation requested")
        self.assertTrue(reporter.was_interrupted)
        self.assertEqual(reporter.stage, "interrupted")
        self.assertEqual(reporter.last_message, "Cancellation requested")
        reporter.event.assert_called_with("interrupted", message="Cancellation requested")

        failure = self.reporter()
        suppress = failure.__exit__(RuntimeError, RuntimeError("ClickHouse unavailable"), None)
        self.assertFalse(suppress)
        self.assertEqual(failure.stage, "failed")
        self.assertEqual(failure.last_message, "ClickHouse unavailable")

    def test_keyboard_interrupt_is_suppressed_for_exit_code_translation(self) -> None:
        reporter = self.reporter()
        suppress = reporter.__exit__(KeyboardInterrupt, KeyboardInterrupt(), None)
        self.assertTrue(suppress)
        self.assertTrue(reporter.was_interrupted)
        self.assertEqual(reporter.stage, "interrupted")


class TemporalContractTest(unittest.TestCase):
    def _five_seconds(self) -> BarView:
        features = torch.zeros((5, len(FEATURE_NAMES)), dtype=torch.float32)
        features[:, FEATURE_INDEX["trade_present"]] = 1
        features[:, FEATURE_INDEX["trade_open"]] = torch.arange(10, 15)
        features[:, FEATURE_INDEX["trade_high"]] = torch.arange(11, 16)
        features[:, FEATURE_INDEX["trade_low"]] = torch.arange(9, 14)
        features[:, FEATURE_INDEX["trade_close"]] = torch.arange(10.5, 15.5)
        features[:, FEATURE_INDEX["trade_size_sum"]] = 2
        starts = torch.arange(5, dtype=torch.long) * 1_000_000
        ends = starts + 1_000_000
        return BarView(features, starts, ends, ends)

    def test_coarse_bar_is_unavailable_until_its_close(self) -> None:
        base = self._five_seconds()
        coarse = rollup_intraday_view(base, 5_000_000)
        self.assertEqual(coarse.features.shape[0], 1)
        indices = causal_asof_indices(coarse.available_at_us, base.available_at_us)
        self.assertEqual(indices.tolist(), [-1, -1, -1, -1, 0])
        self.assertEqual(float(coarse.features[0, FEATURE_INDEX["trade_open"]]), 10.0)
        self.assertEqual(float(coarse.features[0, FEATURE_INDEX["trade_close"]]), 14.5)
        self.assertEqual(float(coarse.features[0, FEATURE_INDEX["trade_size_sum"]]), 10.0)

    def test_three_sessions_collapse_to_one_daily_bar_available_at_20_et(self) -> None:
        rows = []
        bounds = ((1, 2), (2, 3), (3, 4))
        for session, (start, end) in zip(("premarket", "regular", "after_hours"), bounds, strict=True):
            row = {name: 0.0 for name in FEATURE_NAMES}
            row.update(
                local_date="2026-01-02",
                ticker="AAPL",
                session_kind=session,
                bar_start_us=start,
                bar_end_us=end,
                available_at_us=end,
            )
            rows.append(row)
        rows[1]["trade_present"] = 1.0
        rows[1]["trade_open"] = 100.0
        rows[1]["trade_high"] = 102.0
        rows[1]["trade_low"] = 99.0
        rows[1]["trade_close"] = 101.0
        rows[1]["trade_size_sum"] = 10.0
        rows[1]["trade_event_count"] = 2.0
        rows[1]["source_event_count"] = 2.0
        dates, daily = daily_session_frame_to_view(pl.DataFrame(rows))
        self.assertEqual(dates, ["2026-01-02"])
        self.assertEqual(daily.available_at_us.tolist(), [4])
        self.assertEqual(float(daily.features[0, FEATURE_INDEX["trade_close"]]), 101.0)
        self.assertEqual(float(daily.features[0, FEATURE_INDEX["source_event_count"]]), 2.0)
        with self.assertRaisesRegex(ValueError, "premarket, regular, and after-hours"):
            daily_session_frame_to_view(pl.DataFrame(rows[:2]))

    def test_horizon_support_is_indexed_without_window_copies(self) -> None:
        timestamps = torch.arange(1, 11, dtype=torch.long) * 1_000_000
        indices, mask = horizon_target_indices(
            timestamps,
            torch.tensor([2_000_000, 8_000_000]),
            torch.tensor([1_000_000, 2_000_000]),
        )
        self.assertEqual(indices.tolist(), [[2, 3], [8, 9]])
        self.assertEqual(mask.tolist(), [[True, True], [True, True]])

        raw = self._five_seconds().features
        targets = build_physical_horizon_targets(
            raw,
            torch.tensor([0, 1, 3]),
            torch.tensor([1_000_000, 2_000_000]),
            condition_flags=torch.tensor(
                [[0, 0, 0, 0], [0, 0, 0, 0], [1, 0, 1, 0], [0, 0, 0, 0], [0, 1, 0, 1]],
                dtype=torch.float32,
            ),
        )
        self.assertEqual(targets.values.shape, (3, 2, len(TARGET_NAMES)))
        self.assertEqual(targets.values[0, 2 - 1, -4:].tolist(), [1.0, 0.0, 1.0, 0.0])
        self.assertFalse(bool(targets.mask[2, 1].any()))

    def test_sparse_storage_densifies_without_fabricating_families(self) -> None:
        base = self._five_seconds()
        sparse = BarView(
            features=base.features[[0, 2, 4]],
            bar_start_us=base.bar_start_us[[0, 2, 4]],
            bar_end_us=base.bar_end_us[[0, 2, 4]],
            available_at_us=base.available_at_us[[0, 2, 4]],
        )
        dense = densify_one_second_view(sparse)
        self.assertEqual(dense.features.shape[0], 5)
        self.assertEqual(dense.features[:, FEATURE_INDEX["trade_present"]].tolist(), [1, 0, 1, 0, 1])
        self.assertEqual(dense.available_at_us.tolist(), [1_000_000, 2_000_000, 3_000_000, 4_000_000, 5_000_000])

        full_clock = densify_one_second_view(sparse, clock_start_us=0, clock_end_us=6_000_000)
        self.assertEqual(full_clock.features.shape[0], 6)
        self.assertEqual(full_clock.features[:, FEATURE_INDEX["trade_present"]].tolist(), [1, 0, 1, 0, 1, 0])


class ModelContractTest(unittest.TestCase):
    def test_forward_shapes_and_future_causality(self) -> None:
        torch.manual_seed(7)
        config = BarGPTConfig(
            feature_dim=len(MODEL_FEATURE_NAMES),
            d_model=64,
            n_layers=2,
            n_heads=4,
            n_kv_heads=2,
            horizon_rank=16,
            dropout=0.0,
        )
        model = BarGPTV1(config).eval()
        fine = torch.randn(1, 8, len(MODEL_FEATURE_NAMES))
        coarse = torch.randn(1, 2, len(MODEL_FEATURE_NAMES))
        origins = torch.tensor([[0, 1, 2, 3]])
        coarse_asof = torch.tensor([[-1, -1, -1, 0]])
        kwargs = {
            "timeframe_us": {"1s": 1_000_000, "5s": 5_000_000},
            "pathway_ids": {"1s": 0, "5s": 0},
            "base_view": "1s",
            "origin_indices": origins,
            "asof_indices": {"5s": coarse_asof},
            "horizon_ids": torch.tensor([0, 1, 2]),
        }
        first = model({"1s": fine, "5s": coarse}, **kwargs)
        changed = fine.clone()
        changed[:, 5:] += 100
        second = model({"1s": changed, "5s": coarse}, **kwargs)
        self.assertEqual(first.embeddings.shape, (1, 4, 64))
        self.assertEqual(first.horizon_quantiles.shape, (1, 4, 3, CONTINUOUS_TARGET_COUNT, 3))
        self.assertEqual(first.horizon_availability_logits.shape, (1, 4, 3, AVAILABILITY_TARGET_COUNT))
        self.assertTrue(torch.all(first.horizon_quantiles[..., 1:] >= first.horizon_quantiles[..., :-1]))
        torch.testing.assert_close(first.embeddings, second.embeddings)

    def test_continuous_timeframe_value_accepts_unseen_scale(self) -> None:
        config = BarGPTConfig(d_model=64, n_layers=1, n_heads=4, n_kv_heads=2, horizon_rank=16)
        model = BarGPTV1(config).eval()
        features = torch.randn(1, 5, len(MODEL_FEATURE_NAMES))
        output = model(
            {"custom": features},
            timeframe_us={"custom": 12_000_000},
            pathway_ids={"custom": 0},
            base_view="custom",
            origin_indices=torch.tensor([[1, 2]]),
            horizon_ids=torch.tensor([0]),
        )
        self.assertEqual(output.embeddings.shape, (1, 2, 64))

    def test_stationary_projection_has_no_absolute_price_channel(self) -> None:
        raw = torch.zeros((4, len(FEATURE_NAMES)))
        for prefix in ("trade", "bid", "ask"):
            raw[:, FEATURE_INDEX[f"{prefix}_present"]] = 1
            for field in ("open", "high", "low", "close"):
                raw[:, FEATURE_INDEX[f"{prefix}_{field}"]] = 100.0
            raw[:, FEATURE_INDEX[f"{prefix}_size_sum"]] = 10
            raw[:, FEATURE_INDEX[f"{prefix}_size_squared_sum"]] = 100
            raw[:, FEATURE_INDEX[f"{prefix}_price_size_sum"]] = 1_000
            raw[:, FEATURE_INDEX[f"{prefix}_event_count"]] = 1
        projected = project_stationary_features(raw)
        scaled = raw.clone()
        for prefix in ("trade", "bid", "ask"):
            for field in ("open", "high", "low", "close"):
                scaled[:, FEATURE_INDEX[f"{prefix}_{field}"]] *= 5
            scaled[:, FEATURE_INDEX[f"{prefix}_price_size_sum"]] *= 5
        torch.testing.assert_close(projected, project_stationary_features(scaled))

    def test_daily_rows_without_intraday_moments_remain_neutral(self) -> None:
        raw = torch.zeros((2, len(FEATURE_NAMES)))
        raw[:, FEATURE_INDEX["trade_present"]] = 1
        for field in ("open", "high", "low", "close"):
            raw[:, FEATURE_INDEX[f"trade_{field}"]] = 100
        raw[:, FEATURE_INDEX["trade_size_sum"]] = 1000
        raw[:, FEATURE_INDEX["trade_event_count"]] = 10
        projected = project_stationary_features(raw)
        self.assertEqual(float(projected[1, MODEL_FEATURE_NAMES.index("trade_vwap_deviation_bps")]), 0.0)
        self.assertEqual(float(projected[1, MODEL_FEATURE_NAMES.index("trade_size_cv")]), 0.0)


if __name__ == "__main__":
    unittest.main()
