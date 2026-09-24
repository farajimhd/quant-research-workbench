from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from types import SimpleNamespace

import pytest

from src.trading_runtime.arte_long_momentum_parameter_journal import (
    COMMIT_TABLE, UncommittedParameterSnapshot, load_attested_parameters,
    load_diagnostic_parameters, publish_parameters,
)
from src.trading_runtime.keeper_ownership import KeeperUnavailable, _encode, _path
from src.trading_runtime.keeper_parameter_admission import KeeperParameterSnapshotAdmission
from src.trading_runtime.strategy_engine import resolve_long_momentum_parameters


IDENTITY = dict(assignment_id="assignment-1", strategy_id="long-momentum-campaign",
                strategy_revision=47, snapshot_id="4a2c2393-d13d-4ecb-9fae-b5e26ea9ff6f",
                session="2026-09-24")
DIGEST = "a" * 64
RESOURCE = "assignment-1"


class NodeExistsError(Exception):
    pass


class NoNodeError(Exception):
    pass


class BadVersionError(Exception):
    pass


class FakeTransaction:
    def __init__(self, client: "FakeKeeper") -> None:
        self.client = client
        self.ops: list[tuple] = []

    def check(self, path: str, *, version: int) -> "FakeTransaction":
        self.ops.append(("check", path, version))
        return self

    def create(self, path: str, value: bytes) -> "FakeTransaction":
        self.ops.append(("create", path, value))
        return self

    def set_data(self, path: str, value: bytes, *, version: int) -> "FakeTransaction":
        self.ops.append(("set", path, value, version))
        return self

    def commit(self) -> list:
        with self.client.lock:
            if self.client.replace_during_transaction:
                self.client.replace_during_transaction = False
                self.client._install_owner_unlocked("worker-b", 9)
            try:
                for op in self.ops:
                    kind, path = op[:2]
                    if kind == "check" and (path not in self.client.nodes
                                            or self.client.nodes[path][1] != op[2]):
                        raise BadVersionError()
                    if kind == "create" and path in self.client.nodes:
                        raise NodeExistsError()
                    if kind == "set" and (path not in self.client.nodes
                                          or self.client.nodes[path][1] != op[3]):
                        raise BadVersionError()
                for op in self.ops:
                    kind, path = op[:2]
                    if kind == "create":
                        self.client.nodes[path] = (op[2], 0, 0)
                    if kind == "set":
                        self.client.nodes[path] = (op[2], self.client.nodes[path][1] + 1, 0)
            except Exception as exc:
                return [exc]
        if self.client.create_then_error:
            self.client.create_then_error = False
            raise TimeoutError("transaction acknowledgment lost")
        return [True for _ in self.ops]


class FakeKeeper:
    def __init__(self) -> None:
        self.connected = True
        self.client_state = SimpleNamespace(name="CONNECTED")
        self.client_id = (123, b"fake")
        self.nodes: dict[str, tuple[bytes, int, int]] = {}
        self.lock = Lock()
        self.create_then_error = False
        self.replace_during_transaction = False

    def ensure_path(self, path: str) -> None:
        assert path.startswith("/trading/")

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)

    def get(self, path: str) -> tuple[bytes, SimpleNamespace]:
        with self.lock:
            if path not in self.nodes:
                raise NoNodeError()
            value, version, ephemeral_owner = self.nodes[path]
            return value, SimpleNamespace(version=version, ephemeralOwner=ephemeral_owner)

    def _install_owner_unlocked(self, owner: str, epoch: int) -> None:
        base = _path("portfolio", RESOURCE)
        counter_path, holder_path = f"{base}/epoch", f"{base}/holder"
        old_version = self.nodes[counter_path][1] if counter_path in self.nodes else -1
        self.nodes[counter_path] = (str(epoch).encode(), old_version + 1, 0)
        self.nodes[holder_path] = (_encode(owner, epoch, "portfolio"), 0, self.client_id[0])

    def install_owner(self, owner: str, epoch: int) -> None:
        with self.lock:
            self._install_owner_unlocked(owner, epoch)


class FakeCoordinator:
    def __init__(self, client: FakeKeeper) -> None:
        self.client = client

    def portfolio_admission_lease_is_current(self, resource_id: str, *, owner_id: str, epoch: int) -> bool:
        assert resource_id == RESOURCE
        base = _path("portfolio", resource_id)
        try:
            holder, stat = self.client.get(f"{base}/holder")
            counter, _ = self.client.get(f"{base}/epoch")
        except NoNodeError:
            return False
        return (holder == _encode(owner_id, epoch, "portfolio")
                and int(counter) == epoch and stat.ephemeralOwner == self.client.client_id[0])


class FakeParameterStorage:
    def __init__(self) -> None:
        self.rows: dict[str, list[dict]] = {}

    def insert(self, table: str, rows: list[dict]) -> None:
        self.rows.setdefault(table, []).extend(dict(row) for row in rows)

    def read(self, table: str, identity: dict) -> list[dict]:
        return [dict(row) for row in self.rows.get(table, [])
                if all(row[key] == value for key, value in identity.items())]


def admission(client: FakeKeeper, owner: str, epoch: int) -> KeeperParameterSnapshotAdmission:
    return KeeperParameterSnapshotAdmission(client, FakeCoordinator(client),
                                            resource_id=RESOURCE, owner_id=owner, owner_epoch=epoch)


def test_persistent_claim_restart_and_read_only_reconciliation() -> None:
    client = FakeKeeper()
    client.install_owner("worker-a", 8)
    first = admission(client, "worker-a", 8)
    assert first.read_claim(IDENTITY) is None
    assert first.begin_once(IDENTITY)
    restarted = admission(client, "worker-a", 8)
    assert not restarted.begin_once(IDENTITY)
    first.mark_committed(IDENTITY, DIGEST)
    status = restarted.read_claim(IDENTITY)
    assert (status.owner_id, status.owner_epoch, status.state, status.content_hash) == (
        "worker-a", 8, "committed", DIGEST,
    )
    assert not restarted.begin_once(IDENTITY)


def test_atomic_contention_and_stale_owner_aba_cannot_publish_or_mark() -> None:
    client = FakeKeeper()
    client.install_owner("worker-a", 8)
    first, second = admission(client, "worker-a", 8), admission(client, "worker-a", 8)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda owner: owner.begin_once(IDENTITY), (first, second))) == [False, True]
    client.install_owner("worker-b", 9)
    with pytest.raises(KeeperUnavailable, match="not current"):
        first.mark_committed(IDENTITY, DIGEST)
    with pytest.raises(KeeperUnavailable, match="not current"):
        first.begin_once({**IDENTITY, "snapshot_id": "5a2c2393-d13d-4ecb-9fae-b5e26ea9ff6f"})


def test_holder_replacement_during_transaction_cas_fails_closed() -> None:
    client = FakeKeeper()
    client.install_owner("worker-a", 8)
    first = admission(client, "worker-a", 8)
    client.replace_during_transaction = True
    with pytest.raises(KeeperUnavailable, match="ambiguous"):
        first.begin_once(IDENTITY)
    assert first.read_claim(IDENTITY) is None
    client.install_owner("worker-a", 10)
    next_owner = admission(client, "worker-a", 10)
    assert next_owner.begin_once(IDENTITY)
    client.replace_during_transaction = True
    with pytest.raises(KeeperUnavailable, match="fence|CAS"):
        next_owner.mark_committed(IDENTITY, DIGEST)
    assert next_owner.read_claim(IDENTITY).state == "started"


def test_ambiguous_create_consumes_claim_and_disconnection_fails_closed() -> None:
    client = FakeKeeper()
    client.install_owner("worker-a", 8)
    client.create_then_error = True
    first = admission(client, "worker-a", 8)
    with pytest.raises(KeeperUnavailable, match="ambiguous"):
        first.begin_once(IDENTITY)
    assert not admission(client, "worker-a", 8).begin_once(IDENTITY)
    client.connected = False
    with pytest.raises(KeeperUnavailable, match="not writable"):
        first.read_claim(IDENTITY)


def test_read_only_reconciliation_requires_complete_typed_commit() -> None:
    keeper = FakeKeeper()
    keeper.install_owner("worker-a", 8)
    store = FakeParameterStorage()
    owner = admission(keeper, "worker-a", 8)
    assert owner.diagnose_commit(store, IDENTITY) is None
    parameters = resolve_long_momentum_parameters(revision=47)
    publish_parameters(store, parameters, admission=owner, **IDENTITY)
    before_keeper = dict(keeper.nodes)
    before_tables = {table: list(rows) for table, rows in store.rows.items()}
    assert owner.diagnose_commit(store, IDENTITY) == parameters
    assert load_attested_parameters(store, owner, **IDENTITY) == parameters
    assert keeper.nodes == before_keeper and store.rows == before_tables
    keeper.install_owner("worker-b", 9)
    assert load_attested_parameters(store, owner, **IDENTITY) == parameters
    store.rows[next(iter(store.rows))].clear()
    with pytest.raises(ValueError, match="fence"):
        owner.diagnose_commit(store, IDENTITY)


def test_owner_replaced_after_first_child_insert_stops_commit() -> None:
    keeper = FakeKeeper()
    keeper.install_owner("worker-a", 8)

    class ReplacingStorage(FakeParameterStorage):
        def insert(self, table: str, rows: list[dict]) -> None:
            super().insert(table, rows)
            if len(self.rows) == 1:
                keeper.install_owner("worker-b", 9)

    store = ReplacingStorage()
    owner = admission(keeper, "worker-a", 8)
    with pytest.raises(KeeperUnavailable, match="not current"):
        publish_parameters(store, resolve_long_momentum_parameters(revision=47),
                           admission=owner, **IDENTITY)
    assert COMMIT_TABLE.name not in store.rows
    assert owner.read_claim(IDENTITY).state == "started"


def test_owner_lost_after_ch_commit_remains_diagnostic_only() -> None:
    keeper = FakeKeeper()
    keeper.install_owner("worker-a", 8)

    class ReplaceAfterCommit(FakeParameterStorage):
        def insert(self, table: str, rows: list[dict]) -> None:
            super().insert(table, rows)
            if table == COMMIT_TABLE.name:
                keeper.install_owner("worker-b", 9)

    store = ReplaceAfterCommit()
    owner = admission(keeper, "worker-a", 8)
    parameters = resolve_long_momentum_parameters(revision=47)
    with pytest.raises(KeeperUnavailable, match="not current"):
        publish_parameters(store, parameters, admission=owner, **IDENTITY)
    assert load_diagnostic_parameters(store, **IDENTITY) == parameters
    assert owner.read_claim(IDENTITY).state == "started"
    assert owner.diagnose_commit(store, IDENTITY) == parameters
    with pytest.raises(UncommittedParameterSnapshot, match="committed Keeper claim"):
        load_attested_parameters(store, owner, **IDENTITY)
    with pytest.raises(UncommittedParameterSnapshot, match="committed Keeper claim"):
        publish_parameters(store, parameters, admission=owner, **IDENTITY)
