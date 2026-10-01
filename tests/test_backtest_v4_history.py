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
        "initial_cash": "100000.0000000000",
    }


def _head():
    return {
        "run_id": RUN, "last_sequence": 7782, "status": "completed",
        "committed_at": "2026-09-26 01:06:00.000000", "batch_id": BATCH,
    }


@pytest.mark.parametrize("number", range(1, 15))
def test_history_preserves_number_identity_without_claiming_audited_review(monkeypatch, number):
    def rows(_client, sql):
        if "trading_run_v1 AS r" in sql:
            import re
            admitted = re.search(r"c.strategy_revision IN \(([^)]+)\)", sql)
            assert admitted and number in {int(value) for value in admitted[1].split(",")}
            return [{**_context(), "strategy_revision": number}]
        return [_head()]
    monkeypatch.setattr(history, "_rows", rows)
    row = history.load_strategy_one_v4_history(object())[0]
    assert row["strategy_revision"] == row["configuration_revision"] == number
    assert row["strategy_name"] == row["configuration_label"] == f"Strategy {number}"
    assert row["journal_verification"] == "inventory_only"


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
    assert "trading_backtest_definition_commit_v1" in queries[0]
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
        "initial_cash": 100000.0,
        "configuration_revision": 1, "resident": False,
        "journal_backend": "arte_typed_journal_v4",
        "journal_verification": "inventory_only", "journal_sequence": 7782,
        "review_available": False,
        "v4_review_available": True,
        "resume_attempt_available": False,
        "checkpoint": {"resume_supported": False},
        "tickers": [], "processed_events": None,
    }]


def test_running_history_offers_only_a_verified_on_click_resume_attempt(monkeypatch):
    monkeypatch.setattr(history, "_rows", lambda _client, sql:
                        [_context()] if "trading_run_v1 AS r" in sql
                        else [{**_head(), "status": "running"}])
    row, = history.load_strategy_one_v4_history(object())
    assert row["status"] == "running"
    assert row["resume_attempt_available"] is True
    assert row["checkpoint"] == {"resume_supported": False}


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


@pytest.mark.parametrize("credential_key,credential_value", [
    ("BACKTEST_V4_RUNNER_CLICKHOUSE_USER", "backtest_v4_runner"),
    ("BACKTEST_V4_RUNNER_CREDENTIAL_FILE", "private-runner.env"),
])
def test_service_lists_v4_without_run_directory_or_sqlite(
    monkeypatch, tmp_path, credential_key, credential_value,
):
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

    monkeypatch.delenv("BACKTEST_V4_RUNNER_CLICKHOUSE_USER", raising=False)
    monkeypatch.delenv("BACKTEST_V4_RUNNER_CREDENTIAL_FILE", raising=False)
    monkeypatch.setenv(credential_key, credential_value)
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


def test_strategy_one_history_never_scans_legacy_run_directories(monkeypatch, tmp_path):
    from src.backend import replay_run_service
    from src.trading_runtime import arte_journal_writer

    recorded = {
        "run_id": RUN, "status": "completed", "mode": "backtest",
        "journal_backend": "arte_typed_journal_v4",
        "created_at": "2026-09-26T01:02:03+00:00",
    }

    class Client:
        def close(self):
            pass

    monkeypatch.delenv("BACKTEST_V4_RUNNER_CLICKHOUSE_USER", raising=False)
    monkeypatch.setenv("BACKTEST_V4_RUNNER_CREDENTIAL_FILE", "private-runner.env")
    monkeypatch.setattr(arte_journal_writer, "backtest_v4_operator_client_from_env",
                        Client)
    monkeypatch.setattr(history, "load_strategy_one_v4_history",
                        lambda _client: [recorded])
    monkeypatch.setattr(replay_run_service, "_durable_run_selection",
                        lambda *_a: pytest.fail("Legacy run directory was read"))
    (tmp_path / "legacy-run").mkdir()
    assert replay_run_service.ReplayRunService(runtime_root=tmp_path).list(
        include_durable=True, strategy_one_only=True) == [recorded]


def test_strategy_one_history_filters_resident_legacy_runs(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from src.backend import replay_run_service

    strategy_one = {
        "mode": "backtest", "strategy_id": "early-squeeze-strategy",
        "strategy_revision": 1, "created_at": "2026-09-28T01:00:00+00:00",
    }
    legacy = {**strategy_one, "strategy_revision": 350}
    monkeypatch.delenv("BACKTEST_V4_RUNNER_CLICKHOUSE_USER", raising=False)
    monkeypatch.delenv("BACKTEST_V4_RUNNER_CREDENTIAL_FILE", raising=False)
    monkeypatch.setattr(replay_run_service, "_replay_run_list_projection",
                        lambda snapshot, **_k: snapshot)
    monkeypatch.setattr(replay_run_service, "_run_selection_projection",
                        lambda row, _revision: row)
    service = replay_run_service.ReplayRunService(runtime_root=tmp_path)
    for run_id, row in ((RUN, strategy_one), ("legacy-run", legacy)):
        service._runs[run_id] = SimpleNamespace(
            run_id=run_id,
            definition=SimpleNamespace(configuration_revision={}),
            stream_snapshot=lambda row=row, run_id=run_id: {**row, "run_id": run_id},
        )
    assert [row["run_id"] for row in service.list(
        include_durable=True, strategy_one_only=True)] == [RUN]


def test_terminal_resident_v4_run_uses_durable_saved_review(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from src.backend import replay_run_service
    from src.trading_runtime import arte_journal_writer

    durable = {
        "run_id": RUN, "status": "completed", "mode": "backtest",
        "strategy_id": "early-squeeze-strategy",
        "configuration_content_hash": "a" * 64,
        "journal_backend": "arte_typed_journal_v4",
        "v4_review_available": True,
        "created_at": "2026-09-26T01:02:03+00:00",
    }
    resident = {**durable, "journal_backend": None,
                "v4_review_available": False, "resident": True}

    class Client:
        def close(self):
            pass

    monkeypatch.setenv("BACKTEST_V4_RUNNER_CLICKHOUSE_USER", "backtest_v4_runner")
    monkeypatch.setattr(arte_journal_writer, "backtest_v4_operator_client_from_env",
                        Client)
    monkeypatch.setattr(history, "load_strategy_one_v4_history",
                        lambda _client: [durable])
    monkeypatch.setattr(replay_run_service, "_replay_run_list_projection",
                        lambda *_a, **_k: resident)
    monkeypatch.setattr(replay_run_service, "_run_selection_projection",
                        lambda row, _revision: row)
    monkeypatch.setattr(replay_run_service, "_completed_backtest_selection_projection",
                        lambda *_a: pytest.fail("SQLite projection opened"))
    service = replay_run_service.ReplayRunService(runtime_root=tmp_path)
    service._runs[RUN] = SimpleNamespace(
        run_id=RUN, definition=SimpleNamespace(configuration_revision=1),
        stream_snapshot=lambda: {},
    )
    assert service.list(include_durable=True) == [durable]
