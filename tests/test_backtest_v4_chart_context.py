from datetime import date
import json

import pytest

from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit,
)
from src.backend.backtest_v4_chart_context import context_chart_page


DAY = "2026-08-18"
PREVIOUS = "2026-08-17"


def _plan(days):
    return CertifiedMarketDayPlan(
        ExecutionInterval.fixed(100), "build", "a" * 64, tuple(days),
        ("WFF",), tuple(MarketDayUnit(
            "build", day, "WFF", "bars",
            "11111111-1111-4111-8111-111111111111", "b" * 64, 1,
            "c" * 64) for day in days), (100, 1000, 30_000), "d" * 64)


class Client:
    def __init__(self):
        self.queries = []
    def execute(self, query):
        self.queries.append(query)
        if "market_day_planned_scope_v1" in query:
            return "\n".join(json.dumps({"session_date": day})
                             for day in (DAY, PREVIOUS))
        assert "FROM arte.bars_v1" in query
        return "\n".join(json.dumps(row) for row in (
            {"session_date": PREVIOUS, "open_int": 10000,
             "high_int": 12000, "low_int": 9000, "close_int": 11000,
             "volume": 100, "source_rows": 2},
            {"session_date": DAY, "open_int": 11000,
             "high_int": 13000, "low_int": 10000, "close_int": 12000,
             "volume": 200, "source_rows": 3}))


def test_daily_and_monthly_context_are_causal_and_certified():
    client = Client()
    run = _plan((DAY,))
    def load_plan(**kwargs):
        assert kwargs["sessions"] == (PREVIOUS, DAY)
        assert kwargs["tickers"] == ("WFF",)
        return _plan((PREVIOUS, DAY))
    daily = context_chart_page(
        client, session=date.fromisoformat(DAY), ticker="WFF",
        boundary_ms=19_500_000, timeframe="1d", run_plan=run,
        configuration={}, plan_loader=load_plan)
    assert len(daily["bars"]) == 2
    assert daily["bars"][0]["is_closed"] is True
    assert daily["bars"][1]["is_closed"] is False
    assert daily["history_limited"] is True
    assert "bucket_index<" in client.queries[1]
    monthly = context_chart_page(
        client, session=date.fromisoformat(DAY), ticker="WFF",
        boundary_ms=19_500_000, timeframe="1mo", run_plan=run,
        configuration={}, plan_loader=load_plan)
    assert monthly["bars"][0]["open"] == 1.0
    assert monthly["bars"][0]["high"] == 1.3
    assert monthly["bars"][0]["volume"] == 300
    assert monthly["bars"][0]["is_closed"] is False


def test_context_rejects_wrong_build_before_bar_query():
    client = Client()
    bad = _plan((PREVIOUS, DAY))
    object.__setattr__(bad, "build_id", "other")
    with pytest.raises(RuntimeError, match="certificate differs"):
        context_chart_page(
            client, session=date.fromisoformat(DAY), ticker="WFF",
            boundary_ms=19_500_000, timeframe="1d", run_plan=_plan((DAY,)),
            configuration={}, plan_loader=lambda **_kwargs: bad)
    assert len(client.queries) == 1
