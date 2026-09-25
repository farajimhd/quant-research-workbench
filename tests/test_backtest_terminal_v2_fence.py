from datetime import date, datetime, timezone
from uuid import UUID

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_terminal_snapshot_v2 import project_snapshot_group, position_set_sha256, project_position_scalars
from src.backend.backtest_terminal_v2_fence import (
    load_terminal_v2_commit, project_terminal_v2_commit, seal_v2_row,
)
from src.trading_runtime.arte_journal_projection import runtime_lifecycle_batch
from src.trading_runtime.arte_journal_writer import CommittedPrefix, typed_row
from src.trading_runtime.ibkr_schema import AccountSummary, PortfolioPosition
from src.trading_runtime.journal_contract import canonical_json


RUN = "00000000-0000-0000-0000-000000000b01"
ATTEMPT = "00000000-0000-0000-0000-000000000b02"
PRIOR = "00000000-0000-0000-0000-000000000b03"
BATCH = "00000000-0000-0000-0000-000000000b04"
AT = datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc)


def _suffix():
    journal = BacktestMemoryJournal(run_id=RUN, initial_sequence=1)
    summary = AccountSummary("DU1", 101.1234567890123, 90.0, 90.0,
                             11.1234567890123, 80.0, 80.0, timestamp=AT)
    position = PortfolioPosition("DU1", 42, "ABCD", 1.25, 8.8, 11.0,
                                 8.0, 8.0, 0.0, 1.0)
    snapshot_id = "00000000-0000-0000-0000-000000000b05"
    digest = position_set_sha256((project_position_scalars(
        position.to_cpapi(), account_id="DU1"),))
    account, child = journal.append_many([
        dict(run_id=RUN, category="snapshot", entity_type="portfolio",
             entity_id="DU1", account_id="DU1", event_time=AT,
             payload={**summary.to_cpapi(), "snapshot_id": snapshot_id,
                      "expected_position_count": 1, "position_set_sha256": digest}),
        dict(run_id=RUN, category="snapshot", entity_type="position",
             entity_id="42", account_id="DU1", event_time=AT,
             payload={**position.to_cpapi(), "parent_snapshot_id": snapshot_id,
                      "ordinal": 0}),
    ])
    terminal = journal.append(run_id=RUN, category="lifecycle", entity_type="run",
                              entity_id=RUN, event_time=AT,
                              payload={"status": "completed", "processed_events": 10})
    parent, children = project_snapshot_group(account, (child,), batch_id=BATCH)
    accounts = (seal_v2_row("trading_backtest_account_snapshot_v2", parent),)
    positions = tuple(seal_v2_row("trading_backtest_position_snapshot_v2", row)
                      for row in children)
    events = []
    for record in (account, child, terminal):
        events.append(typed_row("trading_event_v1", {
            "run_id": RUN, "event_month": "2026-08-01", "attempt_id": ATTEMPT,
            "batch_id": BATCH, "record_id": record.record_id,
            "sequence": record.sequence, "event_time": record.event_time,
            "recorded_at": record.recorded_at,
            "category": record.category, "entity_type": record.entity_type,
            "entity_id": record.entity_id, "account_id": record.account_id,
            "correlation_id": record.payload.get("correlation_id", ""),
            "causation_id": record.payload.get("causation_id", ""),
        }))
    lifecycle = runtime_lifecycle_batch(
        terminal, run_month=date(2026, 8, 1), attempt_id=ATTEMPT,
        batch_id=BATCH, prior_batch_id=PRIOR, source_cursor="start")
    transitions = (typed_row("trading_run_transition_v1",
                             lifecycle.run_transitions[0]),)
    prefix = CommittedPrefix(RUN, 1, PRIOR, "start", "running", (PRIOR,))
    return prefix, tuple(events), transitions, accounts, positions


def test_v2_terminal_commit_seals_exact_sequence_and_cold_fake_rows():
    prefix, events, transitions, accounts, positions = _suffix()
    seal = project_terminal_v2_commit(
        prefix, attempt_id=ATTEMPT, batch_id=BATCH, account_ids=("DU1",), source_cursor="start",
        status="completed", committed_at=AT, events=events,
        transitions=transitions, accounts=accounts, positions=positions)
    assert seal["prior_v1_batch_id"] == PRIOR
    assert (seal["first_sequence"], seal["last_sequence"]) == (2, 4)
    assert (seal["event_count"], seal["account_count"], seal["position_count"]) == (3, 1, 1)
    for key in ("event_hash", "run_transition_hash", "account_hash", "position_hash"):
        assert len(seal[key]) == 64

    rows = {
        "trading_backtest_terminal_commit_v2": [seal],
        "trading_event_v1": events,
        "trading_run_transition_v1": transitions,
        "trading_backtest_account_snapshot_v2": accounts,
        "trading_backtest_position_snapshot_v2": positions,
    }

    class Client:
        def execute(self, sql):
            table = sql.split("FROM arte.", 1)[1].split(" ", 1)[0]
            return "\n".join(canonical_json(row) for row in rows[table])

    assert load_terminal_v2_commit(Client(), prefix, account_ids=("DU1",)) == seal
    rows["trading_backtest_position_snapshot_v2"] = [dict(positions[0], quantity=2.0)]
    with pytest.raises(ValueError, match="row hash differs"):
        load_terminal_v2_commit(Client(), prefix, account_ids=("DU1",))


def test_v2_terminal_commit_rejects_missing_child_and_lifecycle_reordering():
    prefix, events, transitions, accounts, positions = _suffix()
    kwargs = dict(attempt_id=ATTEMPT, batch_id=BATCH, account_ids=("DU1",), source_cursor="start",
                  status="completed", committed_at=AT, events=events,
                  transitions=transitions, accounts=accounts, positions=positions)
    with pytest.raises(ValueError, match="do not cover exactly"):
        project_terminal_v2_commit(prefix, **dict(kwargs, positions=()))
    with pytest.raises(ValueError, match="do not extend the V1 sequence"):
        project_terminal_v2_commit(prefix, **dict(kwargs, events=(events[2], *events[:2])))
    with pytest.raises(ValueError, match="population differs"):
        project_terminal_v2_commit(prefix, **dict(kwargs, account_ids=("OTHER",)))
    wrong_attempt = typed_row("trading_event_v1", {
        **{key: value for key, value in events[0].items() if key != "content_hash"},
        "attempt_id": "00000000-0000-0000-0000-000000000b06",
    })
    with pytest.raises(ValueError, match="do not extend the V1 sequence"):
        project_terminal_v2_commit(prefix, **dict(kwargs, events=(wrong_attempt, *events[1:])))
