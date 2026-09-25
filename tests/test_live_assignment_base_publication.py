from dataclasses import replace

import pytest

from src.backend.live_assignment_base_publication import (
    UncertainBasePublication, publish_base_revision,
)
from tests.test_live_assignment_base_keeper import _keeper
from tests.test_live_assignment_base_revision import (
    CHILD_REFS, HASH_A, HASH_B, _assignment,
)
from src.trading_runtime.strategy_engine import AssignmentStatus


class FakeBaseStorage:
    def __init__(self):
        self.rows = []
        self.events = []
        self.ambiguous = False

    def read_base_rows(self, assignment_id):
        self.events.append("read")
        return list(self.rows)

    def insert_base(self, row):
        self.events.append("insert")
        self.rows.append(dict(row))
        if self.ambiguous:
            raise TimeoutError("unknown server outcome")


def _publish(storage, keeper, assignment, **extra):
    return publish_base_revision(
        storage, keeper, assignment, owner_id="publisher",
        state_storage=object(), state_admission=object(),
        parameter_storage=object(),
        parameter_admission=object(), parameter_content_hash=HASH_A,
        state_content_hash=HASH_B, **{**CHILD_REFS, **extra})


def test_publication_orders_child_proof_insert_readback_then_keeper_head(monkeypatch):
    assignment = _assignment()
    storage = FakeBaseStorage()
    keeper = _keeper()
    current = {"assignment": assignment}
    def checked(**kwargs):
        storage.events.append("children")
        return current["assignment"]
    monkeypatch.setattr(
        "src.backend.live_assignment_base_publication.recover_attested_assignment",
        checked)
    monkeypatch.setattr(
        "src.backend.live_assignment_base_keeper.recover_attested_assignment",
        checked)
    first = _publish(storage, keeper, assignment)
    assert first.sequence == 1
    assert storage.events.index("children") < storage.events.index("insert")
    assert storage.events[-2:] == ["read", "children"]
    assert keeper.read_head("as-1") == first
    # Status-only base revision reuses the same immutable parameter and state
    # snapshot references, without republishing either child family.
    second_assignment = replace(assignment, status=AssignmentStatus.PAUSED)
    current["assignment"] = second_assignment
    second = _publish(storage, keeper, second_assignment)
    assert second.sequence == 2
    assert len(storage.rows) == 2
    for key in (*CHILD_REFS, "parameter_content_hash", "state_content_hash"):
        assert storage.rows[0][key] == storage.rows[1][key]
    assert storage.rows[1]["previous_revision_hash"] == first.content_hash


def test_ambiguous_insert_retains_claim_and_never_attests(monkeypatch):
    assignment = _assignment()
    monkeypatch.setattr(
        "src.backend.live_assignment_base_publication.recover_attested_assignment",
        lambda **kwargs: assignment)
    storage, keeper = FakeBaseStorage(), _keeper()
    storage.ambiguous = True
    with pytest.raises(UncertainBasePublication, match="ambiguous"):
        _publish(storage, keeper, assignment)
    assert len(storage.rows) == 1
    with pytest.raises(ValueError, match="missing"):
        keeper.read_head("as-1")
    with pytest.raises(RuntimeError, match="claim is held"):
        _publish(storage, keeper, assignment)
    assert len(storage.rows) == 1


def test_orphan_row_and_child_mismatch_fail_before_insert(monkeypatch):
    assignment = _assignment()
    storage, keeper = FakeBaseStorage(), _keeper()
    storage.rows = [{"assignment_id": "as-1", "revision_sequence": 1}]
    with pytest.raises(UncertainBasePublication, match="orphan"):
        _publish(storage, keeper, assignment)
    assert "insert" not in storage.events
    storage.rows = []
    monkeypatch.setattr(
        "src.backend.live_assignment_base_publication.recover_attested_assignment",
        lambda **kwargs: replace(assignment, source="different"))
    with pytest.raises(ValueError, match="differs"):
        _publish(storage, keeper, assignment)
    assert "insert" not in storage.events


def test_lost_keeper_cas_response_retains_claim_and_forbids_retry(monkeypatch):
    assignment = _assignment()
    for module in ("live_assignment_base_publication", "live_assignment_base_keeper"):
        monkeypatch.setattr(
            f"src.backend.{module}.recover_attested_assignment",
            lambda **kwargs: assignment)
    storage, keeper = FakeBaseStorage(), _keeper()
    original = keeper.attest
    def lost_response(*args, **kwargs):
        original(*args, **kwargs)  # Durable Keeper CAS succeeds.
        raise TimeoutError("Keeper response lost")
    monkeypatch.setattr(keeper, "attest", lost_response)
    with pytest.raises(UncertainBasePublication, match="attestation result is ambiguous"):
        _publish(storage, keeper, assignment)
    assert len(storage.rows) == 1
    assert keeper.read_head("as-1").content_hash == storage.rows[0]["content_hash"]
    assert keeper.is_current("as-1", owner_id="publisher", epoch=1)
    with pytest.raises(RuntimeError, match="claim is held"):
        _publish(storage, keeper, assignment)
    assert len(storage.rows) == 1


def test_base_publication_compares_canonical_typed_clock_without_mutating_assignment(monkeypatch):
    from src.trading_runtime.arte_assignment_observation_clock import normalize_observation_state
    assignment = replace(_assignment(), state={
        "last_observed_at": "2026-09-24T13:00:00+00:00"})
    recovered = replace(assignment, state=normalize_observation_state(assignment.state))
    for module in ("live_assignment_base_publication", "live_assignment_base_keeper"):
        monkeypatch.setattr(f"src.backend.{module}.recover_attested_assignment",
                            lambda **kwargs: recovered)
    storage, keeper = FakeBaseStorage(), _keeper()
    assert _publish(storage, keeper, assignment).sequence == 1
    assert assignment.state["last_observed_at"] == "2026-09-24T13:00:00+00:00"
