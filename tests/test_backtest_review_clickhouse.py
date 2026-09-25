import json
from datetime import date

import pytest

import src.backend.backtest_market_data as market_data
from src.backend.backtest_journal_clickhouse import (
    prepare_batch, prepare_fence,
)
from src.backend.backtest_review import ClickHouseSavedBacktestReview
from src.backend.replay_run_service import RESTART_CHECKPOINT_SCHEMA_VERSION
from tests.test_backtest_journal_clickhouse import (
    RUN, ATTEMPT, _Client, record, seed_legacy_batch,
    seed_legacy_fence, seed_legacy_run,
)


def test_saved_clickhouse_review_requires_no_sqlite_journal(tmp_path, monkeypatch):
    class Client(_Client):
        closed = False

        def execute(self, sql):
            if sql.startswith("SELECT toString(record_id) AS record_id"):
                rows = self.tables.get("arte.bt_event_v1", [])
                return "\n".join(json.dumps(row) for row in rows)
            return super().execute(sql)

        def close(self):
            self.closed = True

    client = Client()
    batch = prepare_batch(records=[record(1)], attempt_id=ATTEMPT,
                          run_date=date(2026, 8, 18))
    seed_legacy_batch(client, batch)
    state = {
        "schema_version": RESTART_CHECKPOINT_SCHEMA_VERSION,
        "complete": True,
        "identity": {
            "run_id": RUN, "mode": "backtest", "account_ids": ["SIM"],
            "configuration_revision_id": "revision",
            "configuration_content_hash": "a" * 64,
        },
        "broker": {"account_ids": ["SIM"]},
        "controller": {},
    }
    fence = prepare_fence(batches=[batch], checkpoint=state, source_cursor="bucket-1",
                          status="completed")
    seed_legacy_fence(client, fence)
    monkeypatch.setattr(market_data, "readonly_clickhouse_client", lambda: client)

    run_dir = tmp_path / RUN
    run_dir.mkdir()
    manifest = {
        "journal_backend": "arte_clickhouse_v1",
        "journal_path": "",
        "definition": {
            "mode": "backtest", "configuration_revision_id": "revision",
            "configuration_content_hash": "a" * 64,
            "initial_cash": 100_000, "session_date": "2026-08-18",
        },
        "run": {
            "run_id": RUN, "status": "completed", "processed_events": 1,
            "current_time": "2026-08-18T08:05:00+00:00",
            "created_at": "2026-08-18T08:00:00+00:00",
            "updated_at": "2026-08-18T08:06:00+00:00",
        },
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    seed_legacy_run(client, definition=manifest["definition"],
                    configuration_hash="a" * 64,
                    market_plan_token="", v7_plan_token="", code_hash="b" * 64)
    (run_dir / "approved-configuration.json").write_text(json.dumps({
        "revision_id": "revision", "content_hash": "a" * 64,
        "payload": {"strategy": {"strategy_id": "test", "revision": 1}},
    }), encoding="utf-8")
    review = ClickHouseSavedBacktestReview(run_dir)
    try:
        assert review.sequence == 1
        assert review.snapshot()["presentation_sequence"] == 1
        assert not (run_dir / "journal.sqlite3").exists()
    finally:
        review._journal.close()
    assert client.closed
    manifest["run"]["status"] = "stopped"
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    client.closed = False
    with pytest.raises(ValueError, match="matching ClickHouse fence"):
        ClickHouseSavedBacktestReview(run_dir)
    assert client.closed
    manifest["run"]["status"] = "completed"
    manifest["definition"]["initial_cash"] = 1
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="differs from its ClickHouse run identity"):
        ClickHouseSavedBacktestReview(run_dir)
