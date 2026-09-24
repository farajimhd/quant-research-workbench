from datetime import datetime, timezone

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.journal_evidence import REFERENCE


RUN_ID = "00000000-0000-4000-8000-000000000001"
AT = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


def _entry(entity_id: str, *, run_id: str = RUN_ID) -> dict:
    return dict(run_id=run_id, category="strategy", entity_type="signal",
                entity_id=entity_id, payload={"ticker": "AAPL"}, event_time=AT)


def test_invalid_batch_does_not_publish_partial_prefix():
    journal = BacktestMemoryJournal(run_id=RUN_ID)
    with pytest.raises(ValueError, match="mix runs"):
        journal.append_many([_entry("one"), _entry("two", run_id="other")])
    assert journal.latest_sequence(RUN_ID) == 0
    assert journal.records(RUN_ID) == []
    with pytest.raises(ValueError, match="mix runs"):
        journal.append_once_many([_entry("one"), _entry("two", run_id="other")])
    assert journal.latest_sequence(RUN_ID) == 0


def test_idempotent_batch_preserves_order_and_first_occurrence():
    journal = BacktestMemoryJournal(run_id=RUN_ID)
    result = journal.append_once_many([_entry("one"), _entry("one"), _entry("two")])
    assert [inserted for _, inserted in result] == [True, False, True]
    assert [record.sequence for record, _ in result] == [1, 1, 2]
    assert journal.append_once_many([_entry("one")])[0] == (result[0][0], False)
    assert [record.sequence for record in journal.records(RUN_ID, after_sequence=1)] == [2]


def test_buffer_fails_closed_and_evidence_uses_live_reference_contract():
    journal = BacktestMemoryJournal(run_id=RUN_ID, max_pending_records=1)
    assert set(journal.reference_json({"foo": 1})) == {REFERENCE}
    encoded = journal.reference_evidence({"levels": [{"price": 12.0}]})
    assert set(encoded["levels"]) == {REFERENCE}
    journal.append_many([_entry("one")])
    with pytest.raises(RuntimeError, match="buffer is full"):
        journal.append_many([_entry("two")])
    assert journal.latest_sequence(RUN_ID) == 1
    with pytest.raises(RuntimeError, match="async fence"):
        journal.flush()
    assert [record.sequence for record in journal.unfenced_records()] == [1]
    journal.mark_fenced(1)
    assert journal.unfenced_records() == []
    assert journal.append_many([_entry("two")])[0].sequence == 2
    with pytest.raises(ValueError, match="outside the unfenced prefix"):
        journal.unfenced_records(after_sequence=0)
