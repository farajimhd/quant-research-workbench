"""Bounded SELECT-only storage view for normalized Signal Stream source rows.

The writer uses a separate principal and RegisteredSignalSourceStorage; this
reader never repairs, deduplicates, creates, or inserts source products.
"""
from __future__ import annotations

from datetime import date
import json
import re
from typing import Any, Mapping

from src.backend.signal_stream_typed_cursor import (
    COMMIT, TABLES as CURSOR_TABLES,
)
from src.backend.signal_stream_typed_occurrence import TABLES as OCCURRENCE_TABLES
from src.backend.signal_stream_typed_readback import ColdOccurrenceAuthority, canonical_row
from src.backend.strategy_one_live_signal_schema import strategy_one_signal_table


_OCCURRENCE = {table.name: table for table in OCCURRENCE_TABLES}
_CURSOR = {table.name: table for table in CURSOR_TABLES}
_TABLES = {**_OCCURRENCE, **_CURSOR}


def _session(value: str) -> str:
    try:
        if (type(value) is not str
                or date.fromisoformat(value).isoformat() != value):
            raise ValueError
        return value
    except ValueError as exc:
        raise ValueError("Signal source read session is invalid") from exc


def _event(value: str) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("Signal source read event identity is invalid")
    return value


class ClickHouseSignalSourceReadStorage:
    """One authoritative table contract, with explicit per-query row limits."""

    def __init__(self, client: Any, catalogs: Mapping[str, Any], *,
                 max_family_rows: int = 65_536,
                 max_commits: int = 100_000,
                 strategy_one: bool = False) -> None:
        if (type(max_family_rows) is not int or not 1 <= max_family_rows <= 65_536
                or type(max_commits) is not int or not 1 <= max_commits <= 100_000
                or type(strategy_one) is not bool):
            raise ValueError("Signal source read bounds are invalid")
        self._client = client
        self._catalogs = catalogs
        self._max_family_rows = max_family_rows
        self._max_commits = max_commits
        self.strategy_one = strategy_one

    def _physical(self, logical_name: str) -> str:
        return (strategy_one_signal_table(logical_name) if self.strategy_one
                else logical_name)

    def _rows(self, table_name: str, predicate: str, maximum: int):
        table = _TABLES.get(table_name)
        if table is None:
            raise ValueError("Signal source read table is not registered")
        columns = ",".join(name for name, _ in table.columns)
        sql = (f"SELECT {columns} FROM arte.{self._physical(table_name)} WHERE {predicate} "
               f"LIMIT {maximum + 1} FORMAT JSONEachRow")
        rows = [canonical_row(table, json.loads(line)) for line in
                self._client.execute(sql).splitlines() if line.strip()]
        if len(rows) > maximum:
            raise RuntimeError("Signal source read exceeds bounded family")
        return rows

    def read_occurrence_rows(self, table_name: str, *, event_id: str):
        if table_name not in _OCCURRENCE:
            raise ValueError("Signal source occurrence table is invalid")
        return self._rows(table_name, f"event_id='{_event(event_id)}'",
                          self._max_family_rows)

    def read_cursor_rows(self, table_name: str, *, session_key: str,
                         batch_sequence: int):
        if table_name not in _CURSOR or type(batch_sequence) is not int or batch_sequence < 1:
            raise ValueError("Signal source cursor scope is invalid")
        return self._rows(
            table_name,
            f"session_key=toDate('{_session(session_key)}') "
            f"AND batch_sequence={batch_sequence}", self._max_family_rows)

    def list_cursor_commits(self, *, session_key: str):
        return self._rows(COMMIT.name,
                          f"session_key=toDate('{_session(session_key)}')",
                          self._max_commits)

    def read_exact_prior_occurrence(self, event_id: str):
        return ColdOccurrenceAuthority(self, self._catalogs).read_exact(_event(event_id))

    def has_any_source_rows(self, *, session_key: str) -> bool:
        """Fail new-gate initialization if any typed row already names the day."""
        day = _session(session_key)
        for table_name in _TABLES:
            sql = (f"SELECT 1 FROM arte.{self._physical(table_name)} "
                   f"WHERE session_key=toDate('{day}') LIMIT 1 FORMAT JSONEachRow")
            if self._client.execute(sql).strip():
                return True
        return False
