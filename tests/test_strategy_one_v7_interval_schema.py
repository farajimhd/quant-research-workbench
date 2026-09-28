"""Normalized, SSD-only producer contract for reusable intraday V7."""
import json

import pytest

from src.trading_runtime import strategy_one_v7_interval_schema as schema


class Catalog:
    def __init__(self, *, wrong_disk=False, missing_policy=False):
        self.wrong_disk = wrong_disk
        self.missing_policy = missing_policy
        self.created = []

    def execute(self, query):
        if query.startswith("CREATE TABLE"):
            self.created.append(query)
            return ""
        if "system.storage_policies" in query:
            return "" if self.missing_policy else json.dumps({
                "disks": [schema.STORAGE_POLICY]})
        if "system.tables" in query:
            return "\n".join(json.dumps({
                "name": table.rsplit(".", 1)[1], "engine": "MergeTree",
                "storage_policy": schema.STORAGE_POLICY,
                "partition_key": "toYYYYMM(session_date)",
                "sorting_key": schema.SORT_KEYS[table],
            }) for table in schema.TABLES)
        if "system.columns" in query:
            return "\n".join(json.dumps({
                "table": table.rsplit(".", 1)[1], "name": name,
                "type": kind, "position": index,
            }) for table in schema.TABLES
                for index, (name, kind) in enumerate(schema.CONTRACTS[table], 1))
        if "system.parts" in query:
            return (json.dumps({"table": "strategy_one_v7_clock_v1",
                                "disk_name": "default"})
                    if self.wrong_disk else "")
        raise AssertionError(query)


def test_v7_derivative_is_three_scalar_ssd_tables():
    statements = schema.ddl()
    assert len(statements) == 3
    assert len(schema.PRODUCT_DIGEST) == 64
    assert all("storage_policy='live_market_ssd'" in sql
               and "PARTITION BY toYYYYMM(session_date)" in sql
               and "ENGINE=MergeTree" in sql
               for sql in statements)
    assert all("JSON" not in sql and "Blob" not in sql and "String" in sql
               for sql in statements)
    assert "valid_from_ms" in statements[1]
    assert "valid_to_ms" in statements[1]
    assert "source_checkpoint_hash" in statements[2]
    assert "decoded_seed_hash" in statements[2]
    client = Catalog()
    schema.install_tables(client)
    assert client.created == list(statements)


def test_v7_derivative_fails_closed_on_policy_or_part_misplacement():
    with pytest.raises(RuntimeError, match="SSD-only"):
        schema.install_tables(Catalog(missing_policy=True))
    with pytest.raises(RuntimeError, match="outside SSD"):
        schema.verify_tables(Catalog(wrong_disk=True))
