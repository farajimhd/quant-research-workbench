"""Staged exact ClickHouse adapter for two named typed definition tables.

JSONEachRow is only the HTTP wire encoding; neither table stores a JSON
column. This adapter never connects itself and never retries an INSERT.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Protocol

from src.backend.live_strategy_definition_authority import (
    DEFINITION, ENABLE_CHANGE, _digest,
)
from src.backend.signal_stream_typed_readback import canonical_row
from src.trading_runtime.journal_contract import canonical_json


MAX_ENABLE_CHANGES = 100_000


class DefinitionClickHouseClient(Protocol):
    def execute(self, sql: str) -> str: ...


def _literal(value: str) -> str:
    if type(value) is not str or not value:
        raise ValueError("definition strategy identity is invalid")
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _identity(strategy_id: str, strategy_revision: int) -> str:
    if type(strategy_revision) is not int or not 1 <= strategy_revision < 2**32:
        raise ValueError("definition revision is invalid")
    return (f"strategy_id={_literal(strategy_id)} "
            f"AND strategy_revision={strategy_revision}")


def _validate(table: Any, row: Mapping[str, Any]) -> dict[str, Any]:
    normalized = canonical_row(table, row)
    if normalized != dict(row) or normalized["content_hash"] != _digest({
            key: value for key, value in normalized.items() if key != "content_hash"}):
        raise ValueError(f"{table.name} row differs from canonical typed content")
    if normalized["schema_version"] != 1:
        raise ValueError("definition typed schema version differs")
    _identity(normalized["strategy_id"], normalized["strategy_revision"])
    if table is ENABLE_CHANGE and not 1 <= normalized["change_sequence"] <= MAX_ENABLE_CHANGES:
        raise ValueError("definition enablement chain exceeds bound")
    return normalized


class ClickHouseDefinitionStorage:
    def __init__(self, client: DefinitionClickHouseClient) -> None:
        self._client = client

    def _read(self, table: Any, *, strategy_id: str,
              strategy_revision: int, limit: int) -> list[dict[str, Any]]:
        columns = ",".join(name for name, _ in table.columns)
        sql = (f"SELECT {columns} FROM arte.{table.name} WHERE "
               f"{_identity(strategy_id, strategy_revision)} "
               f"ORDER BY {table.order} LIMIT {limit} FORMAT JSONEachRow")
        rows = [json.loads(line) for line in self._client.execute(sql).splitlines()
                if line.strip()]
        if len(rows) > limit:
            raise ValueError("definition read exceeds row bound")
        normalized = [canonical_row(table, row) for row in rows]
        if any(row["strategy_id"] != strategy_id
               or row["strategy_revision"] != strategy_revision for row in normalized):
            raise ValueError("definition read returned foreign identity")
        return normalized

    def read_definition_rows(self, *, strategy_id: str,
                             strategy_revision: int) -> list[dict[str, Any]]:
        return self._read(DEFINITION, strategy_id=strategy_id,
                          strategy_revision=strategy_revision, limit=2)

    def read_enable_change_rows(self, *, strategy_id: str,
                                strategy_revision: int) -> list[dict[str, Any]]:
        rows = self._read(ENABLE_CHANGE, strategy_id=strategy_id,
                          strategy_revision=strategy_revision,
                          limit=MAX_ENABLE_CHANGES + 1)
        if len(rows) > MAX_ENABLE_CHANGES:
            raise ValueError("definition enablement chain exceeds bound")
        return rows

    def _insert(self, table: Any, row: Mapping[str, Any]) -> None:
        normalized = _validate(table, row)
        columns = ",".join(name for name, _ in table.columns)
        # One synchronous attempt only. A timeout is ambiguous and must be
        # reconciled against Keeper and exact row readback, never retried.
        self._client.execute(
            f"INSERT INTO arte.{table.name} ({columns}) "
            f"SETTINGS async_insert=0 FORMAT JSONEachRow\n{canonical_json(normalized)}")

    def insert_definition(self, row: Mapping[str, Any]) -> None:
        self._insert(DEFINITION, row)

    def insert_enable_change(self, row: Mapping[str, Any]) -> None:
        self._insert(ENABLE_CHANGE, row)
