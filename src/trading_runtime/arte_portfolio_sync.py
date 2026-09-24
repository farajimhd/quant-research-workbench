"""Verified typed broker-sync snapshot receipt, separate from live cutover."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import re
from typing import Any, Callable

from src.trading_runtime.arte_portfolio_snapshot import (
    CapturedPortfolioSnapshot, load_portfolio_snapshot,
)


@dataclass(frozen=True, slots=True)
class PortfolioSyncReceipt:
    run_id: str
    account_id: str
    state_revision: int
    snapshot_hash: str


class TypedPortfolioSyncAuthority:
    """Hold an account claim until a snapshot is committed and cold-read."""

    def __init__(self, *, client: Any, writer: Any, keeper: Any,
                 next_revision: Callable[..., int]) -> None:
        self._client = client
        self._writer = writer
        self._keeper = keeper
        self._revision = next_revision

    def claim(self, run_id: str, account_id: str):
        return self._keeper.claim_portfolio_snapshot(run_id, account_id)

    def claim_is_current(self, lease: Any) -> bool:
        return bool(self._keeper.portfolio_snapshot_claim_is_current(lease))

    def next_revision(self, run_id: str, account_id: str, lease: Any) -> int:
        return self._revision(run_id, account_id, lease)

    async def publish(self, captured: CapturedPortfolioSnapshot,
                      lease: Any) -> PortfolioSyncReceipt:
        if not isinstance(captured, CapturedPortfolioSnapshot) or not self.claim_is_current(lease):
            raise RuntimeError("Typed portfolio sync lacks a current claim or capture")
        digest = await asyncio.wrap_future(
            self._writer.submit_captured_portfolio_snapshot(captured))
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise RuntimeError("Typed portfolio sync writer returned no snapshot hash")
        loaded = load_portfolio_snapshot(
            self._client, run_id=captured.run_id, account_id=captured.account_id,
            state_revision=captured.state_revision)
        if (loaded is None or loaded["state_hash"] != digest
                or loaded["state_revision"] != captured.state_revision
                or not self.claim_is_current(lease)):
            raise RuntimeError("Typed portfolio sync receipt differs from committed state")
        return PortfolioSyncReceipt(captured.run_id, captured.account_id,
                                    captured.state_revision, digest)
