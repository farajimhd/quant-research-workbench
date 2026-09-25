"""Typed ClickHouse transport for inactive live plan membership publication.

The transport is invoked only by the control-plane publisher. JSONEachRow is
the ClickHouse wire encoding; the three destination schemas contain no JSON,
blob, map, or array columns. No market or real-time execution path uses this.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Protocol, Sequence
from uuid import UUID

from src.backend.live_plan_membership import MEMBER, PARENT, TABLES, WATCH
from src.trading_runtime.arte_journal_schema import storage_preflight
from src.trading_runtime.journal_contract import canonical_json


class ClickHouseClient(Protocol):
    def execute(self, sql: str) -> str: ...


_CONTRACTS = {table.name: table for table in (PARENT, MEMBER, WATCH)}


def _literal(value: str) -> str:
    if type(value) is not str or not value or any(c in value for c in "\r\n\x00"):
        raise ValueError("plan membership SQL identity is invalid")
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _read(client: ClickHouseClient, table: str, where: str, order: str,
          limit: int) -> list[Mapping[str, Any]]:
    if type(limit) is not int or not 1 <= limit <= 100_001:
        raise ValueError("plan membership read bound is invalid")
    contract = _CONTRACTS[table]
    columns = ",".join(name for name, _ in contract.columns)
    output = client.execute(
        f"SELECT {columns} FROM arte.{table} WHERE {where} "
        f"ORDER BY {order} LIMIT {limit} FORMAT JSONEachRow")
    result = [json.loads(line) for line in output.splitlines() if line.strip()]
    expected = {name for name, _ in contract.columns}
    if len(result) > limit or any(type(row) is not dict or set(row) != expected
                                  for row in result):
        raise RuntimeError("plan membership ClickHouse projection differs")
    return result


def _insert(client: ClickHouseClient, table: str,
            rows: Sequence[Mapping[str, Any]], token: str) -> None:
    if not rows:
        return
    columns = tuple(name for name, _ in _CONTRACTS[table].columns)
    expected = set(columns)
    if any(type(row) is not dict or set(row) != expected for row in rows):
        raise ValueError("plan membership insert columns differ")
    body = "\n".join(canonical_json(dict(row)) for row in rows)
    client.execute(
        f"INSERT INTO arte.{table} ({','.join(columns)}) "
        "SETTINGS async_insert=1,wait_for_async_insert=1,insert_deduplicate=1,"
        f"insert_deduplication_token={_literal(token)} FORMAT JSONEachRow\n{body}")


class LivePlanMembershipClickHouseRows:
    """Projection-limited typed row port; owner/CAS belongs to the publisher."""

    def __init__(self, client: ClickHouseClient) -> None:
        # Fail before any INSERT if the explicit SSD policy or existing part
        # placement differs. The default ClickHouse disk is backup-only.
        storage_preflight(client, tables=TABLES)
        self._client = client

    def read_revisions(self, *, configuration_revision_id: str,
                       session_key: str, limit: int) -> list[Mapping[str, Any]]:
        where = (f"configuration_revision_id={_literal(configuration_revision_id)} "
                 f"AND session_key=toDate({_literal(session_key)})")
        return _read(self._client, PARENT.name, where, "membership_sequence", limit)

    def read_members(self, *, configuration_revision_id: str,
                     session_key: str, membership_sequence: int,
                     publication_id: str,
                     limit: int) -> list[Mapping[str, Any]]:
        where = self._child_where(configuration_revision_id, session_key,
                                  membership_sequence, publication_id)
        return _read(self._client, MEMBER.name, where, "assignment_id", limit)

    def read_watches(self, *, configuration_revision_id: str,
                     session_key: str, membership_sequence: int,
                     publication_id: str,
                     limit: int) -> list[Mapping[str, Any]]:
        where = self._child_where(configuration_revision_id, session_key,
                                  membership_sequence, publication_id)
        return _read(self._client, WATCH.name, where, "run_plan_id,ticker", limit)

    @staticmethod
    def _child_where(configuration_revision_id: str, session_key: str,
                     membership_sequence: int, publication_id: str) -> str:
        if (type(membership_sequence) is not int or
                not 1 <= membership_sequence <= 100_000):
            raise ValueError("plan membership sequence is invalid")
        if type(publication_id) is not str or str(UUID(publication_id)) != publication_id:
            raise ValueError("plan membership publication ID is invalid")
        return (f"configuration_revision_id={_literal(configuration_revision_id)} "
                f"AND session_key=toDate({_literal(session_key)}) "
                f"AND membership_sequence={membership_sequence} "
                f"AND publication_id=toUUID({_literal(publication_id)})")

    def insert_members(self, rows: Sequence[Mapping[str, Any]]) -> None:
        self._insert_child(MEMBER.name, rows)

    def insert_watches(self, rows: Sequence[Mapping[str, Any]]) -> None:
        self._insert_child(WATCH.name, rows)

    def insert_revision(self, row: Mapping[str, Any]) -> None:
        _insert(self._client, PARENT.name, (row,),
                f"plan-membership:{row['configuration_revision_id']}:"
                f"{row['session_key']}:{row['membership_sequence']}:parent:"
                f"{row['publication_id']}:{row['content_hash']}")

    def _insert_child(self, table: str,
                      rows: Sequence[Mapping[str, Any]]) -> None:
        if not rows:
            return
        first = rows[0]
        scope = (first["configuration_revision_id"], first["session_key"],
                 first["membership_sequence"], first["publication_id"])
        if any((row["configuration_revision_id"], row["session_key"],
                row["membership_sequence"], row["publication_id"]) != scope
               for row in rows):
            raise ValueError("plan membership child batch mixes scopes")
        token = f"plan-membership:{scope[0]}:{scope[1]}:{scope[2]}:{scope[3]}:{table}"
        _insert(self._client, table, rows, token)
