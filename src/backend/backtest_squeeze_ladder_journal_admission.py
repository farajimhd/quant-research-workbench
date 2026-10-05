"""Prepared source and parent checks before native ladder family publication."""
from datetime import datetime

from src.backend.backtest_squeeze_ladder_evidence import SETUP, TARGET
from src.backend.backtest_squeeze_ladder_readback import reconstruct_ladder_evidence
from src.trading_runtime.arte_intent_projection import strategy_intent_batch
from src.trading_runtime.arte_journal_writer import TypedJournalBatch


def prepare_ladder_journal_families(batch: TypedJournalBatch, rows, **certified_context):
    """Verify one original strategy intent and its exact evidence companions.

    A future writer envelope must bind certified_context to independently
    verified sources, frozen release policy and historical financial prefix.
    This function performs no DDL, insert, commit or order submission.
    """
    if (not isinstance(batch, TypedJournalBatch) or batch.status != 'running'
            or len(batch.events) != 1 or len(batch.intents) != 1):
        raise ValueError('Ladder journal admission requires one running intent parent')
    event = batch.events[0]
    parent = batch.intents[0]
    financial = certified_context['financial']
    if (event['record_id'] != parent['record_id']
            or event['category'] != 'strategy' or event['entity_type'] != 'strategy_intent'
            or event['account_id'] != financial.account_id or parent['account_id'] != financial.account_id):
        raise ValueError('Ladder journal evidence has a foreign intent/account parent')
    intent = reconstruct_ladder_evidence(rows, run_id=batch.run_id, batch_id=batch.batch_id,
        parent_record_id=event['record_id'], **certified_context)
    expected = strategy_intent_batch(intent, run_id=batch.run_id, run_month=batch.run_month,
        account_id=financial.account_id, attempt_id=batch.attempt_id, batch_id=batch.batch_id,
        prior_batch_id=batch.prior_batch_id, sequence=batch.first_sequence,
        source_cursor=batch.source_cursor, run_status=batch.status,
        recorded_at=datetime.fromisoformat(event['recorded_at']), record_id=event['record_id'],
        correlation_id=event['correlation_id'], causation_id=event['causation_id'])
    if (batch.first_sequence != batch.last_sequence
            or tuple(dict(row) for row in batch.events) != tuple(dict(row) for row in expected.events)
            or tuple(dict(row) for row in batch.intents) != tuple(dict(row) for row in expected.intents)
            or tuple(dict(row) for row in batch.intent_slices) != tuple(dict(row) for row in expected.intent_slices)):
        raise ValueError('Ladder parent intent/protection differs from independently reconstructed source')
    return ((SETUP.name, (dict(rows.setup),)),
            (TARGET.name, tuple(dict(row) for row in rows.targets)))
