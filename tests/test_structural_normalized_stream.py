from types import SimpleNamespace
from copy import deepcopy
import pytest

from research.level_book.v7.normalized_stream import normalized_stream
from pipelines.market_sip.events import structural_checkpoint_storage as storage
from src.backend.structural_v7_seed import _seed_interval_rows


class Http:
    structural_stream_reads = True

    def __init__(self, rows):
        self.rows = rows
        self.calls = []
        self.closed = False

    def iter_json_each_row(self, sql, **kwargs):
        self.calls.append((sql, kwargs))
        try:
            yield from deepcopy(self.rows)
        finally:
            self.closed = True


@pytest.mark.parametrize('count', [0, 1, 20001])
def test_single_scan_exact_rows_and_scoped_limits(count):
    rows = [dict(observation_id=str(i)) for i in range(count)]
    http = Http(rows)
    assert list(normalized_stream(http, 'SELECT * FROM arte.x FINAL PREWHERE 1', expected=count)) == rows
    assert len(http.calls) == 1 and http.closed
    sql, kwargs = http.calls[0]
    assert f'LIMIT {count + 1} FORMAT JSONEachRow' in sql
    assert kwargs['query_params']['max_block_size'] == 5000
    assert kwargs['query_params']['result_overflow_mode'] == 'throw'
    assert 'max_memory_usage' not in kwargs['query_params']


@pytest.mark.parametrize('actual,expected', [(0, 1), (1, 0), (2, 1)])
def test_no_silent_truncation(actual, expected):
    http = Http([{}] * actual)
    with pytest.raises(ValueError, match='snapshot differs'):
        list(normalized_stream(http, 'SELECT * FROM arte.x FINAL PREWHERE 1', expected=expected))
    assert http.closed


def test_snapshot_checks_every_field_and_closes_on_corruption():
    expected = [dict(observation_id='a', price=2.0), dict(observation_id='b', price=3.0)]
    http = Http(expected)
    storage.verify_snapshot(SimpleNamespace(http=http), storage.OBSERVATIONS_TABLE, expected, '1')
    assert len(http.calls) == 1
    http = Http([expected[0], dict(observation_id='b', price=4.0)])
    with pytest.raises(ValueError, match='snapshot differs'):
        storage.verify_snapshot(SimpleNamespace(http=http), storage.OBSERVATIONS_TABLE, expected, '1')
    assert http.closed


def test_seed_retains_canonical_identity_order_after_stream():
    rows = [dict(level_id='z', observation_id='a', valid_from='2026-01-01'),
            dict(level_id='a', observation_id='b', valid_from='2026-01-02')]
    http = Http(rows)
    actual = _seed_interval_rows(http, table='arte.x', columns='level_id,observation_id',
        keys="ticker='T'", predicate='1', identity='observation_id', expected=2)
    assert actual == list(reversed(rows))
    assert len(http.calls) == 1


def test_partial_response_failure_is_propagated_and_closed():
    class Broken(Http):
        def iter_json_each_row(self, *args, **kwargs):
            try:
                yield dict(observation_id='a')
                raise TimeoutError('partial stream')
            finally:
                self.closed = True
    http = Broken([])
    with pytest.raises(TimeoutError, match='partial stream'):
        list(normalized_stream(http, 'SELECT * FROM arte.x FINAL PREWHERE 1', expected=2))
    assert http.closed


def test_json_transport_retry_and_limits_do_not_leak(monkeypatch):
    import io
    from urllib.error import URLError
    from research.mlops.clickhouse import ClickHouseHttpClient
    calls = []
    def open_response(req, **kwargs):
        calls.append(req)
        if len(calls) == 1:
            raise URLError(TimeoutError('connect'))
        return io.BytesIO(b'{"x":1}\n')
    monkeypatch.setattr('research.mlops.clickhouse.request.urlopen', open_response)
    client = ClickHouseHttpClient('http://unused', '', '',
        default_query_params={'max_result_rows': 10, 'max_threads': 1})
    assert list(client.iter_json_each_row('SELECT 1 FORMAT JSONEachRow',
        query_id='same-read', query_params={'max_result_rows': 2})) == [{'x': 1}]
    assert calls[0] is calls[1]
    assert client.default_query_params == {'max_result_rows': '10', 'max_threads': '1'}


def test_json_transport_does_not_retry_consumed_rows(monkeypatch):
    from research.mlops.clickhouse import ClickHouseHttpClient
    calls = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def __iter__(self):
            yield b'{"x":1}\n'
            raise TimeoutError('partial')
    def open_response(*args, **kwargs):
        calls.append(1)
        return Response()
    monkeypatch.setattr('research.mlops.clickhouse.request.urlopen', open_response)
    client = ClickHouseHttpClient('http://unused', '', '')
    rows = client.iter_json_each_row('SELECT 1 FORMAT JSONEachRow')
    assert next(rows) == {'x': 1}
    with pytest.raises(TimeoutError):
        next(rows)
    assert len(calls) == 1
