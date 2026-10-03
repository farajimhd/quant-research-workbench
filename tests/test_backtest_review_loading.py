"""Cold Review work must survive polling, cancellation and changing authority."""
import asyncio
from contextlib import asynccontextmanager
from threading import Event

import pytest

from src.backend.backtest_review_loading import ReviewLoading, saved_review_read_scope
from src.backend.workload_budget import classify_workload

RUN = "00000000-0000-0000-0000-000000000001"


class Budget:
    active = 0

    @asynccontextmanager
    async def lease(self, lane):
        assert lane == "simulation"
        self.active += 1
        try:
            yield
        finally:
            self.active -= 1


def test_polls_and_disconnect_share_one_audit_and_hold_capacity():
    async def scenario():
        entered, release = Event(), Event()
        calls = []
        budget = Budget()
        def reader(run_id):
            calls.append(run_id)
            entered.set()
            assert release.wait(5)
            return {"page": {"run": {"run_id": run_id}, "verified_sequence": 10}}
        service = ReviewLoading(reader, lambda result: True, budget, wait_seconds=1)
        try:
            request = asyncio.create_task(service.snapshot(RUN))
            assert await asyncio.to_thread(entered.wait, 2)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            service.wait_seconds = .01
            for _ in range(3):
                assert await service.snapshot(RUN) == {"status": "verifying"}
            assert calls == [RUN] and budget.active == 1
            release.set()
            await service.jobs[RUN]
            result = await service.snapshot(RUN)
            assert result["status"] == "ready" and result["page"]["verified_sequence"] == 10
            assert calls == [RUN] and not service.jobs and budget.active == 0
        finally:
            release.set()
            await service.close()
    asyncio.run(scenario())


def test_changed_head_does_not_deliver_completed_page():
    async def scenario():
        entered, release = Event(), Event()
        head = [10]
        calls = []
        def reader(run_id):
            sequence = head[0]
            calls.append(sequence)
            entered.set()
            assert release.wait(5)
            return {"page": {"verified_sequence": sequence}}
        service = ReviewLoading(reader, lambda result: result["page"]["verified_sequence"] == head[0],
                                Budget(), wait_seconds=.01)
        try:
            await service.snapshot(RUN)
            release.set()
            await service.jobs[RUN]
            head[0] = 11
            result = await service.snapshot(RUN)
            if result["status"] != "ready":
                await service.jobs[RUN]
                result = await service.snapshot(RUN)
            assert result["page"]["verified_sequence"] == 11 and calls == [10, 11]
        finally:
            release.set()
            await service.close()
    asyncio.run(scenario())


def test_queue_bound_and_failed_audit_is_not_ready():
    async def scenario():
        release = Event()
        def reader(_):
            assert release.wait(5)
            raise ValueError("Incomplete committed family")
        service = ReviewLoading(reader, lambda _: True, Budget(), wait_seconds=.01, max_pending=1)
        try:
            assert await service.snapshot(RUN) == {"status": "verifying"}
            assert await service.snapshot("other") == {"status": "queued"}
            release.set()
            await asyncio.gather(*service.jobs.values(), return_exceptions=True)
            with pytest.raises(ValueError, match="Incomplete committed family"):
                await service.snapshot(RUN)
            assert not service.jobs
        finally:
            release.set()
            await service.close()
    asyncio.run(scenario())


def test_immediate_failure_is_removed_so_one_retry_starts_new_work():
    async def scenario():
        calls = []
        def reader(_):
            calls.append(1)
            if len(calls) == 1:
                raise ValueError("Missing journal evidence")
            return {"page": {"verified_sequence": 10}}
        service = ReviewLoading(reader, lambda _: True, Budget(), wait_seconds=1)
        try:
            with pytest.raises(ValueError, match="Missing journal evidence"):
                await service.snapshot(RUN)
            assert not service.jobs
            assert (await service.snapshot(RUN))["status"] == "ready"
            assert len(calls) == 2
        finally:
            await service.close()
    asyncio.run(scenario())


def test_read_scope_shares_history_audit_and_preserves_byte_and_ttl_bounds():
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    from src.backend import backtest_v4_saved_review as saved
    from src.backend import backtest_review_loading as loading
    client = SimpleNamespace(base_url="endpoint", user="reader", password="credential")
    entered, release, second = Event(), Event(), Event()
    bounds = [(cache.max_bytes, cache.max_entry_bytes, cache.ttl_seconds)
              for cache in (saved._V4_CACHE, saved._V4_PERFORMANCE_CACHE)]
    def first():
        with saved_review_read_scope(client, RUN):
            entered.set()
            assert release.wait(5)
    def other():
        with saved_review_read_scope(client, RUN):
            second.set()
    with ThreadPoolExecutor(max_workers=2) as executor:
        one = executor.submit(first)
        assert entered.wait(2)
        two = executor.submit(other)
        try:
            assert not second.wait(.05)
        finally:
            release.set()
        one.result(); two.result()
    assert not loading._scopes
    for cache, bound in zip((saved._V4_CACHE, saved._V4_PERFORMANCE_CACHE), bounds):
        assert cache.max_sessions == 64
        assert (cache.max_bytes, cache.max_entry_bytes, cache.ttl_seconds) == bound


def test_readiness_polls_have_independent_capacity():
    assert classify_workload("GET", f"/api/trading/backtest/runs/{RUN}/v4-review-ready") == "runtime_state"
    assert classify_workload("GET", f"/api/trading/backtest/runs/{RUN}/v4-performance") == "simulation"
