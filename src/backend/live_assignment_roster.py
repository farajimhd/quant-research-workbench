"""Inactive typed global assignment-roster deltas and cold coverage audit.

This is not live admission. Base-revision publishers do not yet hold the roster
owner fence, so a stable roster head cannot freeze concurrent base updates.
The optional ClickHouse adapter performs SELECT only, never DDL or INSERT.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Callable, Mapping, Protocol, Sequence

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.keeper_session import ManagedKeeperSession
from src.trading_runtime.arte_journal_writer import _literal, _rows


ROSTER_ID = "live-strategy-assignments"
ZERO_HASH = "0" * 64
KEEPER_HEAD_PATH = "/trading/live-assignment-roster/v1/head"
REVISION = TableContract(
    "live_strategy_assignment_roster_revision_typed_v1",
    (("roster_id", "String"), ("sequence", "UInt64"),
     ("previous_hash", "FixedString(64)"), ("change_count", "UInt16"),
     ("change_hash", "FixedString(64)"),
     ("content_hash", "FixedString(64)")),
    "cityHash64(roster_id) % 64", "roster_id, sequence",
)
CHANGE = TableContract(
    "live_strategy_assignment_roster_change_typed_v1",
    (("roster_id", "String"), ("sequence", "UInt64"),
     ("ordinal", "UInt16"), ("assignment_id", "String"),
     ("operation", "LowCardinality(String)"),
     ("base_sequence", "Nullable(UInt64)"),
     ("base_hash", "Nullable(FixedString(64))"),
     ("content_hash", "FixedString(64)")),
    "cityHash64(roster_id) % 64", "roster_id, sequence, ordinal",
)
TABLES = (REVISION, CHANGE)


def _digest(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _hash(value: Any) -> str:
    if (type(value) is not str or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise ValueError("roster hash is invalid")
    return value


def _identity(value: Any) -> str:
    if (type(value) is not str or not value
            or any(char in value for char in "\r\n\x00")):
        raise ValueError("roster assignment identity is invalid")
    return value


@dataclass(frozen=True, slots=True)
class RosterChange:
    assignment_id: str
    operation: str
    base_sequence: int | None = None
    base_hash: str | None = None


@dataclass(frozen=True, slots=True)
class RosterHead:
    sequence: int
    content_hash: str
    keeper_version: int


class RosterStorage(Protocol):
    def read_revisions(self, *, limit: int) -> list[Mapping[str, Any]]: ...
    def read_changes(self, sequence: int, *, limit: int) -> list[Mapping[str, Any]]: ...


class RosterWritableStorage(RosterStorage, Protocol):
    def insert_changes(self, rows: Sequence[Mapping[str, Any]]) -> None: ...
    def insert_revision(self, row: Mapping[str, Any]) -> None: ...


class ClickHouseRosterStorage:
    """Bounded exact-column reads through one caller-owned ClickHouse client."""

    def __init__(self, client: Any) -> None:
        if not callable(getattr(client, "execute", None)):
            raise TypeError("roster storage requires a ClickHouse client")
        self._client = client

    @staticmethod
    def _limit(value: int) -> int:
        if type(value) is not int or not 1 <= value <= 100001:
            raise ValueError("roster read limit is invalid")
        return value

    def read_revisions(self, *, limit: int) -> list[Mapping[str, Any]]:
        bound = self._limit(limit)
        columns = ",".join(name for name, _ in REVISION.columns)
        rows = _rows(self._client,
            f"SELECT {columns} FROM arte.{REVISION.name} "
            f"WHERE roster_id={_literal(ROSTER_ID)} "
            f"ORDER BY sequence LIMIT {bound} FORMAT JSONEachRow")
        if len(rows) > bound or any(
                set(row) != {name for name, _ in REVISION.columns}
                or row["roster_id"] != ROSTER_ID
                or type(row["sequence"]) is not int
                for row in rows):
            raise ValueError("roster revision query returned unmodeled rows")
        return rows

    def read_changes(self, sequence: int, *, limit: int) -> list[Mapping[str, Any]]:
        if type(sequence) is not int or not 1 <= sequence <= 2**64 - 1:
            raise ValueError("roster change sequence is invalid")
        bound = self._limit(limit)
        columns = ",".join(name for name, _ in CHANGE.columns)
        rows = _rows(self._client,
            f"SELECT {columns} FROM arte.{CHANGE.name} "
            f"WHERE roster_id={_literal(ROSTER_ID)} AND sequence={sequence} "
            f"ORDER BY ordinal LIMIT {bound} FORMAT JSONEachRow")
        if len(rows) > bound or any(
                set(row) != {name for name, _ in CHANGE.columns}
                or row["roster_id"] != ROSTER_ID
                or type(row["sequence"]) is not int
                or row["sequence"] != sequence
                or type(row["ordinal"]) is not int
                for row in rows):
            raise ValueError("roster change query returned unmodeled rows")
        return rows


class RosterHeadReader(Protocol):
    def read_head(self) -> RosterHead: ...


class ManagedRosterHeadReader:
    """Read the persistent Keeper head through a managed writable session.

    This adapter cannot create or advance the head. Publication CAS and the
    common roster/base owner fence remain separate cutover prerequisites.
    """

    def __init__(self, session: ManagedKeeperSession) -> None:
        if not isinstance(session, ManagedKeeperSession):
            raise TypeError("roster head requires a managed Keeper session")
        self._session = session

    def read_head(self) -> RosterHead:
        session = self._session
        client = session.client
        if (not getattr(session, "_connected", False)
                or getattr(session, "_closed", True)
                or not getattr(client, "connected", False)
                or getattr(getattr(client, "client_state", None), "name", "CONNECTED")
                   == "CONNECTED_RO"
                or getattr(client, "client_id", None) is None):
            raise RuntimeError("roster Keeper session is unavailable")
        generation = session._generation
        session_id = client.client_id
        try:
            raw, stat = client.get(KEEPER_HEAD_PATH)
            if (not session._connected or session._closed
                    or session._generation != generation
                    or client.client_id != session_id
                    or not client.connected):
                raise RuntimeError("roster Keeper session changed during head read")
            fields = raw.decode("utf-8").split("\n")
            if (len(fields) != 4 or fields[:2] != ["1", ROSTER_ID]
                    or str(int(fields[2])) != fields[2]
                    or int(fields[2]) < 1
                    or type(stat.version) is not int or stat.version < 0):
                raise ValueError
            return RosterHead(int(fields[2]), _hash(fields[3]), stat.version)
        except RuntimeError:
            raise
        except Exception as exc:
            raise ValueError("roster Keeper head is missing or corrupt") from exc


class AssignmentBaseHeadReader(Protocol):
    def read_head(self, assignment_id: str) -> Any: ...


def project_roster_revision(
    changes: Sequence[RosterChange], *, sequence: int,
    previous_hash: str,
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    """Project a single immutable delta; no implicit full-population rewrite."""
    if (type(sequence) is not int or sequence < 1 or sequence > 2**64 - 1
            or not 1 <= len(changes) <= 65535
            or (sequence == 1 and previous_hash != ZERO_HASH)
            or (sequence > 1 and previous_hash == ZERO_HASH)):
        raise ValueError("roster revision sequence or predecessor is invalid")
    _hash(previous_hash)
    rows = []
    prior_id = ""
    for ordinal, change in enumerate(changes):
        if not isinstance(change, RosterChange):
            raise ValueError("roster change is not typed")
        assignment_id = _identity(change.assignment_id)
        if assignment_id <= prior_id or change.operation not in {"upsert", "remove"}:
            raise ValueError("roster changes must be unique and ordered")
        prior_id = assignment_id
        if change.operation == "upsert":
            if (type(change.base_sequence) is not int
                    or not 1 <= change.base_sequence <= 2**64 - 1):
                raise ValueError("roster upsert lacks base sequence")
            _hash(change.base_hash)
        elif change.base_sequence is not None or change.base_hash is not None:
            raise ValueError("roster removal must not carry a base revision")
        row = dict(roster_id=ROSTER_ID, sequence=sequence, ordinal=ordinal,
                   assignment_id=assignment_id, operation=change.operation,
                   base_sequence=change.base_sequence, base_hash=change.base_hash)
        rows.append({**row, "content_hash": _digest(row)})
    parent = dict(roster_id=ROSTER_ID, sequence=sequence,
                  previous_hash=previous_hash, change_count=len(rows),
                  change_hash=_digest(rows))
    return {**parent, "content_hash": _digest(parent)}, tuple(rows)


def _verify_revision(parent: Mapping[str, Any], changes: Sequence[Mapping[str, Any]],
                     *, sequence: int, previous_hash: str) -> tuple[RosterChange, ...]:
    if (not isinstance(parent, Mapping)
            or set(parent) != {name for name, _ in REVISION.columns}
            or not isinstance(changes, Sequence)
            or any(not isinstance(row, Mapping)
                   or set(row) != {name for name, _ in CHANGE.columns}
                   for row in changes)):
        raise ValueError("roster revision or child columns differ")
    if (parent["roster_id"] != ROSTER_ID or parent["sequence"] != sequence
            or parent["previous_hash"] != previous_hash
            or parent["change_count"] != len(changes)):
        raise ValueError("roster revision identity, chain, or count differs")
    ordered = sorted(changes, key=lambda row: row["ordinal"])
    if [row["ordinal"] for row in ordered] != list(range(len(ordered))):
        raise ValueError("roster child ordinals have a gap or duplicate")
    decoded = tuple(RosterChange(row["assignment_id"], row["operation"],
                                 row["base_sequence"], row["base_hash"])
                    for row in ordered)
    expected_parent, expected_children = project_roster_revision(
        decoded, sequence=sequence, previous_hash=previous_hash)
    if dict(parent) != expected_parent or tuple(map(dict, ordered)) != expected_children:
        raise ValueError("roster revision content differs")
    return decoded


def audit_cold_roster(
    storage: RosterStorage, head_reader: RosterHeadReader,
    base_heads: AssignmentBaseHeadReader,
    load_assignment: Callable[[str], Any], *, max_revisions: int = 10000,
    max_members: int = 10000,
) -> dict[str, Any]:
    """Verify exact committed roster and base heads; not startup admission."""
    if (type(max_revisions) is not int or not 1 <= max_revisions <= 100000
            or type(max_members) is not int or not 1 <= max_members <= 100000):
        raise ValueError("roster cold audit bounds are invalid")
    first = head_reader.read_head()
    if (not isinstance(first, RosterHead) or type(first.sequence) is not int
            or not 1 <= first.sequence <= max_revisions
            or type(first.keeper_version) is not int or first.keeper_version < 0):
        raise ValueError("roster Keeper head is invalid")
    _hash(first.content_hash)
    parents = storage.read_revisions(limit=max_revisions + 1)
    if len(parents) != first.sequence:
        raise ValueError("roster revision chain has gap, duplicate, or orphan")
    by_sequence = {row.get("sequence"): row for row in parents}
    if set(by_sequence) != set(range(1, first.sequence + 1)):
        raise ValueError("roster revision chain has gap, duplicate, or orphan")
    current: dict[str, tuple[int, str]] = {}
    prior_hash = ZERO_HASH
    for sequence in range(1, first.sequence + 1):
        children = storage.read_changes(sequence, limit=65536)
        if len(children) > 65535:
            raise ValueError("roster child bound exceeded")
        changes = _verify_revision(
            by_sequence[sequence], children,
            sequence=sequence, previous_hash=prior_hash)
        for change in changes:
            if change.operation == "remove":
                if change.assignment_id not in current:
                    raise ValueError("roster removal has no prior member")
                del current[change.assignment_id]
            else:
                current[change.assignment_id] = (change.base_sequence, change.base_hash)
        if len(current) > max_members:
            raise ValueError("roster member bound exceeded")
        prior_hash = by_sequence[sequence]["content_hash"]
    if prior_hash != first.content_hash:
        raise ValueError("roster chain differs from Keeper head")
    recovered = {}
    for assignment_id in sorted(current):
        expected_sequence, expected_hash = current[assignment_id]
        base = base_heads.read_head(assignment_id)
        if (getattr(base, "sequence", None) != expected_sequence
                or getattr(base, "content_hash", None) != expected_hash):
            raise ValueError("assignment base head differs from roster member")
        assignment = load_assignment(assignment_id)
        if getattr(assignment, "assignment_id", None) != assignment_id:
            raise ValueError("loaded assignment identity differs from roster")
        recovered[assignment_id] = assignment
    if head_reader.read_head() != first:
        raise RuntimeError("roster Keeper head changed during cold audit")
    for assignment_id, (sequence, content_hash) in current.items():
        base = base_heads.read_head(assignment_id)
        if (getattr(base, "sequence", None) != sequence
                or getattr(base, "content_hash", None) != content_hash):
            raise RuntimeError("assignment base head changed during cold audit")
    return recovered
