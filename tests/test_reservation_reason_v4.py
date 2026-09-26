"""Reservation lifecycle reasons are normalized and fenced with Strategy 1 V4."""
from datetime import date

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_typed_projection import project_pending_backtest_v4_prefix
from src.trading_runtime.arte_journal_commit_v4 import (
    load_verified_commit_v4, publish_reservation_reason_batch_v4,
)
from src.trading_runtime.arte_reservation_reason_v4 import (
    RESERVATION_REASON, V4ReservationReasonBatch,
)
from tests.test_arte_journal_commit_v4 import attached_v4_client
from tests.test_arte_journal_writer import ATTEMPT, RUN
from tests.test_backtest_v3_portfolio_reservation_projection import _record


def test_release_reason_is_a_complete_v4_cold_verified_child():
    source = _record(event="reservation_released", extras={"reason": "cancelled"})
    journal = BacktestMemoryJournal(run_id=RUN)
    journal.append(run_id=RUN, category=source.category,
                   entity_type=source.entity_type, entity_id=source.entity_id,
                   account_id=source.account_id, event_time=source.event_time,
                   payload=source.payload)
    unit, = project_pending_backtest_v4_prefix(
        journal, attempt_id=ATTEMPT, run_month=date(2026, 8, 1),
        prior_sequence=0, through_sequence=1)
    assert isinstance(unit, V4ReservationReasonBatch)
    assert len(unit.reasons) == 1
    assert "storage_policy = 'live_market_ssd'" in RESERVATION_REASON.ddl()
    client = attached_v4_client()
    assert publish_reservation_reason_batch_v4(
        client, unit.base, reasons=unit.reasons) == unit.base.batch_id
    assert RESERVATION_REASON.name in client.inserts
    load_verified_commit_v4(client, run_id=RUN, batch_id=unit.base.batch_id)
    client.tables[RESERVATION_REASON.name].clear()
    with pytest.raises(RuntimeError, match="missing or excess rows"):
        load_verified_commit_v4(client, run_id=RUN, batch_id=unit.base.batch_id)
