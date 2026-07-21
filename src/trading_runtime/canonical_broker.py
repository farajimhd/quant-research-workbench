from __future__ import annotations

from typing import Protocol

from src.trading_runtime.domain import (
    AccountValue,
    BrokerAccount,
    BrokerEventEnvelope,
    Execution,
    LedgerBalance,
    OrderIntent,
    OrderState,
    PositionState,
    SnapshotManifest,
)


class CanonicalBrokerAdapter(Protocol):
    """Mode-independent broker boundary used by live, paper, replay, and backtest runtimes."""

    async def initialize(self) -> None: ...

    async def canonical_accounts(self) -> list[BrokerAccount]: ...

    async def submit_intents(self, account_id: str, intents: list[OrderIntent]) -> list[BrokerEventEnvelope]: ...

    async def cancel(self, account_id: str, broker_order_id: str) -> list[BrokerEventEnvelope]: ...

    async def replace(self, account_id: str, broker_order_id: str, intent: OrderIntent) -> list[BrokerEventEnvelope]: ...

    async def canonical_orders(self, account_id: str = "") -> list[OrderState]: ...

    async def canonical_executions(self, account_id: str = "", days: int = 7) -> list[Execution]: ...

    async def canonical_position_snapshot(self, account_id: str) -> tuple[SnapshotManifest, list[PositionState]]: ...

    async def canonical_account_values(self, account_id: str) -> list[AccountValue]: ...

    async def canonical_ledger(self, account_id: str) -> list[LedgerBalance]: ...
