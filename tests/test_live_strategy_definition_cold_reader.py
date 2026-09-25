from __future__ import annotations

from copy import deepcopy
from unittest.mock import Mock, patch

import pytest

from src.backend.live_strategy_definition_authority import (
    project_enable_change, project_installed_definition,
)
from src.backend.live_strategy_definition_cold_reader import (
    KeeperDefinitionHeadReader, cold_read_installed_definition,
)
from src.backend.trading_runtime_service import save_strategy_definition
from src.trading_runtime.strategy_engine import STRATEGY_ID, STRATEGY_REVISION
from src.trading_runtime.strategy_registry import typed_persistence_executor
from tests.test_live_signal_completion_keeper import FakeKazoo


def _actual_route_row() -> dict:
    journal = Mock()
    stored = {}

    def save(**kwargs):
        stored.update(deepcopy(kwargs))

    def read(*_args):
        return {**deepcopy(stored), "created_at": "2026-09-24T14:00:00+00:00"}

    journal.save_strategy.side_effect = save
    journal.strategy.side_effect = read
    with patch("src.backend.trading_runtime_service.trading_journal", return_value=journal):
        row = save_strategy_definition(
            typed_persistence_executor(STRATEGY_ID, STRATEGY_REVISION).definition())
    assert "taxonomy" in row and "created_at" in row
    return row


def _case():
    route = _actual_route_row()
    definition = project_installed_definition(route)
    change = project_enable_change(
        definition, change_sequence=1, enabled=route["enabled"],
        changed_at=route["created_at"])

    class Rows:
        definitions = [definition]
        changes = [change]

        def read_definition_rows(self, *, strategy_id, strategy_revision):
            return deepcopy(self.definitions)

        def read_enable_change_rows(self, *, strategy_id, strategy_revision):
            return deepcopy(self.changes)

    client = FakeKazoo()
    keeper = KeeperDefinitionHeadReader(client, endpoint="127.0.0.1:9181")
    client.nodes[keeper.path(STRATEGY_ID, STRATEGY_REVISION)] = (
        f"1\n{STRATEGY_ID}\n{STRATEGY_REVISION}\n{definition['content_hash']}\n1\n{change['content_hash']}".encode(),
        0, 0)
    return route, Rows(), client, keeper


def test_actual_save_route_shape_cold_recovers_without_sqlite_read() -> None:
    route, rows, _, keeper = _case()
    result = cold_read_installed_definition(
        rows, keeper, strategy_id=STRATEGY_ID,
        strategy_revision=STRATEGY_REVISION)
    assert result["config"] == route["config"]
    assert result["enabled"] == route["enabled"]


def test_cold_reader_rejects_missing_rows_duplicate_and_head_race() -> None:
    _, rows, client, keeper = _case()
    read = lambda: cold_read_installed_definition(
        rows, keeper, strategy_id=STRATEGY_ID,
        strategy_revision=STRATEGY_REVISION)
    rows.changes = []
    with pytest.raises(ValueError, match="incomplete"):
        read()
    _, rows, client, keeper = _case()
    rows.definitions.append(rows.definitions[0])
    with pytest.raises(ValueError, match="definition row differs"):
        cold_read_installed_definition(rows, keeper, strategy_id=STRATEGY_ID,
                                       strategy_revision=STRATEGY_REVISION)
    _, rows, client, keeper = _case()
    original = keeper.read_head
    calls = 0

    def racing(*args):
        nonlocal calls
        calls += 1
        head = original(*args)
        if calls == 2:
            path = keeper.path(STRATEGY_ID, STRATEGY_REVISION)
            value, version, owner = client.nodes[path]
            client.nodes[path] = (value, version + 1, owner)
            return original(*args)
        return head

    keeper.read_head = racing
    with pytest.raises(RuntimeError, match="changed during cold read"):
        cold_read_installed_definition(rows, keeper, strategy_id=STRATEGY_ID,
                                       strategy_revision=STRATEGY_REVISION)


def test_keeper_head_rejects_remote_or_corrupt_scalar_proof() -> None:
    with pytest.raises(ValueError, match="loopback"):
        KeeperDefinitionHeadReader(FakeKazoo(), endpoint="192.0.2.1:9181")
    _, _, client, keeper = _case()
    path = keeper.path(STRATEGY_ID, STRATEGY_REVISION)
    client.nodes[path] = (b"{json}", 0, 0)
    with pytest.raises(ValueError, match="corrupt"):
        keeper.read_head(STRATEGY_ID, STRATEGY_REVISION)
