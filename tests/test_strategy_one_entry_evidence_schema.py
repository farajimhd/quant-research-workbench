"""Strategy 1 reusable entry evidence is scalar, normalized, and SSD-only."""
import json

import pytest

from src.trading_runtime import strategy_one_entry_evidence_schema as schema


class Catalog:
    def __init__(self, *, misplaced=False, policy=True, column_drift=False):
        self.queries = []
        self.misplaced = misplaced
        self.policy = policy
        self.column_drift = column_drift

    def execute(self, query):
        self.queries.append(query)
        if query.startswith("SELECT disks FROM system.storage_policies"):
            rows = [{"disks": [schema.STORAGE_POLICY]}] if self.policy else []
        elif "FROM system.tables" in query:
            rows = [dict(name=name.rsplit(".", 1)[1], engine="MergeTree",
                         storage_policy=schema.STORAGE_POLICY,
                         partition_key="toYYYYMM(session_date)", sorting_key=sorting)
                    for name, _, sorting in schema._LAYOUT]
        elif "FROM system.columns" in query:
            rows = [dict(table=name.rsplit(".", 1)[1], name=column,
                         type=("String" if self.column_drift and column == "target_price"
                               else type_name), position=index + 1)
                    for name, columns, _ in schema._LAYOUT
                    for index, (column, type_name) in enumerate(columns)]
        elif "FROM system.parts" in query:
            rows = ([{"table": "strategy_one_entry_evidence_v1",
                      "disk_name": "default"}] if self.misplaced else [])
        elif query.startswith("CREATE TABLE IF NOT EXISTS"):
            return ""
        else:
            raise AssertionError(query)
        return "\n".join(json.dumps(row) for row in rows)


def test_all_four_tables_are_normalized_partitioned_and_ordered():
    statements = schema.ddl()
    assert len(statements) == 4
    for statement in statements:
        assert "PARTITION BY toYYYYMM(session_date)" in statement
        assert "ORDER BY (source_build_id,session_date,ticker,derivation_attempt_id" in statement
        assert "storage_policy='live_market_ssd'" in statement
        assert not any(opaque in statement.lower() for opaque in (" json", " blob", " object"))
    assert "episode_start_ms,ordinal" in statements[1]
    assert "boundary_ms" in statements[2]
    assert "candidate_content_hash" in statements[3]


def test_exact_catalog_and_physical_ssd_placement_are_required():
    schema.verify_tables(Catalog())
    with pytest.raises(RuntimeError, match="outside SSD"):
        schema.verify_tables(Catalog(misplaced=True))
    with pytest.raises(RuntimeError, match="columns differ"):
        schema.verify_tables(Catalog(column_drift=True))


def test_installer_fails_before_ddl_when_policy_is_unavailable():
    client = Catalog(policy=False)
    with pytest.raises(RuntimeError, match="SSD-only"):
        schema.install_tables(client)
    assert not any(query.startswith("CREATE TABLE") for query in client.queries)
    client = Catalog()
    schema.install_tables(client)
    assert sum(query.startswith("CREATE TABLE") for query in client.queries) == 4
