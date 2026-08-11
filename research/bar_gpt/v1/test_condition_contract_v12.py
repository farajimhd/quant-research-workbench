from __future__ import annotations

import unittest

import polars as pl
import torch

from research.bar_gpt.v1.config import DataConfig
from research.bar_gpt.v1.direct_event_shards import (
    DirectEventSession,
    _chain_condition_sessions,
    _session_from_direct_rows,
    direct_trade_bar_query,
)
from research.bar_gpt.v1.features import MODEL_FEATURE_NAMES, project_stationary_features
from research.bar_gpt.v1.loader import TickerInterval
from research.bar_gpt.v1.schema import FEATURE_INDEX, FEATURE_NAMES, ONE_SECOND_US
from research.bar_gpt.v1.targets import TARGET_NAMES, build_physical_horizon_targets


def _frame(rows: list[dict[str, float | int | str]]) -> pl.DataFrame:
    normalized: list[dict[str, float | int | str]] = []
    for row in rows:
        values: dict[str, float | int | str] = {
            **{name: 0.0 for name in FEATURE_NAMES},
            "ticker": "AAPL",
        }
        values.update(row)
        normalized.append(values)
    return pl.DataFrame(normalized)


class ConditionContractV12Test(unittest.TestCase):
    def test_condition_only_second_is_target_authority_and_not_price_token(self) -> None:
        start = 1_609_750_800_000_000  # 2021-01-04 04:00 America/New_York
        rows = _frame([
            {
                "local_date": "2021-01-04",
                "bar_start_us": start,
                "bar_end_us": start + ONE_SECOND_US,
                "available_at_us": start + ONE_SECOND_US,
                "condition_halt_pause_count": 1,
                "condition_nonzero_count": 1,
                "condition_event_count": 1,
                "source_event_count": 1,
            },
            {
                "local_date": "2021-01-04",
                "bar_start_us": start + 2 * ONE_SECOND_US,
                "bar_end_us": start + 3 * ONE_SECOND_US,
                "available_at_us": start + 3 * ONE_SECOND_US,
                "origin_eligible": 1,
                "context_eligible": 1,
                "trade_present": 1,
                "trade_open": 100,
                "trade_high": 100,
                "trade_low": 100,
                "trade_close": 100,
                "trade_event_count": 1,
                "eligible_trade_event_count": 1,
                "source_event_count": 1,
            },
        ])
        bundle = _session_from_direct_rows(rows, device="cpu")
        self.assertIsNotNone(bundle.view)
        assert bundle.view is not None
        self.assertEqual(bundle.view.features.shape[0], 1)
        self.assertEqual(bundle.condition_flags[0, 0].item(), 1)
        self.assertEqual(bundle.view.features[0, FEATURE_INDEX["condition_halt_pause_count"]].item(), 1)
        self.assertEqual(bundle.view.features[0, FEATURE_INDEX["source_event_count"]].item(), 2)

    def test_trailing_condition_carries_to_next_trade_session(self) -> None:
        features = torch.zeros((1, len(FEATURE_NAMES)), dtype=torch.float64)
        features[0, FEATURE_INDEX["trade_event_count"]] = 1
        from research.bar_gpt.v1.data import BarView

        view = BarView(
            features=features,
            bar_start_us=torch.tensor([10]),
            bar_end_us=torch.tensor([11]),
            available_at_us=torch.tensor([11]),
        )
        width = 7
        first = DirectEventSession("2021-01-04", None, torch.zeros((57_600, 4)), torch.tensor([1, 0, 0, 0, 1, 1, 1], dtype=torch.float64))
        second = DirectEventSession("2021-01-05", view, torch.zeros((57_600, 4)), torch.zeros(width, dtype=torch.float64))
        resolved, carry = _chain_condition_sessions([first, second])
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0][1].features[0, FEATURE_INDEX["condition_halt_pause_count"]].item(), 1)
        self.assertEqual(resolved[0][1].features[0, FEATURE_INDEX["condition_nonzero_count"]].item(), 1)
        self.assertFalse(bool(carry.any()))

    def test_projection_omits_redundant_channels(self) -> None:
        self.assertEqual(len(MODEL_FEATURE_NAMES), 50)
        for name in ("trade_present", "bid_present", "ask_present", "crossed_quote_fraction"):
            self.assertNotIn(name, MODEL_FEATURE_NAMES)
        projected = project_stationary_features(torch.zeros((2, len(FEATURE_NAMES))))
        self.assertEqual(projected.shape, (2, 50))

    def test_condition_target_uses_exact_clock_not_folded_input_timestamp(self) -> None:
        raw = torch.zeros((2, len(FEATURE_NAMES)), dtype=torch.float64)
        for row in range(2):
            raw[row, FEATURE_INDEX["trade_present"]] = 1
            raw[row, FEATURE_INDEX["trade_open"]] = 100
            raw[row, FEATURE_INDEX["trade_high"]] = 100
            raw[row, FEATURE_INDEX["trade_low"]] = 100
            raw[row, FEATURE_INDEX["trade_close"]] = 100
            raw[row, FEATURE_INDEX["trade_event_count"]] = 1
        available = torch.tensor([1_000_000, 5_000_000], dtype=torch.long)
        targets = build_physical_horizon_targets(
            raw,
            torch.tensor([0]),
            torch.tensor([5_000_000]),
            base_timeframe_us=ONE_SECOND_US,
            available_at_us=available,
            coverage_end_us=6_000_000,
            condition_available_at_us=torch.tensor([3_000_000]),
            condition_flags=torch.tensor([[1.0, 0.0, 0.0, 0.0]]),
        )
        index = TARGET_NAMES.index("halt_pause_within_horizon")
        self.assertTrue(targets.mask[0, 0, index])
        self.assertEqual(targets.values[0, 0, index].item(), 1)

    def test_query_decouples_conditions_from_price_eligibility(self) -> None:
        config = DataConfig(tickers=("AAPL",), start_date="2021-01-01", end_date="2021-02-01")
        stream = type("Stream", (), {"max_threads": 4, "max_block_size": 65_536, "max_memory_usage": 1_000_000_000})()
        sql = direct_trade_bar_query(
            config,
            stream,
            ticker="AAPL",
            start_date="2021-01-01",
            end_date="2021-02-01",
            source_intervals=(TickerInterval("AAPL", "AAPL", "2021-01-01", "2021-02-01"),),
        )
        self.assertIn("countIf(condition_halt_pause_flag_event > 0)", sql)
        self.assertNotIn("event_retained AND condition_halt_pause_flag_event", sql)
        self.assertIn("HAVING eligible_trade_event_count>0 OR condition_event_count>0", sql)


if __name__ == "__main__":
    unittest.main()
