"""Unused native preparation must not leave writers alive after app shutdown."""
import asyncio
from types import SimpleNamespace

import pytest

from tests.test_program_configuration_selection_service import installed
from src.backend import program_app_bootstrap as bootstrap


def handoff(service, events, *, runtime_error=None):
    async def close_runtime():
        events.append('runtime')
        if runtime_error:
            raise runtime_error
    async def close_journal():
        events.append('journal')
    controller = SimpleNamespace(_task=None, status='created',
        _runtime=SimpleNamespace(close_background_tasks=close_runtime),
        _close_fixed_journal=close_journal, _program_launch_plan=object(),
        _program_feature_cache=object())
    service._prepared = (object(), controller)
    return controller


def test_unused_handoff_closes_once_and_revokes_launch_plan(monkeypatch):
    service, revision, _ = installed(monkeypatch)
    events = []
    controller = handoff(service, events)
    async def run():
        await service.close_prepared_handoff()
        await service.close_prepared_handoff()
        with pytest.raises(RuntimeError, match='authority is closed'):
            await service.snapshot(revision['revision_id'])
    asyncio.run(run())
    assert events == ['runtime', 'journal']
    assert service._prepared is None and service._closed
    assert controller._program_launch_plan is controller._program_feature_cache is None


def test_managed_shutdown_closes_policy_even_when_runtime_cleanup_fails(monkeypatch):
    service, _, _ = installed(monkeypatch)
    events = []
    controller = handoff(service, events, runtime_error=ValueError('runtime cleanup failed'))
    application = SimpleNamespace(state=SimpleNamespace())
    setattr(application.state, bootstrap.OWNED_KEY,
        (SimpleNamespace(close=lambda: events.append('policy')), service))
    with pytest.raises(BaseExceptionGroup, match='Managed programme shutdown failed'):
        asyncio.run(bootstrap.close_managed_program_startup(application))
    assert events == ['runtime', 'journal', 'policy']
    assert controller._program_launch_plan is None
    assert getattr(application.state, bootstrap.OWNED_KEY) is None


@pytest.mark.parametrize('owner', ['_preparing', '_launching'])
def test_shutdown_cannot_dispose_an_in_progress_handoff(monkeypatch, owner):
    service, _, _ = installed(monkeypatch)
    events = []
    controller = handoff(service, events)
    setattr(service, owner, True if owner == '_preparing' else object())
    with pytest.raises(RuntimeError, match='during preparation or launch'):
        asyncio.run(service.close_prepared_handoff())
    assert service._prepared[1] is controller and not service._closed
    assert events == []


def test_shutdown_does_not_stop_a_run_already_transferred_to_run_service(monkeypatch):
    service, _, _ = installed(monkeypatch)
    assert service._prepared is None
    asyncio.run(service.close_prepared_handoff())
    assert service._closed


def test_caller_cancellation_drains_same_cleanup_task(monkeypatch):
    service, _, _ = installed(monkeypatch)
    events = []
    controller = handoff(service, events)
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed_close():
            entered.set()
            await release.wait()
            events.append('runtime')
        controller._runtime.close_background_tasks = delayed_close
        task = asyncio.create_task(service.close_prepared_handoff())
        await entered.wait()
        task.cancel()
        release.set()
        await task
    asyncio.run(run())
    assert events == ['runtime', 'journal']
    assert controller._program_launch_plan is None
