"""The workstation smoke probe reports causal V7 reads separately."""

import pytest

from scripts.clickhouse.smoke_strategy_one_backtest import (
    _SqlCallProfile, _audit_causal_journal, _profile_sql_calls,
)


def test_sql_profile_separates_v7_completed_seconds_from_other_market_reads():
    category = _SqlCallProfile.category
    assert category("SELECT ticker FROM arte.bars_v1 WHERE build_id='b' "
                    "AND resolution_ms=1000 AND bucket_index>=1") == (
                        "v7_completed_second_read")
    assert category("SELECT ticker FROM arte.bars_v1 WHERE resolution_ms=100") == (
        "market_or_control_read")
    assert category("INSERT INTO arte.trading_event_v1 (run_id) VALUES ('r')") == (
        "journal_insert")


def test_preflight_source_profile_uses_bounded_table_labels_not_sql(capsys):
    source = _SqlCallProfile.source_category
    assert source("SELECT x FROM arte.bars_v1 WHERE token='private'") == "arte.bars_v1"
    assert source("SELECT x FROM arte.bars_v1 JOIN system.parts USING x") == (
        "multi_source_select")
    profile = _SqlCallProfile(by_source=True)
    profile.record("SELECT x FROM arte.bars_v1 WHERE token='private'", 1.25)
    profile.print_summary(limit=12)
    output = capsys.readouterr().out
    assert "arte.bars_v1: calls=1 client_s=1.250" in output
    assert "private" not in output


def test_sql_profile_times_streaming_v7_iterator_without_retaining_sql(
    monkeypatch, capsys,
):
    from research.mlops.clickhouse import ClickHouseHttpClient

    def stream(_client, _sql, *args, **kwargs):
        return iter(({"ticker": "TEST"},))

    monkeypatch.setattr(ClickHouseHttpClient, "iter_json_each_row", stream)
    profile = _SqlCallProfile()
    client = object.__new__(ClickHouseHttpClient)
    with _profile_sql_calls(profile):
        rows = client.iter_json_each_row(
            "SELECT ticker FROM arte.bars_v1 WHERE build_id='b' AND resolution_ms=1000 "
            "AND bucket_index>=1")
        assert list(rows) == [{"ticker": "TEST"}]
    assert ClickHouseHttpClient.iter_json_each_row is stream
    profile.print_summary()
    assert "v7_completed_second_stream: calls=1 iterator_s=" in capsys.readouterr().out


@pytest.mark.parametrize("backdate", [False, True])
def test_smoke_audits_every_saved_journal_page(monkeypatch, capsys, backdate):
    from src.backend import backtest_v4_saved_review
    from src.trading_runtime import arte_journal_writer

    class Client:
        closed = False

        def execute(self, sql):
            assert "FROM arte.trading_account_risk_reason_v1" in sql
            return ""

        def close(self):
            self.closed = True

    client = Client()
    monkeypatch.setattr(arte_journal_writer,
                        "backtest_v4_operator_client_from_env", lambda: client)

    def row(sequence, family, at, detail):
        category, entity_type = {
            "trading_strategy_intent_v1": ("strategy", "strategy_intent"),
            "trading_portfolio_decision_v1": ("portfolio_management", "portfolio_decision"),
        }[family]
        return {"event": {"sequence": sequence, "event_time": at,
                          "category": category, "entity_type": entity_type},
                "detail_family": family, "detail": detail}

    source = "2026-08-19 08:00:42.100000000"
    action = ("2026-08-19 08:00:42.064247000" if backdate else source)
    pages = (
        {"events": (row(1, "trading_strategy_intent_v1", source,
                        {"intent_id": "intent-1"}),),
         "next_sequence": 1, "complete": False},
        {"events": (row(2, "trading_portfolio_decision_v1", action,
                        {"request_id": "intent-1"}),),
         "next_sequence": 2, "complete": True},
    )

    def load(_client, _run_id, *, after_sequence, limit):
        assert _client is client and limit == 1000
        page = pages[after_sequence]
        return {**page, "status": "completed", "market_cursor_verified": True,
                "limitations": [], "verified_sequence": 2}

    monkeypatch.setattr(backtest_v4_saved_review,
                        "load_v4_terminal_review_page", load)
    if backdate:
        with pytest.raises(RuntimeError, match="precedes its intent"):
            _audit_causal_journal("run-1")
    else:
        _audit_causal_journal("run-1")
        assert "events=2 intents=1 linked_actions=1 backdated=0" in capsys.readouterr().out
    assert client.closed
