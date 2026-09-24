import json

import pytest

from src.trading_runtime.arte_journal_schema import TABLES, schema_ddl, storage_preflight


def test_operator_schema_has_typed_arte_tables_on_market_ssd() -> None:
    statements = schema_ddl()
    assert len(statements) == len(TABLES) == 11
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
