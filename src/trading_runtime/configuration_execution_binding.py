"""Shared normalized execution selections; no database writes or defaults."""
from dataclasses import asdict, dataclass
from datetime import datetime
from types import MappingProxyType
from copy import deepcopy

from .arte_journal_schema import TableContract
from .execution_policies import ExecutionPolicy, execution_policy_from_payload
from .strategy_configuration_selection import _row, _hash


POLICY = TableContract('trading_configuration_execution_policy_v1', (
    ('configuration_id', 'UUID'), ('policy_id', 'String'), ('revision', 'UInt32'),
    ('name', 'String'), ('partial_fill_policy', 'String'), ('quote_source', 'String'),
    ('maximum_buy_price', 'Nullable(Float64)'), ('minimum_sell_price', 'Nullable(Float64)'),
    ('deadline_ms', 'UInt32'), ('maximum_reprices', 'UInt32'),
    ('minimum_reprice_interval_ms', 'UInt32'), ('persist_until_cancelled', 'UInt8')),
    'tuple()', 'configuration_id,policy_id')
SEAL = TableContract('trading_configuration_execution_binding_v1', (
    ('configuration_id', 'UUID'), ('configuration_hash', 'FixedString(64)'),
    ('entry_policy_id', 'String'), ('exit_policy_id', 'String'), ('tick_size', 'Float64'),
    ('limit_offset_bps', 'Float64'), ('policy_count', 'UInt32'),
    ('policy_hash', 'FixedString(64)'), ('content_hash', 'FixedString(64)'),
    ('published_at', "DateTime64(6, 'UTC')")), 'tuple()', 'configuration_id')
TABLES = (POLICY, SEAL)


def bind_execution_configuration(revision, binding):
    """Join the independent sealed binding to its exact central selection."""
    if type(binding) is not ConfigurationExecutionBinding or not isinstance(revision, dict):
        raise TypeError('Execution configuration requires its typed binding and materialized selection')
    binding = restore_execution_binding(binding.rows)
    header = binding.header
    if (revision.get('revision_id') != 'strategy-configuration:' + header['configuration_id']
            or revision.get('selection_hash') != header['configuration_hash']
            or revision.get('execution_binding_hash') not in (None, header['content_hash'])
            or revision.get('payload', {}).get('oms', {}).get('limit_offset_bps') != header['limit_offset_bps']):
        raise ValueError('Execution binding differs from its selected configuration')
    result = deepcopy(revision)
    oms = result['payload']['oms']
    oms.update(entry_execution_policy_id=header['entry_policy_id'],
        exit_execution_policy_id=header['exit_policy_id'], tick_size=header['tick_size'],
        execution_policies=[asdict(binding.policies[key]) for key in sorted(binding.policies)])
    result['execution_binding_hash'] = header['content_hash']
    result['content_hash'] = _hash(result['payload'])
    return result


@dataclass(frozen=True, slots=True)
class ConfigurationExecutionBinding:
    header: object
    rows: object
    policies: object

    def policy_for(self, action):
        if action in {'enter_long', 'add_long', 'enter_short', 'add_short'}:
            return self.policies[self.header['entry_policy_id']]
        if action in {'reduce_long', 'reduce_short', 'exit', 'cover', 'cancel_entry',
                      'replace_protective_stop'}:
            return self.policies[self.header['exit_policy_id']]
        raise ValueError('Execution binding does not recognize the native action')


def project_execution_binding(*, configuration_id, configuration_hash, policies,
        entry_policy_id, exit_policy_id, tick_size, limit_offset_bps, published_at):
    """Project explicitly selected native objects without choosing a policy."""
    if not isinstance(published_at, datetime) or not policies:
        raise ValueError('Execution binding requires explicit policies and publication time')
    rows = []
    for policy in policies:
        if type(policy) is not ExecutionPolicy:
            raise TypeError('Execution binding requires native execution policy objects')
        values = asdict(policy)
        envelope = values.pop('envelope')
        row = dict(configuration_id=configuration_id, **values, **envelope)
        row['name'] = str(row['name'])
        row['partial_fill_policy'] = str(row['partial_fill_policy'])
        row['persist_until_cancelled'] = int(row['persist_until_cancelled'])
        rows.append(_row(POLICY, row))
    rows.sort(key=lambda row: row['policy_id'])
    header = dict(configuration_id=configuration_id, configuration_hash=configuration_hash,
        entry_policy_id=entry_policy_id, exit_policy_id=exit_policy_id,
        tick_size=tick_size, limit_offset_bps=limit_offset_bps, policy_count=len(rows),
        policy_hash=_hash(rows), published_at=published_at)
    header['content_hash'] = _hash({key: value for key, value in header.items()
        if key != 'published_at'})
    return restore_execution_binding({POLICY.name: rows, SEAL.name: [header]})


def restore_execution_binding(source):
    """Verify complete typed rows, references and hashes before materialization."""
    if set(source) != {table.name for table in TABLES} or len(source[SEAL.name]) != 1:
        raise ValueError('Execution binding requires one seal and its exact row families')
    header = _row(SEAL, source[SEAL.name][0])
    rows = tuple(_row(POLICY, row) for row in source[POLICY.name])
    if (not rows or len(rows) != header['policy_count']
            or any(row['configuration_id'] != header['configuration_id'] for row in rows)
            or [row['policy_id'] for row in rows] != sorted({row['policy_id'] for row in rows})
            or _hash(rows) != header['policy_hash'] or header['tick_size'] <= 0
            or _hash({key: value for key, value in header.items()
                if key not in {'published_at', 'content_hash'}}) != header['content_hash']):
        raise ValueError('Execution binding identity, ordering or integrity differs')
    policies = {}
    for row in rows:
        if row['persist_until_cancelled'] not in (0, 1):
            raise ValueError('Execution binding boolean is invalid')
        payload = {key: row[key] for key in (
            'policy_id', 'revision', 'name', 'partial_fill_policy', 'quote_source')}
        payload['envelope'] = {key: row[key] for key in (
            'maximum_buy_price', 'minimum_sell_price', 'deadline_ms', 'maximum_reprices',
            'minimum_reprice_interval_ms', 'persist_until_cancelled')}
        payload['envelope']['persist_until_cancelled'] = bool(row['persist_until_cancelled'])
        policy = execution_policy_from_payload(payload)
        policies[policy.policy_id] = policy
    if any(header[key] not in policies for key in ('entry_policy_id', 'exit_policy_id')):
        raise ValueError('Execution binding selected policies are absent')
    return ConfigurationExecutionBinding(MappingProxyType(header),
        MappingProxyType({POLICY.name: tuple(MappingProxyType(row) for row in rows),
            SEAL.name: (MappingProxyType(header),)}), MappingProxyType(policies))
