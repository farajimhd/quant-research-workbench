from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from src.backend.live_strategy_runtime_service import LiveStrategyRuntimeSupervisor


class LiveStrategyRuntimeSupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def test_external_intent_uses_same_runtime_as_strategy_signals(self) -> None:
        supervisor = LiveStrategyRuntimeSupervisor()
        broker = object()
        runtime = AsyncMock()
        planner = SimpleNamespace(upsert_instrument=Mock())
        runtime.submit_external_intent.return_value = {
            "proposal_id": "proposal-1",
            "decision": {"status": "approved"},
            "order_group": {"state": "submitted"},
        }
        supervisor._runtime_state = AsyncMock(  # type: ignore[method-assign]
            return_value=(broker, {"runtime": runtime, "planner": planner})
        )

        returned_broker, result = await supervisor._process_external_intent(
            {
                "run_plan_id": "plan-1",
                "intent": SimpleNamespace(
                    ticker="AAPL",
                    metadata={"conid": 265598, "currency": "USD", "exchange": "SMART"},
                ),
                "account_id": "DU123",
                "proposal_id": "proposal-1",
                "proposal_authority": "manual",
            },
            None,
            {},
        )

        self.assertIs(returned_broker, broker)
        self.assertEqual(result["order_group"]["state"], "submitted")
        runtime.submit_external_intent.assert_awaited_once()
        self.assertEqual(planner.upsert_instrument.call_args.args[0].conid, 265598)
        supervisor._runtime_state.assert_awaited_once_with(
            {"run_plan_id": "plan-1"}, None, {}
        )


if __name__ == "__main__":
    unittest.main()
