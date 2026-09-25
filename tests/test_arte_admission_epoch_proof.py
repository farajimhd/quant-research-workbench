from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.trading_runtime import arte_admission_epoch_proof as proof_module
from src.trading_runtime.arte_admission_fence import _fence
from src.trading_runtime.arte_journal_writer import _wire_row
from src.trading_runtime.keeper_ownership import KeeperUnavailable, _encode, _path
from test_arte_admission_fence import batch, captured


RUN = "live:DU1:2026-08-18"
ACCOUNT = "DU1"
RESOURCE = "portfolio-account:DU1"
HASHES = ("a" * 64, "b" * 64)
LEASE = {"resource_id": RESOURCE, "owner_id": "worker-a", "epoch": 3}


@dataclass
class Stat:
    version: int = 0
    ephemeralOwner: int = 91


class NoNodeError(Exception):
    pass


class NodeExistsError(Exception):
    pass


class Transaction:
    def __init__(self, keeper):
        self.keeper = keeper
        self.checks = []
        self.operations = []

    def check(self, path, version):
        self.checks.append((path, version))

    def create(self, path, value, ephemeral=False):
        assert not ephemeral
        self.operations.append(("create", path, value, None))

    def set_data(self, path, value, version):
        self.operations.append(("set", path, value, version))

    def commit(self):
        if any(self.keeper.rows[path][1].version != version
               for path, version in self.checks):
            return [RuntimeError("conflict")]
        for operation, path, _, version in self.operations:
            if operation == "create" and path in self.keeper.rows:
                return [NodeExistsError()]
            if operation == "set" and self.keeper.rows[path][1].version != version:
                return [NodeExistsError()]
        for operation, path, value, _ in self.operations:
            if operation == "create":
                self.keeper.rows[path] = (value, Stat())
            else:
                self.keeper.rows[path] = (value, Stat(self.keeper.rows[path][1].version + 1))
        return [True]


class Keeper:
    client_id = (91, b"secret")

    def __init__(self):
        base = _path("portfolio", RESOURCE)
        self.rows = {f"{base}/holder": (_encode("worker-a", 3, "portfolio"), Stat()),
                     f"{base}/epoch": (b"3", Stat())}

    def get(self, path):
        if path not in self.rows:
            raise NoNodeError()
        return self.rows[path]

    def ensure_path(self, path):
        assert path.endswith(("/admission_epoch_proof", "/admission_epoch_head"))

    def transaction(self):
        return Transaction(self)


class Coordinator:
    def __init__(self):
        self._client = Keeper()
        self.current = True

    def _require_connected(self):
        pass

    def portfolio_admission_lease_is_current(self, resource_id, *, owner_id, epoch):
        return self.current and (resource_id, owner_id, epoch) == (RESOURCE, "worker-a", 3)


def _install(monkeypatch):
    monkeypatch.setattr(proof_module, "load_fenced_admission", lambda *_args, **_kwargs:
                        {"snapshot_hash": "c" * 64})
    monkeypatch.setattr(proof_module, "_fence_hashes", lambda *_args: HASHES)


def test_epoch_cas_attests_exact_fence_and_historical_read_survives_owner_change(monkeypatch):
    _install(monkeypatch)
    coordinator = Coordinator()
    authority = proof_module.KeeperAdmissionEpochAuthority(coordinator)
    proof = authority.attest(object(), LEASE, run_id=RUN,
                             account_id=ACCOUNT, state_revision=1)
    assert (proof.prepared_hash, proof.committed_hash, proof.epoch) == (*HASHES, 3)
    assert authority.attest(object(), LEASE, run_id=RUN,
                            account_id=ACCOUNT, state_revision=1) == proof
    coordinator.current = False
    assert authority.load(RUN, ACCOUNT, 1) == proof
    assert proof_module.load_attested_admission(object(), authority, run_id=RUN,
                                                account_id=ACCOUNT, state_revision=1)


def test_stale_owner_and_duplicate_conflicting_proof_fail_closed(monkeypatch):
    _install(monkeypatch)
    coordinator = Coordinator()
    authority = proof_module.KeeperAdmissionEpochAuthority(coordinator)
    coordinator.current = False
    with pytest.raises(KeeperUnavailable, match="Stale admission owner"):
        authority.attest(object(), LEASE, run_id=RUN,
                         account_id=ACCOUNT, state_revision=1)
    coordinator.current = True
    authority.attest(object(), LEASE, run_id=RUN,
                     account_id=ACCOUNT, state_revision=1)
    monkeypatch.setattr(proof_module, "_fence_hashes", lambda *_args:
                        ("d" * 64, "e" * 64))
    with pytest.raises(KeeperUnavailable, match="CAS proof conflicts"):
        authority.attest(object(), LEASE, run_id=RUN,
                         account_id=ACCOUNT, state_revision=1)
    with pytest.raises(RuntimeError, match="matching Keeper epoch proof"):
        proof_module.load_attested_admission(object(), authority, run_id=RUN,
                                             account_id=ACCOUNT, state_revision=1)


def test_late_duplicate_or_changed_fence_after_proof_is_rejected(monkeypatch):
    _install(monkeypatch)
    authority = proof_module.KeeperAdmissionEpochAuthority(Coordinator())
    authority.attest(object(), LEASE, run_id=RUN,
                     account_id=ACCOUNT, state_revision=1)
    calls = 0
    def late(*_args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("Admission proof requires exactly two stored fence phases")
        return HASHES
    monkeypatch.setattr(proof_module, "_fence_hashes", late)
    with pytest.raises(RuntimeError, match="exactly two stored fence phases"):
        proof_module.load_attested_admission(object(), authority, run_id=RUN,
                                             account_id=ACCOUNT, state_revision=1)


def test_exact_typed_fence_readback_rejects_duplicate_and_tamper(monkeypatch):
    rows = [_wire_row("trading_admission_fence_v1", _fence(batch(), captured(), "prepared")),
            _wire_row("trading_admission_fence_v1", _fence(batch(), captured(),
                     "committed", "c" * 64))]
    monkeypatch.setattr(proof_module, "_rows", lambda _client, _sql: rows)
    assert len(proof_module._fence_hashes(object(), RUN, ACCOUNT, 1)) == 2
    rows.append(dict(rows[1]))
    with pytest.raises(RuntimeError, match="exactly two stored fence phases"):
        proof_module._fence_hashes(object(), RUN, ACCOUNT, 1)
    rows.pop()
    rows[1] = dict(rows[1], content_hash="0" * 64)
    with pytest.raises(RuntimeError, match="differs from hash"):
        proof_module._fence_hashes(object(), RUN, ACCOUNT, 1)


def test_strict_startup_requires_external_durable_writer_barrier(monkeypatch):
    reads = []
    monkeypatch.setattr(proof_module, "_rows", lambda *_args: reads.append(True) or [])
    with pytest.raises(RuntimeError, match="writer-drain barrier"):
        proof_module.audit_attested_admission_revisions(
            object(), object(), RUN, quiescence=None)
    assert not reads


def test_strict_startup_pages_and_verifies_every_historical_revision(monkeypatch):
    keys = [("DU1", 1), ("DU1", 2), ("DU2", 1)]
    queries = []
    def rows(_client, sql):
        queries.append(sql)
        import re
        after = re.search(r"AND \(account_id,state_revision\) > \('([^']+)',(\d+)\)", sql)
        selected = [key for key in keys if after is None or
                    key > (after.group(1), int(after.group(2)))]
        return [{"account_id": account, "state_revision": revision}
                for account, revision in selected[:1]]
    monkeypatch.setattr(proof_module, "_rows", rows)
    checked = []
    monkeypatch.setattr(proof_module, "load_attested_admission", lambda *_args, **identity:
                        checked.append((identity["account_id"], identity["state_revision"])))
    class Barrier:
        calls = 0
        def assert_fenced(self, run_id):
            assert run_id == RUN
            self.calls += 1
    barrier = Barrier()
    proofs = {(account, revision): proof_module.AdmissionEpochProof(
        RUN, account, revision, f"portfolio-account:{account}", "worker-a", 3,
        *HASHES) for account, revision in keys}
    head = proof_module.AdmissionProofHead(RUN, 0, "0" * 64)
    for item in proofs.values():
        head = proof_module._advance_head(head, item)
    class Authority:
        def load_head(self, _run_id):
            return head, 2
        def load(self, _run_id, account_id, revision):
            return proofs[(account_id, revision)]
    assert proof_module.audit_attested_admission_revisions(
        object(), Authority(), RUN, quiescence=barrier, page_size=1) == 3
    assert checked == keys
    assert len(queries) == 4 and barrier.calls > len(queries)


def test_strict_startup_lost_barrier_blocks_result_mid_scan(monkeypatch):
    monkeypatch.setattr(proof_module, "_rows", lambda *_args:
                        [{"account_id": ACCOUNT, "state_revision": 1}])
    checked = []
    monkeypatch.setattr(proof_module, "load_attested_admission", lambda *_args, **_kwargs:
                        checked.append(True))
    class Barrier:
        calls = 0
        def assert_fenced(self, _run_id):
            self.calls += 1
            if self.calls == 3:
                raise RuntimeError("writer barrier lost")
    class Authority:
        def load_head(self, _run_id):
            return None
    with pytest.raises(RuntimeError, match="writer barrier lost"):
        proof_module.audit_attested_admission_revisions(
            object(), Authority(), RUN, quiescence=Barrier(), page_size=1)
    assert not checked


def test_orphan_keeper_proof_without_clickhouse_revision_blocks_cold_audit(monkeypatch):
    _install(monkeypatch)
    authority = proof_module.KeeperAdmissionEpochAuthority(Coordinator())
    authority.attest(object(), LEASE, run_id=RUN,
                     account_id=ACCOUNT, state_revision=1)
    monkeypatch.setattr(proof_module, "_rows", lambda *_args: [])
    class Barrier:
        def assert_fenced(self, _run_id):
            pass
    with pytest.raises(RuntimeError, match="proof head differs"):
        proof_module.audit_attested_admission_revisions(
            object(), authority, RUN, quiescence=Barrier())
