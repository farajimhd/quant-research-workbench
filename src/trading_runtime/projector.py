from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from src.trading_runtime.domain import (
    AccountValue,
    BrokerAccount,
    BrokerEventEnvelope,
    BrokerProvider,
    Execution,
    LedgerBalance,
    OrderState,
    PositionState,
    RoundTripTrade,
    TradingMode,
    TradingStateSnapshot,
    utc_now,
)
from src.trading_runtime.ibkr_normalizer import merge_ledger_delta


class TradingStateProjector:
    """Deterministic current-state projection over immutable canonical events and snapshots."""

    def __init__(self, mode: TradingMode, provider: BrokerProvider) -> None:
        self.mode = mode
        self.provider = provider
        self.accounts: dict[str, BrokerAccount] = {}
        self.account_values: dict[tuple[str, str, str, str], AccountValue] = {}
        self.ledger: dict[tuple[str, str], LedgerBalance] = {}
        self.orders: dict[tuple[str, str], OrderState] = {}
        self.executions: dict[tuple[str, str], Execution] = {}
        self.positions: dict[tuple[str, int, str], PositionState] = {}
        self.closed_trades: dict[str, RoundTripTrade] = {}
        self.activity: list[BrokerEventEnvelope] = []
        self.last_complete_position_snapshot: dict[str, str] = {}
        self.as_of: datetime | None = None
        self.complete = False
        self.stale = False
        self.stale_reason = "Initial broker snapshot has not completed."

    def set_accounts(self, rows: list[BrokerAccount]) -> None:
        self.accounts = {row.account_id: row for row in rows}
        self._advance(*(row.valid_at for row in rows))

    def set_orders(self, rows: list[OrderState]) -> None:
        for row in rows:
            self.orders[(row.account_id, row.broker_order_id or row.client_order_id)] = row
        self._advance(*(row.source_event_time for row in rows))

    def set_executions(self, rows: list[Execution]) -> None:
        for row in rows:
            self.executions[(row.account_id, row.execution_id)] = row
        self._advance(*(row.source_event_time for row in rows))

    def set_account_values(self, rows: list[AccountValue]) -> None:
        for row in rows:
            self.account_values[(row.account_id, row.key, row.segment, row.currency)] = row
        self._advance(*(row.source_event_time for row in rows))

    def merge_ledger(self, rows: list[LedgerBalance]) -> None:
        merged = merge_ledger_delta(self.ledger.values(), rows)
        self.ledger = {(row.account_id, row.currency): row for row in merged}
        self._advance(*(row.source_event_time for row in rows))

    def apply_position_snapshot(self, account_id: str, snapshot_id: str, complete: bool, rows: list[PositionState]) -> None:
        if not complete:
            self.stale = True
            self.stale_reason = f"Position snapshot {snapshot_id} is incomplete; prior positions were retained."
            return
        incoming = {(row.account_id, row.instrument.conid, row.model): row for row in rows}
        for key in [key for key in self.positions if key[0] == account_id and key not in incoming]:
            del self.positions[key]
        self.positions.update(incoming)
        self.last_complete_position_snapshot[account_id] = snapshot_id
        self.complete = all(account.account_id in self.last_complete_position_snapshot for account in self.accounts.values() if account.can_view)
        self.stale = False
        self.stale_reason = ""
        self._advance(*(row.source_event_time for row in rows))

    def apply_commission(self, account_id: str, execution_id: str, commission: Decimal, currency: str) -> None:
        key = (account_id, execution_id)
        execution = self.executions.get(key)
        if execution is not None:
            self.executions[key] = replace(
                execution,
                commission=commission,
                commission_currency=currency,
                commission_status="final",
            )

    def record_activity(self, event: BrokerEventEnvelope, max_events: int = 10_000) -> None:
        self.activity.append(event)
        if len(self.activity) > max_events:
            del self.activity[: len(self.activity) - max_events]
        self._advance(event.source_event_time)

    def snapshot(self, account_ids: list[str] | None = None) -> TradingStateSnapshot:
        selected = set(account_ids or self.accounts)
        return TradingStateSnapshot(
            schema_version=2,
            mode=self.mode,
            provider=self.provider,
            as_of=self.as_of or utc_now(),
            account_ids=tuple(sorted(selected)),
            complete=self.complete,
            stale=self.stale,
            stale_reason=self.stale_reason,
            accounts=tuple(row for key, row in sorted(self.accounts.items()) if key in selected),
            account_values=tuple(row for row in self.account_values.values() if row.account_id in selected),
            ledger=tuple(row for row in self.ledger.values() if row.account_id in selected),
            positions=tuple(row for row in self.positions.values() if row.account_id in selected),
            orders=tuple(row for row in self.orders.values() if row.account_id in selected),
            executions=tuple(row for row in self.executions.values() if row.account_id in selected),
            closed_trades=tuple(row for row in self.closed_trades.values() if row.account_id in selected),
            activity=tuple(row for row in self.activity if row.account_id in selected),
        )

    def _advance(self, *timestamps: datetime) -> None:
        valid = [timestamp.astimezone(UTC) for timestamp in timestamps]
        if valid:
            self.as_of = max(([self.as_of] if self.as_of is not None else []) + valid)
