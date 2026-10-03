"""History must share cold readers while preserving verified database authority."""
import asyncio
from contextlib import asynccontextmanager
from threading import Event
from time import monotonic

import pytest
from src.backend.backtest_history_performance import HistoryPerformance
from src.backend.workload_budget import classify_workload

RUN = "00000000-0000-0000-0000-000000000001"

class Budget:
    @asynccontextmanager
    async def lease(self, lane):
        assert lane == "simulation"
        yield


def test_single_flight_pending_poll_and_changed_head():
    async def scenario():
        entered, release = Event(), Event()
        calls = []
        head = {RUN: 10}
        def read(run_id):
            calls.append(run_id)
            entered.set()
            assert release.wait(3)
            return {"run_id": RUN, "verified_sequence": 10, "report": {"summary": {"net_pnl": "12.50"}}}
        service = HistoryPerformance(reader=read, heads=lambda _ids: dict(head), budget=Budget())
        try:
            assert (await service.snapshot([(RUN, 10)]))["rows"][0]["status"] == "queued"
            await asyncio.to_thread(entered.wait, 2)
            for _ in range(3):
                assert (await service.snapshot([(RUN, 10)]))["rows"][0]["status"] == "verifying"
            assert calls == [RUN]
            release.set()
            await service.worker
            ready = (await service.snapshot([(RUN, 10)]))["rows"][0]
            assert ready["report"]["summary"]["net_pnl"] == "12.50"
            head[RUN] = 11
            changed = (await service.snapshot([(RUN, 10)]))["rows"][0]
            assert changed["status"] == "unavailable" and "report" not in changed
        finally:
            release.set()
            await service.close()
    asyncio.run(scenario())


def test_queue_bound_and_explicit_failure_retry():
    async def scenario():
        def fail(_run_id):
            raise RuntimeError("Missing final fees")
        service = HistoryPerformance(reader=fail, heads=lambda _ids: {}, budget=Budget())
        requested = [(f"00000000-0000-0000-0000-{i:012d}", 10) for i in range(1, 34)]
        try:
            snapshot = await service.snapshot(requested)
            assert sum(row["status"] == "queued" for row in snapshot["rows"]) == 32
            assert snapshot["rows"][-1]["status"] == "deferred"
            await service.worker
            row = (await service.snapshot([(RUN, 10)]))["rows"][0]
            assert row["error"] == "Missing final fees"
            retry = (await service.snapshot([(RUN, 10)], retry_failed=True))["rows"][0]
            assert retry["status"] == "queued"
        finally:
            await service.close()
    asyncio.run(scenario())


def test_result_finishing_during_head_check_is_not_falsely_invalidated():
    async def scenario():
        second = '00000000-0000-0000-0000-000000000002'
        checking, finish_check, finish_read = Event(), Event(), Event()
        queried = []
        def heads(ids):
            queried.append(list(ids))
            if len(queried) == 1:
                checking.set()
                assert finish_check.wait(3)
            return {run: 10 for run in ids}
        def read(run):
            assert finish_read.wait(3)
            return {'run_id': run, 'verified_sequence': 10, 'report': {'summary': {'net_pnl': '8'}}}
        service = HistoryPerformance(reader=read, heads=heads, budget=Budget())
        service.entries[(RUN, 10)] = {'run_id': RUN, 'status': 'available', 'updated': monotonic(),
            'report': {'summary': {'net_pnl': '12'}}}
        try:
            await service.snapshot([(second, 10)])
            pending = asyncio.create_task(service.snapshot([(RUN, 10), (second, 10)]))
            assert await asyncio.to_thread(checking.wait, 2)
            finish_read.set()
            await service.worker
            finish_check.set()
            result = await pending
            assert [row['status'] for row in result['rows']] == ['available', 'available']
            assert queried == [[RUN]]
            result = await service.snapshot([(RUN, 10), (second, 10)])
            assert queried[-1] == [RUN, second]
            assert all(row['status'] == 'available' for row in result['rows'])
        finally:
            finish_read.set()
            finish_check.set()
            await service.close()
    asyncio.run(scenario())


def test_polling_does_not_consume_cold_reader_capacity():
    assert classify_workload("GET", "/api/trading/backtest/history-performance") == "runtime_state"
    assert classify_workload("GET", f"/api/trading/backtest/runs/{RUN}/v4-performance") == "simulation"
