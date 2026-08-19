from __future__ import annotations

import unittest

from research.bar_gpt.v3.schema import FEATURE_INDEX, FEATURE_NAMES

from bar_gpt_service.cache import CausalCache, RawBar


CAPACITIES = {
    "1s": 4, "5s": 2, "10s": 2, "30s": 2, "1m": 2,
    "5m": 2, "30m": 2, "1h": 2, "1D": 2, "1W": 2, "1MO": 2,
}


def _bar(start: int, *, price: float, revision: int = 1, view: str = "1s", available_at: int | None = None) -> RawBar:
    values = [0.0] * len(FEATURE_NAMES)
    for name in ("trade_open", "trade_high", "trade_low", "trade_close"):
        values[FEATURE_INDEX[name]] = price
    values[FEATURE_INDEX["trade_present"]] = 1.0
    values[FEATURE_INDEX["context_eligible"]] = 1.0
    values[FEATURE_INDEX["origin_eligible"]] = 1.0
    values[FEATURE_INDEX["origin_event_count"]] = 1.0
    duration = 1_000_000 if view == "1s" else 86_400_000_000
    return RawBar("AAPL", view, start, start + duration, available_at or start + duration, tuple(values), revision, "test")


class CacheTests(unittest.TestCase):
    def test_ring_buffer_is_bounded_and_late_correction_rebuilds_closed_bucket(self) -> None:
        cache = CausalCache(CAPACITIES, raw_capacity_1s=8)
        for second in range(6):
            cache.upsert(_bar(second * 1_000_000, price=100 + second))
        before = cache.rows("AAPL", "5s", 6_000_000)[-1]
        cache.upsert(_bar(2_000_000, price=150, revision=2, available_at=6_000_000))
        after = cache.rows("AAPL", "5s", 6_000_000)[-1]
        self.assertEqual(after.revision, 2)
        self.assertGreater(after.values[FEATURE_INDEX["trade_high"]], before.values[FEATURE_INDEX["trade_high"]])
        self.assertEqual(len(cache.rows("AAPL", "1s", 10_000_000)), CAPACITIES["1s"])

    def test_daily_history_retains_warm_source_but_projects_model_context(self) -> None:
        cache = CausalCache(CAPACITIES, raw_capacity_1s=8, raw_capacity_1d=5)
        for day in range(5):
            cache.upsert(_bar(day * 86_400_000_000 + 1, price=100 + day, view="1D"))
        self.assertEqual(len(cache._tickers["AAPL"].rows["1D"]), 5)
        self.assertEqual(len(cache.rows("AAPL", "1D", 10**18)), CAPACITIES["1D"])


if __name__ == "__main__":
    unittest.main()
