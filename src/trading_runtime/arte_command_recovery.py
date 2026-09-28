"""Read-only, fail-closed audit of durable commands against broker evidence.

This is an admission gate, not an order replay mechanism. Broker history is
bounded and absence from it never proves that a command was not delivered.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Protocol

from src.trading_runtime.arte_journal_writer import (
    load_committed_order_command_page, load_committed_order_context_page,
    load_committed_order_transition_page, load_committed_prefix, VerifiedPrefix,
)
from src.trading_runtime.arte_intent_projection import load_committed_strategy_intent_page
from src.trading_runtime.ibkr_schema import Execution, LiveOrder, OrderRequest
from src.trading_runtime.strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER
from src.trading_runtime.strategy_orders import canonical_runtime_order_raw


class RecoveryBroker(Protocol):
    async def live_orders(self) -> list[LiveOrder]: ...
    async def trades(self, days: int = 7) -> list[Execution]: ...


@dataclass(frozen=True, slots=True)
class CommandRecoveryAudit:
    run_id: str
    committed_run_status: str
    committed_commands: int
    open_order_matches: int
    execution_matches: int
    terminal_transition_matches: int
    unresolved_commands: int
    unresolved_sample: tuple[tuple[str, str], ...]

    @property
    def admission_safe(self) -> bool:
        # Seeing an order or execution is not yet a complete OMS state recovery.
        return self.committed_run_status == "running" and self.committed_commands == 0


@dataclass(frozen=True, slots=True)
class RecoveredStrategyOneCommand:
    sequence: int
    command_id: str
    request: OrderRequest


def load_committed_strategy_one_command_page(
    client: Any, prefix: VerifiedPrefix, *, after_sequence: int = 0, limit: int = 200,
) -> tuple[RecoveredStrategyOneCommand, ...]:
    """Cold-read exact Strategy 1 commands; never dispatch them automatically."""
    if not 1 <= limit <= 200:
        raise ValueError("Strategy 1 command recovery page bound is invalid")
    commands = load_committed_order_command_page(
        client, prefix, after_sequence=after_sequence, limit=limit,
    )
    if not commands:
        return ()
    if any((str(command["strategy_id"]), int(command["strategy_revision"])) != (
            STRATEGY_ID, STRATEGY_NUMBER) for command in commands):
        raise RuntimeError("Command page contains a non-Strategy-1 command")
    contexts = load_committed_order_context_page(
        client, prefix, commands, include_source=True,
    )
    # V4 commands may carry OMS-approved target amendments. The V1 context
    # records only the source intent, so reconstructing such a command from
    # that intent would silently substitute the wrong canonical metadata.
    # Scan the complete committed protection history before claiming an
    # original-intent lineage; a partial page cannot prove absence.
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    if isinstance(prefix, V4CommittedPrefix):
        from src.trading_runtime.arte_journal_reader import (
            load_complete_typed_protection_history,
        )
        history = load_complete_typed_protection_history(client, prefix)
        effective_target_first: dict[tuple[str, str, str], int] = {}
        for proof in history.records:
            if (proof.payload.get("phase"), proof.payload.get("kind")) != (
                    "effective", "target"):
                continue
            key = (proof.account_id, str(proof.payload.get("order_group_id")),
                   str(proof.payload.get("client_order_id")))
            effective_target_first[key] = min(
                proof.sequence, effective_target_first.get(key, proof.sequence),
            )
        for command in commands:
            context = contexts[str(command["record_id"])]
            key = (str(command["account_id"]), str(context["order_group_id"]),
                   str(command["client_order_id"]))
            if effective_target_first.get(key, int(command["sequence"]) + 1) <= int(
                    command["sequence"]):
                raise RuntimeError(
                    "Amended Strategy 1 command needs explicit typed OMS lineage"
                )
    source_ids = tuple(sorted({context["source_intent_record_id"]
                               for context in contexts.values()}))
    sources = load_committed_strategy_intent_page(
        client, prefix, limit=limit, record_ids=source_ids,
    )
    by_source = {source.record_id: source for source in sources}
    recovered = []
    for command in commands:
        context = contexts[str(command["record_id"])]
        source = by_source[context["source_intent_record_id"]]
        created_at = datetime.fromisoformat(str(command["created_at"]).replace(" ", "T"))
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if (source.batch_id != context["source_intent_batch_id"]
                or source.sequence != context["source_intent_sequence"]
                or source.account_id != command["account_id"]
                or source.intent.intent_id != context["strategy_intent_id"]
                or source.intent.ticker.upper() != str(command["ticker"]).upper()
                or source.intent.event_time > created_at
                or source.intent.metadata):
            raise RuntimeError("Recovered command differs from exact typed source intent")
        flat = OrderRequest(
            acctId=str(command["account_id"]), conid=int(command["conid"]),
            orderType=str(command["order_type"]), side=str(command["side"]),
            quantity=(float(command["quantity"]) if command["quantity"] is not None else None),
            cashQty=(float(command["cash_quantity"]) if command["cash_quantity"] is not None else None),
            secType=str(command["security_type"]), cOID=str(command["client_order_id"]),
            parentId=str(command["parent_broker_order_id"]) or None,
            ticker=str(command["ticker"]), tif=str(command["time_in_force"]),
            outsideRTH=bool(int(command["outside_rth"])),
            price=(float(command["limit_price"]) if command["limit_price"] is not None else None),
            auxPrice=(float(command["aux_price"]) if command["aux_price"] is not None else None),
            trailingAmt=(float(command["trailing_amount"]) if command["trailing_amount"] is not None else None),
            trailingType=str(command["trailing_type"]) or None,
            listingExchange=str(command["listing_exchange"]),
            isSingleGroup=bool(int(command["single_group"])),
            manualIndicator=bool(int(command["manual_indicator"])),
            extOperator=str(command["external_operator"]) or None,
            referrer=str(command["referrer"]) or None,
            strategy=str(command["broker_strategy"]) or None,
        )
        raw = canonical_runtime_order_raw(
            flat, source.intent, run_id=prefix.run_id,
            strategy_id=STRATEGY_ID, strategy_revision=STRATEGY_NUMBER,
        )
        recovered.append(RecoveredStrategyOneCommand(
            int(command["sequence"]), str(command["command_id"]),
            replace(flat, raw=raw),
        ))
    return tuple(recovered)


async def audit_committed_commands(
    client: Any, broker: RecoveryBroker, run_id: str, *,
    page_size: int = 500, sample_limit: int = 20,
) -> CommandRecoveryAudit:
    """Audit a verified journal prefix without writing or sending orders.

    ClickHouse verification runs off the event loop. The broker's open orders
    and recent executions are snapshots, not a guarantee of complete history.
    Unknown outcomes remain unresolved and must never be automatically resent.
    """
    if not 1 <= page_size <= 1000 or sample_limit < 0:
        raise ValueError("Recovery audit bounds are invalid")
    prefix = await asyncio.to_thread(load_committed_prefix, client, run_id)
    if prefix is None:
        raise RuntimeError("No verified committed journal prefix for recovery")
    if prefix.status not in {"running", "completed", "stopped", "failed"}:
        raise RuntimeError("Committed journal status is not recognized")
    orders, executions = await asyncio.gather(
        broker.live_orders(), broker.trades(days=7),
    )
    open_by_key: dict[tuple[str, str], LiveOrder] = {}
    for order in orders:
        key = (order.account, order.cOID)
        if not key[1] or key in open_by_key:
            raise RuntimeError("Broker open-order identity is missing or duplicated")
        open_by_key[key] = order
    trades_by_key: dict[tuple[str, str], list[Execution]] = {}
    for execution in executions:
        if execution.order_ref:
            trades_by_key.setdefault((execution.account, execution.order_ref), []).append(
                execution
            )
    latest_transition: dict[tuple[str, str], dict[str, Any]] = {}
    transition_cursor = 0
    while True:
        page = await asyncio.to_thread(
            load_committed_order_transition_page, client, prefix,
            after_sequence=transition_cursor, limit=page_size,
        )
        if not page:
            break
        for transition in page:
            key = (str(transition["account_id"]), str(transition["command_id"]))
            if not key[1]:
                raise RuntimeError("Committed transition lacks a command identity")
            latest_transition[key] = transition
        transition_cursor = int(page[-1]["sequence"])
    cursor = 0
    total = open_matches = execution_matches = terminal_matches = unresolved = 0
    sample: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    seen_command_ids: set[tuple[str, str]] = set()
    while True:
        page = await asyncio.to_thread(
            load_committed_order_command_page, client, prefix,
            after_sequence=cursor, limit=page_size,
        )
        if not page:
            break
        await asyncio.to_thread(load_committed_order_context_page, client, prefix, page)
        for command in page:
            key = (str(command["account_id"]), str(command["client_order_id"]))
            if not key[1] or key in seen:
                raise RuntimeError("Committed command identity is missing or duplicated")
            seen.add(key)
            command_key = (key[0], str(command["command_id"]))
            if not command_key[1] or command_key in seen_command_ids:
                raise RuntimeError("Committed command ID is missing or duplicated")
            seen_command_ids.add(command_key)
            total += 1
            expected_conid = int(command["conid"])
            order = open_by_key.get(key)
            matched_trades = trades_by_key.get(key, ())
            transition = latest_transition.get(command_key)
            terminal = False
            if transition is not None:
                if (str(transition["client_order_id"]) != key[1]
                        or int(transition["conid"]) != expected_conid
                        or int(transition["sequence"]) <= int(command["sequence"])):
                    raise RuntimeError("Order transition contradicts committed command contract")
                terminal = bool(int(transition["terminal"]))
                if terminal:
                    terminal_matches += 1
            if order is not None:
                if order.conid != expected_conid:
                    raise RuntimeError("Broker order contradicts committed command contract")
                if terminal:
                    raise RuntimeError("Broker open order contradicts terminal transition")
                open_matches += 1
            if matched_trades:
                if any(trade.conid != expected_conid for trade in matched_trades):
                    raise RuntimeError("Broker execution contradicts committed command contract")
                execution_matches += 1
            if order is None and not matched_trades and not terminal:
                unresolved += 1
                if len(sample) < sample_limit:
                    sample.append(key)
        cursor = int(page[-1]["sequence"])
    if set(latest_transition) - seen_command_ids:
        raise RuntimeError("Committed transition has no matching order command")
    return CommandRecoveryAudit(
        run_id, prefix.status, total, open_matches, execution_matches, terminal_matches,
        unresolved, tuple(sample),
    )
