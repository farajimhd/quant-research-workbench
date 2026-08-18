from __future__ import annotations

import unittest

from src.trading_runtime.strategy_activation import (
    run_plan_accepts_signal,
    strategy_observation_from_signal_occurrence,
)


class StrategyActivationTests(unittest.TestCase):
    def test_signal_occurrence_preserves_exact_field_instances(self) -> None:
        occurrence = _occurrence()
        observation = strategy_observation_from_signal_occurrence(occurrence)

        self.assertEqual(observation.ticker, "AAPL")
        self.assertEqual(observation.price, 192.5)
        self.assertEqual(observation.source_signal_ids, ("signal-1",))
        self.assertEqual(
            observation.source_values[
                "data.qmd.family.core_bars@1:price_change_1_bar_pct@5m"
            ]["value"],
            5.4,
        )

    def test_optional_watchlist_constrains_but_does_not_activate(self) -> None:
        plan = {
            "enabled": True,
            "signal_stream_ids": ["price-squeeze-5m"],
            "watchlist_ids": ["focus"],
            "enablement": {"state": "enabled", "scope": "persistent"},
        }
        self.assertFalse(run_plan_accepts_signal(plan, _occurrence()))
        self.assertTrue(
            run_plan_accepts_signal(plan, _occurrence(), eligible_tickers=["AAPL"])
        )

    def test_current_session_enablement_is_exact(self) -> None:
        plan = {
            "enabled": True,
            "signal_stream_ids": ["price-squeeze-5m"],
            "watchlist_ids": [],
            "enablement": {
                "state": "enabled",
                "scope": "current_session",
                "effective_session": "2026-08-17",
            },
        }
        self.assertTrue(run_plan_accepts_signal(plan, _occurrence()))
        plan["enablement"]["effective_session"] = "2026-08-18"
        self.assertFalse(run_plan_accepts_signal(plan, _occurrence()))


def _occurrence() -> dict[str, object]:
    return {
        "event_id": "signal-1",
        "signal_id": "signal-1",
        "signal_stream_id": "price-squeeze-5m",
        "ticker": "AAPL",
        "event_time": "2026-08-17T14:35:00+00:00",
        "effective_at": "2026-08-17T14:35:00+00:00",
        "field_evidence": {
            "data.qmd.family.core_bars@1:market.last_price": {
                "field_ref": "data.qmd.family.core_bars@1:market.last_price",
                "interval": "",
                "aggregation": "",
                "value": 192.5,
            },
            "data.qmd.family.core_bars@1:price_change_1_bar_pct@5m": {
                "field_ref": "data.qmd.family.core_bars@1:price_change_1_bar_pct",
                "interval": "5m",
                "aggregation": "",
                "value": 5.4,
            },
        },
    }


if __name__ == "__main__":
    unittest.main()
