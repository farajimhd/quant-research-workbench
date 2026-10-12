from src.trading_runtime import strategy_configuration_schema as schema


def test_configuration_seal_references_existing_authorities_and_all_children():
    columns = dict(schema.SELECTION.columns)
    assert columns["program_release_hash"] == "FixedString(64)"
    assert dict(schema.ACCOUNT.columns)["policy_hash"] == "FixedString(64)"
    for family in ("account", "mode", "mandate", "group", "group_member", "supervisor"):
        assert columns[family + "_count"] == "UInt32"
        assert columns[family + "_hash"] == "FixedString(64)"
    assert schema.TABLES[-1] is schema.SELECTION


def test_configuration_relations_are_central_typed_and_use_operational_storage():
    for table, statement in zip(schema.TABLES, schema.ddl(), strict=True):
        names = [name for name, _ in table.columns]
        assert len(names) == len(set(names))
        assert "configuration_id" in names
        assert "live_market_ssd" in statement
        assert not set(names) & {"payload", "blob", "json", "child_key", "value_kind"}
        assert not any(kind.startswith(("Array", "Map", "JSON", "Object")) for _, kind in table.columns)
        assert table.name.startswith("trading_strategy_configuration")
