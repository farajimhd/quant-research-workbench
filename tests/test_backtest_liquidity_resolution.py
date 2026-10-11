from dataclasses import replace
import json

import pytest

from test_liquidity_resolution_producer import Client, Store, run, source
from pipelines.market_sip.events import liquidity_resolution_sql as sql
from src.backend.backtest_liquidity_resolution import (
    certify_liquidity_resolution, liquidity_window_sql, read_liquidity_window,
)


def certified(store):
    return certify_liquidity_resolution(Client(store), source().unit,
        producer_code_hash="f" * 64, expected_execution_volume=90.)


def test_reader_requires_seal_parents_children_and_select_only_grants():
    store = Store()
    with pytest.raises(ValueError, match="missing or duplicate"):
        certified(store)
    run(store)
    seal = certified(store)
    assert seal.resolution_ms == 1000 and seal.liquidity_row_count == 1
    assert len(store.writes) == 3  # The reader never adds writes.
    with pytest.raises(ValueError, match="SELECT-only"):
        certify_liquidity_resolution(Client(store, writer=True), source().unit,
            producer_code_hash="f" * 64, expected_execution_volume=90.)
    with pytest.raises(ValueError, match="parents/code"):
        certify_liquidity_resolution(Client(store), replace(source().unit,
            source_market_unit_hash="a" * 64), producer_code_hash="f" * 64,
            expected_execution_volume=90.)
    store.bad_parity = True
    with pytest.raises(ValueError, match="bucket volumes"):
        certified(store)


def test_reader_rejects_duplicate_and_tampered_seals():
    store = Store()
    run(store)
    original = dict(store.coverage[0])
    store.coverage.append(original)
    with pytest.raises(ValueError, match="duplicate"):
        certified(store)
    store.coverage.pop()
    store.coverage[0]["total_execution_volume"] = 91.
    with pytest.raises(ValueError, match="hash differs"):
        certified(store)


def test_window_is_bounded_driven_by_liquidity_and_keeps_quote_only_rows():
    store = Store()
    run(store)
    seal = certified(store)
    query = liquidity_window_sql(seal, first_bucket=50400, stop_bucket=50656)
    assert "LEFT JOIN" in query and "LIMIT 257" in query
    assert "liquidity_bars_v2" in query and "liquidity_execution_price_bars_v2" in query
    assert "source_attempt_id=" in query and seal.derivation_attempt_id in query
    with pytest.raises(ValueError, match="256"):
        liquidity_window_sql(seal, first_bucket=50400, stop_bucket=50657)
    class WindowReader:
        def __init__(self, rows):
            self.rows = rows
        def execute(self, query):
            assert query.startswith("SELECT")
            return "\n".join(json.dumps(row) for row in self.rows)
    row = {name: 0 for name, _ in sql.LIQUIDITY.columns}
    row.update(ticker="AAPL", resolution_ms=1000, bucket_index=50400,
        source_build_id=seal.source.build_id, session_date=seal.source.session_date.isoformat(),
        source_attempt_id=seal.source.source_attempt_id,
        derivation_attempt_id=seal.derivation_attempt_id, execution_price_levels=[])
    result = read_liquidity_window(WindowReader([row]), seal,
                                  first_bucket=50400, stop_bucket=50402)
    assert len(result) == 1 and result[0]["execution_price_levels"] == ()
    with pytest.raises(ValueError, match="ordering"):
        read_liquidity_window(WindowReader([row, row]), seal,
                              first_bucket=50400, stop_bucket=50402)
