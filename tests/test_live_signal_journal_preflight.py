from __future__ import annotations

import json

import pytest

from scripts.clickhouse.provision_trading_journal import _grants
from src.backend.live_signal_journal_preflight import (
    LIVE_SIGNAL_TABLES, operator_ddl, staged_live_signal_storage_preflight,
)


class Catalog:
    def __init__(self):
        self.policy = ["live_market_ssd"]
        self.misplaced = False
        self.missing = False

    def execute(self, sql):
        if "system.storage_policies" in sql:
            rows = [{"disks": self.policy}]
        elif "system.tables" in sql:
            rows = [dict(name=table.name, engine="MergeTree",
                         storage_policy="live_market_ssd",
                         partition_key="toYYYYMM(session_key)",
                         sorting_key=table.order)
                    for table in LIVE_SIGNAL_TABLES]
            if self.missing:
                rows.pop()
        elif "system.columns" in sql:
            rows = [dict(table=table.name, name=name, type=kind)
                    for table in LIVE_SIGNAL_TABLES for name, kind in table.columns]
        elif "system.parts" in sql:
            rows = [{"table": LIVE_SIGNAL_TABLES[0].name, "disk_name": "default"}] if self.misplaced else []
        else:
            raise AssertionError(sql)
        return "\n".join(json.dumps(row) for row in rows)


def test_operator_only_ddl_and_opt_in_grants() -> None:
    ddl = operator_ddl()
    assert len(ddl) == len(LIVE_SIGNAL_TABLES) == 5
    assert all("storage_policy = 'live_market_ssd'" in row for row in ddl)
    default = _grants()
    staged = _grants(staged_live_signal=True)
    assert len(staged) == len(default) + 5
    assert all(f"arte.{table.name}" in staged[-5 + index]
               for index, table in enumerate(LIVE_SIGNAL_TABLES))
    assert not any("signal_dispatch_intent_typed_v1" in grant for grant in default)


def test_staged_preflight_requires_exact_schema_policy_and_parts() -> None:
    catalog = Catalog()
    staged_live_signal_storage_preflight(catalog)
    catalog.missing = True
    with pytest.raises(RuntimeError, match="inventory"):
        staged_live_signal_storage_preflight(catalog)
    catalog.missing = False
    catalog.policy = ["default"]
    with pytest.raises(RuntimeError, match="SSD-only"):
        staged_live_signal_storage_preflight(catalog)
    catalog.policy = ["live_market_ssd"]
    catalog.misplaced = True
    with pytest.raises(RuntimeError, match="outside"):
        staged_live_signal_storage_preflight(catalog)
