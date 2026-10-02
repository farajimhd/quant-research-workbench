import json

import pytest

from scripts.clickhouse import provision_backtest_v4_runner as provision
from src.trading_runtime import arte_journal_writer as writer_module
from src.trading_runtime.arte_journal_schema import (
    MARKET_READ_TABLES, PORTFOLIO_SNAPSHOT_WRITE_TABLES,
    V4_COMMIT_TABLES, V4_ORDER_COMMAND_LINEAGE, fixed_backtest_v2_contracts,
)
from src.trading_runtime.arte_backtest_definition import TABLES as DEFINITION_TABLES
from src.trading_runtime.arte_strategy_one_entry_schema import (
    ADD_EVIDENCE, ENTRY_EVIDENCE,
)
from src.trading_runtime.arte_broker_acknowledgement_v4 import ACKNOWLEDGEMENT
from src.trading_runtime.arte_portfolio_allocation_v4 import ALLOCATION as V4_ALLOCATION
from src.trading_runtime.arte_reservation_reason_v4 import RESERVATION_REASON
from src.backend.backtest_protection_change_v3 import TABLES as PROTECTION_CHANGE_TABLES
from src.trading_runtime.arte_protection_reconciliation_v4 import (
    TABLES as PROTECTION_RECONCILIATION_TABLES,
)


def test_v4_plan_has_exact_typed_append_surface_and_no_market_writes():
    plan = provision.desired_plan()
    assert plan.insert_arte == writer_module.v4_journal_write_tables()
    assert plan.principal == "backtest_v4_runner"
    assert plan.insert_arte == frozenset(
        provision._v4_family_table(table)
        for table, _, _, _ in provision._FAMILIES) | frozenset(
            table.name for table in V4_COMMIT_TABLES) | PORTFOLIO_SNAPSHOT_WRITE_TABLES | {
                ENTRY_EVIDENCE.name, ADD_EVIDENCE.name,
                V4_ALLOCATION.name, RESERVATION_REASON.name,
                V4_ORDER_COMMAND_LINEAGE.name,
                ACKNOWLEDGEMENT.name,
                provision.CANCEL.name,
                provision.REPRICE.name,
                provision.FAILURE.name,
                provision.PROFIT_GIVEBACK.name,
                provision.CONFIRMED_AH_FAILURE.name,
                provision.LIQUIDITY_FADE_FAILURE.name,
                provision.MOMENTUM.name,
                provision.INITIAL_MOMENTUM.name,
                provision.FIRST_PRICE.name,
                *(table.name for table in provision.OMS_TACTIC_TABLES),
                *(table.name for table in provision.RISK_ACTION_TABLES),
                *(table.name for table in PROTECTION_CHANGE_TABLES),
                *(table.name for table in PROTECTION_RECONCILIATION_TABLES),
                *(table.name for table in provision.PROTECTION_SNAPSHOT_TABLES),
                *(table.name for table in provision.MANAGER_SNAPSHOT_TABLES),
                *(table.name for table in provision.BROKER_MATCH_SNAPSHOT_TABLES),
                *(table.name for table in provision.EVIDENCE_SNAPSHOT_TABLES),
                *(table.name for table in provision.CAMPAIGN_SNAPSHOT_TABLES),
                *(table.name for table in provision.OMS_OBSERVATION_SNAPSHOT_TABLES),
                "trading_backtest_account_snapshot_v2",
                "trading_backtest_position_snapshot_v2",
                *(table.name for table in DEFINITION_TABLES)}
    assert "trading_strategy_signal_v1" not in plan.insert_arte
    assert "trading_strategy_signal_v2" in plan.insert_arte
    assert not plan.insert_arte & MARKET_READ_TABLES
    assert PORTFOLIO_SNAPSHOT_WRITE_TABLES <= plan.insert_arte
    assert PORTFOLIO_SNAPSHOT_WRITE_TABLES <= {
        table.name for table in fixed_backtest_v2_contracts()}
    assert plan.select_arte == frozenset(
            table.name for table in (*fixed_backtest_v2_contracts(), *V4_COMMIT_TABLES,
                                     V4_ORDER_COMMAND_LINEAGE,
                                     ENTRY_EVIDENCE, ADD_EVIDENCE,
                                     V4_ALLOCATION,
                                     RESERVATION_REASON,
                                     ACKNOWLEDGEMENT,
                                     provision.CANCEL,
                                     provision.REPRICE,
                                     provision.FAILURE,
                                     provision.PROFIT_GIVEBACK,
                                     provision.CONFIRMED_AH_FAILURE,
                                     provision.LIQUIDITY_FADE_FAILURE,
                                     provision.MOMENTUM,
                                     provision.INITIAL_MOMENTUM,
                                     provision.FIRST_PRICE,
                                     *provision.OMS_TACTIC_TABLES,
                                     *provision.RISK_ACTION_TABLES,
                                 *PROTECTION_CHANGE_TABLES,
                                 *PROTECTION_RECONCILIATION_TABLES,
                                 *provision.PROTECTION_SNAPSHOT_TABLES,
                                 *provision.MANAGER_SNAPSHOT_TABLES,
                                 *provision.BROKER_MATCH_SNAPSHOT_TABLES,
                                     *provision.EVIDENCE_SNAPSHOT_TABLES,
                                     *provision.CAMPAIGN_SNAPSHOT_TABLES,
                                     *provision.OMS_OBSERVATION_SNAPSHOT_TABLES,
                                     *DEFINITION_TABLES)) | MARKET_READ_TABLES | {
                                     provision.ENTRY_CONTEXT_TABLE.split(".", 1)[1]}
    assert all(" ON arte." in grant or " ON system." in grant
               for grant in plan.grants())


def test_runtime_preflight_uses_the_same_write_grants_as_provisioning(monkeypatch):
    observed = []
    checked_tables = []
    monkeypatch.setattr(writer_module, "storage_preflight",
                        lambda _client, *, tables: checked_tables.extend(tables))
    monkeypatch.setattr(writer_module, "_rows", lambda _client, sql: [
        {"name": provision.ENTRY_CONTEXT_TABLE.split(".", 1)[1]}])
    monkeypatch.setattr(writer_module, "journal_permission_preflight",
                        lambda _client, *, journal_tables, read_only_tables:
                        observed.append((journal_tables, read_only_tables)))
    writer_module._v4_preflight(object())
    assert provision.LIQUIDITY_FADE_FAILURE in checked_tables
    assert observed[0][0] == provision.desired_plan().insert_arte
    assert provision.ENTRY_CONTEXT_TABLE.split(".", 1)[1] in observed[0][1]
    assert provision.ENTRY_CONTEXT_TABLE.split(".", 1)[1] not in observed[0][0]


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
    assert f"preflight:{len(fixed_backtest_v2_contracts())}" in calls
    assert f"preflight:{len(provision.PROTECTION_SNAPSHOT_TABLES)}" in calls
    assert f"preflight:{len(provision.MANAGER_SNAPSHOT_TABLES)}" in calls
    assert f"preflight:{len(provision.BROKER_MATCH_SNAPSHOT_TABLES)}" in calls
    assert "preflight:2" in calls
    assert "preflight:1" in calls
    assert sum(sql.startswith("CREATE USER ") for sql in calls) == 1
    assert sum(sql.startswith("GRANT ") for sql in calls) == len(
        provision._desired_grants(provision.desired_plan()))
    assert calls.index("v4-preflight") > max(
        index for index, sql in enumerate(calls) if sql.startswith("GRANT "))
    assert calls[-1] == "writer.close"


def test_v4_retires_only_superseded_broker_grants():
    calls = []

    class Writer:
        def execute(self, sql):
            assert sql == "SHOW GRANTS FINAL"
            return (
                "GRANT SELECT, INSERT ON arte."
                "trading_strategy_one_broker_match_ticker_v1 "
                f"TO {provision.PRINCIPAL}\n"
                "GRANT SELECT ON arte.bars_v1 "
                f"TO {provision.PRINCIPAL}\n")

    class Admin:
        def execute(self, sql):
            calls.append(sql)

    provision._retire_snapshot_grants(Admin(), Writer())
    assert calls == [
        "REVOKE INSERT ON arte.trading_strategy_one_broker_match_ticker_v1 "
        f"FROM {provision.PRINCIPAL}",
        "REVOKE SELECT ON arte.trading_strategy_one_broker_match_ticker_v1 "
        f"FROM {provision.PRINCIPAL}",
    ]


@pytest.mark.parametrize("invalid_storage", ["missing_table", "wrong_policy", "wrong_part_disk"])
def test_liquidity_exit_storage_failure_precedes_credentials_and_grants(
    monkeypatch, invalid_storage,
):
    """Use the real metadata/part verifier at the provisioning boundary."""
    from src.trading_runtime.arte_journal_schema import (
        BATCH_LOOKUP_INDEX, storage_preflight,
    )

    table = provision.LIQUIDITY_FADE_FAILURE
    calls = []

    class Admin:
        def execute(self, sql):
            calls.append(sql)
            if sql == "SELECT currentUser()":
                return "administrator"
            if sql.startswith("SELECT count() FROM system.users"):
                return "0"
            if sql.startswith("SELECT disks FROM system.storage_policies"):
                return json.dumps({"disks": ["live_market_ssd"]})
            if "FROM system.tables" in sql:
                if invalid_storage == "missing_table":
                    return ""
                return json.dumps({
                    "name": table.name, "engine": "MergeTree",
                    "storage_policy": "default" if invalid_storage == "wrong_policy" else "live_market_ssd",
                    "partition_key": table.partition, "sorting_key": table.order,
                })
            if "FROM system.columns" in sql:
                return "\n".join(json.dumps({"table": table.name, "name": name, "type": kind})
                                 for name, kind in table.columns)
            if "FROM system.data_skipping_indices" in sql:
                return json.dumps({"table": table.name, "name": BATCH_LOOKUP_INDEX,
                                   "type": "bloom_filter", "expr": "batch_id", "granularity": 1})
            if "FROM system.parts" in sql and "disk_name!=" in sql:
                return json.dumps({"table": table.name, "disk_name": "default"})
            raise AssertionError(sql)

    def verify_liquidity(client, *, tables):
        if tables == (table,):
            storage_preflight(client, tables=tables)

    monkeypatch.setattr(provision, "storage_preflight", verify_liquidity)
    with pytest.raises(ValueError, match="missing|layout differs|outside live_market_ssd"):
        provision.apply_with_clients(
            admin=Admin(),
            credential=lambda **_: pytest.fail("credentials requested before storage proof"),
            client_factory=lambda *_: pytest.fail("writer opened before storage proof"),
        )
    assert all(sql.startswith("SELECT ") for sql in calls)
