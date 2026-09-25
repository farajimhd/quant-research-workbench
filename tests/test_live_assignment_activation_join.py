from dataclasses import dataclass

import pytest

from src.backend.live_assignment_activation_join import (
    AttestedPlanMembership, MissingPlanMembershipAuthority,
    PinnedAssignmentMember, PinnedWatch, audit_assignment_activation_join,
    audit_receipt_prefix_assignment_join,
)
from src.backend.live_assignment_roster import (
    RosterChange, RosterHead, ZERO_HASH, project_roster_revision,
)


@dataclass(frozen=True)
class Assignment:
    assignment_id: str
    ticker: str
    state: dict


@dataclass(frozen=True)
class BaseHead:
    sequence: int
    content_hash: str


class Storage:
    def __init__(self):
        self.parent, self.children = project_roster_revision(
            [RosterChange("a-1", "upsert", 1, "a" * 64)],
            sequence=1, previous_hash=ZERO_HASH)
    def read_revisions(self, *, limit):
        return [dict(self.parent)]
    def read_changes(self, sequence, *, limit):
        return list(map(dict, self.children))


class Heads:
    def __init__(self, storage):
        self.storage = storage
    def read_head(self, assignment_id=None):
        if assignment_id is None:
            return RosterHead(1, self.storage.parent["content_hash"], 0)
        assert assignment_id == "a-1"
        return BaseHead(1, "a" * 64)


class Plan:
    changed = False
    def read_attested_plan(self, *, configuration_revision_id, run_plan_id):
        return AttestedPlanMembership(
            configuration_revision_id, run_plan_id,
            (PinnedAssignmentMember("a-1", "plan-1", 1, "a" * 64),),
            (PinnedWatch("ABC", "profile-1", "default"),), "b" * 64)
    def head_hash(self, *, configuration_revision_id, run_plan_id):
        return ("c" if self.changed else "b") * 64


def _call(*, watches=None, authority=None):
    storage = Storage()
    heads = Heads(storage)
    assignments = {"a-1": Assignment("a-1", "ABC", {
        "campaign_profile_id": "profile-1", "campaign_book_id": "default"})}
    return audit_assignment_activation_join(
        watches if watches is not None else [{
            "run_plan_id": "plan-1", "ticker": "ABC",
            "profile_id": "profile-1", "book_id": "default"}],
        storage, heads, heads, assignments.__getitem__,
        configuration_revision_id="config-1", run_plan_id="plan-1",
        membership_authority=authority)


def test_join_fails_before_roster_read_without_attested_plan_authority():
    with pytest.raises(MissingPlanMembershipAuthority,
                       match="configuration_revision_id"):
        _call()


def test_join_exact_identity_and_missing_extra_watch_rejection():
    assert set(_call(authority=Plan())) == {"a-1"}
    with pytest.raises(ValueError, match="coverage is incomplete"):
        _call(watches=[], authority=Plan())
    with pytest.raises(ValueError, match="differs"):
        _call(watches=[{"run_plan_id": "other", "ticker": "ABC",
                        "profile_id": "profile-1", "book_id": "default"}],
              authority=Plan())
    with pytest.raises(ValueError, match="differs"):
        _call(watches=[{"run_plan_id": "plan-1", "ticker": "XYZ",
                        "profile_id": "profile-1", "book_id": "default"}],
              authority=Plan())


def test_join_fails_if_plan_membership_head_changes():
    plan = Plan()
    plan.changed = True
    with pytest.raises(RuntimeError, match="head changed"):
        _call(authority=plan)


def test_receipt_prefix_join_uses_attested_activation_and_never_sqlite():
    from datetime import date
    from unittest.mock import patch
    from tests.test_live_activation_cold_bootstrap import _case
    activation, dispatch, completion, completion_keeper, _ = _case()
    storage = Storage()
    heads = Heads(storage)
    assignment = Assignment("a-1", "ABC", {
        "campaign_profile_id": "profile-1", "campaign_book_id": "default"})
    with patch("src.backend.trading_runtime_service.trading_journal",
               side_effect=AssertionError("join must not access SQLite")):
        result = audit_receipt_prefix_assignment_join(
            activation, dispatch, completion, completion_keeper,
            storage, heads, heads, lambda key: assignment,
            session_date=date(2026, 9, 24),
            source_commit_hashes=("b" * 64,),
            configuration_revision_id="approved-1", run_plan_id="plan-1",
            membership_authority=Plan())
    assert result == {"a-1": assignment}


def test_full_roster_scope_keeps_other_run_plan_without_extra_watch():
    class GlobalStorage(Storage):
        def __init__(self):
            self.parent, self.children = project_roster_revision(
                [RosterChange("a-1", "upsert", 1, "a" * 64),
                 RosterChange("z-2", "upsert", 1, "e" * 64)],
                sequence=1, previous_hash=ZERO_HASH)
    class GlobalHeads(Heads):
        def read_head(self, assignment_id=None):
            if assignment_id == "z-2":
                return BaseHead(1, "e" * 64)
            return super().read_head(assignment_id)
    class GlobalPlan(Plan):
        def read_attested_plan(self, *, configuration_revision_id, run_plan_id):
            prior = super().read_attested_plan(
                configuration_revision_id=configuration_revision_id,
                run_plan_id=run_plan_id)
            return AttestedPlanMembership(
                prior.configuration_revision_id, prior.run_plan_id,
                (*prior.assignments,
                 PinnedAssignmentMember("z-2", "plan-2", 1, "e" * 64)),
                prior.activated_watches, prior.head_hash)
    storage = GlobalStorage()
    heads = GlobalHeads(storage)
    assignments = {
        "a-1": Assignment("a-1", "ABC", {
            "campaign_profile_id": "profile-1", "campaign_book_id": "default"}),
        "z-2": Assignment("z-2", "XYZ", {
            "campaign_profile_id": "profile-2", "campaign_book_id": "other"}),
    }
    result = audit_assignment_activation_join(
        [{"run_plan_id": "plan-1", "ticker": "ABC",
          "profile_id": "profile-1", "book_id": "default"}],
        storage, heads, heads, assignments.__getitem__,
        configuration_revision_id="config-1", run_plan_id="plan-1",
        membership_authority=GlobalPlan())
    assert set(result) == {"a-1", "z-2"}
