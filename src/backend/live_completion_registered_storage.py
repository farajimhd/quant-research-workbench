"""Normalized ClickHouse completion storage with registered INSERT transport.

Only the dedicated completion worker may use this adapter. The market path
submits immutable work and never waits for ClickHouse or Keeper I/O.
"""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Mapping

from src.backend.live_completion_insert_dispatch import (
    CompletionInsertDispatch, completion_insert_run_id,
)
from src.backend.live_signal_completion_keeper import completion_resource
from src.backend.live_signal_work_completion import COMPLETION
from src.backend.signal_stream_typed_readback import canonical_row
from src.trading_runtime.journal_contract import canonical_json


class RegisteredCompletionStorage:
    registered_transport = True

    def __init__(self, read_client: Any, insert_client: Any,
                 dispatch: CompletionInsertDispatch, *, session_key: str) -> None:
        self.run_id = completion_insert_run_id(session_key)
        self._session_key = session_key
        self._read = read_client
        self._insert = insert_client
        self._dispatch = dispatch

    def _rows(self, sql: str) -> list[dict[str, Any]]:
        return [canonical_row(COMPLETION, json.loads(line))
                for line in self._read.execute(sql).splitlines() if line.strip()]

    def initialize_new_session(self) -> None:
        sql = ("SELECT 1 FROM arte.live_signal_work_completion_typed_v1 "
               f"WHERE session_key=toDate('{self._session_key}') "
               "LIMIT 1 FORMAT JSONEachRow")
        self._dispatch.initialize_new_session(
            self.run_id, has_ch_rows=bool(self._read.execute(sql).strip()))

    def insert_completion_row(self, row: Mapping[str, Any]) -> None:
        normalized = canonical_row(COMPLETION, row)
        if normalized["session_key"] != self._session_key:
            raise ValueError("completion INSERT session differs")
        resource = completion_resource(
            normalized["session_key"], normalized["source_batch_sequence"],
            normalized["ordinal"], normalized["delivery_id"])
        token = "completion:" + sha256(
            f"{self.run_id}\x00{resource}\x00{normalized['content_hash']}".encode()
        ).hexdigest()
        columns = ",".join(name for name, _ in COMPLETION.columns)
        sql = (f"INSERT INTO arte.{COMPLETION.name} ({columns}) SETTINGS "
               "async_insert=1,wait_for_async_insert=1,insert_deduplicate=1,"
               f"insert_deduplication_token='{token}' FORMAT JSONEachRow\n"
               + canonical_json(normalized))
        self._dispatch.execute(
            self._insert, run_id=self.run_id, resource=resource,
            row_hash=normalized["content_hash"], sql=sql, token=token)

    def read_completion_rows(self, *, session_key: str,
                             source_batch_sequence: int,
                             ordinal: int) -> list[Mapping[str, Any]]:
        if (session_key != self._session_key
                or type(source_batch_sequence) is not int
                or source_batch_sequence < 1
                or type(ordinal) is not int or ordinal < 0):
            raise ValueError("completion read scope differs")
        columns = ",".join(name for name, _ in COMPLETION.columns)
        return self._rows(
            f"SELECT {columns} FROM arte.{COMPLETION.name} "
            f"WHERE session_key=toDate('{session_key}') "
            f"AND source_batch_sequence={source_batch_sequence} "
            f"AND ordinal={ordinal} LIMIT 2 FORMAT JSONEachRow")

    def seal_completion_row(self, row: Mapping[str, Any], *, keeper: Any) -> str:
        if row.get("session_key") != self._session_key:
            raise ValueError("completion seal session differs")
        return self._dispatch.seal_readback(
            run_id=self.run_id, row=row, storage=self, keeper=keeper)

    def assert_existing_completion_row(self, row: Mapping[str, Any]) -> None:
        if row.get("session_key") != self._session_key:
            raise ValueError("completion prior row session differs")
        resource = completion_resource(
            row["session_key"], row["source_batch_sequence"],
            row["ordinal"], row["delivery_id"])
        self._dispatch.assert_sealed_resource(
            self.run_id, resource=resource, row_hash=row["content_hash"])

    def close_for_cold(self) -> None:
        self._dispatch.close_for_cold(self.run_id)

    def assert_cold_receipts(self, expected: Mapping[str, str]) -> None:
        self._dispatch.assert_cold_receipts(self.run_id, expected)
