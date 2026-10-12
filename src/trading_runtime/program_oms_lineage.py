"""Program order lineage on the shared normalized OMS recovery contracts."""
from dataclasses import replace
from math import isfinite

from .arte_intent_projection import RecoveredIntent
from .arte_journal_reader import CompleteProtectionHistory
from .arte_oms_projection import (
    RecoveredOmsGroupState, RecoveredStrategyOneOmsLineage,
    _ColdLineageView, canonical_oms_order_metadata,
)
from .program_oms_admission import restore_program_admitted_intent


def reconstruct_program_oms_lineage(state, source, protection, *, release,
        proposal, assignment_id, reservation, decision,
        execution_policy, protection_profile, outside_rth):
    """Rebuild raw attribution from proved semantics, never stored JSON.

    The proposal must be reselected from the completed historical program
    boundary before calling. Shared readers must already verify committed
    reservation/decision rows. This pure projection grants no run admission.
    """
    if (type(state) is not RecoveredOmsGroupState or type(source) is not RecoveredIntent
            or type(protection) is not CompleteProtectionHistory):
        raise ValueError("Program OMS lineage requires typed committed facts")
    group = state.group
    if (group.get("run_id") != protection.run_id
            or group.get("strategy_id") != release.header["strategy_id"]
            or group.get("strategy_revision") != release.header["strategy_number"]
            or group.get("batch_id") not in protection.committed_batch_ids
            or source.batch_id not in protection.committed_batch_ids
            or not 0 < source.sequence < state.sequence <= protection.through_sequence
            or state.intent_record_id != source.record_id
            or group.get("account_id") != source.account_id
            or group.get("strategy_intent_id") != source.intent.intent_id
            or not state.orders or len(state.orders) > 65535
            or len({row.cOID for row in state.orders}) != len(state.orders)
            or any(not row.cOID or row.raw or row.strategyParameters
                   or row.acctId != source.account_id or row.ticker != source.intent.ticker
                   for row in state.orders)):
        raise ValueError("Program OMS lineage scope or flat orders differ")
    approved = restore_program_admitted_intent(release, proposal, source,
        run_id=protection.run_id, account_id=source.account_id, assignment_id=assignment_id,
        reservation=reservation, decision=decision, execution_policy=execution_policy,
        protection_profile=protection_profile, outside_rth=outside_rth)
    history = tuple(row for row in protection.records
        if row.payload.get("order_group_id") == group["group_id"] and row.sequence <= state.sequence)
    if any(row.run_id != protection.run_id or row.account_id != source.account_id
           or not source.sequence < row.sequence <= protection.through_sequence
           or (row.category, row.entity_type) != ("protection", "protection_change")
           or row.payload.get("strategy_id") != group["strategy_id"]
           or row.payload.get("strategy_revision") != group["strategy_revision"]
           or row.payload.get("ticker") != source.intent.ticker for row in history):
        raise ValueError("Program OMS protection lineage differs")
    # Program target reductions use managed exits. Only fixed protective stops
    # can amend acquisition groups under the approved stop-only contract.
    effective = [row for row in history if row.payload.get("phase") == "effective"
        and row.payload.get("action") in {"replace_protective_stop", "replace_profit_target"}]
    if any(row.payload.get("kind") != "stop"
           or row.payload.get("action") != "replace_protective_stop" for row in effective):
        raise ValueError("Program OMS stop-only policy has a target amendment")
    if effective:
        price = max(effective, key=lambda row: row.sequence).payload["price"]
        profile = approved.protection_profile
        if profile is None or not isfinite(price) or price <= 0:
            raise ValueError("Program OMS amended stop lacks its acquisition profile")
        approved = replace(approved, invalidation_price=price,
            protection_profile=replace(profile, slices=tuple(
                replace(item, stop=replace(item.stop, price=price)) for item in profile.slices)),
            metadata={**approved.metadata, "confirmed_support_stop": price})
    bindings = {str(row["broker_order_id"]): int(row["request_index"])
        for row in state.broker_bindings if row["request_index"] is not None}
    view = _ColdLineageView(group["group_id"], source.account_id, approved,
        state.orders, bindings, frozenset(str(row["broker_order_id"])
            for row in state.broker_bindings if row["terminal"]))
    orders = tuple(replace(row, raw=dict(canonical_run_id=protection.run_id,
        canonical_strategy_id=group["strategy_id"],
        canonical_strategy_revision=group["strategy_revision"],
        canonical_metadata=canonical_oms_order_metadata(view, row))) for row in state.orders)
    return RecoveredStrategyOneOmsLineage(state, source, orders,
        protection.through_sequence, approved, reservation)
