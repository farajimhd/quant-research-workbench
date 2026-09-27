"""Inactive ClickHouse/Keeper cold read for the normalized v55 accounts section.

The accounts head is section-local. It is not a full approved-release head and
cannot authorize Strategy 1 order admission or replace SQLite configuration.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Mapping, Protocol

from src.backend.live_approved_accounts_typed import (
    BINDING, MODE, PARENT, TABLES, restore_accounts_section,
)
from src.trading_runtime.arte_journal_schema import storage_preflight
from src.trading_runtime.keeper_session import ManagedKeeperSession


ROOT = "/trading/live-approved-accounts/v1"
_ORDERS = {PARENT.name: "configuration_revision_id",
           BINDING.name: "ordinal", MODE.name: "binding_ordinal,mode_ordinal"}


def _identity(value: str) -> str:
    if type(value) is not str or not value or any(c in value for c in "\r\n\x00"):
        raise ValueError("approved accounts revision identity is invalid")
    return value


def _hash(value: str) -> str:
    if type(value) is not str or len(value) != 64 or any(
            c not in "0123456789abcdef" for c in value):
        raise ValueError("approved accounts head hash is invalid")
    return value


def _literal(value: str) -> str:
    return "'" + _identity(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


@dataclass(frozen=True, slots=True)
class AccountsSectionHead:
    configuration_revision_id: str
    content_hash: str
    keeper_version: int


class AccountsHeadReader(Protocol):
    def read_head(self, configuration_revision_id: str) -> AccountsSectionHead: ...


class KeeperAccountsHeadReader:
    """Read an existing scalar Keeper head; never create or advance it."""

    def __init__(self, session: ManagedKeeperSession) -> None:
        if not isinstance(session, ManagedKeeperSession):
            raise TypeError("approved accounts head requires managed Keeper session")
        self._session = session

    @staticmethod
    def path(configuration_revision_id: str) -> str:
        digest = sha256(_identity(configuration_revision_id).encode()).hexdigest()
        return f"{ROOT}/{digest}/head"

    def read_head(self, configuration_revision_id: str) -> AccountsSectionHead:
        session = self._session
        client = session.client
        if (not session._connected or session._closed or not client.connected
                or client.client_id is None
                or getattr(getattr(client, "client_state", None), "name", "CONNECTED")
                   == "CONNECTED_RO"):
            raise RuntimeError("approved accounts Keeper session is unavailable")
        generation, client_id = session._generation, client.client_id
        try:
            raw, stat = client.get(self.path(configuration_revision_id))
            fields = raw.decode("utf-8").split("\n")
            if (len(fields) != 3 or fields[:2] != ["1", configuration_revision_id]
                    or type(stat.version) is not int or stat.version < 0):
                raise ValueError
            head = AccountsSectionHead(configuration_revision_id,
                                       _hash(fields[2]), stat.version)
        except Exception as exc:
            raise ValueError("approved accounts Keeper head missing or corrupt") from exc
        if (not session._connected or session._closed
                or session._generation != generation or client.client_id != client_id
                or not client.connected):
            raise RuntimeError("approved accounts Keeper session changed during read")
        return head


class ClickHouseAccountsRows:
    """Exact-column bounded reads; JSONEachRow is wire encoding only."""

    def __init__(self, client: Any) -> None:
        storage_preflight(client, tables=TABLES)
        self._client = client

    def read(self, table: Any, *, configuration_revision_id: str,
             limit: int) -> list[Mapping[str, Any]]:
        if table not in TABLES or type(limit) is not int or not 1 <= limit <= 100_001:
            raise ValueError("approved accounts read table or bound is invalid")
        columns = ",".join(name for name, _ in table.columns)
        sql = (f"SELECT {columns} FROM arte.{table.name} "
               f"WHERE configuration_revision_id={_literal(configuration_revision_id)} "
               f"ORDER BY {_ORDERS[table.name]} LIMIT {limit} FORMAT JSONEachRow")
        output = self._client.execute(sql)
        result = [json.loads(line) for line in output.splitlines() if line.strip()]
        expected = {name for name, _ in table.columns}
        if (len(result) > limit or any(type(row) is not dict or set(row) != expected
                                       for row in result)):
            raise ValueError("approved accounts ClickHouse row columns differ")
        return result


def cold_read_approved_accounts(
    rows: ClickHouseAccountsRows, keeper: AccountsHeadReader, *,
    configuration_revision_id: str,
) -> dict[str, Any]:
    """Verify one section under an unchanged Keeper head, never live-admit."""
    revision = _identity(configuration_revision_id)
    first = keeper.read_head(revision)
    if (not isinstance(first, AccountsSectionHead)
            or first.configuration_revision_id != revision):
        raise ValueError("approved accounts Keeper head scope differs")
    parent_rows = rows.read(PARENT, configuration_revision_id=revision,
                            limit=2)
    if len(parent_rows) != 1 or parent_rows[0]["content_hash"] != first.content_hash:
        raise ValueError("approved accounts parent differs from Keeper head")
    parent = parent_rows[0]
    binding_count, mode_count = parent["binding_count"], parent["mode_count"]
    if (type(binding_count) is not int or type(mode_count) is not int
            or not 1 <= binding_count <= 100_000
            or not 1 <= mode_count <= 100_000):
        raise ValueError("approved accounts child bounds differ")
    bindings = rows.read(BINDING, configuration_revision_id=revision,
                         limit=binding_count + 1)
    modes = rows.read(MODE, configuration_revision_id=revision,
                      limit=mode_count + 1)
    if len(bindings) != binding_count or len(modes) != mode_count:
        raise ValueError("approved accounts child count differs")
    restored = restore_accounts_section(parent, bindings, modes)
    if keeper.read_head(revision) != first:
        raise RuntimeError("approved accounts Keeper head changed during cold read")
    return restored
