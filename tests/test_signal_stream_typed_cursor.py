from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256

import pytest

from src.backend.signal_stream_typed_cursor import (
    TABLES, project_cursor_batch, read_cursor_batch, recover_cursor_batch,
    recover_cursor_chain,
)


SESSION = "2026-09-24"
PREVIOUS = "0" * 64
KEY = dict(session_key=SESSION, batch_sequence=1,
           cutoff_at=datetime(2026, 9, 24, 14, 0, tzinfo=UTC),
           configuration_revision="configuration-9", source_revision="source-4",
           previous_commit_hash=PREVIOUS)


def _occurrence():
    event_id = sha256(b"signal-event-1").hexdigest()
    return dict(event_id=event_id, signal_id=event_id,
                signal_stream_id="stream-1", ticker="ABC",
                definition_revision="definition-2",
                event_time="2026-09-24T13:59:00+00:00",
                available_at="2026-09-24T13:59:00+00:00")


class FakeAuthority:
    def __init__(self, rows):
        self.rows = {row["event_id"]: row for row in rows}

    def read_exact(self, event_id):
        return self.rows.get(event_id)


class FakeCursorClient:
    def __init__(self, rows):
        self.rows = {
            "signal_stream_state_delta_typed_v1": rows["state_delta"],
            "signal_stream_occurrence_ref_typed_v1": rows["occurrence_ref"],
            "signal_stream_cursor_commit_typed_v1": [rows["commit"]],
        }

    def read_rows(self, table_name, *, session_key, batch_sequence):
        return deepcopy(self.rows[table_name])


def test_typed_delta_fence_and_fake_cold_recovery() -> None:
    before = {"stream-1": {"ABC": {"matching": False,
                                    "definition_revision": "definition-2"}}}
    after = {"stream-1": {"ABC": {"matching": True,
                                   "definition_revision": "definition-2",
                                   "last_emitted_at": "2026-09-24T13:59:00+00:00"}}}
    occurrence = _occurrence()
    rows = project_cursor_batch(before, after, [occurrence], **KEY)
    restored, replayed, head = recover_cursor_batch(
        before, read_cursor_batch(FakeCursorClient(rows), session_key=SESSION,
                                  batch_sequence=1),
        occurrence_authority=FakeAuthority([occurrence]),
        session_key=SESSION, expected_sequence=1, previous_commit_hash=PREVIOUS,
        configuration_revision="configuration-9", source_revision="source-4")
    assert restored["stream-1"]["ABC"]["matching"] is True
    assert replayed == [occurrence]
    assert head == rows["commit"]["content_hash"]
    assert len(rows["state_delta"]) == len(rows["occurrence_ref"]) == 1
    assert all("storage_policy = 'live_market_ssd'" in table.ddl() for table in TABLES)
    assert all(f"arte.{table.name}" in table.ddl() for table in TABLES)
    assert all("JSON" not in table.ddl() and "payload" not in table.ddl()
               for table in TABLES)


@pytest.mark.parametrize("change", [
    {"state_delta": []}, {"occurrence_ref": []},
])
def test_missing_children_fail_closed(change) -> None:
    occurrence = _occurrence()
    rows = project_cursor_batch({}, {"stream-1": {"ABC": {
        "matching": True, "definition_revision": "definition-2"}}}, [occurrence], **KEY)
    rows.update(change)
    with pytest.raises(ValueError):
        recover_cursor_batch({}, rows, occurrence_authority=FakeAuthority([occurrence]),
                             session_key=SESSION, expected_sequence=1,
                             previous_commit_hash=PREVIOUS,
                             configuration_revision="configuration-9",
                             source_revision="source-4")


def test_missing_or_conflicting_typed_occurrence_fails_closed() -> None:
    occurrence = _occurrence()
    rows = project_cursor_batch({}, {}, [occurrence], **KEY)
    for authority in (FakeAuthority([]), FakeAuthority([{**occurrence, "ticker": "XYZ"}])):
        with pytest.raises(ValueError, match="missing or conflicting"):
            recover_cursor_batch({}, rows, occurrence_authority=authority,
                                 session_key=SESSION, expected_sequence=1,
                                 previous_commit_hash=PREVIOUS,
                                 configuration_revision="configuration-9",
                                 source_revision="source-4")


def test_fake_client_duplicate_commit_fence_rejected() -> None:
    rows = project_cursor_batch({}, {}, [], **KEY)
    client = FakeCursorClient(rows)
    client.rows["signal_stream_cursor_commit_typed_v1"].append(deepcopy(rows["commit"]))
    with pytest.raises(ValueError, match="duplicate"):
        read_cursor_batch(client, session_key=SESSION, batch_sequence=1)


def test_unmodeled_state_route_admissions_and_noncausal_occurrences_fail() -> None:
    occurrence = _occurrence()
    with pytest.raises(ValueError, match="unmodeled"):
        project_cursor_batch({}, {"stream-1": {"ABC": {
            "matching": True, "definition_revision": "definition-2", "unknown": 1}}},
            [], **KEY)
    with pytest.raises(ValueError, match="admission"):
        project_cursor_batch({}, {}, [], after_admissions={"watchlist": {"ABC": {}}}, **KEY)
    with pytest.raises(ValueError, match="noncausal"):
        project_cursor_batch({}, {}, [{**occurrence,
            "available_at": "2026-09-24T14:01:00+00:00"}], **KEY)
    with pytest.raises(ValueError, match="state identity"):
        project_cursor_batch({}, {"stream-1": {}}, [], **KEY)


def test_cold_chain_rejects_repeated_event_across_contiguous_fences() -> None:
    occurrence = _occurrence()
    first = project_cursor_batch({}, {}, [occurrence], **KEY)
    second = project_cursor_batch({}, {}, [occurrence], **{
        **KEY, "batch_sequence": 2,
        "previous_commit_hash": first["commit"]["content_hash"]})
    with pytest.raises(ValueError, match="duplicate source occurrence"):
        recover_cursor_chain([first, second], occurrence_authority=FakeAuthority([occurrence]),
                             session_key=SESSION, configuration_revision="configuration-9",
                             source_revision="source-4")
