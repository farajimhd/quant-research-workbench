from copy import deepcopy
from datetime import datetime, timezone

import pytest

from src.backend.fixed_watchlist_scanner_sidecar import (
    SCANNER_SCHEMA, SCORE_REVISION, _hash, load_scanner_boundary,
    operator_publication_plan, prepare_scanner_boundary, sidecar_ddl,
)


AT = datetime(2026, 8, 18, 14, 0, 0, 100000, tzinfo=timezone.utc)


def _snapshot():
    row = {
        "ticker": "AAPL", "last_event_ts": AT.isoformat(),
        "event_age_ms": 0, "quality_state": "ready",
        "quality_flags": [], "degradation_reason": None,
        "previous_close": 9.0,
        "liquidity_eligibility_reasons": [], "liquidity_eligible": True,
        "last_price": 10.0, "bid": 9.99, "ask": 10.01,
        "bid_size": 100, "ask_size": 100,
        "day_dollar_volume": 2_000_000.0, "day_volume": 200_000.0,
        "day_trade_count": 2000, "trade_rate_10s": 1.2,
        "trade_rate_60s": 0.8, "spread": 0.02,
        "liquidity_score": 72.5, "liquidity_rank": 1,
    }
    return {
        "as_of": AT.isoformat(), "schema_version": SCANNER_SCHEMA,
        "scanner_score_revision": SCORE_REVISION, "event_count": 3000,
        "source_revision": {"token": "revision-1", "source_plan_hash": "source-1",
                            "complete_for_history": True, "request_complete": True},
        "market_rows": [row], "market_row_count": 1,
        "market_rows_sha256": _hash([row]),
    }


class Client:
    def __init__(self, boundary, rows):
        self.boundary = boundary
        self.rows = rows
        self.sql = []

    def iter_json_each_row(self, sql):
        self.sql.append(sql)
        if "qmd_scanner_boundary_v1" in sql:
            return iter([self.boundary])
        if "qmd_scanner_symbol_v1" in sql:
            return iter(self.rows)
        raise AssertionError(sql)


def test_staged_sidecar_contract_is_scalar_and_market_ssd_only():
    ddl = sidecar_ddl()
    assert len(ddl) == 2
    assert all("live_market_ssd" in sql and "payload_json" not in sql
               and "ENGINE=MergeTree" in sql for sql in ddl)
    assert all("INSERT" not in sql for sql in ddl)


def test_producer_certified_boundary_readback_rejects_corruption():
    boundary, rows = prepare_scanner_boundary(_snapshot(), market_plan_token="a" * 64)
    assert boundary["market_row_count"] == 1
    assert rows[0]["liquidity_score"].startswith("72.5")
    client = Client(boundary, list(rows))
    assert load_scanner_boundary(client, boundary["boundary_id"],
                                 market_plan_token="a" * 64,
                                 source_revision_token="revision-1",
                                 boundary_at=AT)[1] == rows
    assert all(sql.startswith("SELECT") for sql in client.sql)
    corrupt = deepcopy(rows[0])
    corrupt["liquidity_score"] = "99.000000000000000000"
    with pytest.raises(RuntimeError, match="content or full-scope"):
        load_scanner_boundary(Client(boundary, [corrupt]), boundary["boundary_id"],
                              market_plan_token="a" * 64,
                              source_revision_token="revision-1",
                              boundary_at=AT)
    with pytest.raises(RuntimeError, match="content or full-scope"):
        load_scanner_boundary(Client(boundary, list(rows)), boundary["boundary_id"],
                              market_plan_token="b" * 64,
                              source_revision_token="revision-1",
                              boundary_at=AT)


def test_operator_plan_seals_complete_rows_before_boundary_without_writes():
    plan = operator_publication_plan(_snapshot(), market_plan_token="a" * 64)
    assert tuple(table for table, _ in plan) == (
        "arte.qmd_scanner_symbol_v1", "arte.qmd_scanner_boundary_v1",
    )
    assert len(plan[0][1]) == plan[1][1][0]["market_row_count"] == 1
    assert plan[0][1][0]["boundary_id"] == plan[1][1][0]["boundary_id"]
    assert all("content_hash" in row for _, rows in plan for row in rows)


def test_current_qmd_snapshot_without_upstream_certification_fails_closed():
    source = _snapshot()
    del source["scanner_score_revision"]
    with pytest.raises(ValueError, match="certified score revision"):
        prepare_scanner_boundary(source, market_plan_token="a" * 64)
    source = _snapshot()
    source["market_row_count"] = 2
    with pytest.raises(ValueError, match="source population certificate"):
        prepare_scanner_boundary(source, market_plan_token="a" * 64)
