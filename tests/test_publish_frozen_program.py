"""Runnable release planning and native publication without real operations."""
import asyncio
from dataclasses import replace
from datetime import date, timedelta
from threading import get_ident
from types import SimpleNamespace

import pytest

from scripts import publish_frozen_program as operator
from tests.test_optimized_program_specification import source
from tests.test_columnar_strategy_publication import Client, Keeper
from src.trading_runtime.columnar_strategy_release import TABLES


@pytest.mark.parametrize('publishing', [False, True])
def test_cli_reads_base_environment_only_for_approved_publication(monkeypatch, publishing):
    from research.mlops import env
    args = SimpleNamespace(publish=publishing)
    events = []
    monkeypatch.setattr(operator, 'parse_args', lambda argv: args)
    monkeypatch.setattr(env, 'discover_env_files', lambda root: (root / '.env',))
    def load(paths, *, verbose):
        assert publishing and verbose is False
        assert paths == (operator.Path(operator.__file__).resolve().parents[1] / '.env',)
        events.append('base_environment')
    monkeypatch.setattr(env, 'load_env_files', load)
    async def run(selected):
        assert selected is args
        assert events == (['base_environment'] if publishing else [])
        return 0
    monkeypatch.setattr(operator, 'run', run)
    assert operator.main([]) == 0


def arguments():
    return ['--reference-root', 'synthetic-reference', '--clock-root', 'synthetic-clocks',
        '--strategy-number', '62', '--strategy-id', 'synthetic-shared-program',
        '--release-id', '00000000-0000-0000-0000-000000000062',
        '--published-at', '2026-10-13T00:00:00+00:00', '--code-hash', 'd' * 64,
        '--history-code-hash', '5' * 64, '--universe-source-hash', 'e' * 64,
        '--concurrency', '2', '--max-threads', '2', '--max-memory-bytes', '1048576',
        '--query-timeout', '30', '--max-result-rows', '100000', '--max-result-bytes', '16000000']


@pytest.mark.parametrize('mode', ['plan', 'publish', 'changed_release', 'ambiguous'])
def test_operator_reaches_native_scalar_publication_and_owns_session(monkeypatch, capsys, mode):
    from src.backend import program_app_credentials
    from src.trading_runtime import keeper_session
    monkeypatch.setattr('src.backend.backtest_journal_clickhouse.backtest_code_hash', lambda _: 'd' * 64)
    reference, _, _, _ = source(monkeypatch)
    reference = replace(reference, sessions={
        (date(2026, 7, 30) + timedelta(days=i)).isoformat(): {} for i in range(30)})
    main = get_ident()
    reads, closed, connections = [], [], []
    def read(root, **kwargs):
        reads.append(get_ident())
        assert root == 'synthetic-reference' and kwargs == dict(concurrency=2, input_root='synthetic-clocks')
        return reference
    monkeypatch.setattr(operator, 'read_reference', read)
    monkeypatch.setattr(operator.platform, 'node', lambda: 'DESKTOP-SAAI85T')
    stored = {table.name: () for table in TABLES}
    keeper = Keeper()
    def open_session():
        assert get_ident() != main
        connections.append('keeper')
        return SimpleNamespace(client=keeper, close=lambda: closed.append(get_ident()))
    monkeypatch.setattr(keeper_session, 'open_workstation_keeper_session', open_session)
    def factory(role, **kwargs):
        assert kwargs['query_limits']['max_execution_time'] == 30
        def open_client():
            assert get_ident() != main
            client = Client(stored, writer=role == 'release_publisher',
                fail=TABLES[0].name if mode == 'ambiguous' and role == 'release_publisher' else None)
            connections.append(client)
            return client
        return open_client
    monkeypatch.setattr(program_app_credentials, 'program_app_reader_factory', factory)
    monkeypatch.setattr(program_app_credentials, 'program_app_writer_factory', factory)
    args = operator.parse_args(arguments())
    assert asyncio.run(operator.run(args)) == 0
    output = capsys.readouterr().out
    content_hash = output.split('Release ')[1].split(' |')[0]
    assert '30 training sessions verified' in output and not connections
    if mode != 'plan':
        expected = '0' * 64 if mode == 'changed_release' else content_hash
        args = operator.parse_args(arguments() + ['--publish', '--expected-release-hash', expected])
        if mode == 'changed_release':
            with pytest.raises(ValueError, match='reviewed publication hash'):
                asyncio.run(operator.run(args))
            assert not connections
        elif mode == 'ambiguous':
            with pytest.raises(RuntimeError, match='ambiguous INSERT'):
                asyncio.run(operator.run(args))
            assert not stored[TABLES[-1].name]
        else:
            assert asyncio.run(operator.run(args)) == 0
            assert stored[TABLES[-1].name]
            assert next(iter(keeper.claims.values()))[0].startswith(b'1\ncommitted\n')
        if mode != 'changed_release':
            assert len(closed) == 1 and closed[0] != main
            assert all(client.closed for client in connections if client != 'keeper')
    assert reads and all(thread != main for thread in reads)


def test_publish_requires_reviewed_hash_before_any_source_read():
    with pytest.raises(SystemExit):
        operator.parse_args(arguments() + ['--publish'])


def test_changed_native_code_blocks_before_frozen_reads(monkeypatch):
    monkeypatch.setattr('src.backend.backtest_journal_clickhouse.backtest_code_hash', lambda _: 'a' * 64)
    monkeypatch.setattr(operator, 'read_reference', lambda *args, **kwargs:
        pytest.fail('Read source for a changed native code closure'))
    with pytest.raises(ValueError, match='native Backtest source closure'):
        operator.prepare_release(operator.parse_args(arguments()))
