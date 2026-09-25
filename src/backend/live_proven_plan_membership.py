"""Inactive proof-bound cold adapter for normalized live plan membership.

This does not authorize startup: a production typed approved-configuration
proof is not yet available, and receipt/assignment/broker admission is separate.
"""
from __future__ import annotations

from src.backend.live_plan_membership import (
    PlanMembershipHead, PlanMembershipRows, TypedPlanMembershipAuthority,
)
from src.backend.live_plan_membership_publication import ProofPort


def _digest(value: object) -> str:
    if (type(value) is not str or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise ValueError("plan membership proof hash is invalid")
    return value


class ProvenPlanMembershipAuthority:
    """Recheck both external heads around each exact membership cold read."""

    def __init__(self, rows: PlanMembershipRows, keeper: PlanMembershipHead, *,
                 approved: ProofPort | None, source: ProofPort | None,
                 configuration_revision_id: str, session_key: str) -> None:
        if (approved is None or source is None
                or approved.identity != configuration_revision_id
                or source.identity != session_key):
            raise ValueError("approved configuration or source proof is missing")
        self._approved, self._source = approved, source
        self._approved_hash = _digest(approved.content_hash)
        self._source_hash = _digest(source.content_hash)
        self._configuration_revision_id = configuration_revision_id
        self._session_key = session_key
        self._check_proofs()
        self._delegate = TypedPlanMembershipAuthority(
            rows, keeper, configuration_content_hash=approved.content_hash,
            session_key=session_key, source_cursor_commit_hash=source.content_hash)

    def _check_proofs(self) -> None:
        self._check_proof_values()
        self._approved.assert_current()
        self._source.assert_current()
        self._check_proof_values()

    def _check_proof_values(self) -> None:
        if (self._approved.identity != self._configuration_revision_id
                or self._source.identity != self._session_key):
            raise RuntimeError("plan membership external proof scope changed")
        if (_digest(self._approved.content_hash) != self._approved_hash
                or _digest(self._source.content_hash) != self._source_hash):
            raise RuntimeError("plan membership external proof hash changed")

    def read_attested_plan(self, *, configuration_revision_id: str,
                           run_plan_id: str):
        if configuration_revision_id != self._configuration_revision_id:
            raise ValueError("plan membership configuration proof scope differs")
        self._check_proofs()
        result = self._delegate.read_attested_plan(
            configuration_revision_id=configuration_revision_id,
            run_plan_id=run_plan_id)
        self._check_proofs()
        return result

    def head_hash(self, *, configuration_revision_id: str,
                  run_plan_id: str) -> str:
        if configuration_revision_id != self._configuration_revision_id:
            raise ValueError("plan membership configuration proof scope differs")
        self._check_proofs()
        result = self._delegate.head_hash(
            configuration_revision_id=configuration_revision_id,
            run_plan_id=run_plan_id)
        self._check_proofs()
        return result
