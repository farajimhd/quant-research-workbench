import json

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
            return "\n".join(json.dumps({"name": name}) for name in sorted(installed))

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
            return json.dumps({"name": fixed_backtest_v2_contracts()[0].name})

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


def test_v3_plan_includes_only_missing_normalized_squeeze_and_terminal_tables(monkeypatch):
    contracts = plan.profile_contracts("fixed-v3")
    v2 = fixed_backtest_v2_contracts()
    assert len(contracts) == len(v2) + 4
    assert {table.name for table in contracts[len(v2):]} == {
        "trading_backtest_squeeze_episode_v1",
        "trading_portfolio_reservation_reason_v1", "trading_commit_v3",
        "trading_backtest_terminal_commit_v3"}
    present = {table.name for table in v2}

    class Client:
        def execute(self, sql):
            assert sql.startswith("SELECT name FROM system.tables")
            return "\n".join(json.dumps({"name": name}) for name in sorted(present))

    monkeypatch.setattr(plan, "storage_preflight", lambda *_args, **_kwargs: None)
    missing, ddl = plan.plan_missing(Client(), profile="fixed-v3")
    assert set(missing) == {table.name for table in contracts[len(v2):]}
    assert len(ddl) == 4 and all("live_market_ssd" in sql for sql in ddl)
