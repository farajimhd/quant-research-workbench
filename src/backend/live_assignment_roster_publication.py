"""Inactive control-plane roster publication; never call on market intake."""
from __future__ import annotations

from typing import Any, Protocol, Sequence

from src.backend.live_assignment_roster import (
    KEEPER_HEAD_PATH, ROSTER_ID, RosterChange, RosterHead, RosterWritableStorage, ZERO_HASH,
    _verify_revision, project_roster_revision,
)
from src.backend.live_assignment_base_keeper import KeeperAssignmentHead
from src.trading_runtime.keeper_session import ManagedKeeperSession


class KeeperRosterOwnerFence:
    """Persistent epoch/ephemeral holder CAS shared with base publication."""

    def __init__(self, session: ManagedKeeperSession) -> None:
        class _DistinctRosterOwner(KeeperAssignmentHead):
            @staticmethod
            def path(assignment_id: str) -> str:
                if assignment_id != ROSTER_ID:
                    raise ValueError("roster owner identity differs")
                return "/trading/live-assignment-roster/v1/owner"

        self._owner = _DistinctRosterOwner(session)
        self._client = session.client

    def acquire(self, *, owner_id: str) -> int | None:
        return self._owner.acquire(ROSTER_ID, owner_id=owner_id)

    def is_current(self, *, owner_id: str, epoch: int) -> bool:
        return self._owner.is_current(ROSTER_ID, owner_id=owner_id, epoch=epoch)

    def release(self, *, owner_id: str, epoch: int) -> bool:
        return self._owner.release(ROSTER_ID, owner_id=owner_id, epoch=epoch)

    def read_head_or_none(self) -> RosterHead | None:
        self._owner._connected()
        if self._client.exists(KEEPER_HEAD_PATH) is None:
            return None
        from src.backend.live_assignment_roster import ManagedRosterHeadReader
        return ManagedRosterHeadReader(self._owner._session).read_head()

    def attest(self, row: dict, *, owner_id: str, epoch: int,
               previous: RosterHead | None) -> RosterHead:
        if not self.is_current(owner_id=owner_id, epoch=epoch):
            raise RuntimeError("roster owner fence lost")
        base = self._owner.path(ROSTER_ID)
        epoch_raw, epoch_stat = self._client.get(f"{base}/epoch")
        holder_raw, holder_stat = self._client.get(f"{base}/holder")
        if (epoch_raw != str(epoch).encode()
                or holder_raw != f"1\n{owner_id}\n{epoch}".encode()
                or holder_stat.ephemeralOwner != self._client.client_id[0]):
            raise RuntimeError("roster owner epoch changed")
        wire = (f"1\n{ROSTER_ID}\n{row['sequence']}\n"
                f"{row['content_hash']}").encode()
        txn = self._client.transaction()
        txn.check(f"{base}/epoch", version=epoch_stat.version)
        txn.check(f"{base}/holder", version=holder_stat.version)
        if previous is None:
            if self._client.exists(KEEPER_HEAD_PATH) is not None:
                raise RuntimeError("roster Keeper head already exists")
            self._client.ensure_path(KEEPER_HEAD_PATH.rsplit("/", 1)[0])
            txn.create(KEEPER_HEAD_PATH, wire)
        else:
            if self.read_head_or_none() != previous:
                raise RuntimeError("roster Keeper head changed")
            txn.set_data(KEEPER_HEAD_PATH, wire, version=previous.keeper_version)
        if any(isinstance(item, BaseException) for item in txn.commit()):
            raise RuntimeError("roster Keeper CAS failed")
        confirmed = self.read_head_or_none()
        if (confirmed is None or confirmed.sequence != row["sequence"]
                or confirmed.content_hash != row["content_hash"]):
            raise RuntimeError("roster Keeper CAS readback differs")
        return confirmed


class RosterOwnerFence(Protocol):
    def acquire(self, *, owner_id: str) -> int | None: ...
    def is_current(self, *, owner_id: str, epoch: int) -> bool: ...
    def read_head_or_none(self) -> RosterHead | None: ...
    def attest(self, row: dict, *, owner_id: str, epoch: int,
               previous: RosterHead | None) -> RosterHead: ...
    def release(self, *, owner_id: str, epoch: int) -> bool: ...


class UncertainRosterPublication(RuntimeError):
    """A MergeTree row or Keeper CAS may have committed; do not retry."""


def publish_rostered_base_revision(
    roster_storage: RosterWritableStorage, roster_keeper: RosterOwnerFence,
    base_storage: Any, base_keeper: KeeperAssignmentHead, assignment: Any,
    *, owner_id: str, base_heads: Any, **base_kwargs: Any,
) -> tuple[Any, RosterHead]:
    """Control-plane only: one roster epoch spans base ACK and roster CAS.

    Any error retains the roster holder: a base/roster write or CAS may have
    committed. The caller must reconcile under operator control, not retry.
    """
    epoch = roster_keeper.acquire(owner_id=owner_id)
    if epoch is None:
        raise RuntimeError("roster owner claim is held")
    try:
        from src.backend.live_assignment_base_publication import publish_base_revision
        base = publish_base_revision(
            base_storage, base_keeper, assignment, owner_id=owner_id,
            roster_fence=roster_keeper, roster_epoch=epoch, **base_kwargs)
        roster = publish_roster_revision(
            roster_storage, roster_keeper,
            [RosterChange(assignment.assignment_id, "upsert",
                          base.sequence, base.content_hash)],
            owner_id=owner_id, base_heads=base_heads, owned_epoch=epoch)
    except BaseException:
        # Even a lost response can conceal a durable base or roster commit.
        raise
    roster_keeper.release(owner_id=owner_id, epoch=epoch)
    return base, roster


def publish_roster_revision(
    storage: RosterWritableStorage, keeper: RosterOwnerFence,
    changes: Sequence[RosterChange], *, owner_id: str,
    base_heads: Any, owned_epoch: int | None = None,
) -> RosterHead:
    """Insert children, insert parent, verify exact rows, then CAS Keeper head.

    The owner fence must also cover any base-head updates being rostered. This
    function deliberately does not acquire a base head or enable live routing.
    """
    epoch = keeper.acquire(owner_id=owner_id) if owned_epoch is None else owned_epoch
    if epoch is None:
        raise RuntimeError("roster owner claim is held")
    if not keeper.is_current(owner_id=owner_id, epoch=epoch):
        raise RuntimeError("roster owner fence is not current")
    uncertain = False
    try:
        previous = keeper.read_head_or_none()
        parents = storage.read_revisions(limit=100001)
        expected_count = 0 if previous is None else previous.sequence
        if len(parents) != expected_count:
            uncertain = True
            raise UncertainRosterPublication("roster has orphan, missing, or duplicate rows")
        if previous is not None and (
                parents[-1].get("content_hash") != previous.content_hash
                or parents[-1].get("sequence") != previous.sequence):
            uncertain = True
            raise UncertainRosterPublication("roster head differs from stored chain")
        prior_hash = ZERO_HASH
        try:
            for index, actual in enumerate(parents, 1):
                children = storage.read_changes(index, limit=65536)
                _verify_revision(actual, children, sequence=index,
                                 previous_hash=prior_hash)
                prior_hash = actual["content_hash"]
        except BaseException:
            uncertain = True
            raise
        sequence = expected_count + 1
        parent, children = project_roster_revision(
            changes, sequence=sequence,
            previous_hash=ZERO_HASH if previous is None else previous.content_hash)
        for change in changes:
            if change.operation == "upsert":
                base = base_heads.read_head(change.assignment_id)
                if (getattr(base, "sequence", None) != change.base_sequence
                        or getattr(base, "content_hash", None) != change.base_hash):
                    raise ValueError("roster upsert differs from attested base head")
        if not keeper.is_current(owner_id=owner_id, epoch=epoch):
            raise RuntimeError("roster owner fence lost")
        # A failed INSERT is ambiguous. Never retry into a non-unique MergeTree.
        try:
            storage.insert_changes(children)
            storage.insert_revision(parent)
        except BaseException as exc:
            uncertain = True
            raise UncertainRosterPublication("roster INSERT outcome is ambiguous") from exc
        try:
            actual_parents = storage.read_revisions(limit=100001)
            actual_children = storage.read_changes(sequence, limit=65536)
            if (len(actual_parents) != sequence
                    or actual_parents[-1] != parent
                    or len(actual_children) != len(children)):
                raise ValueError("roster exact readback differs")
            _verify_revision(actual_parents[-1], actual_children,
                             sequence=sequence,
                             previous_hash=parent["previous_hash"])
            if not keeper.is_current(owner_id=owner_id, epoch=epoch):
                raise RuntimeError("roster owner fence lost")
            return keeper.attest(parent, owner_id=owner_id, epoch=epoch,
                                 previous=previous)
        except BaseException as exc:
            uncertain = True
            raise UncertainRosterPublication(
                "roster readback or Keeper CAS is uncertain; reconcile") from exc
    finally:
        if not uncertain and owned_epoch is None:
            keeper.release(owner_id=owner_id, epoch=epoch)
