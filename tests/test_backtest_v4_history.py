"""Normalized Strategy 1 history is bounded and never asserts saved review."""
from __future__ import annotations

import pytest

from src.backend import backtest_v4_history as history


RUN = "00000000-0000-0000-0000-000000000001"
BATCH = "00000000-0000-0000-0000-000000000002"


def _context():
    return {
        "run_id": RUN, "run_month": "2026-09-01",
        "session_date": "2026-08-18",
        "started_at": "2026-09-26 01:02:03.123456",
        "configuration_hash": "a" * 64,
        "strategy_id": "early-squeeze-strategy", "strategy_revision": 1,
    }


def _head():
    return {
        "run_id": RUN, "last_sequence": 7782, "status": "completed",
        "committed_at": "2026-09-26 01:06:00.000000", "batch_id": BATCH,
    }


def test_history_lists_normalized_record_without_claiming_review(monkeypatch):
    queries = []

    def rows(_client, sql):
        queries.append(sql)
        return [_context()] if "trading_run_v1 AS r" in sql else [_head()]

    monkeypatch.setattr(history, "_rows", rows)
    result = history.load_strategy_one_v4_history(object())
    assert len(queries) == 2
    assert all("FORMAT JSONEachRow" in query for query in queries)
    assert "LIMIT 33" in queries[0]
    assert "LIMIT 2 BY run_id" in queries[1]
    assert "INSERT" not in " ".join(queries)
    assert result == [{
        "schema_version": 1, "run_id": RUN, "status": "completed",
        "mode": "backtest", "execution_mode": "100ms",
        "session_date": "2026-08-18",
        "created_at": "2026-09-26T01:02:03.123456+00:00",
        "updated_at": "2026-09-26T01:06:00+00:00",
        "current_time": None,
        "configuration_content_hash": "a" * 64,
        "configuration_label": "Strategy 1",
        "strategy_id": "early-squeeze-strategy",
        "strategy_name": "Strategy 1", "strategy_revision": 1,
        "configuration_revision": 1, "resident": False,
        "journal_backend": "arte_typed_journal_v4",
        "journal_verification": "inventory_only", "journal_sequence": 7782,
        "review_available": False,
        "v4_review_available": True,
        "checkpoint": {"resume_supported": False},
        "tickers": [], "processed_events": None,
    }]


def test_history_rejects_ambiguous_commit_heads(monkeypatch):
    monkeypatch.setattr(history, "_rows", lambda _client, sql:
                        [_context()] if "trading_run_v1 AS r" in sql
                        else [_head(), {**_head(), "batch_id": RUN}])
    with pytest.raises(RuntimeError, match="ambiguous commit heads"):
        history.load_strategy_one_v4_history(object())


def test_history_requires_bounded_limit(monkeypatch):
    monkeypatch.setattr(history, "_rows", lambda *_a: pytest.fail("queried"))
    with pytest.raises(ValueError, match="limit"):
        history.load_strategy_one_v4_history(object(), limit=101)


def test_service_lists_v4_without_run_directory_or_sqlite(monkeypatch, tmp_path):
    from src.backend import replay_run_service
    from src.trading_runtime import arte_journal_writer

    recorded = {
        "run_id": RUN, "status": "completed", "mode": "backtest",
        "journal_backend": "arte_typed_journal_v4",
        "created_at": "2026-09-26T01:02:03+00:00", "review_available": False,
    }
    closed = []

    class Client:
        def close(self):
            closed.append(True)

    monkeypatch.setenv("BACKTEST_V4_RUNNER_CLICKHOUSE_USER", "backtest_v4_runner")
    monkeypatch.setattr(arte_journal_writer, "backtest_v4_operator_client_from_env",
                        Client)
    monkeypatch.setattr(history, "load_strategy_one_v4_history",
                        lambda _client: [recorded])
    monkeypatch.setattr(replay_run_service, "_completed_backtest_selection_projection",
                        lambda *_a: pytest.fail("SQLite projection opened"))
    assert replay_run_service.ReplayRunService(runtime_root=tmp_path).list(
        include_durable=True) == [recorded]
    assert closed == [True]
    assert not (tmp_path / RUN).exists()
