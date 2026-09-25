"""Exact ClickHouse transport for Keeper-owned assignment state snapshots.

JSONEachRow is only the wire format: every persisted field has a scalar typed
column. This adapter does not retry an ambiguous INSERT or create any table.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from hashlib import sha256
import json
import math
import re
from typing import Any, Mapping
from uuid import NAMESPACE_URL, UUID, uuid5

from src.backend.live_assignment_state_snapshot import STATE_TABLES
from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.arte_journal_writer import _literal
from src.trading_runtime.journal_contract import canonical_json


_TABLES = {table.name: table for table in STATE_TABLES}
_IDENTITY = ("run_id", "assignment_id", "revision", "snapshot_id", "session")


def _column_value(name: str, kind: str, value: Any) -> Any:
    if kind.startswith("Nullable(") and kind.endswith(")"):
        return None if value is None else _column_value(name, kind[9:-1], value)
    if value is None:
        raise ValueError(f"State {name} cannot be null")
    if kind == "Bool":
        if type(value) is bool:
            return value
        if type(value) is int and value in (0, 1):
            return bool(value)
    elif kind.startswith("UInt") or kind.startswith("Int"):
        match = re.fullmatch(r"(U?Int)(8|16|32|64)", kind)
        if match is not None and type(value) is int:
            bits = int(match.group(2))
            low = 0 if match.group(1) == "UInt" else -(1 << (bits - 1))
            high = (1 << bits) - 1 if match.group(1) == "UInt" else (1 << (bits - 1)) - 1
            if low <= value <= high:
                return value
    elif kind == "Float64":
        if type(value) in (int, float) and math.isfinite(value):
            return float(value)
    elif kind == "UUID":
        if isinstance(value, str):
            return str(UUID(value))
    elif kind == "Date":
        if isinstance(value, str) and date.fromisoformat(value).isoformat() == value:
            return value
    elif kind.startswith("DateTime64(6, 'UTC')"):
        if (isinstance(value, str) and re.fullmatch(
                r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}\.\d{6}(?:\+00:00|Z)?",
                value)):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            if parsed.utcoffset().total_seconds() == 0:
                return parsed.isoformat(timespec="microseconds")
    elif kind == "FixedString(64)":
        if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value):
            return value
    elif kind in {"String", "LowCardinality(String)"}:
        if isinstance(value, str) and "\x00" not in value:
            return value
    raise ValueError(f"State {name} is not a valid {kind}")


def _row(table: TableContract, value: Mapping[str, Any]) -> dict[str, Any]:
    if set(value) != {name for name, _ in table.columns}:
        raise ValueError(f"State {table.name} columns differ")
    return {name: _column_value(name, kind, value[name])
            for name, kind in table.columns}


def _identity_value(table: TableContract, identity: Mapping[str, Any]) -> dict[str, Any]:
    if set(identity) != set(_IDENTITY):
        raise ValueError("State snapshot identity is incomplete")
    kinds = dict(table.columns)
    aliases = {"state_revision": "revision", "snapshot_session": "session"}
    relevant = {}
    for column in ("run_id", "assignment_id", "revision", "state_revision",
                   "snapshot_id", "session", "snapshot_session"):
        # The clock's nullable ``session`` is strategy state, not the
        # snapshot partition key. ``snapshot_session`` is its identity.
        if column == "session" and "snapshot_session" in kinds:
            continue
        if column in kinds:
            source = aliases.get(column, column)
            relevant[column] = _column_value(column, kinds[column], identity[source])
    if "assignment_id" not in relevant or not ({"revision", "state_revision"} & relevant.keys()):
        raise ValueError("State table lacks assignment revision identity")
    return relevant


class ClickHouseAssignmentStateStorage:
    """Transport only; Keeper claim and late commit are owned by the publisher."""

    def __init__(self, client: Any) -> None:
        if not callable(getattr(client, "execute", None)):
            raise TypeError("state storage needs a ClickHouse client")
        self.client = client

    def read(self, table: str, identity: Mapping[str, Any]) -> list[dict[str, Any]]:
        contract = _TABLES.get(table)
        if contract is None:
            raise ValueError("State storage table is not allowlisted")
        expected = _identity_value(contract, identity)
        where = " AND ".join(f"{name}={_literal(str(value))}" for name, value in expected.items())
        columns = ",".join(name for name, _ in contract.columns)
        sql = (f"SELECT {columns} FROM arte.{table} WHERE {where} "
               "FORMAT JSONEachRow")
        raw = self.client.execute(sql)
        if not isinstance(raw, str):
            raise ValueError("State storage returned a non-text response")
        rows = [_row(contract, json.loads(line)) for line in raw.splitlines() if line.strip()]
        if any(any(row[name] != value for name, value in expected.items()) for row in rows):
            raise ValueError("State query returned another snapshot identity")
        return rows

    def insert(self, table: str, rows: list[dict[str, Any]]) -> None:
        contract = _TABLES.get(table)
        if contract is None or not rows:
            raise ValueError("State INSERT requires one allowlisted nonempty family")
        normalized = [_row(contract, row) for row in rows]
        first = normalized[0]
        identity = {name: first[name] for name in ("run_id", "assignment_id", "revision",
                    "state_revision", "snapshot_id", "session", "snapshot_session") if name in first}
        if any(any(row[name] != value for name, value in identity.items())
               for row in normalized):
            raise ValueError("State INSERT mixes snapshot identities")
        body = "\n".join(canonical_json(row) for row in normalized)
        token = f"assignment-state:{table}:{sha256(body.encode()).hexdigest()}"
        query_id = str(uuid5(NAMESPACE_URL, token))
        columns = ",".join(name for name, _ in contract.columns)
        sql = (f"INSERT INTO arte.{table} ({columns}) "
               "SETTINGS async_insert=1,wait_for_async_insert=1,insert_deduplicate=1,"
               f"insert_deduplication_token={_literal(token)} FORMAT JSONEachRow\n{body}")
        self.client.execute(sql, query_id=query_id)
