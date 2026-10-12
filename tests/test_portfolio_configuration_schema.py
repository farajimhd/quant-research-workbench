from dataclasses import fields

from src.trading_runtime.portfolio import PortfolioPolicy
from src.trading_runtime import portfolio_configuration_schema as schema


def test_shared_policy_contract_defines_every_existing_policy_field():
    metadata = {"configuration_id", "membership_count", "membership_hash", "content_hash"}
    scalars = {name for name, _ in schema.POLICY.columns} - metadata
    assert scalars | set(schema.COLLECTION_FIELDS) == {field.name for field in fields(PortfolioPolicy)}
    assert not scalars & set(schema.COLLECTION_FIELDS)
    assert len(schema.POLICY.columns) == len({name for name, _ in schema.POLICY.columns})
    collection_type = dict(schema.MEMBERSHIP.columns)["collection"]
    assert all(f"'{name}'=" in collection_type for name in schema.COLLECTION_FIELDS)


def test_shared_policy_ddl_uses_operational_storage_and_no_opaque_fields():
    statements = schema.ddl()
    assert len(statements) == 2
    for table, statement in zip(schema.TABLES, statements, strict=True):
        assert table.name.startswith("trading_portfolio_")
        assert "live_market_ssd" in statement
        assert not any(kind.startswith(("JSON", "Object", "Array", "Map")) for _, kind in table.columns)
        assert not any(name in {"payload", "json", "blob", "node_id", "child_key"} for name, _ in table.columns)
