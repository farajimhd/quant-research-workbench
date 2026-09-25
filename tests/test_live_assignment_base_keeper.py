from dataclasses import replace

import pytest

from src.backend.live_assignment_base_keeper import (
    KeeperAssignmentHead, cold_read_attested_assignment,
)
from src.backend.live_assignment_base_revision import project_base_revision
from tests.test_live_assignment_base_revision import (
    CHILD_REFS, GENESIS, HASH_A, HASH_B, _assignment,
)
from tests.test_live_signal_completion_keeper import FakeKazoo
from src.trading_runtime.keeper_endpoint import KeeperEndpoint
from src.trading_runtime.keeper_session import (
    ManagedKeeperSession, open_workstation_keeper_session,
)


def _keeper(client=None):
    client = client or FakeKazoo()
    client.add_listener = lambda listener: None
    session = ManagedKeeperSession(client)
    session._on_state("CONNECTED")
    return KeeperAssignmentHead(session)


class Rows:
    def __init__(self, rows):
        self.rows = list(rows)

    def read_base_rows(self, assignment_id):
        return list(self.rows)


def _row(sequence=1, prior=GENESIS):
    return project_base_revision(
        _assignment(), revision_sequence=sequence, **CHILD_REFS,
        parameter_content_hash=HASH_A, state_content_hash=HASH_B,
        previous_revision_hash=prior)


def _attest(keeper, row, owner, epoch, previous, rows):
    return keeper.attest(row, owner_id=owner, epoch=epoch, previous=previous,
                         base_rows=rows, state_storage=object(),
                         parameter_storage=object(), parameter_admission=object())


def test_keeper_head_cas_cold_chain_and_parameter_state_verification(monkeypatch):
    checked = []
    monkeypatch.setattr("src.backend.live_assignment_base_keeper.recover_attested_assignment",
                        lambda **kwargs: checked.append(kwargs) or _assignment())
    client = FakeKazoo()
    keeper = _keeper(client)
    first_row = _row()
    rows = Rows([first_row])
    epoch = keeper.acquire("as-1", owner_id="owner-a")
    assert epoch == 1
    first = _attest(keeper, first_row, "owner-a", epoch, None, rows)
    assert first.sequence == 1 and first.content_hash == first_row["content_hash"]
    assert cold_read_attested_assignment(
        rows, keeper, assignment_id="as-1", state_storage=object(),
        parameter_storage=object(), parameter_admission=object()) == _assignment()
    assert keeper.release("as-1", owner_id="owner-a", epoch=epoch)
    next_epoch = keeper.acquire("as-1", owner_id="owner-b")
    assert next_epoch == 2
    second_row = _row(2, first.content_hash)
    rows.rows.append(second_row)
    with pytest.raises(RuntimeError, match="owner fence"):
        _attest(keeper, second_row, "owner-a", epoch, first, rows)
    second = _attest(keeper, second_row, "owner-b", next_epoch, first, rows)
    assert second.sequence == 2 and second.keeper_version == 1
    assert len(checked) == 3


def test_keeper_rejects_duplicate_readback_and_concurrent_holder_loss(monkeypatch):
    monkeypatch.setattr("src.backend.live_assignment_base_keeper.recover_attested_assignment",
                        lambda **kwargs: _assignment())
    client = FakeKazoo()
    keeper = _keeper(client)
    row = _row()
    epoch = keeper.acquire("as-1", owner_id="owner")
    with pytest.raises(ValueError, match="readback"):
        _attest(keeper, row, "owner", epoch, None, Rows([row, row]))
    holder = f"{keeper.path('as-1')}/holder"
    def replace_holder():
        raw, version, owner = client.nodes[holder]
        client.nodes[holder] = (raw, version + 1, owner)
    client.before_commit = replace_holder
    with pytest.raises(RuntimeError, match="CAS"):
        _attest(keeper, row, "owner", epoch, None, Rows([row]))
    with pytest.raises(ValueError, match="missing"):
        keeper.read_head("as-1")


def test_keeper_cold_reader_rejects_gap_duplicate_and_head_change(monkeypatch):
    monkeypatch.setattr("src.backend.live_assignment_base_keeper.recover_attested_assignment",
                        lambda **kwargs: _assignment())
    keeper = _keeper()
    first = _row()
    epoch = keeper.acquire("as-1", owner_id="owner")
    head = _attest(keeper, first, "owner", epoch, None, Rows([first]))
    second = _row(2, head.content_hash)
    with pytest.raises(ValueError, match="gap"):
        cold_read_attested_assignment(Rows([first, first]), keeper,
            assignment_id="as-1", state_storage=object(),
            parameter_storage=object(), parameter_admission=object())
    next_head = _attest(keeper, second, "owner", epoch, head, Rows([first, second]))
    assert next_head.sequence == 2
    with pytest.raises(ValueError, match="gap"):
        cold_read_attested_assignment(Rows([second]), keeper,
            assignment_id="as-1", state_storage=object(),
            parameter_storage=object(), parameter_admission=object())


def test_keeper_requires_managed_discovered_session_and_safe_owner():
    with pytest.raises(TypeError, match="managed"):
        KeeperAssignmentHead(FakeKazoo())
    captured = []
    class DiscoverableFake(FakeKazoo):
        def __init__(self, hosts, timeout):
            super().__init__()
            captured.append(hosts)
        def add_listener(self, listener):
            pass
        def remove_listener(self, listener):
            pass
        def start(self, timeout):
            pass
        def stop(self):
            pass
        def close(self):
            pass
    session = open_workstation_keeper_session(
        client_factory=DiscoverableFake,
        discover=lambda: KeeperEndpoint("172.26.16.2"))
    keeper = KeeperAssignmentHead(session)
    assert captured == ["172.26.16.2:9181"]
    with pytest.raises(ValueError, match="identity"):
        keeper.acquire("as-1", owner_id="bad\nowner")
    session.close()


def test_keeper_rejects_future_orphan_row_before_head_attestation(monkeypatch):
    monkeypatch.setattr("src.backend.live_assignment_base_keeper.recover_attested_assignment",
                        lambda **kwargs: _assignment())
    keeper = _keeper()
    first = _row()
    future = _row(2, first["content_hash"])
    epoch = keeper.acquire("as-1", owner_id="owner")
    with pytest.raises(ValueError, match="orphan"):
        _attest(keeper, first, "owner", epoch, None, Rows([first, future]))
    with pytest.raises(ValueError, match="missing"):
        keeper.read_head("as-1")


def test_keeper_rejects_same_owner_aba_and_stale_head_retry(monkeypatch):
    monkeypatch.setattr("src.backend.live_assignment_base_keeper.recover_attested_assignment",
                        lambda **kwargs: _assignment())
    keeper = _keeper()
    first = _row()
    epoch = keeper.acquire("as-1", owner_id="owner")
    assert keeper.release("as-1", owner_id="owner", epoch=epoch)
    replacement = keeper.acquire("as-1", owner_id="owner")
    assert replacement == epoch + 1
    with pytest.raises(RuntimeError, match="owner fence"):
        _attest(keeper, first, "owner", epoch, None, Rows([first]))
    head = _attest(keeper, first, "owner", replacement, None, Rows([first]))
    with pytest.raises(RuntimeError, match="already exists"):
        _attest(keeper, first, "owner", replacement, None, Rows([first]))
    second = _row(2, head.content_hash)
    _attest(keeper, second, "owner", replacement, head, Rows([first, second]))
    with pytest.raises(RuntimeError, match="head changed"):
        _attest(keeper, second, "owner", replacement, head, Rows([first, second]))


def test_cold_read_detects_concurrent_head_change(monkeypatch):
    keeper = _keeper()
    first = _row()
    epoch = keeper.acquire("as-1", owner_id="owner")
    monkeypatch.setattr("src.backend.live_assignment_base_keeper.recover_attested_assignment",
                        lambda **kwargs: _assignment())
    _attest(keeper, first, "owner", epoch, None, Rows([first]))
    original = keeper.read_head
    calls = []
    def changed_head(assignment_id):
        head = original(assignment_id)
        calls.append(head)
        return head if len(calls) == 1 else replace(head, keeper_version=head.keeper_version + 1)
    monkeypatch.setattr(keeper, "read_head", changed_head)
    with pytest.raises(RuntimeError, match="changed during cold read"):
        cold_read_attested_assignment(Rows([first]), keeper,
            assignment_id="as-1", state_storage=object(),
            parameter_storage=object(), parameter_admission=object())
