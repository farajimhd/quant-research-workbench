from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.trading_runtime.arte_market_day_keeper import (
    MarketDayKeeperAuthority, require_attested_inventory,
)
from src.trading_runtime.keeper_ownership import KeeperUnavailable


BUILD = "a" * 64
DIGEST = "b" * 64


class NoNodeError(Exception):
    pass


class NodeExistsError(Exception):
    pass


class BadVersionError(Exception):
    pass


@dataclass
class Stat:
    version: int = 0
    ephemeralOwner: int = 0


class FakeTransaction:
    def __init__(self, store):
        self.store = store
        self.actions = []

    def check(self, path, version):
        self.actions.append(("check", path, version))

    def set_data(self, path, value, version):
        self.actions.append(("set", path, value, version))

    def create(self, path, value, ephemeral=False):
        self.actions.append(("create", path, value, ephemeral))

    def commit(self):
        if self.store.before_commit:
            callback, self.store.before_commit = self.store.before_commit, None
            callback()
        for action in self.actions:
            kind, path = action[:2]
            if kind in {"check", "set"} and (
                path not in self.store.rows or self.store.rows[path][1].version != action[-1]
            ):
                return [BadVersionError()]
            if kind == "create" and path in self.store.rows:
                return [NodeExistsError()]
        for action in self.actions:
            kind, path = action[:2]
            if kind == "set":
                previous = self.store.rows[path][1]
                self.store.rows[path] = (action[2], Stat(previous.version + 1,
                                                       previous.ephemeralOwner))
            elif kind == "create":
                self.store.rows[path] = (action[2], Stat(
                    ephemeralOwner=self.store.client_id[0] if action[3] else 0))
        return [True] * len(self.actions)


class FakeKeeper:
    connected = True
    client_id = (11, "")

    def __init__(self):
        self.rows = {}
        self.before_commit = None

    def ensure_path(self, path):
        pass

    def create(self, path, value):
        if path in self.rows:
            raise NodeExistsError()
        self.rows[path] = (value, Stat())

    def get(self, path):
        if path not in self.rows:
            raise NoNodeError()
        return self.rows[path]

    def exists(self, path):
        return self.rows[path][1] if path in self.rows else None

    def transaction(self):
        return FakeTransaction(self)


def args():
    return dict(definition_hash=DIGEST, source_plan_hash=DIGEST,
                source_inventory_hash=DIGEST, header_hash=DIGEST, scope_hash=DIGEST,
                stage_hash=DIGEST, seed_hash=DIGEST)


def test_cas_proof_survives_owner_change_and_matches_only_exact_fence() -> None:
    store = FakeKeeper()
    authority = MarketDayKeeperAuthority(store)
    claim = authority.acquire(BUILD, "worker-a")
    assert claim is not None and claim.epoch == 1
    proof = authority.attest(claim, **args())
    require_attested_inventory(proof, {"build_id": BUILD, **args()})
    with pytest.raises(RuntimeError, match="matching Keeper"):
        require_attested_inventory(proof, {"build_id": BUILD, **{
            **args(), "stage_hash": "c" * 64}})
    with pytest.raises(RuntimeError, match="matching Keeper"):
        require_attested_inventory(None, {"build_id": BUILD, **args()})
    holder = next(path for path in store.rows if path.endswith("/holder"))
    del store.rows[holder]
    newer = authority.acquire(BUILD, "worker-b")
    assert newer is not None and newer.epoch == 2
    assert authority.load(BUILD) == proof
    with pytest.raises(KeeperUnavailable, match="Stale"):
        authority.attest(claim, **args())


def test_owner_change_during_cas_cannot_attest() -> None:
    store = FakeKeeper()
    authority = MarketDayKeeperAuthority(store)
    claim = authority.acquire(BUILD, "worker-a")
    assert claim is not None
    holder = next(path for path in store.rows if path.endswith("/holder"))
    store.before_commit = lambda: store.rows.pop(holder)
    with pytest.raises(KeeperUnavailable, match="CAS proof conflicts"):
        authority.attest(claim, **args())
    assert authority.load(BUILD) is None


def test_proof_identity_rejects_delimiter_and_digest_conflict() -> None:
    authority = MarketDayKeeperAuthority(FakeKeeper())
    with pytest.raises(ValueError, match="owner"):
        authority.acquire(BUILD, "worker\nwrong")
    claim = authority.acquire(BUILD, "worker")
    assert claim is not None
    with pytest.raises(ValueError, match="digest"):
        authority.attest(claim, **{**args(), "seed_hash": "bad"})
    authority.attest(claim, **args())
    with pytest.raises(KeeperUnavailable, match="conflicts"):
        authority.attest(claim, **{**args(), "seed_hash": "c" * 64})


def test_pre_source_parity_keeper_proof_version_is_not_admitted() -> None:
    store = FakeKeeper()
    authority = MarketDayKeeperAuthority(store)
    claim = authority.acquire(BUILD, "worker")
    assert claim is not None
    authority.attest(claim, **args())
    path = next(path for path in store.rows if path.endswith("/attestation"))
    value, stat = store.rows[path]
    assert value.startswith(b"3\n")
    store.rows[path] = (b"2\n" + value[2:], stat)
    with pytest.raises(KeeperUnavailable, match="corrupt"):
        authority.load(BUILD)
