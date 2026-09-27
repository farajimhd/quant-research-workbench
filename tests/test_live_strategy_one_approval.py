"""Live Strategy 1 approval is a scalar operator proof, never a fallback."""
from dataclasses import replace
import json
from uuid import UUID

import pytest

from src.backend.backtest_strategy_one_configuration import (
    CertifiedStrategyOneConfiguration,
)
from src.backend.live_strategy_one_approval import (
    ApprovalHead, KeeperApprovalHeadReader, TABLE, approval_row,
    verify_selected_approval,
)
from src.trading_runtime.keeper_session import ManagedKeeperSession
from src.trading_runtime.strategy_one_contract import STRATEGY_ID


def release():
    return CertifiedStrategyOneConfiguration(
        str(UUID(int=1)), "a" * 64, "b" * 64, "candidate", "c" * 64,
        "d" * 64, {"strategy": {"strategy_id": STRATEGY_ID,
                               "strategy_number": 1, "revision": 1,
                               "execution_interval": "100ms"}})


class Keeper:
    def __init__(self, head):
        self.head = head
        self.reads = 0
        self.after_first = None

    def read_head(self, mode):
        assert mode == "paper"
        self.reads += 1
        return self.after_first if self.reads > 1 and self.after_first else self.head


class Client:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def execute(self, sql):
        self.queries.append(sql)
        return "\n".join(json.dumps(row) for row in self.rows)


def authority():
    selected = approval_row(
        approval_id=str(UUID(int=2)), mode="paper", release=release(),
        approved_at_us=1_779_000_000_000_000, approver_id="operator-1")
    return selected, ApprovalHead("paper", selected["approval_id"],
                                  selected["content_hash"])


def test_managed_keeper_head_reader_rejects_missing_or_changed_session():
    row, _ = authority()

    class State:
        name = "CONNECTED"

    class Stat:
        version = 0

    class KeeperClient:
        connected = True
        client_state = State()
        client_id = (7, b"secret")

        def add_listener(self, callback):
            self.listener = callback

        def get(self, path):
            assert path == "/trading/strategy-one-approval/v1/paper/head"
            return (f"1\npaper\n{row['approval_id']}\n{row['content_hash']}".encode(), Stat())

    client = KeeperClient()
    session = ManagedKeeperSession(client)
    session._on_state("CONNECTED")
    reader = KeeperApprovalHeadReader(session)
    assert reader.read_head("paper") == ApprovalHead(
        "paper", row["approval_id"], row["content_hash"])
    with pytest.raises(ValueError):
        reader.read_head("backtest")
    def changed(path):
        result = KeeperClient.get(client, path)
        session._on_state("SUSPENDED")
        return result
    client.get = changed
    with pytest.raises(RuntimeError, match="session changed"):
        reader.read_head("paper")
    with pytest.raises(RuntimeError, match="unavailable"):
        reader.read_head("paper")


def test_normalized_approval_roundtrip_requires_keeper_selection():
    row, head = authority()
    client, keeper = Client([row]), Keeper(head)
    assert verify_selected_approval(client, keeper, mode="paper",
                                    release=release()) == row
    assert keeper.reads == 2
    assert len(client.queries) == 1
    assert client.queries[0].startswith("SELECT ")
    assert "LIMIT 2" in client.queries[0]
    assert "storage_policy = 'live_market_ssd'" in TABLE.ddl()
    assert "JSON" not in TABLE.ddl() and "Object" not in TABLE.ddl()


@pytest.mark.parametrize("fault", (
    "missing", "duplicate", "head_absent", "hash", "release_hash",
    "attempt", "extra_column", "approval_changed", "wrong_mode",
))
def test_approval_fails_closed_on_missing_or_changed_authority(fault):
    row, head = authority()
    rows, keeper, selected_release = [row], Keeper(head), release()
    if fault == "missing":
        rows = []
    elif fault == "duplicate":
        rows = [row, row]
    elif fault == "head_absent":
        keeper.head = None
    elif fault == "hash":
        rows = [{**row, "content_hash": "f" * 64}]
    elif fault == "release_hash":
        selected_release = replace(selected_release, payload_hash="e" * 64)
    elif fault == "attempt":
        selected_release = replace(selected_release, attempt_id=str(UUID(int=3)))
    elif fault == "extra_column":
        rows = [{**row, "config_blob": "{}"}]
    elif fault == "approval_changed":
        keeper.after_first = ApprovalHead("paper", head.approval_id, "e" * 64)
    else:
        keeper.head = ApprovalHead("live", head.approval_id, head.content_hash)
    with pytest.raises((TypeError, ValueError)):
        verify_selected_approval(Client(rows), keeper, mode="paper",
                                 release=selected_release)


@pytest.mark.parametrize("override", (
    {"mode": "backtest"}, {"approved_at_us": True},
    {"approver_id": "\n"}, {"approval_id": "not-a-uuid"},
))
def test_approval_projection_rejects_untyped_operator_decisions(override):
    values = dict(approval_id=str(UUID(int=2)), mode="paper",
                  release=release(), approved_at_us=1, approver_id="operator-1")
    with pytest.raises(ValueError):
        approval_row(**{**values, **override})


def test_approval_projection_rejects_other_strategy_or_interval():
    base = release()
    for change in ({"strategy_id": "other"}, {"strategy_number": 2},
                   {"revision": 2}, {"execution_interval": "events"}):
        invalid = replace(base, payload={"strategy": {
            **base.payload["strategy"], **change}})
        with pytest.raises(ValueError, match="scope"):
            approval_row(approval_id=str(UUID(int=2)), mode="paper",
                         release=invalid, approved_at_us=1,
                         approver_id="operator-1")
