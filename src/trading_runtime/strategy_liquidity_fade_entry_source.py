"""Committed original-entry ancestry for the prepared liquidity exit family.

This check does not attest first-held time, current financial state or native
market observations. Publication must verify those independent authorities.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .arte_liquidity_fade_failure_v4 import (
    restore_liquidity_fade_failure, project_liquidity_fade_failure, CHECKPOINT_REFERENCE_FIELDS,
)
from .strategy_liquidity_fade_exit import liquidity_fade_exit_intent


def validate_liquidity_fade_entry_source(client, row, parent, event, *, verified_prefix,
                                        first_price_source=None, manager_source=None):
    """Bind one complete exit to its independently verified preceding entry."""
    from .arte_journal_commit_v4 import V4CommittedPrefix
    from .arte_followthrough_failure_v4 import _source_entry
    from .arte_intent_projection import project_strategy_intent
    from .arte_journal_writer import _canonical_typed_content
    from .strategy_one_stateful import StrategyOneFinancialView
    from .strategy_one_stateful import StrategyOneEntryProposal
    from .strategy_engine import AssignmentStatus, StrategyPermissions

    if (type(verified_prefix) is not V4CommittedPrefix
            or verified_prefix.status != 'running' or not verified_prefix.batch_ids
            or verified_prefix.last_batch_id != verified_prefix.batch_ids[-1]
            or verified_prefix.run_id != row['run_id']
            or type(event['sequence']) is not int
            or verified_prefix.last_sequence >= event['sequence']):
        raise ValueError('Liquidity exit requires an independently verified preceding prefix')
    witness = restore_liquidity_fade_failure(row)
    if row['source_manager_checkpoint_sequence'] > verified_prefix.last_sequence:
        raise ValueError('Liquidity manager checkpoint is outside its verified preceding prefix')
    context = dict(verified_prefix=verified_prefix)
    if first_price_source is not None:
        context['first_price_source'] = first_price_source
    source, source_event, child = _source_entry(
        client, row['run_id'], str(row['source_entry_intent_id']),
        prior_batch_id=verified_prefix.last_batch_id, exit_batch_id=str(row['batch_id']), **context)
    if (type(child['strategy_number']) is not int or child['strategy_number'] != row['strategy_number']
            or child['assignment_id'] != row['assignment_id']
            or type(child['boundary_ms']) is not int
            or not child['boundary_ms'] < witness.first_held_boundary_ms
            or source_event['account_id'] != event['account_id']
            or source_event['sequence'] >= event['sequence']
            or source_event['sequence'] >= row['source_manager_checkpoint_sequence']
            or str(source['intent_id']) != str(row['source_entry_intent_id'])
            or source['ticker'] != parent['ticker']
            or source['action'] != 'enter_long' or source['reason'] != 'strategy_one_entry'
            or float(source['reference_price']) != witness.reference_ask
            or float(source['invalidation_price']) != witness.initial_stop
            or row['parent_record_id'] != parent['record_id']
            or parent['record_id'] != event['record_id']
            or any(x['run_id'] != row['run_id'] or x['batch_id'] != row['batch_id']
                   for x in (parent, event))
            or parent['event_month'] != row['event_month']
            or parent['account_id'] != event['account_id']
            or event['category'] != 'strategy' or event['entity_type'] != 'strategy_intent'
            or str(parent['intent_id']) != str(event['entity_id'])):
        raise ValueError('Liquidity exit differs from its committed original entry or parent graph')
    if manager_source is not None and (
            type(manager_source) is not StrategyOneEntryProposal
            or manager_source.boundary_ms != child['boundary_ms']
            or (manager_source.account_id, manager_source.assignment_id, manager_source.ticker)
            != (event['account_id'], row['assignment_id'], parent['ticker'])
            or manager_source.strategy_number != row['strategy_number']
            or manager_source.reference_ask != witness.reference_ask
            or manager_source.initial_stop != witness.initial_stop):
        raise ValueError('Liquidity manager checkpoint differs from its committed original entry')
    at = datetime.fromisoformat(str(event['event_time']).replace('Z', '+00:00'))
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    day = at.astimezone(ZoneInfo('America/New_York')).date()
    source_at = datetime.fromisoformat(str(source_event['event_time']).replace('Z', '+00:00'))
    if source_at.tzinfo is None:
        source_at = source_at.replace(tzinfo=timezone.utc)
    if (source_at.astimezone(ZoneInfo('America/New_York')).date() != day
            or source_at >= at):
        raise ValueError('Liquidity exit original entry belongs to a different session day or future clock')
    # Parent quantity is used only to replay the exact factory. It is not a
    # substitute for independent verification of held quantity/pending orders.
    financial = StrategyOneFinancialView(row['assignment_id'], event['account_id'], parent['ticker'],
        AssignmentStatus.MANAGING, StrategyPermissions(), float(parent['quantity']), False, False, False, 1)
    expected = liquidity_fade_exit_intent(witness, financial, session_date=day,
        source_entry_intent_id=str(row['source_entry_intent_id']), strategy_number=row['strategy_number'])
    if expected.event_time != at.astimezone(timezone.utc):
        raise ValueError('Liquidity exit clock differs from its native decision boundary')
    source_keys = ('source_build_id', 'source_market_plan_token', 'source_bars_attempt_id',
                   'source_indicators_attempt_id', 'source_liquidity_attempt_id', *CHECKPOINT_REFERENCE_FIELDS)
    projected = project_liquidity_fade_failure(witness, expected, financial, session_date=day,
        source_entry_intent_id=str(row['source_entry_intent_id']), run_id=row['run_id'],
        batch_id=row['batch_id'], parent_record_id=row['parent_record_id'],
        strategy_number=row['strategy_number'],
        **{key: row[key] for key in source_keys})
    if {key: value for key, value in row.items() if key != 'content_hash'} != projected:
        raise ValueError('Liquidity exit scalar identity differs from its deterministic projection')
    content = {key: value for key, value in parent.items() if key != 'content_hash'}
    expected_content = {**content, **{key: value for key, value in project_strategy_intent(expected).core.items()
                                    if key != 'event_time'}}
    if (_canonical_typed_content('trading_strategy_intent_v1', content, stored_utc=True)
            != _canonical_typed_content('trading_strategy_intent_v1', expected_content, stored_utc=True)):
        raise ValueError('Liquidity exit differs from its immutable factory intent')
    return witness
