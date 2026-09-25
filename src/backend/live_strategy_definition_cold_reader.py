"""Inactive CH+Keeper cold bridge for installed definition enablement.

This reader never opens SQLite or writes ClickHouse. A caller must separately
hold an exclusive configuration admission fence before using its result for
execution; two reads only detect changes during this cold read.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Mapping, Protocol

from src.backend.live_strategy_definition_authority import recover_installed_definition


ROOT = "/trading/live-strategy-definition-head/v1"


def _hex(value: str) -> str:
    if type(value) is not str or len(value) != 64 or any(
            char not in "0123456789abcdef" for char in value):
        raise ValueError("definition head hash is invalid")
    return value


@dataclass(frozen=True)
class DefinitionHead:
    strategy_id: str
    strategy_revision: int
    definition_content_hash: str
    change_sequence: int
    change_content_hash: str
    keeper_version: int


class DefinitionHeadReader(Protocol):
    def read_head(self, strategy_id: str, strategy_revision: int) -> DefinitionHead: ...


class DefinitionRowReader(Protocol):
    def read_definition_rows(self, *, strategy_id: str,
                             strategy_revision: int) -> list[Mapping[str, Any]]: ...
    def read_enable_change_rows(self, *, strategy_id: str,
                                strategy_revision: int) -> list[Mapping[str, Any]]: ...


class KeeperDefinitionHeadReader:
    """Read a persistent scalar CAS head from a connected local Keeper client."""

    def __init__(self, client: Any, *, endpoint: str) -> None:
        if endpoint != "127.0.0.1:9181":
            raise ValueError("definition Keeper requires local loopback endpoint")
        self._client = client

    @staticmethod
    def path(strategy_id: str, strategy_revision: int) -> str:
        if (type(strategy_id) is not str or not strategy_id
                or any(char in strategy_id for char in ("\r", "\n", "\x00"))
                or type(strategy_revision) is not int or strategy_revision < 1):
            raise ValueError("definition head identity is invalid")
        identity = sha256(f"{strategy_id}\x00{strategy_revision}".encode()).hexdigest()
        return f"{ROOT}/{identity}/head"

    def read_head(self, strategy_id: str, strategy_revision: int) -> DefinitionHead:
        if not getattr(self._client, "connected", False) or self._client.client_id is None:
            raise RuntimeError("definition Keeper session is unavailable")
        try:
            raw, stat = self._client.get(self.path(strategy_id, strategy_revision))
            fields = raw.decode("utf-8").split("\n")
            if len(fields) != 6 or fields[0] != "1":
                raise ValueError
            stored_id, stored_revision, definition_hash, sequence, change_hash = fields[1:]
            if (stored_id != strategy_id or int(stored_revision) != strategy_revision
                    or int(sequence) < 1 or str(int(sequence)) != sequence
                    or str(int(stored_revision)) != stored_revision
                    or type(stat.version) is not int or stat.version < 0):
                raise ValueError
            return DefinitionHead(strategy_id, strategy_revision, _hex(definition_hash),
                                  int(sequence), _hex(change_hash), stat.version)
        except Exception as exc:
            raise ValueError("definition Keeper head is missing or corrupt") from exc


def cold_read_installed_definition(
    rows: DefinitionRowReader, keeper: DefinitionHeadReader, *,
    strategy_id: str, strategy_revision: int,
) -> dict[str, Any]:
    """Verify all rows against one stable Keeper head and installed code."""
    first = keeper.read_head(strategy_id, strategy_revision)
    if (not isinstance(first, DefinitionHead) or first.strategy_id != strategy_id
            or first.strategy_revision != strategy_revision):
        raise ValueError("definition Keeper head identity differs")
    definitions = rows.read_definition_rows(
        strategy_id=strategy_id, strategy_revision=strategy_revision)
    changes = rows.read_enable_change_rows(
        strategy_id=strategy_id, strategy_revision=strategy_revision)
    if len(definitions) != 1 or definitions[0].get("content_hash") != first.definition_content_hash:
        raise ValueError("definition row differs from Keeper head")
    recovered = recover_installed_definition(
        definitions, changes, expected_change_sequence=first.change_sequence,
        expected_change_hash=first.change_content_hash)
    if keeper.read_head(strategy_id, strategy_revision) != first:
        raise RuntimeError("definition Keeper head changed during cold read")
    return recovered
