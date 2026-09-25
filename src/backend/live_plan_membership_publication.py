"""Inactive control-plane publication for typed live plan membership.

No active route uses this module. Its caller must supply independently
attested approved-configuration and source-cursor proofs; current SQLite
approved releases and mutable watchlist snapshots are not acceptable proofs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from src.backend.live_assignment_activation_join import PinnedAssignmentMember
from src.backend.live_plan_membership import (
    ManagedPlanMembershipHeadReader, PlanWatchMember, ZERO_HASH,
    project_plan_membership,
)
from src.backend.live_assignment_base_keeper import KeeperAssignmentHead
from src.trading_runtime.keeper_session import ManagedKeeperSession


@dataclass(frozen=True, slots=True)
class ImmutableAuthorityProof:
    identity: str
    content_hash: str
    def assert_current(self) -> None:
        """Production adapters must override with a persistent head check."""
        raise RuntimeError("unbound plan membership proof is not an authority")


class ProofPort(Protocol):
    identity: str
    content_hash: str
    def assert_current(self) -> None: ...


class WritableMembershipRows(Protocol):
    def read_revisions(self, *, configuration_revision_id: str,
                       session_key: str, limit: int) -> list[Mapping[str, Any]]: ...
    def read_members(self, *, configuration_revision_id: str,
                     session_key: str, membership_sequence: int,
                     limit: int) -> list[Mapping[str, Any]]: ...
    def read_watches(self, *, configuration_revision_id: str,
                     session_key: str, membership_sequence: int,
                     limit: int) -> list[Mapping[str, Any]]: ...
    def insert_members(self, rows: Sequence[Mapping[str, Any]]) -> None: ...
    def insert_watches(self, rows: Sequence[Mapping[str, Any]]) -> None: ...
    def insert_revision(self, row: Mapping[str, Any]) -> None: ...


class MembershipKeeperPort(Protocol):
    def acquire(self, *, configuration_revision_id: str,
                session_key: str, owner_id: str) -> int | None: ...
    def is_current(self, *, configuration_revision_id: str,
                   session_key: str, owner_id: str, epoch: int) -> bool: ...
    def read_head_or_none(self, *, configuration_revision_id: str,
                          session_key: str) -> tuple[int, str, int] | None: ...
    def attest(self, row: Mapping[str, Any], *, owner_id: str,
               epoch: int, previous: tuple[int, str, int] | None) -> tuple[int, str, int]: ...
    def release(self, *, configuration_revision_id: str,
                session_key: str, owner_id: str, epoch: int) -> bool: ...


class KeeperPlanMembershipPublisher:
    """Managed Keeper epoch/holder and persistent head CAS, no CH access."""

    def __init__(self, session: ManagedKeeperSession) -> None:
        class _Owner(KeeperAssignmentHead):
            @staticmethod
            def path(assignment_id: str) -> str:
                if (type(assignment_id) is not str or len(assignment_id) != 64
                        or any(c not in "0123456789abcdef" for c in assignment_id)):
                    raise ValueError("plan membership owner scope is invalid")
                return "/trading/live-plan-membership/v1/" + assignment_id
        self._owner = _Owner(session)
        self._reader = ManagedPlanMembershipHeadReader(session)
        self._client = session.client

    def _scope(self, configuration_revision_id: str, session_key: str) -> str:
        path = self._reader.path(configuration_revision_id, session_key)
        return path.rsplit("/", 2)[-2]

    def acquire(self, *, configuration_revision_id: str,
                session_key: str, owner_id: str) -> int | None:
        return self._owner.acquire(self._scope(configuration_revision_id, session_key),
                                   owner_id=owner_id)

    def is_current(self, *, configuration_revision_id: str,
                   session_key: str, owner_id: str, epoch: int) -> bool:
        return self._owner.is_current(
            self._scope(configuration_revision_id, session_key),
            owner_id=owner_id, epoch=epoch)

    def release(self, *, configuration_revision_id: str,
                session_key: str, owner_id: str, epoch: int) -> bool:
        return self._owner.release(
            self._scope(configuration_revision_id, session_key),
            owner_id=owner_id, epoch=epoch)

    def read_head_or_none(self, *, configuration_revision_id: str,
                          session_key: str) -> tuple[int, str, int] | None:
        path = self._reader.path(configuration_revision_id, session_key)
        self._owner._connected()
        if self._client.exists(path) is None:
            return None
        return self._reader.read_head(
            configuration_revision_id=configuration_revision_id,
            session_key=session_key)

    def attest(self, row: Mapping[str, Any], *, owner_id: str,
               epoch: int, previous: tuple[int, str, int] | None) -> tuple[int, str, int]:
        config = row["configuration_revision_id"]
        session_key = row["session_key"]
        scope = self._scope(config, session_key)
        if not self.is_current(configuration_revision_id=config,
                               session_key=session_key, owner_id=owner_id, epoch=epoch):
            raise RuntimeError("plan membership Keeper owner lost")
        base = self._owner.path(scope)
        epoch_raw, epoch_stat = self._client.get(f"{base}/epoch")
        holder_raw, holder_stat = self._client.get(f"{base}/holder")
        if (epoch_raw != str(epoch).encode()
                or holder_raw != f"1\n{owner_id}\n{epoch}".encode()
                or holder_stat.ephemeralOwner != self._client.client_id[0]):
            raise RuntimeError("plan membership Keeper epoch changed")
        sequence = row["membership_sequence"]
        if sequence != (1 if previous is None else previous[0] + 1):
            raise ValueError("plan membership Keeper sequence differs")
        wire = f"1\n{config}\n{session_key}\n{sequence}\n{row['content_hash']}".encode()
        path = self._reader.path(config, session_key)
        txn = self._client.transaction()
        txn.check(f"{base}/epoch", version=epoch_stat.version)
        txn.check(f"{base}/holder", version=holder_stat.version)
        if previous is None:
            if self._client.exists(path) is not None:
                raise RuntimeError("plan membership Keeper head already exists")
            txn.create(path, wire)
        else:
            if self.read_head_or_none(configuration_revision_id=config,
                                      session_key=session_key) != previous:
                raise RuntimeError("plan membership Keeper head changed")
            txn.set_data(path, wire, version=previous[2])
        if any(isinstance(item, BaseException) for item in txn.commit()):
            raise RuntimeError("plan membership Keeper CAS failed")
        confirmed = self._reader.read_head(
            configuration_revision_id=config, session_key=session_key)
        if confirmed[:2] != (sequence, row["content_hash"]):
            raise RuntimeError("plan membership Keeper readback differs")
        return confirmed


class UncertainMembershipPublication(RuntimeError):
    """MergeTree INSERT or Keeper CAS may have committed; operator audit only."""


def publish_plan_membership(
    rows: WritableMembershipRows, keeper: MembershipKeeperPort,
    members: Sequence[PinnedAssignmentMember], watches: Sequence[PlanWatchMember], *,
    configuration_revision_id: str, session_key: str,
    approved_revision: ProofPort, source_cursor: ProofPort,
    owner_id: str,
) -> tuple[int, str, int]:
    """Rows-first/head-last publication; control-plane only, never market path."""
    if (approved_revision is None or source_cursor is None
            or approved_revision.identity != configuration_revision_id
            or source_cursor.identity != session_key):
        raise ValueError("plan membership lacks pinned approved/source proof")
    approved_revision.assert_current()
    source_cursor.assert_current()
    epoch = keeper.acquire(configuration_revision_id=configuration_revision_id,
                           session_key=session_key, owner_id=owner_id)
    if epoch is None:
        raise RuntimeError("plan membership Keeper claim is held")
    uncertain = False
    try:
        prior = keeper.read_head_or_none(
            configuration_revision_id=configuration_revision_id,
            session_key=session_key)
        existing = rows.read_revisions(
            configuration_revision_id=configuration_revision_id,
            session_key=session_key, limit=100_001)
        prior_sequence = 0 if prior is None else prior[0]
        if (len(existing) != prior_sequence
                or (prior is not None and (
                    existing[-1].get("content_hash") != prior[1]
                    or existing[-1].get("membership_sequence") != prior_sequence))):
            uncertain = True
            raise UncertainMembershipPublication(
                "plan membership has missing, duplicate, or orphan revisions")
        sequence = prior_sequence + 1
        if sequence > 100_000:
            raise ValueError("plan membership revision bound exceeded")
        if (rows.read_members(configuration_revision_id=configuration_revision_id,
                              session_key=session_key, membership_sequence=sequence,
                              limit=1)
                or rows.read_watches(configuration_revision_id=configuration_revision_id,
                                     session_key=session_key, membership_sequence=sequence,
                                     limit=1)):
            uncertain = True
            raise UncertainMembershipPublication(
                "plan membership has orphan child rows at the next sequence")
        parent, member_rows, watch_rows = project_plan_membership(
            members, watches, configuration_revision_id=configuration_revision_id,
            configuration_content_hash=approved_revision.content_hash,
            session_key=session_key,
            source_cursor_commit_hash=source_cursor.content_hash,
            membership_sequence=sequence,
            previous_hash=ZERO_HASH if prior is None else prior[1])
        approved_revision.assert_current()
        source_cursor.assert_current()
        if not keeper.is_current(configuration_revision_id=configuration_revision_id,
                                 session_key=session_key, owner_id=owner_id,
                                 epoch=epoch):
            raise RuntimeError("plan membership Keeper owner lost")
        try:
            if member_rows:
                rows.insert_members(member_rows)
            if watch_rows:
                rows.insert_watches(watch_rows)
            rows.insert_revision(parent)
        except BaseException as exc:
            uncertain = True
            raise UncertainMembershipPublication(
                "plan membership INSERT outcome is ambiguous") from exc
        try:
            actual_parents = rows.read_revisions(
                configuration_revision_id=configuration_revision_id,
                session_key=session_key, limit=100_001)
            actual_members = rows.read_members(
                configuration_revision_id=configuration_revision_id,
                session_key=session_key, membership_sequence=sequence,
                limit=100_001)
            actual_watches = rows.read_watches(
                configuration_revision_id=configuration_revision_id,
                session_key=session_key, membership_sequence=sequence,
                limit=100_001)
            if (len(actual_parents) != sequence
                    or actual_parents[-1] != parent
                    or tuple(actual_members) != member_rows
                    or tuple(actual_watches) != watch_rows):
                raise ValueError("plan membership exact readback differs")
            approved_revision.assert_current()
            source_cursor.assert_current()
            if not keeper.is_current(configuration_revision_id=configuration_revision_id,
                                     session_key=session_key, owner_id=owner_id,
                                     epoch=epoch):
                raise RuntimeError("plan membership Keeper owner lost")
            return keeper.attest(parent, owner_id=owner_id, epoch=epoch,
                                 previous=prior)
        except BaseException as exc:
            uncertain = True
            raise UncertainMembershipPublication(
                "plan membership readback or Keeper CAS is uncertain") from exc
    finally:
        if not uncertain:
            keeper.release(configuration_revision_id=configuration_revision_id,
                           session_key=session_key, owner_id=owner_id, epoch=epoch)
