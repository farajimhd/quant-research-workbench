from datetime import datetime
from types import SimpleNamespace

from research.level_book.v7 import campaign as c
from research.level_book.v7 import direct_publisher as direct


def test_v2_ddl_is_separate_typed_storage():
    sql = "\n".join(direct.ddl())
    assert "CREATE TABLE IF NOT EXISTS arte.structural_levels_v7_v2" in sql
    assert "CREATE TABLE IF NOT EXISTS arte.structural_level_observations_v7_v2" in sql
    assert "CREATE TABLE IF NOT EXISTS arte.structural_level_coverage_v7_v2" in sql
    assert "reporting_revision LowCardinality(String)" in sql
    assert "checkpoint_json" not in sql
    assert "storage_policy = 'live_market_ssd'" in sql


def test_direct_ticker_publishes_rows_before_coverage_without_book_files(tmp_path, monkeypatch):
    day = "2026-08-21"
    start = int(datetime.fromisoformat(day + "T04:00:00-04:00").timestamp())
    prices = [10, 10.04, 10.08, 10.12, 10.08, 10.04, 10] * 5
    bars = [dict(t=start+i+1, open=p, high=p, low=p, close=p, volume=100,
                 trades=1, last_count=1, extrema_count=1) for i, p in enumerate(prices)]
    metadata = dict(source_date=day, ticker="TEST", event_count=35, next_ordinal=35,
                    last_ordinal=34, first_sip_timestamp_us=start*1_000_000,
                    last_sip_timestamp_us=(start+36)*1_000_000)
    coverage = dict(ticker="TEST", days=1, events=35, first=day, last=day, signature="fixture")
    reporting=[dict(source_date=day,status='complete')]
    plan = dict(start=day, end=day, output_contract=direct.VERSION,
                reporting_revision=direct.REPORTING_REVISION, rules=[],
                reporting_coverage_hash=c.digest(reporting),
                rows=[dict(ticker="TEST", status="queued", coverage=coverage)])
    (tmp_path / "plan.json").write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(c, "checked_plan", lambda _: plan)
    def query(sql, threads=1):
        if sql == c.RULE_SQL:
            return []
        if "GROUP BY ticker ORDER BY ticker" in sql:
            return [coverage]
        if "historical_trade_reporting_coverage_v1" in sql:
            return reporting
        if "market_stock_split_v1" in sql:
            return []
        if "GROUP BY t ORDER BY t" in sql:
            assert "bitAnd(event_meta,128)=0" in sql
            return bars
        if "events_ordinal_continuity" in sql:
            return [metadata]
        raise AssertionError(sql)
    monkeypatch.setattr(c, "query", query)
    published = []
    monkeypatch.setattr(direct, "insert", lambda _, table, rows, *a, **kw:
                        published.append((table, list(rows))))
    monkeypatch.setattr(direct, "_client", lambda **_: SimpleNamespace(close=lambda: None))
    result = direct.build_ticker(tmp_path, "TEST")
    assert result["state"] == "completed" and result["sessions"] == 1
    assert [table for table, _ in published] == [direct.LEVELS, direct.OBSERVATIONS, direct.COVERAGE]
    assert published[-1][1][0]["reporting_revision"] == direct.REPORTING_REVISION
    assert sorted(path.name for path in tmp_path.iterdir()) == ["plan.json"]


def test_direct_ticker_publishes_initial_and_carried_empty_days(tmp_path, monkeypatch):
    days = ["2026-08-20", "2026-08-21", "2026-08-24"]
    metadata = [dict(source_date=day, ticker="TEST", event_count=1,
                     next_ordinal=i+1, last_ordinal=i,
                     first_sip_timestamp_us=1, last_sip_timestamp_us=2)
                for i, day in enumerate(days)]
    coverage = dict(ticker="TEST", days=3, events=3, first=days[0], last=days[-1], signature="fixture")
    reporting = [dict(source_date=day, status="complete") for day in days]
    plan = dict(start=days[0], end=days[-1], output_contract=direct.VERSION,
                reporting_revision=direct.REPORTING_REVISION, rules=[],
                reporting_coverage_hash=c.digest(reporting),
                rows=[dict(ticker="TEST", status="queued", coverage=coverage)])
    (tmp_path / "plan.json").write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(c, "checked_plan", lambda _: plan)
    def query(sql, threads=1):
        if sql == c.RULE_SQL:
            return []
        if "GROUP BY ticker ORDER BY ticker" in sql:
            return [coverage]
        if "historical_trade_reporting_coverage_v1" in sql:
            return reporting
        if "market_stock_split_v1" in sql:
            return []
        if "GROUP BY t ORDER BY t" in sql:
            if "ordinal>=1 AND ordinal<2" not in sql:
                return []
            start = int(datetime.fromisoformat(days[1] + "T04:05:00-04:00").timestamp())
            return [dict(t=start+1, open=10, high=10, low=10, close=10,
                         volume=100, trades=1, last_count=1, extrema_count=1)]
        if "events_ordinal_continuity" in sql:
            return [item for item in metadata if "source_date='" not in sql or item["source_date"] in sql]
        raise AssertionError(sql)
    monkeypatch.setattr(c, "query", query)
    published = []
    monkeypatch.setattr(direct, "insert", lambda _, table, rows, *a, **kw:
                        published.append((table, list(rows))))
    monkeypatch.setattr(direct, "_client", lambda **_: SimpleNamespace(close=lambda: None))
    result = direct.build_ticker(tmp_path, "TEST")
    assert result["fitted"] == 1 and result["empty"] == 2
    states = [row["state"] for row in published[-1][1]]
    assert states == ["empty", "complete", "empty"]
    assert published[-1][1][0]["level_count"] == 0
    assert published[-1][1][-1]["source_checkpoint_hash"] == published[-1][1][1]["source_checkpoint_hash"]
