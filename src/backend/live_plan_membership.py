"""Inactive versioned typed assignment-to-run-plan membership authority.

Rows are only a contract: approved configuration and source-cursor hashes must
be independently attested before publication or recovery. No DDL/INSERT runs.
"""
from __future__ import annotations

from hashlib import sha256
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence
from uuid import UUID

from src.backend.live_assignment_activation_join import (
    AttestedPlanMembership, PinnedAssignmentMember, PinnedWatch,
)
from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.keeper_session import ManagedKeeperSession


ZERO_HASH = "0" * 64
PARENT = TableContract(
    "live_plan_membership_revision_typed_v1",
    (("schema_version", "UInt16"), ("configuration_revision_id", "String"),
     ("configuration_content_hash", "FixedString(64)"),
     ("session_key", "Date"), ("source_cursor_commit_hash", "FixedString(64)"),
     ("membership_sequence", "UInt64"), ("publication_id", "UUID"),
     ("previous_hash", "FixedString(64)"),
     ("member_count", "UInt32"), ("member_hash", "FixedString(64)"),
     ("watch_count", "UInt32"), ("watch_hash", "FixedString(64)"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(session_key)",
    "configuration_revision_id, session_key, membership_sequence",
)
MEMBER = TableContract(
    "live_plan_assignment_member_typed_v1",
    (("schema_version", "UInt16"), ("configuration_revision_id", "String"),
     ("session_key", "Date"), ("membership_sequence", "UInt64"),
     ("publication_id", "UUID"),
     ("assignment_id", "String"), ("run_plan_id", "String"),
     ("base_sequence", "UInt64"), ("base_hash", "FixedString(64)"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(session_key)",
    "configuration_revision_id, session_key, membership_sequence, assignment_id",
)
WATCH = TableContract(
    "live_plan_activated_watch_typed_v1",
    (("schema_version", "UInt16"), ("configuration_revision_id", "String"),
     ("session_key", "Date"), ("membership_sequence", "UInt64"),
     ("publication_id", "UUID"),
     ("run_plan_id", "String"), ("ticker", "String"),
     ("profile_id", "String"), ("book_id", "String"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(session_key)",
    "configuration_revision_id, session_key, membership_sequence, run_plan_id, ticker",
)
TABLES = (PARENT, MEMBER, WATCH)


@dataclass(frozen=True, slots=True)
class PlanWatchMember:
    run_plan_id: str
    ticker: str
    profile_id: str
    book_id: str


@dataclass(frozen=True, slots=True)
class RecoveredPlanMembership:
    assignments: tuple[PinnedAssignmentMember, ...]
    watches: tuple[PlanWatchMember, ...]
    head_hash: str


def _hash(value: Any) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def _digest(value: Any) -> str:
    if (type(value) is not str or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise ValueError("plan membership hash is invalid")
    return value


def _identity(value: Any) -> str:
    if type(value) is not str or not value or any(char in value for char in "\r\n\x00"):
        raise ValueError("plan membership identity is invalid")
    return value


def _publication_id(value: Any) -> str:
    if type(value) is not str:
        raise ValueError("plan membership publication ID is invalid")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValueError("plan membership publication ID is invalid") from exc
    if str(parsed) != value or parsed.int == 0:
        raise ValueError("plan membership publication ID is invalid")
    return value


def project_plan_membership(
    members: Sequence[PinnedAssignmentMember],
    watches: Sequence[PlanWatchMember], *,
    configuration_revision_id: str, configuration_content_hash: str,
    session_key: str, source_cursor_commit_hash: str,
    membership_sequence: int, publication_id: str, previous_hash: str,
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...],
           tuple[dict[str, Any], ...]]:
    from datetime import date
    _identity(configuration_revision_id)
    _digest(configuration_content_hash)
    _digest(source_cursor_commit_hash)
    _digest(previous_hash)
    _publication_id(publication_id)
    if (type(session_key) is not str
            or date.fromisoformat(session_key).isoformat() != session_key
            or type(membership_sequence) is not int
            or not 1 <= membership_sequence <= 2**64 - 1
            or (membership_sequence == 1) != (previous_hash == ZERO_HASH)
            or len(members) > 100_000 or len(watches) > 100_000):
        raise ValueError("plan membership revision scope is invalid")
    children = []
    prior_id = ""
    for member in members:
        if not isinstance(member, PinnedAssignmentMember):
            raise ValueError("plan member is untyped")
        assignment_id = _identity(member.assignment_id)
        if (assignment_id <= prior_id or type(member.base_sequence) is not int
                or not 1 <= member.base_sequence <= 2**64 - 1):
            raise ValueError("plan members must be sorted unique with base revision")
        prior_id = assignment_id
        row = dict(schema_version=1,
                   configuration_revision_id=configuration_revision_id,
                   session_key=session_key, membership_sequence=membership_sequence,
                   publication_id=publication_id,
                   assignment_id=assignment_id,
                   run_plan_id=_identity(member.run_plan_id),
                   base_sequence=member.base_sequence,
                   base_hash=_digest(member.base_hash))
        children.append({**row, "content_hash": _hash(row)})
    watch_rows = []
    prior_watch = ("", "")
    for watch in watches:
        if not isinstance(watch, PlanWatchMember):
            raise ValueError("plan watch is untyped")
        key = (_identity(watch.run_plan_id), _identity(watch.ticker))
        if key <= prior_watch or key[1] != key[1].upper():
            raise ValueError("plan watches must be sorted unique uppercase identities")
        prior_watch = key
        row = dict(schema_version=1,
                   configuration_revision_id=configuration_revision_id,
                   session_key=session_key, membership_sequence=membership_sequence,
                   publication_id=publication_id,
                   run_plan_id=key[0], ticker=key[1],
                   profile_id=_identity(watch.profile_id),
                   book_id=_identity(watch.book_id))
        watch_rows.append({**row, "content_hash": _hash(row)})
    parent = dict(schema_version=1,
                  configuration_revision_id=configuration_revision_id,
                  configuration_content_hash=configuration_content_hash,
                  session_key=session_key,
                  source_cursor_commit_hash=source_cursor_commit_hash,
                  membership_sequence=membership_sequence,
                  publication_id=publication_id,
                  previous_hash=previous_hash,
                  member_count=len(children), member_hash=_hash(children),
                  watch_count=len(watch_rows), watch_hash=_hash(watch_rows))
    return ({**parent, "content_hash": _hash(parent)},
            tuple(children), tuple(watch_rows))


class PlanMembershipRows(Protocol):
    def read_revisions(self, *, configuration_revision_id: str,
                       session_key: str, limit: int) -> list[Mapping[str, Any]]: ...
    def read_members(self, *, configuration_revision_id: str,
                     session_key: str, membership_sequence: int,
                     publication_id: str,
                     limit: int) -> list[Mapping[str, Any]]: ...
    def read_watches(self, *, configuration_revision_id: str,
                     session_key: str, membership_sequence: int,
                     publication_id: str,
                     limit: int) -> list[Mapping[str, Any]]: ...


class PlanMembershipHead(Protocol):
    def read_head(self, *, configuration_revision_id: str,
                  session_key: str) -> tuple[int, str, int]: ...


class ManagedPlanMembershipHeadReader:
    """Read-only persistent Keeper head; no claim creation or head mutation."""

    def __init__(self, session: ManagedKeeperSession) -> None:
        if not isinstance(session, ManagedKeeperSession):
            raise TypeError("plan membership head requires managed Keeper session")
        self._session = session

    @staticmethod
    def path(configuration_revision_id: str, session_key: str) -> str:
        from datetime import date
        _identity(configuration_revision_id)
        if (type(session_key) is not str
                or date.fromisoformat(session_key).isoformat() != session_key):
            raise ValueError("plan membership session is invalid")
        scope = f"{configuration_revision_id}\n{session_key}".encode()
        return "/trading/live-plan-membership/v1/" + sha256(scope).hexdigest() + "/head"

    def read_head(self, *, configuration_revision_id: str,
                  session_key: str) -> tuple[int, str, int]:
        session, client = self._session, self._session.client
        if (not session._connected or session._closed
                or not getattr(client, "connected", False)
                or getattr(getattr(client, "client_state", None), "name", "CONNECTED")
                   == "CONNECTED_RO"
                or client.client_id is None):
            raise RuntimeError("plan membership Keeper session is unavailable")
        generation, client_id = session._generation, client.client_id
        try:
            raw, stat = client.get(self.path(configuration_revision_id, session_key))
            fields = raw.decode("utf-8").split("\n")
            if (len(fields) != 5 or fields[:3] != ["1", configuration_revision_id,
                                                   session_key]
                    or str(int(fields[3])) != fields[3] or int(fields[3]) < 1
                    or type(stat.version) is not int or stat.version < 0):
                raise ValueError
            head = (int(fields[3]), _digest(fields[4]), stat.version)
        except Exception as exc:
            raise ValueError("plan membership Keeper head missing or corrupt") from exc
        if (not session._connected or session._closed
                or session._generation != generation or client.client_id != client_id
                or not client.connected):
            raise RuntimeError("plan membership Keeper session changed during read")
        return head


def recover_attested_plan_membership(
    rows: PlanMembershipRows, keeper: PlanMembershipHead, *,
    configuration_revision_id: str, configuration_content_hash: str,
    session_key: str, source_cursor_commit_hash: str,
    max_revisions: int = 10_000,
) -> RecoveredPlanMembership:
    """Exact cold chain; does not attest approved config/source by itself."""
    if type(max_revisions) is not int or not 1 <= max_revisions <= 100_000:
        raise ValueError("plan membership recovery bound is invalid")
    first = keeper.read_head(configuration_revision_id=configuration_revision_id,
                             session_key=session_key)
    if (not isinstance(first, tuple) or len(first) != 3
            or type(first[0]) is not int or not 1 <= first[0] <= max_revisions
            or type(first[2]) is not int or first[2] < 0):
        raise ValueError("plan membership Keeper head is invalid")
    _digest(first[1])
    parents = rows.read_revisions(configuration_revision_id=configuration_revision_id,
                                  session_key=session_key, limit=100_001)
    if len(parents) > 100_000:
        raise ValueError("plan membership attempt bound exceeded")
    # A delayed INSERT from an owner whose Keeper claim expired can add rows
    # after a newer publication was attested. Only the hash chain ending at
    # Keeper's accepted head is authoritative; other attempts are inert.
    by_hash: dict[str, list[Mapping[str, Any]]] = {}
    for row in parents:
        by_hash.setdefault(str(row.get("content_hash")), []).append(row)
    accepted: dict[int, Mapping[str, Any]] = {}
    expected_hash = first[1]
    for sequence in range(first[0], 0, -1):
        matches = [row for row in by_hash.get(expected_hash, ())
                   if row.get("membership_sequence") == sequence]
        if len(matches) != 1:
            raise ValueError("plan membership accepted chain has duplicate or gap")
        accepted[sequence] = matches[0]
        expected_hash = _digest(matches[0].get("previous_hash"))
    if expected_hash != ZERO_HASH:
        raise ValueError("plan membership accepted chain has an invalid root")
    previous = ZERO_HASH
    result = ()
    result_watches = ()
    for sequence in range(1, first[0] + 1):
        parent = accepted[sequence]
        publication_id = _publication_id(parent.get("publication_id"))
        children = rows.read_members(
            configuration_revision_id=configuration_revision_id,
            session_key=session_key, membership_sequence=sequence,
            publication_id=publication_id,
            limit=100_001)
        watch_rows = rows.read_watches(
            configuration_revision_id=configuration_revision_id,
            session_key=session_key, membership_sequence=sequence,
            publication_id=publication_id,
            limit=100_001)
        if (len(children) > 100_000
                or len(watch_rows) > 100_000
                or set(parent) != {name for name, _ in PARENT.columns}
                or any(set(row) != {name for name, _ in MEMBER.columns}
                       for row in children)
                or any(set(row) != {name for name, _ in WATCH.columns}
                       for row in watch_rows)):
            raise ValueError("plan membership columns or member bound differ")
        decoded = tuple(PinnedAssignmentMember(
            row["assignment_id"], row["run_plan_id"],
            row["base_sequence"], row["base_hash"]) for row in
            sorted(children, key=lambda row: row["assignment_id"]))
        decoded_watches = tuple(PlanWatchMember(
            row["run_plan_id"], row["ticker"], row["profile_id"], row["book_id"])
            for row in sorted(watch_rows,
                              key=lambda row: (row["run_plan_id"], row["ticker"])))
        expected, expected_children, expected_watches = project_plan_membership(
            decoded, decoded_watches,
            configuration_revision_id=configuration_revision_id,
            configuration_content_hash=configuration_content_hash,
            session_key=session_key,
            source_cursor_commit_hash=_digest(parent["source_cursor_commit_hash"]),
            membership_sequence=sequence, publication_id=publication_id,
            previous_hash=previous)
        if (dict(parent) != expected
                or tuple(map(dict, children)) != expected_children
                or tuple(map(dict, watch_rows)) != expected_watches):
            raise ValueError("plan membership content or child order differs")
        previous, result, result_watches = (expected["content_hash"],
                                            decoded, decoded_watches)
    if previous != first[1]:
        raise ValueError("plan membership chain differs from Keeper head")
    if accepted[first[0]]["source_cursor_commit_hash"] != _digest(source_cursor_commit_hash):
        raise ValueError("plan membership latest source cursor differs")
    if keeper.read_head(configuration_revision_id=configuration_revision_id,
                        session_key=session_key) != first:
        raise RuntimeError("plan membership Keeper head changed during cold read")
    return RecoveredPlanMembership(result, result_watches, first[1])


class TypedPlanMembershipAuthority:
    """Inactive adapter for the join's selected-plan read contract."""

    def __init__(self, rows: PlanMembershipRows, keeper: PlanMembershipHead, *,
                 configuration_content_hash: str, session_key: str,
                 source_cursor_commit_hash: str) -> None:
        self._rows, self._keeper = rows, keeper
        self._configuration_content_hash = _digest(configuration_content_hash)
        self._session_key = session_key
        self._source_cursor_commit_hash = _digest(source_cursor_commit_hash)

    def read_attested_plan(self, *, configuration_revision_id: str,
                           run_plan_id: str) -> AttestedPlanMembership:
        recovered = recover_attested_plan_membership(
            self._rows, self._keeper,
            configuration_revision_id=configuration_revision_id,
            configuration_content_hash=self._configuration_content_hash,
            session_key=self._session_key,
            source_cursor_commit_hash=self._source_cursor_commit_hash)
        if not any(member.run_plan_id == run_plan_id
                   for member in recovered.assignments):
            raise ValueError("selected run plan has no attested assignment members")
        watches = tuple(PinnedWatch(row.ticker, row.profile_id, row.book_id)
                        for row in recovered.watches if row.run_plan_id == run_plan_id)
        return AttestedPlanMembership(
            configuration_revision_id, run_plan_id, recovered.assignments,
            watches, recovered.head_hash)

    def head_hash(self, *, configuration_revision_id: str,
                  run_plan_id: str) -> str:
        _identity(run_plan_id)
        return self._keeper.read_head(
            configuration_revision_id=configuration_revision_id,
            session_key=self._session_key)[1]
