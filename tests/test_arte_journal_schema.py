import json

import pytest

from src.trading_runtime.arte_journal_schema import (
    TABLES, intent_schema_upgrade_ddl, order_context_upgrade_ddl,
    oms_state_upgrade_ddl, intent_use_upgrade_ddl,
    journal_permission_preflight,
    schema_ddl, storage_preflight,
)


def test_operator_schema_has_typed_arte_tables_on_market_ssd() -> None:
    statements = schema_ddl()
    assert len(statements) == len(TABLES) == 23
    for table, statement in zip(TABLES, statements):
        assert f"CREATE TABLE IF NOT EXISTS arte.{table.name}" in statement
        assert "ENGINE = MergeTree" in statement
        assert f"PARTITION BY {table.partition}" in statement
        assert f"ORDER BY ({table.order})" in statement
        assert "storage_policy = 'live_market_ssd'" in statement
        assert not any(token in statement.lower() for token in (
            "payload_json", "state_json", "raw_json", "blob", "object('json')",
        ))
        assert len({name for name, _ in table.columns}) == len(table.columns)


def test_shared_event_and_execution_contract_uses_lossless_identifiers() -> None:
    columns = {table.name: dict(table.columns) for table in TABLES}
    for name in columns:
        assert columns[name]["run_id"] == "String"
    assert columns["trading_event_v1"]["record_id"] == "UUID"
    assert columns["trading_event_v1"]["sequence"] == "UInt64"
    assert columns["trading_execution_v1"]["quantity"] == "Decimal(38, 10)"
    assert columns["trading_execution_v1"]["price"] == "Decimal(38, 10)"
    assert columns["trading_execution_v1"]["currency"] == "LowCardinality(String)"
    for field in ("net_amount", "cumulative_quantity", "average_price",
                  "signal_price", "arrival_midpoint", "planned_risk"):
        assert columns["trading_execution_v1"][field] == "Nullable(Decimal(38, 10))"
    assert columns["trading_execution_v1"]["liquidation_trade"] == "UInt8"
    assert columns["trading_commission_v1"]["commission"] == "Decimal(38, 10)"
    assert columns["trading_commission_v1"]["time_authority"] == "LowCardinality(String)"
    assert columns["trading_strategy_signal_v1"]["score"] == "Decimal(38, 18)"
    assert columns["trading_signal_source_v1"]["parent_record_id"] == "UUID"
    assert columns["trading_order_command_v1"]["trailing_amount"] == "Nullable(Decimal(38, 10))"
    assert columns["trading_order_command_v1"]["parent_broker_order_id"] == "String"
    assert columns["trading_runtime_config_v1"]["strategy_revision"] == "UInt32"
    assert columns["trading_run_account_v1"]["ordinal"] == "UInt16"
    assert columns["trading_run_context_commit_v1"]["account_hash"] == "FixedString(64)"
    assert columns["trading_strategy_intent_v1"]["quantity"] == "Decimal(38, 18)"
    assert columns["trading_intent_protection_slice_v1"]["parent_record_id"] == "UUID"
    assert columns["trading_commit_v1"]["intent_slice_hash"] == "FixedString(64)"
    assert columns["trading_order_command_context_v1"]["parent_record_id"] == "UUID"
    assert columns["trading_commit_v1"]["order_context_hash"] == "FixedString(64)"


def test_intent_upgrade_is_journal_only_and_backfills_empty_fence_hashes() -> None:
    statements = intent_schema_upgrade_ddl()
    assert len(statements) == 6
    assert "storage_policy = 'live_market_ssd'" in statements[0]
    assert "storage_policy = 'live_market_ssd'" in statements[1]
    assert all("arte.trading_commit_v1" in sql for sql in statements[2:])
    assert all("IF NOT EXISTS" in sql for sql in statements)
    assert not any("arte.bars_v1" in sql or "arte.indicators_v1" in sql
                   or "arte.liquidity_100ms_v1" in sql for sql in statements)
    assert statements[4].count("4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945") == 1


def test_order_context_upgrade_does_not_mutate_order_commands_or_market() -> None:
    statements = order_context_upgrade_ddl()
    assert len(statements) == 3
    assert "storage_policy = 'live_market_ssd'" in statements[0]
    assert all("arte.trading_commit_v1" in sql for sql in statements[1:])
    assert not any("ALTER TABLE arte.trading_order_command_v1" in sql
                   or "arte.bars_v1" in sql for sql in statements)


def test_oms_upgrade_is_typed_additive_and_journal_only() -> None:
    statements = oms_state_upgrade_ddl()
    assert len(statements) == 15
    assert all("storage_policy = 'live_market_ssd'" in sql for sql in statements[:5])
    assert all("ALTER TABLE arte.trading_commit_v1" in sql for sql in statements[5:])
    assert all("IF NOT EXISTS" in sql for sql in statements)
    assert not any("arte.bars_v1" in sql or "arte.indicators_v1" in sql
                   or "arte.liquidity_100ms_v1" in sql for sql in statements)


def test_intent_use_upgrade_links_exact_revisions_without_market_writes() -> None:
    statements = intent_use_upgrade_ddl()
    assert len(statements) == 3
    assert "storage_policy = 'live_market_ssd'" in statements[0]
    assert all("ALTER TABLE arte.trading_commit_v1" in sql for sql in statements[1:])
    assert all("IF NOT EXISTS" in sql for sql in statements)
    assert not any("arte.bars_v1" in sql or "arte.indicators_v1" in sql for sql in statements)


def test_preflight_requires_exact_layout_and_actual_ssd_parts() -> None:
    class Catalog:
        def __init__(self) -> None:
            self.wrong_disk = False

        def execute(self, sql: str) -> str:
            if "FROM system.storage_policies" in sql:
                rows = [{"disks": ["live_market_ssd"]}]
            elif "FROM system.tables" in sql:
                rows = [{"name": table.name, "engine": "MergeTree",
                         "storage_policy": "live_market_ssd",
                         "partition_key": table.partition, "sorting_key": table.order}
                        for table in TABLES]
            elif "FROM system.columns" in sql:
                rows = [{"table": table.name, "name": name, "type": kind}
                        for table in sorted(TABLES, key=lambda item: item.name)
                        for name, kind in table.columns]
            elif "FROM system.parts" in sql:
                rows = ([{"table": TABLES[0].name, "disk_name": "default"}]
                        if self.wrong_disk else [])
            else:
                raise AssertionError(sql)
            return "\n".join(json.dumps(row) for row in rows)

    client = Catalog()
    storage_preflight(client)
    client.wrong_disk = True
    with pytest.raises(ValueError, match="outside live_market_ssd"):
        storage_preflight(client)


def test_journal_principal_cannot_write_market_or_change_schema() -> None:
    market = {"bars_v1", "indicators_v1", "liquidity_100ms_v1",
              "structural_level_coverage_v7", "structural_level_observations_v7",
              "structural_levels_v7"}
    journal = {table.name for table in TABLES}

    class Grants:
        extra_grant = ""

        def execute(self, sql: str) -> str:
            if "FROM system.tables" in sql:
                return "\n".join(json.dumps({"name": name}) for name in sorted(market | journal))
            if sql.startswith("CHECK GRANT "):
                privilege, scope = sql.removeprefix("CHECK GRANT ").split(" ON ")
                if sql == self.extra_grant:
                    return "1\n"
                if privilege == "SELECT" and scope.removeprefix("arte.") in market | journal:
                    return "1\n"
                if privilege == "INSERT" and scope.removeprefix("arte.") in journal:
                    return "1\n"
                return "0\n"
            raise AssertionError(sql)

    client = Grants()
    journal_permission_preflight(client)
    for grant in ("CHECK GRANT INSERT ON arte.bars_v1",
                  "CHECK GRANT INSERT ON arte.*",
                  "CHECK GRANT CREATE TABLE ON arte.*",
                  "CHECK GRANT DROP TABLE ON arte.bars_v1",
                  "CHECK GRANT ALTER DELETE ON arte.trading_event_v1"):
        client.extra_grant = grant
        with pytest.raises(ValueError):
            journal_permission_preflight(client)
