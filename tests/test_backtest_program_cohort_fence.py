"""Fence lifecycle checks; native cohort execution is covered separately."""
import asyncio
from functools import wraps
from types import SimpleNamespace

import pytest

from src.backend import backtest_program_cohort as cohort
from src.backend.backtest_typed_publisher import TypedBacktestReceipt
from src.backend import program_policy_recovery


def async_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return run


@async_test
@pytest.mark.parametrize("failure", [None, "receipt", "ownership", "cancel"])
async def test_cohort_retains_owner_until_exact_fence(monkeypatch, failure):
    entered, release = asyncio.Event(), asyncio.Event()
    journal = SimpleNamespace(latest_sequence=lambda run: 9)
    actor = SimpleNamespace(hash="policy")
    runtime = SimpleNamespace(journal=journal, last_event_time=7, broker=object(),
        config=object(), order_manager=object(), portfolio=object())
    source = SimpleNamespace(typed_source_cursor=lambda: ("source", 7))
    publisher = SimpleNamespace(journal=journal, writer=object(), fenced_sequence=0,
        _batch_id="batch", _source_cursor="cursor", _error=None,
        enqueue_pending=lambda: None)
    controller = SimpleNamespace(_runtime=runtime, _journal=journal,
        _program_strategy=actor, _program_signal_runtime=source,
        _journal_publisher=publisher, definition=object(), run_id="run",
        _program_dispatch_failure=None, _program_cohort_active=False)
    result = cohort.ProgramCohortResult("completed", 10, 1, 1, object())

    async def prepared(*args, **kwargs):
        assert controller._program_cohort_active
        return result

    async def fence():
        entered.set()
        await release.wait()
        publisher.fenced_sequence = 9
        return TypedBacktestReceipt(8 if failure == "receipt" else 9, "batch", "cursor")

    publisher.await_fence = fence
    monkeypatch.setattr(cohort, "run_prepared_program_cohort", prepared)
    monkeypatch.setattr(program_policy_recovery, "program_policy_fingerprint", lambda a: a.hash)
    task = asyncio.create_task(cohort.run_fenced_program_cohort(controller, {}, None, None))
    await entered.wait()
    assert controller._program_cohort_active
    with pytest.raises(RuntimeError, match="already owns"):
        await cohort.run_fenced_program_cohort(controller, {}, None, None)
    if failure == "ownership":
        controller.definition = object()
    if failure == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        release.set()
        if failure:
            with pytest.raises(RuntimeError, match="exact fence"):
                await task
        else:
            assert await task is result
    assert not controller._program_cohort_active
    assert (controller._program_dispatch_failure is not None) == bool(failure)
    if failure:
        with pytest.raises(RuntimeError, match="requires recovery"):
            await cohort.run_fenced_program_cohort(controller, {}, None, None)


@async_test
async def test_stopped_empty_cohort_does_not_publish(monkeypatch):
    controller = SimpleNamespace(_program_cohort_active=False, _program_dispatch_failure=None)
    result = cohort.ProgramCohortResult("stopped", 0, 0, 0, None)

    async def stopped(*args, **kwargs):
        return result

    monkeypatch.setattr(cohort, "run_prepared_program_cohort", stopped)
    assert await cohort.run_fenced_program_cohort(controller, {}, None, None) is result
    assert not controller._program_cohort_active
