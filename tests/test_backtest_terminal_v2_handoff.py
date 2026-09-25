from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from src.backend import backtest_terminal_v2_publication as publication
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_terminal_snapshot_v2 import (
    position_set_sha256, project_position_scalars,
)
from src.backend.replay_run_service import ReplayRunController, RunMode
from src.trading_runtime.arte_journal_writer import CommittedPrefix
from src.trading_runtime.ibkr_schema import AccountSummary, PortfolioPosition
from tests.test_backtest_terminal_v2_publication import FakeStorage


RUN = "00000000-0000-0000-0000-000000000c01"
ATTEMPT = "00000000-0000-0000-0000-000000000c02"
PRIOR = "00000000-0000-0000-0000-000000000c03"
AT = datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc)


def _controller():
    journal = BacktestMemoryJournal(run_id=RUN, initial_sequence=1)
    summary = AccountSummary("DU1", 101.0, 90.0, 90.0, 11.0, 80.0, 80.0,
                             timestamp=AT)
    position = PortfolioPosition("DU1", 42, "ABCD", 1.25, 8.8, 11.0,
                                 8.0, 8.0, 0.0, 1.0)
    digest = position_set_sha256((project_position_scalars(
        position.to_cpapi(), account_id="DU1"),))
    snapshot_id = "00000000-0000-0000-0000-000000000c04"
    journal.append_many([
        dict(run_id=RUN, category="snapshot", entity_type="portfolio",
             entity_id="DU1", account_id="DU1", event_time=AT,
             payload={**summary.to_cpapi(), "snapshot_id": snapshot_id,
                      "expected_position_count": 1,
                      "position_set_sha256": digest}),
        dict(run_id=RUN, category="snapshot", entity_type="position",
             entity_id="42", account_id="DU1", event_time=AT,
             payload={**position.to_cpapi(), "parent_snapshot_id": snapshot_id,
                      "ordinal": 0}),
    ])
    journal.append(run_id=RUN, category="lifecycle", entity_type="run",
                   entity_id=RUN, event_time=AT,
                   payload={"status": "completed", "processed_events": 10})
    prefix = CommittedPrefix(RUN, 1, PRIOR, "start", "running", (PRIOR,))
    controller = object.__new__(ReplayRunController)
    controller.definition = SimpleNamespace(mode=RunMode.BACKTEST)
    controller._account_map = {"DU1": "DU1"}
    controller._journal = journal
    controller._journal_publisher = SimpleNamespace(
        _task=None, fenced_sequence=1, _batch_id=PRIOR,
        _source_cursor="start", attempt_id=ATTEMPT,
        run_month=date(2026, 8, 1))
    return controller, prefix


def test_controller_prepares_exact_terminal_suffix_and_fake_publisher_seals(monkeypatch):
    controller, prefix = _controller()
    handoff = controller._prepare_terminal_v2_handoff(prefix, committed_at=AT)
    assert (handoff.commit["first_sequence"], handoff.commit["last_sequence"]) == (2, 4)
    assert [row["category"] for row in handoff.events] == [
        "snapshot", "snapshot", "lifecycle"]
    assert handoff.accounts[0]["expected_position_count"] == 1
    assert handoff.positions[0]["parent_snapshot_id"] == handoff.accounts[0]["snapshot_id"]
    client = FakeStorage()
    monkeypatch.setattr(publication, "load_committed_prefix",
                        lambda _client, run_id: prefix if run_id == RUN else None)
    assert publication.publish_terminal_v2_suffix(
        client, prefix, **handoff.publication_fields()) == handoff.commit
    assert len(client.tables["trading_backtest_terminal_commit_v2"]) == 1


def test_controller_handoff_rejects_stale_prefix_and_unmodeled_suffix():
    controller, prefix = _controller()
    controller._journal_publisher._batch_id = "00000000-0000-0000-0000-000000000c05"
    with pytest.raises(RuntimeError, match="settled typed V1 prefix"):
        controller._prepare_terminal_v2_handoff(prefix, committed_at=AT)
    controller._journal_publisher._batch_id = PRIOR
    controller._journal.append(run_id=RUN, category="snapshot",
                               entity_type="position", entity_id="99",
                               account_id="DU1", event_time=AT,
                               payload={"unmodeled": True})
    with pytest.raises(ValueError, match="incomplete or noncausal suffix"):
        controller._prepare_terminal_v2_handoff(prefix, committed_at=AT)
