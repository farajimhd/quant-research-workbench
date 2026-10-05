"""Read-only causal exit and broker-observed equity evidence for saved runs."""
from __future__ import annotations

from src.backend.backtest_v4_saved_review import declared_saved_read_operation

from datetime import datetime
from decimal import Decimal


def protection_exit_evidence(lifecycle, execution):
    """Journal order is causal; bucket fill time is not an OMS event clock."""
    states = [row for row in lifecycle.get("protection_timeline", ())
              if row["phase"] == "effective"
              and row["order_id"] == execution.broker_order_id
              and int(row["sequence"]) < execution.journal_sequence
              and datetime.fromisoformat(row["event_time"]) <= execution.source_event_time]
    if not states:
        return None
    state = max(states, key=lambda row: int(row["sequence"]))
    if not state["active"] or state["kind"] not in {"stop", "target"}:
        return None
    return {"reason": "stop_hit" if state["kind"] == "stop" else "target_hit",
            "source": "effective_protection_order", "source_sequence": int(state["sequence"])}


def managed_exit_evidence(client, prefix, executions):
    """Resolve only exact committed command/context/intent identities, in batches."""
    from src.backend.backtest_v4_saved_review import _complete_detail_rows, _utc_timestamp
    from src.backend.backtest_terminal_v2_fence import _verify_v1_rows
    from src.trading_runtime.arte_journal_writer import (
        _CONTRACTS, _committed_batch_filter, _literal, _rows,
        load_committed_order_command_page,
    )
    from src.trading_runtime.arte_intent_projection import load_committed_strategy_intent_page

    wanted = {(e.account_id, e.client_order_id) for e in executions if e.client_order_id}
    if not wanted:
        return {}
    commands = _complete_detail_rows(load_committed_order_command_page, client, prefix)
    selected = [row for row in commands if (row["account_id"], row["client_order_id"]) in wanted]
    command_by_key = {(row["account_id"], row["client_order_id"]): row for row in selected}
    if len(command_by_key) != len(selected):
        raise RuntimeError("Exit attribution repeats an exact command identity")
    contexts = {}
    intents = {}
    for offset in range(0, len(selected), 500):
        group = selected[offset:offset + 500]
        ids = ",".join(f"toUUID({_literal(row['record_id'])})" for row in group)
        table = "trading_order_command_context_v1"
        columns = ",".join(name for name, _ in _CONTRACTS[table].columns)
        rows = _rows(client, f"SELECT {columns} FROM arte.{table} "
            f"WHERE run_id={_literal(prefix.run_id)} AND parent_record_id IN ({ids}) "
            f"{_committed_batch_filter(prefix)}LIMIT {len(group) + 1} FORMAT JSONEachRow")
        verified = _verify_v1_rows(table, tuple(rows))
        parents = {row["record_id"]: row for row in group}
        for row in verified:
            parent = parents.get(row["parent_record_id"])
            if (parent is None or row["parent_record_id"] in contexts
                    or any(row[key] != parent[key] for key in ("run_id", "account_id", "batch_id"))):
                raise RuntimeError("Exit attribution command context differs")
            contexts[row["parent_record_id"]] = row
        intent_ids = sorted({row["strategy_intent_id"] for row in verified})
        if not intent_ids:
            continue
        requested = ",".join(_literal(value) for value in intent_ids)
        identities = _rows(client, "SELECT record_id,intent_id FROM arte.trading_strategy_intent_v1 "
            f"WHERE run_id={_literal(prefix.run_id)} AND intent_id IN ({requested}) "
            f"{_committed_batch_filter(prefix)}LIMIT {len(intent_ids) + 1} FORMAT JSONEachRow")
        if len(identities) != len(intent_ids) or {row["intent_id"] for row in identities} != set(intent_ids):
            raise RuntimeError("Exit attribution lacks unique committed intent evidence")
        recovered = load_committed_strategy_intent_page(client, prefix, limit=500,
            record_ids=tuple(row["record_id"] for row in identities))
        intents.update({row.intent.intent_id: row for row in recovered})
    result = {}
    for execution in executions:
        command = command_by_key.get((execution.account_id, execution.client_order_id))
        context = contexts.get(command["record_id"]) if command else None
        if context is None:
            continue  # Missing proof stays unavailable, never inferred from price or names.
        source = intents[context["strategy_intent_id"]]
        intent = source.intent
        if (command["ticker"] != execution.instrument.symbol
                or int(command["conid"]) != execution.instrument.conid
                or command["strategy_id"] != execution.strategy_id
                or int(command["strategy_revision"]) != execution.strategy_revision
                or command["side"] != execution.side
                or source.account_id != execution.account_id or intent.ticker != execution.instrument.symbol
                or not source.sequence < int(command["sequence"]) < execution.journal_sequence
                or not intent.event_time <= _utc_timestamp(command["created_at"]) <= execution.source_event_time):
            raise RuntimeError("Exit attribution causal command/intent lineage differs from fill")
        if intent.action == "exit" and intent.reason:
            result[execution.execution_id] = {"reason": intent.reason, "source": "typed_strategy_intent",
                "source_sequence": source.sequence, "source_record_id": source.record_id,
                "intent_id": intent.intent_id, "command_record_id": command["record_id"],
                "command_sequence": int(command["sequence"])}
    return result


def attach_exit_evidence(client, prefix, lifecycles, executions):
    by_id = {row.execution_id: row for row in executions}
    pending = []
    grouped = []
    for lifecycle in lifecycles:
        opening_side = "BUY" if lifecycle["side"] == "LONG" else "SELL"
        exits = [by_id[str(identity)] for identity in lifecycle["execution_ids"]
                 if by_id[str(identity)].side != opening_side]
        orders = {}
        for execution in sorted(exits, key=lambda row: row.journal_sequence):
            orders.setdefault(execution.broker_order_id, []).append(execution)
        evidence = {}
        for order, fills in orders.items():
            first = fills[0]
            proof = ({"reason": first.exit_reason, "source": "journal",
                      "source_sequence": first.journal_sequence} if first.exit_reason
                     else protection_exit_evidence(lifecycle, first))
            if proof is None:
                pending.append(first)
            evidence[order] = proof
        grouped.append((lifecycle, exits, orders, evidence))
    managed = managed_exit_evidence(client, prefix, pending)
    for lifecycle, exits, orders, evidence in grouped:
        components = []
        for order, fills in orders.items():
            proof = evidence[order] or managed.get(fills[0].execution_id)
            components.append({"broker_order_id": order,
                "execution_ids": [row.execution_id for row in fills],
                "quantity": sum((row.quantity for row in fills), Decimal(0)),
                **(proof or {"reason": "unavailable", "source": "unavailable"})})
        lifecycle["exit_components"] = components
        if not lifecycle.get("closed_at") or not exits:
            continue
        closing = datetime.fromisoformat(lifecycle["closed_at"])
        terminal_orders = {row.broker_order_id for row in exits if row.source_event_time == closing}
        terminal = [row for row in components if row["broker_order_id"] in terminal_orders]
        reasons = {row["reason"] for row in terminal}
        if terminal and "unavailable" not in reasons:
            lifecycle["presentation_exit_reason"] = (next(iter(reasons)) if len(reasons) == 1
                else "mixed:" + ",".join(sorted(reasons)))
            lifecycle["presentation_exit_reason_source"] = "+".join(sorted({row["source"] for row in terminal}))


@declared_saved_read_operation
def load_broker_observed_drawdown(client, run_id):
    """Verify normalized snapshot families and the terminal committed market cursor."""
    from src.backend.backtest_v4_saved_review import _terminal_attestation
    from src.backend.typed_backtest_review_core import _head_matches
    from src.trading_runtime.arte_journal_projection import load_latest_backtest_cursor
    from src.trading_runtime.strategy_one_broker_match_snapshot import (
        load_unattested_broker_match_snapshot, float64_from_bits,
    )
    prefix = _terminal_attestation(client, run_id, None)["prefix"]
    cursor = load_latest_backtest_cursor(client, prefix)
    if not cursor or not 0 < cursor["event_sequence"] <= prefix.last_sequence:
        raise RuntimeError("Broker drawdown lacks a terminal committed market cursor")
    image = load_unattested_broker_match_snapshot(client, run_id=run_id,
        checkpoint_sequence=cursor["event_sequence"])
    root = image.snapshot
    if (root["boundary_ms"] != cursor["boundary_ms"] or root["session_date"] != cursor["session_date"]
            or root["performance_complete"] != 1 or not _head_matches(client, run_id, prefix)):
        raise RuntimeError("Broker drawdown snapshot differs from complete terminal evidence")
    maximum = float64_from_bits(root["maximum_drawdown_f64_bits"], "maximum drawdown")
    if maximum < 0:
        raise RuntimeError("Broker drawdown is negative")
    return {"maximum_drawdown": maximum,
        "equity_pnl_peak": float64_from_bits(root["equity_peak_f64_bits"], "equity peak"),
        "scope": "broker-observed marked-equity extrema; separate from closed-episode drawdown",
        "mark_policy": "completed 100ms valid close, otherwise quote midpoint aged at most 1s; last conid mark retained when neither is available",
        "limitations": ["Per-ticker and post-fill updates are asynchronous, not an atomic all-ticker equity curve",
                        "Retained marks have no age limit or freshness evidence; complete tracking does not prove fresh marks",
                        "No intrabar high/low equity path or guaranteed liquidation value",
                        "Snapshot root/children and committed cursor verified; Keeper head not independently attested"],
        "performance_as_of": root["performance_as_of"], "tracking_complete": True,
        "mark_age_available": False,
        "snapshot_hash": root["content_hash"], "checkpoint_sequence": cursor["event_sequence"],
        "verified_terminal_sequence": prefix.last_sequence, "boundary_ms": root["boundary_ms"]}
