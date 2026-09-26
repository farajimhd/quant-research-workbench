from __future__ import annotations

import json

import pytest

from src.backend.backtest_market_data import CertifiedMarketDayPlan, ExecutionInterval
from src.backend.backtest_strategy_one_identity import (
    certify_identity_plan, identity_content_hash,
)
from src.trading_runtime.strategy_one_identity_schema import ddl


BUILD = "a" * 64
ATTEMPT = "00000000-0000-0000-0000-000000000001"
ROWS = [
    {"ticker": "AAA", "symbol_id": "s1", "listing_id": "l1",
     "security_id": "x1", "ibkr_conid": 101, "source_run_id": "u1"},
    {"ticker": "BBB", "symbol_id": "s2", "listing_id": "l2",
     "security_id": "x2", "ibkr_conid": 102, "source_run_id": "u1"},
]


def _market() -> CertifiedMarketDayPlan:
    return CertifiedMarketDayPlan(
        ExecutionInterval.parse("100ms"), BUILD, "definition",
        ("2026-08-18",), ("AAA", "BBB"), (), (100,), "market-token")


class Client:
    def __init__(self, *, rows=ROWS, coverage=None):
        self.rows = rows
        self.coverage = coverage if coverage is not None else [{
            "identity_attempt_id": ATTEMPT, "universe_date": "2026-08-18",
            "ticker_count": 2, "content_hash": identity_content_hash(ROWS),
        }]
        self.queries = []

    def execute(self, query):
        self.queries.append(query)
        selected = self.coverage if "identity_coverage_v1" in query else self.rows
        return "\n".join(json.dumps(row) for row in selected)


def test_identity_ddl_is_normalized_and_ssd_only():
    statements = ddl()
    assert len(statements) == 2
    assert all("storage_policy='live_market_ssd'" in sql for sql in statements)
    assert all("PARTITION BY toYYYYMM(session_date)" in sql for sql in statements)
    assert all("JSON" not in sql and "Blob" not in sql for sql in statements)


def test_identity_certifies_exact_market_population():
    client = Client()
    plan = certify_identity_plan(_market(), client=client)
    assert plan.market_token == "market-token"
    assert plan.conid_for("BBB") == 102
    assert len(client.queries) == 2
    assert all("SELECT" in query and "INSERT" not in query for query in client.queries)


@pytest.mark.parametrize("client", [
    Client(rows=ROWS[:1]),
    Client(rows=[ROWS[0], ROWS[0]]),
    Client(rows=[{**ROWS[0], "ibkr_conid": 0}, ROWS[1]]),
    Client(coverage=[]),
    Client(coverage=[{"identity_attempt_id": ATTEMPT,
                      "universe_date": "2026-08-18", "ticker_count": 2,
                      "content_hash": "0" * 64}]),
])
def test_identity_rejects_missing_duplicate_or_changed_data(client):
    with pytest.raises(RuntimeError):
        certify_identity_plan(_market(), client=client)
