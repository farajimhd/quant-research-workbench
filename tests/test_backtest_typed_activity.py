"""Fixed activity pages use only V2 typed facts and a verified prefix."""
import sqlite3
from types import SimpleNamespace

import pytest

from src.backend.backtest_typed_activity import load_fixed_typed_activity_page
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.replay_run_service import ReplayRunController, RunMode
from src.trading_runtime import arte_journal_writer as writer
from tests.test_arte_journal_v2_profile import V2MemoryClient, _batch


def _published(monkeypatch):
    monkeypatch.setattr(writer, "versioned_journal_v2_preflight", lambda client: None)
    monkeypatch.setattr(writer, "storage_preflight", lambda client, **kwargs: None)
    monkeypatch.setattr(writer, "_verify_run_identity",
                        lambda client, run_id: {"mode": "backtest"})
    client = V2MemoryClient()
    first = _batch()
    second = _batch(batch_id="00000000-0000-0000-0000-000000000006",
                    prior_batch_id=first.batch_id, sequence=2,
                    signal_id="signal-2")
    writer.publish_typed_batch(client, first, journal_profile="backtest_v2")
    writer.publish_typed_batch(client, second, journal_profile="backtest_v2")
    prefix = writer.load_committed_prefix(
        client, first.run_id, journal_profile="backtest_v2")
    assert isinstance(prefix, writer.V2CommittedPrefix)
    client.selects.clear()
    return client, prefix


def test_v2_activity_orders_and_pages_exact_typed_signal_facts(monkeypatch):
    client, prefix = _published(monkeypatch)
    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs:
                        pytest.fail("SQLite opened"))
    first = load_fixed_typed_activity_page(client, prefix, limit=1)
    assert first["next_sequence"] == 1
    assert not first["caught_up_to_prefix"]
    assert first["scanned_event_count"] == 1
    assert first["events"][0]["detail_family"] == "trading_strategy_signal_v2"
    second = load_fixed_typed_activity_page(
        client, prefix, after_sequence=first["next_sequence"], limit=1)
    assert second["next_sequence"] == 2
    assert second["caught_up_to_prefix"]
    assert [row["event"]["sequence"] for page in (first, second)
            for row in page["events"]] == [1, 2]
    assert all(query.startswith("SELECT ") for query in client.selects)
    assert not any("arte.bt_" in query for query in client.selects)


def test_v2_activity_fails_on_gap_hash_corruption_or_wrong_prefix(monkeypatch):
    client, prefix = _published(monkeypatch)
    with pytest.raises(ValueError, match="verified V2 prefix"):
        load_fixed_typed_activity_page(client, object())
    with pytest.raises(ValueError, match="cursor exceeds"):
        load_fixed_typed_activity_page(client, prefix,
                                       after_sequence=prefix.last_sequence + 1)
    first = client.tables["trading_event_v1"].pop(0)
    with pytest.raises(RuntimeError, match="committed prefix"):
        load_fixed_typed_activity_page(client, prefix)
    client.tables["trading_event_v1"].insert(0, first)
    client.tables["trading_strategy_signal_v2"][0]["reason"] = "tampered"
    with pytest.raises(RuntimeError, match="differs from its hash"):
        load_fixed_typed_activity_page(client, prefix)


def test_fixed_controller_typed_entrypoint_uses_only_v2_fence(monkeypatch):
    client, prefix = _published(monkeypatch)
    controller = object.__new__(ReplayRunController)
    controller.run_id = prefix.run_id
    controller.definition = SimpleNamespace(mode=RunMode.BACKTEST)
    controller._journal = BacktestMemoryJournal(run_id=prefix.run_id)
    controller._journal_publisher = SimpleNamespace(
        fenced_sequence=prefix.last_sequence, _batch_id=prefix.last_batch_id)
    page = controller.fixed_typed_activity_page(client=client, limit=1)
    assert page["next_sequence"] == 1
    assert page["events"][0]["detail_family"] == "trading_strategy_signal_v2"
    controller._journal_publisher._batch_id = "00000000-0000-0000-0000-000000000000"
    with pytest.raises(RuntimeError, match="publisher fence"):
        controller.fixed_typed_activity_page(client=client, limit=1)
