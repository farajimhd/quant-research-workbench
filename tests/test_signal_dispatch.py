from __future__ import annotations

import unittest

from src.trading_runtime.signal_dispatch import dispatchable_strategy_signals


class SignalDispatchTests(unittest.TestCase):
    def test_signal_stream_activates_and_watchlist_only_constrains_eligibility(self) -> None:
        configuration = {
            "run_plans": {"plans": [{
                "run_plan_id": "momentum",
                "profile_id": "profile",
                "book_id": "main",
                "enabled": True,
                "allowed_environments": ["live"],
                "signal_stream_ids": ["squeeze"],
                "watchlist_ids": ["focus"],
                "activation": {"watchlist_policy": "any_selected"},
                "enablement": {"state": "enabled", "scope": "persistent"},
            }]},
        }
        occurrences = [
            {"event_id": "a", "signal_stream_id": "squeeze", "ticker": "AAPL", "event_time": "2026-08-17T14:00:00+00:00"},
            {"event_id": "b", "signal_stream_id": "squeeze", "ticker": "MSFT", "event_time": "2026-08-17T14:00:01+00:00"},
        ]
        runtime = {"watchlists": [{"watchlist_id": "focus", "members": [{"ticker": "AAPL"}]}]}

        deliveries = dispatchable_strategy_signals(
            configuration, occurrences, watchlist_runtime=runtime, mode="live"
        )

        self.assertEqual([row["ticker"] for row in deliveries], ["AAPL"])
        self.assertEqual(deliveries[0]["event_id"], "a")

    def test_signal_stream_without_watchlist_accepts_core_scan_ticker(self) -> None:
        configuration = {
            "run_plans": {"plans": [{
                "run_plan_id": "core",
                "enabled": True,
                "allowed_environments": ["paper"],
                "signal_stream_ids": ["squeeze"],
                "watchlist_ids": [],
                "enablement": {"state": "enabled", "scope": "persistent"},
            }]},
        }
        deliveries = dispatchable_strategy_signals(
            configuration,
            [{"event_id": "a", "signal_stream_id": "squeeze", "ticker": "AMD", "event_time": "2026-08-17T14:00:00+00:00"}],
            mode="paper",
        )
        self.assertEqual(len(deliveries), 1)


if __name__ == "__main__":
    unittest.main()
