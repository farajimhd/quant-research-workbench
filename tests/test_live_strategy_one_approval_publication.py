"""Operator approval is normalized, read back, and Keeper-selected last."""
from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import UUID

import pytest

from src.backend.live_strategy_one_approval import approval_row
from src.backend.live_strategy_one_approval_publication import (
    publish_strategy_one_approval,
)
from src.trading_runtime.keeper_session import ManagedKeeperSession
from tests.test_live_strategy_one_approval import release


class NoNodeError(Exception):
    pass


class Keeper:
    connected = True
    client_state = SimpleNamespace(name="CONNECTED")
    client_id = (7, b"session")

    def __init__(self):
        self.nodes = {}
        self.creates = 0

    def add_listener(self, listener):
        self.listener = listener

    def get(self, path):
        if path not in self.nodes:
            raise NoNodeError()
        return self.nodes[path], SimpleNamespace(version=0)

    def ensure_path(self, path):
        assert path == "/trading/strategy-one-approval/v1/paper"

    def create(self, path, value, *, ephemeral):
        assert not ephemeral and path not in self.nodes
        self.nodes[path] = value
        self.creates += 1


class Store:
    def __init__(self):
        self.rows = []
        self.inserts = 0
        self.lose_response = False

    def execute(self, sql, *, query_id=None):
        if sql.startswith("SELECT "):
            return "\n".join(json.dumps(row) for row in self.rows)
        assert sql.startswith("INSERT INTO arte.live_strategy_one_approval_v1 (")
        assert "wait_for_async_insert=1" in sql and query_id.startswith(
            "strategy_one_approval_")
        self.rows.append(json.loads(sql.split("\n", 1)[1]))
        self.inserts += 1
        if self.lose_response:
            raise TimeoutError("HTTP response lost")
        return ""


def request(store, keeper):
    session = ManagedKeeperSession(keeper)
    session._on_state("CONNECTED")
    return dict(write_client=store, read_client=store.reader,
                session=session, release=release(),
                approval_id=str(UUID(int=2)), mode="paper",
                approved_at_us=1_779_000_000_000_000,
                approver_id="operator-1")


def test_operator_approval_selects_only_after_exact_typed_readback(monkeypatch):
    from src.backend import live_strategy_one_approval_publication as publication
    monkeypatch.setattr(publication, "storage_preflight",
                        lambda _client, *, tables: None)
    store, keeper = Store(), Keeper()
    store.reader = Store()
    store.reader.rows = store.rows
    values = request(store, keeper)
    selected = publish_strategy_one_approval(**values)
    assert selected == approval_row(
        approval_id=values["approval_id"], mode="paper",
        release=values["release"], approved_at_us=values["approved_at_us"],
        approver_id=values["approver_id"])
    assert store.inserts == 1 and keeper.creates == 1
    assert publish_strategy_one_approval(**values) == selected
    assert store.inserts == 1 and keeper.creates == 1


def test_lost_insert_response_never_selects_keeper_head(monkeypatch):
    from src.backend import live_strategy_one_approval_publication as publication
    monkeypatch.setattr(publication, "storage_preflight",
                        lambda _client, *, tables: None)
    store, keeper = Store(), Keeper()
    store.reader = Store()
    store.reader.rows = store.rows
    store.lose_response = True
    values = request(store, keeper)
    with pytest.raises(TimeoutError, match="response lost"):
        publish_strategy_one_approval(**values)
    assert keeper.creates == 0 and len(store.rows) == 1
    store.lose_response = False
    publish_strategy_one_approval(**values)
    assert store.inserts == 1 and keeper.creates == 1


def test_approval_rejects_conflicting_row_or_keeper_head(monkeypatch):
    from src.backend import live_strategy_one_approval_publication as publication
    monkeypatch.setattr(publication, "storage_preflight",
                        lambda _client, *, tables: None)
    store, keeper = Store(), Keeper()
    store.reader = Store()
    store.reader.rows = store.rows
    values = request(store, keeper)
    store.rows.append(approval_row(
        approval_id=values["approval_id"], mode="paper",
        release=values["release"], approved_at_us=values["approved_at_us"],
        approver_id="different-operator"))
    with pytest.raises(ValueError, match="different row"):
        publish_strategy_one_approval(**values)
    assert store.inserts == keeper.creates == 0
    store.rows.clear()
    keeper.nodes["/trading/strategy-one-approval/v1/paper/head"] = (
        f"1\npaper\n{values['approval_id']}\n{'f' * 64}").encode()
    with pytest.raises(ValueError, match="different approval"):
        publish_strategy_one_approval(**values)
    assert store.inserts == keeper.creates == 0
