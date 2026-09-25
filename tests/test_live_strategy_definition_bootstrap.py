from __future__ import annotations

from copy import deepcopy
import json
import re
from unittest.mock import patch

import pytest

from src.backend.live_strategy_definition_authority import (
    DEFINITION, ENABLE_CHANGE, _installed, project_enable_change,
    project_installed_definition,
)
from src.backend.live_strategy_definition_bootstrap import (
    DefinitionBootstrapSettings, bootstrap_staged_definitions,
)
from src.backend.live_strategy_definition_cold_reader import KeeperDefinitionHeadReader
from src.trading_runtime.strategy_registry import installed_strategy_executors
from tests.test_live_signal_completion_keeper import FakeKazoo
from tests.test_live_strategy_definition_clickhouse import FakeSystem


class FakeClient(FakeSystem):
    def __init__(self):
        super().__init__()
        self.rows = {DEFINITION.name: [], ENABLE_CHANGE.name: []}
        self.closed = False
        self.on_select = None

    def execute(self, sql):
        if "FROM arte." not in sql:
            return super().execute(sql)
        if self.on_select is not None:
            self.on_select()
        table = sql.split("FROM arte.", 1)[1].split(" ", 1)[0]
        revision = int(re.search(r"strategy_revision=(\d+)", sql).group(1))
        selected = [deepcopy(row) for row in self.rows[table]
                    if row["strategy_revision"] == revision]
        for row in selected:
            key = "created_at" if table == DEFINITION.name else "changed_at"
            row[key] = row[key].replace("T", " ").replace("+00:00", "")
            if "enabled" in row:
                row["enabled"] = int(row["enabled"])
        return "\n".join(json.dumps(row) for row in selected)

    def close(self):
        self.closed = True


def _case():
    client, keeper_client = FakeClient(), FakeKazoo()
    reader = KeeperDefinitionHeadReader(keeper_client, endpoint="127.0.0.1:9181")
    for registration in installed_strategy_executors():
        saved = {**_installed(*registration.key),
                 "created_at": "2026-09-24T14:00:00+00:00"}
        definition = project_installed_definition(saved)
        change = project_enable_change(
            definition, change_sequence=1, enabled=True,
            changed_at=saved["created_at"])
        client.rows[DEFINITION.name].append(definition)
        client.rows[ENABLE_CHANGE.name].append(change)
        keeper_client.nodes[reader.path(*registration.key)] = (
            f"1\n{registration.strategy_id}\n{registration.revision}"
            f"\n{definition['content_hash']}\n1\n{change['content_hash']}".encode(), 0, 0)
    settings = DefinitionBootstrapSettings(
        clickhouse_url="http://127.0.0.1:8123",
        clickhouse_user="trading_journal_writer", clickhouse_password="x" * 40)
    factory = lambda *args, **kwargs: client
    return client, keeper_client, settings, factory, reader


def test_full_installed_catalog_bootstraps_read_only_and_closes_client() -> None:
    client, keeper_client, settings, factory, _ = _case()
    with patch("src.backend.trading_runtime_service.trading_journal",
               side_effect=AssertionError("bootstrap must not read SQLite")):
        catalog = bootstrap_staged_definitions(
            settings, keeper_client=keeper_client, client_factory=factory)
    assert len(catalog.definitions) == len(installed_strategy_executors())
    assert client.closed
    with pytest.raises(TypeError):
        next(iter(catalog.definitions.values()))["config"]["direction"] = "changed"


def test_bootstrap_missing_head_or_bad_placement_fails_closed() -> None:
    client, keeper_client, settings, factory, reader = _case()
    first = installed_strategy_executors()[0]
    del keeper_client.nodes[reader.path(*first.key)]
    with pytest.raises(ValueError, match="head is missing"):
        bootstrap_staged_definitions(
            settings, keeper_client=keeper_client, client_factory=factory)
    assert client.closed
    client, keeper_client, settings, factory, _ = _case()
    client.parts = [{"table": DEFINITION.name, "disk_name": "default"}]
    with pytest.raises(RuntimeError, match="outside live_market_ssd"):
        bootstrap_staged_definitions(
            settings, keeper_client=keeper_client, client_factory=factory)
    assert client.closed


def test_bootstrap_detects_cross_revision_head_race() -> None:
    client, keeper_client, settings, factory, reader = _case()
    first = installed_strategy_executors()[0]
    path = reader.path(*first.key)
    changed = False
    reads = 0

    def mutate_after_first_revision():
        nonlocal reads, changed
        reads += 1
        if reads == 3 and not changed:
            value, version, owner = keeper_client.nodes[path]
            keeper_client.nodes[path] = (value, version + 1, owner)
            changed = True

    client.on_select = mutate_after_first_revision
    with pytest.raises(RuntimeError, match="catalog changed"):
        bootstrap_staged_definitions(
            settings, keeper_client=keeper_client, client_factory=factory)


@pytest.mark.parametrize("override", (
    {"clickhouse_url": "http://remote.example:8123"},
    {"clickhouse_user": "default"},
    {"clickhouse_password": "short"},
    {"keeper_endpoint": "remote.example:9181"},
    {"mode": "active"},
))
def test_bootstrap_settings_reject_implicit_or_unsafe_authority(override):
    values = dict(clickhouse_url="http://127.0.0.1:8123",
                  clickhouse_user="trading_journal_writer",
                  clickhouse_password="x" * 40)
    values.update(override)
    with pytest.raises(ValueError, match="incomplete or unsafe"):
        DefinitionBootstrapSettings(**values)


def test_managed_workstation_http_endpoint_is_host_bound() -> None:
    values = dict(clickhouse_url="http://DESKTOP-SAAI85T:18123",
                  clickhouse_user="trading_journal_writer",
                  clickhouse_password="x" * 40)
    with patch("src.backend.live_strategy_definition_bootstrap.platform.node",
               return_value="DESKTOP-SAAI85T"):
        assert DefinitionBootstrapSettings(**values).clickhouse_url == values["clickhouse_url"]
        with pytest.raises(ValueError, match="incomplete or unsafe"):
            DefinitionBootstrapSettings(**{**values,
                                           "clickhouse_url": "http://DESKTOP-SAAI85T:8123"})
    with patch("src.backend.live_strategy_definition_bootstrap.platform.node",
               return_value="OTHER-HOST"):
        with pytest.raises(ValueError, match="incomplete or unsafe"):
            DefinitionBootstrapSettings(**values)
