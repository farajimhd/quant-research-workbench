"""Terminal V3 INSERTs have durable operation admission and cold inventory."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.backend.backtest_terminal_v3_dispatch import TerminalV3DispatchClient
from src.trading_runtime.arte_typed_insert_dispatch import (
    TypedInsertDispatch, _Gate, _gate_path,
)
from src.trading_runtime.keeper_ownership import KeeperUnavailable
from tests.test_keeper_ownership import _Client, _Store


RUN = "run-terminal-dispatch"
BATCH = "00000000-0000-0000-0000-000000000e03"
SQL = ("INSERT INTO arte.trading_backtest_terminal_commit_v3 (run_id) "
       "SETTINGS async_insert=1,wait_for_async_insert=1,insert_deduplicate=1,"
       f"insert_deduplication_token='terminal-v3:{BATCH}:commit' "
       "FORMAT JSONEachRow\n{\"run_id\":\"run-terminal-dispatch\"}")


class Keeper(_Client):
    def get_children(self, path):
        return [item[len(path) + 1:] for item in self.store.nodes
                if item.startswith(path + "/") and "/" not in item[len(path) + 1:]]


class Writer:
    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail

    def execute(self, sql, *, query_id):
        self.calls.append((sql, query_id))
        if self.fail:
            raise TimeoutError("lost response")
        return ""


def _client(*, fail=False):
    keeper = Keeper(_Store(), 8)
    keeper.ensure_path(_gate_path(RUN).rsplit("/", 1)[0])
    keeper.create(_gate_path(RUN), _Gate(
        "closed", 0, 2, 0, 3, BATCH, "a" * 64,
        "00000000-0000-0000-0000-000000000000").wire())
    dispatch = TypedInsertDispatch(keeper)
    barrier = SimpleNamespace(authority=dispatch, run_id=RUN,
                              assert_fenced=lambda run_id: None)
    fence = SimpleNamespace(barrier=barrier, retain_on_failure=False)
    writer = Writer(fail=fail)
    client = TerminalV3DispatchClient(
        writer, dispatch, fence, run_id=RUN, batch_id=BATCH,
        last_sequence=4)
    return client, fence, writer


def test_terminal_insert_acknowledges_once_and_inventory_is_exact():
    client, fence, writer = _client()
    client.execute(SQL)
    assert fence.retain_on_failure
    assert len(writer.calls) == 1
    assert writer.calls[0][1].startswith("arte_typed_")
    assert len(client.acknowledged_operations()) == 1
    client.execute(SQL)
    assert len(writer.calls) == 1


def test_lost_insert_response_keeps_started_operation_and_rejects_retry():
    client, fence, writer = _client(fail=True)
    with pytest.raises(TimeoutError, match="lost response"):
        client.execute(SQL)
    assert fence.retain_on_failure
    with pytest.raises(KeeperUnavailable, match="ambiguous"):
        client.execute(SQL)
    with pytest.raises(KeeperUnavailable, match="unresolved"):
        client.acknowledged_operations()
    assert len(writer.calls) == 1


def test_terminal_dispatch_rejects_unpinned_table_without_transport():
    client, fence, writer = _client()
    with pytest.raises(ValueError, match="closed typed"):
        client.execute(SQL.replace("trading_backtest_terminal_commit_v3",
                                   "trading_unauthorized_v1"))
    assert not writer.calls and not fence.retain_on_failure


def test_terminal_dispatch_rejects_cross_run_body_before_keeper_admission():
    client, fence, writer = _client()
    with pytest.raises(ValueError, match="pinned run"):
        client.execute(SQL.replace('"run-terminal-dispatch"', '"other-run"'))
    assert not writer.calls and not fence.retain_on_failure
