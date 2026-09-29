"""History must respond even when setup work occupies every default worker."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from fastapi import HTTPException
from src.backend.app import (
    backtest_run_service, trading_backtest_run_resume, trading_backtest_runs,
)


class BacktestHistoryIsolationTests(IsolatedAsyncioTestCase):
    async def test_resume_uses_only_backtest_service(self):
        run_id = "00000000-0000-0000-0000-000000000001"
        with patch.object(backtest_run_service, "resume") as resumed:
            resumed.return_value = SimpleNamespace(
                stream_snapshot=lambda: {"run_id": run_id})
            self.assertEqual(await trading_backtest_run_resume(run_id),
                             {"run_id": run_id})
            resumed.assert_awaited_once_with(run_id)
        with patch.object(backtest_run_service, "resume", side_effect=KeyError(run_id)):
            with self.assertRaises(HTTPException) as raised:
                await trading_backtest_run_resume(run_id)
            self.assertEqual(raised.exception.status_code, 404)

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
