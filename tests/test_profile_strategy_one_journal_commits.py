"""Bounded, read-only V4 commit profiling contract."""
from uuid import UUID

import pytest

from scripts.clickhouse.profile_strategy_one_journal_commits import _profile


def _row(sequence, *, prior, first, last, cursor, status="running"):
    return dict(batch_id=str(UUID(int=sequence)), prior_batch_id=str(UUID(int=prior)),
                first_sequence=first, last_sequence=last,
                event_count=last - first + 1, family_count=4,
                source_cursor=cursor, status=status)


def test_profile_reports_bounded_commit_distribution():
    lines = _profile([
        _row(1, prior=0, first=1, last=1, cursor="start"),
        _row(2, prior=1, first=2, last=10, cursor="04:00"),
        _row(3, prior=2, first=11, last=11, cursor="04:00",
             status="completed"),
    ])
    assert lines[0] == "V4 commits=3 events=11 terminal=1"
    assert lines[1] == "Commit events: min=1 median=1 max=9 mean=3.7"
    assert lines[2].endswith("2, 0, 1, 0, 0")
    assert lines[3] == "Cursor changes=1 family_count_median=4"


def test_profile_rejects_gap():
    with pytest.raises(ValueError, match="contiguous"):
        _profile([_row(1, prior=0, first=2, last=2, cursor="start")])
