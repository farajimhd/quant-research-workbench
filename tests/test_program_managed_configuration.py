"""Managed selection composition verifies central pins and closes owned sessions."""
import asyncio
from threading import get_ident
from types import SimpleNamespace

import pytest

from src.backend import program_managed_configuration as managed
from src.trading_runtime.strategy_registry import ProgramStrategyExecutorRegistration
from tests.test_strategy_configuration_selection import prepare


@pytest.mark.parametrize('mode', ['complete', 'release_mismatch', 'publish_failure', 'code_changed', 'reference_failure'])
def test_managed_operator_cold_checks_before_claim_and_closes_on_failure(monkeypatch, mode):
    selected = prepare()
    registration = ProgramStrategyExecutorRegistration(selected.header['strategy_id'],
        selected.header['strategy_revision'], selected.header['program_release_hash'], 'c' * 64, 'd' * 64)
    stages, closed = [], []
    main = get_ident()
    monkeypatch.setattr('src.backend.backtest_journal_clickhouse.backtest_code_hash',
        lambda _: '0' * 64 if mode == 'code_changed' else registration.expected_code_hash)
    def reader_factory(role, **kwargs):
        def owned():
            assert get_ident() != main
            stages.append(role)
            return SimpleNamespace(base_url='http://synthetic-central', close=lambda: closed.append(role))
        return owned
    monkeypatch.setattr(managed, 'program_app_reader_factory', reader_factory)
    monkeypatch.setattr(managed, 'program_app_writer_factory', reader_factory)
    async def load(*args, **kwargs):
        stages.append('release-verified')
        assert kwargs['expected_content_hash'] == registration.expected_content_hash
        return SimpleNamespace(header=dict(strategy_id=selected.header['strategy_id'],
            release_id='different' if mode == 'release_mismatch' else selected.header['program_release_id']))
    monkeypatch.setattr(managed, 'load_program_release', load)
    monkeypatch.setattr(managed, 'register_program_strategy_executor', lambda value: stages.append('registered'))
    def session():
        assert get_ident() != main
        stages.append('session')
        return SimpleNamespace(client=object(), close=lambda: closed.append('session'))
    monkeypatch.setattr(managed, 'open_workstation_keeper_session', session)
    def references(actual, packet):
        assert get_ident() != main and packet == selected
        stages.append('references-verified')
        if mode == 'reference_failure':
            raise ValueError('Policy reference unavailable')
    monkeypatch.setattr(managed.ConfigurationReferenceReader, 'verify', references)
    async def publish(**kwargs):
        stages.append('native-publication')
        assert kwargs['reference_reader'].endpoint == 'http://synthetic-central'
        assert kwargs['source'] == selected.rows and kwargs['concurrency'] == 2
        if mode == 'publish_failure':
            raise ValueError('Native reference changed')
        return selected
    monkeypatch.setattr(managed, 'publish_configuration_selection', publish)
    async def run():
        pending = managed.publish_managed_program_configuration(selected.rows, registration,
            query_limits={}, concurrency=2)
        if mode == 'complete':
            assert await pending == selected
        else:
            with pytest.raises(ValueError):
                await pending
    asyncio.run(run())
    if mode == 'code_changed':
        assert not stages and not closed
    elif mode == 'release_mismatch':
        assert stages == ['release-verified'] and not closed
    else:
        expected = ['release-verified', 'registered', 'selection_reader', 'session', 'references-verified']
        if mode != 'reference_failure': expected.append('native-publication')
        assert stages == expected
        assert closed == ['selection_reader', 'session']


def test_changed_selection_pin_fails_before_reader_factories(monkeypatch):
    selected = prepare()
    registration = ProgramStrategyExecutorRegistration(selected.header['strategy_id'],
        selected.header['strategy_revision'], '0' * 64, 'c' * 64, 'd' * 64)
    monkeypatch.setattr(managed, 'program_app_reader_factory', lambda *args, **kwargs:
        pytest.fail('Opened a mismatched configuration authority'))
    with pytest.raises(ValueError, match='approved programme release'):
        asyncio.run(managed.publish_managed_program_configuration(selected.rows, registration,
            query_limits={}, concurrency=2))
