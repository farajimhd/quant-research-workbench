"""Versioned persistent V3 terminal receipt under pinned account lease epochs."""
from __future__ import annotations

from hashlib import sha256
import re
from typing import Any
from uuid import UUID

from src.trading_runtime.keeper_ownership import (
    KeeperOwnershipCoordinator, KeeperUnavailable, _committed, _decode,
    _identity, _path, _sync_resource_id,
)
from src.trading_runtime.journal_contract import canonical_json
from src.backend.backtest_terminal_v3_dispatch import _decode as _decode_operation


def _receipt_path(run_id: str, batch_id: str) -> str:
    UUID(batch_id)
    return _path("backtest_terminal_v3", run_id, batch_id)


def operation_inventory_hash(
    operations: tuple[tuple[str, int, str], ...],
) -> str:
    if (not operations or len({path for path, _, _ in operations}) != len(operations)
            or tuple(sorted(operations)) != operations
            or any(type(version) is not int or version < 0
                   or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                   for _, version, digest in operations)):
        raise ValueError("V3 terminal operation inventory is invalid")
    return sha256(canonical_json([(path, digest) for path, _, digest in operations])
                  .encode()).hexdigest()


def load_terminal_v3_receipt(
    coordinator: KeeperOwnershipCoordinator, *, run_id: str, batch_id: str,
) -> bytes | None:
    coordinator._require_connected()
    try:
        value, _ = coordinator._client.get(_receipt_path(run_id, batch_id))
    except Exception as exc:
        if type(exc).__name__ == "NoNodeError":
            return None
        raise KeeperUnavailable("V3 terminal Keeper receipt cannot be read") from exc
    coordinator._require_connected()
    try:
        parts = value.decode("ascii").split("\n")
        count = int(parts[6])
        if (len(parts) != 7 + 3 * count or parts[0] != "4"
                or parts[1:3] != [run_id, batch_id] or count < 1
                or any(re.fullmatch(r"[0-9a-f]{64}", item) is None
                       for item in parts[3:6])
                or parts[7::3] != sorted(set(parts[7::3]))
                or any(not owner or not epoch.isdigit() or int(epoch) < 1
                       for owner, epoch in zip(parts[8::3], parts[9::3], strict=True))):
            raise ValueError("invalid receipt")
    except (UnicodeError, ValueError, IndexError) as exc:
        raise KeeperUnavailable("V3 terminal Keeper receipt is corrupt") from exc
    return value


def attest_terminal_v3(
    coordinator: KeeperOwnershipCoordinator,
    leases: tuple[tuple[str, dict[str, Any]], ...], *,
    run_id: str, seal: dict[str, Any], accounts: dict[str, dict[str, Any]],
    operations: tuple[tuple[str, int, str], ...],
) -> bytes:
    """Single CAS against all account holders/epochs and one persistent proof."""
    if (not isinstance(coordinator, KeeperOwnershipCoordinator)
            or seal.get("run_id") != run_id or not leases
            or len({account for account, _ in leases}) != len(leases)
            or set(accounts) != {account for account, _ in leases}):
        raise ValueError("V3 terminal proof lacks complete account authority")
    batch_id = str(UUID(str(seal["batch_id"])))
    seal_hash = sha256(canonical_json(seal).encode()).hexdigest()
    accounts_hash = sha256(canonical_json(sorted(
        (account, accounts[account]["state_hash"]) for account in accounts
    )).encode()).hexdigest()
    operation_hash = operation_inventory_hash(operations)
    ordered = sorted(leases)
    parts = ["4", _identity(run_id, "run"), batch_id, seal_hash,
             accounts_hash, operation_hash, str(len(ordered))]
    for account, lease in ordered:
        if (lease.get("resource_id") != _sync_resource_id(run_id, account)
                or type(lease.get("epoch")) is not int or lease["epoch"] < 1):
            raise ValueError("V3 terminal claim differs from account")
        parts.extend((_identity(account, "account"),
                      _identity(lease["owner_id"], "owner"), str(lease["epoch"])))
    payload = "\n".join(parts).encode("ascii")
    path = _receipt_path(run_id, batch_id)
    with coordinator._lock:
        coordinator._require_connected()
        coordinator._client.ensure_path(_path("backtest_terminal_v3"))
        transaction = coordinator._client.transaction()
        operation_root = _path("backtest_terminal_v3_operations", run_id, batch_id)
        saw_seal = False
        for operation_path, operation_version, digest in operations:
            if not operation_path.startswith(operation_root + "/"):
                raise KeeperUnavailable("V3 terminal operation has wrong parent")
            try:
                value, stat = coordinator._client.get(operation_path)
            except Exception as exc:
                raise KeeperUnavailable("V3 terminal operation is absent") from exc
            table, _, query_id, _, status = _decode_operation(
                value, run_id=run_id, batch_id=batch_id)
            if (stat.version != operation_version
                    or sha256(value).hexdigest() != digest
                    or status != "acknowledged"
                    or operation_path.rsplit("/", 1)[1] != query_id):
                raise KeeperUnavailable("V3 terminal operation changed before proof")
            saw_seal |= table == "trading_backtest_terminal_commit_v3"
            transaction.check(operation_path, version=operation_version)
        if not saw_seal:
            raise KeeperUnavailable("V3 terminal commit operation is absent")
        for account, lease in ordered:
            if not coordinator.portfolio_snapshot_claim_is_current(lease):
                raise KeeperUnavailable("V3 terminal claim expired before CAS")
            base = _path("portfolio", lease["resource_id"])
            holder, holder_stat = coordinator._client.get(f"{base}/holder")
            counter, counter_stat = coordinator._client.get(f"{base}/epoch")
            if (_decode(holder) != (lease["owner_id"], lease["epoch"], "portfolio")
                    or int(counter) != lease["epoch"]
                    or holder_stat.ephemeralOwner != coordinator._client.client_id[0]):
                raise KeeperUnavailable("V3 terminal owner epoch changed")
            transaction.check(f"{base}/holder", version=holder_stat.version)
            transaction.check(f"{base}/epoch", version=counter_stat.version)
        transaction.create(path, payload, ephemeral=False)
        if not _committed(transaction.commit()):
            if load_terminal_v3_receipt(
                    coordinator, run_id=run_id, batch_id=batch_id) != payload:
                raise KeeperUnavailable("V3 terminal CAS lost or conflicts")
        coordinator._require_connected()
    if (load_terminal_v3_receipt(coordinator, run_id=run_id, batch_id=batch_id)
            != payload or any(not coordinator.portfolio_snapshot_claim_is_current(lease)
                              for _, lease in ordered)):
        raise KeeperUnavailable("V3 terminal proof is uncertain after CAS")
    return payload
