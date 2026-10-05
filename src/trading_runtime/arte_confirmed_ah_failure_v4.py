"""Prepared scalar contract only: no table installation or writer registration.

Projection/restoration do not attest market provenance, committed ancestry or
current Portfolio/OMS state. Native admission must supply those checks before
this family can become an executable Strategy 34 exit.
"""
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from types import MappingProxyType
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

from .arte_journal_schema import TableContract
from .strategy_confirmed_ah_risk_failure import ConfirmedAhRiskFailure
from .strategy_confirmed_ah_failure_exit import (
    confirmed_ah_exit_intent, validate_confirmed_ah_witness,
)
from .strategy_followthrough_failure import FollowThroughFailure

CONFIRMED_AH_FAILURE = TableContract('trading_confirmed_ah_failure_v4', (
    ('record_id', 'UUID'), ('parent_record_id', 'UUID'), ('run_id', 'String'),
    ('event_month', 'Date'), ('batch_id', 'UUID'), ('strategy_number', 'UInt32'),
    ('source_entry_intent_id', 'UUID'), ('assignment_id', 'String'),
    ('boundary_ms', 'UInt32'), ('first_held_boundary_ms', 'UInt32'),
    ('reference_ask', 'Decimal(38, 18)'), ('initial_stop', 'Decimal(38, 18)'),
    ('completed_close_int', 'UInt64'), ('macd_line', 'Float64'), ('macd_signal', 'Float64'),
    ('bid', 'Decimal(38, 18)'), ('ask', 'Decimal(38, 18)'), ('quote_age_us', 'UInt64'),
    ('completed_ten_second_boundary_ms', 'UInt32'),
    ('ten_second_macd_line', 'Float64'), ('ten_second_macd_signal', 'Float64'),
    ('content_hash', 'FixedString(64)'),
), 'toYYYYMM(event_month)', 'run_id, parent_record_id, record_id')
TABLES = (CONFIRMED_AH_FAILURE,)


@dataclass(frozen=True, slots=True)
class V4ConfirmedAhFailureBatch:
    """Prepared immutable one-intent transport, not admitted by a writer.

    Original entry/prefix and producer provenance checks remain separate;
    an envelope must never stand in for those checks at commit admission.
    """
    base: object
    confirmation: object

    def __post_init__(self):
        from .arte_journal_writer import TypedJournalBatch, _canonical_typed_content
        from .arte_intent_projection import project_strategy_intent
        from .strategy_one_stateful import StrategyOneFinancialView
        from .strategy_engine import AssignmentStatus, StrategyPermissions
        if (type(self.base) is not TypedJournalBatch or self.base.status != 'running'
                or len(self.base.events) != 1 or len(self.base.intents) != 1
                or self.base.first_sequence != self.base.last_sequence):
            raise ValueError('AH batch requires one exact running typed intent')
        row = dict(self.confirmation)
        if set(row) != {name for name, _ in CONFIRMED_AH_FAILURE.columns} - {'content_hash'}:
            raise ValueError('AH batch requires its complete unsealed scalar witness')
        witness = restore_confirmed_ah_failure(row)
        event, parent = self.base.events[0], self.base.intents[0]
        if (row['run_id'] != self.base.run_id or row['batch_id'] != self.base.batch_id
                or row['parent_record_id'] != event['record_id']
                or parent['record_id'] != event['record_id']
                or parent['run_id'] != row['run_id'] or event['run_id'] != row['run_id']
                or parent['batch_id'] != row['batch_id'] or event['batch_id'] != row['batch_id']
                or parent['account_id'] != event['account_id']
                or parent['event_month'] != row['event_month']
                or event['category'] != 'strategy' or event['entity_type'] != 'strategy_intent'
                or parent['intent_id'] != event['entity_id']
                or event['sequence'] != self.base.first_sequence):
            raise ValueError('AH batch differs from its event/intent envelope')
        at = datetime.fromisoformat(str(event['event_time']).replace('Z', '+00:00'))
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        local = at.astimezone(ZoneInfo('America/New_York'))
        financial = StrategyOneFinancialView(
            row['assignment_id'], event['account_id'], parent['ticker'],
            AssignmentStatus.WATCHING, StrategyPermissions(),
            float(parent['quantity']), False, False, False, 1,
        )
        expected = confirmed_ah_exit_intent(
            witness, financial, session_date=local.date(),
            source_entry_intent_id=str(row['source_entry_intent_id']), strategy_number=row['strategy_number'],
        )
        if expected.event_time != at.astimezone(timezone.utc):
            raise ValueError('AH batch event clock differs from its witness')
        expected_row = project_confirmed_ah_failure(
            witness, expected, financial, session_date=local.date(),
            source_entry_intent_id=str(row['source_entry_intent_id']),
            run_id=row['run_id'], batch_id=row['batch_id'], parent_record_id=row['parent_record_id'],
            strategy_number=row['strategy_number'],
        )
        if row != expected_row:
            raise ValueError('AH batch has altered scalar identity or values')
        content = {k: v for k, v in parent.items() if k != 'content_hash'}
        expected_content = {**content, **{k: v for k, v in project_strategy_intent(expected).core.items()
                                         if k != 'event_time'}}
        if (_canonical_typed_content('trading_strategy_intent_v1', content, stored_utc=True)
                != _canonical_typed_content('trading_strategy_intent_v1', expected_content, stored_utc=True)):
            raise ValueError('AH batch differs from its exact exit factory')
        object.__setattr__(self, 'confirmation', MappingProxyType(row))


def project_confirmed_ah_failure(
    witness, intent, financial, *, session_date, source_entry_intent_id,
    run_id, batch_id, parent_record_id, strategy_number=34,
):
    """Exact unsealed factory projection; no committed-source claim."""
    expected = confirmed_ah_exit_intent(
        witness, financial, session_date=session_date,
        source_entry_intent_id=source_entry_intent_id, strategy_number=strategy_number,
    )
    if intent != expected or type(run_id) is not str or not run_id:
        raise ValueError('AH witness differs from its exact factory intent')
    for value in (batch_id, parent_record_id, source_entry_intent_id):
        UUID(value)
    return dict(
        record_id=str(uuid5(NAMESPACE_URL, f'{run_id}:{parent_record_id}:confirmed-ah-failure')),
        parent_record_id=parent_record_id, run_id=run_id,
        event_month=intent.event_time.astimezone(timezone.utc).strftime('%Y-%m-01'),
        batch_id=batch_id, strategy_number=strategy_number,
        source_entry_intent_id=source_entry_intent_id, assignment_id=financial.assignment_id,
        **{f.name: getattr(witness.five_second, f.name) for f in fields(FollowThroughFailure)},
        completed_ten_second_boundary_ms=witness.completed_ten_second_boundary_ms,
        ten_second_macd_line=witness.ten_second_macd_line,
        ten_second_macd_signal=witness.ten_second_macd_signal,
    )


def restore_confirmed_ah_failure(row):
    """Scalar replay only; stored-row hash/prefix verification must precede it."""
    if type(row.get('strategy_number')) is not int or row['strategy_number'] not in (34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47):
        raise ValueError('AH confirmation requires Strategy 34 through 36')
    integers = {'boundary_ms', 'first_held_boundary_ms', 'completed_close_int', 'quote_age_us'}
    values = {}
    for f in fields(FollowThroughFailure):
        value = row[f.name]
        if f.name in integers:
            if type(value) is not int:
                raise ValueError('AH witness lacks exact integer producer authority')
            values[f.name] = value
        else:
            if type(value) is bool:
                raise ValueError('AH witness scalar cannot be boolean')
            values[f.name] = float(value)
    witness = ConfirmedAhRiskFailure(
        FollowThroughFailure(**values), row['completed_ten_second_boundary_ms'],
        row['ten_second_macd_line'], row['ten_second_macd_signal'],
    )
    validate_confirmed_ah_witness(witness)
    return witness


def seal_confirmed_ah_rows(
    client, rows, intents, events, *, verified_prefix, first_price_source=None,
):
    """Prepared graph/hash sealing; active commit admission remains separate.

    Cold callers must verify original stored hashes before invoking this
    function. Producer/held/pending authority is not replaced by row hashing.
    """
    from .strategy_confirmed_ah_failure_source import validate_confirmed_ah_rows
    from .arte_journal_writer import typed_row
    checked = validate_confirmed_ah_rows(
        client, rows, intents, events, verified_prefix=verified_prefix,
        first_price_source=first_price_source,
    )
    return tuple(typed_row(CONFIRMED_AH_FAILURE.name, row) for row in checked)


def load_confirmed_ah_failure(client, prefix, exit_record_id):
    """Bounded cold scalar read after independently verified commit-prefix loading.

    Hash verification precedes UInt adaptation and predicate replay. This
    loader does not replace complete commit graph/source verification.
    """
    from .arte_journal_commit_v4 import V4CommittedPrefix
    from .arte_journal_writer import _literal, _rows
    from .arte_intent_projection import _verify_stored_row
    parent_id = str(UUID(str(exit_record_id)))
    if (type(prefix) is not V4CommittedPrefix or not prefix.batch_ids
            or prefix.status not in {'running', 'completed', 'stopped', 'failed'}):
        raise ValueError('AH cold read requires an independently verified committed prefix')
    columns = ','.join(name for name, _ in CONFIRMED_AH_FAILURE.columns)
    rows = _rows(client, f'SELECT {columns} FROM arte.{CONFIRMED_AH_FAILURE.name} '
                 f'WHERE run_id={_literal(prefix.run_id)} '
                 f'AND parent_record_id=toUUID({_literal(parent_id)}) LIMIT 2 FORMAT JSONEachRow')
    if (len(rows) != 1 or str(rows[0]['batch_id']) not in prefix.batch_ids
            or rows[0]['run_id'] != prefix.run_id
            or str(rows[0]['parent_record_id']) != parent_id):
        raise RuntimeError('AH confirmation lacks its unique committed prefix row')
    canonical = _verify_stored_row(CONFIRMED_AH_FAILURE.name, rows[0])
    unsigned = {name for name, kind in CONFIRMED_AH_FAILURE.columns if kind.startswith('UInt')}
    adapted = {k: int(v) if k in unsigned else v for k, v in canonical.items()}
    return rows[0], restore_confirmed_ah_failure(adapted)
