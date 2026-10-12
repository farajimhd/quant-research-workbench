"""Future configurations reuse the existing central immutable policy catalogue."""
from dataclasses import fields

from src.trading_runtime.portfolio import PortfolioPolicy
from src.trading_runtime import arte_journal_schema as schema


def test_central_policy_catalogue_defines_every_existing_policy_field():
    defined = ({"policy_id", "revision"} | set(schema.POLICY_NUMERIC_FIELDS)
               | set(schema.POLICY_INTEGER_FIELDS) | set(schema.POLICY_BOOLEAN_FIELDS)
               | set(schema.POLICY_ALLOWED_FIELDS))
    assert defined == {field.name for field in fields(PortfolioPolicy)}
    tables = {table.name: table for table in schema.TABLES}
    root = tables["trading_portfolio_policy_v1"]
    assert {name for name, _ in root.columns} == defined - set(schema.POLICY_ALLOWED_FIELDS) | {"policy_hash"}
    assert set(schema.POLICY_ALLOWED_TABLES) == set(schema.POLICY_ALLOWED_FIELDS)
    for name, (table, column) in schema.POLICY_ALLOWED_TABLES.items():
        assert {field for field, _ in tables[table].columns} == {"policy_hash", "ordinal", column}


def test_central_policy_ddl_has_no_opaque_fields_and_uses_operational_storage():
    statements = schema.portfolio_policy_schema_upgrade_ddl()
    assert len(statements) == 7  # scalar root, five named lists, final commit fence
    assert all("live_market_ssd" in statement for statement in statements)
    assert all("JSON" not in statement and "Object(" not in statement for statement in statements)
