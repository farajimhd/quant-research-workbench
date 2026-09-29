"""Exclusive Keeper owner for one normalized Strategy 1 Backtest run.

This is a process-lifetime claim, separate from per-INSERT dispatch receipts.
Recovery must acquire a later epoch and cold-verify the committed prefix before
it may append. Neither the claim nor a V4 receipt is a disk checkpoint.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re
from uuid import UUID

from src.backend.live_assignment_base_keeper import KeeperAssignmentHead
from src.trading_runtime.keeper_session import ManagedKeeperSession


def _run_id(value: str) -> str:
    try:
        normalized = str(UUID(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("Backtest V4 Keeper run ID is invalid") from exc
    if value != normalized:
        raise ValueError("Backtest V4 Keeper run ID must be canonical")
    return normalized


def _digest(value: str) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("Backtest V4 genesis needs a SHA-256 identity")
    return value


class _BacktestRunOwner(KeeperAssignmentHead):
    @staticmethod
    def path(run_id: str) -> str:
        return ("/trading/strategy-one-backtest-v4/v1/"
                + sha256(_run_id(run_id).encode()).hexdigest())


@dataclass(frozen=True, slots=True)
class BacktestV4KeeperLease:
    owner: _BacktestRunOwner
    run_id: str
    owner_id: str
    epoch: int

    @classmethod
    def acquire(cls, session: ManagedKeeperSession, *, run_id: str,
                owner_id: str) -> "BacktestV4KeeperLease":
        normalized = _run_id(run_id)
        owner = _BacktestRunOwner(session)
        epoch = owner.acquire(normalized, owner_id=owner_id)
        if epoch is None:
            raise RuntimeError("Backtest V4 run Keeper claim is held")
        return cls(owner, normalized, owner_id, epoch)

    def assert_current(self) -> None:
        if not self.owner.is_current(self.run_id, owner_id=self.owner_id,
                                     epoch=self.epoch):
            raise RuntimeError("Backtest V4 Keeper lease lost")

    def release(self) -> bool:
        return self.owner.release(self.run_id, owner_id=self.owner_id,
                                  epoch=self.epoch)

    def _genesis_path(self) -> str:
        return self.owner.path(self.run_id) + "/genesis"

    def _genesis_wire(self, *, configuration_hash: str,
                      market_plan_token: str, code_hash: str) -> bytes:
        return ("1\n" + "\n".join((self.run_id,
                _digest(configuration_hash), _digest(market_plan_token),
                _digest(code_hash), "1"))).encode()

    def attest_genesis(self, *, configuration_hash: str,
                       market_plan_token: str, code_hash: str) -> None:
        """Seal only a newly launched run that held this owner from creation."""
        self.assert_current()
        if self.epoch != 1:
            raise RuntimeError("Backtest V4 genesis requires its first owner epoch")
        expected = self._genesis_wire(
            configuration_hash=configuration_hash,
            market_plan_token=market_plan_token, code_hash=code_hash)
        try:
            self.owner._client.create(self._genesis_path(), expected)
        except Exception as exc:
            raise RuntimeError("Backtest V4 genesis already exists or failed") from exc
        self.assert_genesis(configuration_hash=configuration_hash,
                            market_plan_token=market_plan_token,
                            code_hash=code_hash)

    def assert_genesis(self, *, configuration_hash: str,
                       market_plan_token: str, code_hash: str) -> None:
        self.assert_current()
        expected = self._genesis_wire(
            configuration_hash=configuration_hash,
            market_plan_token=market_plan_token, code_hash=code_hash)
        try:
            actual, _ = self.owner._client.get(self._genesis_path())
        except Exception as exc:
            raise RuntimeError("Backtest V4 genesis is absent") from exc
        if actual != expected:
            raise RuntimeError("Backtest V4 genesis differs from pinned run")
