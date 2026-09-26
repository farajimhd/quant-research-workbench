"""Disjoint V7 supplement authority is normalized and SELECT-only."""
import json

import pytest

from src.trading_runtime.structural_v7_lineage import (
    TABLE, certified_ticker_lineage, ddl, verify_table,
)


class Reader:
    def __init__(self, rows=()):
        self.rows = rows
        self.queries = []

    def execute(self, sql):
        self.queries.append(sql)
        if "FROM system.tables" in sql:
            rows = [dict(engine="MergeTree", partition_key="toYYYYMM(verified_at)",
                         sorting_key="parent_source_plan_hash, supplement_source_plan_hash, ticker",
                         storage_policy="live_market_ssd")]
        elif "FROM system.storage_policies" in sql:
            rows = [dict(disks=["live_market_ssd"])]
        elif "FROM system.columns" in sql:
            rows = [dict(name=name, type=kind) for name, kind in (
                ("parent_source_plan_hash", "FixedString(64)"),
                ("supplement_source_plan_hash", "FixedString(64)"),
                ("ticker", "LowCardinality(String)"),
                ("verified_at", "DateTime64(6, 'UTC')"))]
        elif "FROM system.parts" in sql:
            rows = []
        elif "FROM " + TABLE in sql:
            rows = self.rows
        else:
            raise AssertionError(sql)
        return "\n".join(json.dumps(row) for row in rows)


def test_lineage_layout_is_normalized_and_ssd_only():
    assert "source_plan_hash FixedString(64)" in ddl()
    assert "storage_policy='live_market_ssd'" in ddl()
    reader = Reader()
    verify_table(reader)
    assert len(reader.queries) == 4
    assert all(sql.startswith("SELECT ") for sql in reader.queries)


def test_lineage_reads_only_certified_supplement_membership():
    row = dict(parent_source_plan_hash="a" * 64,
               supplement_source_plan_hash="b" * 64, ticker="FIXED")
    reader = Reader((row,))
    assert certified_ticker_lineage(reader, tickers=("BASE", "FIXED")) == (row,)
    with pytest.raises(ValueError, match="duplicate"):
        certified_ticker_lineage(Reader((row, row)), tickers=("FIXED",))
    with pytest.raises(ValueError, match="canonical"):
        certified_ticker_lineage(reader, tickers=("FIXED'",))
