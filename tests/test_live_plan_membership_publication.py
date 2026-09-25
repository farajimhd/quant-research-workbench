from copy import deepcopy

import pytest

from src.backend.live_assignment_activation_join import PinnedAssignmentMember
from src.backend.live_plan_membership import PlanWatchMember
from src.backend.live_plan_membership_publication import (
    KeeperPlanMembershipPublisher, UncertainMembershipPublication,
    publish_plan_membership,
)
from src.trading_runtime.keeper_session import ManagedKeeperSession
from tests.test_live_signal_completion_keeper import FakeKazoo


class Proof:
    def __init__(self, identity, content_hash):
        self.identity, self.content_hash = identity, content_hash
        self.current = True
    def assert_current(self):
        if not self.current:
            raise RuntimeError("proof head changed")


class Rows:
    def __init__(self):
        self.parents = []
        self.members = {}
        self.watches = {}
        self.events = []
        self.ambiguous = False
    def read_revisions(self, *, configuration_revision_id, session_key, limit):
        self.events.append("read_parent")
        return deepcopy(self.parents[:limit])
    def read_members(self, *, configuration_revision_id, session_key,
                     membership_sequence, limit):
        self.events.append("read_members")
        return deepcopy(self.members.get(membership_sequence, [])[:limit])
    def read_watches(self, *, configuration_revision_id, session_key,
                     membership_sequence, limit):
        self.events.append("read_watches")
        return deepcopy(self.watches.get(membership_sequence, [])[:limit])
    def insert_members(self, rows):
        self.events.append("insert_members")
        self.members[rows[0]["membership_sequence"]] = deepcopy(list(rows))
    def insert_watches(self, rows):
        self.events.append("insert_watches")
        self.watches[rows[0]["membership_sequence"]] = deepcopy(list(rows))
    def insert_revision(self, row):
        self.events.append("insert_parent")
        self.parents.append(deepcopy(row))
        if self.ambiguous:
            raise TimeoutError("unknown INSERT result")


def _keeper():
    client = FakeKazoo()
    client.add_listener = lambda _listener: None
    session = ManagedKeeperSession(client)
    session._on_state("CONNECTED")
    return KeeperPlanMembershipPublisher(session)


def _publish(rows, keeper, *, approved=None, source=None):
    return publish_plan_membership(
        rows, keeper,
        [PinnedAssignmentMember("as-1", "plan-1", 1, "a" * 64)],
        [PlanWatchMember("plan-1", "ABC", "profile-1", "default")],
        configuration_revision_id="config-1", session_key="2026-09-24",
        approved_revision=approved or Proof("config-1", "b" * 64),
        source_cursor=source or Proof("2026-09-24", "c" * 64),
        owner_id="publisher")


def test_membership_rows_first_exact_readback_then_keeper_head():
    rows, keeper = Rows(), _keeper()
    head = _publish(rows, keeper)
    assert head[:2] == (1, rows.parents[0]["content_hash"])
    assert rows.events == ["read_parent", "read_members", "read_watches",
                           "insert_members", "insert_watches",
                           "insert_parent", "read_parent", "read_members",
                           "read_watches"]
    assert keeper.read_head_or_none(configuration_revision_id="config-1",
                                    session_key="2026-09-24") == head
    assert _publish(rows, keeper)[0] == 2


def test_ambiguous_insert_and_lost_cas_response_retain_keeper_owner(monkeypatch):
    rows, keeper = Rows(), _keeper()
    rows.ambiguous = True
    with pytest.raises(UncertainMembershipPublication, match="ambiguous"):
        _publish(rows, keeper)
    assert keeper.is_current(configuration_revision_id="config-1",
                             session_key="2026-09-24", owner_id="publisher", epoch=1)
    with pytest.raises(RuntimeError, match="held"):
        _publish(rows, keeper)
    rows, keeper = Rows(), _keeper()
    original = keeper.attest
    def lost(*args, **kwargs):
        original(*args, **kwargs)
        raise TimeoutError("Keeper response lost")
    monkeypatch.setattr(keeper, "attest", lost)
    with pytest.raises(UncertainMembershipPublication, match="uncertain"):
        _publish(rows, keeper)
    assert keeper.read_head_or_none(configuration_revision_id="config-1",
                                    session_key="2026-09-24")[0] == 1
    assert keeper.is_current(configuration_revision_id="config-1",
                             session_key="2026-09-24", owner_id="publisher", epoch=1)


def test_missing_or_stale_external_proof_fails_before_insert():
    rows, keeper = Rows(), _keeper()
    with pytest.raises(ValueError, match="proof"):
        publish_plan_membership(
            rows, keeper, [], [], configuration_revision_id="config-1",
            session_key="2026-09-24", approved_revision=None,
            source_cursor=None, owner_id="publisher")
    proof = Proof("config-1", "b" * 64)
    proof.current = False
    with pytest.raises(RuntimeError, match="proof head changed"):
        _publish(rows, keeper, approved=proof)
    assert rows.events == []


def test_orphan_child_at_next_sequence_fails_closed_before_insert():
    rows, keeper = Rows(), _keeper()
    rows.members[1] = [{"membership_sequence": 1}]
    with pytest.raises(UncertainMembershipPublication, match="orphan child"):
        _publish(rows, keeper)
    assert not any(event.startswith("insert_") for event in rows.events)
    assert keeper.is_current(configuration_revision_id="config-1",
                             session_key="2026-09-24", owner_id="publisher", epoch=1)
