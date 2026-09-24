from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID

import pytest

from src.trading_runtime.arte_assignment_command_projection import (
    COMMANDS, STATUS_COMMANDS, assignment_command_batch,
    project_assignment_command, recover_assignment_command,
)
from src.trading_runtime.arte_journal_schema import TABLES, strategy_assignment_command_upgrade_ddl
from src.trading_runtime.arte_journal_writer import _canonical_typed_content, _sealed_families
from src.trading_runtime.journal_contract import JournalRecord


AT = datetime(2026, 8, 17, 15, 0, tzinfo=timezone.utc)
RECORD_ID = "8d19b0f6-3fda-5c77-aa63-b1b3e0b5f55a"
BATCH_ID = "35eb42cb-4cb3-5d59-97aa-e5ccb8d60dd5"


def example(command: str, *, detail: dict | None = None) -> tuple[JournalRecord, dict, dict]:
    base = {
        "assignment_id": "assignment-1", "account_id": "account-1",
        "ticker": "ABC", "strategy_id": "early-squeeze", "strategy_revision": 24,
        "status": "managing", "updated_at": "2026-08-17T14:59:00+00:00",
        "state": {"campaign_id": "campaign-1"},
        "parameters": {"entry": {"threshold": 1.0}},
    }
    saved = {**base, "state": dict(base["state"]), "updated_at": AT.isoformat()}
    saved["status"] = STATUS_COMMANDS.get(command, base["status"])
    if command == "disable_after_exit":
        saved["state"]["disable_after_exit"] = True
    elif command == "request_entry":
        saved["state"]["manual_entry_requested"] = True
    elif command == "force_entry":
        saved["state"]["force_entry_requested"] = True
    elif command in {"request_exit", "exit_and_stop", "exit_keep_watching"}:
        saved["state"]["manual_exit_requested"] = True
        saved["state"]["disable_after_exit"] = command == "exit_and_stop"
    record = JournalRecord(
        RECORD_ID, "assignment-1", 7, AT, AT,
        "strategy", "strategy_assignment_command", "assignment-1", "account-1",
        {
            "event": "assignment_command", "command": command,
            "assignment_id": "assignment-1", "strategy_id": "early-squeeze",
            "strategy_revision": 24, "ticker": "ABC", "status": saved["status"],
            "detail": {} if detail is None else detail,
            "correlation_id": "correlation-1", "causation_id": "causation-1",
        },
    )
    return record, base, saved


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_all_live_command_variants_project_and_recover_without_invented_actor(command: str) -> None:
    record, base, saved = example(command)
    row = project_assignment_command(record, saved, batch_id=BATCH_ID)
    assert str(UUID(row["record_id"])) == RECORD_ID
    assert row["command"] == command
    assert "detail_count" not in row
    assert "manual_entry_set" not in row
    assert "operator_id" not in row
    assert recover_assignment_command(base, row, record_id=RECORD_ID, sequence=7) == saved


def test_nonempty_api_detail_fails_closed_until_typed_detail_contract_exists() -> None:
    record, _, saved = example("disable", detail={"reason": "operator review"})
    with pytest.raises(ValueError, match="nonempty detail"):
        project_assignment_command(record, saved, batch_id=BATCH_ID)


def test_projection_rejects_saved_state_mismatch_and_unknown_payload_fields() -> None:
    record, _, saved = example("request_entry")
    saved["state"].pop("manual_entry_requested")
    with pytest.raises(ValueError, match="manual_entry_requested"):
        project_assignment_command(record, saved, batch_id=BATCH_ID)
    record, _, saved = example("pause")
    record.payload["unexpected"] = 1
    with pytest.raises(ValueError, match="unmodeled"):
        project_assignment_command(record, saved, batch_id=BATCH_ID)


def test_recovery_rejects_changed_base_or_detail_hash() -> None:
    record, base, saved = example("exit_and_stop")
    row = project_assignment_command(record, saved, batch_id=BATCH_ID)
    changed = {**base, "account_id": "another-account"}
    with pytest.raises(ValueError, match="account_id"):
        recover_assignment_command(changed, row, record_id=RECORD_ID, sequence=7)
    changed_row = {**row, "status": "disabled"}
    with pytest.raises(ValueError, match="hash mismatch"):
        recover_assignment_command(base, changed_row, record_id=RECORD_ID, sequence=7)


def test_shared_table_and_writer_seal_assignment_command_family() -> None:
    record, _, saved = example("pause")
    batch = assignment_command_batch(
        record, saved, run_month=date(2026, 8, 1),
        attempt_id="41bdc04c-e8cd-4e85-810e-366b2e30a8fd",
        batch_id=BATCH_ID,
        prior_batch_id="00000000-0000-0000-0000-000000000000",
        source_cursor="journal:assignment-1:7",
    )
    sealed = dict(_sealed_families(batch))
    assert len(sealed["trading_event_v1"]) == 1
    assert sealed["trading_strategy_assignment_command_v1"][0]["command"] == "pause"
    table = next(table for table in TABLES if table.name == "trading_strategy_assignment_command_v1")
    assert "storage_policy = 'live_market_ssd'" in table.ddl()
    assert all("JSON" not in kind for _, kind in table.columns)


def test_cold_typed_row_recovery_matches_canonical_writer_hash() -> None:
    record, base, saved = example("request_exit")
    row = project_assignment_command(record, saved, batch_id=BATCH_ID)
    stored = _canonical_typed_content(
        "trading_strategy_assignment_command_v1",
        {key: value for key, value in row.items() if key != "content_hash"},
    )
    stored["content_hash"] = row["content_hash"]
    assert recover_assignment_command(
        base, stored, record_id=RECORD_ID, sequence=7, stored_utc=True,
    ) == saved


def test_assignment_command_operator_upgrade_ddl_is_additive_and_pinned() -> None:
    table = next(table for table in TABLES if table.name == "trading_strategy_assignment_command_v1")
    empty_hash = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    assert strategy_assignment_command_upgrade_ddl() == (
        table.ddl(),
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        "assignment_command_count UInt32 DEFAULT 0 AFTER event_count",
        "ALTER TABLE arte.trading_commit_v1 ADD COLUMN IF NOT EXISTS "
        f"assignment_command_hash FixedString(64) DEFAULT '{empty_hash}' "
        "AFTER event_hash",
    )
    assert "CREATE TABLE IF NOT EXISTS arte.trading_strategy_assignment_command_v1" in table.ddl()
    assert "storage_policy = 'live_market_ssd'" in table.ddl()
