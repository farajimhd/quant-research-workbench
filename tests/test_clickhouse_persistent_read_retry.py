"""An idle keep-alive may be retried only for an idempotent read."""
from __future__ import annotations

import http.client
from unittest.mock import MagicMock

import pytest

from research.mlops.clickhouse import ClickHouseHttpClient


@pytest.mark.parametrize("failure", [
    http.client.RemoteDisconnected("idle socket closed"),
    ConnectionAbortedError(10053, "host aborted connection"),
    ConnectionResetError(10054, "connection reset"),
    BrokenPipeError(32, "broken pipe"),
    TimeoutError("read timed out"),
])
def test_reconnects_one_plain_select_after_transport_close(monkeypatch, failure):
    client = ClickHouseHttpClient("http://localhost:8123", "reader", "secret",
                                  persistent=True)
    calls = []

    def execute(sql, params):
        calls.append(sql)
        if len(calls) == 1:
            raise failure
        return "ok"

    monkeypatch.setattr(client, "_execute_persistent", execute)
    assert client.execute("  SELECT 1") == "ok"
    assert calls == ["  SELECT 1", "  SELECT 1"]


@pytest.mark.parametrize("sql", ["INSERT INTO arte.x VALUES (1)",
                                       b"INSERT INTO arte.x FORMAT RowBinary\n\xff\x00\x80",
                                       "ALTER TABLE arte.x DELETE WHERE 1",
                                       "WITH 1 AS x SELECT x"])
@pytest.mark.parametrize("failure", [
    http.client.RemoteDisconnected("no response"),
    ConnectionAbortedError(10053, "host aborted connection"),
])
def test_never_replays_ambiguous_non_select(monkeypatch, sql, failure):
    client = ClickHouseHttpClient("http://localhost:8123", "writer", "secret",
                                  persistent=True)
    calls = []

    def execute(statement, params):
        calls.append(statement)
        raise failure

    monkeypatch.setattr(client, "_execute_persistent", execute)
    with pytest.raises(type(failure)):
        client.execute(sql)
    assert calls == [sql]


def test_writer_reopens_idle_socket_before_sending_insert(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("research.mlops.clickhouse.time.monotonic", lambda: now[0])
    sockets = [MagicMock(), MagicMock()]
    for connection in sockets:
        response = MagicMock(status=200, reason="OK", will_close=False)
        response.read.return_value = b""
        connection.getresponse.return_value = response
    client = ClickHouseHttpClient(
        "http://localhost:8123", "writer", "secret", persistent=True,
        max_persistent_idle_seconds=5)
    monkeypatch.setattr(client, "_new_connection", lambda: sockets.pop(0))
    client.execute("INSERT INTO arte.x VALUES (1)")
    first = client._connection
    now[0] = 6.0
    client.execute("INSERT INTO arte.x VALUES (2)")
    first.close.assert_called_once()
    assert client._connection is not first
    assert first.request.call_count == 1
    assert client._connection.request.call_count == 1


def test_binary_insert_body_is_sent_without_utf8_conversion(monkeypatch):
    connection = MagicMock()
    response = MagicMock(status=200, reason='OK', will_close=False)
    response.read.return_value = b''
    connection.getresponse.return_value = response
    client = ClickHouseHttpClient('http://localhost:8123', 'writer', 'secret', persistent=True)
    monkeypatch.setattr(client, '_new_connection', lambda: connection)
    payload = b'INSERT INTO arte.x FORMAT RowBinary\n\xff\x00\x80'
    assert client.execute(payload, query_id='binary-1') == ''
    assert connection.request.call_args.kwargs['body'] is payload
    assert connection.request.call_args.kwargs['headers']['Content-Type'] == 'application/octet-stream'


def test_nonpersistent_binary_insert_body_is_sent_without_conversion(monkeypatch):
    response = MagicMock()
    response.__enter__.return_value.read.return_value = b''
    requests = []
    def open_request(req, timeout):
        requests.append(req)
        return response
    monkeypatch.setattr('research.mlops.clickhouse.request.urlopen', open_request)
    client = ClickHouseHttpClient('http://localhost:8123', 'writer', 'secret')
    payload = b'INSERT INTO arte.x FORMAT RowBinary\n\xff\x00\x80'
    assert client.execute(payload) == ''
    assert requests[0].data is payload
