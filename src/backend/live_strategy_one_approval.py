"""Inactive, normalized approval proof for one immutable Strategy 1 release.

An approval does not enable live orders. The live launcher must also recover
the approved account, assignment, portfolio, OMS, broker, and journal heads.
Only an operator may publish rows and select one through Keeper; no runtime
principal receives INSERT or DDL authority for this table.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re
from typing import Any, Mapping, Protocol
from uuid import UUID

from src.backend.backtest_strategy_one_configuration import (
    CertifiedStrategyOneConfiguration,
)
from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.keeper_session import ManagedKeeperSession
from src.trading_runtime.strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER


TABLE = TableContract(
    "live_strategy_one_approval_v1",
    (("schema_version", "UInt16"), ("approval_id", "UUID"),
     ("mode", "LowCardinality(String)"), ("release_attempt_id", "UUID"),
     ("release_content_hash", "FixedString(64)"),
     ("release_node_hash", "FixedString(64)"),
     ("approved_at_us", "UInt64"), ("approver_id", "String"),
     ("content_hash", "FixedString(64)")),
    "cityHash64(release_attempt_id) % 16", "mode, approval_id",
)
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_MODES = frozenset({"paper", "live"})


def _uuid(value: Any) -> str:
    if type(value) is not str:
        raise ValueError("Strategy 1 approval UUID is invalid")
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise ValueError("Strategy 1 approval UUID is invalid") from exc


def _hash(value: Any) -> str:
    if type(value) is not str or _HEX.fullmatch(value) is None:
        raise ValueError("Strategy 1 approval hash is invalid")
    return value


def _content_hash(row: Mapping[str, Any]) -> str:
    return sha256(canonical_json(dict(row)).encode("utf-8")).hexdigest()


def approval_row(*, approval_id: str, mode: str,
                 release: CertifiedStrategyOneConfiguration,
                 approved_at_us: int, approver_id: str) -> dict[str, Any]:
    """Project one immutable scalar operator decision, not a copied config."""
    strategy = dict(release.payload.get("strategy") or {}) if isinstance(
        release, CertifiedStrategyOneConfiguration) else {}
    if (type(mode) is not str or mode not in _MODES
            or not isinstance(release, CertifiedStrategyOneConfiguration)
            or strategy.get("strategy_id") != STRATEGY_ID
            or strategy.get("strategy_number") != STRATEGY_NUMBER
            or strategy.get("revision") != STRATEGY_NUMBER
            or strategy.get("execution_interval") != "100ms"
            or type(approved_at_us) is not int or approved_at_us <= 0
            or type(approver_id) is not str or not approver_id.strip()
            or len(approver_id) > 256
            or any(char in approver_id for char in "\r\n\x00")):
        raise ValueError("Strategy 1 approval scope is invalid")
    base = {
        "schema_version": 1,
        "approval_id": _uuid(approval_id),
        "mode": mode,
        "release_attempt_id": _uuid(release.attempt_id),
        "release_content_hash": _hash(release.payload_hash),
        "release_node_hash": _hash(release.node_hash),
        "approved_at_us": approved_at_us,
        "approver_id": approver_id,
    }
    return {**base, "content_hash": _content_hash(base)}


@dataclass(frozen=True, slots=True)
class ApprovalHead:
    mode: str
    approval_id: str
    content_hash: str


class ApprovalHeadReader(Protocol):
    def read_head(self, mode: str) -> ApprovalHead: ...


class KeeperApprovalHeadReader:
    """Read one persistent mode head under an unchanged managed session."""

    def __init__(self, session: ManagedKeeperSession) -> None:
        if not isinstance(session, ManagedKeeperSession):
            raise TypeError("Strategy 1 approval needs a managed Keeper session")
        self._session = session

    @staticmethod
    def path(mode: str) -> str:
        if type(mode) is not str or mode not in _MODES:
            raise ValueError("Strategy 1 approval mode is invalid")
        return f"/trading/strategy-one-approval/v1/{mode}/head"

    def read_head(self, mode: str) -> ApprovalHead:
        session = self._session
        if not session.writable:
            raise RuntimeError("Strategy 1 approval Keeper session is unavailable")
        client = session.client
        generation, client_id = session._generation, client.client_id
        if (not isinstance(client_id, tuple) or not client_id
                or type(client_id[0]) is not int or client_id[0] <= 0):
            raise RuntimeError("Strategy 1 approval Keeper session has no identity")
        try:
            raw, stat = client.get(self.path(mode))
            fields = raw.decode("utf-8").split("\n")
            if (len(fields) != 4 or fields[:2] != ["1", mode]
                    or type(stat.version) is not int or stat.version < 0):
                raise ValueError
            head = ApprovalHead(mode, _uuid(fields[2]), _hash(fields[3]))
        except Exception as exc:
            raise ValueError("Strategy 1 approval Keeper head is missing or corrupt") from exc
        if (not session.writable or session._generation != generation
                or client.client_id != client_id):
            raise RuntimeError("Strategy 1 approval Keeper session changed during read")
        return head


def verify_selected_approval(client: Any, keeper: ApprovalHeadReader, *,
                             mode: str,
                             release: CertifiedStrategyOneConfiguration,
                             expected_approval_id: str | None = None) -> dict[str, Any]:
    """Cold-read exactly one Keeper-selected row and recheck the selection.

    This is a read-only prerequisite, not the live admission decision. It
    cannot authorize an unapproved release merely because a ClickHouse row
    exists without the corresponding Keeper head.
    """
    if type(mode) is not str or mode not in _MODES:
        raise ValueError("Strategy 1 approval mode is invalid")
    if not isinstance(release, CertifiedStrategyOneConfiguration):
        raise TypeError("Strategy 1 approval requires a certified release")
    head = keeper.read_head(mode)
    if (type(head) is not ApprovalHead or head.mode != mode
            or _uuid(head.approval_id) != head.approval_id
            or _hash(head.content_hash) != head.content_hash
            or expected_approval_id is not None
            and _uuid(expected_approval_id) != head.approval_id):
        raise ValueError("Strategy 1 Keeper approval head is invalid")
    row = read_approval_row(client, mode=mode,
                            approval_id=head.approval_id, release=release)
    if row["content_hash"] != head.content_hash:
        raise ValueError("Strategy 1 approved row differs from its Keeper head")
    if keeper.read_head(mode) != head:
        raise ValueError("Strategy 1 Keeper approval changed during cold read")
    return row


def read_approval_row(client: Any, *, mode: str, approval_id: str,
                      release: CertifiedStrategyOneConfiguration,
                      required: bool = True) -> dict[str, Any] | None:
    """Read one exact typed row without treating an unselected row as approval."""
    if type(mode) is not str or mode not in _MODES:
        raise ValueError("Strategy 1 approval mode is invalid")
    normalized_id = _uuid(approval_id)
    if not isinstance(release, CertifiedStrategyOneConfiguration):
        raise TypeError("Strategy 1 approval requires a certified release")
    from src.trading_runtime.arte_journal_writer import _literal, _rows

    columns = ",".join(name for name, _ in TABLE.columns)
    rows = _rows(client,
        f"SELECT {columns} FROM arte.{TABLE.name} "
        f"WHERE mode={_literal(mode)} "
        f"AND approval_id=toUUID({_literal(normalized_id)}) "
        "LIMIT 2 FORMAT JSONEachRow")
    if not rows and not required:
        return None
    if len(rows) != 1 or set(rows[0]) != {name for name, _ in TABLE.columns}:
        raise ValueError("Strategy 1 approved row is absent, duplicate, or untyped")
    row = dict(rows[0])
    if (type(row["schema_version"]) is not int or row["schema_version"] != 1
            or _uuid(row["approval_id"]) != normalized_id
            or row["mode"] != mode
            or _uuid(row["release_attempt_id"]) != release.attempt_id
            or _hash(row["release_content_hash"]) != release.payload_hash
            or _hash(row["release_node_hash"]) != release.node_hash
            or row != approval_row(
                approval_id=normalized_id, mode=mode, release=release,
                approved_at_us=row["approved_at_us"],
                approver_id=row["approver_id"])):
        raise ValueError("Strategy 1 approved row differs from its release")
    return row
