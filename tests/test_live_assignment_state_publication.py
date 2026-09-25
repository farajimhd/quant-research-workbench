from copy import deepcopy

import pytest

from src.backend.live_assignment_state_admission import KeeperStateSnapshotAdmission
from src.backend.live_assignment_state_snapshot import (
    STATE_COMMIT, UncertainStatePublication, load_attested_state_snapshot,
    publish_state_snapshot,
)
from tests.test_arte_assignment_state_composite import KEY, _state
from tests.test_live_assignment_base_keeper import _keeper


class FakeStateStorage:
    def __init__(self):
        self.rows = {}
        self.events = []
        self.ambiguous_table = None

    def read(self, table, identity):
        self.events.append(("read", table))
        return deepcopy(self.rows.get(table, []))

    def insert(self, table, rows):
        self.events.append(("insert", table))
        self.rows.setdefault(table, []).extend(deepcopy(rows))
        if table == self.ambiguous_table:
            raise TimeoutError("unknown INSERT result")


def _admission(keeper, owner="publisher"):
    epoch = keeper.acquire(KEY["assignment_id"], owner_id=owner)
    assert epoch is not None
    return KeeperStateSnapshotAdmission(
        keeper, assignment_id=KEY["assignment_id"],
        owner_id=owner, owner_epoch=epoch), epoch


def test_state_publication_late_commit_and_attested_cold_read():
    keeper = _keeper()
    admission, epoch = _admission(keeper)
    storage = FakeStateStorage()
    commit = publish_state_snapshot(storage, admission, _state(), **KEY)
    assert storage.rows[STATE_COMMIT.name] == [commit]
    assert storage.events.index(("insert", STATE_COMMIT.name)) > max(
        index for index, event in enumerate(storage.events)
        if event[0] == "insert" and event[1] != STATE_COMMIT.name)
    assert admission.read_claim(KEY).state == "committed"
    assert load_attested_state_snapshot(storage, admission, **KEY,
                                        expected_commit_hash=commit["content_hash"]) == _state()
    with pytest.raises(UncertainStatePublication, match="already exists"):
        publish_state_snapshot(storage, admission, _state(), **KEY)
    assert keeper.release(KEY["assignment_id"], owner_id="publisher", epoch=epoch)


def test_state_publication_normalizes_zero_microsecond_clock_only_in_typed_rows():
    keeper = _keeper()
    admission, _ = _admission(keeper)
    storage = FakeStateStorage()
    source = {**_state(), "last_observed_at": "2026-09-24T13:00:00+00:00",
              "last_price": 101.0}
    commit = publish_state_snapshot(storage, admission, source, **KEY)
    restored = load_attested_state_snapshot(
        storage, admission, **KEY, expected_commit_hash=commit["content_hash"])
    assert restored["last_observed_at"] == "2026-09-24T13:00:00.000000+00:00"
    assert source["last_observed_at"] == "2026-09-24T13:00:00+00:00"


def test_state_publication_ambiguous_child_never_retries():
    keeper = _keeper()
    admission, _ = _admission(keeper)
    storage = FakeStateStorage()
    # First non-empty child family is the campaign control row.
    from src.trading_runtime.arte_campaign_control_projection import TABLES
    storage.ambiguous_table = TABLES[0].name
    with pytest.raises(UncertainStatePublication, match="partial or ambiguous"):
        publish_state_snapshot(storage, admission, _state(), **KEY)
    assert admission.read_claim(KEY).state == "started"
    assert storage.rows[storage.ambiguous_table]
    assert STATE_COMMIT.name not in storage.rows
    count = len(storage.events)
    with pytest.raises(UncertainStatePublication, match="already exists"):
        publish_state_snapshot(storage, admission, _state(), **KEY)
    assert not any(event[0] == "insert" for event in storage.events[count:])


def test_state_mark_rejects_owner_aba_and_started_claim_is_not_authority():
    keeper = _keeper()
    admission, epoch = _admission(keeper)
    assert admission.begin_once(KEY)
    assert keeper.release(KEY["assignment_id"], owner_id="publisher", epoch=epoch)
    replacement = keeper.acquire(KEY["assignment_id"], owner_id="publisher")
    assert replacement == epoch + 1
    with pytest.raises(RuntimeError, match="owner fence"):
        admission.mark_committed(KEY, "a" * 64)
    with pytest.raises(ValueError, match="committed Keeper claim"):
        load_attested_state_snapshot(FakeStateStorage(), admission, **KEY,
                                     expected_commit_hash="a" * 64)


def test_state_claim_rejects_duplicate_and_owner_loss_during_cas():
    keeper = _keeper()
    admission, _ = _admission(keeper)
    assert admission.begin_once(KEY)
    assert not admission.begin_once(KEY)
    holder = f"{keeper.path(KEY['assignment_id'])}/holder"
    def replace_holder():
        raw, version, owner = keeper._client.nodes[holder]
        keeper._client.nodes[holder] = (raw, version + 1, owner)
    keeper._client.before_commit = replace_holder
    with pytest.raises(RuntimeError, match="CAS"):
        admission.mark_committed(KEY, "a" * 64)
    assert admission.read_claim(KEY).state == "started"


def test_lost_keeper_mark_response_remains_uncertain_and_read_only():
    keeper = _keeper()
    admission, _ = _admission(keeper)
    storage = FakeStateStorage()
    original = keeper._client.transaction
    calls = 0
    def transaction():
        nonlocal calls
        calls += 1
        txn = original()
        if calls == 1:
            return txn  # begin_once
        class LostResponse:
            def check(self, *args, **kwargs):
                txn.check(*args, **kwargs)
                return self
            def set_data(self, *args, **kwargs):
                txn.set_data(*args, **kwargs)
                return self
            def commit(self):
                txn.commit()  # durable mark succeeds before response loss
                raise TimeoutError("Keeper mark response lost")
        return LostResponse()
    keeper._client.transaction = transaction
    with pytest.raises(UncertainStatePublication, match="partial or ambiguous"):
        publish_state_snapshot(storage, admission, _state(), **KEY)
    commit = storage.rows[STATE_COMMIT.name][0]
    assert admission.read_claim(KEY).state == "committed"
    assert load_attested_state_snapshot(storage, admission, **KEY,
                                        expected_commit_hash=commit["content_hash"]) == _state()
    keeper._client.transaction = original
    inserts = sum(event[0] == "insert" for event in storage.events)
    with pytest.raises(UncertainStatePublication, match="already exists"):
        publish_state_snapshot(storage, admission, _state(), **KEY)
    assert sum(event[0] == "insert" for event in storage.events) == inserts
