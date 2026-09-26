import json

import pytest

from src.trading_runtime.arte_journal_schema import (
    TABLES, TableContract, BATCH_LOOKUP_INDEX, batch_lookup_index_upgrade_ddl,
    batch_lookup_index_materialize_ddl,
    intent_decision_upgrade_ddl,
    portfolio_policy_schema_upgrade_ddl, portfolio_snapshot_schema_upgrade_ddl,
    portfolio_reconciliation_event_upgrade_ddl,
    portfolio_snapshot_timestamp_upgrade_ddl,
    POLICY_ALLOWED_FIELDS, POLICY_ALLOWED_TABLES,
    POLICY_NUMERIC_FIELDS, POLICY_INTEGER_FIELDS, POLICY_BOOLEAN_FIELDS,
    intent_schema_upgrade_ddl, order_context_upgrade_ddl,
    oms_state_upgrade_ddl, intent_use_upgrade_ddl, run_transition_upgrade_ddl,
    operational_fault_upgrade_ddl,
    account_risk_upgrade_ddl, backtest_cursor_upgrade_ddl,
    backtest_snapshot_anchor_upgrade_ddl,
    journal_permission_preflight,
    schema_ddl, storage_preflight,
)
from src.trading_runtime.portfolio import PortfolioPolicy


def test_operator_schema_has_typed_arte_tables_on_market_ssd() -> None:
    statements = schema_ddl()
    assert len(statements) == len(TABLES) == 65
    assert any(table.name == "trading_strategy_signal_evidence_node_v1" for table in TABLES)
    upgrade = backtest_cursor_upgrade_ddl()
    assert len(upgrade) == 3
    assert "CREATE TABLE IF NOT EXISTS arte.trading_backtest_cursor_v1" in upgrade[0]
    assert "backtest_cursor_count UInt32 DEFAULT 0" in upgrade[1]
    assert "backtest_cursor_hash FixedString(64)" in upgrade[2]
    assert ("CREATE TABLE IF NOT EXISTS arte.trading_backtest_snapshot_anchor_v1"
            in backtest_snapshot_anchor_upgrade_ddl())
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
        if "batch_id" in dict(table.columns):
            assert f"INDEX {BATCH_LOOKUP_INDEX} batch_id TYPE bloom_filter(0.01) GRANULARITY 1" in statement


@pytest.mark.parametrize("column,kind", [
    ("payload_json", "String"), ("checkpoint_blob", "String"),
    ("details", "JSON"), ("facts", "Map(String, String)"),
    ("raw_bytes", "String"),
])
def test_journal_schema_rejects_unstructured_persistence(column: str, kind: str) -> None:
    with pytest.raises(ValueError, match="normalized typed contract"):
        TableContract("invalid_v1", ((column, kind),), "x", "x")


def test_nullable_sort_key_requires_explicit_clickhouse_setting() -> None:
    evidence = next(table for table in TABLES
                    if table.name == "trading_strategy_signal_evidence_node_v1")
    assert evidence.allow_nullable_key
    assert "allow_nullable_key = 1" in evidence.ddl()
    with pytest.raises(ValueError, match="nullable sorting key"):
        TableContract("invalid_v1", (("parent_id", "Nullable(UUID)"),),
                      "toYYYYMM(today())", "parent_id")


def test_batch_readback_index_upgrade_only_targets_typed_journal_tables() -> None:
    indexed = {table.name for table in TABLES if "batch_id" in dict(table.columns)}
    statements = batch_lookup_index_upgrade_ddl()
    assert len(statements) == len(indexed)
    assert {sql.split("arte.", 1)[1].split(" ", 1)[0] for sql in statements} == indexed
    assert all("ADD INDEX IF NOT EXISTS" in sql for sql in statements)
    materialize = batch_lookup_index_materialize_ddl()
    assert len(materialize) == len(statements)
    assert {sql.split("arte.", 1)[1].split(" ", 1)[0] for sql in materialize} == indexed


def test_intent_decision_upgrade_normalizes_reasons_and_commit_fence() -> None:
    statements = intent_decision_upgrade_ddl()
    assert len(statements) == 6
    assert "trading_intent_decision_v1" in statements[0]
    assert "trading_intent_decision_reason_v1" in statements[1]
    assert all("ALTER TABLE arte.trading_commit_v1" in sql for sql in statements[2:])
    assert not any("arte.bars_v1" in sql or "arte.liquidity_100ms_v1" in sql
                   for sql in statements)


def test_portfolio_policy_catalog_covers_all_fields_without_json() -> None:
    fields = set(PortfolioPolicy.__dataclass_fields__)
    mapped = ({"policy_id", "revision"} | set(POLICY_NUMERIC_FIELDS)
              | set(POLICY_INTEGER_FIELDS) | set(POLICY_BOOLEAN_FIELDS)
              | set(POLICY_ALLOWED_FIELDS))
    assert mapped == fields
    statements = portfolio_policy_schema_upgrade_ddl()
    assert len(statements) == 7
    assert not any("field_name" in statement or " value String" in statement
                   for statement in statements)
    assert all("live_market_ssd" in sql and "cityHash64(policy_hash) % 32" in sql
               for sql in statements)


def test_portfolio_snapshot_is_normalized_and_fenced() -> None:
    statements = portfolio_snapshot_schema_upgrade_ddl()
    assert len(statements) == 15
    assert all("live_market_ssd" in sql and "PARTITION BY" in sql
               for sql in statements)
    names = {sql.split("arte.", 1)[1].split(" ", 1)[0] for sql in statements}
    assert "trading_portfolio_snapshot_commit_v1" in names
    assert "trading_portfolio_request_reason_v1" in names
    assert "snapshot_at Nullable(DateTime64(6, 'UTC'))" in portfolio_snapshot_timestamp_upgrade_ddl()
    assert not any("payload_json" in sql or "blob" in sql for sql in statements)


def test_portfolio_reconciliation_upgrade_has_late_fence_and_commit_proof() -> None:
    statements = portfolio_reconciliation_event_upgrade_ddl()
    assert len(statements) == 5
    assert all("live_market_ssd" in sql and "toYYYYMM(" in sql
               for sql in statements[:3])
    assert "portfolio_reconciliation_event_count" in statements[3]
    assert "portfolio_reconciliation_event_hash" in statements[4]


def test_shared_event_and_execution_contract_uses_lossless_identifiers() -> None:
    columns = {table.name: dict(table.columns) for table in TABLES}
    policy_names = {"trading_portfolio_policy_v1", "trading_portfolio_policy_commit_v2"}
    policy_names.update(table for table, _ in POLICY_ALLOWED_TABLES.values())
    for name in columns.keys() - policy_names:
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


def test_run_transition_upgrade_is_typed_additive_and_journal_only() -> None:
    statements = run_transition_upgrade_ddl()
    assert len(statements) == 3
    assert "CREATE TABLE IF NOT EXISTS arte.trading_run_transition_v1" in statements[0]
    assert "storage_policy = 'live_market_ssd'" in statements[0]
    assert all("ALTER TABLE arte.trading_commit_v1" in sql for sql in statements[1:])
    assert not any("arte.bars_v1" in sql or "arte.indicators_v1" in sql
                   for sql in statements)


def test_operational_fault_upgrade_is_typed_and_journal_only() -> None:
    statements = operational_fault_upgrade_ddl()
    assert len(statements) == 3
    assert "CREATE TABLE IF NOT EXISTS arte.trading_operational_fault_v1" in statements[0]
    assert "storage_policy = 'live_market_ssd'" in statements[0]
    assert all("ALTER TABLE arte.trading_commit_v1" in sql for sql in statements[1:])
    assert not any("arte.bars_v1" in sql or "arte.indicators_v1" in sql
                   for sql in statements)


def test_account_risk_upgrade_normalizes_metrics_and_reasons() -> None:
    statements = account_risk_upgrade_ddl()
    assert len(statements) == 6
    assert all("storage_policy = 'live_market_ssd'" in sql for sql in statements[:2])
    assert all("ALTER TABLE arte.trading_commit_v1" in sql for sql in statements[2:])
    assert not any("arte.bars_v1" in sql or "arte.indicators_v1" in sql
                   for sql in statements)
    columns = dict(next(table for table in TABLES
                        if table.name == "trading_account_risk_state_v1").columns)
    assert columns["daily_loss"] == "Decimal(38, 18)"
    assert columns["position_count"] == "UInt32"


def test_preflight_requires_exact_layout_and_actual_ssd_parts() -> None:
    class Catalog:
        def __init__(self) -> None:
            self.wrong_disk = False
            self.unindexed_part = False

        def execute(self, sql: str) -> str:
            if "FROM system.storage_policies" in sql:
                rows = [{"disks": ["live_market_ssd"]}]
            elif "FROM system.tables" in sql:
                rows = [{"name": table.name, "engine": "MergeTree",
                         "storage_policy": "live_market_ssd",
                         "partition_key": table.partition,
                         "sorting_key": table.order.replace(",", ", ")}
                        for table in TABLES]
            elif "FROM system.columns" in sql:
                rows = [{"table": table.name, "name": name, "type": kind}
                        for table in sorted(TABLES, key=lambda item: item.name)
                        for name, kind in table.columns]
            elif "FROM system.data_skipping_indices" in sql:
                rows = [{"table": table.name, "name": BATCH_LOOKUP_INDEX,
                         "type": "bloom_filter", "expr": "batch_id", "granularity": 1}
                        for table in TABLES if "batch_id" in dict(table.columns)]
            elif "FROM system.parts" in sql:
                if "secondary_indices_compressed_bytes=0" in sql:
                    rows = ([{"table": "trading_event_v1", "name": "part-1"}]
                            if self.unindexed_part else [])
                else:
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
    client.wrong_disk = False
    client.unindexed_part = True
    with pytest.raises(ValueError, match="without materialized batch indexes"):
        storage_preflight(client)


def test_journal_principal_cannot_write_market_or_change_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.backend import live_signal_journal_preflight as staged_profile
    from src.backend.live_plan_membership import TABLES as membership_tables
    import src.trading_runtime.arte_journal_schema as schema_module

    market = {"bars_v1", "indicators_v1", "liquidity_100ms_v1",
              "structural_level_coverage_v7", "structural_level_observations_v7",
              "structural_levels_v7"}
    journal = {table.name for table in TABLES}
    staged_journal = {table.name for table in staged_profile.LIVE_SIGNAL_TABLES}
    membership_journal = {table.name for table in membership_tables}
    unrelated = "unrelated_operator_table_v1"

    class Grants:
        extra_grant = ""
        grant_line = ""
        staged = False
        membership = False
        reference = False
        missing_select = ""
        calls: list[str]

        def __init__(self) -> None:
            self.calls = []

        def execute(self, sql: str) -> str:
            self.calls.append(sql)
            if "FROM system.tables" in sql:
                if "database='q_live'" in sql:
                    return (json.dumps({"name": "market_stock_split_v1"})
                            if self.reference else "")
                assert "name IN (" in sql
                tables = (market | journal | (staged_journal if self.staged else set())
                          | (membership_journal if self.membership else set()))
                return "\n".join(json.dumps({"name": name}) for name in sorted(tables))
            if sql == "SELECT currentUser()":
                return "journal_writer\n"
            if sql == "SHOW GRANTS FINAL":
                writable = (journal | (staged_journal if self.staged else set())
                            | (membership_journal if self.membership else set()))
                grants = [*(f"GRANT SELECT, INSERT ON arte.{name} TO journal_writer"
                            for name in sorted(writable)),
                          *(f"GRANT SELECT ON arte.{name} TO journal_writer"
                            for name in sorted(market)),
                          *(f"GRANT SELECT ON system.{name} TO journal_writer"
                            for name in ("storage_policies", "tables", "columns", "parts",
                                         "data_skipping_indices"))]
                if self.grant_line:
                    grants.append(self.grant_line)
                if self.reference:
                    grants.append("GRANT SELECT ON q_live.market_stock_split_v1 TO journal_writer")
                if self.missing_select:
                    grants = [line for line in grants
                              if f"ON arte.{self.missing_select} " not in line]
                return "\n".join(grants)
            if sql.startswith("CHECK GRANT "):
                privilege, scope = sql.removeprefix("CHECK GRANT ").split(" ON ")
                if sql == self.extra_grant:
                    return "1\n"
                writable = (journal | (staged_journal if self.staged else set())
                            | (membership_journal if self.membership else set()))
                if privilege == "SELECT" and scope.removeprefix("arte.") in market | writable:
                    return "1\n"
                if privilege == "INSERT" and scope.removeprefix("arte.") in writable:
                    return "1\n"
                return "0\n"
            raise AssertionError(sql)

    client = Grants()
    journal_permission_preflight(client)
    assert "SHOW GRANTS FINAL" in client.calls
    assert sum(sql.startswith("CHECK GRANT ") for sql in client.calls) <= 8
    reference = frozenset({("q_live", "market_stock_split_v1")})
    with pytest.raises(ValueError, match="reference table"):
        journal_permission_preflight(client, reference_read_tables=reference)
    client.reference = True
    journal_permission_preflight(client, reference_read_tables=reference)
    client.grant_line = "GRANT SELECT ON q_live.* TO journal_writer"
    with pytest.raises(ValueError, match="unauthorized"):
        journal_permission_preflight(client, reference_read_tables=reference)
    client.grant_line = ""
    client.reference = False
    client.missing_select = "bars_v1"
    with pytest.raises(ValueError, match="cannot read"):
        journal_permission_preflight(client)
    client.missing_select = ""
    checked: list[bool] = []
    monkeypatch.setattr(staged_profile, "staged_live_signal_storage_preflight",
                        lambda _client: checked.append(True))
    client.staged = True
    journal_permission_preflight(client)
    assert checked == [True]
    client.staged = False
    checked_membership = []
    monkeypatch.setattr(schema_module, "storage_preflight",
                        lambda _client, *, tables: checked_membership.append(tables))
    client.membership = True
    journal_permission_preflight(client)
    assert checked_membership == [membership_tables]
    client.membership = False
    for grant in ("CHECK GRANT INSERT ON arte.bars_v1",
                  "CHECK GRANT INSERT ON arte.*",
                  "CHECK GRANT CREATE TABLE ON arte.*",
                  "CHECK GRANT DROP TABLE ON arte.bars_v1",
                  "CHECK GRANT ALTER DELETE ON arte.trading_event_v1"):
        client.extra_grant = grant
        with pytest.raises(ValueError):
            journal_permission_preflight(client)
    client.extra_grant = ""
    for grant in (f"GRANT INSERT ON arte.{unrelated} TO journal_writer",
                  "GRANT INSERT ON arte.bt_event_v1 TO journal_writer",
                  "GRANT INSERT ON arte.bt_blob_v1 TO journal_writer",
                  "GRANT ALTER ON arte.trading_event_v1 TO journal_writer",
                  "GRANT editor TO journal_writer",
                  "GRANT SELECT ON arte.* TO journal_writer"):
        client.grant_line = grant
        with pytest.raises(ValueError):
            journal_permission_preflight(client)
