"""Closed native command and cold OMS lineage for independent Strategy 45."""
from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from .strategy_forty_five_rules import STRATEGY_ID, STRATEGY_NUMBER


def command_ids(rows):
    """Add only this exact numbered identity to the mandatory lineage graph."""
    return {str(UUID(str(row["record_id"]))) for row in rows
            if row["strategy_id"] == STRATEGY_ID
            and type(row["strategy_revision"]) is int
            and row["strategy_revision"] == STRATEGY_NUMBER}


def reconstruct_leg_orders(state, source_intent, protection_history, *,
                           admission_reservation, admission_decision):
    from .arte_intent_projection import RecoveredIntent
    from .arte_journal_reader import CompleteProtectionHistory
    from .arte_oms_projection import (
        RecoveredOmsGroupState, _ColdLineageView,
        _approved_strategy_one_oms_intent, canonical_oms_order_metadata,
    )
    if (type(state) is not RecoveredOmsGroupState
            or type(source_intent) is not RecoveredIntent
            or type(protection_history) is not CompleteProtectionHistory):
        raise ValueError("Strategy 45 cold lineage requires complete typed evidence")
    group, intent = state.group, source_intent.intent
    entry = (intent.action == "enter_long" and intent.reason == "strategy_forty_five_entry"
             and intent.protection_profile is not None
             and len(intent.protection_profile.slices) == 1
             and intent.protection_profile.slices[0].quantity_fraction == 1.
             and bool(intent.profit_target_price) and bool(intent.invalidation_price)
             and len(state.orders) == 3)
    exit_leg = (intent.action == "exit" and intent.reason in {"strategy_forty_five_session_liquidation", "strategy_forty_five_liquidity_exit"}
                and intent.protection_profile is None and intent.profit_target_price is None
                and intent.invalidation_price is None and len(state.orders) == 1)
    if exit_leg:
        # The normalized exit envelope names its original acquisition and its
        # producer cutoff fact. No inferred FIFO sale attribution is admitted.
        batch = source_intent.source_batch
        if batch is None or len(batch.events) != 1:
            raise ValueError("Strategy 45 cold liquidation lacks its original leg reference")
        event = batch.events[0]
        for field in ("correlation_id", "causation_id"):
            try:
                if str(UUID(event[field])) != event[field]:
                    raise ValueError
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("Strategy 45 cold liquidation lacks its original leg reference") from exc
    if (group.get("strategy_id") != STRATEGY_ID
            or type(group.get("strategy_revision")) is not int
            or group["strategy_revision"] != STRATEGY_NUMBER
            or group.get("run_id") != protection_history.run_id
            or group.get("batch_id") not in protection_history.committed_batch_ids
            or source_intent.batch_id not in protection_history.committed_batch_ids
            or not 0 < source_intent.sequence < state.sequence <= protection_history.through_sequence
            or state.intent_record_id != source_intent.record_id
            or group.get("account_id") != source_intent.account_id
            or group.get("strategy_intent_id") != intent.intent_id
            or intent.metadata or not (entry or exit_leg)
            or admission_reservation is None or admission_decision is None
            or float(admission_reservation.get("quantity", 0)) != intent.quantity
            or len({order.cOID for order in state.orders}) != len(state.orders)):
        raise ValueError("Strategy 45 cold lineage differs from its independent leg")
    account = group["account_id"]
    if any(order.acctId != account or order.ticker.upper() != intent.ticker
           or not order.cOID or order.raw or order.strategyParameters for order in state.orders):
        raise ValueError("Strategy 45 cold orders differ from their typed leg")
    approved, history = _approved_strategy_one_oms_intent(
        state, source_intent, protection_history, admission_reservation, admission_decision)
    if any(row.payload.get("action") == "replace_profit_target" for row in history):
        raise ValueError("Strategy 45 targets are frozen at entry")
    view = _ColdLineageView(group["group_id"], account, approved, state.orders,
        {str(row["broker_order_id"]): int(row["request_index"])
         for row in state.broker_bindings if row["request_index"] is not None},
        frozenset(str(row["broker_order_id"]) for row in state.broker_bindings if row["terminal"]))
    return tuple(replace(order, raw={
        "canonical_run_id": protection_history.run_id,
        "canonical_strategy_id": STRATEGY_ID,
        "canonical_strategy_revision": STRATEGY_NUMBER,
        "canonical_metadata": canonical_oms_order_metadata(view, order, {}),
    }) for order in state.orders)
