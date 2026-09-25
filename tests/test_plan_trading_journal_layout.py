from scripts.clickhouse import plan_trading_journal_layout as plan
from src.trading_runtime.arte_journal_schema import fixed_backtest_v2_contracts


def test_missing_plan_checks_installed_contracts_and_emits_only_missing_ddl(
    monkeypatch,
):
    contracts = fixed_backtest_v2_contracts()
    installed = {contracts[0].name, contracts[-1].name}
    checked = []

    class Client:
        def execute(self, sql):
            assert sql.startswith("SELECT name FROM system.tables")
            return "\n".join(sorted(installed))

    monkeypatch.setattr(plan, "storage_preflight",
                        lambda _client, *, tables: checked.extend(tables))
    missing, ddl = plan.plan_missing(Client())
    assert {table.name for table in checked} == installed
    assert set(missing) == {table.name for table in contracts} - installed
    assert len(ddl) == len(missing)
    assert all("CREATE TABLE IF NOT EXISTS arte." in statement
               and "live_market_ssd" in statement for statement in ddl)


def test_missing_plan_fails_if_existing_table_is_incompatible(monkeypatch):
    class Client:
        def execute(self, sql):
            assert sql.startswith("SELECT name FROM system.tables")
            return fixed_backtest_v2_contracts()[0].name

    def incompatible(_client, *, tables):
        assert len(tables) == 1
        raise RuntimeError("existing table is incompatible")

    monkeypatch.setattr(plan, "storage_preflight", incompatible)
    try:
        plan.plan_missing(Client())
    except RuntimeError as exc:
        assert "incompatible" in str(exc)
    else:
        raise AssertionError("The plan must fail closed on existing incompatible tables")
