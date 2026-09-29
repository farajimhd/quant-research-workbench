"""Exclusive Keeper owner for one normalized Strategy 1 Backtest run.

This is a process-lifetime claim, separate from per-INSERT dispatch receipts.
Recovery must acquire a later epoch and cold-verify the committed prefix before
it may append. Neither the claim nor a V4 receipt is a disk checkpoint.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
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
