"""Normalized activity companion; source-certified commit admission is separate."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from uuid import UUID, NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from .arte_journal_schema import TableContract
from .strategy_entry_activity_witness import EntryActivityWitness, validate_entry_activity_witness
from .strategy_entry_activity_fade import AFTERHOURS_START_MS

_SOURCE_FIELDS = ('source_build_id', 'bars_attempt_id', 'market_plan_token',
                  'activity_source_token', 'candidate_plan_token', 'entry_plan_token',
                  'parent_selection_token')

ENTRY_ACTIVITY = TableContract('trading_entry_activity_v4', (
    ('record_id', 'UUID'), ('parent_record_id', 'UUID'), ('run_id', 'String'),
    ('event_month', 'Date'), ('batch_id', 'UUID'), ('strategy_number', 'UInt32'),
    ('ticker', 'String'), ('session_date', 'Date'), ('boundary_ms', 'UInt32'),
    ('source_build_id', 'FixedString(64)'), ('bars_attempt_id', 'UUID'),
    ('market_plan_token', 'FixedString(64)'), ('activity_source_token', 'FixedString(64)'),
    ('candidate_plan_token', 'FixedString(64)'), ('entry_plan_token', 'FixedString(64)'),
    ('parent_selection_token', 'FixedString(64)'), ('history_active', 'UInt8'),
    *((f'candle_{i}_{field}', dtype) for i in range(4) for field, dtype in
      (('boundary_ms', 'Nullable(UInt32)'), ('trade_count', 'Nullable(UInt64)'))),
    ('content_hash', 'FixedString(64)')), 'toYYYYMM(event_month)',
    'run_id,parent_record_id,record_id')


def activity_event_instant(witness):
    validate_entry_activity_witness(witness)
    start = datetime.fromisoformat(witness.session_date + 'T04:00:00').replace(
        tzinfo=ZoneInfo('America/New_York'))
    return (start + timedelta(milliseconds=witness.boundary_ms)).astimezone(timezone.utc)


def seal_certified_entry_activity_rows(rows, entries, intents, events, *, run_id, source=None):
    """Reconstruct authority from certified inputs before sealing stored evidence.

    Row-contained tokens and producer-supplied witnesses never authorize cold
    readback. Older numbered entries need no activity source or companions.
    """
    required = tuple(row for row in entries if row['strategy_number'] in (36, 37, 38, 39, 40))
    if not required:
        if rows:
            raise ValueError('Entry activity companions have no supported numbered parent')
        return ()
    from src.backend.backtest_strategy_entry_activity_source import EntryActivityReadbackAuthority
    from src.backend.backtest_strategy_episode_activity_source import EpisodeActivityReadbackAuthority
    source_types = {36: EntryActivityReadbackAuthority, 37: EpisodeActivityReadbackAuthority, 38: EpisodeActivityReadbackAuthority, 39: EpisodeActivityReadbackAuthority, 40: EpisodeActivityReadbackAuthority}
    if (len({row['strategy_number'] for row in required}) != 1
            or type(source) is not source_types[required[0]['strategy_number']]
            or source.run_id != run_id):
        raise ValueError('Entry activity requires independent certified run source')
    parents = {row['parent_record_id'] for row in required}
    selected_intents = tuple(row for row in intents if row['record_id'] in parents)
    authorities = source.resolve(run_id, required, selected_intents)
    return seal_entry_activity_rows(rows, required, selected_intents,
        tuple(row for row in events if row['record_id'] in parents), authorities)


def project_entry_activity(witness, *, run_id, batch_id, parent_record_id, event_month,
                           strategy_number=36):
    """Project independently supplied evidence; row-contained seals grant nothing."""
    validate_entry_activity_witness(witness)
    if type(strategy_number) is not int or strategy_number not in (36, 37, 38, 39, 40):
        raise ValueError('Entry activity projection requires exact supported strategy number')
    if type(run_id) is not str or not run_id:
        raise ValueError('Entry activity requires exact run identity')
    for value in (batch_id, parent_record_id):
        if type(value) is not str or str(UUID(value)) != value or not UUID(value).int:
            raise ValueError('Entry activity requires canonical nonzero parent UUIDs')
    expected_month = activity_event_instant(witness).date().replace(day=1).isoformat()
    if type(event_month) is not str or event_month != expected_month:
        raise ValueError('Entry activity month differs from exact UTC source clock')
    row = dict(record_id=str(uuid5(NAMESPACE_URL, f'{parent_record_id}:entry-activity:5000')),
               parent_record_id=parent_record_id, run_id=run_id, event_month=event_month,
               batch_id=batch_id, strategy_number=strategy_number, ticker=witness.ticker,
               session_date=witness.session_date, boundary_ms=witness.boundary_ms,
               history_active=int(witness.history_active))
    row.update({name: getattr(witness, name) for name in _SOURCE_FIELDS})
    for index, candle in enumerate(witness.candles):
        row[f'candle_{index}_boundary_ms'] = None if candle is None else candle.boundary_ms
        row[f'candle_{index}_trade_count'] = None if candle is None else candle.trade_count
    return row


def restore_entry_activity(rows, *, expected_witness, run_id, batch_id,
                           parent_record_id, event_month, strategy_number=36):
    """Compare one row against a separately reconstructed certified witness.

    The journal reader must verify its ordinary content hash before this source
    comparison. This function cannot authenticate row-contained source tokens.
    """
    expected = project_entry_activity(expected_witness, run_id=run_id, batch_id=batch_id,
                                      parent_record_id=parent_record_id, event_month=event_month,
                                      strategy_number=strategy_number)
    if len(rows) != 1:
        raise ValueError('Entry activity requires exactly one accepted-entry companion')
    row = rows[0]
    if (set(row) not in (set(expected), set(expected) | {'content_hash'})
            or any(type(row[name]) is not type(value) or row[name] != value
                   for name, value in expected.items())
            or ('content_hash' in row and (type(row['content_hash']) is not str
                or not re.fullmatch('[0-9a-f]{64}', row['content_hash'])))):
        raise ValueError('Entry activity companion differs from certified source evidence')
    return expected_witness


@dataclass(frozen=True, slots=True)
class EntryActivityAuthority:
    """Per-entry receipt supplied by an independently certified source reader."""
    parent_record_id: str
    witness: EntryActivityWitness
    episode_start_ms: int

    def __post_init__(self):
        parent = self.parent_record_id
        if type(parent) is not str or str(UUID(parent)) != parent or not UUID(parent).int:
            raise ValueError('Entry activity authority needs canonical entry parent')
        validate_entry_activity_witness(self.witness)
        opening = AFTERHOURS_START_MS if self.witness.boundary_ms >= AFTERHOURS_START_MS else 0
        if (type(self.episode_start_ms) is not int
                or not opening < self.episode_start_ms <= self.witness.boundary_ms
                or self.episode_start_ms % 100 != 0):
            raise ValueError('Entry activity authority lacks exact native episode clock')


def seal_entry_activity_rows(rows, entries, intents, events, authorities):
    """Seal a complete accepted-entry graph against independent source receipts.

    The journal integration must register the table before this can run. This
    function neither installs a table nor changes writer permissions.
    """
    from .arte_journal_writer import typed_row, _datetime_wire, _CONTRACTS
    if _CONTRACTS.get(ENTRY_ACTIVITY.name) != ENTRY_ACTIVITY:
        raise ValueError('Entry activity table contract is not registered by journal integration')
    required = {r['parent_record_id']: r for r in entries if r['strategy_number'] in (36, 37, 38, 39, 40)}
    parents = {r['record_id']: r for r in intents}
    source_events = {r['record_id']: r for r in events}
    if (len(required) != sum(r['strategy_number'] in (36, 37, 38, 39, 40) for r in entries)
            or len(parents) != len(intents) or len(source_events) != len(events)
            or any(type(a) is not EntryActivityAuthority for a in authorities)):
        raise ValueError('Entry activity graph has ambiguous parents or untyped authority')
    certified = {a.parent_record_id: a for a in authorities}
    if len(certified) != len(authorities) or set(certified) != set(required):
        raise ValueError('Entry activity graph lacks exact source authority population')
    sealed = tuple(typed_row(ENTRY_ACTIVITY.name, {k: v for k, v in row.items() if k != 'content_hash'})
                   for row in rows)
    grouped = {}
    for old, row in zip(rows, sealed):
        if (row['parent_record_id'] not in required
                or ('content_hash' in old and old['content_hash'] != row['content_hash'])):
            raise ValueError('Entry activity graph has extra or changed companions')
        grouped.setdefault(row['parent_record_id'], []).append(row)
    if len({r['record_id'] for r in sealed}) != len(sealed):
        raise ValueError('Entry activity graph has duplicate companions')
    for parent, entry in required.items():
        authority = certified[parent]
        witness = authority.witness
        intent, event = parents.get(parent), source_events.get(parent)
        if (type(entry['strategy_number']) is not int or intent is None or event is None
                or intent['action'] != 'enter_long' or intent['reason'] != 'strategy_one_entry'
                or event['category'] != 'strategy' or event['entity_type'] != 'strategy_intent'
                or event['entity_id'] != intent['intent_id'] or event['account_id'] != intent['account_id']
                or intent['ticker'] != witness.ticker or entry['boundary_ms'] != witness.boundary_ms
                or entry['episode_start_ms'] != authority.episode_start_ms
                or any(str(intent[name]) != str(entry[name]) or str(event[name]) != str(entry[name])
                       for name in ('run_id', 'batch_id', 'event_month'))):
            raise ValueError('Entry activity graph has unrelated entry, intent or event scope')
        identity = (f"strategy-{entry['strategy_number']}:{witness.session_date}:{entry['assignment_id']}:"
                    f"{intent['account_id']}:{witness.ticker}:{witness.boundary_ms}:{authority.episode_start_ms}")
        if intent['intent_id'] != str(uuid5(NAMESPACE_URL, identity)):
            raise ValueError('Entry activity graph differs from numbered intent identity')
        # Preserve DateTime64(9) precision; Python datetime alone would truncate
        # a nonzero nanosecond tail and could falsely match a completed clock.
        try:
            wire = _datetime_wire(event['event_time'], 9)
        except ValueError:
            wire = _datetime_wire(event['event_time'], 9, stored_utc=True)
        if wire != _datetime_wire(activity_event_instant(witness), 9):
            raise ValueError('Entry activity graph differs from native event clock')
        restore_entry_activity(grouped.get(parent, ()), expected_witness=witness,
                               run_id=entry['run_id'], batch_id=entry['batch_id'],
                               parent_record_id=parent, event_month=str(entry['event_month']),
                               strategy_number=entry['strategy_number'])
    return sealed
