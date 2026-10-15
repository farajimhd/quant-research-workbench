"""Bounded SELECT-only reader for normalized live dispatch facts.

No writer, DDL, fallback journal, or disk artifact is available here. Cold
recovery validates the returned rows against the source and Keeper receipts.
"""
from __future__ import annotations

from datetime import date
import json
from typing import Any

from src.backend.signal_dispatch_typed_cursor import (
    ACK_COMMIT, DISPATCH_TABLES, INTENT_COMMIT,
)
from src.backend.signal_stream_typed_readback import canonical_row
from src.backend.strategy_one_live_signal_schema import strategy_one_signal_table


_TABLES = {table.name: table for table in DISPATCH_TABLES}
_COMMITS = frozenset((INTENT_COMMIT.name, ACK_COMMIT.name))


def _session(value: str) -> str:
    try:
        if (type(value) is not str
                or date.fromisoformat(value).isoformat() != value):
            raise ValueError
        return value
    except ValueError as exc:
        raise ValueError("dispatch read session is invalid") from exc


class ClickHouseDispatchColdStorage:
    """Primary-key scoped rows with explicit per-family recovery bounds."""

    def __init__(self, client: Any, *, max_family_rows: int = 65_536,
                 max_commits: int = 100_000,
                 strategy_one: bool = False, source_context=None) -> None:
        if (type(max_family_rows) is not int or not 1 <= max_family_rows <= 65_536
                or type(max_commits) is not int or not 1 <= max_commits <= 100_000
                or type(strategy_one) is not bool):
            raise ValueError("dispatch cold read bounds are invalid")
        if source_context is not None:
            from .signal_execution_context import SignalExecutionContext
            if type(source_context) is not SignalExecutionContext or strategy_one:
                raise ValueError("Contextual source requires its concrete generic run context")
        self.source_context = source_context
        self._context_seal_verified = False
        self._client = client
        self._max_family_rows = max_family_rows
        self._max_commits = max_commits
        self.strategy_one = strategy_one

    def verify_source_context(self) -> None:
        if self.source_context is not None and not self._context_seal_verified:
            from .signal_context_transport import verify_context_seal
            verify_context_seal(self._client, self.source_context)
            self._context_seal_verified = True

    def _context_predicate(self) -> str:
        self.verify_source_context()
        if self.source_context is None:
            return ""
        return f"source_context_id='{self.source_context.context_id}' AND "

    def _physical(self, logical_name: str) -> str:
        if self.source_context is not None:
            from .signal_context_schema import context_table
            return context_table(logical_name).name
        return (strategy_one_signal_table(logical_name) if self.strategy_one
                else logical_name)

    def _rows(self, table_name: str, predicate: str, maximum: int):
        table = _TABLES.get(table_name)
        if table is None:
            raise ValueError("dispatch cold read table is not registered")
        if self.source_context is not None:
            self.verify_source_context()
            from .signal_context_transport import read_context_rows
            return read_context_rows(self._client, self.source_context, table, predicate, maximum)
        columns = ",".join(name for name, _ in table.columns)
        sql = (f"SELECT {columns} FROM arte.{self._physical(table_name)} WHERE {predicate} "
               f"LIMIT {maximum + 1} FORMAT JSONEachRow")
        rows = [canonical_row(table, json.loads(line)) for line in
                self._client.execute(sql).splitlines() if line.strip()]
        if len(rows) > maximum:
            raise RuntimeError("dispatch cold read exceeds bounded family")
        return rows

    def read_dispatch_rows(self, table_name: str, *, session_key: str,
                           source_batch_sequence: int):
        if (type(source_batch_sequence) is not int
                or source_batch_sequence < 1):
            raise ValueError("dispatch cold batch sequence is invalid")
        return self._rows(
            table_name, f"session_key=toDate('{_session(session_key)}') "
            f"AND source_batch_sequence={source_batch_sequence}",
            self._max_family_rows)

    def list_dispatch_commits(self, table_name: str, *, session_key: str):
        if table_name not in _COMMITS:
            raise ValueError("dispatch cold commit table is invalid")
        return self._rows(
            table_name, f"session_key=toDate('{_session(session_key)}')",
            self._max_commits)

    def has_any_dispatch_rows(self, *, session_key: str) -> bool:
        day = _session(session_key)
        for table_name in _TABLES:
            sql = (f"SELECT 1 FROM arte.{self._physical(table_name)} "
                   f"WHERE {self._context_predicate()}session_key=toDate('{day}') "
                   "LIMIT 1 FORMAT JSONEachRow")
            if self._client.execute(sql).strip():
                return True
        return False

    def has_dispatch_rows_after(self, *, session_key: str, source_batch_sequence: int) -> bool:
        """Prove a replayable tail has no uncommitted child or parent rows."""
        if type(source_batch_sequence) is not int or source_batch_sequence < 0:
            raise ValueError("dispatch inventory boundary is invalid")
        day = _session(session_key)
        for table_name in _TABLES:
            sql = (f"SELECT 1 FROM arte.{self._physical(table_name)} "
                   f"WHERE {self._context_predicate()}session_key=toDate('{day}') "
                   f"AND source_batch_sequence>{source_batch_sequence} "
                   "LIMIT 1 FORMAT JSONEachRow")
            if self._client.execute(sql).strip():
                return True
        return False
