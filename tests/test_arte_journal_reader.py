from datetime import date, datetime, timezone

import pytest

from src.trading_runtime.arte_journal_projection import runtime_lifecycle_batch
from src.trading_runtime.arte_journal_reader import load_typed_event_page
from src.trading_runtime.arte_journal_writer import (
    load_committed_prefix, publish_typed_batch,
)
from src.trading_runtime.journal_contract import JournalRecord
from tests.test_arte_journal_writer import MemoryClient


RUN = "live:DU1"
AT = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)


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
