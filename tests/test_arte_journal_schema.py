from src.trading_runtime.arte_journal_schema import TABLES, schema_ddl


def test_operator_schema_has_typed_arte_tables_on_market_ssd() -> None:
    statements = schema_ddl()
    assert len(statements) == len(TABLES) == 4
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
    assert columns["trading_commission_v1"]["commission"] == "Decimal(38, 10)"
