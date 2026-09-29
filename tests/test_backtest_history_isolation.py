"""History must respond even when setup work occupies every default worker."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from fastapi import HTTPException
from src.backend.app import (
    backtest_run_service, trading_backtest_run_resume, trading_backtest_runs,
)


class BacktestHistoryIsolationTests(IsolatedAsyncioTestCase):
    async def test_resume_fails_before_any_disk_backed_replay_lookup(self):
        with patch.object(backtest_run_service, "resume") as legacy:
            with self.assertRaises(HTTPException) as raised:
                await trading_backtest_run_resume(
                    "00000000-0000-0000-0000-000000000001")
            self.assertEqual(raised.exception.status_code, 409)
            self.assertIn("ClickHouse/Keeper", raised.exception.detail)
            legacy.assert_not_called()

    async def test_history_does_not_queue_behind_preparation(self):
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        entered = asyncio.Event()
        release = Event()

        def slow_preparation():
            loop.call_soon_threadsafe(entered.set)
            release.wait(10)

        preparation = asyncio.create_task(asyncio.to_thread(slow_preparation))
        try:
            await entered.wait()
            rows = [{"run_id": "saved", "status": "stopped"}]
            with patch.object(backtest_run_service, "list", return_value=rows) as listing:
                result = await asyncio.wait_for(trading_backtest_runs(), timeout=2)
                self.assertEqual(result, {"schema_version": 1, "rows": rows, "row_count": 1})
                listing.assert_called_once_with(
                    include_durable=True, strategy_one_only=False)
                self.assertFalse(preparation.done())
        finally:
            release.set()
            await preparation
