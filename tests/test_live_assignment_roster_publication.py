from copy import deepcopy

import pytest

from src.backend.live_assignment_roster import RosterChange, RosterHead
from src.backend.live_assignment_roster_publication import (
    KeeperRosterOwnerFence, UncertainRosterPublication, publish_roster_revision,
    publish_rostered_base_revision,
)
from src.trading_runtime.keeper_session import ManagedKeeperSession
from tests.test_live_signal_completion_keeper import FakeKazoo


class Storage:
    def __init__(self):
        self.parents = []
        self.children = {}
        self.events = []
        self.fail = None

    def read_revisions(self, *, limit):
        self.events.append("read_parent")
        return deepcopy(self.parents[:limit])

    def read_changes(self, sequence, *, limit):
        self.events.append("read_child")
        return deepcopy(self.children.get(sequence, [])[:limit])

    def insert_changes(self, rows):
        self.events.append("insert_child")
        self.children[rows[0]["sequence"]] = deepcopy(list(rows))
        if self.fail == "child":
            raise TimeoutError("ambiguous")

    def insert_revision(self, row):
        self.events.append("insert_parent")
        self.parents.append(deepcopy(row))
        if self.fail == "parent":
            raise TimeoutError("ambiguous")


def _keeper():
    client = FakeKazoo()
    client.add_listener = lambda _listener: None
    session = ManagedKeeperSession(client)
    session._on_state("CONNECTED")
    return KeeperRosterOwnerFence(session), client


class Bases:
    def read_head(self, assignment_id):
        assert assignment_id == "a"
        return type("Head", (), {"sequence": self.sequence,
                                 "content_hash": self.content_hash})()

    def __init__(self, sequence=1, content_hash="a" * 64):
        self.sequence, self.content_hash = sequence, content_hash


def test_roster_rows_first_exact_readback_then_keeper_head():
    storage, (keeper, _) = Storage(), _keeper()
    head = publish_roster_revision(
        storage, keeper, [RosterChange("a", "upsert", 1, "a" * 64)],
        owner_id="publisher", base_heads=Bases())
    assert head == RosterHead(1, storage.parents[0]["content_hash"], 0)
    assert storage.events == ["read_parent", "insert_child", "insert_parent",
                              "read_parent", "read_child"]
    assert keeper.read_head_or_none() == head
    second = publish_roster_revision(
        storage, keeper, [RosterChange("a", "upsert", 2, "b" * 64)],
        owner_id="publisher", base_heads=Bases(2, "b" * 64))
    assert second.sequence == 2 and second.keeper_version == 1


@pytest.mark.parametrize("failure", ["child", "parent"])
def test_ambiguous_insert_retains_owner_and_forbids_retry(failure):
    storage, (keeper, _) = Storage(), _keeper()
    storage.fail = failure
    with pytest.raises(UncertainRosterPublication, match="ambiguous"):
        publish_roster_revision(storage, keeper,
                                [RosterChange("a", "upsert", 1, "a" * 64)],
                                owner_id="publisher", base_heads=Bases())
    assert keeper.is_current(owner_id="publisher", epoch=1)
    with pytest.raises(RuntimeError, match="held"):
        publish_roster_revision(storage, keeper,
                                [RosterChange("a", "upsert", 1, "a" * 64)],
                                owner_id="publisher", base_heads=Bases())


def test_lost_keeper_cas_response_is_uncertain_and_retains_owner(monkeypatch):
    storage, (keeper, _) = Storage(), _keeper()
    original = keeper.attest
    def lost(*args, **kwargs):
        original(*args, **kwargs)
        raise TimeoutError("lost response")
    monkeypatch.setattr(keeper, "attest", lost)
    with pytest.raises(UncertainRosterPublication, match="uncertain"):
        publish_roster_revision(storage, keeper,
                                [RosterChange("a", "upsert", 1, "a" * 64)],
                                owner_id="publisher", base_heads=Bases())
    assert keeper.read_head_or_none().sequence == 1
    assert keeper.is_current(owner_id="publisher", epoch=1)


def test_keeper_cas_rejects_stale_owner_epoch():
    keeper, client = _keeper()
    from src.backend.live_assignment_base_keeper import KeeperAssignmentHead
    assert keeper._owner.path("live-strategy-assignments") != (
        KeeperAssignmentHead.path("live-strategy-assignments"))
    assert keeper.acquire(owner_id="old") == 1
    assert keeper.release(owner_id="old", epoch=1)
    assert keeper.acquire(owner_id="new") == 2
    assert not keeper.is_current(owner_id="old", epoch=1)
    assert keeper.read_head_or_none() is None


def test_roster_rejects_base_mismatch_before_any_insert():
    storage, (keeper, _) = Storage(), _keeper()
    with pytest.raises(ValueError, match="base head"):
        publish_roster_revision(storage, keeper,
                                [RosterChange("a", "upsert", 1, "a" * 64)],
                                owner_id="publisher", base_heads=Bases(2))
    assert "insert_child" not in storage.events


def test_coordinator_holds_same_epoch_through_base_ack_and_roster_cas(monkeypatch):
    storage, (keeper, _) = Storage(), _keeper()
    assignment = type("Assignment", (), {"assignment_id": "a"})()
    observed = []
    def base_publish(*args, **kwargs):
        observed.append(kwargs["roster_epoch"])
        assert kwargs["roster_fence"] is keeper
        assert keeper.is_current(owner_id="publisher", epoch=observed[-1])
        return type("Head", (), {"sequence": 1, "content_hash": "a" * 64})()
    monkeypatch.setattr(
        "src.backend.live_assignment_base_publication.publish_base_revision",
        base_publish)
    base, roster = publish_rostered_base_revision(
        storage, keeper, object(), object(), assignment,
        owner_id="publisher", base_heads=Bases())
    assert base.sequence == roster.sequence == 1
    assert observed == [1]
    assert not keeper.is_current(owner_id="publisher", epoch=1)


def test_coordinator_retains_owner_after_ambiguous_base_ack(monkeypatch):
    storage, (keeper, _) = Storage(), _keeper()
    assignment = type("Assignment", (), {"assignment_id": "a"})()
    def lost(*args, **kwargs):
        assert keeper.is_current(owner_id="publisher", epoch=kwargs["roster_epoch"])
        raise TimeoutError("base ACK lost")
    monkeypatch.setattr(
        "src.backend.live_assignment_base_publication.publish_base_revision", lost)
    with pytest.raises(TimeoutError, match="ACK lost"):
        publish_rostered_base_revision(
            storage, keeper, object(), object(), assignment,
            owner_id="publisher", base_heads=Bases())
    assert keeper.is_current(owner_id="publisher", epoch=1)
    assert storage.parents == []
