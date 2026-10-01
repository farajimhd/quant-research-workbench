"""Staged exact-integer first-price companion; no table/writer admission."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import re
from uuid import UUID, NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from .arte_journal_schema import TableContract
from .strategy_initial_price_break import FirstSetupPriceBreakWitness, first_setup_price_break, PREMARKET_END_MS
from .strategy_initial_strong_momentum import (
    InitialMomentumSelectionWitness, validate_initial_momentum_selection,
    initial_strong_momentum_entry,
)
from .strategy_initial_momentum_growth import first_setup_momentum_growth_entry
from .strategy_rising_momentum_witness import RisingMomentumWitness

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


def _bind(current, selection, price, *, strategy_number=20):
    if type(strategy_number) is not int or strategy_number not in (20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31):
        raise ValueError('First price requires exact source policy number')
    if type(selection) is not InitialMomentumSelectionWitness:
        raise ValueError('First price requires typed original selection')
    validate_initial_momentum_selection(current, selection,
        episode_start_ms=selection.initial.episode_start_ms)
    first = selection.initial.first_setup
    from .strategy_initial_ten_percent import first_setup_ten_percent_entry
    first_rule = first_setup_ten_percent_entry if strategy_number in (26, 27, 28, 29, 30, 31) else first_setup_momentum_growth_entry
    if (not initial_strong_momentum_entry(current, selection.initial)
            or not first_rule(first)):
        raise ValueError('First price requires policy-matched first-setup momentum')
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
                              run_id, batch_id, parent_record_id, event_month,
                              strategy_number=20):
    if type(strategy_number) is not int or strategy_number not in (20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31):
        raise ValueError('First price requires an installed source-bound number')
    if not _bind(current, selection, price, strategy_number=strategy_number):
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
        'event_month': month.isoformat(), 'batch_id': batch_id, 'strategy_number': strategy_number,
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


def restore_first_price_entry(rows, current, selection, *, expected_price,
                              expected_price_source_token, strategy_number=None):
    """Restore only against independently supplied certified source evidence.

    Row-contained tokens cannot authenticate themselves. The caller must obtain
    expected values from the certified source plan or its verified source audit.
    """
    if selection.initial.first_setup.boundary_ms >= PREMARKET_END_MS:
        if rows:
            raise ValueError('After-hours cannot carry first-price companions')
        _bind(current, selection, None, strategy_number=20 if strategy_number is None else strategy_number)
        if expected_price is not None:
            raise ValueError('After-hours source cannot contain a price witness')
        return None
    if len(rows) != 1:
        raise ValueError('Premarket requires exactly one first-price companion')
    row = rows[0]
    if strategy_number is not None and row['strategy_number'] != strategy_number:
        raise ValueError('First price differs from expected numbered policy')
    if row['price_source_token'] != expected_price_source_token:
        raise ValueError('First price source seal differs from certified authority')
    flags = (row['current_price_valid'], row['prior_extremes_valid'])
    if any(type(v) is not int or v not in (0, 1) for v in flags):
        raise ValueError('First price flags must be exact binary integers')
    price = FirstSetupPriceBreakWitness(*(row[name] for name in (
        'ticker', 'first_setup_boundary_ms', 'source_build_id', 'bars_attempt_id',
        'market_plan_token', 'current_boundary_ms', 'prior_boundary_ms',
        'current_close_int', 'prior_high_int')), *(bool(v) for v in flags))
    projected = project_first_price_entry(current, selection, price,
        **{name: row[name] for name in ('price_source_token', 'run_id', 'batch_id',
                                       'parent_record_id', 'event_month', 'strategy_number')})[0]
    if any(type(row[name]) is not type(value) or row[name] != value for name, value in projected.items()):
        raise ValueError('First price row differs from original entry selection')
    if type(expected_price) is not FirstSetupPriceBreakWitness or price != expected_price:
        raise ValueError('First price values differ from certified source authority')
    return price


@dataclass(frozen=True, slots=True)
class FirstPriceEntryAuthority:
    """Per-entry evidence supplied by a separately certified compiler/audit."""
    parent_record_id: str
    current: RisingMomentumWitness
    selection: InitialMomentumSelectionWitness
    price: FirstSetupPriceBreakWitness | None
    price_source_token: str
    strategy_number: int = 20

    def __post_init__(self):
        parent = self.parent_record_id
        if type(parent) is not str or str(UUID(parent)) != parent or not UUID(parent).int:
            raise ValueError('First price authority needs canonical entry parent')
        if (type(self.price_source_token) is not str
                or not re.fullmatch('[0-9a-f]{64}', self.price_source_token)):
            raise ValueError('First price authority requires certified source token')
        _bind(self.current, self.selection, self.price, strategy_number=self.strategy_number)


def seal_first_price_rows(rows, entries, intents, events, authorities):
    """Bind complete Strategy20 entry graph to independent source receipts.

    The table must be registered by the journal integration before calling this
    sealer. This function neither registers it nor installs operational tables.
    """
    from .arte_journal_writer import typed_row
    required = {row['parent_record_id']: row for row in entries if row['strategy_number'] in (20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31)}
    parents = {row['record_id']: row for row in intents}
    source_events = {row['record_id']: row for row in events}
    if (len(required) != sum(row['strategy_number'] in (20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31) for row in entries)
            or len(parents) != len(intents) or len(source_events) != len(events)
            or any(type(authority) is not FirstPriceEntryAuthority for authority in authorities)):
        raise ValueError('First price graph has ambiguous parents or untyped authority')
    certified = {authority.parent_record_id: authority for authority in authorities}
    if len(certified) != len(authorities) or set(certified) != set(required):
        raise ValueError('First price graph lacks exact entry authority population')
    sealed = tuple(typed_row(FIRST_PRICE.name,
        {name: value for name, value in row.items() if name != 'content_hash'}) for row in rows)
    if (len({row['record_id'] for row in sealed}) != len(sealed)
            or any(row['parent_record_id'] not in required for row in sealed)
            or any('content_hash' in old and old['content_hash'] != new['content_hash']
                   for old, new in zip(rows, sealed))):
        raise ValueError('First price graph has extra, duplicate or changed companions')
    # Build the sparse parent index once; avoid scanning all rows for each entry.
    grouped = {}
    for row in sealed:
        grouped.setdefault(row['parent_record_id'], []).append(row)
    for parent, entry in required.items():
        authority = certified[parent]
        intent, event = parents.get(parent), source_events.get(parent)
        if (intent is None or event is None
                or intent['action'] != 'enter_long' or intent['reason'] != 'strategy_one_entry'
                or event['category'] != 'strategy' or event['entity_type'] != 'strategy_intent'
                or event['entity_id'] != intent['intent_id']
                or event['account_id'] != intent['account_id']
                or intent['ticker'] != authority.current.ticker
                or entry['boundary_ms'] != authority.current.boundary_ms
                or entry['episode_start_ms'] != authority.selection.initial.episode_start_ms
                or any(intent[name] != entry[name] or event[name] != entry[name]
                       for name in ('run_id', 'batch_id', 'event_month'))):
            raise ValueError('First price graph has unrelated entry/intent/event scope')
        if ((authority.strategy_number in (26, 27, 28, 29, 30, 31) or entry['strategy_number'] in (26, 27, 28, 29, 30, 31))
                and authority.strategy_number != entry['strategy_number']):
            raise ValueError('First price source authority differs from entry policy')
        selected = grouped.get(parent, ())
        if any(row['strategy_number'] != entry['strategy_number'] for row in selected):
            raise ValueError('First price companion differs from numbered entry identity')
        if any(row[name] != entry[name] for row in selected
               for name in ('run_id', 'batch_id', 'event_month')):
            raise ValueError('First price companion differs from entry run scope')
        restore_first_price_entry(selected, authority.current, authority.selection,
            expected_price=authority.price, expected_price_source_token=authority.price_source_token,
            strategy_number=entry['strategy_number'])
        at = datetime.fromisoformat(str(event['event_time']).replace('Z', '+00:00'))
        if at.tzinfo is None:
            raise ValueError('First price entry source clock must be timezone-aware')
        local = at.astimezone(ZoneInfo('America/New_York'))
        expected = datetime.combine(local.date(), datetime.min.time(), ZoneInfo('America/New_York'))
        expected += timedelta(hours=4, milliseconds=entry['boundary_ms'])
        identity = (f"strategy-{entry['strategy_number']}:{local.date().isoformat()}:{entry['assignment_id']}:"
                    f"{intent['account_id']}:{intent['ticker']}:{entry['boundary_ms']}:"
                    f"{entry['episode_start_ms']}")
        if at != expected or intent['intent_id'] != str(uuid5(NAMESPACE_URL, identity)):
            raise ValueError('First price parent differs from exact numbered source clock')
    return sealed
