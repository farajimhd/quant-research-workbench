from datetime import date, datetime, timezone

import pytest

from src.trading_runtime.arte_journal_projection import runtime_lifecycle_batch
from src.trading_runtime.arte_journal_reader import (
    TypedJournalEvent, _V4_EVENT_DETAILS, _detail_family,
    load_complete_typed_protection_history, load_typed_event_page,
    load_typed_protection_page, TypedProtectionPage,
    readonly_typed_journal_client,
)
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.trading_runtime.arte_journal_writer import (
    load_committed_prefix, publish_typed_batch,
)
from src.trading_runtime.journal_contract import JournalRecord
from tests.test_arte_journal_writer import MemoryClient


RUN = "live:DU1"
AT = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)


def test_v4_protection_page_recovers_exact_order_children(monkeypatch) -> None:
    from src.backend.backtest_protection_change_v3 import project_protection_change_v3
    from src.trading_runtime import arte_journal_reader as reader

    record = JournalRecord(
        "00000000-0000-0000-0000-000000000083", RUN, 1, AT, AT,
        "protection", "protection_change", "broker-1", "DU1",
        {"schema_version": 1, "order_group_id": "group-1",
         "entry_order_ids": ["entry-1"], "order_id": "broker-1",
         "client_order_id": "target-1", "kind": "target",
         "phase": "effective", "price": 12.5, "active": True,
         "ticker": "AAA", "source_intent_id": "source-1",
         "strategy_id": "strategy-1", "strategy_revision": 1,
         "correlation_id": "corr-1", "causation_id": "cause-1",
         "action": "replace_profit_target", "intent_id": "amend-1"},
    )
    batch_id = "00000000-0000-0000-0000-000000000082"
    projected = project_protection_change_v3(
        record, attempt_id="00000000-0000-0000-0000-000000000081",
        batch_id=batch_id)
    event = {**projected.event,
             "event_time": "2026-08-18 08:05:00.000000000",
             "recorded_at": "2026-08-18 08:05:00.000000"}
    prefix = V4CommittedPrefix(RUN, 1, batch_id, "bar:1", "running", (batch_id,))
    monkeypatch.setattr(reader, "load_typed_event_page",
                        lambda *_a, **_k: (TypedJournalEvent(
                            event, "trading_protection_change_v3", projected.detail),))
    def rows(_client, sql):
        assert "trading_protection_entry_order_v3" in sql
        assert "LIMIT 2" in sql
        return [dict(projected.entry_orders[0])]
    monkeypatch.setattr(reader, "_rows", rows)
    page = load_typed_protection_page(object(), prefix)
    assert page.next_sequence == 1
    assert page.records == (record,)
    with pytest.raises(RuntimeError, match="child bound"):
        load_typed_protection_page(object(), prefix, max_children=0)
    monkeypatch.setattr(reader, "_rows", lambda *_a: [
        {**projected.entry_orders[0], "entry_order_id": "tampered"}])
    with pytest.raises(ValueError, match="content differs"):
        load_typed_protection_page(object(), prefix)


def test_v4_protection_page_checks_zero_child_inventory_and_advances_cursor(
        monkeypatch) -> None:
    from src.backend.backtest_protection_change_v3 import project_protection_change_v3
    from src.trading_runtime import arte_journal_reader as reader

    batch_id = "00000000-0000-0000-0000-000000000082"
    prefix = V4CommittedPrefix(RUN, 2, batch_id, "bar:2", "running", (batch_id,))
    record = JournalRecord(
        "00000000-0000-0000-0000-000000000083", RUN, 2, AT, AT,
        "protection", "protection_change", "broker-1", "DU1",
        {"schema_version": 1, "order_group_id": "group-1",
         "entry_order_ids": [], "order_id": "broker-1",
         "client_order_id": "target-1", "kind": "target",
         "phase": "effective", "price": 12.5, "active": True,
         "ticker": "AAA", "source_intent_id": "source-1",
         "strategy_id": "strategy-1", "strategy_revision": 1,
         "correlation_id": "corr-1", "causation_id": "cause-1"},
    )
    projected = project_protection_change_v3(
        record, attempt_id="00000000-0000-0000-0000-000000000081",
        batch_id=batch_id)
    event = {**projected.event, "event_time": "2026-08-18 08:05:00.000000000",
             "recorded_at": "2026-08-18 08:05:00.000000"}
    other = TypedJournalEvent({"sequence": 1, "category": "lifecycle",
                               "entity_type": "run"}, None, None)
    monkeypatch.setattr(reader, "load_typed_event_page",
                        lambda *_a, **_k: (other, TypedJournalEvent(
                            event, "trading_protection_change_v3", projected.detail)))
    monkeypatch.setattr(reader, "_rows", lambda *_a: [])
    page = load_typed_protection_page(object(), prefix)
    assert page.next_sequence == 2 and page.records == (record,)
    monkeypatch.setattr(reader, "_rows", lambda *_a: [{"record_id": record.record_id}])
    with pytest.raises(RuntimeError, match="excess children"):
        load_typed_protection_page(object(), prefix)


def test_complete_protection_history_requires_every_committed_page(monkeypatch) -> None:
    from src.trading_runtime import arte_journal_reader as reader

    batch_id = "00000000-0000-0000-0000-000000000082"
    prefix = V4CommittedPrefix(RUN, 3, batch_id, "bar:3", "running", (batch_id,))
    record = JournalRecord(
        "00000000-0000-0000-0000-000000000083", RUN, 2, AT, AT,
        "protection", "protection_change", "broker-1", "DU1", {},
    )
    calls = []
    def page(_client, _prefix, *, after_sequence, limit, max_children):
        calls.append((after_sequence, limit, max_children))
        return {0: TypedProtectionPage(1, ()),
                1: TypedProtectionPage(2, (record,)),
                2: TypedProtectionPage(3, ())}[after_sequence]
    monkeypatch.setattr(reader, "load_typed_protection_page", page)
    assert load_complete_typed_protection_history(
        object(), prefix, page_size=1, max_events=3) == (record,)
    assert calls == [(0, 1, 50_000), (1, 1, 50_000), (2, 1, 50_000)]
    with pytest.raises(RuntimeError, match="event bound"):
        load_complete_typed_protection_history(object(), prefix, max_events=2)
    monkeypatch.setattr(reader, "load_typed_protection_page",
                        lambda *_a, **_k: TypedProtectionPage(0, ()))
    with pytest.raises(RuntimeError, match="did not advance"):
        load_complete_typed_protection_history(object(), prefix)


def test_v4_review_resolves_every_supplement_without_changing_legacy_map() -> None:
    from src.trading_runtime.arte_journal_writer import _CONTRACTS

    prefix = V4CommittedPrefix(
        RUN, 1, "00000000-0000-0000-0000-000000000001",
        "start", "completed", ("00000000-0000-0000-0000-000000000001",))
    for kind, family in _V4_EVENT_DETAILS.items():
        assert _detail_family(prefix, kind) == family
        assert family in _CONTRACTS
    assert _detail_family(prefix, ("lifecycle", "run")) == "trading_run_transition_v1"
    with pytest.raises(RuntimeError, match="unknown detail contract"):
        _detail_family(prefix, ("unknown", "unknown"))


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
