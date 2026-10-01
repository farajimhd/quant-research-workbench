"""Cross-commit consumers retain the exact hash of their rekeyed source."""
from dataclasses import replace
from datetime import date, datetime, timezone
from uuid import UUID

import pytest

from src.backend.backtest_typed_publisher import _coalesce_v4_units
from src.trading_runtime.arte_intent_projection import strategy_intent_batch
from src.trading_runtime.arte_journal_compound_v4 import publish_compound_v4
from src.trading_runtime.arte_journal_writer import _FAMILIES, _verify_exact_intent_uses, typed_row
from src.trading_runtime.arte_oms_projection import oms_group_state_batch
from src.trading_runtime.arte_profit_giveback_v4 import V4ProfitGivebackBatch
from src.trading_runtime.ibkr_schema import OrderRequest
from src.trading_runtime.order_management import _ManagedOrderGroup, OrderManagementState
from src.trading_runtime.strategy_orders import StrategyOrderPlan
from test_arte_intent_projection import intent
from test_profit_giveback_typed_batch import unit
from tests.test_arte_journal_commit_v4 import attached_v4_client
from tests.test_arte_journal_writer import batch


@pytest.mark.parametrize('corrupt', [False, True])
def test_rekeyed_predecessor_intent_is_exactly_bound_to_later_consumer(corrupt):
    profit, row = unit(strategy_number=34)
    at = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)
    source = intent()
    first = strategy_intent_batch(source, run_id=profit.run_id, run_month=date(2026, 8, 1),
        account_id='DU1', attempt_id=profit.attempt_id, batch_id=str(UUID(int=990)),
        prior_batch_id=str(UUID(int=0)), sequence=1, source_cursor='start',
        run_status='running', recorded_at=at)
    seed = batch()
    events = tuple(typed_row('trading_event_v1', {
        **{k: v for k, v in seed.events[0].items() if k != 'content_hash'},
        'run_id': profit.run_id, 'batch_id': profit.prior_batch_id,
        'attempt_id': profit.attempt_id, 'record_id': str(UUID(int=1000 + sequence)),
        'sequence': sequence,
    }) for sequence in range(2, 10))
    middle = replace(seed, run_id=profit.run_id, attempt_id=profit.attempt_id,
        batch_id=profit.prior_batch_id, prior_batch_id=first.batch_id,
        first_sequence=2, last_sequence=9, events=events)
    order = OrderRequest(acctId='DU1', conid=123, cOID='entry-1', ticker='test',
                         orderType='LMT', side='BUY', quantity=5, price=12.5)
    group = _ManagedOrderGroup('group-1', source, 'DU1', StrategyOrderPlan((order,)),
        OrderManagementState.CREATED, at, at, [order], remaining_quantity=5.)
    consumer = oms_group_state_batch(group, run_id=profit.run_id, run_month=date(2026, 8, 1),
        attempt_id=profit.attempt_id, batch_id=str(UUID(int=991)),
        prior_batch_id=profit.batch_id, sequence=11, source_cursor='start',
        run_status='running', strategy_id='strategy-1', strategy_revision=1,
        recorded_at=at, published_intent_batch=first, committed_intent_batch_id=first.batch_id)
    original = consumer.intent_uses[0]['intent_content_hash']
    if corrupt:
        consumer = replace(consumer, intent_uses=({**consumer.intent_uses[0],
            'intent_content_hash': 'a' * 64},))
        with pytest.raises(ValueError, match='original source'):
            _coalesce_v4_units((first, middle, V4ProfitGivebackBatch(profit, row), consumer))
        return
    groups = _coalesce_v4_units((first, middle, V4ProfitGivebackBatch(profit, row), consumer))
    assert len(groups) == 2 and consumer.intent_uses[0]['intent_content_hash'] == original
    client = attached_v4_client()
    publish_compound_v4(client, groups[0])
    final = groups[1].base
    families = tuple((name, tuple(typed_row(name, {
        k: v for k, v in item.items() if k != 'content_hash'})
        for item in getattr(final, attribute))) for name, attribute, *_ in _FAMILIES)
    _verify_exact_intent_uses(client, final, families, journal_profile='backtest_v4')
    assert final.intent_uses[0]['intent_content_hash'] != original
