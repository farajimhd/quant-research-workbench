from __future__ import annotations

import json

import pytest

from pipelines.strategy_one.identity_publication import dated_source_rows
from src.backend.backtest_market_data import CertifiedMarketDayPlan, ExecutionInterval


def _market():
    return CertifiedMarketDayPlan(
        ExecutionInterval.parse("100ms"), "a" * 64, "definition",
        ("2026-08-18",), ("AAA", "BBB"), (), (100,), "token")


def _row(ticker, *, conid="101", inserted="2026-08-18 07:56:11.600"):
    return {"ticker": ticker, "symbol_id": f"s-{ticker}",
            "listing_id": f"l-{ticker}", "security_id": f"x-{ticker}",
            "ibkr_conid": conid, "source_run_id": "u1",
            "source_inserted_at": inserted}


class Source:
    def __init__(self, rows):
        self.rows = rows
    def execute(self, query):
        assert query.startswith("SELECT ")
        assert "q_live.feature_tradable_universe_v1 FINAL" in query
        return "\n".join(json.dumps(row) for row in self.rows)


def test_publisher_requires_exact_dated_population():
    rows = dated_source_rows(Source([_row("AAA"), _row("BBB"), _row("ZZZ")]),
                             _market())
    assert [row["ticker"] for row in rows] == ["AAA", "BBB"]
    assert [row["ibkr_conid"] for row in rows] == [101, 101]


@pytest.mark.parametrize("rows", [
    [_row("AAA")],
    [_row("AAA"), _row("AAA"), _row("BBB")],
    [_row("AAA", conid=""), _row("BBB")],
    [_row("AAA", inserted="2026-08-18 08:00:00.001"), _row("BBB")],
])
def test_publisher_fails_closed_on_gap_duplicate_invalid_or_late(rows):
    with pytest.raises(RuntimeError):
        dated_source_rows(Source(rows), _market())
