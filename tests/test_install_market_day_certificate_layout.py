from __future__ import annotations

import json
import re

import pytest

from scripts.clickhouse import install_market_day_certificate_layout as layout
from src.trading_runtime.arte_market_day_certification import TABLES


class FakeClient:
    def __init__(self, present=()):
        self.present = set(present)
        self.creates = []

    def execute(self, sql):
        if "FROM system.storage_policies" in sql:
            return json.dumps({"disks": ["live_market_ssd"]})
        if "FROM system.databases" in sql:
            return "1"
        if "FROM system.tables" in sql:
            return "\n".join(json.dumps({"name": name})
                             for name in sorted(self.present))
        if sql.startswith("CREATE TABLE IF NOT EXISTS arte."):
            name = re.search(r"arte\.([a-z0-9_]+)", sql).group(1)
            self.present.add(name)
            self.creates.append(name)
            return ""
        raise AssertionError(f"Unexpected SQL: {sql}")


def test_layout_dry_run_never_creates_and_apply_verifies_each_table(monkeypatch):
    client = FakeClient()
    checked = []
    def verify(_client, *, tables):
        names = {table.name for table in tables}
        assert names <= client.present
        checked.append(names)
    monkeypatch.setattr(layout, "storage_preflight", verify)
    assert layout.install_layout(client, apply=False) == (0, 0)
    assert not client.creates
    assert layout.install_layout(client, apply=True) == (0, len(TABLES))
    assert client.creates == [table.name for table in TABLES]
    assert len(checked) == len(TABLES) + 1
    assert layout.install_layout(client, apply=True) == (len(TABLES), 0)
    assert len(client.creates) == len(TABLES)


def test_incompatible_existing_layout_fails_before_any_create(monkeypatch):
    client = FakeClient({TABLES[0].name})
    def reject(_client, *, tables):
        raise ValueError("existing table differs")
    monkeypatch.setattr(layout, "storage_preflight", reject)
    with pytest.raises(ValueError, match="differs"):
        layout.install_layout(client, apply=True)
    assert not client.creates


def test_cli_uses_managed_ipv4_endpoint_and_rejects_other_host(monkeypatch):
    client = FakeClient(table.name for table in TABLES)
    seen = []
    monkeypatch.setattr(layout.platform, "node", lambda: "DESKTOP-SAAI85T")
    monkeypatch.setattr(layout.socket, "gethostbyname", lambda _: "192.168.1.218")
    monkeypatch.setattr(layout, "_admin_client", lambda url: seen.append(url) or client)
    monkeypatch.setattr(layout, "storage_preflight", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(client, "close", lambda: None, raising=False)
    assert layout.main([]) == 0
    assert seen == ["http://192.168.1.218:18123"]
    with pytest.raises(SystemExit, match="2"):
        layout.main(["--url", "http://other-host:18123"])
    assert seen == ["http://192.168.1.218:18123"]
