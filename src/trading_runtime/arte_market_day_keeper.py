"""Inactive Keeper CAS proof for a typed market-day build certificate.

Keeper stores only identity, owner epoch, and digests. ClickHouse remains the
data authority. No producer or Backtest reader invokes this staged adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re
from typing import Any

from src.trading_runtime.keeper_ownership import KeeperUnavailable, _committed


_ROOT = "/trading/ownership/v1/market_day_build"
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_BUILD = re.compile(r"[0-9a-f]{64}(?:-[0-9a-f]{12})?\Z")


def _identity(build_id: str, owner_id: str) -> None:
    if not isinstance(build_id, str) or not _BUILD.fullmatch(build_id):
        raise ValueError("Market-day build identity is invalid")
    if (not isinstance(owner_id, str) or not owner_id
            or any(char in owner_id for char in "\n\r\x00")):
        raise ValueError("Market-day owner identity is invalid")


def _base(build_id: str) -> str:
    _identity(build_id, "owner")
    return f"{_ROOT}/{sha256(build_id.encode()).hexdigest()}"


@dataclass(frozen=True)
class BuildClaim:
    build_id: str
    owner_id: str
    epoch: int


@dataclass(frozen=True)
class BuildAttestation:
    build_id: str
    definition_hash: str
    source_plan_hash: str
    source_inventory_hash: str
    header_hash: str
    scope_hash: str
    stage_hash: str
    seed_hash: str
    owner_id: str
    epoch: int

    def __post_init__(self) -> None:
        _identity(self.build_id, self.owner_id)
        if type(self.epoch) is not int or self.epoch < 1 or any(
            not isinstance(value, str) or not _HEX.fullmatch(value)
            for value in (self.definition_hash, self.source_plan_hash,
                          self.source_inventory_hash, self.header_hash, self.scope_hash,
                          self.stage_hash, self.seed_hash)
        ):
            raise ValueError("Market-day attestation has invalid epoch or digest")

    def wire(self) -> bytes:
        return ("2\n" + "\n".join(map(str, (
            self.build_id, self.definition_hash, self.source_plan_hash,
            self.source_inventory_hash, self.header_hash,
            self.scope_hash, self.stage_hash, self.seed_hash,
            self.owner_id, self.epoch)))).encode("utf-8")


class MarketDayKeeperAuthority:
    """Blocking control-plane adapter; transaction commit must be atomic."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self._connected()
        client.ensure_path(_ROOT)

    def _connected(self) -> None:
        if not self.client.connected or self.client.client_id is None:
            raise KeeperUnavailable("Market-day Keeper session is unavailable")

    def acquire(self, build_id: str, owner_id: str) -> BuildClaim | None:
        _identity(build_id, owner_id)
        self._connected()
        base = _base(build_id)
        self.client.ensure_path(base)
        counter, holder = base + "/epoch", base + "/holder"
        try:
            self.client.create(counter, b"0")
        except Exception as exc:
            if type(exc).__name__ != "NodeExistsError":
                raise KeeperUnavailable("Cannot initialize market-day epoch") from exc
        for _ in range(8):
            self._connected()
            if self.client.exists(holder) is not None:
                return None
            value, stat = self.client.get(counter)
            try:
                epoch = int(value) + 1
            except ValueError as exc:
                raise KeeperUnavailable("Market-day epoch is corrupt") from exc
            if epoch < 1:
                raise KeeperUnavailable("Market-day epoch is invalid")
            txn = self.client.transaction()
            txn.set_data(counter, str(epoch).encode("ascii"), version=stat.version)
            txn.create(holder, f"{owner_id}\n{epoch}".encode(), ephemeral=True)
            if not _committed(txn.commit()):
                continue
            claim = BuildClaim(build_id, owner_id, epoch)
            if not self.current(claim):
                raise KeeperUnavailable("Market-day claim was lost after acquire")
            return claim
        raise KeeperUnavailable("Market-day claim contended beyond retry budget")

    def current(self, claim: BuildClaim) -> bool:
        _identity(claim.build_id, claim.owner_id)
        self._connected()
        try:
            value, stat = self.client.get(_base(claim.build_id) + "/holder")
        except Exception as exc:
            if type(exc).__name__ == "NoNodeError":
                return False
            raise KeeperUnavailable("Cannot read market-day claim") from exc
        self._connected()
        return (value == f"{claim.owner_id}\n{claim.epoch}".encode()
                and stat.ephemeralOwner == self.client.client_id[0])

    def attest(self, claim: BuildClaim, *, definition_hash: str,
               source_plan_hash: str, source_inventory_hash: str,
               header_hash: str, scope_hash: str, stage_hash: str,
               seed_hash: str) -> BuildAttestation:
        proof = BuildAttestation(claim.build_id, definition_hash,
                                 source_plan_hash, source_inventory_hash, header_hash,
                                 scope_hash, stage_hash, seed_hash,
                                 claim.owner_id, claim.epoch)
        self._connected()
        base = _base(claim.build_id)
        holder, counter, receipt = (base + suffix for suffix in
                                    ("/holder", "/epoch", "/attestation"))
        if not self.current(claim):
            raise KeeperUnavailable("Stale market-day owner cannot attest")
        try:
            held, holder_stat = self.client.get(holder)
            _, counter_stat = self.client.get(counter)
        except Exception as exc:
            raise KeeperUnavailable("Cannot read market-day CAS state") from exc
        if held != f"{claim.owner_id}\n{claim.epoch}".encode() or not self.current(claim):
            raise KeeperUnavailable("Market-day owner changed before CAS")
        txn = self.client.transaction()
        txn.check(holder, version=holder_stat.version)
        txn.check(counter, version=counter_stat.version)
        txn.create(receipt, proof.wire(), ephemeral=False)
        if not _committed(txn.commit()):
            existing = self.load(claim.build_id)
            if existing != proof or not self.current(claim):
                raise KeeperUnavailable("Market-day CAS proof conflicts")
        if self.load(claim.build_id) != proof:
            raise KeeperUnavailable("Market-day CAS proof is not durable")
        return proof

    def load(self, build_id: str) -> BuildAttestation | None:
        """Historical proof remains readable after its owner has changed."""
        self._connected()
        try:
            value, _ = self.client.get(_base(build_id) + "/attestation")
        except Exception as exc:
            if type(exc).__name__ == "NoNodeError":
                return None
            raise KeeperUnavailable("Cannot read market-day attestation") from exc
        try:
            parts = value.decode("utf-8").split("\n")
            if len(parts) != 11 or parts[0] != "2":
                raise ValueError("unknown attestation wire version")
            proof = BuildAttestation(*parts[1:10], int(parts[10]))
            if proof.build_id != build_id or proof.wire() != value:
                raise ValueError("attestation differs from requested build")
            return proof
        except (UnicodeError, ValueError, TypeError) as exc:
            raise KeeperUnavailable("Market-day attestation is corrupt") from exc


def require_attested_inventory(proof: BuildAttestation | None,
                               fence: dict[str, Any]) -> None:
    """Cold admission rejects all CH fences lacking exact historical CAS proof."""
    if proof is None or any(fence.get(name) != getattr(proof, name) for name in (
        "build_id", "definition_hash", "source_plan_hash",
        "source_inventory_hash", "header_hash", "scope_hash",
        "stage_hash", "seed_hash"
    )):
        raise RuntimeError("Market-day fence lacks matching Keeper CAS attestation")
