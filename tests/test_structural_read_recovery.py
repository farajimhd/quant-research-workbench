from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from scripts import backfill_structural_sessions as runner


def client(monkeypatch, outcomes):
    calls = []
    def query(self, sql, **kwargs):
        calls.append((sql, kwargs))
        value = outcomes.pop(0)
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(runner.builder.Client, 'query', query)
    monkeypatch.setattr('time.sleep', lambda _: None)
    obj = object.__new__(runner.StructuralClient)
    obj.profile_totals = {}
    obj.http = SimpleNamespace(close=lambda: None)
    return obj, calls


def test_source_read_retries_whole_buffer_and_counts_recovery(monkeypatch):
    obj, calls = client(monkeypatch, [ConnectionResetError(), [{'certified': 1}]])
    assert obj.query('SELECT source_hash FROM certificates') == [{'certified': 1}]
    assert len(calls) == 2
    assert obj.profile_totals['transport_retries']['count'] == 1


@pytest.mark.parametrize('error', [ValueError('hash mismatch'), HTTPError('url', 500, 'server', {}, None)])
def test_integrity_and_server_errors_are_not_retried(monkeypatch, error):
    obj, calls = client(monkeypatch, [error])
    with pytest.raises(type(error)):
        obj.query('SELECT source_hash FROM certificates')
    assert len(calls) == 1


def test_retry_exhaustion_is_bounded(monkeypatch):
    obj, calls = client(monkeypatch, [ConnectionResetError() for _ in range(3)])
    with pytest.raises(ConnectionResetError):
        obj.query('SELECT 1')
    assert len(calls) == 3


@pytest.mark.parametrize('sql,read', [('INSERT INTO t SELECT 1', False), ('INSERT INTO t SELECT 1', True)])
def test_never_retry_writes(monkeypatch, sql, read):
    obj, calls = client(monkeypatch, [ConnectionResetError()])
    with pytest.raises(ConnectionResetError):
        obj.query(sql, read=read)
    assert len(calls) == 1
