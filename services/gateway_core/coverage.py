"""Coverage interval helpers shared by gateway backfill logic."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable


@dataclass(frozen=True)
class CoverageInterval:
    start_utc: datetime
    end_utc: datetime
    status: str = "covered"
    source: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CoverageGap:
    start_utc: datetime
    end_utc: datetime
    reason: str = "missing_coverage"

    @property
    def seconds(self) -> float:
        return max(0.0, (self.end_utc - self.start_utc).total_seconds())

    @property
    def days(self) -> float:
        return self.seconds / 86400.0


def merge_covered_intervals(
    intervals: Iterable[CoverageInterval],
    *,
    tolerance: timedelta = timedelta(0),
) -> list[CoverageInterval]:
    """Merge covered intervals only.

    Services with market-session rules should pass intervals that are already
    safe to merge. This helper intentionally does not decide market-hour gaps.
    """

    covered = sorted(
        (item for item in intervals if item.status.lower() in {"covered", "completed", "running"}),
        key=lambda item: item.start_utc,
    )
    if not covered:
        return []
    merged: list[CoverageInterval] = [covered[0]]
    for item in covered[1:]:
        last = merged[-1]
        if item.start_utc <= last.end_utc + tolerance:
            merged[-1] = CoverageInterval(
                start_utc=last.start_utc,
                end_utc=max(last.end_utc, item.end_utc),
                status=last.status,
                source=last.source or item.source,
                metadata={**last.metadata, **item.metadata},
            )
        else:
            merged.append(item)
    return merged


def find_interval_gaps(
    *,
    expected_start_utc: datetime,
    expected_end_utc: datetime,
    covered_intervals: Iterable[CoverageInterval],
    tolerance: timedelta = timedelta(0),
) -> list[CoverageGap]:
    cursor = expected_start_utc
    gaps: list[CoverageGap] = []
    for interval in merge_covered_intervals(covered_intervals, tolerance=tolerance):
        if interval.end_utc <= cursor:
            continue
        if interval.start_utc > cursor + tolerance:
            gaps.append(CoverageGap(cursor, interval.start_utc))
        cursor = max(cursor, interval.end_utc)
        if cursor >= expected_end_utc:
            break
    if cursor < expected_end_utc:
        gaps.append(CoverageGap(cursor, expected_end_utc))
    return gaps
