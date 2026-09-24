from datetime import date, datetime, timezone

import pytest

from src.trading_runtime.arte_journal_projection import runtime_lifecycle_batch
from src.trading_runtime.arte_journal_reader import (
    load_typed_event_page, readonly_typed_journal_client,
)
from src.trading_runtime.arte_journal_writer import (
    load_committed_prefix, publish_typed_batch,
)
from src.trading_runtime.journal_contract import JournalRecord
from tests.test_arte_journal_writer import MemoryClient


RUN = "live:DU1"
AT = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)


def test_typed_review_connection_is_journal_only_and_readonly(monkeypatch) -> None:
    monkeypatch.setenv("TRADING_JOURNAL_CLICKHOUSE_URL", "http://localhost:18123")
    monkeypatch.setenv("TRADING_JOURNAL_CLICKHOUSE_USER", "journal-only")
    monkeypatch.setenv("TRADING_JOURNAL_CLICKHOUSE_PASSWORD", "test-only")
    monkeypatch.setenv("BACKTEST_CLICKHOUSE_USER", "market-only")
    client = readonly_typed_journal_client()
    try:
        assert client.user == "journal-only"
        assert client.default_query_params["readonly"] == "1"
    finally:
        client.close()
    monkeypatch.setenv("BACKTEST_CLICKHOUSE_USER", "journal-only")
    with pytest.raises(ValueError, match="separate journal credentials"):
        readonly_typed_journal_client()


def test_typed_reader_loads_fenced_event_and_rejects_missing_detail() -> None:
    record = JournalRecord(
        "00000000-0000-0000-0000-000000000083", RUN, 1, AT, AT,
        "lifecycle", "run", RUN, "", {"status": "running", "config": {"mode": "live"}},
    )
    batch = runtime_lifecycle_batch(
        record, run_month=date(2026, 8, 1),
        attempt_id="00000000-0000-0000-0000-000000000081",
        batch_id="00000000-0000-0000-0000-000000000082",
        prior_batch_id="00000000-0000-0000-0000-000000000000",
        source_cursor="start", expected_config={"mode": "live"},
    )
    client = MemoryClient()
    publish_typed_batch(client, batch)
    prefix = load_committed_prefix(client, RUN)
    assert prefix is not None
    page = load_typed_event_page(client, prefix)
    assert len(page) == 1
    assert page[0].detail_family == "trading_run_transition_v1"
    assert page[0].detail["status"] == "running"
    assert load_typed_event_page(client, prefix, after_sequence=1) == ()
    client.tables["trading_event_v1"][0]["entity_id"] = "tampered"
    with pytest.raises(RuntimeError, match="differs from its hash"):
        load_typed_event_page(client, prefix)
    client.tables["trading_event_v1"][0]["entity_id"] = RUN
    client.tables["trading_run_transition_v1"].clear()
    with pytest.raises(RuntimeError, match="missing or duplicate"):
        load_typed_event_page(client, prefix)


def test_typed_reader_rejects_missing_committed_event() -> None:
    record = JournalRecord(
        "00000000-0000-0000-0000-000000000093", RUN, 1, AT, AT,
        "lifecycle", "run", RUN, "", {"status": "running", "config": {"mode": "live"}},
    )
    batch = runtime_lifecycle_batch(
        record, run_month=date(2026, 8, 1),
        attempt_id="00000000-0000-0000-0000-000000000091",
        batch_id="00000000-0000-0000-0000-000000000092",
        prior_batch_id="00000000-0000-0000-0000-000000000000",
        source_cursor="start", expected_config={"mode": "live"},
    )
    client = MemoryClient()
    publish_typed_batch(client, batch)
    prefix = load_committed_prefix(client, RUN)
    assert prefix is not None
    client.tables["trading_event_v1"].clear()
    with pytest.raises(RuntimeError, match="missing committed rows"):
        load_typed_event_page(client, prefix)


def test_typed_reader_rejects_gap_and_truncated_final_page() -> None:
    client = MemoryClient()
    prior = "00000000-0000-0000-0000-000000000000"
    for sequence in (1, 2):
        record = JournalRecord(
            f"00000000-0000-0000-0000-{sequence:012d}", RUN, sequence,
            AT, AT, "lifecycle", "run", RUN, "",
            {"status": "running", "config": {"mode": "live"}},
        )
        batch_id = f"00000000-0000-0000-0001-{sequence:012d}"
        batch = runtime_lifecycle_batch(
            record, run_month=date(2026, 8, 1),
            attempt_id="00000000-0000-0000-0000-000000000091",
            batch_id=batch_id, prior_batch_id=prior,
            source_cursor=f"cursor-{sequence}", expected_config={"mode": "live"},
        )
        publish_typed_batch(client, batch)
        prior = batch_id
    prefix = load_committed_prefix(client, RUN)
    assert prefix is not None and prefix.last_sequence == 2
    events = client.tables["trading_event_v1"]
    first = events.pop(0)
    with pytest.raises(RuntimeError, match="committed prefix"):
        load_typed_event_page(client, prefix)
    events.insert(0, first)
    events.pop()
    with pytest.raises(RuntimeError, match="ends before the committed prefix"):
        load_typed_event_page(client, prefix)
