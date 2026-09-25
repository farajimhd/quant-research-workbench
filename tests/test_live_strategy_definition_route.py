from __future__ import annotations

from unittest.mock import patch

import pytest

from src.backend import trading_runtime_service as service
from src.backend.live_strategy_definition_route import StagedDefinitionReadRoute
from src.trading_runtime.strategy_engine import STRATEGY_ID, STRATEGY_REVISION
from src.trading_runtime.strategy_registry import installed_strategy_executors
from tests.test_live_strategy_definition_bootstrap import _case


def test_explicit_typed_definition_reads_do_not_touch_sqlite(monkeypatch) -> None:
    client, keeper, settings, factory, _ = _case()
    route = StagedDefinitionReadRoute(
        settings, keeper_client=keeper, client_factory=factory)
    monkeypatch.setenv("TRADING_STRATEGY_DEFINITION_READ_AUTHORITY", "typed_staged")
    with patch.object(service, "_staged_definition_read_route", route), patch.object(
            service, "trading_journal",
            side_effect=AssertionError("typed definition read must not use SQLite")):
        latest = service.list_strategy_definitions()
        all_rows = service.list_strategy_definitions(latest_only=False)
        selected = service.get_strategy_definition(STRATEGY_ID)
        pinned = service.get_strategy_definition(STRATEGY_ID, 26)
        assert len(latest) == 1
        assert len(all_rows) == len(installed_strategy_executors())
        assert selected["revision"] == STRATEGY_REVISION
        assert pinned["revision"] == 26
        assert selected["executor"]["installed"] is True
        with pytest.raises(RuntimeError, match="read-only"):
            service.save_strategy_definition(selected)
        with pytest.raises(RuntimeError, match="cannot reseed SQLite"):
            service.create_strategy_assignment({})
        with pytest.raises(RuntimeError, match="cannot read SQLite assignments"):
            service.list_strategy_assignments()
        with pytest.raises(RuntimeError, match="cannot mutate SQLite assignments"):
            service.command_strategy_assignment("assignment-1", "pause")
        with pytest.raises(RuntimeError, match="cannot evaluate SQLite assignments"):
            service.evaluate_strategy_assignment("assignment-1", {})
    assert client.closed


def test_selected_typed_route_without_bootstrap_fails_without_sqlite(monkeypatch) -> None:
    monkeypatch.setenv("TRADING_STRATEGY_DEFINITION_READ_AUTHORITY", "typed_staged")
    with patch.object(service, "_staged_definition_read_route", None), patch.object(
            service, "trading_journal",
            side_effect=AssertionError("must not fall back to SQLite")):
        with pytest.raises(RuntimeError, match="lacks cold bootstrap"):
            service.list_strategy_definitions()
        with pytest.raises(RuntimeError, match="lacks cold bootstrap"):
            service.get_strategy_definition(STRATEGY_ID, STRATEGY_REVISION)
    monkeypatch.setenv("TRADING_STRATEGY_DEFINITION_READ_AUTHORITY", "unknown")
    with pytest.raises(RuntimeError, match="authority is invalid"):
        service.get_strategy_definition(STRATEGY_ID)


def test_typed_route_head_change_fails_closed(monkeypatch) -> None:
    client, keeper, settings, factory, reader = _case()
    route = StagedDefinitionReadRoute(
        settings, keeper_client=keeper, client_factory=factory)
    key = reader.path(STRATEGY_ID, STRATEGY_REVISION)
    value, version, owner = keeper.nodes[key]
    keeper.nodes[key] = (value, version + 1, owner)
    # A new stable version is valid; corrupt the attested content itself.
    keeper.nodes[key] = (value.replace(b"\n1\n", b"\n2\n"), version + 2, owner)
    monkeypatch.setenv("TRADING_STRATEGY_DEFINITION_READ_AUTHORITY", "typed_staged")
    with patch.object(service, "_staged_definition_read_route", route):
        with pytest.raises((ValueError, RuntimeError)):
            service.get_strategy_definition(STRATEGY_ID)
