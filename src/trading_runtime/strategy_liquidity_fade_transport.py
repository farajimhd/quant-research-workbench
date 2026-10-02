"""Prepared immutable intent transport; no native writer or financial attestation."""
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from zoneinfo import ZoneInfo

from .arte_liquidity_fade_failure_v4 import (
    restore_liquidity_fade_failure, project_liquidity_fade_failure, CHECKPOINT_REFERENCE_FIELDS,
)
from .strategy_liquidity_fade_exit import liquidity_fade_exit_intent


@dataclass(frozen=True, slots=True)
class V4LiquidityFadeFailureBatch:
    """Check exact factory/envelope identity before any future native admission.

    Source ancestry, producer observations and actual held/pending financial
    state must be independently verified at publication. This transport does
    not certify them and is not an installed journal type.
    """
    base: object
    failure: object

    def __post_init__(self):
        from .arte_journal_writer import TypedJournalBatch, _canonical_typed_content, _FAMILIES
        from .arte_intent_projection import project_strategy_intent
        from .strategy_one_stateful import StrategyOneFinancialView
        from .strategy_engine import AssignmentStatus, StrategyPermissions
        if (type(self.base) is not TypedJournalBatch or self.base.status != 'running'
                or len(self.base.events) != 1 or len(self.base.intents) != 1
                or self.base.first_sequence != self.base.last_sequence
                or any(getattr(self.base, family) for _, family, _, _ in _FAMILIES
                       if family not in ('events', 'intents'))):
            raise ValueError('Liquidity batch requires one exact running typed intent')
        row = dict(self.failure)
        witness = restore_liquidity_fade_failure(row)
        if 'content_hash' in row:
            raise ValueError('Liquidity transport requires its complete unsealed scalar witness')
        event, parent = self.base.events[0], self.base.intents[0]
        if (row['run_id'] != self.base.run_id or row['batch_id'] != self.base.batch_id
                or row['parent_record_id'] != event['record_id']
                or parent['record_id'] != event['record_id']
                or any(x['run_id'] != row['run_id'] or x['batch_id'] != row['batch_id']
                       for x in (parent, event))
                or parent['account_id'] != event['account_id']
                or parent['event_month'] != row['event_month']
                or event['category'] != 'strategy' or event['entity_type'] != 'strategy_intent'
                or parent['intent_id'] != event['entity_id']
                or event['sequence'] != self.base.first_sequence
                or row['source_manager_checkpoint_sequence'] >= event['sequence']):
            raise ValueError('Liquidity batch differs from its exact event/intent envelope')
        at = datetime.fromisoformat(str(event['event_time']).replace('Z', '+00:00'))
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        day = at.astimezone(ZoneInfo('America/New_York')).date()
        financial = StrategyOneFinancialView(row['assignment_id'], event['account_id'], parent['ticker'],
            AssignmentStatus.MANAGING, StrategyPermissions(), float(parent['quantity']), False, False, False, 1)
        expected = liquidity_fade_exit_intent(witness, financial, session_date=day,
                                            source_entry_intent_id=row['source_entry_intent_id'])
        if expected.event_time != at.astimezone(timezone.utc):
            raise ValueError('Liquidity batch event clock differs from its witness')
        source = {key: row[key] for key in ('source_build_id', 'source_market_plan_token',
                  'source_bars_attempt_id', 'source_indicators_attempt_id', 'source_liquidity_attempt_id',
                  *CHECKPOINT_REFERENCE_FIELDS)}
        projected = project_liquidity_fade_failure(witness, expected, financial, session_date=day,
            source_entry_intent_id=row['source_entry_intent_id'], run_id=row['run_id'],
            batch_id=row['batch_id'], parent_record_id=row['parent_record_id'], **source)
        if row != projected:
            raise ValueError('Liquidity batch has altered scalar identity or values')
        content = {k: v for k, v in parent.items() if k != 'content_hash'}
        expected_content = {**content, **{k: v for k, v in project_strategy_intent(expected).core.items()
                                         if k != 'event_time'}}
        if (_canonical_typed_content('trading_strategy_intent_v1', content, stored_utc=True)
                != _canonical_typed_content('trading_strategy_intent_v1', expected_content, stored_utc=True)):
            raise ValueError('Liquidity batch differs from its exact ordinary exit factory')
        object.__setattr__(self, 'failure', MappingProxyType(row))
