from __future__ import annotations

import unittest
from types import SimpleNamespace

import torch

from bar_gpt_service.decoding import _physical_fields
from research.bar_gpt.v3.targets import (
    AVAILABILITY_TARGET_NAMES,
    CONTINUOUS_TARGET_NAMES,
)


class DecodingTests(unittest.TestCase):
    def test_raw_head_is_unchanged_and_decoded_price_is_separate(self) -> None:
        release = SimpleNamespace(
            config=SimpleNamespace(version="v2"),
            data_config=SimpleNamespace(horizons_us=(60_000_000,)),
        )
        quantiles = torch.zeros((1, len(CONTINUOUS_TARGET_NAMES), 3), dtype=torch.float32)
        quantiles[0, 3, 1] = 0.25
        availability = torch.zeros((1, len(AVAILABILITY_TARGET_NAMES)), dtype=torch.float32)
        classes = torch.zeros((1, 12, 3), dtype=torch.float32)
        fields = _physical_fields(
            release, "AAPL", {"trade": 100.0}, quantiles, availability,
            classes, (0.1, 0.5, 0.9),
        )
        prefix = "model.bargpt.v2.physical.1m.trade_close_return.q50"
        self.assertEqual(fields[f"{prefix}.raw"], 0.25)
        self.assertNotEqual(fields[f"{prefix}.value"], 0.25)
        self.assertIn("model.bargpt.v2.physical.1m.trade_available.logit", fields)

    def test_missing_price_base_decodes_to_json_safe_null(self) -> None:
        release = SimpleNamespace(
            config=SimpleNamespace(version="v3"),
            data_config=SimpleNamespace(horizons_us=(5_000_000,)),
        )
        fields = _physical_fields(
            release, "AAPL", {},
            torch.zeros((1, len(CONTINUOUS_TARGET_NAMES), 3)),
            torch.zeros((1, len(AVAILABILITY_TARGET_NAMES))),
            None, (0.1, 0.5, 0.9),
        )
        self.assertIsNone(fields["model.bargpt.v3.physical.5s.bid_close_return.q50.value"])


if __name__ == "__main__":
    unittest.main()
