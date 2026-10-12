"""Shared normalized evidence of cancelling entry acquisition before reduction."""
from __future__ import annotations
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, TYPE_CHECKING
from uuid import NAMESPACE_URL, uuid5

from .arte_journal_schema import TableContract
if TYPE_CHECKING:
    from .arte_journal_writer import TypedJournalBatch

_COMMON = (("record_id", "UUID"), ("run_id", "String"),
           ("event_month", "Date"), ("batch_id", "UUID"), ("account_id", "String"))
FREEZE = TableContract("trading_oms_entry_acquisition_freeze_v1", _COMMON + (
    ("exit_intent_id", "String"), ("ticker", "LowCardinality(String)"),
    ("exit_action", "LowCardinality(String)"), ("command_order", "LowCardinality(String)"),
    ("strategy_id", "String"), ("strategy_revision", "UInt32"),
    ("group_count", "UInt32"), ("content_hash", "FixedString(64)")),
    "toYYYYMM(event_month)", "run_id,account_id,record_id")
GROUP = TableContract("trading_oms_entry_acquisition_freeze_group_v1", _COMMON + (
    ("parent_record_id", "UUID"), ("ordinal", "UInt32"), ("source_group_id", "String"),
    ("content_hash", "FixedString(64)")),
    "toYYYYMM(event_month)", "run_id,parent_record_id,ordinal,record_id")
TABLES = (FREEZE, GROUP)
FAMILY_NAMES = frozenset(table.name for table in TABLES)


def seal_entry_freeze_rows(parents, children, events, *, run_id, batch_id):
    from .arte_journal_writer import typed_row
    sealed = []
    for table, rows in ((FREEZE, parents), (GROUP, children)):
        family = []
        ids = set()
        for row in rows:
            result = typed_row(table.name, {k: v for k, v in row.items() if k != "content_hash"})
            if (row.get("content_hash", result['content_hash']) != result['content_hash']
                    or result['run_id'] != run_id or result['batch_id'] != batch_id
                    or result['record_id'] in ids):
                raise ValueError("Entry freeze scalar hash or identity differs")
            ids.add(result['record_id'])
            family.append(result)
        sealed.append(tuple(family))
    parents, children = sealed
    wanted = {event['record_id']: event for event in events if
        (event['category'], event['entity_type']) == ('order_management', 'entry_acquisition_frozen_before_exit')}
    if len(parents) != len(wanted) or {row['record_id'] for row in parents} != set(wanted):
        raise ValueError("Entry freeze parent coverage differs from events")
    by_parent = {row['record_id']: [] for row in parents}
    for child in children:
        if child['parent_record_id'] not in by_parent:
            raise ValueError("Entry freeze group has no parent")
        by_parent[child['parent_record_id']].append(child)
    for parent in parents:
        event = wanted[parent['record_id']]
        groups = sorted(by_parent[parent['record_id']], key=lambda row: row['ordinal'])
        if (not parent['ticker'] or not parent['strategy_id'] or not parent['exit_intent_id']
                or parent['exit_action'] not in {'exit', 'take_profit', 'reduce_long', 'cover', 'reduce_short'}
                or parent['command_order'] != 'cancel_entries_then_submit_exit'
                or parent['exit_intent_id'] != event['entity_id']
                or parent['account_id'] != event['account_id']
                or parent['event_month'] != event['event_month']
                or not 1 <= parent['group_count'] <= 4096 or len(groups) != parent['group_count']
                or [row['ordinal'] for row in groups] != list(range(len(groups)))
                or len({row['source_group_id'] for row in groups}) != len(groups)
                or any(not row['source_group_id'] or any(row[field] != parent[field] for field in
                    ('run_id', 'batch_id', 'event_month', 'account_id')) for row in groups)):
            raise ValueError("Entry freeze groups differ from the reduction event")
    return parents, children


@dataclass(frozen=True, slots=True)
class EntryAcquisitionFreezeBatch:
    base: TypedJournalBatch
    freeze: Mapping[str, Any]
    groups: tuple[Mapping[str, Any], ...]

    def __post_init__(self):
        from .arte_journal_writer import TypedJournalBatch
        if type(self.base) is not TypedJournalBatch or len(self.base.events) != 1:
            raise ValueError("Entry freeze requires one typed event envelope")
        parent, children = seal_entry_freeze_rows((self.freeze,), self.groups, self.base.events,
            run_id=self.base.run_id, batch_id=self.base.batch_id)
        object.__setattr__(self, 'freeze', MappingProxyType(parent[0]))
        object.__setattr__(self, 'groups', tuple(MappingProxyType(row) for row in children))


def entry_acquisition_freeze_batch(record, *, run_month, attempt_id, batch_id,
                                 prior_batch_id, source_cursor):
    from .arte_journal_writer import TypedJournalBatch
    from datetime import timezone
    payload = record.payload
    required = {'ticker', 'exit_action', 'source_group_ids', 'command_order', 'strategy_id', 'strategy_revision'}
    if (record.category != 'order_management' or record.entity_type != 'entry_acquisition_frozen_before_exit'
            or not required <= set(payload) or set(payload) - required - {'correlation_id', 'causation_id'}
            or type(payload['source_group_ids']) not in (list, tuple)):
        raise ValueError(f"Entry freeze source requires its closed emitted fields: {sorted(payload)}")
    if (record.event_time.tzinfo is None or record.recorded_at.tzinfo is None
            or not 1 <= len(payload['source_group_ids']) <= 4096):
        raise ValueError("Entry freeze needs aware clocks and bounded source groups")
    common = dict(run_id=record.run_id, event_month=record.event_time.astimezone(timezone.utc).strftime('%Y-%m-01'),
        batch_id=batch_id, account_id=record.account_id)
    event = {**common, 'record_id': record.record_id, 'attempt_id': attempt_id,
        'sequence': record.sequence, 'event_time': record.event_time.isoformat(),
        'recorded_at': record.recorded_at.isoformat(), 'category': record.category,
        'entity_type': record.entity_type, 'entity_id': record.entity_id,
        'correlation_id': payload.get('correlation_id', ''), 'causation_id': payload.get('causation_id', '')}
    parent = {**common, 'record_id': record.record_id, 'exit_intent_id': record.entity_id,
        **{key: payload[key] for key in ('ticker', 'exit_action', 'command_order', 'strategy_id', 'strategy_revision')},
        'group_count': len(payload['source_group_ids'])}
    children = tuple({**common, 'record_id': str(uuid5(NAMESPACE_URL, f"{record.record_id}:frozen-group:{ordinal}")),
        'parent_record_id': record.record_id, 'ordinal': ordinal, 'source_group_id': group}
        for ordinal, group in enumerate(payload['source_group_ids']))
    base = TypedJournalBatch(record.run_id, run_month, attempt_id, batch_id, prior_batch_id,
        record.sequence, record.sequence, source_cursor, 'running', (event,))
    return EntryAcquisitionFreezeBatch(base, parent, children)
