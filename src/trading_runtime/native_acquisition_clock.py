"""Exact acquisition clock from reconciled OMS entry-group executions.

This reducer supplies evidence only. The native caller must bind its broker/OMS
checkpoint and original entry intent before using or persisting the result.
It never substitutes submission, observed-held or retrospective episode clocks.
"""
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from math import isfinite
from zoneinfo import ZoneInfo

from .ibkr_schema import Execution
from .order_management import OrderGroupSnapshot


@dataclass(frozen=True, slots=True)
class NativeAcquisitionClock:
    session_date: date
    account_id: str
    assignment_id: str
    ticker: str
    source_entry_intent_id: str
    source_group_id: str
    first_execution_id: str
    acquisition_day_us: int
    execution_ids: tuple[str, ...]
    first_execution_time: datetime

    def __post_init__(self):
        identities = (self.account_id, self.assignment_id, self.ticker,
                      self.source_entry_intent_id, self.source_group_id, self.first_execution_id)
        if (type(self.session_date) is not date
                or any(type(x) is not str or not x for x in identities)
                or type(self.acquisition_day_us) is not int
                or not 0 <= self.acquisition_day_us < 86_400_000_000
                or type(self.execution_ids) is not tuple or not self.execution_ids
                or any(type(x) is not str or not x for x in self.execution_ids)
                or len(set(self.execution_ids)) != len(self.execution_ids)
                or self.execution_ids[0] != self.first_execution_id
                or type(self.first_execution_time) is not datetime
                or self.first_execution_time.tzinfo is None
                or self.first_execution_time.utcoffset() != timedelta(0)):
            raise ValueError('Exact source session, UTC execution and acquisition identities required')
        local = self.first_execution_time.astimezone(ZoneInfo('America/New_York'))
        clock = ((local.hour * 3600 + local.minute * 60 + local.second) * 1_000_000
                 + local.microsecond)
        if local.date() != self.session_date or clock != self.acquisition_day_us:
            raise ValueError('Acquisition day clock differs from its original UTC execution')


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
        utc = e.trade_time.astimezone(timezone.utc)
        delta = utc - datetime(1970, 1, 1, tzinfo=timezone.utc)
        epoch_us = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
        if type(e.trade_time_r) is not int or e.trade_time_r != epoch_us // 1000:
            raise ValueError('Entry execution millisecond identity differs from its UTC clock')
    if sum((Decimal(str(e.size)) for e in fills), Decimal(0)) != Decimal(str(group.filled_quantity)):
        raise ValueError('Entry execution quantity differs from reconciled OMS group')
    first = min(fills, key=lambda e: (e.trade_time, e.execution_id))
    local = first.trade_time.astimezone(zone)
    clock = ((local.hour * 3600 + local.minute * 60 + local.second) * 1_000_000
             + local.microsecond)
    return NativeAcquisitionClock(session_date, group.account_id, group.assignment_id, group.ticker,
        group.intent_id, group.group_id, first.execution_id, clock,
        tuple(e.execution_id for e in sorted(fills, key=lambda e: (e.trade_time, e.execution_id))),
        first.trade_time.astimezone(timezone.utc))


def acquisition_clock_payload(clock):
    """Canonical scalar projection; a payload is not checkpoint ownership proof."""
    if type(clock) is not NativeAcquisitionClock:
        raise ValueError('Exact native acquisition clock required')
    clock.__post_init__()
    return dict(session_date=clock.session_date.isoformat(), account_id=clock.account_id,
        assignment_id=clock.assignment_id, ticker=clock.ticker,
        source_entry_intent_id=clock.source_entry_intent_id, source_group_id=clock.source_group_id,
        first_execution_id=clock.first_execution_id, acquisition_day_us=clock.acquisition_day_us,
        execution_ids=list(clock.execution_ids),
        first_execution_time=clock.first_execution_time.isoformat(timespec='microseconds'))


def restore_acquisition_clock(payload):
    """Parse losslessly. Native recovery must separately reprove referenced fills."""
    required = {'session_date', 'account_id', 'assignment_id', 'ticker',
        'source_entry_intent_id', 'source_group_id', 'first_execution_id',
        'acquisition_day_us', 'execution_ids', 'first_execution_time'}
    if (type(payload) is not dict or set(payload) != required
            or type(payload['session_date']) is not str
            or type(payload['first_execution_time']) is not str
            or type(payload['execution_ids']) is not list):
        raise ValueError('Complete scalar acquisition projection required')
    clock = NativeAcquisitionClock(date.fromisoformat(payload['session_date']),
        payload['account_id'], payload['assignment_id'], payload['ticker'],
        payload['source_entry_intent_id'], payload['source_group_id'],
        payload['first_execution_id'], payload['acquisition_day_us'],
        tuple(payload['execution_ids']), datetime.fromisoformat(payload['first_execution_time']))
    if acquisition_clock_payload(clock) != payload:
        raise ValueError('Acquisition projection is not canonical or lossless')
    return clock


def require_acquisition_clock_matches_executions(clock, group, executions, **source):
    """Recompute against the original OMS/fill inputs; do not trust a saved clock."""
    if type(clock) is not NativeAcquisitionClock:
        raise ValueError('Exact restored acquisition clock required')
    clock.__post_init__()
    expected = acquisition_clock_from_entry_executions(group, executions, **source)
    if clock != expected:
        raise ValueError('Restored acquisition clock differs from original entry executions')
    return clock
