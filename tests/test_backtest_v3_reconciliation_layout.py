"""Operator-only V3 reconciliation child upgrade and exact principal coverage."""
from __future__ import annotations

import json

import pytest

from scripts.clickhouse import install_trading_journal_layout as installer
from scripts.clickhouse import plan_trading_journal_layout as planner
from scripts.clickhouse.provision_fixed_backtest_v3_principals import desired_plan
from src.backend import backtest_fixed_v3_preflight as preflight
from src.backend.backtest_squeeze_episode_schema import (
    RECONCILIATION_DIFFERENCE, SQUEEZE_COMMIT_V3,
    staged_reconciliation_difference_ddl, staged_v3_ddl,
)


CHILD = "trading_portfolio_reconciliation_difference_v3"
COUNT = "portfolio_reconciliation_difference_count"
HASH = "portfolio_reconciliation_difference_hash"


def test_child_and_fence_have_exact_ssd_operator_contract():
    assert RECONCILIATION_DIFFERENCE.name == CHILD
    assert "storage_policy = 'live_market_ssd'" in RECONCILIATION_DIFFERENCE.ddl()
    assert dict(SQUEEZE_COMMIT_V3.columns)[COUNT] == "UInt32"
    assert dict(SQUEEZE_COMMIT_V3.columns)[HASH] == "FixedString(64)"
    ddl = staged_reconciliation_difference_ddl()
    assert len(ddl) == 3
    assert ddl[0] == RECONCILIATION_DIFFERENCE.ddl()
    assert COUNT in ddl[1] and "AFTER portfolio_reservation_reason_hash" in ddl[1]
    assert HASH in ddl[2] and f"AFTER {COUNT}" in ddl[2]
    assert all("trading_portfolio_reconciliation_event_v3" not in row for row in ddl)
    assert RECONCILIATION_DIFFERENCE.ddl() in staged_v3_ddl()


def test_layout_and_three_principals_include_only_child_new_authority():
    names = {table.name for table in planner.profile_contracts("fixed-v3")}
    assert CHILD in names
    assert "trading_portfolio_reconciliation_event_v3" not in names
    assert CHILD in {table.name for table in preflight.running_v3_contracts()}
    assert CHILD in {table.name for table in preflight.terminal_v3_contracts()}
    read, running, terminal = desired_plan()
    assert CHILD in read.select_arte and CHILD not in read.insert_arte
    assert CHILD in running.select_arte and CHILD in running.insert_arte
    assert CHILD in terminal.select_arte and CHILD not in terminal.insert_arte


class UpgradeClient:
    def __init__(self, *, state="old", child=False, fence_rows=0, child_rows=0):
        self.state, self.child = state, child
        self.fence_rows, self.child_rows = fence_rows, child_rows
        self.statements = []

    def execute(self, sql):
        self.statements.append(sql)
        if sql.startswith("SELECT name,type FROM system.columns"):
            omitted = ({COUNT, HASH} if self.state == "old" else
                       {HASH} if self.state == "partial" else set())
            return "\n".join(json.dumps({"name": name, "type": kind})
                             for name, kind in SQUEEZE_COMMIT_V3.columns
                             if name not in omitted)
        if sql.startswith("SELECT count() FROM system.tables"):
            return "1" if self.child else "0"
        if sql == "SELECT count() FROM arte.trading_commit_v3":
            return str(self.fence_rows)
        if sql == f"SELECT count() FROM arte.{CHILD}":
            return str(self.child_rows)
        if sql.startswith(f"CREATE TABLE IF NOT EXISTS arte.{CHILD}"):
            self.child = True
            return ""
        if sql.startswith("ALTER TABLE arte.trading_commit_v3 ADD COLUMN IF NOT EXISTS"):
            self.state = "partial" if COUNT in sql and self.state == "old" else "full"
            return ""
        raise AssertionError(sql)


def test_empty_fence_upgrade_plans_then_resumes_without_insert(monkeypatch):
    checked = []
    monkeypatch.setattr(installer, "storage_preflight",
                        lambda _client, *, tables: checked.append(tuple(t.name for t in tables)))
    client = UpgradeClient()
    assert installer.upgrade_v3_reconciliation_difference(client, apply=False) == "planned"
    assert all(sql.startswith("SELECT ") for sql in client.statements)
    assert installer.upgrade_v3_reconciliation_difference(client, apply=True) == "upgraded"
    assert client.state == "full" and client.child
    assert [sql.split(" ", 1)[0] for sql in client.statements
            if not sql.startswith("SELECT ")] == ["CREATE", "ALTER", "ALTER"]
    assert checked[-1] == (CHILD, "trading_commit_v3")
    assert installer.upgrade_v3_reconciliation_difference(client, apply=True) == "verified"


def test_upgrade_rejects_occupied_commit_or_child_without_ddl(monkeypatch):
    monkeypatch.setattr(installer, "storage_preflight", lambda *_args, **_kwargs: None)
    for client in (UpgradeClient(fence_rows=1), UpgradeClient(child=True, child_rows=1)):
        with pytest.raises(RuntimeError):
            installer.upgrade_v3_reconciliation_difference(client, apply=True)
        assert all(sql.startswith("SELECT ") for sql in client.statements)
    partial = UpgradeClient(state="partial", child=True)
    assert installer.upgrade_v3_reconciliation_difference(partial, apply=True) == "upgraded"
    writes = [sql for sql in partial.statements if not sql.startswith("SELECT ")]
    assert len(writes) == 1 and HASH in writes[0]


def test_reconciliation_upgrade_rejects_invalid_proposal_suffix_without_ddl(monkeypatch):
    class HashOnlyProposalClient(UpgradeClient):
        def execute(self, sql):
            if sql.startswith("SELECT name,type FROM system.columns"):
                self.statements.append(sql)
                omitted = {COUNT, HASH, "trade_proposal_child_count"}
                return "\n".join(json.dumps({"name": name, "type": kind})
                                 for name, kind in SQUEEZE_COMMIT_V3.columns
                                 if name not in omitted)
            return super().execute(sql)

    monkeypatch.setattr(installer, "storage_preflight", lambda *_args, **_kwargs: None)
    client = HashOnlyProposalClient()
    with pytest.raises(RuntimeError, match="invalid trade-proposal suffix"):
        installer.upgrade_v3_reconciliation_difference(client, apply=True)
    assert all(sql.startswith("SELECT ") for sql in client.statements)


def test_prior_reason_upgrade_still_accepts_pre_difference_empty_fence(monkeypatch):
    from tests.test_install_trading_journal_layout import V3UpgradeClient

    class PreDifferenceClient(V3UpgradeClient):
        def execute(self, sql):
            if sql.startswith("SELECT name,type FROM system.columns"):
                self.statements.append(sql)
                omitted = {COUNT, HASH}
                if self.state == "old":
                    omitted |= {"portfolio_reservation_reason_count",
                                "portfolio_reservation_reason_hash"}
                elif self.state == "partial":
                    omitted.add("portfolio_reservation_reason_hash")
                return "\n".join(json.dumps({"name": name, "type": kind})
                                 for name, kind in SQUEEZE_COMMIT_V3.columns
                                 if name not in omitted)
            return super().execute(sql)

    monkeypatch.setattr(installer, "storage_preflight", lambda *_args, **_kwargs: None)
    client = PreDifferenceClient()
    assert installer.upgrade_v3_reservation_reason(client, apply=True) == "upgraded"
    assert client.state == "full" and client.child
