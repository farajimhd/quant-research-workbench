from __future__ import annotations

from typing import Any, Protocol

from src.trading_runtime.canonical_broker import CanonicalBrokerAdapter
from src.trading_runtime.domain import (
    BrokerEventEnvelope,
    BrokerEventType,
    BrokerProvider,
    TradingMode,
    json_safe,
)
from src.trading_runtime.ibkr_normalizer import normalize_account_values, normalize_execution, normalize_ledger, normalize_order
from src.trading_runtime.projector import TradingStateProjector


class CanonicalEventSink(Protocol):
    def persist_canonical_events(self, events: list[BrokerEventEnvelope]) -> int: ...

    def persist_canonical_snapshot(self, snapshot: Any) -> None: ...


class CanonicalBrokerSession:
    """One startup, stream, projection, persistence, and reconciliation lifecycle for every broker mode."""

    def __init__(
        self,
        adapter: CanonicalBrokerAdapter,
        *,
        mode: TradingMode,
        provider: BrokerProvider,
        sink: CanonicalEventSink | None = None,
    ) -> None:
        self.adapter = adapter
        self.mode = mode
        self.provider = provider
        self.sink = sink
        self.projector = TradingStateProjector(mode, provider)

    async def bootstrap(self) -> None:
        await self.adapter.initialize()
        accounts = await self.adapter.canonical_accounts()
        self.projector.set_accounts(accounts)
        viewable = [row.account_id for row in accounts if row.can_view]
        for account_id in viewable:
            self.projector.replace_account_values(account_id, await self.adapter.canonical_account_values(account_id))
            self.projector.replace_ledger(account_id, await self.adapter.canonical_ledger(account_id))
            manifest, positions = await self.adapter.canonical_position_snapshot(account_id)
            self.projector.apply_position_snapshot(account_id, manifest.snapshot_id, manifest.complete, positions)
        orders = []
        executions = []
        for account_id in [row.account_id for row in accounts if row.can_trade]:
            orders.extend(await self.adapter.canonical_orders(account_id))
            executions.extend(await self.adapter.canonical_executions(account_id, days=7))
        self.projector.set_orders(orders)
        self.projector.set_executions(executions)
        self.projector.complete = all(account_id in self.projector.last_complete_position_snapshot for account_id in viewable)
        self.projector.stale = not self.projector.complete
        self.projector.stale_reason = "" if self.projector.complete else "One or more initial position snapshots did not complete."
        if self.sink:
            self.sink.persist_canonical_snapshot(self.projector.snapshot())

    def apply_websocket_message(self, message: dict[str, Any]) -> list[BrokerEventEnvelope]:
        raw_topic = str(message.get("topic") or "")
        topic = raw_topic.lower()
        result = message.get("result")
        rows = result if isinstance(result, list) else [result] if isinstance(result, dict) else []
        events: list[BrokerEventEnvelope] = []
        if topic.startswith("sor"):
            for raw in rows:
                order = normalize_order(raw)
                self.projector.set_orders([order])
                events.append(self._event(BrokerEventType.ORDER_STATUS_CHANGED, order.account_id, raw, order.source_event_time, broker_order_id=order.broker_order_id, client_order_id=order.client_order_id))
        elif topic.startswith("str"):
            for raw in rows:
                execution = normalize_execution(raw)
                self.projector.set_executions([execution])
                events.append(self._event(BrokerEventType.EXECUTION_REPORTED, execution.account_id, raw, execution.source_event_time, broker_order_id=execution.broker_order_id, client_order_id=execution.client_order_id, execution_id=execution.execution_id))
        elif topic.startswith("ssd"):
            account_id = _topic_account(raw_topic)
            payload = {str(row.get("key")): row for row in rows if isinstance(row, dict) and row.get("key")}
            values = normalize_account_values(payload, account_id)
            self.projector.set_account_values(values)
            for value in values:
                events.append(self._event(BrokerEventType.ACCOUNT_VALUE_CHANGED, account_id, value.raw, value.source_event_time, broker_event_id=f"{value.key}:{value.segment}:{value.currency}"))
        elif topic.startswith("sld"):
            account_id = _topic_account(raw_topic)
            payload = {str(row.get("key") or "").replace("LedgerList", ""): row for row in rows if isinstance(row, dict) and row.get("key")}
            ledger = normalize_ledger(payload, account_id)
            self.projector.merge_ledger(ledger)
            for balance in ledger:
                events.append(self._event(BrokerEventType.LEDGER_VALUE_CHANGED, account_id, balance.raw, balance.source_event_time, broker_event_id=balance.currency))
        for event in events:
            self.projector.record_activity(event)
        if self.sink and events:
            self.sink.persist_canonical_events(events)
        return events

    async def reconcile(self) -> dict[str, Any]:
        before = self.projector.snapshot()
        differences: dict[str, Any] = {
            "account_values": [],
            "ledger": [],
            "orders": [],
            "positions": [],
            "executions": [],
        }
        for account in before.accounts:
            if account.can_view:
                account_values = await self.adapter.canonical_account_values(account.account_id)
                previous_values = {
                    (row.key, row.segment, row.currency): row.value
                    for row in before.account_values
                    if row.account_id == account.account_id
                }
                current_values = {
                    (row.key, row.segment, row.currency): row.value
                    for row in account_values
                }
                if previous_values != current_values:
                    differences["account_values"].append(
                        {
                            "account_id": account.account_id,
                            "before": json_safe(previous_values),
                            "after": json_safe(current_values),
                        }
                    )
                self.projector.replace_account_values(account.account_id, account_values)
                ledger = await self.adapter.canonical_ledger(account.account_id)
                previous_ledger = {
                    row.currency: row.values
                    for row in before.ledger
                    if row.account_id == account.account_id
                }
                current_ledger = {row.currency: row.values for row in ledger}
                if previous_ledger != current_ledger:
                    differences["ledger"].append(
                        {
                            "account_id": account.account_id,
                            "before": json_safe(previous_ledger),
                            "after": json_safe(current_ledger),
                        }
                    )
                self.projector.replace_ledger(account.account_id, ledger)
                manifest, positions = await self.adapter.canonical_position_snapshot(account.account_id)
                previous = {(row.instrument.conid, row.model): row.quantity for row in before.positions if row.account_id == account.account_id}
                current = {(row.instrument.conid, row.model): row.quantity for row in positions}
                if previous != current:
                    differences["positions"].append({"account_id": account.account_id, "before": json_safe(previous), "after": json_safe(current)})
                self.projector.apply_position_snapshot(account.account_id, manifest.snapshot_id, manifest.complete, positions)
        orders = []
        executions = []
        for account in before.accounts:
            if account.can_trade:
                orders.extend(await self.adapter.canonical_orders(account.account_id))
                executions.extend(await self.adapter.canonical_executions(account.account_id, days=7))
        previous_orders = {(row.account_id, row.broker_order_id): (row.lifecycle_state.value, row.filled_quantity) for row in before.orders}
        current_orders = {(row.account_id, row.broker_order_id): (row.lifecycle_state.value, row.filled_quantity) for row in orders}
        if previous_orders != current_orders:
            differences["orders"] = {"before": json_safe(previous_orders), "after": json_safe(current_orders)}
        previous_executions = {(row.account_id, row.execution_id) for row in before.executions}
        current_executions = {(row.account_id, row.execution_id) for row in executions}
        if previous_executions != current_executions:
            differences["executions"] = {"missing": sorted(previous_executions - current_executions), "new": sorted(current_executions - previous_executions)}
        self.projector.set_orders(orders)
        self.projector.set_executions(executions)
        event = self._event(
            BrokerEventType.RECONCILIATION_COMPLETED,
            "",
            {"status": "matched" if not any(differences.values()) else "corrected", "differences": differences},
            self.projector.as_of,
        )
        self.projector.record_activity(event)
        if self.sink:
            self.sink.persist_canonical_events([event])
            self.sink.persist_canonical_snapshot(self.projector.snapshot())
        return json_safe(event.payload)

    async def reconcile_executions(self, executions: list[Any]) -> dict[str, Any]:
        """Project simulated fills without rescanning the complete fill history.

        A historical broker emits the exact fills produced by the current
        market event. Re-fetching every prior execution after each partial
        fill is quadratic and does not add authority. Apply those new fills
        directly, while still refreshing the affected account, order, ledger,
        and complete position snapshots after every market event.
        """

        if not executions:
            return {"status": "matched", "execution_count": 0}
        if self.provider != BrokerProvider.SIMULATED or self.mode not in {
            TradingMode.REPLAY,
            TradingMode.BACKTEST,
            TradingMode.BACKTEST_DEBUG,
        }:
            return await self.reconcile()

        affected_accounts = sorted(
            {str(execution.account) for execution in executions if execution.account}
        )
        for account_id in affected_accounts:
            self.projector.replace_account_values(
                account_id,
                await self.adapter.canonical_account_values(account_id),
            )
            self.projector.replace_ledger(
                account_id,
                await self.adapter.canonical_ledger(account_id),
            )
            manifest, positions = await self.adapter.canonical_position_snapshot(
                account_id
            )
            self.projector.apply_position_snapshot(
                account_id,
                manifest.snapshot_id,
                manifest.complete,
                positions,
            )
            self.projector.set_orders(
                await self.adapter.canonical_orders(account_id)
            )

        canonical_executions = [
            normalize_execution(execution.to_cpapi(), str(execution.account))
            for execution in executions
        ]
        self.projector.set_executions(canonical_executions)
        event = self._event(
            BrokerEventType.RECONCILIATION_COMPLETED,
            "",
            {
                "status": "incremental",
                "execution_count": len(canonical_executions),
                "account_ids": affected_accounts,
            },
            max(row.source_event_time for row in canonical_executions),
        )
        self.projector.record_activity(event)
        if self.sink:
            self.sink.persist_canonical_events([event])
            self.sink.persist_canonical_snapshot(self.projector.snapshot())
        return json_safe(event.payload)

    def _event(self, event_type: BrokerEventType, account_id: str, payload: dict[str, Any], source_event_time: Any, **identity: Any) -> BrokerEventEnvelope:
        return BrokerEventEnvelope.create(
            event_type=event_type,
            provider=self.provider,
            mode=self.mode,
            account_id=account_id,
            payload=json_safe(payload),
            source_event_time=source_event_time,
            **identity,
        )


def _topic_account(topic: str) -> str:
    parts = topic.split("+")
    return parts[1] if len(parts) > 1 else ""
