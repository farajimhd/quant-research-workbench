"""Candidate sidecar is sparse, normalized, and separate from source products."""
import json

import pytest

from src.trading_runtime.strategy_one_candidate_schema import (
    CANDIDATE_TABLE, COVERAGE_TABLE, _CANDIDATE_COLUMNS,
    _COVERAGE_COLUMNS, ddl, install_tables, verify_tables,
)


def test_candidate_layout_is_typed_ssd_and_coverage_last():
    candidate, coverage = ddl()
    assert CANDIDATE_TABLE in candidate and COVERAGE_TABLE in coverage
    assert "storage_policy='live_market_ssd'" in candidate
    assert "storage_policy='live_market_ssd'" in coverage
    assert "PARTITION BY toYYYYMM(session_date)" in candidate
    assert "PARTITION BY toYYYYMM(session_date)" in coverage
    assert "source_row_index UInt32" in candidate
    assert "macd_30s_boundary_ms UInt32" in candidate
    assert "stop_low_int UInt64" in candidate
    assert "candidate_count UInt32" in coverage
    assert "bars_attempt_id UUID" in coverage
    assert "technical_attempt_id UUID" in coverage
    assert "liquidity_attempt_id UUID" in coverage
    assert all(forbidden not in (candidate + coverage).lower()
               for forbidden in ("json", "blob", "sqlite"))
    assert "arte.bars_v1" not in candidate + coverage


class Catalog:
    def __init__(self, *, misplaced=False, bad_column=False):
        self.sql = []
        self.misplaced = misplaced
        self.bad_column = bad_column

    def execute(self, query):
        self.sql.append(query)
        if query.startswith("CREATE TABLE"):
            return ""
        if "FROM system.storage_policies" in query:
            return '{"disks":["live_market_ssd"]}'
        if "FROM system.tables" in query:
            return "\n".join(json.dumps({
                "name": name, "engine": "MergeTree",
                "storage_policy": "live_market_ssd",
                "partition_key": "toYYYYMM(session_date)",
                "sorting_key": "source_build_id, session_date, ticker, "
                               "derivation_attempt_id" +
                               (", boundary_ms" if name.endswith("candidate_v1") else ""),
            }) for name in ("strategy_one_candidate_v1",
                            "strategy_one_candidate_coverage_v1"))
        if "FROM system.columns" in query:
            rows = []
            for name, fields in (("strategy_one_candidate_v1", _CANDIDATE_COLUMNS),
                                 ("strategy_one_candidate_coverage_v1", _COVERAGE_COLUMNS)):
                rows.extend(json.dumps({
                    "table": name, "name": field, "type": (
                        "String" if self.bad_column and field == "stop_low_int" else kind),
                    "position": position,
                }) for position, (field, kind) in enumerate(fields, 1))
            return "\n".join(rows)
        if "FROM system.parts" in query:
            return '{"table":"strategy_one_candidate_v1","disk_name":"default"}' if self.misplaced else ""
        raise AssertionError(query)


def test_install_verifies_exact_layout_and_part_placement():
    client = Catalog()
    install_tables(client)
    assert sum(query.startswith("CREATE TABLE") for query in client.sql) == 2
    assert any("FROM system.parts" in query for query in client.sql)


@pytest.mark.parametrize("fault", ["misplaced", "bad_column"])
def test_candidate_catalog_fails_closed(fault):
    with pytest.raises(RuntimeError):
        verify_tables(Catalog(**{fault: True}))
