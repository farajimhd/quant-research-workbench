"""Causal, pinned and scalar-only Strategy 1 entry context tests."""
from __future__ import annotations

from datetime import date, datetime
import json

import pytest

from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit,
)
from src.backend import strategy_one_entry_context as subject


SESSION = date(2026, 8, 18)


def _plan() -> CertifiedMarketDayPlan:
    return CertifiedMarketDayPlan(
        ExecutionInterval.fixed(100), "build", "f" * 64,
        (SESSION.isoformat(),), ("WFF",),
        (MarketDayUnit("build", SESSION.isoformat(), "WFF", "bars",
                       "11111111-1111-4111-8111-111111111111", "a" * 64,
                       100, "b" * 64),), (100, 1000), "c" * 64)


def test_entry_window_excludes_forming_second_and_is_session_bounded():
    # An entry inside 06:01:09 sees the second ending at 06:01:09,
    # never the 06:01:09-10 bar. The same rule holds at exact boundaries.
    for stamp in ("2026-08-18T06:01:09-04:00",
                  "2026-08-18T06:01:09.500-04:00"):
        lower, upper, second = subject.completed_second_window(
            SESSION, datetime.fromisoformat(stamp))
        assert second == 7269
        assert upper == 21668
        assert lower == 21609
    assert subject.completed_second_window(
        SESSION, datetime.fromisoformat("2026-08-18T04:00:00-04:00")) == (
            14400, 14399, 0)
    with pytest.raises(ValueError, match="timezone-aware"):
        subject.completed_second_window(SESSION, datetime(2026, 8, 18, 6))


def test_volume_uses_exact_pinned_arte_attempt_and_completed_window():
    class Reader:
        def execute(self, query):
            assert "FROM arte.bars_v1" in query
            assert "attempt_id=toUUID('11111111-1111-4111-8111-111111111111')" in query
            assert "resolution_ms=1000" in query
            assert "bucket_index<=21668" in query
            assert "sumIf(volume,bucket_index>=21609)" in query
            return json.dumps({"session_volume": 500, "last_minute_volume": 80,
                               "last_minute_trade_count": 12}) + "\n"
    assert subject.pinned_entry_volume(
        Reader(), _plan(), session=SESSION, ticker="WFF",
        entry_at=datetime.fromisoformat("2026-08-18T06:01:09.5-04:00")) == {
            "session_volume": 500.0, "last_minute_volume": 80.0,
            "last_minute_trade_count": 12}


def test_reference_requires_matching_asof_conid():
    class Reader:
        def execute(self, query):
            assert "inserted_at<=parseDateTime64BestEffort" in query
            if "feature_tradable_universe_v1" in query:
                assert "ibkr_conid='872439877'" in query
                return json.dumps({"symbol_id": "identity", "ibkr_conid": "872439877",
                                   "universe_date": "2026-08-18"}) + "\n"
            assert "symbol_id='identity'" in query
            if "free_float IS NOT NULL" in query:
                return json.dumps({"effective_date": "2026-04-29",
                                   "source_system": "massive", "source_content_sha256": "a" * 64,
                                   "value": 1597556}) + "\n"
            return json.dumps({"effective_date": "2026-08-18",
                               "source_system": "massive", "source_content_sha256": "b" * 64,
                               "value": 3440664}) + "\n"
    value = subject.asof_reference(
        Reader(), session=SESSION, ticker="WFF", conid=872439877,
        entry_at=datetime.fromisoformat("2026-08-18T09:00:00-04:00"))
    assert value["float_value"] == 1597556
    assert value["shares_value"] == 3440664
    assert value["float_evidence_hash"] == "a" * 64
    assert "Tuple(" not in subject.CREATE_TABLE
    assert " JSON" not in subject.CREATE_TABLE
    assert "storage_policy='live_market_ssd'" in subject.CREATE_TABLE


def test_saved_entry_context_joins_only_verified_episode_and_market_plan():
    episode = {"episode_id": "22222222-2222-4222-8222-222222222222",
               "opened_at": "2026-08-18T10:00:00-04:00",
               "instrument": {"symbol": "WFF", "conid": 872439877}}
    page = {"run_id": "33333333-3333-4333-8333-333333333333",
            "report": {"episodes": [episode]}}
    row = {"episode_id": episode["episode_id"], "ticker": "WFF",
           "conid": 872439877, "entry_at_utc": "2026-08-18 14:00:00.000000",
           "market_plan_token": "f" * 64, "calculation_version": 1,
           "shares_outstanding": 3440664, "entry_rvol": 1.5,
           "float_shares": 1597556, "last_minute_trade_count": 15,
           "last_minute_volume": 1000}

    class Reader:
        def execute(self, query):
            if "system.tables" in query:
                return "1\n"
            assert "FINAL" in query and "run_id=toUUID" in query
            return json.dumps(row) + "\n"

    result = subject.attach_saved_entry_context(
        Reader(), page, market_plan_token="f" * 64)
    assert result["report"]["episodes"][0]["entry_rvol"] == 1.5
    assert result["report"]["episodes"][0]["entry_last_minute_trade_count"] == 15
    row["market_plan_token"] = "e" * 64
    with pytest.raises(RuntimeError, match="differs"):
        subject.attach_saved_entry_context(
            Reader(), page, market_plan_token="f" * 64)


def test_saved_entry_context_absence_does_not_invent_values():
    class Reader:
        def execute(self, query):
            assert "system.tables" in query
            return "0\n"

    page = {"run_id": "33333333-3333-4333-8333-333333333333",
            "report": {"episodes": []}}
    assert subject.attach_saved_entry_context(
        Reader(), page, market_plan_token="f" * 64) is page


def test_entry_timestamp_uses_clickhouse_datetime64_wire_form(monkeypatch):
    monkeypatch.setattr(subject, "pinned_entry_volume", lambda *_a, **_k: {
        "session_volume": 10, "last_minute_volume": 5,
        "last_minute_trade_count": 2})
    monkeypatch.setattr(subject, "asof_reference", lambda *_a, **_k: {
        "symbol_id": "identity", "float_date": None,
        "float_source": "unavailable", "float_evidence_hash": "",
        "float_value": None, "shares_date": None,
        "shares_source": "unavailable", "shares_evidence_hash": "",
        "shares_value": None})
    monkeypatch.setattr(subject, "certified_entry_rvol", lambda *_a, **_k: (None, None))
    rows = subject.build_entry_context_rows(
        run_id="33333333-3333-4333-8333-333333333333",
        report={"episodes": [{
            "episode_id": "22222222-2222-4222-8222-222222222222",
            "instrument": {"symbol": "WFF", "conid": 872439877},
            "opened_at": "2026-08-18T10:00:00.123456-04:00"}]},
        session=SESSION, plan=_plan(), market_client=object(),
        reference_client=object(), baseline_provider=lambda *_a: {
            "content_hash": "a" * 64, "source_revision": {"token": "source"}})
    assert rows[0]["entry_at_utc"] == "2026-08-18 14:00:00.123456"
    assert "+00:00" not in json.dumps(rows[0])
