from scripts.clickhouse import provision_backtest_v4_runner as provision
from src.trading_runtime.arte_journal_schema import MARKET_READ_TABLES, TABLES, V4_COMMIT_TABLES


def test_v4_plan_has_exact_typed_append_surface_and_no_market_writes():
    plan = provision.desired_plan()
    assert plan.principal == "backtest_v4_runner"
    assert plan.insert_arte == frozenset(
        table for table, _, _, _ in provision._FAMILIES) | frozenset(
            table.name for table in V4_COMMIT_TABLES)
    assert not plan.insert_arte & MARKET_READ_TABLES
    assert plan.select_arte == frozenset(
        table.name for table in (*TABLES, *V4_COMMIT_TABLES)) | MARKET_READ_TABLES
    assert all(" ON arte." in grant or " ON system." in grant
               for grant in plan.grants())


def test_v4_cli_dry_run_does_not_open_a_connection(capsys, monkeypatch):
    monkeypatch.setattr(provision, "_admin_client",
                        lambda _: (_ for _ in ()).throw(AssertionError("connected")))
    assert provision.main([]) == 0
    out = capsys.readouterr().out
    assert "Plan only" in out and "arte INSERT" in out


def test_v4_apply_reconciles_exact_grants_before_runtime_preflight(monkeypatch):
    calls = []

    class Admin:
        def execute(self, sql):
            calls.append(sql)
            if sql == "SELECT currentUser()":
                return "administrator"
            if sql.startswith("SELECT count() FROM system.users"):
                return "0"
            return ""

    class Writer:
        def execute(self, sql):
            calls.append(sql)
            if sql == "SELECT currentUser()":
                return provision.PRINCIPAL
            return ""

        def close(self):
            calls.append("writer.close")

    monkeypatch.setattr(provision, "storage_preflight",
                        lambda _client, *, tables: calls.append(
                            "preflight:" + str(len(tables))))
    monkeypatch.setattr(provision, "_v4_preflight",
                        lambda _client: calls.append("v4-preflight"))
    provision.apply_with_clients(
        admin=Admin(), credential=lambda *, account_exists: "x" * 48,
        client_factory=lambda user, password: Writer())
    assert "preflight:63" in calls and "preflight:2" in calls
    assert sum(sql.startswith("CREATE USER ") for sql in calls) == 1
    assert sum(sql.startswith("GRANT ") for sql in calls) == len(
        provision._desired_grants(provision.desired_plan()))
    assert calls.index("v4-preflight") > max(
        index for index, sql in enumerate(calls) if sql.startswith("GRANT "))
    assert calls[-1] == "writer.close"
