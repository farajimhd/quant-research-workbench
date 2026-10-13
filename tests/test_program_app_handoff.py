"""Normal app startup invokes the explicit native handoff without launching."""
import asyncio
from datetime import datetime, UTC
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from src.backend import program_app_handoff as handoff
from src.backend.program_app_startup import start_program_app_selection, configure_program_app_startup
from src.backend import program_configuration_selection_service as selection
from src.backend.backtest_program_input_plan import ProgramPublishedInputRequest
from src.backend.replay_run_service import ReplayRunService
from src.backend.signal_stream_typed_publication import TypedSignalPublicationQueue
from test_signal_stream_typed_publication import FakeStorage
from tests.test_program_managed_journal import controller_request
from tests.test_program_app_startup import supplied


def request(tmp_path, publisher):
    controller, journal = controller_request(tmp_path)
    inputs = ProgramPublishedInputRequest(None, (), (), 0, 2, 'source-origin',
        ((14400, 14402),), datetime(2026, 10, 13, tzinfo=UTC), 1)
    return handoff.ProgramAppHandoff(controller.definition, controller._program_strategy.assignments(),
        controller._program_strategy._listings, 1000, inputs,
        ({'source_publisher': publisher},), journal, publisher, {}, lambda: None, 1)


@pytest.mark.parametrize('mode', ['ready', 'failure', 'owner_change'])
def test_central_installation_precedes_native_preparation_and_no_task_starts(tmp_path, monkeypatch, mode):
    application, selector, _, _ = supplied(monkeypatch)
    publisher = TypedSignalPublicationQueue(FakeStorage(), capacity=1)
    try:
        approved = request(tmp_path, publisher)
        handoff.configure_program_app_handoff(application, approved)
        service = ReplayRunService(runtime_root=tmp_path)
        calls = []
        prepared = SimpleNamespace(_task=None)
        async def prepare(actual, definition, **kwargs):
            assert selection.program_configuration_selection_service() is selector
            assert actual is service and definition is approved.definition
            assert kwargs['cohorts'] is approved.published_inputs
            assert kwargs['journal_scope'] is approved.journal
            assert 'history_post' not in kwargs
            calls.append('prepare')
            if mode == 'failure':
                raise ValueError('Published coverage unavailable')
            if mode == 'owner_change':
                setattr(application.state, handoff.HANDOFF_KEY, None)
            return prepared
        async def close(): calls.append('close')
        monkeypatch.setattr(selector, 'prepare_app_controller', prepare)
        monkeypatch.setattr(selector, 'close_prepared_handoff', close)
        if mode == 'ready':
            assert asyncio.run(start_program_app_selection(application, run_service=service)) == 1
            assert calls == ['prepare'] and prepared._task is None
        else:
            with pytest.raises((ValueError, RuntimeError)):
                asyncio.run(start_program_app_selection(application, run_service=service))
            assert calls == (['prepare', 'close'] if mode == 'owner_change' else ['prepare'])
    finally:
        publisher.close()


def test_handoff_without_central_selection_fails_before_preparation(tmp_path, monkeypatch):
    monkeypatch.setattr(selection, '_SERVICE', None)
    publisher = TypedSignalPublicationQueue(FakeStorage(), capacity=1)
    try:
        application = FastAPI()
        handoff.configure_program_app_handoff(application, request(tmp_path, publisher))
        with pytest.raises(ValueError, match='approved central selections'):
            asyncio.run(start_program_app_selection(application, run_service=ReplayRunService(runtime_root=tmp_path)))
    finally:
        publisher.close()


def test_actual_lifespan_passes_its_normal_backtest_service():
    import ast
    from pathlib import Path
    tree = ast.parse(Path('src/backend/app.py').read_text(encoding='utf-8'))
    lifespan = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
        and node.name == 'application_lifespan')
    calls = [node for node in ast.walk(lifespan) if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == 'start_program_app_selection']
    assert len(calls) == 1
    assert any(keyword.arg == 'run_service' and isinstance(keyword.value, ast.Name)
        and keyword.value.id == 'backtest_run_service' for keyword in calls[0].keywords)
