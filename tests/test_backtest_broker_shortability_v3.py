"""Real OMS shortability emitters into closed normalized scalar evidence."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.backend.backtest_broker_shortability_v3 import (
    project_short_order_skip_v3, recover_short_order_skip_payload,
    seal_short_order_skip_v3,
)
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.order_management import (
    OrderManagementEngine, ShortabilitySnapshot,
)
from src.trading_runtime.signals import StrategyIntent
from tests.test_arte_journal_writer import ATTEMPT, BATCH, RUN


AT = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)


def _manager(provider):
    journal = BacktestMemoryJournal(run_id=RUN)
    manager = OrderManagementEngine.__new__(OrderManagementEngine)
    manager.shortability_provider = provider
    manager.journal = journal
    manager.run_id = RUN
    manager.strategy_id = "strategy-1"
    manager.strategy_revision = 7
    manager._groups = {}
    return manager, journal


def _intent():
    return StrategyIntent("intent-1", "AAA", AT, "enter_short", 5.0, 10.0)


def _project(record):
    return project_short_order_skip_v3(
        record, attempt_id=ATTEMPT, batch_id=BATCH)


def test_real_oms_emitter_no_provider_is_exactly_recoverable():
    manager, journal = _manager(None)
    with pytest.raises(ValueError, match="shortability is unavailable"):
        asyncio.run(manager._require_shortability(_intent(), SimpleNamespace(orders=())))
    record, = journal.unfenced_records()
    projected = _project(record)
    assert projected.detail["reason"] == "shortability_provider_unavailable"
    assert projected.detail["required_shares"] is None
    assert recover_short_order_skip_payload(
        projected.event, projected.detail) == record.payload
    assert seal_short_order_skip_v3(
        [projected.detail], [projected.event], run_id=RUN,
        batch_id=BATCH)["broker_short_order_skip_count"] == 1


@pytest.mark.parametrize("available", [0, 0.0, 3.25])
def test_real_oms_emitter_insufficient_borrow_preserves_numeric_type(available):
    class Provider:
        async def shortability(self, conid):
            assert conid == 123
            return ShortabilitySnapshot(123, available, "not shortable", AT)

    manager, journal = _manager(Provider())
    plan = SimpleNamespace(orders=(SimpleNamespace(conid=123, acctId="DU1"),))
    with pytest.raises(ValueError, match="sufficient shortable"):
        asyncio.run(manager._require_shortability(_intent(), plan))
    record, = journal.unfenced_records()
    projected = _project(record)
    assert projected.detail["available_shares"] == float(available)
    assert projected.detail["available_shares_was_int"] == int(type(available) is int)
    assert recover_short_order_skip_payload(
        projected.event, projected.detail) == record.payload


def test_shortability_projection_rejects_unmodeled_broker_evidence():
    manager, journal = _manager(None)
    with pytest.raises(ValueError):
        asyncio.run(manager._require_shortability(_intent(), SimpleNamespace(orders=())))
    record, = journal.unfenced_records()
    from dataclasses import replace
    for change in ({"other": "x"}, {"reason": "new_reason"},
                   {"required_shares": 1.0}):
        with pytest.raises(ValueError):
            _project(replace(record, payload={**record.payload, **change}))


def test_shortability_seal_rejects_missing_duplicate_and_corrupt_child():
    manager, journal = _manager(None)
    with pytest.raises(ValueError):
        asyncio.run(manager._require_shortability(_intent(), SimpleNamespace(orders=())))
    projected = _project(journal.unfenced_records()[0])
    detail, event = projected.detail, projected.event
    for rows in ([], [detail, detail], [{**detail, "ticker": "BBB"}],
                 [{**detail, "available_shares": 0.0}]):
        with pytest.raises(ValueError):
            seal_short_order_skip_v3(rows, [event], run_id=RUN, batch_id=BATCH)


def test_borrow_projection_rejects_duplicate_source_fields_that_disagree():
    class Provider:
        async def shortability(self, conid):
            return ShortabilitySnapshot(conid, 0.0, "not shortable", AT)

    manager, journal = _manager(Provider())
    plan = SimpleNamespace(orders=(SimpleNamespace(conid=123, acctId="DU1"),))
    with pytest.raises(ValueError):
        asyncio.run(manager._require_shortability(_intent(), plan))
    record, = journal.unfenced_records()
    from dataclasses import replace
    for ibkr_fields in ({"7636": 1.0, "7644": "not shortable"},
                        {"7636": 0.0, "7644": "easy_to_borrow"},
                        {"7636": 0, "7644": "not shortable"}):
        with pytest.raises(ValueError, match="Borrow evidence"):
            _project(replace(record, payload={**record.payload,
                                              "ibkr_fields": ibkr_fields}))
