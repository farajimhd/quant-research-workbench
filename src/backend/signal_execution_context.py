"""Actual-run Signal Stream identity and scalar contextual row envelopes."""
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from hashlib import sha256

from src.trading_runtime.journal_contract import canonical_json
from .signal_context_schema import CONTEXT, LOGICAL_TABLES, context_table


def _hash(value):
    return sha256(canonical_json(value).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class SignalExecutionContext:
    run_id: str
    mode: str
    session_key: str
    configuration_revision: str
    source_revision: str

    def __post_init__(self):
        if (any(type(value) is not str or not value or value != value.strip()
                for value in (self.run_id, self.configuration_revision, self.source_revision))
                or any(len(value) > 1024 for value in (self.run_id, self.configuration_revision, self.source_revision))
                or type(self.mode) is not str or self.mode not in {'backtest', 'replay', 'paper', 'live'}
                or type(self.session_key) is not str
                or date.fromisoformat(self.session_key).isoformat() != self.session_key
                or self.source_journal_run_id != self.run_id):
            raise ValueError('Signal context requires its exact native run and calendar identity')

    @property
    def context_id(self):
        return _hash(_identity(self))

    @property
    def source_journal_run_id(self):
        return self.run_id

    @property
    def seal_hash(self):
        return seal_context(self)['content_hash']


def _identity(context):
    if type(context) is not SignalExecutionContext:
        raise TypeError('Signal rows require their concrete execution context')
    return dict(schema_version=2, session_key=context.session_key, run_id=context.run_id,
        source_journal_run_id=context.source_journal_run_id, mode=context.mode,
        configuration_revision=context.configuration_revision, source_revision=context.source_revision)


def seal_context(context):
    row = {**_identity(context), 'source_context_id': context.context_id}
    return {**row, 'content_hash': _hash(row)}


def restore_context_seal(row):
    if not isinstance(row, Mapping) or set(row) != {name for name, _ in CONTEXT.columns}:
        raise ValueError('Signal context seal fields differ')
    if type(row['schema_version']) is not int or row['schema_version'] != 2:
        raise ValueError('Signal context seal schema differs')
    try:
        context = SignalExecutionContext(**{key: row[key] for key in (
            'run_id', 'mode', 'session_key', 'configuration_revision', 'source_revision')})
    except (TypeError, ValueError) as exc:
        raise ValueError('Signal context seal identity is invalid') from exc
    if dict(row) != seal_context(context):
        raise ValueError('Signal context seal integrity differs')
    return context


def _logical(logical_table, row):
    concrete = context_table(logical_table)
    original = next(table for table in LOGICAL_TABLES if context_table(table) == concrete)
    if not isinstance(row, Mapping) or set(row) != {key for key, _ in original.columns}:
        raise ValueError('Contextual logical row fields differ')
    value = dict(row)
    for key, kind in original.columns:
        _require_scalar(kind, value[key])
    from src.trading_runtime.arte_journal_rowbinary import encode_journal_rows
    try:
        encode_journal_rows(original.columns, (value,))
    except (TypeError, ValueError, AttributeError, OverflowError) as exc:
        raise ValueError('Contextual logical scalar types differ') from exc
    if value['content_hash'] != _hash({key: item for key, item in value.items() if key != 'content_hash'}):
        raise ValueError('Contextual logical content hash differs')
    return original, value


def _require_scalar(kind, value):
    if kind.startswith('Nullable('):
        if value is not None:
            _require_scalar(kind[9:-1], value)
        return
    if kind == 'Bool':
        valid = type(value) is bool
    elif kind.startswith(('UInt', 'Int')):
        valid = type(value) is int
    elif kind in {'Float32', 'Float64'}:
        valid = type(value) in {int, float}
    else:
        valid = type(value) is str
    if not valid:
        raise ValueError('Contextual logical scalar types differ')


def project_context_row(context, logical_table, row):
    original, value = _logical(logical_table, row)
    if value['session_key'] != context.session_key:
        raise ValueError('Contextual row calendar differs from native context')
    for key, expected in (('configuration_revision', context.configuration_revision),
                          ('configuration_revision_id', context.configuration_revision),
                          ('source_revision', context.source_revision)):
        if key in value and value[key] != expected:
            raise ValueError('Contextual row configuration/source revision differs from native context')
    envelope = dict(source_context_id=context.context_id, logical_table=original.name, row=value)
    return {**value, 'source_context_id': context.context_id, 'context_content_hash': _hash(envelope)}


def restore_context_row(context, logical_table, row):
    concrete = context_table(logical_table)
    if not isinstance(row, Mapping) or set(row) != {key for key, _ in concrete.columns}:
        raise ValueError('Contextual persisted row fields differ')
    value = {key: item for key, item in row.items() if key not in {'source_context_id', 'context_content_hash'}}
    if project_context_row(context, logical_table, value) != dict(row):
        raise ValueError('Contextual persisted row integrity differs')
    return value
