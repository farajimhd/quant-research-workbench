"""Staged Strategy 1 evidence stays scalar and linked to a typed parent."""
from dataclasses import replace
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from src.trading_runtime.arte_strategy_one_entry_journal import (
    ENTRY_EVIDENCE, project_strategy_one_entry_evidence,
    seal_strategy_one_entry_evidence,
)
from src.trading_runtime.arte_journal_writer import _CONTRACTS
from src.trading_runtime.arte_journal_writer import _sealed_families
from src.trading_runtime.arte_journal_writer import ArteJournalWriter, V4StrategyOneEntryBatch
from src.trading_runtime.arte_intent_projection import strategy_intent_batch
from src.trading_runtime.arte_journal_commit_v4 import (
    _publish_typed_batch_v4, _sealed_strategy_one_entry_rows,
    load_verified_commit_v4, publish_base_typed_batch_v4,
)
from src.trading_runtime.strategy_one_intent import strategy_one_entry_intent
from src.trading_runtime.strategy_one_stateful import StrategyOneEntryProposal


def _source():
    proposal = StrategyOneEntryProposal(
        "assignment-1", "DU1", "AAA", 31_000, 30_000, 10.01, 9.89,
        12., "R4", .5, 30_000, "S1",
    )
    session = date(2026, 8, 18)
    return proposal, strategy_one_entry_intent(proposal, session_date=session), session


def test_entry_evidence_contract_is_normalized_nonredundant_and_ordered():
    proposal, intent, session = _source()
    parent = str(uuid4())
    batch = str(uuid4())
    row = project_strategy_one_entry_evidence(
        proposal, intent, session_date=session, run_id="run-1",
        batch_id=batch, parent_record_id=parent)
    assert row == project_strategy_one_entry_evidence(
        proposal, intent, session_date=session, run_id="run-1",
        batch_id=batch, parent_record_id=parent)
    assert row["parent_record_id"] == parent
    assert row["event_month"] == "2026-08-01"
    assert row["frozen_gap"] == "0.500000000000000000"
    assert row["target_level_id"] == "R4"
    assert set(row) == {column for column, _ in ENTRY_EVIDENCE.columns} - {"content_hash"}
    assert not {"account_id", "ticker", "reference_ask", "initial_stop",
                "initial_target", "json", "payload"} & set(row)
    assert "live_market_ssd" in ENTRY_EVIDENCE.ddl()
    assert ENTRY_EVIDENCE.partition == "toYYYYMM(event_month)"
    assert _CONTRACTS[ENTRY_EVIDENCE.name] is ENTRY_EVIDENCE
    sealed = seal_strategy_one_entry_evidence(row)
    assert len(sealed["content_hash"]) == 64
    assert sealed == seal_strategy_one_entry_evidence(row)


def test_entry_evidence_rejects_parent_intent_or_causal_mismatch():
    proposal, intent, session = _source()
    kwargs = dict(session_date=session, run_id="run-1", batch_id=str(uuid4()),
                  parent_record_id=str(uuid4()))
    with pytest.raises(ValueError, match="differs"):
        project_strategy_one_entry_evidence(
            proposal, replace(intent, profit_target_price=13.), **kwargs)
    with pytest.raises(ValueError, match="causal boundaries"):
        project_strategy_one_entry_evidence(
            replace(proposal, bos_break_boundary_ms=32_000),
            strategy_one_entry_intent(replace(proposal, bos_break_boundary_ms=32_000),
                                      session_date=session), **kwargs)


def test_v4_commit_seals_exact_one_entry_child_to_the_typed_parent():
    proposal, intent, session = _source()
    attempt, batch_id = str(uuid4()), str(uuid4())
    prior = "00000000-0000-0000-0000-000000000000"
    item = strategy_intent_batch(
        intent, run_id="run-1", run_month=date(2026, 8, 1),
        account_id="DU1", attempt_id=attempt, batch_id=batch_id,
        prior_batch_id=prior, sequence=1, source_cursor="boundary-31000",
        run_status="running", recorded_at=datetime(2026, 8, 18, 8, 0, 31,
                                                    tzinfo=timezone.utc))
    source = project_strategy_one_entry_evidence(
        proposal, intent, session_date=session, run_id="run-1",
        batch_id=batch_id, parent_record_id=item.intents[0]["record_id"])
    base = _sealed_families(item)
    sealed = _sealed_strategy_one_entry_rows(item, base, (source,))
    assert len(sealed) == 1
    assert sealed[0]["parent_record_id"] == item.intents[0]["record_id"]
    assert len(sealed[0]["content_hash"]) == 64
    with pytest.raises(ValueError, match="lacks exact normalized evidence"):
        _sealed_strategy_one_entry_rows(item, base, ())
    with pytest.raises(ValueError, match="differs from its typed parent"):
        _sealed_strategy_one_entry_rows(item, base, (
            {**source, "boundary_ms": 31_100},))

    from tests.test_arte_journal_commit_v4 import attached_v4_client

    missing_child_client = attached_v4_client()
    with pytest.raises(ValueError, match="lacks exact normalized evidence"):
        publish_base_typed_batch_v4(missing_child_client, item)
    assert not missing_child_client.inserts

    client = attached_v4_client()
    _publish_typed_batch_v4(client, item, strategy_one_entry_rows=(source,))
    commit, families = load_verified_commit_v4(
        client, run_id=item.run_id, batch_id=item.batch_id)
    assert commit["family_count"] == len(families)
    assert ENTRY_EVIDENCE.name in {row["family_name"] for row in families}
    assert len(client.tables[ENTRY_EVIDENCE.name]) == 1


def test_v4_writer_queues_strategy_one_child_and_returns_verified_receipt(monkeypatch):
    from src.trading_runtime import arte_journal_writer as writer_module
    from tests.test_arte_journal_commit_v4 import attached_v4_client

    proposal, intent, session = _source()
    attempt, batch_id = str(uuid4()), str(uuid4())
    item = strategy_intent_batch(
        intent, run_id="run-1", run_month=date(2026, 8, 1),
        account_id="DU1", attempt_id=attempt, batch_id=batch_id,
        prior_batch_id="00000000-0000-0000-0000-000000000000",
        sequence=1, source_cursor="boundary-31000", run_status="running",
        recorded_at=datetime(2026, 8, 18, 8, 0, 31, tzinfo=timezone.utc))
    source = project_strategy_one_entry_evidence(
        proposal, intent, session_date=session, run_id="run-1",
        batch_id=batch_id, parent_record_id=item.intents[0]["record_id"])
    client = attached_v4_client()
    monkeypatch.setattr(writer_module, "storage_preflight",
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(writer_module, "journal_permission_preflight",
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity",
                        lambda *_args: {"mode": "backtest", "account_ids": ("DU1",)})
    writer = ArteJournalWriter(
        client, run_id="run-1", journal_profile="backtest_v4",
        coalesce_batches=False)
    try:
        receipt = writer.submit_strategy_one_entry_v4(
            V4StrategyOneEntryBatch(item, (source,)))
        assert receipt.result(timeout=5) == batch_id
        assert client.inserts.index(ENTRY_EVIDENCE.name) < client.inserts.index(
            "trading_commit_v4")
        assert load_verified_commit_v4(client, run_id="run-1", batch_id=batch_id)[0][
            "status"] == "running"
    finally:
        writer.close()
