"""The read-only commit diagnostic must share the V4 verifier's batch bound."""
from uuid import UUID

import pytest

from scripts.clickhouse.profile_strategy_one_journal_commits import _profile


def _header(events: int) -> dict[str, object]:
    return {
        "batch_id": str(UUID(int=1)), "prior_batch_id": str(UUID(int=0)),
        "first_sequence": 1, "last_sequence": events,
        "event_count": events, "family_count": 3,
        "source_cursor": "2026-08-18:1", "status": "running",
    }


def test_v4_maximum_commit_size_is_profiled():
    result = _profile([_header(2048)])
    assert "events=2048" in result[0]
    assert "1025-2048" in result[2]
    assert result[2].endswith("0, 0, 0, 0, 0, 1")


def test_oversized_commit_remains_rejected():
    with pytest.raises(ValueError, match="bound=2048"):
        _profile([_header(2049)])
