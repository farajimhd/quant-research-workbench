from __future__ import annotations

import math
import unittest

import torch

from research.bar_gpt.v1.corporate_actions import (
    SplitAction,
    cumulative_share_factors,
    normalize_features_to_anchor,
)
from research.bar_gpt.v1.schema import FEATURE_INDEX, FEATURE_NAMES
from research.bar_gpt.v1.targets import build_physical_horizon_targets
from research.bar_gpt.v1.loader import provider_timeline_intervals


def raw_rows(prices: tuple[float, ...], sizes: tuple[float, ...]) -> torch.Tensor:
    raw = torch.zeros((len(prices), len(FEATURE_NAMES)), dtype=torch.float32)
    for row, (price, size) in enumerate(zip(prices, sizes)):
        for family in ("trade", "bid", "ask"):
            raw[row, FEATURE_INDEX[f"{family}_present"]] = 1
            for field in ("open", "high", "low", "close"):
                raw[row, FEATURE_INDEX[f"{family}_{field}"]] = price
            raw[row, FEATURE_INDEX[f"{family}_size_sum"]] = size
            raw[row, FEATURE_INDEX[f"{family}_size_squared_sum"]] = size * size
            raw[row, FEATURE_INDEX[f"{family}_price_size_sum"]] = price * size
            raw[row, FEATURE_INDEX[f"{family}_event_count"]] = 1
        raw[row, FEATURE_INDEX["quote_pair_present"]] = 1
        raw[row, FEATURE_INDEX["quote_pair_count"]] = 1
        raw[row, FEATURE_INDEX["midpoint_close"]] = price
        raw[row, FEATURE_INDEX["spread_close"]] = price * 0.0001
        raw[row, FEATURE_INDEX["spread_sum"]] = price * 0.0001
    return raw


class CausalSplitContractTest(unittest.TestCase):
    def test_input_history_is_normalized_only_to_the_anchor_basis(self) -> None:
        action = SplitAction(2_000_000, 4.0, "2020-08-31", "AAPL")
        timestamps = torch.tensor([1_000_000, 2_000_000], dtype=torch.long)
        raw = raw_rows((400.0, 100.0), (10.0, 40.0))
        normalized = normalize_features_to_anchor(raw, timestamps, anchor_us=2_000_000, actions=(action,))
        self.assertEqual(normalized[:, FEATURE_INDEX["trade_close"]].tolist(), [100.0, 100.0])
        self.assertEqual(normalized[:, FEATURE_INDEX["trade_size_sum"]].tolist(), [40.0, 40.0])
        self.assertEqual(
            normalized[:, FEATURE_INDEX["trade_price_size_sum"]].tolist(),
            raw[:, FEATURE_INDEX["trade_price_size_sum"]].tolist(),
        )

    def test_future_target_crossing_split_is_rebased_back_to_origin(self) -> None:
        raw = raw_rows((400.0, 100.0, 101.0), (10.0, 40.0, 44.0))
        factors = torch.tensor([1.0, 4.0, 4.0])
        targets = build_physical_horizon_targets(
            raw,
            torch.tensor([0]),
            torch.tensor([1_000_000]),
            share_factors=factors,
        )
        endpoint = float(targets.values[0, 0, 0])
        volume = float(targets.values[0, 0, 4])
        self.assertAlmostEqual(endpoint, 0.0, places=6)
        self.assertAlmostEqual(volume, math.log1p(10.0), places=6)

    def test_reverse_and_multiple_splits_compound_at_exact_boundaries(self) -> None:
        actions = (
            SplitAction(2_000_000, 0.1, "2022-01-03", "XYZ"),
            SplitAction(4_000_000, 2.0, "2024-01-02", "XYZ"),
        )
        values = cumulative_share_factors(
            torch.tensor([1_999_999, 2_000_000, 3_999_999, 4_000_000], dtype=torch.long),
            actions,
        )
        self.assertEqual(values.tolist(), [1.0, 0.1, 0.1, 0.2])

    def test_adjusted_authorities_are_rejected_by_configuration(self) -> None:
        from research.bar_gpt.v1.config import DataConfig

        config = DataConfig(one_second_table="bar_gpt_1s_split_adjusted")
        with self.assertRaisesRegex(ValueError, "globally adjusted"):
            config.validate()

    def test_provider_timeline_preserves_bounded_aliases(self) -> None:
        intervals = provider_timeline_intervals(
            "META",
            [
                ("figi:meta", "2012-05-18", "FB"),
                ("figi:meta", "2022-06-09", "META"),
            ],
            coverage_start="2019-01-01",
        )
        self.assertEqual(
            [(item.source_ticker, item.valid_from, item.valid_to_exclusive) for item in intervals],
            [("FB", "2012-05-18", "2022-06-09"), ("META", "2022-06-09", "9999-12-31")],
        )

    def test_provider_timeline_fails_on_current_ticker_conflict(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "ends at GOOG"):
            provider_timeline_intervals(
                "GOOGL",
                [("figi:alphabet", "2014-04-03", "GOOG")],
                coverage_start="2019-01-01",
            )


if __name__ == "__main__":
    unittest.main()
