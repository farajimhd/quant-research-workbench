"""Causal assignment projection of the existing OMS's processed order facts.

This is a read of owned OMS state, not a broker query or order planner. Requested
prices are never substituted for the protection prices actually observed by OMS.
No position, program feature, journal table or policy target is created here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite

from .order_management import OrderManagementEngine, TERMINAL_MANAGEMENT_STATES


@dataclass(frozen=True)
class ObservedWorkingOrder:
    broker_order_id: str
    role: str
    remaining_quantity: float
    limit_price: float
    stop_price: float
    status: str


@dataclass(frozen=True)
class AssignmentOrderObservation:
    account_id: str
    assignment_id: str
    ticker: str
    observed_at: datetime
    pending_acquisition: bool
    pending_reduction: bool
    unobserved_order_ids: tuple[str, ...]
    working_orders: tuple[ObservedWorkingOrder, ...]
    high_water_price: float


def observe_assignment_orders(manager: OrderManagementEngine, *, account_id: str,
        assignment_id: str, ticker: str, at: datetime) -> AssignmentOrderObservation:
    """Project one exact assignment using the authoritative OMS reference index.

    Only processed fingerprints contribute prices and quantities. Both legacy
    LiveOrder and canonical OrderState fingerprints have explicit codecs; other
    shapes fail rather than borrowing requested or latest broker state.
    """
    if (type(manager) is not OrderManagementEngine or not account_id or not assignment_id
            or not ticker or type(at) is not datetime or at.tzinfo is None or at.utcoffset() is None):
        raise ValueError("OMS observation requires exact assignment and completed clock")
    market = manager.execution_market_data.snapshot(ticker)
    if market is not None and market.observed_at > at:
        raise ValueError("OMS assignment observation cannot see a future market snapshot")
    pending_entry = pending_exit = False
    missing, working = [], []
    high_water = 0.
    for group in manager._groups_by_assignment.get((account_id, assignment_id), ()):
        if (group.account_id != account_id or group.intent.ticker != ticker
                or group.intent.metadata.get("assignment_id") != assignment_id):
            raise ValueError("OMS assignment index contains conflicting lineage")
        if group.created_at > at or group.updated_at > at:
            raise ValueError("OMS assignment observation cannot see future order state")
        action = str(group.intent.action)
        entry = action in {"enter_long", "add_long", "enter_short", "add_short"}
        reduction = action in {"exit", "cover", "reduce_long", "reduce_short", "take_profit"}
        unresolved = group.state not in TERMINAL_MANAGEMENT_STATES and not group.broker_order_ids
        pending_entry |= entry and unresolved
        pending_exit |= reduction and unresolved
        active_group = False
        for broker_id in group.broker_order_ids:
            if broker_id in group.terminal_broker_order_ids:
                continue
            role = group.broker_order_roles.get(broker_id)
            if role not in {"entry", "managed_exit", "profit_target", "protective_stop", "trailing_stop", "protective_exit"}:
                raise ValueError("OMS observed order lacks exact role lineage")
            fingerprint = group.broker_order_state_fingerprints.get(broker_id)
            if fingerprint is None:
                missing.append(broker_id)
                active_group = True
                pending_entry |= entry and role == "entry"
                pending_exit |= reduction and role == "managed_exit"
                continue
            if len(fingerprint) == 7:
                status, _, remaining, _, limit, stop, _ = fingerprint
            elif len(fingerprint) == 10:
                status, _, _, remaining, _, limit, stop, _, _, _ = fingerprint
            else:
                raise ValueError("OMS observed order fingerprint has an unknown codec")
            if (type(status) is not str or any(type(v) not in (int, float)
                    or not isfinite(v) or v < 0 for v in (remaining, limit, stop))):
                raise ValueError("OMS observed order quantities or prices are invalid")
            if remaining <= 0:
                continue
            active_group = True
            pending_entry |= entry and role == "entry"
            pending_exit |= reduction and role == "managed_exit"
            working.append(ObservedWorkingOrder(broker_id, role, float(remaining),
                float(limit), float(stop), status))
        if active_group and entry:
            if not isfinite(group.high_water_price) or group.high_water_price < 0:
                raise ValueError("OMS observed high-water price is invalid")
            high_water = max(high_water, group.high_water_price)
    return AssignmentOrderObservation(account_id, assignment_id, ticker, at,
        pending_entry, pending_exit, tuple(sorted(missing)),
        tuple(sorted(working, key=lambda row: row.broker_order_id)), high_water)
