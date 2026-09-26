"""Strategy 1 allocation fills have one parent and one normalized V4 child."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_typed_projection import project_pending_backtest_v4_prefix
from src.trading_runtime.arte_journal_writer import (
    ArteJournalWriter, _sealed_families, typed_row,
)
from src.trading_runtime import arte_journal_writer as writer_module
from src.trading_runtime.arte_journal_commit_v4 import (
    load_verified_commit_v4, publish_portfolio_allocation_batch_v4,
)
from src.trading_runtime.arte_portfolio_allocation_v4 import (
    ALLOCATION, V4PortfolioAllocationBatch, seal_portfolio_allocation_v3,
)
from tests.test_arte_journal_writer import ATTEMPT, RUN
from tests.test_arte_journal_commit_v4 import attached_v4_client


def test_allocation_has_exact_v4_scalar_family_and_parent(monkeypatch):
    journal = BacktestMemoryJournal(run_id=RUN)
    journal.append(
        run_id=RUN, category="portfolio_management",
        entity_type="portfolio_allocation",
        entity_id="DU1:early-squeeze-strategy:assignment-1:AAA",
        account_id="DU1", event_time=datetime(2026, 8, 18, 14,
                                               tzinfo=timezone.utc),
        payload={"event": "allocation_fill_applied",
                 "incremental_quantity": 3.0, "quantity": 3.0,
                 "ticker": "AAA", "action": "enter_long",
                 "strategy_id": "early-squeeze-strategy",
                 "assignment_id": "assignment-1",
                 "correlation_id": "intent-1", "causation_id": "fill-1"})
    unit, = project_pending_backtest_v4_prefix(
        journal, attempt_id=ATTEMPT, run_month=date(2026, 8, 1),
        prior_sequence=0, through_sequence=1)
    assert isinstance(unit, V4PortfolioAllocationBatch)
    assert ALLOCATION.name == "trading_portfolio_allocation_fill_v4"
    ddl = ALLOCATION.ddl()
    assert "storage_policy = 'live_market_ssd'" in ddl
    assert "PARTITION BY toYYYYMM(event_month)" in ddl
    assert "ORDER BY (run_id,account_id,record_id)" in ddl
    assert not any(kind in ddl for kind in (" JSON", "Array(", "Map(", "Blob"))
    child = typed_row(ALLOCATION.name, {
        key: value for key, value in unit.allocation.items()
        if key != "content_hash"})
    assert child["content_hash"] == unit.allocation["content_hash"]
    assert seal_portfolio_allocation_v3(
        (child,), unit.base.events, run_id=RUN,
        batch_id=unit.base.batch_id)["portfolio_allocation_fill_count"] == 1
    families = _sealed_families(
        unit.base, v4_allocation_ids=(unit.base.events[0]["record_id"],))
    assert dict(families)["trading_event_v1"]
    client = attached_v4_client()
    assert publish_portfolio_allocation_batch_v4(
        client, unit.base, allocation=unit.allocation) == unit.base.batch_id
    assert ALLOCATION.name in client.inserts
    commit, sealed = load_verified_commit_v4(
        client, run_id=RUN, batch_id=unit.base.batch_id)
    assert commit["family_count"] == 2
    assert {row["family_name"] for row in sealed} == {
        "trading_event_v1", ALLOCATION.name}
    client.tables[ALLOCATION.name].clear()
    with pytest.raises(RuntimeError, match="missing or excess rows"):
        load_verified_commit_v4(client, run_id=RUN,
                                batch_id=unit.base.batch_id)
    queued_client = attached_v4_client()
    monkeypatch.setattr(writer_module, "_v4_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity",
                        lambda _client, _run_id: {
                            "mode": "backtest", "account_ids": ("DU1",)})
    writer = ArteJournalWriter(
        queued_client, run_id=RUN, journal_profile="backtest_v4",
        coalesce_batches=False)
    try:
        assert writer.submit_portfolio_allocation_v4(unit).result(timeout=5) \
            == unit.base.batch_id
        assert ALLOCATION.name in queued_client.inserts
    finally:
        writer.close()
