"""Declared authority remains active through actual public saved-read operations."""
from types import SimpleNamespace
import pytest
from src.backend import backtest_v4_saved_review as review
from src.backend import backtest_recorded_journal as recorded
from src.backend.typed_backtest_review_core import AuditedSessionCache
from src.trading_runtime import arte_journal_writer as writer
from test_backtest_v4_saved_review import RUN, _context, _prefix, Client


@pytest.fixture
def readers(monkeypatch):
    import research.mlops.clickhouse as transport
    opened = []
    class Reader(Client):
        def __init__(self, url, user, password, **kwargs):
            self.base_url, self.user, self.password = url, user, password
            self.params = kwargs['default_query_params']
            self.closed = False
            opened.append(self)
        def close(self):
            self.closed = True
    monkeypatch.setattr(transport, 'ClickHouseHttpClient', Reader)
    for stem, user in [('BACKTEST_V4_ENTRY_COST_RUNNER', 'backtest_v4_entry_cost_runner'),
                       ('BACKTEST_V4_LADDER_RUNNER', 'backtest_v4_ladder_runner')]:
        monkeypatch.delenv(stem + '_CREDENTIAL_FILE', raising=False)
        for suffix, value in [('URL', 'http://127.0.0.1:8123'), ('USER', user), ('PASSWORD', 'test-only')]:
            monkeypatch.setenv(stem + '_CLICKHOUSE_' + suffix, value)
    def preflight(client):
        assert client.params['readonly'] == 1
        assert client.v4_batched_detail_readback is True
        assert client.user in {'backtest_v4_entry_cost_runner', 'backtest_v4_ladder_runner'}
    monkeypatch.setattr(writer, '_v4_preflight', preflight)
    return opened


def context_setup(monkeypatch, number):
    from src.backend import backtest_market_data as market
    from src.backend import backtest_strategy_one_configuration as configurations
    from src.backend import historical_runtime_versions as versions
    context = {**_context(), 'strategy_revision': number, 'configuration_hash': 'a' * 64}
    monkeypatch.setattr(review, 'load_typed_run_context', lambda *_: context)
    monkeypatch.setattr(recorded, 'load_typed_run_context', lambda *_: context)
    monkeypatch.setattr(market, 'readonly_clickhouse_client', lambda **_: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(configurations, 'certify_numbered_configuration', lambda *_: SimpleNamespace(payload_hash='a' * 64))
    monkeypatch.setattr(versions, 'backend_source_fingerprint', lambda: versions.LOADED_BACKEND_FINGERPRINT)
    monkeypatch.setattr(review, '_saved_twenty_price_source', lambda *_: object())
    return context


@pytest.mark.parametrize('number', [1, 49, 57, 58])
def test_terminal_real_operation_cache_hit_miss_keeps_profile_for_events(monkeypatch, readers, number):
    context_setup(monkeypatch, number)
    audited = []
    def checked(client):
        if number != 1:
            assert client.user == ('backtest_v4_ladder_runner' if number == 49 else 'backtest_v4_entry_cost_runner')
            assert not client.closed
    def prefix(client, *_, **__):
        checked(client); audited.append(client); return _prefix()
    monkeypatch.setattr(review, 'load_verified_v4_prefix', prefix)
    monkeypatch.setattr(review, 'load_terminal_backtest_snapshot', lambda *_, **__: {'state_hash': 'a'*64, 'state_revision': 1, 'snapshot_at': '2026-08-18T13:30:00+00:00'})
    monkeypatch.setattr(review, 'load_latest_backtest_cursor', lambda *_: {'session_date': '2026-08-18', 'boundary_ms': 34200000})
    monkeypatch.setattr(review, 'load_committed_initial_cash', lambda *_, **__: 10000.)
    monkeypatch.setattr(review, '_terminal_financial_accounts', lambda *_: {'SIM-01-A': {'net_liquidation': 10000.}})
    monkeypatch.setattr(review, '_head_matches', lambda client, *_: checked(client) or True)
    event_clients = []
    def events(client, *_, **__):
        checked(client); event_clients.append(client); return ()
    monkeypatch.setattr(review, 'load_typed_event_page', events)
    cache = AuditedSessionCache()
    client = Client()
    for _ in range(2):
        assert review.load_v4_terminal_review_page(client, RUN, cache=cache)['verified_sequence'] == 2
    assert len(audited) == 1
    assert len(event_clients) == 2
    assert len(readers) == (0 if number == 1 else 2)
    assert all(reader.closed for reader in readers)
    assert review._DECLARED_READ_SCOPE.get() is None


@pytest.mark.parametrize('failure', ['preflight', 'detail'])
def test_failure_closes_selected_reader_and_never_continues_default(monkeypatch, readers, failure):
    context_setup(monkeypatch, 57)
    def fail(*_):
        raise RuntimeError('expected failure')
    if failure == 'preflight':
        monkeypatch.setattr(writer, '_v4_preflight', fail)
        monkeypatch.setattr(review, 'load_verified_v4_prefix', lambda *_: pytest.fail('preflight bypass'))
    else:
        def prefix(client, *_, **__):
            assert client.entry_spread_risk_profile and not client.closed
            fail()
        monkeypatch.setattr(review, 'load_verified_v4_prefix', prefix)
    with pytest.raises(RuntimeError, match='expected failure'):
        review.load_v4_terminal_review_page(Client(), RUN, cache=AuditedSessionCache())
    assert len(readers) == 1 and readers[0].closed
    assert review._DECLARED_READ_SCOPE.get() is None


def test_recorded_cold_operation_reads_details_only_selected_profile(monkeypatch, readers):
    context_setup(monkeypatch, 57)
    monkeypatch.setattr(recorded, '_CACHE', AuditedSessionCache())
    seen = []
    def rows(client, query):
        assert client.entry_spread_risk_profile and not client.closed
        seen.append(query)
        return []
    monkeypatch.setattr(recorded, '_rows', rows)
    with pytest.raises(ValueError, match='commit inventory is absent'):
        recorded.load_recorded_page(Client(), RUN)
    assert len(seen) == 1 and 'trading_commit_v4' in seen[0]
    assert readers[0].closed


def test_report_wrapper_preserves_all_flags_and_nested_operation_scope(monkeypatch, readers):
    from scripts.clickhouse.report_strategy_one_trades import SelectOnly
    context = context_setup(monkeypatch, 57)
    calls = []
    @review.declared_saved_read_operation
    def detail(client, run_id):
        review._require_declared_read_profile(client, context)
        assert client.v4_batched_detail_readback
        calls.append(client)
    @review.declared_saved_read_operation
    def report(client, market, run_id):
        detail(client, str(__import__("uuid").UUID(run_id)))
        detail(client, str(__import__("uuid").UUID(run_id)))
        assert client.entry_spread_risk_profile
        return client
    wrapped = SelectOnly(Client())
    selected = report(wrapped, object(), '{' + RUN.upper() + '}')
    assert isinstance(selected, SelectOnly)
    assert calls == [selected, selected]
    assert len(readers) == 1 and readers[0].closed

def test_recorded_batched_cost_family_select_uses_live_dedicated_reader(monkeypatch, readers):
    import json
    import research.mlops.clickhouse as transport
    context_setup(monkeypatch, 57)
    monkeypatch.setattr(recorded, '_CACHE', AuditedSessionCache())
    cost_reads = []
    def execute(client, query):
        assert client.user == 'backtest_v4_entry_cost_runner'
        assert client.params['readonly'] == 1 and not client.closed
        if 'FROM arte.trading_commit_v4 ' in query:
            return json.dumps({'batch_id': '00000000-0000-0000-0000-000000000002'})
        if 'FROM arte.trading_commit_family_v4 ' in query:
            return json.dumps({'family_name': 'trading_entry_spread_risk_v4', 'row_count': 1})
        assert 'FROM arte.trading_entry_spread_risk_v4 ' in query
        assert 'toJSONString(tuple(' in query
        cost_reads.append(query)
        # Real batched decoder must reject foreign sealed data, not fabricate acceptance.
        return json.dumps({'family_name': 'foreign-mutated-family', 'payload': '[]'})
    monkeypatch.setattr(transport.ClickHouseHttpClient, 'execute', execute, raising=False)
    with pytest.raises(RuntimeError, match='foreign family'):
        recorded.load_recorded_page(Client(), RUN)
    assert len(cost_reads) == 1
    assert len(readers) == 1 and readers[0].closed
