"""Normalized first-setup observations and source-plan receipts for Strategy18/19.

These rows preserve the compiler-selected anchor and its three plan seals.
Scalar restoration verifies sources and eligibility; it cannot independently
prove initiality without the sealed candidate/compiler selection authority.
"""
from datetime import date, datetime, timedelta, timezone
import struct
from uuid import UUID, NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from .arte_journal_schema import TableContract
from .arte_rising_momentum_entry_v4 import VALUES, restore_rising_momentum
from .strategy_rising_momentum_witness import (
    CompletedMomentumObservation, RisingMomentumWitness, validate_momentum_witness,
)
from .strategy_one_stateful import StrategyOneEntryProposal
from .strategy_initial_strong_momentum import (
    InitialStrongMomentumWitness, InitialMomentumSelectionWitness,
    initial_strong_momentum_entry, validate_initial_momentum_selection,
)

INITIAL_MOMENTUM = TableContract('trading_initial_momentum_entry_v4', (
    ('record_id', 'UUID'), ('parent_record_id', 'UUID'), ('run_id', 'String'),
    ('event_month', 'Date'), ('batch_id', 'UUID'), ('strategy_number', 'UInt32'),
    ('ticker', 'String'), ('boundary_ms', 'UInt32'), ('episode_start_ms', 'UInt32'),
    ('first_setup_boundary_ms', 'UInt32'), ('resolution_ms', 'UInt32'),
    ('source_build_id', 'FixedString(64)'), ('source_attempt_id', 'UUID'),
    ('market_plan_token', 'FixedString(64)'), ('candidate_plan_token', 'FixedString(64)'),
    ('entry_plan_token', 'FixedString(64)'), ('selection_token', 'FixedString(64)'),
    ('current_boundary_ms', 'UInt32'), ('prior_boundary_ms', 'UInt32'),
    *((name, 'Nullable(Float64)') for name in VALUES),
    ('content_hash', 'FixedString(64)')), 'toYYYYMM(event_month)',
    'run_id, parent_record_id, resolution_ms, record_id')
TABLES = (INITIAL_MOMENTUM,)


def initial_momentum_select_columns() -> str:
    """Read exact IEEE bits alongside nullable JSON scalars."""
    return ','.join(name for name, _ in INITIAL_MOMENTUM.columns) + ',' + ','.join(
        f'if(isNull({name}),NULL,reinterpretAsUInt64(assumeNotNull({name}))) AS {name}_bits'
        for name in VALUES)


def decode_initial_momentum_row(row):
    result = dict(row)
    for name in VALUES:
        bits = result.pop(name + '_bits')
        if (bits is None) != (result[name] is None):
            raise ValueError('Initial momentum null scalar differs from bit projection')
        if bits is not None and (type(bits) is not int or not 0 <= bits < 2**64):
            raise ValueError('Initial momentum Float64 bit projection is malformed')
        result[name] = None if bits is None else struct.unpack('>d', bits.to_bytes(8, 'big'))[0]
    return result


def _selection(current, selection, strategy_number=18):
    if type(strategy_number) is not int or strategy_number not in (18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30):
        raise ValueError("Initial momentum strategy number differs")
    if type(selection) is not InitialMomentumSelectionWitness:
        raise ValueError('Initial momentum requires exact typed selection plan seals')
    validate_initial_momentum_selection(current, selection,
        episode_start_ms=getattr(selection.initial, 'episode_start_ms', None))
    if not initial_strong_momentum_entry(current, selection.initial):
        raise ValueError('Initial momentum requires strong first and current setups')
    if strategy_number in (19, 20, 21, 22, 23, 24, 25):
        from .strategy_initial_momentum_growth import first_setup_momentum_growth_entry
        if not first_setup_momentum_growth_entry(selection.initial.first_setup):
            raise ValueError('Strategy 19 requires premarket first-setup 50pct growth')
    elif strategy_number in (26, 27, 28, 29, 30):
        from .strategy_initial_ten_percent import first_setup_ten_percent_entry
        if not first_setup_ten_percent_entry(selection.initial.first_setup):
            raise ValueError('Strategy 26 requires first-setup strict 10pct growth')


def project_initial_momentum_entry(proposal, initial, *, run_id, batch_id,
                                   parent_record_id, event_month):
    if type(proposal) is not StrategyOneEntryProposal:
        raise ValueError('Initial momentum requires exact typed entry proposal')
    if type(proposal.strategy_number) is not int or proposal.strategy_number not in (18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30):
        if initial is not None:
            raise ValueError('Old entry cannot carry initial momentum companions')
        return ()
    _selection(proposal.momentum, initial, proposal.strategy_number)
    if proposal.strategy_number in (19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30) and initial != proposal.initial_momentum:
        raise ValueError('Strategy 19 initial momentum differs from original selection tokens')
    anchor = initial.initial
    first = anchor.first_setup
    if (proposal.ticker != proposal.momentum.ticker
            or proposal.boundary_ms != proposal.momentum.boundary_ms
            or proposal.episode_start_ms != anchor.episode_start_ms
            or not isinstance(run_id, str) or not run_id):
        raise ValueError('Initial momentum differs from original entry proposal')
    parent, batch = str(UUID(parent_record_id)), str(UUID(batch_id))
    month = date.fromisoformat(str(event_month))
    if month.day != 1:
        raise ValueError('Initial momentum event month is not a month boundary')
    return tuple({
        'record_id': str(uuid5(NAMESPACE_URL, f'{parent}:initial-momentum:{observation.resolution_ms}')),
        'parent_record_id': parent, 'run_id': run_id, 'event_month': month.isoformat(),
        'batch_id': batch, 'strategy_number': proposal.strategy_number, 'ticker': proposal.ticker,
        'boundary_ms': proposal.boundary_ms, 'episode_start_ms': anchor.episode_start_ms,
        'first_setup_boundary_ms': first.boundary_ms, 'resolution_ms': observation.resolution_ms,
        'source_build_id': first.source_build_id, 'source_attempt_id': first.source_attempt_id,
        'market_plan_token': first.market_plan_token,
        **{name: getattr(initial, name) for name in
           ('candidate_plan_token', 'entry_plan_token', 'selection_token')},
        'current_boundary_ms': observation.current_boundary_ms,
        'prior_boundary_ms': observation.prior_boundary_ms,
        **{name: getattr(observation, name) for name in VALUES},
    } for observation in first.observations)


def restore_initial_momentum(rows, *, ticker, boundary_ms, episode_start_ms, current_momentum, strategy_number=None):
    validate_momentum_witness(current_momentum)
    if len(rows) != 2 or {row['resolution_ms'] for row in rows} != {1000, 10000}:
        raise ValueError('Initial momentum requires exact two observations')
    ordered = sorted(rows, key=lambda row: row['resolution_ms'])
    first = ordered[0]
    if strategy_number is None:
        strategy_number = first['strategy_number']
    if type(strategy_number) is not int or strategy_number not in (18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30):
        raise ValueError('Initial momentum strategy number differs')
    identity = ('parent_record_id', 'run_id', 'event_month', 'batch_id', 'strategy_number',
                'ticker', 'boundary_ms', 'episode_start_ms', 'first_setup_boundary_ms',
                'source_build_id', 'source_attempt_id', 'market_plan_token',
                'candidate_plan_token', 'entry_plan_token', 'selection_token')
    if (type(first['strategy_number']) is not int or first['strategy_number'] != strategy_number
            or first['ticker'] != ticker or first['boundary_ms'] != boundary_ms
            or first['episode_start_ms'] != episode_start_ms
            or current_momentum.ticker != ticker or current_momentum.boundary_ms != boundary_ms
            or any(any(row[name] != first[name] for name in identity) for row in ordered)
            or any(row['record_id'] != str(uuid5(NAMESPACE_URL,
                f"{row['parent_record_id']}:initial-momentum:{row['resolution_ms']}")) for row in ordered)):
        raise ValueError('Initial momentum identity, parent or entry clocks differ')
    for name in ('parent_record_id', 'batch_id'):
        if str(UUID(first[name])) != first[name] or not UUID(first[name]).int:
            raise ValueError('Initial momentum parent identity is not canonical UUID')
    witness = RisingMomentumWitness(ticker, first['first_setup_boundary_ms'],
        first['source_build_id'], first['source_attempt_id'], first['market_plan_token'],
        tuple(CompletedMomentumObservation(row['resolution_ms'], row['current_boundary_ms'],
            row['prior_boundary_ms'], *(row[name] for name in VALUES)) for row in ordered))
    selection = InitialMomentumSelectionWitness(
        InitialStrongMomentumWitness(episode_start_ms, witness),
        first['candidate_plan_token'], first['entry_plan_token'], first['selection_token'])
    _selection(current_momentum, selection, strategy_number)
    return selection


def seal_initial_momentum_rows(rows, entries, intents, events, current_momentum_rows):
    """Bind exact18 child pairs to entry/intent/source clocks before commitment."""
    from .arte_journal_writer import typed_row
    sealed = tuple(typed_row(INITIAL_MOMENTUM.name,
        {key: value for key, value in row.items() if key != 'content_hash'}) for row in rows)
    if any('content_hash' in source and source['content_hash'] != row['content_hash']
           for source, row in zip(rows, sealed)):
        raise ValueError('Initial momentum scalar content seal changed')
    eligible_entries = [row for row in entries if row['strategy_number'] in (18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30)]
    required = {row['parent_record_id']: row for row in eligible_entries}
    parents = {row['record_id']: row for row in intents if row['reason'] == 'strategy_one_entry'}
    source_events = {row['record_id']: row for row in events}
    if (len(required) != len(eligible_entries)
            or len({row['record_id'] for row in intents}) != len(intents)
            or len(source_events) != len(events)
            or len({row['record_id'] for row in sealed}) != len(sealed)
            or len(sealed) != 2 * len(required)
            or any(row['parent_record_id'] not in required or row['strategy_number'] not in (18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30) for row in sealed)
            or any(row['strategy_number'] in (18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30) and row['parent_record_id'] not in required
                   for row in current_momentum_rows)):
        raise ValueError('Initial momentum has missing, extra, duplicate or old entry companions')
    for parent, entry in required.items():
        selected = tuple(row for row in sealed if row['parent_record_id'] == parent)
        current_rows = tuple(row for row in current_momentum_rows if row['parent_record_id'] == parent)
        intent = parents.get(parent)
        event = source_events.get(parent)
        if (intent is None or event is None or intent['action'] != 'enter_long'
                or event['category'] != 'strategy' or event['entity_type'] != 'strategy_intent'
                or event['entity_id'] != intent['intent_id'] or event['account_id'] != intent['account_id']
                or any(row['strategy_number'] != entry['strategy_number'] or row['run_id'] != entry['run_id']
                       or row['batch_id'] != entry['batch_id'] or row['event_month'] != entry['event_month']
                       for row in (*selected, *current_rows))
                or any(intent[name] != entry[name] or event[name] != entry[name]
                       for name in ('run_id', 'batch_id', 'event_month'))):
            raise ValueError('Initial momentum has unrelated typed parent/source scope')
        current = restore_rising_momentum(current_rows, ticker=intent['ticker'],
                                          boundary_ms=entry['boundary_ms'])
        restore_initial_momentum(selected, ticker=intent['ticker'], boundary_ms=entry['boundary_ms'],
                                 episode_start_ms=entry['episode_start_ms'], current_momentum=current,
                                 strategy_number=entry['strategy_number'])
        at = datetime.fromisoformat(str(event['event_time']).replace('Z', '+00:00'))
        at = at if at.tzinfo is not None else at.replace(tzinfo=timezone.utc)
        local = at.astimezone(ZoneInfo('America/New_York'))
        expected_at = datetime.combine(local.date(), datetime.min.time(), ZoneInfo('America/New_York')) + timedelta(hours=4, milliseconds=entry['boundary_ms'])
        identity = (f"strategy-{entry['strategy_number']}:{local.date().isoformat()}:{entry['assignment_id']}:"
                    f"{intent['account_id']}:{intent['ticker']}:{entry['boundary_ms']}:"
                    f"{entry['episode_start_ms']}")
        if at != expected_at or intent['intent_id'] != str(uuid5(NAMESPACE_URL, identity)):
            raise ValueError('Initial momentum parent is not its exact numbered entry source clock')
    return sealed
