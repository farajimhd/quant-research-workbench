"""Exact ClickHouse transport for a Keeper-owned live assignment base row.

This adapter is only a transport. The caller must hold the assignment Keeper
claim and use ``publish_base_revision`` to attest child commits and the head.
No route constructs this adapter until that publication path is enabled.
"""
from __future__ import annotations

from datetime import UTC, datetime
import json
import re
from typing import Any, Mapping
from uuid import NAMESPACE_URL, UUID, uuid5

from src.backend.live_assignment_base_revision import (
    BASE_REVISION, _time, recover_base_revision,
)
from src.trading_runtime.arte_journal_writer import _literal
from src.trading_runtime.journal_contract import canonical_json


_COLUMNS = tuple(name for name, _ in BASE_REVISION.columns)
_BOOLEAN = frozenset(name for name, kind in BASE_REVISION.columns if kind == "Bool")
_INTEGERS = frozenset(name for name, kind in BASE_REVISION.columns
                      if kind.startswith("UInt"))
_TIMES = frozenset(name for name, kind in BASE_REVISION.columns
                   if kind.startswith("DateTime64"))
_UUIDS = frozenset(name for name, kind in BASE_REVISION.columns if kind == "UUID")


def _stored_row(value: Mapping[str, Any]) -> dict[str, Any]:
    if set(value) != set(_COLUMNS):
        raise ValueError("assignment base readback columns differ")
    row = dict(value)
    for name in _BOOLEAN:
        if type(row[name]) is bool:
            continue
        if type(row[name]) is not int or row[name] not in (0, 1):
            raise ValueError(f"assignment base {name} is not Boolean")
        row[name] = bool(row[name])
    for name in _INTEGERS:
        if type(row[name]) is not int or row[name] < 0:
            raise ValueError(f"assignment base {name} is not unsigned")
    for name in _TIMES:
        raw = row[name]
        if type(raw) is not str:
            raise ValueError(f"assignment base {name} is not a timestamp")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}\.\d{6}(?:\+00:00|Z)?", raw) is None:
            raise ValueError(f"assignment base {name} exceeds DateTime64(6)")
        at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        # DateTime64(6, 'UTC') has an explicit storage timezone; ClickHouse's
        # JSONEachRow spelling commonly omits an offset.
        row[name] = _time(at.replace(tzinfo=UTC) if at.tzinfo is None else at)
    for name in _UUIDS:
        row[name] = str(UUID(str(row[name])))
    recover_base_revision(
        [row], expected_assignment_id=row["assignment_id"],
        expected_sequence=row["revision_sequence"],
        expected_hash=row["content_hash"],
        parameter_content_hash=row["parameter_content_hash"],
        state_content_hash=row["state_content_hash"],
        previous_revision_hash=row["previous_revision_hash"],
    )
    return row


class ClickHouseAssignmentBaseStorage:
    """One explicit client, no credential discovery or hidden retry."""

    def __init__(self, client: Any) -> None:
        if not callable(getattr(client, "execute", None)):
            raise TypeError("assignment base storage needs a ClickHouse client")
        self.client = client

    def read_base_rows(self, assignment_id: str) -> list[dict[str, Any]]:
        if type(assignment_id) is not str or not assignment_id:
            raise ValueError("assignment base identity is required")
        sql = (f"SELECT {','.join(_COLUMNS)} FROM arte.{BASE_REVISION.name} "
               f"WHERE assignment_id={_literal(assignment_id)} "
               "ORDER BY revision_sequence FORMAT JSONEachRow")
        values = [json.loads(line) for line in self.client.execute(sql).splitlines()
                  if line.strip()]
        if any(value.get("assignment_id") != assignment_id for value in values):
            raise ValueError("assignment base query returned another identity")
        rows = [_stored_row(value) for value in values]
        return rows

    def insert_base(self, row: Mapping[str, Any]) -> None:
        canonical = _stored_row(row)
        token = (f"assignment-base:{canonical['assignment_id']}:"
                 f"{canonical['revision_sequence']}:{canonical['content_hash']}")
        query_id = str(uuid5(NAMESPACE_URL, token))
        sql = (
            f"INSERT INTO arte.{BASE_REVISION.name} ({','.join(_COLUMNS)}) "
            "SETTINGS async_insert=1,wait_for_async_insert=1,insert_deduplicate=1,"
            f"insert_deduplication_token={_literal(token)} FORMAT JSONEachRow\n"
            f"{canonical_json(canonical)}"
        )
        # A failed response may be a successful server-side write. The owner
        # publisher must hold its Keeper claim and never retry blindly.
        self.client.execute(sql, query_id=query_id)
