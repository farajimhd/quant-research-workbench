import pytest
from datetime import datetime, timezone
from unittest.mock import patch

from src.backend.live_proven_plan_membership import (
    ProvenPlanMembershipAuthority, cold_strategy_one_plan_membership_authority,
)
from tests.test_live_plan_membership import CONFIG, SESSION, Keeper, Rows, _fixture


class Proof:
    def __init__(self, identity, content_hash):
        self.identity, self.content_hash = identity, content_hash
        self.current = True
        self.reads = 0

    def assert_current(self):
        self.reads += 1
        if not self.current:
            raise RuntimeError("external head changed")


def _authority(approved=None, source=None):
    parent, children, watches, _, _ = _fixture()
    approved = approved if approved is not None else Proof(CONFIG, "c" * 64)
    source = source if source is not None else Proof(SESSION, "d" * 64)
    authority = ProvenPlanMembershipAuthority(
        Rows(parent, children, watches), Keeper(parent),
        approved=approved, source=source,
        configuration_revision_id=CONFIG, session_key=SESSION)
    return authority, approved, source


def test_proven_plan_exact_cold_read_and_rechecks_both_heads():
    authority, approved, source = _authority()
    result = authority.read_attested_plan(
        configuration_revision_id=CONFIG, run_plan_id="plan-1")
    assert len(result.assignments) == 2  # full roster, not just selected plan
    assert [(watch.ticker, watch.profile_id) for watch in result.activated_watches] == [
        ("ABC", "profile-1")]
    assert approved.reads >= 3 and source.reads >= 3
    source.current = False
    with pytest.raises(RuntimeError, match="external head changed"):
        authority.head_hash(configuration_revision_id=CONFIG, run_plan_id="plan-1")


def test_proven_plan_missing_or_changed_proof_fails_closed():
    parent, children, watches, _, _ = _fixture()
    with pytest.raises(ValueError, match="proof is missing"):
        ProvenPlanMembershipAuthority(
            Rows(parent, children, watches), Keeper(parent),
            approved=None, source=Proof(SESSION, "d" * 64),
            configuration_revision_id=CONFIG, session_key=SESSION)
    with pytest.raises(ValueError, match="proof is missing"):
        _authority(source=Proof("wrong-session", "d" * 64))
    authority, approved, _ = _authority()
    approved.content_hash = "e" * 64
    with pytest.raises(RuntimeError, match="proof hash changed"):
        authority.read_attested_plan(
            configuration_revision_id=CONFIG, run_plan_id="plan-1")
    with pytest.raises(ValueError, match="scope differs"):
        authority.read_attested_plan(
            configuration_revision_id="other", run_plan_id="plan-1")


def test_proven_plan_rechecks_mutated_identity_and_hash_during_assertion():
    authority, approved, source = _authority()
    source.identity = "another-session"
    with pytest.raises(RuntimeError, match="proof scope changed"):
        authority.head_hash(configuration_revision_id=CONFIG, run_plan_id="plan-1")

    source.identity = SESSION
    original_assert_current = approved.assert_current

    def mutate_after_check():
        original_assert_current()
        approved.content_hash = "e" * 64

    approved.assert_current = mutate_after_check
    with pytest.raises(RuntimeError, match="proof hash changed"):
        authority.head_hash(configuration_revision_id=CONFIG, run_plan_id="plan-1")


def test_numbered_cold_factory_binds_approved_revision_to_isolated_source():
    parent, children, watches, _, _ = _fixture()
    approved = Proof(CONFIG, "c" * 64)
    source = Proof(SESSION, "d" * 64)
    with patch(
        "src.backend.live_proven_plan_membership.cold_approved_configuration_proof",
        return_value=approved,
    ) as approved_reader, patch(
        "src.backend.live_proven_plan_membership.cold_attested_signal_source_cursor",
        return_value=source,
    ) as source_reader:
        authority = cold_strategy_one_plan_membership_authority(
            Rows(parent, children, watches), Keeper(parent),
            configuration_client=object(), approval_reader=object(),
            source_storage=object(), source_commit_client=object(),
            source_keeper=object(), session_key=SESSION,
            source_revision="source-1", catalogs={}, mode="live",
            required_through_at=datetime(2026, 9, 24, 14, tzinfo=timezone.utc))
    assert approved_reader.call_args.kwargs["mode"] == "live"
    assert source_reader.call_args.kwargs["configuration_revision"] == CONFIG
    assert source_reader.call_args.kwargs["strategy_one"] is True
    assert source_reader.call_args.kwargs["required_through_at"] == datetime(
        2026, 9, 24, 14, tzinfo=timezone.utc)
    assert len(authority.read_attested_plan(
        configuration_revision_id=CONFIG, run_plan_id="plan-1").assignments) == 2
    source.current = False
    with pytest.raises(RuntimeError, match="external head changed"):
        authority.head_hash(configuration_revision_id=CONFIG, run_plan_id="plan-1")
