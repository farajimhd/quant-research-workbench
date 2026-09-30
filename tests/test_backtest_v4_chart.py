"""A saved Strategy 1 chart may read only its reconstructed pinned market plan."""
from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from src.backend import backtest_v4_chart as subject
from src.backend.backtest_market_data import CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit
from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
from src.trading_runtime.strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER


RUN_ID = "983b4fcb-7eda-4467-83df-2c689f93ac45"
TOKEN = "a" * 64
HASH = "b" * 64
REVISION_ID = "strategy-one-1:11111111-1111-4111-8111-111111111111"


def _fixtures(monkeypatch, *, token: str = TOKEN,
              status: str = "completed"):
    context = {
        "run_id": RUN_ID, "mode": "backtest", "strategy_id": STRATEGY_ID,
        "strategy_revision": STRATEGY_NUMBER, "evaluation_interval_ms": 100,
        "session_date": "2026-08-18", "configuration_hash": HASH,
        "market_plan_token": TOKEN,
    }
    monkeypatch.setattr(subject, "load_v4_terminal_review_page", lambda *a, **kw: {
        "run": context, "status": status, "market_cursor_verified": True,
        "market_cursor": {"session_date": "2026-08-18", "boundary_ms": 19_500_000},
    })
    monkeypatch.setattr(subject, "load_backtest_definition", lambda *a, **kw: {
        "definition": {"configuration_revision_id": REVISION_ID}, "tickers": (),
    })
    release = CertifiedStrategyOneConfiguration(
        "11111111-1111-4111-8111-111111111111", HASH, "c" * 64,
        "candidate", "d" * 64, "e" * 64,
        {"strategy": {"strategy_id": STRATEGY_ID,
                      "strategy_number": STRATEGY_NUMBER,
                      "revision": STRATEGY_NUMBER, "execution_interval": "100ms"},
         "run_plan": {"run_plan_id": "strategy-r1"}},
    )
    monkeypatch.setattr(subject, "certify_strategy_one_configuration", lambda client: release)
    return CertifiedMarketDayPlan(
        ExecutionInterval.fixed(100), "build", "f" * 64,
        ("2026-08-18",), ("SUGP",),
        (MarketDayUnit("build", "2026-08-18", "SUGP", "broker_100ms",
                       "11111111-1111-4111-8111-111111111111", "f" * 64, 1, "e" * 64),),
        (100, 1000, 30_000), token)


def test_cold_chart_uses_only_exact_reproduced_plan_and_completed_cursor(monkeypatch):
    plan = _fixtures(monkeypatch)
    called = []

    def load_plan(**kwargs):
        called.append(("plan", kwargs))
        return plan

    def read_chart(**kwargs):
        called.append(("chart", kwargs))
        return {"bars": [{"close": 1.0}], "indicators": [], "has_more": False,
                "next_before": "", "indicator_provenance": {"unavailable_columns": []}}

    monkeypatch.setattr(subject, "chart_page", read_chart)
    class ReadClient:
        def execute(self, query):
            assert "arte.liquidity_100ms_v1" in query
            assert "11111111-1111-4111-8111-111111111111" in query
            stamp = int(subject.market_day_boundary(date(2026, 8, 18), 18_000_000).timestamp() * 1_000_000) - 100_000
            return f'{{"bucket_index":194999,"quote_timestamp_us":{stamp},"bid_int":10000,"ask_int":10100,"bid_size":10,"ask_size":20}}\n'

    market_client = ReadClient()
    page = subject.cold_v4_chart_page(
        object(), market_client, run_id=RUN_ID, ticker="sugp", timeframe="1s",
        before_boundary_ms=18_000_000, indicator_columns=("macd_line",),
        plan_loader=load_plan)
    assert page["market_plan_token"] == TOKEN
    assert page["through_boundary_ms"] == 18_000_000
    assert page["quote"]["bid"] == 1.0
    assert page["quote"]["fresh"] is True
    assert called[0][1]["sessions"] == (date(2026, 8, 18),)
    assert called[0][1]["tickers"] == ()
    assert called[1][1]["pinned_plan"] is plan
    assert called[1][1]["read_client"] is market_client
    assert called[1][1]["page_end"] == subject.market_day_boundary(
        date(2026, 8, 18), 18_000_000)


def test_failed_terminal_chart_uses_verified_cursor_but_stopped_cannot(monkeypatch):
    plan = _fixtures(monkeypatch, status="failed")
    session, context, cursor, selected = subject.certified_saved_run_plan(
        object(), object(), run_id=RUN_ID, plan_loader=lambda **_kwargs: plan)
    assert session == date(2026, 8, 18)
    assert context["run_id"] == RUN_ID
    assert cursor["boundary_ms"] == 19_500_000
    assert selected is plan

    _fixtures(monkeypatch, status="stopped")
    with pytest.raises(ValueError, match="terminal verified cursor"):
        subject.certified_saved_run_plan(
            object(), object(), run_id=RUN_ID,
            plan_loader=lambda **_kwargs: pytest.fail(
                "stopped run reached market read"))


def test_cold_chart_rejects_different_certificate_without_market_read(monkeypatch):
    plan = _fixtures(monkeypatch, token="f" * 64)
    monkeypatch.setattr(subject, "chart_page", lambda **kwargs: pytest.fail(
        "mismatched market certificate reached ARTE chart read"))
    with pytest.raises(RuntimeError, match="cannot reproduce"):
        subject.cold_v4_chart_page(
            object(), object(), run_id=RUN_ID, ticker="SUGP",
            timeframe="1s", plan_loader=lambda **kwargs: plan)


def test_cold_chart_rejects_invalid_page_before_any_authority_read(monkeypatch):
    monkeypatch.setattr(subject, "load_v4_terminal_review_page", lambda *a, **kw:
                        pytest.fail("invalid request reached journal"))
    with pytest.raises(ValueError, match="invalid"):
        subject.cold_v4_chart_page(
            object(), object(), run_id=RUN_ID, ticker="SUGP",
            timeframe="1s", before_boundary_ms=150)
    with pytest.raises(ValueError, match="invalid"):
        subject.cold_v4_chart_page(
            object(), object(), run_id=RUN_ID, ticker="SUGP",
            timeframe="1mo", before_boundary_ms=18_000_000)


def test_pinned_quote_rejects_future_timestamp(monkeypatch):
    plan = _fixtures(monkeypatch)
    class FutureClient:
        def execute(self, query):
            stamp = int(subject.market_day_boundary(date(2026, 8, 18), 18_000_000).timestamp() * 1_000_000) + 1
            return f'{{"quote_timestamp_us":{stamp},"bid_int":10000,"ask_int":10100,"bid_size":10,"ask_size":20}}\n'
    with pytest.raises(RuntimeError, match="newer than"):
        subject._pinned_quote(FutureClient(), plan, session=date(2026, 8, 18),
                              ticker="SUGP", boundary_ms=18_000_000)


def test_saved_daily_context_uses_arte_context_reader_not_intraday_reader(monkeypatch):
    plan = _fixtures(monkeypatch)
    monkeypatch.setattr(subject, "chart_page", lambda **_kwargs: pytest.fail(
        "daily context reached intraday chart reader"))
    seen = []
    def context(*args, **kwargs):
        seen.append(kwargs)
        return {"bars": [], "indicators": [], "has_more": False,
                "next_before": "", "indicator_provenance": {
                    "unavailable_columns": []}}
    monkeypatch.setattr(subject, "context_chart_page", context)
    class ReadClient:
        def execute(self, query):
            assert "arte.liquidity_100ms_v1" in query
            return ""
    result = subject.cold_v4_chart_page(
        object(), ReadClient(), run_id=RUN_ID, ticker="SUGP",
        timeframe="1d", plan_loader=lambda **_kwargs: plan)
    assert result["timeframe"] == "1d"
    assert seen[0]["run_plan"] is plan
    assert seen[0]["boundary_ms"] == 19_500_000


def test_overlay_reads_only_pinned_indicators_without_reloading_bars(monkeypatch):
    plan = _fixtures(monkeypatch)
    technical = MarketDayUnit(
        "build", "2026-08-18", "SUGP", "technical",
        "22222222-2222-4222-8222-222222222222", "f" * 64, 1, "e" * 64)
    plan = replace(plan, units=(*plan.units, technical))
    monkeypatch.setattr(subject, "chart_page", lambda **_kwargs: pytest.fail(
        "overlay attempted to reload bars"))
    class ReadClient:
        def execute(self, query):
            assert "FROM arte.indicators_v1" in query
            assert "FROM arte.bars_v1" not in query
            assert technical.attempt_id in query
            return '{"bucket_index":14400,"ema_7":1.23}\n'
    result = subject.cold_v4_chart_overlays(
        object(), ReadClient(), run_id=RUN_ID, ticker="SUGP",
        timeframe="1s", bucket_indices=(14400,),
        indicator_columns=("ema_7",), plan_loader=lambda **_kwargs: plan)
    assert result["indicators"][0]["ema_7"] == 1.23
    assert result["indicators"][0]["bar_start"].endswith("04:00:00-04:00")


def test_overlay_rejects_unverified_buckets_before_any_market_read(monkeypatch):
    plan = _fixtures(monkeypatch)
    class NoRead:
        def execute(self, _query):
            pytest.fail("invalid overlay reached market read")
    with pytest.raises(ValueError, match="cursor"):
        subject.cold_v4_chart_overlays(
            object(), NoRead(), run_id=RUN_ID, ticker="SUGP",
            timeframe="1s", bucket_indices=(164000,),
            indicator_columns=("ema_7",), plan_loader=lambda **_kwargs: plan)


def test_structural_chart_uses_completed_boundaries_and_compact_segments(monkeypatch):
    from src.backend import backtest_strategy_one_candidate_store as candidates
    from src.backend import backtest_strategy_one_preparation as preparation
    from src.backend import backtest_strategy_one_v7_interval_store as interval_store
    from src.backend import structural_v7_seed
    from src.trading_runtime.strategy_one_v7_intervals import V7LevelInterval

    plan = _fixtures(monkeypatch)
    seed_token = "7" * 64
    monkeypatch.setattr(subject, "load_backtest_definition", lambda *a, **kw: {
        "definition": {"causal_v7_plan_token": seed_token}})
    monkeypatch.setattr(candidates, "certify_candidate_plan", lambda *a, **kw:
                        type("Candidates", (), {"prepared": object()})())
    monkeypatch.setattr(preparation, "strategy_one_v7_tickers", lambda prepared: ("SUGP",))
    monkeypatch.setattr(subject, "project_market_day_plan", lambda *a, **kw: plan)
    monkeypatch.setattr(structural_v7_seed, "certified_seed_plan", lambda *a, **kw:
                        structural_v7_seed.CertifiedSeedPlan(
                            "build", "8" * 64, ({"ticker": "SUGP"},), seed_token, True))
    clocks = [subject.market_day_boundary(date(2026, 8, 18), step * 1000)
              for step in range(3)]
    bars = [{"bar_start": clocks[index].isoformat(),
             "bar_end": clocks[index + 1].isoformat(), "close": 6.0}
            for index in range(2)]
    intervals = (
        V7LevelInterval("first", 0, 1000, 2000, 4.9, 5.1,
                        "support", "", 1, False),
        V7LevelInterval("second", 0, 2000, 57_600_001, 4.9, 5.1,
                        "support", "", 2, False))
    monkeypatch.setattr(interval_store, "certify_v7_interval_plan", lambda *a, **kw:
                        type("Intervals", (), {"intervals": (("SUGP", intervals),)})())
    segments, reason = subject._causal_v7_chart_segments(
        object(), object(), run_id=RUN_ID,
        run_context={"run_id": RUN_ID}, session=date(2026, 8, 18),
        ticker="SUGP", plan=plan, bars=bars)
    assert reason == ""
    assert [(row["level_id"], row["start"], row["end"]) for row in segments] == [
        ("first", clocks[1].timestamp(), clocks[2].timestamp()),
        ("second", clocks[2].timestamp(), clocks[2].timestamp() + 1),
    ]
    assert [row["historical"] for row in segments] == [False, False]


def test_pinned_vwap_carries_only_persisted_prior_value(monkeypatch):
    plan = _fixtures(monkeypatch)
    origin = subject.market_day_boundary(date(2026, 8, 18), 0)
    from datetime import timedelta
    bars = [{"bar_start": (origin + timedelta(seconds=index)).isoformat(),
             "bar_end": (origin + timedelta(seconds=index + 1)).isoformat()}
            for index in range(3)]
    class ReadClient:
        def execute(self, query):
            assert "arte.liquidity_100ms_v1" in query
            assert "attempt_id=toUUID(" in query
            if "ORDER BY bucket_index DESC LIMIT 1" in query:
                return '{"bucket_index":143999,"execution_vwap":6.1}\n'
            assert "argMax(execution_vwap,bucket_index)" in query
            return '{"bar_bucket":14401,"vwap_value":6.2}\n'
    result = subject._pinned_execution_vwap(
        ReadClient(), plan, session=date(2026, 8, 18),
        ticker="SUGP", timeframe="1s", bars=bars)
    assert [row["execution_vwap"] for row in result] == [6.1, 6.2, 6.2]
