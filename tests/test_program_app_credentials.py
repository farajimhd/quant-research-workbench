"""Actual launcher bootstrap reaches lazy, isolated SELECT-only app readers."""
import asyncio
from threading import get_ident
from types import SimpleNamespace

import pytest

from src.backend import program_app_credentials as profiles
from src.backend import managed_backtest_credentials as managed
from tests.test_managed_backtest_credentials import _write_files


def test_launcher_loads_shared_profiles_and_native_owned_workers_close_readers(tmp_path, monkeypatch):
    from scripts import run_backend, service_manager
    from research.mlops import clickhouse
    from src.trading_runtime import arte_journal_writer, clickhouse_transport
    from src.backend.qmd_columnar_history_transport import _owned_worker
    _write_files(tmp_path)
    (tmp_path / 'backtest_v4_program_runner.env').write_text(
        'BACKTEST_V4_PROGRAM_RUNNER_CLICKHOUSE_URL=http://DESKTOP-SAAI85T:18123\n'
        'BACKTEST_V4_PROGRAM_RUNNER_CLICKHOUSE_USER=backtest_v4_program_runner\n'
        'BACKTEST_V4_PROGRAM_RUNNER_CLICKHOUSE_PASSWORD=synthetic-program-private\n')
    for name, keys in profiles.PROGRAM_APP_CREDENTIAL_FILES.items():
        principal = name.removesuffix('.env')
        values = ('http://DESKTOP-SAAI85T:18123', principal, 'synthetic-' + principal)
        (tmp_path / name).write_text(''.join(k + '=' + v + '\n' for k, v in zip(keys, values)))
    original = managed.load_managed_backtest_credentials
    monkeypatch.setattr(managed, 'load_managed_backtest_credentials', lambda **kw:
        original(**kw, workstation='DESKTOP-SAAI85T', secret_root=tmp_path))
    monkeypatch.setattr(service_manager, '_load_catalog', lambda:
        ({'backend': SimpleNamespace(environment={})}, None))
    env = {}
    run_backend._apply_service_environment(env)
    monkeypatch.setattr(arte_journal_writer.os, 'environ', env)
    created = []
    owner_thread = get_ident()
    class Client:
        def __init__(self, url, user, password, **kwargs):
            assert get_ident() != owner_thread
            assert url == 'http://192.168.1.218:18123'
            assert kwargs['default_query_params'] == {'readonly': '1'}
            assert user in (*profiles.PROGRAM_APP_PRINCIPALS.values(), 'backtest_v4_program_runner')
            self.user = user
            self.closed = False
            created.append(self)
        def close(self):
            self.closed = True
        def execute(self, query):
            if query == 'SELECT currentUser()':
                return self.user
            if query == "SELECT getSetting('readonly')":
                return '1'
            raise AssertionError(query)
    monkeypatch.setattr(clickhouse, 'ClickHouseHttpClient', Client)
    monkeypatch.setattr(clickhouse_transport, 'workstation_ipv4_transport', lambda url:
        'http://192.168.1.218:18123')
    factories = [profiles.program_app_reader_factory(role) for role in
        ('market_input_reader', 'release_reader', 'selection_reader', 'journal_reader')]
    assert not created
    async def read(factory):
        def owned():
            client = factory()
            try:
                if client.user == 'backtest_v4_program_runner':
                    from src.backend.backtest_fixed_journal_bootstrap import _v4_cold_reader_preflight
                    _v4_cold_reader_preflight(client)
                return id(client)
            finally:
                client.close()
        return await _owned_worker(owned)
    async def all_reads():
        return await asyncio.gather(*(read(f) for f in factories))
    identities = asyncio.run(all_reads())
    assert len(set(identities)) == len(created) == 4
    assert all(c.closed for c in created)


def test_optional_profile_conflict_leaves_all_existing_environment_values_unchanged(tmp_path):
    _write_files(tmp_path)
    name = 'program_release_reader.env'
    keys = profiles.PROGRAM_APP_CREDENTIAL_FILES[name]
    (tmp_path / name).write_text(''.join(k + '=' + v + '\n' for k, v in zip(keys,
        ('http://DESKTOP-SAAI85T:18123', 'program_release_reader', 'synthetic-private'))))
    env = {keys[1]: 'different'}
    before = dict(env)
    with pytest.raises(ValueError, match='conflicts'):
        managed.load_managed_backtest_credentials(environment=env,
            workstation='DESKTOP-SAAI85T', secret_root=tmp_path)
    assert env == before


@pytest.mark.parametrize('role', ['history_publisher', 'release_publisher', 'selection_publisher', 'unknown'])
def test_reader_factory_never_accepts_writer_authority(role):
    with pytest.raises(ValueError, match='read-only role'):
        profiles.program_app_reader_factory(role)


@pytest.mark.parametrize('url,user,password', [
    ('http://other-host:18123', 'program_release_reader', 'private'),
    ('http://DESKTOP-SAAI85T:18123?x=1', 'program_release_reader', 'private'),
    ('http://DESKTOP-SAAI85T:18123', 'program_release_publisher', 'private'),
    ('http://DESKTOP-SAAI85T:18123', 'program_release_reader', ''),
    ('http://DESKTOP-SAAI85T:private-value', 'program_release_reader', 'private'),
])
def test_changed_profile_is_rejected_before_connection(monkeypatch, url, user, password):
    from src.trading_runtime import arte_journal_writer
    from research.mlops import clickhouse
    monkeypatch.setattr(arte_journal_writer, '_dedicated_clickhouse_credentials',
        lambda *args: (url, user, password))
    monkeypatch.setattr(clickhouse, 'ClickHouseHttpClient', lambda *args, **kwargs:
        pytest.fail('Created connection for a different private profile'))
    with pytest.raises(ValueError, match='managed profile'):
        profiles.program_app_reader_factory('release_reader')()


@pytest.mark.parametrize('role', sorted(profiles._WRITE_ROLES))
def test_writer_factories_use_only_the_selected_private_profile(monkeypatch, role):
    from src.trading_runtime import arte_journal_writer, clickhouse_transport
    from research.mlops import clickhouse
    from src.backend.qmd_columnar_history_transport import _owned_worker
    opened = []
    main = get_ident()
    principal = profiles.PROGRAM_APP_PRINCIPALS[role]
    def credentials(prefix, path):
        assert get_ident() != main
        assert prefix == principal.upper() + '_CLICKHOUSE_'
        return 'http://DESKTOP-SAAI85T:18123', principal, 'synthetic-private'
    monkeypatch.setattr(arte_journal_writer, '_dedicated_clickhouse_credentials', credentials)
    monkeypatch.setattr(clickhouse_transport, 'workstation_ipv4_transport', lambda _: 'http://192.168.1.218:18123')
    class Client:
        def __init__(self, url, user, password, **kwargs):
            opened.append((user, kwargs))
        def close(self):
            opened.append('closed')
    monkeypatch.setattr(clickhouse, 'ClickHouseHttpClient', Client)
    limits = dict(max_threads=2, max_memory_usage=1024, max_execution_time=30,
        max_result_rows=10, max_result_bytes=2048)
    factory = profiles.program_app_writer_factory(role, query_limits=limits)
    limits['max_threads'] = 999
    assert not opened
    def owned():
        client = factory()
        client.close()
    asyncio.run(_owned_worker(owned))
    assert opened[0][0] == principal
    settings = opened[0][1]['default_query_params']
    assert settings['max_threads'] == 2 and 'readonly' not in settings
    assert settings['max_bytes_before_external_sort'] == 0
    assert settings['max_bytes_before_external_group_by'] == 0
    assert opened[-1] == 'closed'


@pytest.mark.parametrize('role', ['market_input_reader', 'journal_reader', 'unknown'])
def test_writer_roles_cannot_substitute_read_or_admin_authority(role):
    with pytest.raises(ValueError):
        profiles.program_app_writer_factory(role, query_limits={})


def test_bounded_producer_reader_keeps_readonly_and_no_spill(monkeypatch):
    opened = []
    monkeypatch.setattr(profiles, '_open_managed_client', lambda principal, **kwargs:
        opened.append((principal, kwargs)))
    limits = dict(max_threads=2, max_memory_usage=1024, max_execution_time=30,
        max_result_rows=10, max_result_bytes=2048)
    factory = profiles.program_app_reader_factory('market_input_reader', query_limits=limits)
    limits['max_memory_usage'] = 999
    assert not opened
    factory()
    assert opened[0][0] == 'program_market_input_reader'
    settings = opened[0][1]['settings']
    assert settings['readonly'] == '1' and settings['max_memory_usage'] == 1024
    assert settings['max_bytes_before_external_sort'] == 0


@pytest.mark.parametrize('limits', [{}, {'readonly': 0}, dict(max_threads=True,
    max_memory_usage=1024, max_execution_time=30, max_result_rows=10, max_result_bytes=2048)])
def test_invalid_query_settings_fail_before_private_credentials(limits, monkeypatch):
    monkeypatch.setattr(profiles, '_open_managed_client', lambda *args, **kwargs:
        pytest.fail('Opened invalid managed connection'))
    with pytest.raises(ValueError):
        profiles.program_app_reader_factory('market_input_reader', query_limits=limits)
    with pytest.raises(ValueError):
        profiles.program_app_writer_factory('history_publisher', query_limits=limits)
