"""Prepared source graph verification; no writer/commit admission or live path.

The caller must independently verify the prefix. The existing bounded original
entry loader retains its committed-ancestry checks; this module binds that
authority to the complete new exit witness and exact factory projection.
"""
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo
from types import MappingProxyType
from uuid import NAMESPACE_URL, UUID, uuid5

from .arte_confirmed_ah_failure_v4 import restore_confirmed_ah_failure
from .strategy_confirmed_ah_failure_exit import confirmed_ah_reason, confirmed_ah_exit_intent


def validate_confirmed_ah_rows(
    client, rows, intents, events, *, verified_prefix, first_price_source=None,
):
    """Prepared bounded family graph check, before registered typed sealing.

    Stored hashes must be verified by cold readers before this function; it
    never registers, hashes, writes, or attests producer market observations.
    """
    from .arte_confirmed_ah_failure_v4 import CONFIRMED_AH_FAILURE
    if max(len(rows), len(intents), len(events)) > 65_536:
        raise ValueError('AH confirmation graph exceeds its bounded family limit')
    reasons = {confirmed_ah_reason(number) for number in (34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47)}
    parents = {str(p['record_id']): p for p in intents if p['reason'] in reasons}
    event_map = {str(e['record_id']): e for e in events}
    if (len({str(p['record_id']) for p in intents}) != len(intents)
            or len(event_map) != len(events) or len(rows) != len(parents)):
        raise ValueError('AH confirmation family has missing, extra or duplicate parents')
    expected_columns = {name for name, _ in CONFIRMED_AH_FAILURE.columns} - {'content_hash'}
    seen, records, result = set(), set(), []
    for row in rows:
        if set(row) - {'content_hash'} != expected_columns:
            raise ValueError('AH confirmation family lacks its complete scalar shape')
        parent_id = str(row['parent_record_id'])
        record_id = str(row['record_id'])
        expected_id = str(uuid5(NAMESPACE_URL, f"{row['run_id']}:{parent_id}:confirmed-ah-failure"))
        if (parent_id in seen or record_id in records
                or parent_id not in parents or parent_id not in event_map
                or str(UUID(record_id)) != expected_id):
            raise ValueError('AH confirmation family lacks a unique deterministic exit child')
        seen.add(parent_id)
        records.add(record_id)
        validate_confirmed_ah_source(
            client, row, parents[parent_id], event_map[parent_id],
            verified_prefix=verified_prefix, first_price_source=first_price_source,
        )
        result.append(MappingProxyType({k: v for k, v in row.items() if k != 'content_hash'}))
    return tuple(result)


def validate_confirmed_ah_source(
    client, row, parent, event, *, verified_prefix, first_price_source=None,
):
    """Verify one preceding entry/exit graph; return its scalar witness.

    This is deliberately not a sealing function: the family is not registered,
    and native market/held-state authority must also be bound before admission.
    """
    from .arte_journal_commit_v4 import V4CommittedPrefix
    from .arte_followthrough_failure_v4 import _source_entry
    from .arte_intent_projection import project_strategy_intent
    from .arte_journal_writer import _canonical_typed_content
    from .strategy_one_stateful import StrategyOneFinancialView
    from .strategy_engine import AssignmentStatus, StrategyPermissions

    if (type(verified_prefix) is not V4CommittedPrefix
            or verified_prefix.status != 'running'
            or not verified_prefix.batch_ids
            or verified_prefix.last_batch_id != verified_prefix.batch_ids[-1]
            or verified_prefix.run_id != row['run_id']
            or type(event['sequence']) is not int
            or verified_prefix.last_sequence >= event['sequence']):
        raise ValueError('AH failure requires an independently verified preceding prefix')
    witness = restore_confirmed_ah_failure(row)
    w = witness.five_second
    context = dict(verified_prefix=verified_prefix)
    if first_price_source is not None:
        context['first_price_source'] = first_price_source
    source, source_event, child = _source_entry(
        client, row['run_id'], str(row['source_entry_intent_id']),
        prior_batch_id=verified_prefix.last_batch_id,
        exit_batch_id=str(row['batch_id']), **context,
    )
    instant = datetime.fromisoformat(str(event['event_time']).replace('Z', '+00:00'))
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    local = instant.astimezone(ZoneInfo('America/New_York'))
    elapsed_ms = (local - datetime.combine(local.date(), time(4), local.tzinfo)).total_seconds() * 1000
    if (row['strategy_number'] != child['strategy_number']
            or row['assignment_id'] != child['assignment_id']
            or row['parent_record_id'] != parent['record_id']
            or parent['record_id'] != event['record_id']
            or row['run_id'] != parent['run_id'] or parent['run_id'] != event['run_id']
            or row['batch_id'] != parent['batch_id'] or parent['batch_id'] != event['batch_id']
            or row['event_month'] != parent['event_month']
            or event['account_id'] != source_event['account_id']
            or str(source['intent_id']) != str(row['source_entry_intent_id'])
            or source['ticker'] != parent['ticker'] or parent['action'] != 'exit'
            or parent['reason'] != confirmed_ah_reason(row['strategy_number'])
            or source['action'] != 'enter_long' or source['reason'] != 'strategy_one_entry'
            or source_event['sequence'] >= event['sequence']
            or child['boundary_ms'] >= w.first_held_boundary_ms
            or float(source['reference_price']) != w.reference_ask
            or float(source['invalidation_price']) != w.initial_stop
            or float(parent['reference_price']) != w.bid
            or elapsed_ms != w.boundary_ms
            or parent['intent_id'] != event['entity_id']):
        raise ValueError('AH confirmation differs from its original entry or exit graph')
    financial = StrategyOneFinancialView(
        row['assignment_id'], event['account_id'], parent['ticker'],
        AssignmentStatus.WATCHING, StrategyPermissions(),
        float(parent['quantity']), False, False, False, 1,
    )
    expected = confirmed_ah_exit_intent(
        witness, financial, session_date=local.date(),
        source_entry_intent_id=str(row['source_entry_intent_id']), strategy_number=row['strategy_number'],
    )
    content = {k: v for k, v in parent.items() if k != 'content_hash'}
    expected_content = {
        **content, **{k: v for k, v in project_strategy_intent(expected).core.items()
                     if k != 'event_time'},
    }
    if (_canonical_typed_content('trading_strategy_intent_v1', content, stored_utc=True)
            != _canonical_typed_content('trading_strategy_intent_v1', expected_content, stored_utc=True)):
        raise ValueError('AH confirmation differs from its immutable factory intent')
    return witness
