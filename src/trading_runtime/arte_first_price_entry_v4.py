"""Staged exact-integer first-price companion; no table/writer admission."""
from datetime import date
import re
from uuid import UUID, NAMESPACE_URL, uuid5

from .arte_journal_schema import TableContract
from .strategy_initial_price_break import FirstSetupPriceBreakWitness, first_setup_price_break, PREMARKET_END_MS
from .strategy_initial_strong_momentum import (
    InitialMomentumSelectionWitness, validate_initial_momentum_selection,
    initial_strong_momentum_entry,
)
from .strategy_initial_momentum_growth import first_setup_momentum_growth_entry

FIRST_PRICE = TableContract('trading_first_price_entry_v4', (
    ('record_id', 'UUID'), ('parent_record_id', 'UUID'), ('run_id', 'String'),
    ('event_month', 'Date'), ('batch_id', 'UUID'), ('strategy_number', 'UInt32'),
    ('ticker', 'String'), ('boundary_ms', 'UInt32'), ('episode_start_ms', 'UInt32'),
    ('first_setup_boundary_ms', 'UInt32'), ('source_build_id', 'FixedString(64)'),
    ('bars_attempt_id', 'UUID'), ('market_plan_token', 'FixedString(64)'),
    ('candidate_plan_token', 'FixedString(64)'), ('entry_plan_token', 'FixedString(64)'),
    ('selection_token', 'FixedString(64)'), ('price_source_token', 'FixedString(64)'),
    ('current_boundary_ms', 'UInt32'), ('prior_boundary_ms', 'UInt32'),
    ('current_close_int', 'UInt64'), ('prior_high_int', 'UInt64'),
    ('current_price_valid', 'UInt8'), ('prior_extremes_valid', 'UInt8'),
    ('content_hash', 'FixedString(64)')), 'toYYYYMM(event_month)',
    'run_id,parent_record_id,record_id')


def _bind(current, selection, price):
    if type(selection) is not InitialMomentumSelectionWitness:
        raise ValueError('First price requires typed original selection')
    validate_initial_momentum_selection(current, selection,
        episode_start_ms=selection.initial.episode_start_ms)
    first = selection.initial.first_setup
    if (not initial_strong_momentum_entry(current, selection.initial)
            or not first_setup_momentum_growth_entry(first)):
        raise ValueError('First price requires parent19 first-setup momentum')
    if first.boundary_ms >= PREMARKET_END_MS:
        if price is not None:
            raise ValueError('After-hours cannot carry first-price companions')
        return False
    if (type(price) is not FirstSetupPriceBreakWitness
            or price.ticker != first.ticker
            or price.first_setup_boundary_ms != first.boundary_ms
            or price.source_build_id != first.source_build_id
            or price.market_plan_token != first.market_plan_token
            or not first_setup_price_break(price)):
        raise ValueError('First price differs from admitted momentum anchor')
    return True


def project_first_price_entry(current, selection, price, *, price_source_token,
                              run_id, batch_id, parent_record_id, event_month):
    if not _bind(current, selection, price):
        return ()
    if (type(price_source_token) is not str or not re.fullmatch('[0-9a-f]{64}', price_source_token)
            or type(run_id) is not str or not run_id):
        raise ValueError('First price requires exact source seal and run identity')
    for value in (batch_id, parent_record_id):
        if type(value) is not str or str(UUID(value)) != value or not UUID(value).int:
            raise ValueError('First price requires canonical nonzero parent UUIDs')
    month = date.fromisoformat(str(event_month))
    if month.day != 1:
        raise ValueError('First price event month is not a month boundary')
    return ({'record_id': str(uuid5(NAMESPACE_URL, f'{parent_record_id}:first-price:1000')),
        'parent_record_id': parent_record_id, 'run_id': run_id,
        'event_month': month.isoformat(), 'batch_id': batch_id, 'strategy_number': 20,
        'ticker': current.ticker, 'boundary_ms': current.boundary_ms,
        'episode_start_ms': selection.initial.episode_start_ms,
        **{name: getattr(price, name) for name in (
            'first_setup_boundary_ms', 'source_build_id', 'bars_attempt_id', 'market_plan_token',
            'current_boundary_ms', 'prior_boundary_ms', 'current_close_int', 'prior_high_int')},
        'current_price_valid': int(price.current_price_valid),
        'prior_extremes_valid': int(price.prior_extremes_valid),
        **{name: getattr(selection, name) for name in (
            'candidate_plan_token', 'entry_plan_token', 'selection_token')},
        'price_source_token': price_source_token},)


def restore_first_price_entry(rows, current, selection):
    if selection.initial.first_setup.boundary_ms >= PREMARKET_END_MS:
        if rows:
            raise ValueError('After-hours cannot carry first-price companions')
        _bind(current, selection, None)
        return None
    if len(rows) != 1:
        raise ValueError('Premarket requires exactly one first-price companion')
    row = rows[0]
    flags = (row['current_price_valid'], row['prior_extremes_valid'])
    if any(type(v) is not int or v not in (0, 1) for v in flags):
        raise ValueError('First price flags must be exact binary integers')
    price = FirstSetupPriceBreakWitness(*(row[name] for name in (
        'ticker', 'first_setup_boundary_ms', 'source_build_id', 'bars_attempt_id',
        'market_plan_token', 'current_boundary_ms', 'prior_boundary_ms',
        'current_close_int', 'prior_high_int')), *(bool(v) for v in flags))
    projected = project_first_price_entry(current, selection, price,
        **{name: row[name] for name in ('price_source_token', 'run_id', 'batch_id',
                                       'parent_record_id', 'event_month')})[0]
    if any(type(row[name]) is not type(value) or row[name] != value for name, value in projected.items()):
        raise ValueError('First price row differs from original entry selection')
    return price
