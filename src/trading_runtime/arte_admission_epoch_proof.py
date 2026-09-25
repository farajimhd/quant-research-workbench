"""Inactive, read-only cold check and Keeper CAS proof for one admission.

Keeper stores identity and digests only. ClickHouse remains the typed data
authority. This module is not wired into startup or order admission.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re
from typing import Any, Protocol

from src.trading_runtime.arte_admission_fence import load_fenced_admission
from src.trading_runtime.arte_journal_writer import _canonical_typed_content, _literal, _rows
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.keeper_ownership import (
    KeeperUnavailable, _ROOT, _committed, _decode, _identity, _path,
)


_TABLE = "trading_admission_fence_v1"
_HEX = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class AdmissionEpochProof:
    run_id: str
    account_id: str
    state_revision: int
    resource_id: str
    owner_id: str
    epoch: int
    prepared_hash: str
    committed_hash: str

    def __post_init__(self) -> None:
        for value, label in ((self.run_id, "run"), (self.account_id, "account"),
                             (self.resource_id, "resource"), (self.owner_id, "owner")):
            _identity(value, label)
        if (type(self.state_revision) is not int or self.state_revision < 1
                or type(self.epoch) is not int or self.epoch < 1
                or not _HEX.fullmatch(self.prepared_hash)
                or not _HEX.fullmatch(self.committed_hash)):
            raise ValueError("Admission epoch proof has invalid revision, epoch or digest")

    def wire(self) -> bytes:
        return ("1\n" + "\n".join(map(str, (
            self.run_id, self.account_id, self.state_revision, self.resource_id,
            self.owner_id, self.epoch, self.prepared_hash, self.committed_hash,
        )))).encode("utf-8")


def _path_for(run_id: str, account_id: str, revision: int) -> str:
    if type(revision) is not int or revision < 1:
        raise ValueError("Admission proof needs a positive revision")
    return _path("admission_epoch_proof", run_id, account_id, str(revision))


def _fence_hashes(client: Any, run_id: str, account_id: str,
                  revision: int) -> tuple[str, str]:
    rows = _rows(client, f"SELECT * FROM arte.{_TABLE} "
        f"WHERE run_id={_literal(run_id)} AND account_id={_literal(account_id)} "
        f"AND state_revision={revision} FORMAT JSONEachRow")
    if len(rows) != 2:
        raise RuntimeError("Admission proof requires exactly two stored fence phases")
    hashes: dict[str, str] = {}
    for row in rows:
        content = {key: value for key, value in row.items() if key != "content_hash"}
        canonical = _canonical_typed_content(_TABLE, content, stored_utc=True)
        digest = sha256(canonical_json(canonical).encode()).hexdigest()
        phase = canonical["phase"]
        if (phase not in {"prepared", "committed"} or phase in hashes
                or row["content_hash"] != digest):
            raise RuntimeError("Admission proof fence is duplicate or differs from hash")
        hashes[phase] = digest
    if set(hashes) != {"prepared", "committed"}:
        raise RuntimeError("Admission proof lacks both fence phases")
    return hashes["prepared"], hashes["committed"]


def load_attested_admission(client: Any, authority: Any, *, run_id: str,
                            account_id: str, state_revision: int) -> dict[str, Any]:
    """Reject unattested, late-duplicate or changed CH facts on cold recovery."""
    committed = load_fenced_admission(client, run_id=run_id,
        account_id=account_id, state_revision=state_revision)
    if committed is None:
        raise RuntimeError("Admission epoch proof lacks committed ClickHouse state")
    hashes = _fence_hashes(client, run_id, account_id, state_revision)
    proof = authority.load(run_id, account_id, state_revision)
    if (not isinstance(proof, AdmissionEpochProof)
            or (proof.run_id, proof.account_id, proof.state_revision) !=
               (run_id, account_id, state_revision)
            or (proof.prepared_hash, proof.committed_hash) != hashes
            or proof.resource_id != f"portfolio-account:{account_id}"):
        raise RuntimeError("Admission fence lacks matching Keeper epoch proof")
    if _fence_hashes(client, run_id, account_id, state_revision) != hashes:
        raise RuntimeError("Admission fence changed during epoch proof audit")
    return committed


class KeeperAdmissionEpochAuthority:
    """Blocking CAS adapter over the existing portfolio holder/epoch namespace."""

    def __init__(self, coordinator: Any) -> None:
        self.coordinator = coordinator
        self.client = coordinator._client

    def load(self, run_id: str, account_id: str,
             state_revision: int) -> AdmissionEpochProof | None:
        self.coordinator._require_connected()
        try:
            value, _ = self.client.get(_path_for(run_id, account_id, state_revision))
        except Exception as exc:
            if type(exc).__name__ == "NoNodeError":
                return None
            raise KeeperUnavailable("Cannot read admission epoch proof") from exc
        self.coordinator._require_connected()
        try:
            parts = value.decode("utf-8").split("\n")
            if len(parts) != 9 or parts[0] != "1":
                raise ValueError("unknown proof wire version")
            proof = AdmissionEpochProof(parts[1], parts[2], int(parts[3]),
                parts[4], parts[5], int(parts[6]), parts[7], parts[8])
            if (proof.wire() != value or
                    (proof.run_id, proof.account_id, proof.state_revision) !=
                    (run_id, account_id, state_revision)):
                raise ValueError("proof identity differs")
            return proof
        except (UnicodeError, ValueError, TypeError) as exc:
            raise KeeperUnavailable("Admission epoch proof is corrupt") from exc

    def attest(self, client: Any, lease: dict[str, Any], *, run_id: str,
               account_id: str, state_revision: int) -> AdmissionEpochProof:
        resource_id = f"portfolio-account:{account_id}"
        if (lease.get("resource_id") != resource_id
                or type(lease.get("epoch")) is not int):
            raise ValueError("Admission proof lease differs from account")
        committed = load_fenced_admission(client, run_id=run_id,
            account_id=account_id, state_revision=state_revision)
        if committed is None:
            raise RuntimeError("Admission epoch proof needs committed ClickHouse state")
        hashes = _fence_hashes(client, run_id, account_id, state_revision)
        proof = AdmissionEpochProof(run_id, account_id, state_revision,
            resource_id, lease["owner_id"], lease["epoch"], *hashes)
        base = _path("portfolio", resource_id)
        self.coordinator._require_connected()
        self.client.ensure_path(f"{_ROOT}/admission_epoch_proof")
        if not self.coordinator.portfolio_admission_lease_is_current(
                resource_id, owner_id=proof.owner_id, epoch=proof.epoch):
            raise KeeperUnavailable("Stale admission owner cannot attest")
        holder, holder_stat = self.client.get(f"{base}/holder")
        counter, counter_stat = self.client.get(f"{base}/epoch")
        if (_decode(holder) != (proof.owner_id, proof.epoch, "portfolio")
                or int(counter) != proof.epoch
                or holder_stat.ephemeralOwner != self.client.client_id[0]):
            raise KeeperUnavailable("Admission owner epoch changed before CAS")
        txn = self.client.transaction()
        txn.check(f"{base}/holder", version=holder_stat.version)
        txn.check(f"{base}/epoch", version=counter_stat.version)
        txn.create(_path_for(run_id, account_id, state_revision), proof.wire(),
                   ephemeral=False)
        if not _committed(txn.commit()):
            if (self.load(run_id, account_id, state_revision) != proof
                    or not self.coordinator.portfolio_admission_lease_is_current(
                        resource_id, owner_id=proof.owner_id, epoch=proof.epoch)):
                raise KeeperUnavailable("Admission epoch CAS proof conflicts")
        if self.load(run_id, account_id, state_revision) != proof:
            raise KeeperUnavailable("Admission epoch proof is not durable")
        if not self.coordinator.portfolio_admission_lease_is_current(
                resource_id, owner_id=proof.owner_id, epoch=proof.epoch):
            raise KeeperUnavailable("Admission owner changed after CAS")
        if _fence_hashes(client, run_id, account_id, state_revision) != hashes:
            raise RuntimeError("Admission fence changed after epoch proof CAS")
        return proof


class AdmissionWriterQuiescence(Protocol):
    """External durable barrier, not a Keeper lease-expiry observation.

    A conforming implementation must prove that all previous owners' CH
    inserts have drained or are transactionally fenced, and that no new owner
    can enqueue one until the audit exits. No production implementation exists.
    """

    def assert_fenced(self, run_id: str) -> None: ...


def audit_attested_admission_revisions(
        client: Any, authority: Any, run_id: str, *,
        quiescence: AdmissionWriterQuiescence | None,
        page_size: int = 256) -> int:
    """Inactive strict startup scan of every CH revision and historical proof.

    This function cannot establish a stable read by itself. A missing or lost
    external writer-drain barrier is an error, never a successful audit.
    """
    _identity(run_id, "run")
    if type(page_size) is not int or not 1 <= page_size <= 1000:
        raise ValueError("Admission proof audit page size is invalid")
    if quiescence is None or not callable(getattr(quiescence, "assert_fenced", None)):
        raise RuntimeError("Admission proof audit requires a writer-drain barrier")
    quiescence.assert_fenced(run_id)
    cursor: tuple[str, int] | None = None
    checked = 0
    while True:
        quiescence.assert_fenced(run_id)
        after = ("" if cursor is None else
                 "AND (account_id,state_revision) > "
                 f"({_literal(cursor[0])},{cursor[1]}) ")
        page = _rows(client,
            "SELECT account_id,state_revision "
            "FROM arte.trading_admission_fence_v1 "
            f"WHERE run_id={_literal(run_id)} {after}"
            "GROUP BY account_id,state_revision "
            f"ORDER BY account_id,state_revision LIMIT {page_size} FORMAT JSONEachRow")
        if len(page) > page_size:
            raise RuntimeError("Admission proof audit page exceeded bound")
        for row in page:
            key = (row.get("account_id"), row.get("state_revision"))
            if (not isinstance(key[0], str) or not key[0]
                    or type(key[1]) is not int or key[1] < 1
                    or (cursor is not None and key <= cursor)):
                raise RuntimeError("Admission proof audit keys are invalid or nonmonotonic")
            quiescence.assert_fenced(run_id)
            load_attested_admission(client, authority, run_id=run_id,
                                    account_id=key[0], state_revision=key[1])
            cursor = key
            checked += 1
        quiescence.assert_fenced(run_id)
        if len(page) < page_size:
            break
    quiescence.assert_fenced(run_id)
    return checked
