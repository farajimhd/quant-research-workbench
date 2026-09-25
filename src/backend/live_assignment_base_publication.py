"""Inactive control-plane publication for one immutable base-assignment revision.

The base row is inserted once after existing typed children are attested. Any
insert error is ambiguous and terminal; MergeTree gives no uniqueness promise.
No live route imports this module and no connection is created here.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Protocol

from src.backend.live_assignment_base_keeper import (
    AssignmentHead, BaseRowReader, KeeperAssignmentHead,
    cold_read_attested_assignment,
)
from src.backend.live_assignment_base_revision import project_base_revision
from src.backend.live_assignment_state_snapshot import recover_attested_assignment
from src.trading_runtime.arte_assignment_observation_clock import normalize_observation_state
from src.trading_runtime.strategy_engine import StrategyAssignment


class BaseStorage(BaseRowReader, Protocol):
    def insert_base(self, row: Mapping[str, Any]) -> None: ...


class UncertainBasePublication(RuntimeError):
    """Possible orphan base row; operator reconciliation is required."""


def publish_base_revision(
    storage: BaseStorage, keeper: KeeperAssignmentHead,
    assignment: StrategyAssignment, *, owner_id: str,
    state_storage: Any, state_admission: Any,
    parameter_storage: Any, parameter_admission: Any,
    parameter_snapshot_id: str, parameter_session: str,
    parameter_content_hash: str, state_snapshot_id: str,
    state_snapshot_revision: int, state_session: str, state_run_id: str,
    state_content_hash: str,
) -> AssignmentHead:
    """Synchronous control-plane only; never call from a market callback."""
    assignment_id = assignment.assignment_id
    epoch = keeper.acquire(assignment_id, owner_id=owner_id)
    if epoch is None:
        raise RuntimeError("assignment base Keeper claim is held")
    uncertain = False
    try:
        head_path = f"{keeper.path(assignment_id)}/head"
        if keeper._client.exists(head_path) is None:
            prior = None
            if storage.read_base_rows(assignment_id):
                raise UncertainBasePublication(
                    "orphan base rows exist without Keeper head")
        else:
            prior = keeper.read_head(assignment_id)
            cold_read_attested_assignment(
                storage, keeper, assignment_id=assignment_id,
                state_storage=state_storage, state_admission=state_admission,
                parameter_storage=parameter_storage,
                parameter_admission=parameter_admission)
        sequence = 1 if prior is None else prior.sequence + 1
        previous_hash = "0" * 64 if prior is None else prior.content_hash
        row = project_base_revision(
            assignment, revision_sequence=sequence,
            parameter_snapshot_id=parameter_snapshot_id,
            parameter_session=parameter_session,
            state_snapshot_id=state_snapshot_id,
            state_snapshot_revision=state_snapshot_revision,
            state_session=state_session, state_run_id=state_run_id,
            parameter_content_hash=parameter_content_hash,
            state_content_hash=state_content_hash,
            previous_revision_hash=previous_hash,
        )
        # This verifies the persistent parameter claim and exact typed state
        # fence before any base row is inserted. Reused snapshots are allowed.
        recovered = recover_attested_assignment(
            base_rows=[row], state_storage=state_storage,
            state_admission=state_admission,
            parameter_storage=parameter_storage,
            parameter_admission=parameter_admission,
            assignment_id=assignment_id, revision_sequence=sequence,
            expected_base_hash=row["content_hash"],
            previous_revision_hash=previous_hash,
        )
        if recovered != replace(assignment, state=normalize_observation_state(assignment.state)):
            raise ValueError("assignment differs from its attested typed children")
        if not keeper.is_current(assignment_id, owner_id=owner_id, epoch=epoch):
            raise RuntimeError("assignment base Keeper owner fence lost")
        try:
            storage.insert_base(row)
        except BaseException as exc:
            uncertain = True
            raise UncertainBasePublication(
                "base INSERT result is ambiguous; do not retry automatically") from exc
        # CAS verifies exact CH readback plus both child snapshots again.
        try:
            return keeper.attest(
                row, owner_id=owner_id, epoch=epoch, previous=prior,
                base_rows=storage, state_storage=state_storage,
                state_admission=state_admission,
                parameter_storage=parameter_storage,
                parameter_admission=parameter_admission,
            )
        except BaseException as exc:
            # The Keeper transaction may have committed before its response
            # was lost. Never release this owner or retry a MergeTree insert.
            uncertain = True
            raise UncertainBasePublication(
                "base Keeper attestation result is ambiguous; reconcile before retry") from exc
    finally:
        if not uncertain:
            keeper.release(assignment_id, owner_id=owner_id, epoch=epoch)
