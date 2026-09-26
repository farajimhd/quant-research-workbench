"""Normalized HOD product excludes opaque persisted evidence."""

import json

import pytest

from src.trading_runtime.strategy_one_hod_schema import (
    PRODUCT_DIGEST, ddl, verify_tables,
)


def test_hod_ddl_is_normalized_and_ssd_only():
    statements = ddl()
    assert len(statements) == 2
    assert len(PRODUCT_DIGEST) == 64
    for sql in statements:
        assert "storage_policy='live_market_ssd'" in sql
        assert "PARTITION BY toYYYYMM(session_date)" in sql
        assert "payload_json" not in sql
        assert "blob" not in sql.lower()
    assert "candidate_content_hash FixedString(64)" in statements[1]
    assert "v7_seed_plan_token FixedString(64)" in statements[1]


def test_hod_schema_rejects_misplaced_parts():
    class Client:
        def execute(self, sql):
            if "system.storage_policies" in sql:
                rows = [dict(disks=["live_market_ssd"])]
            elif "system.tables" in sql:
                rows = [dict(name=name, engine="MergeTree",
                             storage_policy="live_market_ssd",
                             partition_key="toYYYYMM(session_date)",
                             sorting_key="source_build_id,session_date,ticker,"
                                         "derivation_attempt_id" + (
                                             ",boundary_ms" if name.endswith("context_v1")
                                             else ""))
                        for name in ("strategy_one_hod_context_v1",
                                     "strategy_one_hod_coverage_v1")]
            elif "system.columns" in sql:
                from src.trading_runtime.strategy_one_hod_schema import (
                    _CONTEXT_COLUMNS, _COVERAGE_COLUMNS,
                )
                rows = [dict(table=table, name=name, type=kind, position=index)
                        for table, columns in (
                            ("strategy_one_hod_context_v1", _CONTEXT_COLUMNS),
                            ("strategy_one_hod_coverage_v1", _COVERAGE_COLUMNS))
                        for index, (name, kind) in enumerate(columns, 1)]
            elif "system.parts" in sql:
                rows = [dict(table="strategy_one_hod_context_v1",
                             disk_name="default")]
            else:
                raise AssertionError(sql)
            return "\n".join(json.dumps(row) for row in rows)

    with pytest.raises(RuntimeError, match="outside SSD"):
        verify_tables(Client())
