"""The read-only commit diagnostic must share the V4 verifier's batch bound."""
from uuid import UUID

import pytest

from scripts.clickhouse.profile_strategy_one_journal_commits import _profile
from src.trading_runtime.arte_journal_commit_v4 import MAX_V4_COMMIT_EVENTS


def _header(events: int) -> dict[str, object]:
    return {
        "batch_id": str(UUID(int=1)), "prior_batch_id": str(UUID(int=0)),
        "first_sequence": 1, "last_sequence": events,
        "event_count": events, "family_count": 3,
        "source_cursor": "2026-08-18:1", "status": "running",
    }


def test_v4_maximum_commit_size_is_profiled():
    result = _profile([_header(MAX_V4_COMMIT_EVENTS)])
    assert f"events={MAX_V4_COMMIT_EVENTS}" in result[0]
    assert f"1025-{MAX_V4_COMMIT_EVENTS}" in result[2]
    assert result[2].endswith("0, 0, 0, 0, 0, 1")


def test_oversized_commit_remains_rejected():
    with pytest.raises(ValueError, match=f"bound={MAX_V4_COMMIT_EVENTS}"):
        _profile([_header(MAX_V4_COMMIT_EVENTS + 1)])
