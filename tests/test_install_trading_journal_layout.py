"""The operator installer is resumable and never inserts market or journal rows."""
import json
import sys

import pytest

from scripts.clickhouse import install_trading_journal_layout as install


class Client:
    def __init__(self):
        self.statements = []

    def execute(self, sql):
        self.statements.append(sql)
        if sql.startswith("SELECT disks FROM system.storage_policies"):
            return json.dumps({"disks": ["live_market_ssd"]})
        if sql.startswith("SELECT count() FROM system.databases"):
            return "1"
        if sql.startswith("CREATE TABLE IF NOT EXISTS arte."):
            return ""
        raise AssertionError(sql)

    def close(self):
        pass


def test_plan_is_read_only_and_apply_verifies_each_new_table(monkeypatch):
    contracts = install.fixed_backtest_v2_contracts()
    missing = (contracts[-2].name, contracts[-1].name)
    monkeypatch.setattr(install, "plan_missing", lambda _: (missing, ()))
    verified = []
    monkeypatch.setattr(install, "storage_preflight",
                        lambda _, *, tables: verified.append(tuple(t.name for t in tables)))
    client = Client()
    assert install.install_missing(client, apply=False) == (len(contracts) - 2, 0)
    assert all(sql.startswith("SELECT ") for sql in client.statements)
    assert install.install_missing(client, apply=True) == (len(contracts) - 2, 2)
    assert verified == [missing, tuple(t.name for t in contracts)]
    writes = [sql for sql in client.statements if not sql.startswith("SELECT ")]
    assert len(writes) == 2
    assert all(sql.startswith("CREATE TABLE IF NOT EXISTS arte.trading_") for sql in writes)
    assert all("storage_policy = 'live_market_ssd'" in sql for sql in writes)


def test_installer_fails_before_ddl_when_policy_is_not_ssd(monkeypatch):
    client = Client()
    monkeypatch.setattr(install, "plan_missing",
                        lambda _: pytest.fail("must not inspect tables after bad policy"))
    client.execute = lambda sql: json.dumps({"disks": ["default"]})
    with pytest.raises(RuntimeError, match="SSD-only"):
        install.install_missing(client, apply=True)


def test_v3_install_profile_is_read_only_by_default(monkeypatch):
    client = Client()
    contracts = install.profile_contracts("fixed-v3")
    missing = tuple(table.name for table in contracts[-3:])
    monkeypatch.setattr(install, "plan_missing",
                        lambda _, *, profile: (missing, ()))
    monkeypatch.setattr(install, "storage_preflight",
                        lambda *_args, **_kwargs: None)
    assert install.install_missing(client, apply=False, profile="fixed-v3") == (
        len(contracts) - 3, 0)
    assert all(sql.startswith("SELECT ") for sql in client.statements)


def test_cli_defaults_to_read_only_plan_on_workstation(monkeypatch, capsys):
    client = Client()
    monkeypatch.setattr(install.platform, "node", lambda: "DESKTOP-SAAI85T")
    monkeypatch.setattr(install.socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (None, None, None, None, (install.WORKSTATION_IPV4, 18123))])
    monkeypatch.setattr(install, "_admin_client", lambda _: client)
    monkeypatch.setattr(install, "plan_missing", lambda _: ((), ()))
    monkeypatch.setattr(install, "storage_preflight", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(sys, "argv", ["install_trading_journal_layout.py"])
    assert install.main() == 0
    assert "Plan only; no ClickHouse state changed" in capsys.readouterr().out
    assert all(sql.startswith("SELECT ") for sql in client.statements)


def test_live_membership_layout_is_typed_read_only_and_restart_safe(monkeypatch):
    class MembershipClient:
        def __init__(self):
            self.installed = {}
            self.rows = 0
            self.statements = []
        def execute(self, sql):
            self.statements.append(sql)
            if sql.startswith("SELECT name FROM system.tables"):
                return "\n".join(json.dumps({"name": name})
                                 for name in sorted(self.installed))
            if sql.startswith("SELECT name,type FROM system.columns"):
                name = sql.split("AND table='", 1)[1].split("'", 1)[0]
                return "\n".join(json.dumps({"name": column, "type": kind})
                                 for column, kind in self.installed[name])
            if sql.startswith("SELECT count() FROM arte."):
                return str(self.rows)
            if sql.startswith("CREATE TABLE IF NOT EXISTS arte."):
                name = sql.split("arte.", 1)[1].split(" ", 1)[0]
                self.installed[name] = next(
                    table.columns for table in install.LIVE_PLAN_MEMBERSHIP_TABLES
                    if table.name == name)
                return ""
            if sql.startswith("ALTER TABLE arte."):
                name = sql.split("arte.", 1)[1].split(" ", 1)[0]
                self.installed[name] = next(
                    table.columns for table in install.LIVE_PLAN_MEMBERSHIP_TABLES
                    if table.name == name)
                return ""
            raise AssertionError(sql)
    verified = []
    monkeypatch.setattr(install, "storage_preflight",
                        lambda _, *, tables: verified.append(tables[0].name))
    client = MembershipClient()
    assert install.install_live_plan_membership(client, apply=False) == "planned"
    assert all(sql.startswith("SELECT ") for sql in client.statements)
    assert install.install_live_plan_membership(client, apply=True) == "upgraded"
    names = {table.name for table in install.LIVE_PLAN_MEMBERSHIP_TABLES}
    assert set(client.installed) == names
    assert set(verified) == names
    writes = [sql for sql in client.statements if sql.startswith("CREATE TABLE")]
    assert len(writes) == 3
    assert all("storage_policy = 'live_market_ssd'" in sql for sql in writes)
    assert all(" JSON " not in sql and " payload" not in sql for sql in writes)
    assert install.install_live_plan_membership(client, apply=False) == "verified"
    assert len([sql for sql in client.statements if sql.startswith("CREATE TABLE")]) == 3
    for table in install.LIVE_PLAN_MEMBERSHIP_TABLES:
        client.installed[table.name] = tuple(
            column for column in table.columns if column[0] != "publication_id")
    client.rows = 1
    with pytest.raises(RuntimeError, match="Occupied membership"):
        install.install_live_plan_membership(client, apply=True)
    assert not any(sql.startswith("ALTER TABLE") for sql in client.statements)
    client.rows = 0
    assert install.install_live_plan_membership(client, apply=False) == "planned"
    assert install.install_live_plan_membership(client, apply=True) == "upgraded"
    assert len([sql for sql in client.statements if sql.startswith("ALTER TABLE")]) == 3
    assert install.install_live_plan_membership(client, apply=False) == "verified"


class V3UpgradeClient:
    def __init__(self, *, state="old", child=False, rows=0):
        self.state = state
        self.child = child
        self.rows = rows
        self.statements = []

    def execute(self, sql):
        self.statements.append(sql)
        full = install._without_proposals(install.SQUEEZE_COMMIT_V3.columns)
        if sql.startswith("SELECT name,type FROM system.columns"):
            omitted = ({"portfolio_reservation_reason_count",
                        "portfolio_reservation_reason_hash"} if self.state == "old"
                       else {"portfolio_reservation_reason_hash"}
                       if self.state == "partial" else set())
            return "\n".join(json.dumps({"name": name, "type": kind})
                             for name, kind in full if name not in omitted)
        if sql.startswith("SELECT count() FROM system.tables"):
            return "1" if self.child else "0"
        if sql == "SELECT count() FROM arte.trading_commit_v3":
            return str(self.rows)
        if sql == "SELECT count() FROM arte.trading_portfolio_reservation_reason_v1":
            return "0"
        if sql.startswith("CREATE TABLE IF NOT EXISTS arte.trading_portfolio_reservation_reason_v1"):
            self.child = True
            return ""
        if sql.startswith("ALTER TABLE arte.trading_commit_v3 ADD COLUMN IF NOT EXISTS"):
            self.state = ("partial" if "portfolio_reservation_reason_count" in sql
                          and self.state == "old" else "full")
            return ""
        raise AssertionError(sql)


def test_v3_reason_upgrade_plans_read_only_and_applies_only_empty_fence(monkeypatch):
    checks = []
    monkeypatch.setattr(install, "storage_preflight",
                        lambda _, *, tables: checks.append(tuple(t.name for t in tables)))
    client = V3UpgradeClient()
    assert install.upgrade_v3_reservation_reason(client, apply=False) == "planned"
    assert all(sql.startswith("SELECT ") for sql in client.statements)
    assert install.upgrade_v3_reservation_reason(client, apply=True) == "upgraded"
    assert client.state == "full" and client.child
    assert [sql.split(" ", 1)[0] for sql in client.statements
            if not sql.startswith("SELECT ")] == ["CREATE", "ALTER", "ALTER"]
    assert checks[-1] == ("trading_portfolio_reservation_reason_v1", "trading_commit_v3")


def test_v3_broker_oms_upgrade_requires_empty_fence_and_is_resumable(monkeypatch):
    from src.backend.backtest_squeeze_episode_schema import BROKER_OMS_TABLES

    class BrokerClient:
        def __init__(self, count="0"):
            self.count = count
            self.columns = list(install.SQUEEZE_COMMIT_V3.columns[:-(3 + 14)]) + list(
                install.SQUEEZE_COMMIT_V3.columns[-3:])
            self.tables = set()
            self.writes = []

        def execute(self, sql):
            if sql.startswith("SELECT name,type FROM system.columns"):
                return "\n".join(json.dumps({"name": n, "type": t})
                                 for n, t in self.columns)
            if sql.startswith("SELECT name FROM system.tables"):
                return "\n".join(json.dumps({"name": name})
                                 for name in sorted(self.tables))
            if sql == "SELECT count() FROM arte.trading_commit_v3":
                return self.count
            if sql.startswith("CREATE TABLE IF NOT EXISTS arte."):
                self.writes.append(sql)
                self.tables.add(sql.split("arte.", 1)[1].split(" ", 1)[0])
                return ""
            if sql.startswith("ALTER TABLE arte.trading_commit_v3"):
                self.writes.append(sql)
                name = sql.split("ADD COLUMN IF NOT EXISTS ", 1)[1].split(" ", 1)[0]
                self.columns.insert(-3, next(column for column in
                    install.SQUEEZE_COMMIT_V3.columns if column[0] == name))
                return ""
            raise AssertionError(sql)

    monkeypatch.setattr(install, "storage_preflight", lambda *_a, **_k: None)
    occupied = BrokerClient(count="1")
    with pytest.raises(RuntimeError, match="has rows"):
        install.upgrade_v3_broker_oms(occupied, apply=True)
    assert occupied.writes == []
    client = BrokerClient()
    assert install.upgrade_v3_broker_oms(client, apply=False) == "planned"
    assert client.writes == []
    assert install.upgrade_v3_broker_oms(client, apply=True) == "upgraded"
    assert len(client.tables) == len(BROKER_OMS_TABLES)
    assert len(client.writes) == len(BROKER_OMS_TABLES) + 8
    assert install.upgrade_v3_broker_oms(client, apply=True) == "verified"


def test_v3_capacity_upgrade_requires_empty_fence_and_is_resumable(monkeypatch):
    from src.backend.backtest_squeeze_episode_schema import ENTRY_REPRICE_CAPACITY_TABLES

    class CapacityClient:
        def __init__(self, count="0"):
            self.count = count
            self.columns = list(install.SQUEEZE_COMMIT_V3.columns[:-9]) + list(
                install.SQUEEZE_COMMIT_V3.columns[-3:])
            self.tables = set()
            self.writes = []

        def execute(self, sql):
            if sql.startswith("SELECT name,type FROM system.columns"):
                return "\n".join(json.dumps({"name": n, "type": t})
                                 for n, t in self.columns)
            if sql.startswith("SELECT name FROM system.tables"):
                return "\n".join(json.dumps({"name": name})
                                 for name in sorted(self.tables))
            if sql == "SELECT count() FROM arte.trading_commit_v3":
                return self.count
            if sql.startswith("CREATE TABLE IF NOT EXISTS arte."):
                self.writes.append(sql)
                self.tables.add(sql.split("arte.", 1)[1].split(" ", 1)[0])
                return ""
            if sql.startswith("ALTER TABLE arte.trading_commit_v3"):
                self.writes.append(sql)
                name = sql.split("ADD COLUMN IF NOT EXISTS ", 1)[1].split(" ", 1)[0]
                self.columns.insert(-3, next(column for column in
                    install.SQUEEZE_COMMIT_V3.columns if column[0] == name))
                return ""
            raise AssertionError(sql)

    monkeypatch.setattr(install, "storage_preflight", lambda *_a, **_k: None)
    occupied = CapacityClient(count="1")
    with pytest.raises(RuntimeError, match="has rows"):
        install.upgrade_v3_entry_reprice_capacity(occupied, apply=True)
    assert occupied.writes == []
    client = CapacityClient()
    assert install.upgrade_v3_entry_reprice_capacity(client, apply=False) == "planned"
    assert client.writes == []
    assert install.upgrade_v3_entry_reprice_capacity(client, apply=True) == "upgraded"
    assert len(client.tables) == len(ENTRY_REPRICE_CAPACITY_TABLES)
    assert len(client.writes) == len(ENTRY_REPRICE_CAPACITY_TABLES) + 4
    assert install.upgrade_v3_entry_reprice_capacity(client, apply=True) == "verified"


def test_v3_refusal_upgrade_requires_empty_fence_and_is_resumable(monkeypatch):
    from src.backend.backtest_squeeze_episode_schema import ENTRY_REPRICE_REJECTED

    class RefusalClient:
        def __init__(self, count="0"):
            self.count = count
            self.columns = list(install.SQUEEZE_COMMIT_V3.columns[:-5]) + list(
                install.SQUEEZE_COMMIT_V3.columns[-3:])
            self.child = False
            self.writes = []

        def execute(self, sql):
            if sql.startswith("SELECT name,type FROM system.columns"):
                return "\n".join(json.dumps({"name": n, "type": t})
                                 for n, t in self.columns)
            if sql.startswith("SELECT count() FROM system.tables"):
                return "1" if self.child else "0"
            if sql == "SELECT count() FROM arte.trading_commit_v3":
                return self.count
            if sql.startswith("CREATE TABLE IF NOT EXISTS arte."):
                self.writes.append(sql)
                self.child = True
                return ""
            if sql.startswith("ALTER TABLE arte.trading_commit_v3"):
                self.writes.append(sql)
                name = sql.split("ADD COLUMN IF NOT EXISTS ", 1)[1].split(" ", 1)[0]
                self.columns.insert(-3, next(column for column in
                    install.SQUEEZE_COMMIT_V3.columns if column[0] == name))
                return ""
            raise AssertionError(sql)

    monkeypatch.setattr(install, "storage_preflight", lambda *_a, **_k: None)
    occupied = RefusalClient(count="1")
    with pytest.raises(RuntimeError, match="has rows"):
        install.upgrade_v3_entry_reprice_rejected(occupied, apply=True)
    assert occupied.writes == []
    client = RefusalClient()
    assert install.upgrade_v3_entry_reprice_rejected(client, apply=False) == "planned"
    assert client.writes == []
    assert install.upgrade_v3_entry_reprice_rejected(client, apply=True) == "upgraded"
    assert client.child and len(client.writes) == 3
    assert install.upgrade_v3_entry_reprice_rejected(client, apply=True) == "verified"


class ControlUpgradeClient:
    def __init__(self, *, rows=0, state="old", child=False):
        self.rows, self.state, self.child = rows, state, child
        self.statements = []

    def close(self):
        pass

    def execute(self, sql):
        self.statements.append(sql)
        if sql.startswith("SELECT name,type FROM system.columns"):
            omitted = ({"portfolio_control_count", "portfolio_control_hash"}
                       if self.state == "old" else {"portfolio_control_hash"}
                       if self.state == "partial" else set())
            return "\n".join(json.dumps({"name": name, "type": kind})
                             for name, kind in install._without_proposals(
                                 install.SQUEEZE_COMMIT_V3.columns)
                             if name not in omitted)
        if sql.startswith("SELECT count() FROM system.tables"):
            return "1" if self.child else "0"
        if sql == "SELECT count() FROM arte.trading_commit_v3":
            return str(self.rows)
        if sql == "SELECT count() FROM arte.trading_portfolio_control_v3":
            return "0"
        if sql.startswith("CREATE TABLE IF NOT EXISTS arte.trading_portfolio_control_v3"):
            self.child = True
            return ""
        if sql.startswith("ALTER TABLE arte.trading_commit_v3 ADD COLUMN IF NOT EXISTS"):
            self.state = ("partial" if "ADD COLUMN IF NOT EXISTS "
                          "portfolio_control_count UInt32" in sql else "full")
            return ""
        raise AssertionError(sql)


def test_v3_control_upgrade_requires_empty_fence_and_is_restart_safe(monkeypatch):
    monkeypatch.setattr(install, "storage_preflight", lambda *_args, **_kwargs: None)
    client = ControlUpgradeClient()
    assert install.upgrade_v3_portfolio_control(client, apply=False) == "planned"
    assert all(sql.startswith("SELECT ") for sql in client.statements)
    assert install.upgrade_v3_portfolio_control(client, apply=True) == "upgraded"
    assert client.child and client.state == "full"
    assert install.upgrade_v3_portfolio_control(client, apply=True) == "verified"
    occupied = ControlUpgradeClient(rows=1)
    with pytest.raises(RuntimeError, match="versioned migration"):
        install.upgrade_v3_portfolio_control(occupied, apply=True)
    assert all(sql.startswith("SELECT ") for sql in occupied.statements)


def test_v3_control_cli_reports_plan_without_writes(monkeypatch, capsys):
    client = ControlUpgradeClient()
    monkeypatch.setattr(install.platform, "node", lambda: "DESKTOP-SAAI85T")
    monkeypatch.setattr(install.socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (None, None, None, None, (install.WORKSTATION_IPV4, 18123))])
    monkeypatch.setattr(install, "_admin_client", lambda _: client)
    monkeypatch.setattr(install, "storage_preflight", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(sys, "argv", ["install_trading_journal_layout.py",
                                     "--upgrade-v3-portfolio-control"])
    assert install.main() == 0
    assert "V3 portfolio-control layout: planned; no rows inserted" in capsys.readouterr().out
    assert all(sql.startswith("SELECT ") for sql in client.statements)


def test_v3_reason_upgrade_refuses_nonempty_or_unknown_fence(monkeypatch):
    monkeypatch.setattr(install, "storage_preflight", lambda *_args, **_kwargs: None)
    occupied = V3UpgradeClient(rows=1)
    with pytest.raises(RuntimeError, match="versioned migration"):
        install.upgrade_v3_reservation_reason(occupied, apply=True)
    assert all(sql.startswith("SELECT ") for sql in occupied.statements)
    occupied.state = "full"
    occupied.child = True
    assert install.upgrade_v3_reservation_reason(occupied, apply=True) == "verified"
    assert all(sql.startswith("SELECT ") for sql in occupied.statements)


def test_v3_reason_upgrade_resumes_after_first_alter(monkeypatch):
    monkeypatch.setattr(install, "storage_preflight", lambda *_args, **_kwargs: None)
    client = V3UpgradeClient(state="partial", child=True)
    assert install.upgrade_v3_reservation_reason(client, apply=True) == "upgraded"
    writes = [sql for sql in client.statements if not sql.startswith("SELECT ")]
    assert len(writes) == 1 and "portfolio_reservation_reason_hash" in writes[0]


class TradeProposalUpgradeClient:
    def __init__(self, *, rows=0):
        self.rows = rows
        self.columns = list(install._without_proposals(install.SQUEEZE_COMMIT_V3.columns))
        self.tables = set()
        self.statements = []

    def execute(self, sql):
        self.statements.append(sql)
        if sql.startswith("SELECT name,type FROM system.columns"):
            return "\n".join(json.dumps({"name": name, "type": kind})
                             for name, kind in self.columns)
        if sql.startswith("SELECT name FROM system.tables"):
            return "\n".join(json.dumps({"name": name}) for name in sorted(self.tables))
        if sql == "SELECT count() FROM arte.trading_commit_v3":
            return str(self.rows)
        if sql.startswith("SELECT count() FROM arte.trading_trade_proposal_"):
            return "0"
        if sql.startswith("CREATE TABLE IF NOT EXISTS arte.trading_trade_proposal_"):
            self.tables.add(sql.split("arte.", 1)[1].split(" ", 1)[0])
            return ""
        if sql.startswith("ALTER TABLE arte.trading_commit_v3 ADD COLUMN IF NOT EXISTS "):
            name = sql.split("ADD COLUMN IF NOT EXISTS ", 1)[1].split(" ", 1)[0]
            self.columns = [column for column in install.SQUEEZE_COMMIT_V3.columns
                            if column[0] in {n for n, _ in self.columns} | {name}]
            return ""
        raise AssertionError(sql)


def test_v3_trade_proposal_upgrade_is_dry_run_and_empty_fence_only(monkeypatch):
    monkeypatch.setattr(install, "storage_preflight", lambda *_args, **_kwargs: None)
    client = TradeProposalUpgradeClient()
    assert install.upgrade_v3_trade_proposal(client, apply=False) == "planned"
    assert all(sql.startswith("SELECT ") for sql in client.statements)
    assert install.upgrade_v3_trade_proposal(client, apply=True) == "upgraded"
    assert len(client.tables) == len(install.TRADE_PROPOSAL_TABLES)
    assert install.upgrade_v3_trade_proposal(client, apply=True) == "verified"
    occupied = TradeProposalUpgradeClient(rows=1)
    with pytest.raises(RuntimeError, match="versioned migration"):
        install.upgrade_v3_trade_proposal(occupied, apply=True)
    assert all(sql.startswith("SELECT ") for sql in occupied.statements)
