"""Inactive canonical readback and committed-head recovery for typed signals.

Adapter behavior is defined for driver values only; it never connects to a
database. The caller must separately verify provisioned storage placement.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from math import isfinite
from typing import Any, Mapping, Protocol

from src.backend.signal_stream_typed_cursor import (
    ADMISSION_DELTA, COMMIT, OCCURRENCE_REF, STATE_DELTA,
    TypedTable, recover_cursor_batch,
)
from src.backend.signal_stream_typed_occurrence import (
    COLUMN, FIELD, PARENT, RULE, restore_typed_occurrence,
)


_TABLES = {table.name: table for table in (
    PARENT, RULE, COLUMN, FIELD, STATE_DELTA, OCCURRENCE_REF, ADMISSION_DELTA, COMMIT,
)}
_OCCURRENCE_TABLES = frozenset({PARENT.name, RULE.name, COLUMN.name, FIELD.name})


def _canonical_time(value: Any, *, occurrence: bool) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("DateTime64 readback has unsupported value")
    # Every table here declares DateTime64(..., 'UTC'); a naive driver value
    # is therefore UTC, never host-local time.
    parsed = parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    return parsed.isoformat() if occurrence else parsed.isoformat(timespec="microseconds")


def _normalize(value: Any, kind: str, *, occurrence: bool) -> Any:
    if kind.startswith("Nullable(") and kind.endswith(")"):
        return None if value is None else _normalize(value, kind[9:-1], occurrence=occurrence)
    if value is None:
        raise ValueError("non-nullable typed readback column is null")
    if kind.startswith("DateTime64("):
        return _canonical_time(value, occurrence=occurrence)
    if kind == "Date":
        parsed = date.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(parsed, date) or isinstance(parsed, datetime):
            raise ValueError("Date readback has unsupported value")
        return parsed.isoformat()
    if kind == "Bool":
        if type(value) is bool:
            return value
        if type(value) is int and value in (0, 1):
            return bool(value)
        raise ValueError("Boolean readback is not 0/1")
    if kind.startswith("FixedString("):
        if isinstance(value, bytes):
            value = value.decode("ascii")
        if not isinstance(value, str) or len(value) != int(kind[12:-1]):
            raise ValueError("FixedString readback has wrong width")
        return value
    if kind in {"String", "LowCardinality(String)"}:
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        if not isinstance(value, str):
            raise ValueError("String readback has unsupported value")
        return value
    if kind.startswith(("UInt", "Int")):
        if type(value) is not int:
            raise ValueError("integer readback has unsupported value")
        bits = int(kind[4:] if kind.startswith("UInt") else kind[3:])
        lower, upper = (0, 2**bits) if kind.startswith("UInt") else (-(2**(bits-1)), 2**(bits-1))
        if not lower <= value < upper:
            raise ValueError("integer readback exceeds column width")
        return value
    if kind == "Float64":
        if type(value) not in (int, float):
            raise ValueError("Float64 readback has unsupported value")
        result = float(value)
        if not isfinite(result):
            raise ValueError("Float64 readback is nonfinite")
        return result
    raise ValueError(f"unsupported typed readback column {kind}")


def canonical_row(table: TypedTable, row: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize driver DateTime64/Bool/FixedString without dropping columns."""
    if not isinstance(row, Mapping) or set(row) != {name for name, _ in table.columns}:
        raise ValueError("typed readback columns differ from schema")
    occurrence = table.name in _OCCURRENCE_TABLES
    return {name: _normalize(row[name], kind, occurrence=occurrence)
            for name, kind in table.columns}


class SignalColdStorage(Protocol):
    def read_occurrence_rows(self, table_name: str, *, event_id: str) -> list[Mapping[str, Any]]: ...
    def read_cursor_rows(self, table_name: str, *, session_key: str,
                         batch_sequence: int) -> list[Mapping[str, Any]]: ...
    def list_cursor_commits(self, *, session_key: str) -> list[Mapping[str, Any]]: ...


class ColdOccurrenceAuthority:
    def __init__(self, storage: SignalColdStorage, catalogs: Mapping[str, Any]) -> None:
        self._storage = storage
        self._catalogs = catalogs

    def read_exact(self, event_id: str) -> dict[str, Any] | None:
        parent_rows = self._storage.read_occurrence_rows(PARENT.name, event_id=event_id)
        if not parent_rows:
            return None
        if len(parent_rows) != 1:
            raise ValueError("duplicate typed occurrence parent")
        parent = canonical_row(PARENT, parent_rows[0])
        if parent["event_id"] != event_id:
            raise ValueError("typed occurrence parent identity differs")
        source = self._catalogs.get(parent["signal_stream_id"])
        if source is None:
            raise ValueError("typed occurrence catalog unavailable at cold read")
        rows = {"parent": parent}
        for table, family in ((RULE, "rules"), (COLUMN, "columns"), (FIELD, "fields")):
            raw = self._storage.read_occurrence_rows(table.name, event_id=event_id)
            rows[family] = sorted((canonical_row(table, row) for row in raw),
                                  key=lambda row: row["ordinal"])
        return restore_typed_occurrence(rows, source.catalog, source.stream, source.columns)


@dataclass(frozen=True)
class CommittedCursorHead:
    session_key: str
    sequence: int
    content_hash: str
    states: dict[str, Any]
    admissions: dict[str, Any]
    occurrence_count: int


def recover_committed_head(
    storage: SignalColdStorage, *, session_key: str, configuration_revision: str,
    source_revision: str, catalogs: Mapping[str, Any],
) -> CommittedCursorHead:
    """Read all committed batches; reject duplicate, gap, or conflicting rows."""
    listed = [canonical_row(COMMIT, row)
              for row in storage.list_cursor_commits(session_key=session_key)]
    listed.sort(key=lambda row: row["batch_sequence"])
    if [row["batch_sequence"] for row in listed] != list(range(1, len(listed) + 1)):
        raise ValueError("typed Signal Stream committed cursor is duplicate or gapped")
    authority = ColdOccurrenceAuthority(storage, catalogs)
    states: dict[str, Any] = {}
    admissions: dict[str, Any] = {}
    head = "0" * 64
    seen: set[str] = set()
    for sequence, listed_commit in enumerate(listed, start=1):
        rows = {}
        for table, family in ((STATE_DELTA, "state_delta"),
                              (OCCURRENCE_REF, "occurrence_ref"),
                              (ADMISSION_DELTA, "admission_delta")):
            raw = storage.read_cursor_rows(table.name, session_key=session_key,
                                           batch_sequence=sequence)
            rows[family] = [canonical_row(table, row) for row in raw]
            if family == "state_delta":
                rows[family].sort(key=lambda row: (row["signal_stream_id"], row["ticker"]))
            elif family == "occurrence_ref":
                rows[family].sort(key=lambda row: row["ordinal"])
            else:
                rows[family].sort(key=lambda row: (row["watchlist_id"], row["ticker"]))
        commits = storage.read_cursor_rows(COMMIT.name, session_key=session_key,
                                           batch_sequence=sequence)
        if len(commits) != 1:
            raise ValueError("missing or duplicate typed cursor commit fence")
        rows["commit"] = canonical_row(COMMIT, commits[0])
        if rows["commit"] != listed_commit:
            raise ValueError("typed cursor commit listing differs from cold read")
        states, admissions, occurrences, head = recover_cursor_batch(
            states, rows, occurrence_authority=authority, session_key=session_key,
            expected_sequence=sequence, previous_commit_hash=head,
            configuration_revision=configuration_revision,
            source_revision=source_revision, before_admissions=admissions,
        )
        ids = {row["event_id"] for row in occurrences}
        if ids & seen:
            raise ValueError("duplicate typed occurrence across committed batches")
        seen.update(ids)
    return CommittedCursorHead(session_key, len(listed), head,
                               states, admissions, len(seen))
