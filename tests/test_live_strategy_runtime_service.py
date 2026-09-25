from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from src.backend.live_strategy_runtime_service import (
    LiveStrategyRuntimeSupervisor, RetryableSignalWorkError,
)


class LiveStrategyRuntimeSupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def test_signal_work_is_processed_by_existing_queue_consumer(self) -> None:
        import asyncio

        supervisor = LiveStrategyRuntimeSupervisor()
        supervisor._process = AsyncMock(return_value=None)
        delivery = {"delivery_id": "plan-1:event-1", "run_plan_id": "plan-1",
                    "ticker": "ABC", "occurrence": {"event_id": "event-1"}}
        receipt = supervisor.submit_signal_work(delivery, intent_content_hash="a" * 64)
        worker = asyncio.create_task(supervisor._run())
        try:
            self.assertEqual(await asyncio.wait_for(asyncio.wrap_future(receipt), 3),
                             delivery["delivery_id"])
            supervisor._process.assert_awaited_once()
        finally:
            supervisor._stop.set()
            supervisor._queue.put_nowait(None)
            await asyncio.wait_for(worker, 3)

    async def test_signal_work_receipt_retries_only_proven_pre_execution_failure(self) -> None:
        supervisor = LiveStrategyRuntimeSupervisor()
        delivery = {"delivery_id": "plan-1:event-1", "run_plan_id": "plan-1",
                    "ticker": "ABC", "occurrence": {"event_id": "event-1"}}
        supervisor._signal_work_preflight = Mock(side_effect=[
            RetryableSignalWorkError("not executed"), None])
        supervisor._process = AsyncMock(return_value=object())
        first = supervisor.submit_signal_work(delivery, intent_content_hash="a" * 64)
        self.assertIs(supervisor.submit_signal_work(delivery, intent_content_hash="a" * 64), first)
        self.assertFalse(first.cancel())
        with self.assertRaisesRegex(RetryableSignalWorkError, "not executed"):
            await supervisor._process_signal_work(supervisor._queue.get_nowait(), None, {})
        self.assertTrue(first.done())
        second = supervisor.submit_signal_work(delivery, intent_content_hash="a" * 64)
        self.assertIsNot(second, first)
        await supervisor._process_signal_work(supervisor._queue.get_nowait(), None, {})
        self.assertEqual(second.result(), delivery["delivery_id"])
        self.assertIs(supervisor.submit_signal_work(delivery, intent_content_hash="a" * 64), second)
        with self.assertRaisesRegex(ValueError, "identity conflicts"):
            supervisor.submit_signal_work(delivery, intent_content_hash="b" * 64)
        self.assertEqual(supervisor._signal_work[delivery["delivery_id"]].status,
                         "completed")

    async def test_signal_work_ambiguous_failure_and_cold_replay_fail_closed(self) -> None:
        supervisor = LiveStrategyRuntimeSupervisor()
        delivery = {"delivery_id": "plan-1:event-1", "run_plan_id": "plan-1",
                    "ticker": "ABC", "occurrence": {"event_id": "event-1"}}
        with self.assertRaisesRegex(RuntimeError, "cold signal replay"):
            supervisor.submit_signal_work(delivery, intent_content_hash="a" * 64,
                                          cold_replay=True)
        supervisor._process = AsyncMock(side_effect=RuntimeError("broker uncertain"))
        receipt = supervisor.submit_signal_work(delivery, intent_content_hash="a" * 64)
        with self.assertRaisesRegex(RuntimeError, "broker uncertain"):
            await supervisor._process_signal_work(supervisor._queue.get_nowait(), None, {})
        self.assertTrue(receipt.done())
        with self.assertRaisesRegex(RuntimeError, "outcome is uncertain"):
            supervisor.submit_signal_work(delivery, intent_content_hash="a" * 64)
        self.assertEqual(supervisor._signal_work[delivery["delivery_id"]].status,
                         "uncertain")

    async def test_early_squeeze_admits_one_persistent_watch_per_run_and_ticker(self) -> None:
        supervisor = LiveStrategyRuntimeSupervisor()
        first = {"delivery_id": "one", "run_plan_id": "plan-1", "ticker": "SUGP"}
        repeated = {"delivery_id": "two", "run_plan_id": "plan-1", "ticker": "SUGP"}
        with patch.object(supervisor, "_save_activations"):
            self.assertEqual(supervisor.submit([first]), 1)
            self.assertEqual(supervisor.submit([repeated]), 1)

        self.assertEqual(len(supervisor._activations), 1)
        self.assertEqual(supervisor._queue.qsize(), 1)

    async def test_market_row_uses_causal_per_ticker_unified_structure_book(self) -> None:
        supervisor = LiveStrategyRuntimeSupervisor()
        broker = object()
        runtime = SimpleNamespace(
            broker=SimpleNamespace(positions=AsyncMock(return_value=[])),
            process_account_strategy_observation=AsyncMock(),
        )
        assigned = SimpleNamespace(
            ticker="SUGP",
            account_id="DU123",
            conid=1,
            parameters={"structural_entry": {"enabled": True}},
        )
        state = {
            "runtime": runtime,
            "strategy": SimpleNamespace(assignments=lambda: [assigned]),
            "positions_cache": {},
        }
        supervisor._runtime_state = AsyncMock(return_value=(broker, state))  # type: ignore[method-assign]
        as_of = datetime(2026, 8, 21, 8, 10, 1, tzinfo=timezone.utc)
        with patch(
            "src.backend.live_strategy_runtime_service.qmd_current_structure_snapshot",
            return_value={
                "sym": "SUGP",
                "bar_end": "2026-08-21T08:10:00Z",
                "qmd_structure_unified_levels": [{
                    "unified_level_id": 17,
                    "side": -1,
                    "lower": 3.80,
                    "upper": 3.82,
                }],
            },
        ):
            returned = await supervisor._process_market_row(
                {
                    "delivery": {"ticker": "SUGP", "run_plan_id": "plan-1"},
                    "row": {
                        "ticker": "SUGP",
                        "market.last_price": 3.83,
                        "quote.bid_price": 3.82,
                        "quote.ask_price": 3.83,
                        "session.phase": "premarket",
                    },
                    "as_of": as_of.isoformat(),
                },
                None,
                {},
            )

        self.assertIs(returned, broker)
        observation = runtime.process_account_strategy_observation.await_args.args[0]
        self.assertEqual(observation.structural_resistance_levels[0]["unified_level_id"], 17)
        self.assertEqual(observation.structural_resistance_upper, None)

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
