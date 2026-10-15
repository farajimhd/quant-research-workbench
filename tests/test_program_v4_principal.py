from dataclasses import replace
import asyncio
import threading

import pytest

from src.backend import program_v4_principal as module
from src.trading_runtime import strategy_registry as registry
from src.trading_runtime.keeper_session import ManagedKeeperSession
from tests.test_live_signal_completion_keeper import FakeKazoo
from tests.test_live_strategy_one_v4_principal import Client


def lease(monkeypatch):
    monkeypatch.setattr(registry, '_PROGRAM_REGISTRY', {})
    registration = registry.ProgramStrategyExecutorRegistration('generic-program', 62,
        'a' * 64, 'b' * 64, 'c' * 64, requires_execution_risk=True)
    registry.register_program_strategy_executor(registration)
    client = FakeKazoo()
    client.add_listener = lambda listener: None
    session = ManagedKeeperSession(client)
    session._on_state('CONNECTED')
    return module.ProgramV4KeeperLease.acquire(session, run_id='program-paper:2026-10-15',
        owner_id='worker-1', registration=registration, configuration_hash='d' * 64)


def test_program_principal_is_explicit_shared_and_has_all_typed_storage_contracts():
    plan = module.desired_plan()
    assert plan.principal == 'program_v4_runner'
    assert {'trading_portfolio_control_v3', 'trading_commit_v4'} <= plan.insert_arte
    assert not any('backtest' in table for table in plan.insert_arte)
    assert not {'trading_strategy_one_entry_evidence_v1', 'trading_strategy_one_add_evidence_v1'} & plan.insert_arte
    names = {table.name for table in module.storage_contracts()}
    assert plan.insert_arte <= names
    assert all('*' not in grant and 'CREATE' not in grant for grant in plan.grants())


def test_program_owner_is_immutable_exclusive_and_bound_to_installed_executable(monkeypatch):
    selected = lease(monkeypatch)
    selected.assert_current()
    assert '/program-v4/' in selected.owner.path(selected.run_id)
    assert selected.release()
    with pytest.raises(RuntimeError, match='lease lost'):
        selected.assert_current()


def test_program_writer_direct_mutation_and_unregistered_insert_are_forbidden(monkeypatch):
    selected = lease(monkeypatch)
    raw = Client(user=module.PRINCIPAL)
    client = module.ProgramV4WriterClient(raw, selected)
    assert client.execute('SELECT currentUser()') == module.PRINCIPAL
    with pytest.raises(RuntimeError, match='direct ClickHouse mutation'):
        client.execute('INSERT INTO arte.trading_event_v1 VALUES (1)')
    with pytest.raises(RuntimeError, match='registered typed dispatch'):
        client.execute_registered_insert('INSERT INTO arte.trading_portfolio_control_v3 (a) VALUES (1)',
            query_id='unregistered')
    assert not raw.inserts
    selected.release()


@pytest.mark.parametrize('reject', [False, True])
def test_managed_open_is_owned_offloop_and_closes_failed_native_preflight(monkeypatch, reject):
    selected = lease(monkeypatch)
    closed, threads = [], []
    raw = Client(user=module.PRINCIPAL)
    raw.close = lambda: closed.append(True)
    def open_raw(principal, **kwargs):
        assert principal == module.PRINCIPAL
        threads.append(threading.get_ident())
        return raw
    monkeypatch.setattr('src.backend.program_app_credentials._open_managed_client', open_raw)
    def preflight(client):
        assert type(client) is module.ProgramV4WriterClient
        if reject:
            raise RuntimeError('native schema/grant mismatch')
    monkeypatch.setattr(module, 'program_v4_preflight', preflight)
    async def run():
        limits = dict(max_threads=2, max_memory_usage=1000000, max_execution_time=10,
                      max_result_rows=100, max_result_bytes=10000)
        if reject:
            with pytest.raises(RuntimeError, match='schema/grant'):
                await module.open_program_v4_writer(selected, query_limits=limits)
            assert closed == [True]
        else:
            opened = await module.open_program_v4_writer(selected, query_limits=limits)
            assert not closed
            opened.close()
            assert closed == [True]
        assert threads[0] != threading.get_ident()
    try:
        asyncio.run(run())
    finally:
        selected.release()
