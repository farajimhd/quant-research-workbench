"""Exact acquisition clock from reconciled OMS entry-group executions.

This reducer supplies evidence only. The native caller must bind its broker/OMS
checkpoint and original entry intent before using or persisting the result.
It never substitutes submission, observed-held or retrospective episode clocks.
"""
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from math import isfinite
from zoneinfo import ZoneInfo

from .ibkr_schema import Execution
from .order_management import OrderGroupSnapshot


@dataclass(frozen=True, slots=True)
class NativeAcquisitionClock:
    account_id: str
    assignment_id: str
    ticker: str
    source_entry_intent_id: str
    source_group_id: str
    first_execution_id: str
    acquisition_day_us: int
    execution_ids: tuple[str, ...]


def acquisition_clock_from_entry_executions(group, executions, *,
        session_date, as_of, source_entry_intent_id, expected_conid):
    """Reduce one initial entry group, including partial fills, after reconciliation.

    Add groups do not reset acquisition. Independent brackets may have several
    buy roots. Sell protection fills are excluded; foreign or duplicated entry
    fills and incomplete cumulative entry quantities fail closed.
    """
    if (type(group) is not OrderGroupSnapshot or group.action != 'enter_long'
            or type(executions) is not tuple
            or any(type(e) is not Execution for e in executions)
            or type(session_date) is not date or type(as_of) is not datetime
            or as_of.tzinfo is None or as_of.utcoffset() is None
            or source_entry_intent_id != group.intent_id
            or type(expected_conid) is not int or expected_conid <= 0
            or not all(type(x) is str and x for x in (
                group.account_id, group.assignment_id, group.ticker,
                group.group_id, group.intent_id))
            or type(group.broker_order_ids) is not tuple or not group.broker_order_ids
            or any(type(x) is not str or not x for x in group.broker_order_ids)
            or len(set(group.broker_order_ids)) != len(group.broker_order_ids)
            or type(group.filled_quantity) not in (int, float)
            or not isfinite(group.filled_quantity) or group.filled_quantity <= 0):
        raise ValueError('Reconciled original entry group and aware decision clock required')
    selected = tuple(e for e in executions if e.account == group.account_id
                     and e.order_id in group.broker_order_ids)
    if any(e.side not in ('B', 'S') for e in selected):
        raise ValueError('Native broker execution side differs from B/S contract')
    fills = tuple(e for e in selected if e.side == 'B')
    if not fills or len({e.execution_id for e in fills}) != len(fills):
        raise ValueError('Unique complete entry execution identities required')
    zone = ZoneInfo('America/New_York')
    if as_of.astimezone(zone).date() != session_date:
        raise ValueError('Decision clock differs from source session')
    for e in fills:
        if (type(e.execution_id) is not str or not e.execution_id
                or e.symbol != group.ticker or type(e.conid) is not int or e.conid != expected_conid
                or type(e.trade_time) is not datetime or e.trade_time.tzinfo is None
                or e.trade_time.utcoffset() is None or e.trade_time > as_of
                or e.trade_time.astimezone(zone).date() != session_date
                or type(e.size) not in (int, float) or not isfinite(e.size) or e.size <= 0):
            raise ValueError('Entry execution is foreign, future or malformed')
    if sum((Decimal(str(e.size)) for e in fills), Decimal(0)) != Decimal(str(group.filled_quantity)):
        raise ValueError('Entry execution quantity differs from reconciled OMS group')
    first = min(fills, key=lambda e: (e.trade_time, e.execution_id))
    local = first.trade_time.astimezone(zone)
    clock = ((local.hour * 3600 + local.minute * 60 + local.second) * 1_000_000
             + local.microsecond)
    return NativeAcquisitionClock(group.account_id, group.assignment_id, group.ticker,
        group.intent_id, group.group_id, first.execution_id, clock,
        tuple(e.execution_id for e in sorted(fills, key=lambda e: (e.trade_time, e.execution_id))))
