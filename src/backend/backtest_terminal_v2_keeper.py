"""Inactive Keeper-held client for terminal Backtest V2 publication.

This guards every client operation, including the nested portfolio snapshot
writer. It does not itself create a durable Keeper terminal attestation.
"""
from __future__ import annotations

from contextlib import AsyncExitStack
from hashlib import sha256
import re
from typing import Any

from src.trading_runtime.journal_contract import canonical_json


class _GuardedClient:
    def __init__(self, owner: "FixedTerminalKeeperAuthority", client: Any) -> None:
        self._owner = owner
        self._client = client

    def execute(self, sql: str) -> Any:
        self._owner.assert_current(self._owner.run_id, self._owner.account_ids)
        result = self._client.execute(sql)
        self._owner.assert_current(self._owner.run_id, self._owner.account_ids)
        return result


class FixedTerminalKeeperAuthority:
    """Hold all pinned account claims while the injected CH client is used.

    Each claim is acquired through KeeperOwnershipCoordinator's account-sync
    scope. The coordinator's current-claim check must remain authoritative.
    """

    def __init__(self, *, keeper: Any, client: Any, run_id: str,
                 account_ids: tuple[str, ...]) -> None:
        if (not run_id or not account_ids or len(set(account_ids)) != len(account_ids)
                or any(not isinstance(value, str) or not value for value in account_ids)):
            raise ValueError("Terminal Keeper authority needs pinned account identities")
        self.keeper = keeper
        self.run_id = run_id
        self.account_ids = tuple(sorted(account_ids))
        self.client = _GuardedClient(self, client)
        self._stack: AsyncExitStack | None = None
        self._leases: tuple[Any, ...] = ()

    async def __aenter__(self) -> "FixedTerminalKeeperAuthority":
        if self._stack is not None:
            raise RuntimeError("Terminal Keeper authority is already entered")
        stack = AsyncExitStack()
        try:
            leases = tuple([await stack.enter_async_context(
                self.keeper.claim_portfolio_snapshot(self.run_id, account_id))
                for account_id in self.account_ids])
            self._stack = stack
            self._leases = leases
            self.assert_current(self.run_id, self.account_ids)
            return self
        except BaseException:
            await stack.aclose()
            self._stack = None
            self._leases = ()
            raise

    async def __aexit__(self, exc_type, exc, tb) -> None:
        stack = self._stack
        self._stack = None
        self._leases = ()
        if stack is not None:
            await stack.aclose()

    def assert_current(self, run_id: str, account_ids: tuple[str, ...]) -> bool:
        if (self._stack is None or run_id != self.run_id
                or tuple(sorted(account_ids)) != self.account_ids
                or len(self._leases) != len(self.account_ids)):
            raise RuntimeError("Terminal Keeper account claim is not held")
        renewed = []
        for lease in self._leases:
            if not self.keeper.portfolio_snapshot_claim_is_current(lease):
                raise RuntimeError("Terminal Keeper account claim expired or changed")
            replacement = self.keeper.renew_portfolio_admission_lease(
                lease["resource_id"], owner_id=lease["owner_id"],
                epoch=lease["epoch"])
            if replacement is None or not self.keeper.portfolio_snapshot_claim_is_current(
                    replacement):
                raise RuntimeError("Terminal Keeper account claim could not renew")
            renewed.append(replacement)
        self._leases = tuple(renewed)
        return True

    def attest(self, seal: dict[str, Any], accounts: dict[str, dict[str, Any]]) -> bytes:
        self.assert_current(self.run_id, self.account_ids)
        if set(accounts) != set(self.account_ids) or seal.get("run_id") != self.run_id:
            raise RuntimeError("Terminal Keeper proof differs from pinned run/accounts")
        seal_hash = sha256(canonical_json(seal).encode("utf-8")).hexdigest()
        accounts_hash = sha256(canonical_json(sorted(
            (account_id, accounts[account_id]["state_hash"])
            for account_id in accounts)).encode("utf-8")).hexdigest()
        proof = self.keeper.attest_backtest_terminal_v2(
            tuple(zip(self.account_ids, self._leases, strict=True)),
            run_id=self.run_id, batch_id=str(seal["batch_id"]),
            seal_hash=seal_hash, accounts_hash=accounts_hash)
        self.assert_current(self.run_id, self.account_ids)
        return proof


def load_attested_terminal_v2_accounts(
    client: Any, keeper: Any, *, run_id: str,
) -> dict[str, dict[str, Any]]:
    """Cold recovery accepts only a V2 suffix with matching durable CAS proof."""
    from src.backend.backtest_terminal_v2_accounts import (
        load_terminal_v2_portfolio_accounts,
    )
    from src.backend.backtest_terminal_v2_publication import audit_terminal_v2_run
    from src.trading_runtime.arte_journal_writer import load_typed_run_context

    context = load_typed_run_context(client, run_id)
    account_ids = tuple(context["account_ids"])
    seal = audit_terminal_v2_run(client, run_id=run_id, account_ids=account_ids)
    accounts = load_terminal_v2_portfolio_accounts(client, run_id=run_id)
    if set(accounts) != set(account_ids):
        raise RuntimeError("Attested terminal account population differs")
    seal_hash = sha256(canonical_json(seal).encode("utf-8")).hexdigest()
    accounts_hash = sha256(canonical_json(sorted(
        (account_id, accounts[account_id]["state_hash"])
        for account_id in accounts)).encode("utf-8")).hexdigest()
    proof = keeper.load_backtest_terminal_v2_attestation(
        run_id, str(seal["batch_id"]))
    if proof is None:
        raise RuntimeError("Terminal V2 seal lacks committed Keeper proof")
    lines = proof.decode("utf-8").split("\n")
    if (len(lines) != 6 + 3 * len(account_ids)
            or lines[0] != "2" or lines[1:5] != [
            run_id, str(seal["batch_id"]), seal_hash, accounts_hash]
            or lines[5] != str(len(account_ids))
            or lines[6::3] != sorted(account_ids)
            or any(not owner or re.fullmatch(r"[1-9][0-9]*", epoch) is None
                   for owner, epoch in zip(lines[7::3], lines[8::3], strict=True))):
        raise RuntimeError("Terminal V2 Keeper proof differs from cold account seal")
    return accounts
