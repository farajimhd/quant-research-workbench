"""An idle keep-alive may be retried only for an idempotent read."""
from __future__ import annotations

import http.client

import pytest

from research.mlops.clickhouse import ClickHouseHttpClient


def test_reconnects_one_plain_select_after_idle_close(monkeypatch):
    client = ClickHouseHttpClient("http://localhost:8123", "reader", "secret",
                                  persistent=True)
    calls = []

    def execute(sql, params):
        calls.append(sql)
        if len(calls) == 1:
            raise http.client.RemoteDisconnected("idle socket closed")
        return "ok"

    monkeypatch.setattr(client, "_execute_persistent", execute)
    assert client.execute("  SELECT 1") == "ok"
    assert calls == ["  SELECT 1", "  SELECT 1"]


@pytest.mark.parametrize("sql", ["INSERT INTO arte.x VALUES (1)",
                                       "ALTER TABLE arte.x DELETE WHERE 1",
                                       "WITH 1 AS x SELECT x"])
def test_never_replays_ambiguous_non_select(monkeypatch, sql):
    client = ClickHouseHttpClient("http://localhost:8123", "writer", "secret",
                                  persistent=True)
    calls = []

    def execute(statement, params):
        calls.append(statement)
        raise http.client.RemoteDisconnected("no response")

    monkeypatch.setattr(client, "_execute_persistent", execute)
    with pytest.raises(http.client.RemoteDisconnected):
        client.execute(sql)
    assert calls == [sql]
