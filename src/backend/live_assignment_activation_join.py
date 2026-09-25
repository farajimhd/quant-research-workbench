"""Inactive typed assignment/watch cold join; never a startup admission gate.

The current assignment base/state rows omit pinned configuration revision and
non-overridable run-plan membership. A future attested plan-membership reader
must supply those identities; campaign deployment text is not authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Protocol, Sequence

from src.backend.live_assignment_roster import (
    AssignmentBaseHeadReader, RosterHeadReader, RosterStorage,
    audit_cold_roster,
)
from src.backend.live_activation_cold_bootstrap import read_attested_activation_prefix


def _identity(value: Any) -> str:
    if type(value) is not str or not value or any(c in value for c in "\r\n\x00"):
        raise ValueError("pinned plan membership identity is invalid")
    return value


@dataclass(frozen=True, slots=True)
class PinnedAssignmentMember:
    assignment_id: str
    run_plan_id: str
    base_sequence: int
    base_hash: str


@dataclass(frozen=True, slots=True)
class PinnedWatch:
    ticker: str
    profile_id: str
    book_id: str


@dataclass(frozen=True, slots=True)
class AttestedPlanMembership:
    configuration_revision_id: str
    run_plan_id: str
    assignments: tuple[PinnedAssignmentMember, ...]
    activated_watches: tuple[PinnedWatch, ...]
    head_hash: str


class PlanMembershipAuthority(Protocol):
    """Not yet implemented by a production typed configuration authority."""

    def read_attested_plan(self, *, configuration_revision_id: str,
                           run_plan_id: str) -> AttestedPlanMembership: ...
    def head_hash(self, *, configuration_revision_id: str,
                  run_plan_id: str) -> str: ...


class MissingPlanMembershipAuthority(RuntimeError):
    """No typed authority pins config revision, run plan, and membership."""


def audit_assignment_activation_join(
    activations: Sequence[dict[str, Any]],
    roster_storage: RosterStorage, roster_head: RosterHeadReader,
    base_heads: AssignmentBaseHeadReader,
    load_assignment: Callable[[str], Any], *,
    configuration_revision_id: str, run_plan_id: str,
    membership_authority: PlanMembershipAuthority | None = None,
) -> dict[str, Any]:
    """Require exact attested plan membership and watch coverage.

    The caller must supply activations from the receipt-defined source prefix.
    This function cannot attest that prefix and must not install assignments.
    """
    configuration_revision_id = _identity(configuration_revision_id)
    run_plan_id = _identity(run_plan_id)
    if membership_authority is None:
        raise MissingPlanMembershipAuthority(
            "typed assignment lacks pinned configuration_revision_id and "
            "non-overridable run_plan_id membership")
    pinned = membership_authority.read_attested_plan(
        configuration_revision_id=configuration_revision_id,
        run_plan_id=run_plan_id)
    if (not isinstance(pinned, AttestedPlanMembership)
            or pinned.configuration_revision_id != configuration_revision_id
            or pinned.run_plan_id != run_plan_id
            or type(pinned.head_hash) is not str or len(pinned.head_hash) != 64
            or any(c not in "0123456789abcdef" for c in pinned.head_hash)):
        raise ValueError("attested plan scope or head is invalid")
    assignments = audit_cold_roster(
        roster_storage, roster_head, base_heads, load_assignment)
    pinned_by_id: dict[str, PinnedAssignmentMember] = {}
    for member in pinned.assignments:
        if not isinstance(member, PinnedAssignmentMember):
            raise ValueError("pinned assignment member is untyped")
        assignment_id = _identity(member.assignment_id)
        _identity(member.run_plan_id)
        if (assignment_id in pinned_by_id
                or type(member.base_sequence) is not int or member.base_sequence < 1
                or type(member.base_hash) is not str or len(member.base_hash) != 64
                or any(c not in "0123456789abcdef" for c in member.base_hash)):
            raise ValueError("pinned assignment member is duplicate or invalid")
        pinned_by_id[assignment_id] = member
    if set(assignments) != set(pinned_by_id):
        raise ValueError("roster and pinned plan assignment membership differ")
    by_ticker: dict[str, list[Any]] = {}
    for assignment_id, assignment in assignments.items():
        member = pinned_by_id[assignment_id]
        head = base_heads.read_head(assignment_id)
        state = getattr(assignment, "state", None)
        if (type(assignment.ticker) is not str
                or not assignment.ticker or assignment.ticker != assignment.ticker.upper()
                or not isinstance(state, dict)
                or head.sequence != member.base_sequence
                or head.content_hash != member.base_hash):
            raise ValueError("pinned assignment differs from roster/base head")
        if member.run_plan_id == run_plan_id:
            by_ticker.setdefault(assignment.ticker, []).append(assignment)
    expected: dict[str, PinnedWatch] = {}
    for watch in pinned.activated_watches:
        if not isinstance(watch, PinnedWatch):
            raise ValueError("pinned watch is untyped")
        ticker = _identity(watch.ticker)
        _identity(watch.profile_id)
        _identity(watch.book_id)
        if (ticker != ticker.upper() or ticker in expected or ticker not in by_ticker
                or any(row.state.get("campaign_profile_id") != watch.profile_id
                       or row.state.get("campaign_book_id") != watch.book_id
                       for row in by_ticker[ticker])):
            raise ValueError("pinned watch is duplicate or lacks assignment")
        expected[ticker] = watch
    observed: set[str] = set()
    for activation in activations:
        if not isinstance(activation, dict):
            raise ValueError("activation watch is untyped")
        ticker = _identity(activation.get("ticker"))
        watch = expected.get(ticker)
        if (ticker in observed or watch is None
                or activation.get("run_plan_id") != run_plan_id
                or activation.get("profile_id") != watch.profile_id
                or activation.get("book_id") != watch.book_id):
            raise ValueError("activation watch differs from pinned plan membership")
        observed.add(ticker)
    if observed != set(expected):
        raise ValueError("pinned plan activation watch coverage is incomplete")
    if membership_authority.head_hash(
            configuration_revision_id=configuration_revision_id,
            run_plan_id=run_plan_id) != pinned.head_hash:
        raise RuntimeError("plan membership head changed during cold join")
    return assignments


def audit_receipt_prefix_assignment_join(
    activation_client: Any, dispatch_storage: Any,
    completion_storage: Any, completion_keeper: Any,
    roster_storage: RosterStorage, roster_head: RosterHeadReader,
    base_heads: AssignmentBaseHeadReader,
    load_assignment: Callable[[str], Any], *,
    session_date: date, source_commit_hashes: tuple[str, ...],
    configuration_revision_id: str, run_plan_id: str,
    membership_authority: PlanMembershipAuthority | None = None,
) -> dict[str, Any]:
    """Join an externally sealed source prefix to an attested roster/plan.

    This remains inactive because no production typed plan-membership authority
    currently pins the run-plan and configuration revision to assignment IDs.
    """
    if membership_authority is None:
        raise MissingPlanMembershipAuthority(
            "typed assignment lacks pinned configuration_revision_id and "
            "non-overridable run_plan_id membership")
    activations = read_attested_activation_prefix(
        activation_client, dispatch_storage, completion_storage,
        completion_keeper, session_date=session_date,
        source_commit_hashes=source_commit_hashes,
        configuration_revision_id=configuration_revision_id)
    return audit_assignment_activation_join(
        activations, roster_storage, roster_head, base_heads,
        load_assignment, configuration_revision_id=configuration_revision_id,
        run_plan_id=run_plan_id, membership_authority=membership_authority)
