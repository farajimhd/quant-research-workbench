from __future__ import annotations

import argparse
import unittest

import torch

from research.bar_gpt.v1.build_1s import create_target_table_sql, insert_one_second_sql
from research.bar_gpt.v1.config import BarGPTConfig
from research.bar_gpt.v1.data import BarView, causal_asof_indices, densify_one_second_view, horizon_target_indices, rollup_intraday_view
from research.bar_gpt.v1.features import MODEL_FEATURE_NAMES, project_stationary_features
from research.bar_gpt.v1.model import BarGPTV1
from research.bar_gpt.v1.loader import ClickHouseBarStreamConfig, daily_range_query, ticker_range_query
from research.bar_gpt.v1.schema import FEATURE_INDEX, FEATURE_NAMES
from research.bar_gpt.v1.targets import TARGET_NAMES, build_physical_horizon_targets


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
        self.assertIn("PREWHERE ticker = 'AAPL'", sql)
        self.assertIn("ORDER BY ticker, local_date, bucket_index", sql)
        self.assertTrue(sql.strip().endswith("FORMAT ArrowStream"))
        daily_sql = daily_range_query(
            ClickHouseBarStreamConfig(url="http://localhost:8123", user="default", password=""),
            ticker="aapl",
            start_date="2025-01-01",
            end_date="2026-01-01",
        )
        self.assertIn("PREWHERE timeframe = '1d'", daily_sql)
        self.assertNotIn("timeframe = '1w'", daily_sql)


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
        )
        self.assertEqual(targets.values.shape, (3, 2, len(TARGET_NAMES)))
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


class ModelContractTest(unittest.TestCase):
    def test_forward_shapes_and_future_causality(self) -> None:
        torch.manual_seed(7)
        config = BarGPTConfig(
            feature_dim=len(MODEL_FEATURE_NAMES),
            target_dim=6,
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
            "timeframe_ids": {"1s": 0, "5s": 1},
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
        self.assertEqual(first.horizon_quantiles.shape, (1, 4, 3, 6, 3))
        torch.testing.assert_close(first.embeddings, second.embeddings)

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


if __name__ == "__main__":
    unittest.main()
