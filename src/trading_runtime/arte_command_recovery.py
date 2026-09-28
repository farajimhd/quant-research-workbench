"""Read-only, fail-closed audit of durable commands against broker evidence.

This is an admission gate, not an order replay mechanism. Broker history is
bounded and absence from it never proves that a command was not delivered.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Protocol

from src.trading_runtime.arte_journal_writer import (
    load_committed_order_command_page, load_committed_order_context_page,
    load_committed_order_transition_page, load_committed_prefix, VerifiedPrefix,
    _CONTRACTS, _canonical_typed_content, _committed_batch_filter, _literal, _rows,
)
from src.trading_runtime.arte_intent_projection import load_committed_strategy_intent_page
from src.trading_runtime.ibkr_schema import Execution, LiveOrder, OrderRequest
from src.trading_runtime.strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER
from src.trading_runtime.strategy_orders import canonical_runtime_order_raw
from src.trading_runtime.journal_contract import canonical_json


def _v4_command_lineage(client: Any, prefix: VerifiedPrefix,
                        commands: tuple[dict[str, Any], ...],
                        contexts: dict[str, dict[str, Any]]) -> dict[str, tuple[str, Any, Any, Any]]:
    """Verify one compact lineage marker and any exact protection proof."""
    from src.trading_runtime.arte_journal_commit_v4 import load_verified_commit_v4
    from src.trading_runtime.arte_journal_reader import (
        load_complete_typed_protection_history,
    )
    from src.trading_runtime.arte_journal_schema import V4_ORDER_COMMAND_LINEAGE

    table = V4_ORDER_COMMAND_LINEAGE.name
    for batch_id in {str(command["batch_id"]) for command in commands}:
        _, families = load_verified_commit_v4(
            client, run_id=prefix.run_id, batch_id=batch_id)
        if not any(row["family_name"] == table for row in families):
            raise RuntimeError("Strategy 1 command batch lacks sealed lineage markers")
    ids = ",".join(f"toUUID({_literal(str(command['record_id']))})"
                   for command in commands)
    columns = ",".join(name for name, _ in _CONTRACTS[table].columns)
    rows = _rows(client,
        f"SELECT {columns} FROM arte.{table} "
        f"WHERE run_id={_literal(prefix.run_id)} "
        f"AND parent_record_id IN ({ids}) "
        f"{_committed_batch_filter(prefix)}"
        f"LIMIT {len(commands) + 1} FORMAT JSONEachRow")
    if len(rows) != len(commands):
        raise RuntimeError("Strategy 1 command has missing or excess lineage markers")
    commands_by_id = {str(command["record_id"]): command for command in commands}
    markers: dict[str, dict[str, Any]] = {}
    for row in rows:
        parent_id = str(row["parent_record_id"])
        command = commands_by_id.get(parent_id)
        if command is None or parent_id in markers:
            raise RuntimeError("Strategy 1 command has ambiguous lineage marker")
        content = {key: value for key, value in row.items() if key != "content_hash"}
        digest = sha256(canonical_json(_canonical_typed_content(
            table, content, stored_utc=True)).encode("utf-8")).hexdigest()
        if (digest != str(row["content_hash"])
                or str(row["batch_id"]) != str(command["batch_id"])
                or row["event_month"] != command["event_month"]
                or row["account_id"] != command["account_id"]):
            raise RuntimeError("Strategy 1 command lineage differs from its sealed row")
        markers[parent_id] = row
    wanted_oms = {str(marker["oms_group_record_id"]) for marker in markers.values()
                  if marker["oms_group_record_id"] is not None}
    history = (load_complete_typed_protection_history(client, prefix)
               if wanted_oms else None)
    proofs = ({record.record_id: record for record in history.records}
              if history is not None else {})
    if history is not None and len(proofs) != len(history.records):
        raise RuntimeError("Strategy 1 protection history repeats a proof identity")
    states = {}
    if wanted_oms:
        from src.trading_runtime.arte_oms_projection import (
            load_committed_oms_group_state_page,
        )
        cursor = 0
        scanned = 0
        while wanted_oms - set(states):
            page = load_committed_oms_group_state_page(
                client, prefix, after_sequence=cursor, limit=200,
                require_tactic=True)
            if not page:
                break
            for state in page:
                if state.sequence <= cursor:
                    raise RuntimeError("Strategy 1 OMS lineage inventory did not advance")
                identity = str(state.group["record_id"])
                if identity in wanted_oms:
                    if identity in states:
                        raise RuntimeError("Strategy 1 OMS lineage repeated a group revision")
                    states[identity] = state
                scanned += 1
                if scanned > 20_000:
                    raise RuntimeError("Strategy 1 OMS lineage exceeds its cold scan bound")
            cursor = page[-1].sequence
        if set(states) != wanted_oms:
            raise RuntimeError("Strategy 1 command lacks an exact OMS group revision")
    result = {}
    for parent_id, marker in markers.items():
        command = commands_by_id[parent_id]
        context = contexts[parent_id]
        kind, proof_id = marker["lineage_kind"], marker["proof_record_id"]
        oms_id = marker["oms_group_record_id"]
        if kind == "initial_intent" and proof_id is None and oms_id is None:
            result[parent_id] = (kind, None, None, history)
            continue
        if ((kind == "oms_group" and proof_id is None and oms_id is not None)
                or (kind == "oms_target_amendment" and proof_id is not None
                    and oms_id is not None)):
            state = states[str(oms_id)]
            if (state.sequence >= int(command["sequence"])
                    or state.group["account_id"] != command["account_id"]
                    or state.group["group_id"] != context["order_group_id"]
                    or state.group["strategy_intent_id"] != context["strategy_intent_id"]):
                raise RuntimeError("Strategy 1 command OMS lineage differs from its group")
        else:
            raise RuntimeError("Strategy 1 command lineage kind is invalid")
        if kind == "oms_group":
            result[parent_id] = (kind, None, state, history)
            continue
        proof = proofs.get(str(proof_id))
        if (proof is None or proof.sequence >= int(command["sequence"])
                or proof.account_id != command["account_id"]
                or proof.payload.get("order_group_id") != context["order_group_id"]
                or proof.payload.get("client_order_id") != command["client_order_id"]
                or proof.payload.get("phase") != "effective"
                or proof.payload.get("kind") != "target"
                or proof.payload.get("action") != "replace_profit_target"
                or command["limit_price"] is None
                or proof.payload.get("price") != float(command["limit_price"])
                or not proof.payload.get("intent_id")):
            raise RuntimeError("Strategy 1 amended command lacks its exact proof")
        result[parent_id] = (kind, proof, state, history)
    return result


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
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    lineages = (_v4_command_lineage(client, prefix, commands, contexts)
                if isinstance(prefix, V4CommittedPrefix) else {})
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
        kind, proof, state, history = lineages.get(
            str(command["record_id"]), ("initial_intent", None, None, None))
        if kind != "initial_intent":
            from src.trading_runtime.arte_oms_projection import (
                reconstruct_strategy_one_oms_lineage,
            )
            if state.intent_record_id != source.record_id:
                raise RuntimeError("Strategy 1 command OMS source revision differs")
            orders = reconstruct_strategy_one_oms_lineage(state, source, history)
            matching = [order for order in orders if order.cOID == flat.cOID]
            if len(matching) != 1:
                raise RuntimeError("Strategy 1 command lacks one exact OMS client order")
            oms_fields, command_fields = matching[0].to_cpapi(), flat.to_cpapi()
            if oms_fields != command_fields:
                changed = tuple(sorted(key for key in set(oms_fields) | set(command_fields)
                                       if oms_fields.get(key) != command_fields.get(key)))
                raise RuntimeError("Strategy 1 command differs from OMS broker fields: "
                                   + ",".join(changed[:8]))
            if matching[0].raw == raw:
                raise RuntimeError("Strategy 1 command OMS marker is redundant")
            raw = matching[0].raw
            metadata = raw["canonical_metadata"]
            if (kind == "oms_target_amendment") != (
                    metadata.get("reason") == "structural_profit_target_advanced"):
                raise RuntimeError("Strategy 1 command OMS amendment kind differs")
            if proof is not None and (
                    metadata.get("replacement_intent_id") != proof.payload["intent_id"]
                    or metadata.get("target_price") != proof.payload["price"]):
                raise RuntimeError("Strategy 1 command OMS proof differs")
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
