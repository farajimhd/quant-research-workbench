"""Bounded nonblocking command lane gated by a typed ClickHouse receipt.

This transport is not the live cutover. Cold broker reconciliation and complete
typed strategy evidence are required before production may construct it.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import Future as ThreadFuture
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from uuid import UUID

from src.trading_runtime.arte_command_recovery import (
    audit_committed_commands, audit_v4_fresh_command_admission,
)
from src.trading_runtime.arte_journal_writer import (
    TypedJournalBatch, V4OrderCancelBatch, V4OrderModifyCommandBatch,
)
from src.trading_runtime.ibkr_schema import OrderRequest
from src.trading_runtime.strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER


class _JournalWriter(Protocol):
    @property
    def coalesce_batches(self) -> bool: ...

    def submit(self, batch: TypedJournalBatch) -> ThreadFuture[str]: ...
    def submit_base_v4(self, batch: TypedJournalBatch) -> ThreadFuture[str]: ...
    def submit_order_cancel_v4(self, unit: V4OrderCancelBatch) -> ThreadFuture[str]: ...
    def submit_order_modify_command_v4(
        self, unit: V4OrderModifyCommandBatch,
    ) -> ThreadFuture[str]: ...


class _OrderBroker(Protocol):
    async def place_orders(
        self, account_id: str, orders: list[OrderRequest],
    ) -> list[dict[str, Any]]: ...

    async def cancel_order(self, account_id: str, order_id: str) -> dict[str, Any]: ...
    async def modify_order(
        self, account_id: str, order_id: str, order: OrderRequest,
    ) -> list[dict[str, Any]]: ...


class CommandQueueFull(RuntimeError):
    """New order admission must stop; no command was silently dropped."""


@dataclass(slots=True)
class _PendingCommand:
    batch: TypedJournalBatch
    account_id: str
    orders: tuple[OrderRequest, ...]
    result: asyncio.Future[list[dict[str, Any]]]


@dataclass(slots=True)
class _PendingCancel:
    unit: V4OrderCancelBatch
    account_id: str
    order_id: str
    result: asyncio.Future[dict[str, Any]]


@dataclass(slots=True)
class _PendingModify:
    unit: V4OrderModifyCommandBatch
    account_id: str
    order_id: str
    request: OrderRequest
    result: asyncio.Future[list[dict[str, Any]]]


def _command_matches_order(row: Any, order: OrderRequest) -> bool:
    """Match every broker-effective field to its committed typed command."""
    def amount(value: Any) -> Decimal | None:
        if value is None:
            return None
        try:
            result = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
        return result if result.is_finite() else None

    fields = (
        ("account_id", order.acctId), ("client_order_id", order.cOID),
        ("conid", order.conid), ("ticker", order.ticker),
        ("side", order.side), ("order_type", order.orderType),
        ("time_in_force", order.tif), ("security_type", order.secType),
        ("listing_exchange", order.listingExchange),
        ("parent_broker_order_id", order.parentId or ""),
        ("trailing_type", order.trailingType or ""),
        ("external_operator", order.extOperator or ""),
        ("referrer", order.referrer or ""),
        ("broker_strategy", order.strategy or ""),
    )
    flags = (
        ("outside_rth", order.outsideRTH),
        ("single_group", order.isSingleGroup),
        ("manual_indicator", order.manualIndicator),
    )
    prices = (
        ("quantity", order.quantity), ("cash_quantity", order.cashQty),
        ("limit_price", order.price), ("aux_price", order.auxPrice),
        ("trailing_amount", order.trailingAmt),
    )
    return (not order.strategyParameters
            and all(key.startswith("canonical_") for key in order.raw)
            and all(str(row[key]) == str(value) for key, value in fields)
            and all(int(row[key]) == int(value) for key, value in flags)
            and all((value is None or amount(value) is not None)
                    and amount(row[key]) == amount(value)
                    and (row[key] is None) == (value is None)
                    for key, value in prices))


class ArteCommandDispatcher:
    """One ordered broker lane; never await this from a market-data callback."""

    def __init__(self, writer: _JournalWriter, broker: _OrderBroker,
                 *, capacity: int = 64) -> None:
        if capacity < 1:
            raise ValueError("Command queue capacity must be positive")
        if getattr(writer, "coalesce_batches", None) is not False:
            raise ValueError("Command lane requires exact, non-coalesced journal receipts")
        profile = getattr(writer, "journal_profile", None)
        if profile == "backtest_v4":
            raise ValueError("Live command lane cannot use a Backtest V4 writer")
        self._live_v4 = profile == "live_v4"
        if (self._live_v4
                and (not callable(getattr(writer, "submit_base_v4", None))
                     or not callable(getattr(writer, "submit_order_cancel_v4", None))
                     or not callable(getattr(writer, "submit_order_modify_command_v4", None)))):
            raise ValueError("Live V4 command lane requires its explicit family writer")
        self._live_lease = getattr(writer, "live_v4_lease", None) if self._live_v4 else None
        if self._live_v4 and not callable(getattr(self._live_lease, "assert_current", None)):
            raise ValueError("Live V4 command lane requires its pinned Keeper lease")
        self._writer = writer
        self._broker = broker
        self._queue: asyncio.Queue[_PendingCommand | _PendingCancel | _PendingModify | None] = asyncio.Queue(maxsize=capacity)
        self._task: asyncio.Task[None] | None = None
        self._error: BaseException | None = None
        self._admission_error: CommandQueueFull | None = None
        self._closed = False
        self._audited_run_id: str | None = None
        self._starting = False
        # Only broker IDs acknowledged on this fenced lane may be cancelled.
        # Cold recovery must reconstruct this map before resumed admission.
        self._placed_order_groups: dict[tuple[str, str], tuple[str, str]] = {}
        # A cancel response is not terminal broker proof. Once queued, the
        # target cannot be amended or cancelled again without reconciliation.
        self._canceling_targets: set[tuple[str, str]] = set()

    async def start(self, client: Any, run_id: str) -> None:
        """Audit committed state on the control plane before accepting orders."""
        if self._task is not None or self._closed or self._starting:
            raise RuntimeError("Command dispatcher cannot start twice")
        self._starting = True
        try:
            if self._live_v4:
                if self._live_lease.run_id != run_id:
                    raise ValueError("Live V4 command lease differs from run")
                await asyncio.to_thread(self._live_lease.assert_current)
            audit = (await audit_v4_fresh_command_admission(client, run_id)
                     if self._live_v4 else
                     await audit_committed_commands(client, self._broker, run_id))
            if self._closed:
                raise RuntimeError("Command dispatcher closed during recovery audit")
            if audit.run_id != run_id or not audit.admission_safe:
                raise RuntimeError("Command dispatcher requires complete OMS recovery")
            if self._live_v4:
                await asyncio.to_thread(self._live_lease.assert_current)
            self._audited_run_id = run_id
            self._task = asyncio.create_task(self._run(), name="arte-command-dispatcher")
        finally:
            self._starting = False

    def submit(
        self, batch: TypedJournalBatch, account_id: str,
        orders: tuple[OrderRequest, ...],
    ) -> asyncio.Future[list[dict[str, Any]]]:
        """Enqueue without network or durability wait; queue saturation rejects."""
        if self._task is None or self._closed:
            raise RuntimeError("Command dispatcher is not accepting orders")
        if batch.run_id != self._audited_run_id:
            raise ValueError("Command run differs from its audited journal prefix")
        if self._error is not None:
            raise RuntimeError("Command dispatcher requires broker reconciliation") from self._error
        if self._admission_error is not None:
            raise RuntimeError("Command admission stopped after queue saturation") from self._admission_error
        if (batch.status != "running" or not orders
                or len(batch.order_commands) != len(orders)
                or any(order.acctId != account_id for order in orders)
                or any(not _command_matches_order(row, order)
                       for row, order in zip(batch.order_commands, orders))):
            raise ValueError("Typed command batch differs from broker requests")
        if self._live_v4:
            commands = {str(row.get("record_id")): row
                        for row in batch.order_commands}
            lineage = {str(row.get("parent_record_id")): row
                       for row in batch.v4_command_lineages}
            contexts = {str(row.get("parent_record_id")): row
                        for row in batch.order_contexts}
            intent_uses = {str(row.get("parent_record_id")): row
                           for row in batch.intent_uses}
            if (len(commands) != len(orders) or len(lineage) != len(orders)
                    or len(contexts) != len(orders)
                    or len(intent_uses) != len(orders)
                    or set(commands) != set(lineage)
                    or set(commands) != set(contexts)
                    or set(commands) != set(intent_uses)
                    or any((row.get("strategy_id"), row.get("strategy_revision"))
                           != (STRATEGY_ID, STRATEGY_NUMBER)
                           for row in commands.values())
                    or any(row.get("run_id") != batch.run_id
                           or row.get("batch_id") != batch.batch_id
                           or row.get("account_id") != commands[parent].get("account_id")
                           for parent, row in lineage.items())
                    or any(row.get("run_id") != batch.run_id
                           or row.get("batch_id") != batch.batch_id
                           or row.get("account_id") != account_id
                           or not row.get("order_group_id")
                           or not row.get("strategy_intent_id")
                           or not row.get("policy_version")
                           for row in contexts.values())
                    or any(row.get("run_id") != batch.run_id
                           or row.get("batch_id") != batch.batch_id
                           or row.get("account_id") != account_id
                           or not row.get("intent_record_id")
                           or not row.get("intent_content_hash")
                           for row in intent_uses.values())):
                raise ValueError("Live V4 command lacks Strategy 1 typed lineage")
        # TypedJournalBatch already freezes row mappings at construction.
        # OrderRequest.raw remains mutable, so own the broker request snapshot.
        sealed_orders = deepcopy(orders)
        result: asyncio.Future[list[dict[str, Any]]] = asyncio.get_running_loop().create_future()
        try:
            self._queue.put_nowait(_PendingCommand(
                batch, account_id, sealed_orders, result))
        except asyncio.QueueFull as exc:
            self._admission_error = CommandQueueFull(
                "Command queue is full; stop new order admission"
            )
            raise self._admission_error from exc
        return result

    def submit_cancel(
        self, unit: V4OrderCancelBatch, account_id: str, order_id: str,
    ) -> asyncio.Future[dict[str, Any]]:
        """Queue one typed protection cancellation, never a raw OMS cancel."""
        if not self._live_v4 or self._task is None or self._closed:
            raise RuntimeError("Live V4 cancellation lane is not accepting orders")
        if self._error is not None:
            raise RuntimeError("Command dispatcher requires broker reconciliation") from self._error
        if self._admission_error is not None:
            raise RuntimeError("Command admission stopped after queue saturation") from self._admission_error
        if not isinstance(unit, V4OrderCancelBatch):
            raise TypeError("Cancellation needs its normalized V4 batch")
        base, detail = unit.base, unit.cancellation
        event = base.events[0]
        if (base.run_id != self._audited_run_id or base.status != "running"
                or (event["category"], event["entity_type"])
                   != ("command", "order_cancel")
                or event["account_id"] != account_id
                or event["entity_id"] != order_id
                or detail["record_id"] != event["record_id"]
                or detail["run_id"] != base.run_id
                or detail["batch_id"] != base.batch_id
                or detail["broker_order_id"] != order_id
                or detail["result_kind"] != "command"
                or detail["reason"] != "replace_strategy_protection"
                or not detail["order_group_id"] or not detail["intent_id"]
                or event["causation_id"] != detail["intent_id"]
                or self._placed_order_groups.get((account_id, order_id))
                   != (detail["ticker"], detail["order_group_id"])
                or (account_id, order_id) in self._canceling_targets
                or (detail["strategy_id"], detail["strategy_revision"])
                   != (STRATEGY_ID, STRATEGY_NUMBER)):
            raise ValueError("Live V4 cancellation lacks exact Strategy 1 lineage")
        result: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        try:
            self._queue.put_nowait(_PendingCancel(unit, account_id, order_id, result))
        except asyncio.QueueFull as exc:
            self._admission_error = CommandQueueFull(
                "Command queue is full; stop new order admission")
            raise self._admission_error from exc
        self._canceling_targets.add((account_id, order_id))
        return result

    def submit_modify(
        self, unit: V4OrderModifyCommandBatch, account_id: str,
        order_id: str, request: OrderRequest,
    ) -> asyncio.Future[list[dict[str, Any]]]:
        """Enqueue one exact live amendment; its typed receipt precedes broker I/O."""
        if not self._live_v4 or self._task is None or self._closed:
            raise RuntimeError("Live V4 modification lane is not accepting orders")
        if self._error is not None:
            raise RuntimeError("Command dispatcher requires broker reconciliation") from self._error
        if self._admission_error is not None:
            raise RuntimeError("Command admission stopped after queue saturation") from self._admission_error
        if not isinstance(unit, V4OrderModifyCommandBatch):
            raise TypeError("Modification needs its normalized V4 batch")
        base, detail = unit.base, unit.modification
        event = base.events[0]
        if (base.run_id != self._audited_run_id or base.status != "running"
                or (event["category"], event["entity_type"])
                   != ("command", "order_modify")
                or event["account_id"] != account_id
                or event["entity_id"] != order_id
                or detail["record_id"] != event["record_id"]
                or detail["run_id"] != base.run_id
                or detail["batch_id"] != base.batch_id
                or detail["broker_order_id"] != order_id
                or detail["order_group_id"] == ""
                or detail["intent_id"] == ""
                or event["causation_id"] != detail["intent_id"]
                or self._placed_order_groups.get((account_id, order_id))
                   != (detail["ticker"], detail["order_group_id"])
                or (account_id, order_id) in self._canceling_targets
                or (detail["strategy_id"], detail["strategy_revision"])
                   != (STRATEGY_ID, STRATEGY_NUMBER)
                or not _command_matches_order(detail, request)):
            raise ValueError("Live V4 modification lacks exact Strategy 1 lineage")
        result: asyncio.Future[list[dict[str, Any]]] = asyncio.get_running_loop().create_future()
        try:
            self._queue.put_nowait(_PendingModify(
                unit, account_id, order_id, deepcopy(request), result))
        except asyncio.QueueFull as exc:
            self._admission_error = CommandQueueFull(
                "Command queue is full; stop new order admission")
            raise self._admission_error from exc
        return result

    async def _run(self) -> None:
        while True:
            pending = await self._queue.get()
            try:
                if pending is None:
                    return
                if self._error is not None:
                    raise RuntimeError("An earlier command requires broker reconciliation") from self._error
                if self._live_v4:
                    # A queued command may outlive its owner. Do not publish a
                    # new command under a Keeper lease already known to be lost.
                    await asyncio.to_thread(self._live_lease.assert_current)
                batch = (pending.unit.base if isinstance(pending, (_PendingCancel, _PendingModify))
                         else pending.batch)
                receipt = (self._writer.submit_order_cancel_v4(pending.unit)
                           if isinstance(pending, _PendingCancel)
                           else self._writer.submit_order_modify_command_v4(pending.unit)
                           if isinstance(pending, _PendingModify)
                           else self._writer.submit_base_v4(batch)
                           if self._live_v4 else self._writer.submit(batch))
                committed_id = await asyncio.wrap_future(receipt)
                if UUID(str(committed_id)) != UUID(batch.batch_id):
                    raise RuntimeError("Committed command receipt differs from submitted batch")
                if self._live_v4:
                    # A receipt under an old owner cannot dispatch a new
                    # broker side effect after a Keeper takeover.
                    await asyncio.to_thread(self._live_lease.assert_current)
                response = (await self._broker.cancel_order(
                    pending.account_id, pending.order_id)
                    if isinstance(pending, _PendingCancel)
                    else await self._broker.modify_order(
                        pending.account_id, pending.order_id, pending.request)
                    if isinstance(pending, _PendingModify)
                    else await self._broker.place_orders(
                        pending.account_id, list(pending.orders)))
                if self._live_v4 and isinstance(pending, _PendingCommand):
                    if (not isinstance(response, list)
                            or len(response) != len(pending.orders)
                            or any(not isinstance(row, dict)
                                   or not str(row.get("order_id") or "")
                                   for row in response)):
                        raise RuntimeError(
                            "Live place reply lacks exact order identities; reconcile broker")
                    contexts = {str(row["parent_record_id"]): row
                                for row in pending.batch.order_contexts}
                    additions = {}
                    for order, command, reply in zip(
                            pending.orders, pending.batch.order_commands, response):
                        key = (pending.account_id, str(reply["order_id"]))
                        if key in additions or key in self._placed_order_groups:
                            raise RuntimeError("Live place reply repeats a broker order ID")
                        context = contexts[str(command["record_id"])]
                        additions[key] = (order.ticker, str(context["order_group_id"]))
                    self._placed_order_groups.update(additions)
                pending.result.set_result(response)
            except asyncio.CancelledError as exc:
                self._error = RuntimeError(
                    "Command dispatcher interrupted; broker reconciliation required"
                )
                if pending is not None and not pending.result.done():
                    pending.result.set_exception(self._error)
                while True:
                    try:
                        queued = self._queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if queued is not None and not queued.result.done():
                        queued.result.set_exception(self._error)
                    self._queue.task_done()
                raise exc
            except Exception as exc:
                self._error = exc
                if pending is not None and not pending.result.done():
                    pending.result.set_exception(exc)
            finally:
                self._queue.task_done()

    async def close(self) -> None:
        """Control-plane drain only; a failed send remains unresolved."""
        if not self._closed:
            self._closed = True
            if self._task is not None:
                if not self._task.done():
                    await self._queue.put(None)
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
        if self._error is not None:
            raise RuntimeError("Command dispatcher requires broker reconciliation") from self._error
        if self._admission_error is not None:
            raise RuntimeError("Command admission stopped after queue saturation") from self._admission_error
