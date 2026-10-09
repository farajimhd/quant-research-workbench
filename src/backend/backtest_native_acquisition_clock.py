"""Exact acquisition timing from normalized committed execution facts.

The caller supplies verified current OMS acquisition requests and a verified
prefix. This projection is evidence, not a position/admission capability.
"""
from dataclasses import dataclass
from collections.abc import Mapping
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from zoneinfo import ZoneInfo

from src.backend.backtest_v4_execution_restore import _pages
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.trading_runtime.arte_journal_writer import (
    load_committed_execution_page, _datetime_wire, _CONTRACTS,
)
from src.trading_runtime.ibkr_schema import OrderRequest


@dataclass(frozen=True, slots=True)
class NativeAcquisitionClock:
    run_id: str
    prefix_batch_id: str
    prefix_sequence: int
    account_id: str
    ticker: str
    conid: int
    client_order_ids: tuple[str, ...]
    execution_id: str
    execution_sequence: int
    execution_batch_id: str
    source_event_time: str
    acquisition_day_us: int


def _execution_time(value):
    """Normalize only the declared UTC column, rejecting precision loss."""
    schema = dict(_CONTRACTS['trading_execution_v1'].columns)
    if schema.get('source_event_time') != "DateTime64(9, 'UTC')" or type(value) is not str:
        raise ValueError('Exact normalized UTC execution column required')
    stored = re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{6}(?:\d{3})?', value) is not None
    wire = _datetime_wire(value, 6, stored_utc=stored)
    return datetime.fromisoformat(wire).replace(tzinfo=timezone.utc)


def _execution_conid(value):
    if dict(_CONTRACTS['trading_execution_v1'].columns).get('conid') != 'UInt64':
        raise ValueError('Exact normalized instrument column required')
    if type(value) is str and re.fullmatch(r'0|[1-9][0-9]{0,19}', value):
        value = int(value)
    if type(value) is not int or not 0 < value < 2**64:
        raise ValueError('Exact positive UInt64 instrument required')
    return value


def project_native_acquisition_clock(fills, prefix, requests, *,
        session_date, decision_at, max_fills, coid_by_broker_id):
    """Select the first positive fill of this acquisition, never another root.

    All supplied facts must belong to the committed prefix and decision clock.
    Replacement/lot roots must be supplied together by OMS. Future facts cause
    rejection rather than silently changing the causal prefix.
    """
    if (type(prefix) is not V4CommittedPrefix or not prefix.batch_ids or
            type(fills) is not tuple or type(max_fills) is not int or
            not 1 <= max_fills <= 100_000 or len(fills) > max_fills or
            type(requests) is not tuple or not 1 <= len(requests) <= max_fills or
            any(type(r) is not OrderRequest for r in requests) or
            not isinstance(coid_by_broker_id, Mapping) or
            type(prefix.last_sequence) is not int or prefix.last_sequence < 1 or
            prefix.last_batch_id not in prefix.batch_ids or
            type(session_date) is not date or type(decision_at) is not datetime or
            decision_at.tzinfo is None):
        raise ValueError('Bounded committed facts, exact requests and aware clock required')
    first = requests[0]
    identity = (first.acctId, first.ticker, first.conid)
    coids = tuple(r.cOID for r in requests)
    if (len(set(coids)) != len(coids) or any(not c for c in coids) or
            any((r.acctId, r.ticker, r.conid) != identity or r.side != 'BUY' or
                r.raw.get('canonical_run_id') != prefix.run_id for r in requests)):
        raise ValueError('Acquisition requests have foreign ownership or run')
    midnight = datetime.combine(session_date, datetime.min.time(), ZoneInfo('America/New_York'))
    midnight_utc = midnight.astimezone(timezone.utc)
    if decision_at.astimezone(midnight.tzinfo).date() != session_date:
        raise ValueError('Acquisition decision is outside declared source date')
    seen = set()
    selected = []
    prior_sequence = 0
    for fill in fills:
        sequence = fill['sequence']
        execution_id = fill['execution_id']
        if (type(sequence) is not int or not prior_sequence < sequence <= prefix.last_sequence or
                fill['run_id'] != prefix.run_id or fill['batch_id'] not in prefix.batch_ids or
                type(execution_id) is not str or not execution_id or execution_id in seen):
            raise ValueError('Execution facts differ from exact committed prefix')
        prior_sequence = sequence
        seen.add(execution_id)
        at = _execution_time(fill['source_event_time'])
        if at > decision_at:
            raise ValueError('Execution timestamp is naive or future')
        if fill['client_order_id'] not in coids:
            continue
        if (fill['account_id'], fill['ticker'], _execution_conid(fill['conid'])) != identity or fill['side'] != 'B':
            raise ValueError('Acquisition execution differs from exact OMS request')
        if coid_by_broker_id.get(fill['broker_order_id']) != fill['client_order_id']:
            raise ValueError('Acquisition execution lacks exact broker order ownership')
        if type(fill['quantity']) not in (str, Decimal):
            raise ValueError('Acquisition requires an exact normalized quantity')
        try:
            quantity = Decimal(fill['quantity'])
        except (InvalidOperation, TypeError):
            raise ValueError('Acquisition quantity is invalid') from None
        if not quantity.is_finite() or quantity <= 0:
            raise ValueError('Acquisition requires a positive finite execution')
        if at.astimezone(midnight.tzinfo).date() != session_date:
            raise ValueError('Acquisition execution is outside declared source date')
        delta = at.astimezone(timezone.utc) - midnight_utc
        day_us = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
        selected.append((day_us, sequence, fill, at))
    if not selected:
        raise ValueError('Current acquisition has no committed positive fill')
    day_us, sequence, fill, at = min(selected, key=lambda item: item[:2])
    return NativeAcquisitionClock(prefix.run_id, prefix.last_batch_id, prefix.last_sequence,
        *identity, coids, fill['execution_id'], sequence, fill['batch_id'],
        at.astimezone(timezone.utc).isoformat(), day_us)


def load_native_acquisition_clock(client, prefix, requests, *, session_date,
        decision_at, max_fills, coid_by_broker_id):
    """Read bounded committed normalized pages; never query raw history."""
    if type(max_fills) is not int or not 1 <= max_fills <= 100_000:
        raise ValueError('Explicit acquisition read bound required')
    fills = _pages(load_committed_execution_page, client, prefix, maximum=max_fills)
    return project_native_acquisition_clock(fills, prefix, requests,
        session_date=session_date, decision_at=decision_at, max_fills=max_fills,
        coid_by_broker_id=coid_by_broker_id)
