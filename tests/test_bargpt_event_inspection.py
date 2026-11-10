"""Event charts must remain independent of saved payloads and causal on paging."""
from datetime import date
from types import SimpleNamespace

import pytest

from research.bar_gpt.v5 import event_inspection as events


@pytest.fixture
def source(monkeypatch):
    events.chart.cache_clear()
    rows = [dict(source_date=f"2025-09-{d}", first_ordinal=i*100, next_ordinal=(i+1)*100)
            for i, d in enumerate((24, 25))]
    plan = dict(hash="sealed", authority=dict(rules=[], corporate_actions=dict(rows=[])))
    monkeypatch.setattr(events.inspection, "_stream", lambda key: (plan, dict(ticker="EVAX",year=2025)))
    monkeypatch.setattr(events, "bounds", lambda *args: rows)
    monkeypatch.setattr(events, "predicates", lambda rules: "1 p_ok,1 e_ok,1 v_ok")
    client = SimpleNamespace(default_query_params={})
    monkeypatch.setattr(events.sip, "client", lambda **kwargs: client)
    monkeypatch.setattr(events.sip, "close", lambda client: None)
    return rows, client


def test_only_canonical_events_and_split_before_aggregate(source):
    rows, _ = source
    sql = events.query("EVAX", rows, [], [], "1s", 1, 2, date(2025,9,25), 10)
    assert "market_sip_compact.events_2025" in sql
    assert "sip_timestamp_us>=1 AND sip_timestamp_us<2" in sql
    assert "argMinIf(price_int,tuple(sip_timestamp_us,ordinal),p_ok)" in sql
    assert "FROM adjusted" in sql and "price_int*(" in sql
    assert "LIMIT 11" in sql
    assert "file(" not in sql and ".npy" not in sql


def test_calendar_regular_hours_and_completed_cutoff(source):
    rows, _ = source
    sql = events.query("EVAX", rows, [], [], "1w", 1, 2, date(2025,9,25), 10)
    assert "toMonday(session)" in sql
    assert "local_second>=34200 AND local_second<57600" in sql
    assert "session_open)+43200000000<=2" in sql


def test_month_year_and_dst_pages_preserve_split_basis(source):
    rows, _ = source
    actions = [dict(provider_ticker="EVAX",execution_date="2025-09-01",split_from=4,split_to=1),
               dict(provider_ticker="EVAX",execution_date="2026-01-01",split_from=99,split_to=1)]
    sql = events.query("EVAX",rows,[],actions,"1d",1,2,date(2025,9,25),10)
    assert "session<toDate('2025-09-01'),4,1." in sql
    assert "2026-01-01" not in sql
    assert events._period_start(date(2025,1,7),"1mo") == date(2024,1,1)
    assert events.clock("2025-03-10",4)-events.clock("2025-03-07",4) == 71*3600*1_000_000


def test_backwards_page_fixed_basis_and_resources(source, monkeypatch):
    _, client = source
    end = events.clock("2025-09-25", 16)
    values = [dict(start_us=end-i*1_000_000,end_us=end-(i-1)*1_000_000) for i in (1,2,3)]
    seen = []
    monkeypatch.setattr(events.sip,"rows",lambda client,sql: (seen.append(sql) or values))
    result = events.chart("stream",20250925,"1s",None,2)
    assert [r["start_us"] for r in result["bars"]] == [end-2_000_000,end-1_000_000]
    assert result["has_more"] and result["next_before_us"] == end-2_000_000
    assert result["basis"] == "2025-09-25"
    assert client.default_query_params == dict(max_threads=1,max_execution_time=30)
    monkeypatch.setattr(events.sip,"rows",lambda client,sql: [])
    older = events.chart("stream",20250925,"1s",events.clock("2025-09-25",4),2)
    assert older["basis"] == result["basis"]
    assert older["next_before_us"] is None  # oldest covered day drained


@pytest.mark.parametrize("frame,unit",[("bogus",20250925),("1s",20260925)])
def test_reject_foreign_scope(source,frame,unit):
    with pytest.raises(ValueError):events.chart("stream",unit,frame)


def test_invalid_source_bounds_fail_closed(source,monkeypatch):
    monkeypatch.setattr(events.sip,"rows",lambda *args:[dict(start_us=1,end_us=2)])
    with pytest.raises(ValueError,match="bounds"):
        events.chart("stream",20250925)
