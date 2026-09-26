import json

from scripts.clickhouse import plan_trading_journal_layout as plan
from src.trading_runtime.arte_journal_schema import (
    V4_COMMIT_TABLES, fixed_backtest_v2_contracts,
)
from src.backend.backtest_trade_proposal_v3 import TABLES as TRADE_PROPOSAL_TABLES
from src.backend.backtest_squeeze_episode_schema import (
    BROKER_OMS_TABLES, ENTRY_REPRICE_CAPACITY_TABLES,
    ENTRY_REPRICE_REJECTED, PROTECTED_EXIT_SATISFIED, PROTECTION_CHANGE_TABLES,
    PROTECTED_EXIT_SNAPSHOT,
)


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
    assert len(contracts) == len(v2) + 27
    assert {table.name for table in contracts[len(v2):]} == {
        "trading_backtest_squeeze_episode_v1",
        "trading_portfolio_reservation_reason_v1",
        "trading_portfolio_reconciliation_difference_v3", "trading_commit_v3",
        "trading_portfolio_control_v3",
        "trading_backtest_terminal_commit_v3",
        *(table.name for table in TRADE_PROPOSAL_TABLES),
        *(table.name for table in BROKER_OMS_TABLES),
        *(table.name for table in ENTRY_REPRICE_CAPACITY_TABLES),
        ENTRY_REPRICE_REJECTED.name, PROTECTED_EXIT_SATISFIED.name,
        *(table.name for table in PROTECTION_CHANGE_TABLES),
        PROTECTED_EXIT_SNAPSHOT.name, "trading_portfolio_allocation_fill_v3"}
    present = {table.name for table in v2}

    class Client:
        def execute(self, sql):
            assert sql.startswith("SELECT name FROM system.tables")
            return "\n".join(json.dumps({"name": name}) for name in sorted(present))

    monkeypatch.setattr(plan, "storage_preflight", lambda *_args, **_kwargs: None)
    missing, ddl = plan.plan_missing(Client(), profile="fixed-v3")
    assert set(missing) == {table.name for table in contracts[len(v2):]}
    assert len(ddl) == 27 and all("live_market_ssd" in sql for sql in ddl)


def test_v4_commit_plan_is_separate_from_existing_live_layout(monkeypatch):
    from src.trading_runtime.arte_strategy_one_entry_schema import ENTRY_EVIDENCE

    assert plan.profile_contracts("commit-v4") == V4_COMMIT_TABLES + (ENTRY_EVIDENCE,)
    present = V4_COMMIT_TABLES[0].name

    class Client:
        def execute(self, sql):
            assert sql.startswith("SELECT name FROM system.tables")
            return json.dumps({"name": present})

    checked = []
    monkeypatch.setattr(plan, "storage_preflight",
                        lambda _client, *, tables: checked.extend(tables))
    missing, ddl = plan.plan_missing(Client(), profile="commit-v4")
    assert [table.name for table in checked] == [present]
    assert missing == (V4_COMMIT_TABLES[1].name, ENTRY_EVIDENCE.name)
    assert len(ddl) == 2 and all("live_market_ssd" in statement for statement in ddl)
