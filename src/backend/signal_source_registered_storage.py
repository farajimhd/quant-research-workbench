"""Registered INSERT adapter for the normalized Signal Stream source worker.

The caller supplies a SELECT-only storage view and a source-table INSERT client.
All writes are gated before HTTP and sealed only after exact typed readback.
No live route is enabled by constructing this adapter.
"""
from __future__ import annotations

from hashlib import sha256
import re
from typing import Any, Mapping

from src.backend.signal_source_insert_dispatch import (
    SignalSourceInsertDispatch, source_insert_run_id,
)
from src.backend.signal_stream_typed_cursor import TABLES as CURSOR_TABLES
from src.backend.signal_stream_typed_occurrence import TABLES as OCCURRENCE_TABLES
from src.backend.signal_stream_typed_readback import CommittedCursorHead, canonical_row
from src.trading_runtime.journal_contract import canonical_json


_OCCURRENCE = {table.name for table in OCCURRENCE_TABLES}
_TABLES = {table.name: table for table in (*OCCURRENCE_TABLES, *CURSOR_TABLES)}


class RegisteredSignalSourceStorage:
    """Serial adapter; every row family has one exact registered INSERT."""

    registered_transport = True

    def __init__(self, read_storage: Any, insert_client: Any,
                 dispatch: SignalSourceInsertDispatch, *, session_key: str,
                 configuration_revision: str) -> None:
        self.run_id = source_insert_run_id(session_key, configuration_revision)
        self._session_key = session_key
        self._configuration_revision = configuration_revision
        self._read_storage = read_storage
        self._client = insert_client
        self._dispatch = dispatch
        self._active_sequence: int | None = None
        self._operations = 0

    def initialize_new_session(self) -> None:
        """Only a proven empty typed source may receive a fresh Keeper gate."""
        inventory = getattr(self._read_storage, "has_any_source_rows", None)
        if inventory is None:
            raise RuntimeError("Registered source lacks complete row inventory")
        has_rows = inventory(session_key=self._session_key)
        if type(has_rows) is not bool:
            raise RuntimeError("Registered source inventory result is invalid")
        self._dispatch.initialize_new_session(self.run_id, has_ch_rows=has_rows)

    def acquire_bootstrap_barrier(self) -> tuple[int, str]:
        return self._dispatch.acquire_cold_barrier(self.run_id)

    def release_bootstrap_barrier(self, recovered: CommittedCursorHead,
                                  fence: tuple[int, str]) -> None:
        if (recovered.session_key != self._session_key
                or (recovered.sequence, recovered.content_hash) != fence):
            raise ValueError("Registered source cold prefix differs from Keeper drain")
        self._dispatch.release_cold_barrier(
            self.run_id, sequence=fence[0], commit_hash=fence[1])

    def begin_batch(self, batch: Any) -> None:
        if (self._active_sequence is not None
                or batch.session_key != self._session_key
                or batch.configuration_revision != self._configuration_revision):
            raise ValueError("Registered source batch scope differs")
        self._dispatch.begin_batch(
            self.run_id, sequence=batch.batch_sequence,
            previous_commit_hash=batch.previous_commit_hash)
        self._active_sequence = batch.batch_sequence
        self._operations = 0

    def insert_rows(self, table_name: str, rows: list[Mapping[str, Any]]) -> None:
        table = _TABLES.get(table_name)
        if (self._active_sequence is None or table is None or not rows
                or len(rows) > 256 or any(
                    set(row) != {name for name, _ in table.columns}
                    or row.get("session_key") != self._session_key
                    for row in rows)):
            raise ValueError("Registered source typed INSERT scope differs")
        sequence = self._active_sequence
        if table_name in _OCCURRENCE:
            event_ids = {row["event_id"] for row in rows}
            if len(event_ids) != 1:
                raise ValueError("Registered occurrence INSERT spans event identities")
            key = next(iter(event_ids))
        else:
            if any(row["batch_sequence"] != sequence for row in rows):
                raise ValueError("Registered cursor INSERT spans source batches")
            key = str(sequence)
        normalized = [canonical_row(table, row) for row in rows]
        digest = sha256(canonical_json(tuple(
            row["content_hash"] for row in normalized)).encode()).hexdigest()
        token = f"source:{sequence}:{table_name}:{sha256(key.encode()).hexdigest()[:16]}:{digest}"
        columns = ",".join(name for name, _ in table.columns)
        sql = (f"INSERT INTO arte.{table_name} ({columns}) SETTINGS "
               "async_insert=1,wait_for_async_insert=1,insert_deduplicate=1,"
               f"insert_deduplication_token='{token}' FORMAT JSONEachRow\n"
               + "\n".join(canonical_json(row) for row in normalized))
        self._dispatch.execute(
            self._client, run_id=self.run_id, sequence=sequence,
            table=table_name, token=token, row_hash=digest, sql=sql)
        observed = (self.read_occurrence_rows(table_name, event_id=key)
                    if table_name in _OCCURRENCE else self.read_cursor_rows(
                        table_name, session_key=self._session_key,
                        batch_sequence=sequence))
        actual = [canonical_row(table, row) for row in observed]
        order = (lambda row: row["ordinal"]) if "ordinal" in normalized[0] else (
            lambda row: canonical_json(row))
        if sorted(actual, key=order) != sorted(normalized, key=order):
            raise ValueError("Registered source INSERT readback differs")
        self._dispatch.seal_readback(
            run_id=self.run_id, sequence=sequence, table=table_name,
            token=token, row_hash=digest)
        self._operations += 1

    def finish_batch(self, batch: Any, commit_hash: str) -> None:
        if (batch.session_key != self._session_key
                or batch.batch_sequence != self._active_sequence
                or re.fullmatch(r"[0-9a-f]{64}", commit_hash) is None):
            raise ValueError("Registered source commit identity differs")
        self._dispatch.finish_batch(
            run_id=self.run_id, sequence=self._active_sequence,
            commit_hash=commit_hash, expected_operations=self._operations)
        self._active_sequence = None
        self._operations = 0

    def read_occurrence_rows(self, table_name: str, *, event_id: str):
        return self._read_storage.read_occurrence_rows(table_name, event_id=event_id)

    def read_cursor_rows(self, table_name: str, *, session_key: str,
                         batch_sequence: int):
        return self._read_storage.read_cursor_rows(
            table_name, session_key=session_key, batch_sequence=batch_sequence)

    def read_exact_prior_occurrence(self, event_id: str):
        return self._read_storage.read_exact_prior_occurrence(event_id)

    def list_cursor_commits(self, *, session_key: str):
        return self._read_storage.list_cursor_commits(session_key=session_key)
