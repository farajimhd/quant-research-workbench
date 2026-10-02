"""Real native broker row/hash/bit contracts; cold context/OMS readers mocked."""
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timezone

import pytest

from src.backend.backtest_market_data import market_day_boundary
from src.trading_runtime.strategy_liquidity_fade_financial_checkpoint import load_liquidity_fade_financial_checkpoint
from src.trading_runtime.strategy_one_broker_match_snapshot import project_broker_match_snapshot
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.trading_runtime.arte_intent_projection import RecoveredIntent
from src.trading_runtime.arte_oms_projection import RecoveredOmsGroupState, RecoveredStrategyOneOmsLineage
from src.trading_runtime.ibkr_schema import OrderRequest
from src.trading_runtime.strategy_one_contract import STRATEGY_ID
from src.trading_runtime.order_management import OrderManagementState
from src.trading_runtime.strategy_liquidity_fade_exit import liquidity_fade_exit_intent
from test_arte_liquidity_fade_failure_v4 import prepared_case, IDENTITY
from test_liquidity_fade_prepared_transport import transport


def native_lineage(financial, witness, *, action='enter_long', assignment='assignment',
                   intent_id=IDENTITY, group_id='entry', conid=123, state=OrderManagementState.WORKING):
    core = liquidity_fade_exit_intent(witness, financial, session_date=date(2026, 8, 10),
                                     source_entry_intent_id=IDENTITY)
    intent = replace(core, intent_id=intent_id, action=action,
                     event_time=datetime(2026, 8, 10, 20, 26, 20, tzinfo=timezone.utc))
    source = RecoveredIntent(12, financial.account_id, IDENTITY, 'prior', intent)
    order = OrderRequest(acctId=financial.account_id, conid=conid, ticker=financial.ticker,
        orderType='LMT', side='BUY' if action == 'enter_long' else 'SELL', quantity=financial.position_quantity, price=2.33)
    group = dict(run_id='run', batch_id='prior', group_id=group_id, account_id=financial.account_id,
                 strategy_id=STRATEGY_ID, strategy_revision=35, strategy_intent_id=intent_id, state=state.value)
    recovered = RecoveredOmsGroupState(20, IDENTITY, group, (order,), (0,), ('entry',), (), (), ())
    approved = replace(intent, metadata={'assignment_id': assignment})
    return RecoveredStrategyOneOmsLineage(recovered, source, (order,), 64, approved,
                                         {'assignment_id': assignment})


def case(monkeypatch, *, quantity=100, conid=123, ticker='PLUG', lineage_transform=None,
         broker_state_transform=None, mock_oms=True):
    witness, financial, _, _ = prepared_case()
    row, base = transport()
    parent, event = dict(base.intents[0]), dict(base.events[0])
    prefix = V4CommittedPrefix('run', 64, 'prior', 'cursor', 'running', ('prior',))
    day = date(2026, 8, 10)
    state = dict(schema_version=4, bar_mode=True, initial_time=market_day_boundary(day, 0),
        account_ids=('account',), cash={'account': 10000}, realized_pnl={'account': 0},
        positions={'account': [dict(conid=conid, ticker=ticker, quantity=quantity, avg_cost=2.33, realized_pnl=0)]},
        orders=(), next_order_id=1, next_execution_id=1,
        performance_extrema=dict(complete=False, as_of=None, unrealized=0, market_value=0,
            peak_unrealized=0, worst_unrealized=0, equity_peak=0, maximum_drawdown=0))
    if broker_state_transform is not None:
        state = broker_state_transform(state)
    image = project_broker_match_snapshot(run_id='run', session_date=day,
        checkpoint_sequence=64, boundary_ms=witness.boundary_ms, state=state)
    row.update(source_broker_snapshot_id=image.snapshot['snapshot_id'],
               source_broker_snapshot_hash=image.snapshot['content_hash'])
    context = dict(run_id='run', mode='backtest', strategy_id=STRATEGY_ID, strategy_revision=35,
                   account_ids=('account',), session_date=day.isoformat(), evaluation_interval_ms=100)
    cursor = dict(run_id='run', batch_id='prior', event_sequence=64,
                  boundary_ms=witness.boundary_ms, session_date=day.isoformat())
    lineage = (native_lineage(financial, witness),)
    if lineage_transform is not None:
        lineage = lineage_transform(lineage, financial, witness)
    calls = []
    def load_context(client, run_id):
        calls.append(('context', run_id))
        return context
    def load_cursor(client, ceiling):
        calls.append(('cursor', ceiling))
        return cursor
    def load_broker(client, **kwargs):
        calls.append(('broker', kwargs))
        return image
    def load_oms(client, ceiling, **kwargs):
        calls.append(('oms', ceiling, kwargs))
        return lineage
    monkeypatch.setattr('src.trading_runtime.arte_journal_writer.load_typed_run_context', load_context)
    monkeypatch.setattr('src.trading_runtime.arte_journal_projection.load_latest_backtest_cursor', load_cursor)
    monkeypatch.setattr('src.trading_runtime.strategy_one_broker_match_snapshot.load_unattested_broker_match_snapshot', load_broker)
    if mock_oms:
        monkeypatch.setattr('src.trading_runtime.arte_oms_projection.load_recovered_strategy_one_oms_lineage', load_oms)
    return row, parent, event, prefix, financial, image, context, cursor, lineage, calls


def load(data):
    row, parent, event, prefix, financial, *_ = data
    return load_liquidity_fade_financial_checkpoint(None, prefix, row, parent, event, financial,
                                                  first_price_source='pinned')


def test_native_broker_quantity_and_exact_oms_cursor_verified(monkeypatch):
    data = case(monkeypatch)
    proof = load(data)
    assert proof.held_quantity == 100 and proof.conid == 123
    assert proof.broker_snapshot_hash == data[5].snapshot['content_hash']
    assert data[9][-1] == ('oms', replace(data[3], last_sequence=64),
        dict(allowed_accounts=frozenset({'account'}), strategy_number=35, first_price_source='pinned'))
    with pytest.raises(FrozenInstanceError):
        proof.held_quantity = 101


@pytest.mark.parametrize('quantity', [0, -1, 37, 100.00000000000001])
def test_claimed_position_cannot_replace_native_bit_exact_quantity(monkeypatch, quantity):
    with pytest.raises(ValueError, match='native broker checkpoint'):
        load(case(monkeypatch, quantity=quantity))


def test_matching_fractional_quantity_retains_native_float_bits(monkeypatch):
    data = list(case(monkeypatch, quantity=100.00000000000001))
    data[4] = replace(data[4], position_quantity=100.00000000000001)
    data[1]['quantity'] = data[4].position_quantity
    assert load(data).held_quantity == 100.00000000000001


@pytest.mark.parametrize('conid,ticker', [(456, 'PLUG'), (123, 'OTHER')])
def test_ticker_alone_or_wrong_instrument_cannot_supply_position(monkeypatch, conid, ticker):
    with pytest.raises(ValueError, match='exact held instrument'):
        load(case(monkeypatch, conid=conid, ticker=ticker))


@pytest.mark.parametrize('state', [OrderManagementState.WORKING, OrderManagementState.OUTCOME_UNKNOWN,
    OrderManagementState.CANCEL_PENDING, OrderManagementState.WARNING_PENDING, OrderManagementState.CREATED])
def test_native_pending_exit_blocks_even_when_supplied_view_claims_false(monkeypatch, state):
    def changed(items, financial, witness):
        return (*items, native_lineage(financial, witness, action='exit', intent_id='00000000-0000-0000-0000-000000000002', group_id='exit', state=state))
    with pytest.raises(ValueError, match='pending exit'):
        load(case(monkeypatch, lineage_transform=changed))


@pytest.mark.parametrize('state', [OrderManagementState.CANCELLED, OrderManagementState.REJECTED,
                                 OrderManagementState.POLICY_BLOCKED])
def test_terminal_exit_does_not_falsely_block_current_held_position(monkeypatch, state):
    def changed(items, financial, witness):
        return (*items, native_lineage(financial, witness, action='exit', intent_id='00000000-0000-0000-0000-000000000002', group_id='exit', state=state))
    assert load(case(monkeypatch, lineage_transform=changed)).held_quantity == 100


def test_other_assignment_pending_exit_is_not_misattributed(monkeypatch):
    def changed(items, financial, witness):
        return (*items, native_lineage(financial, witness, assignment='other', action='exit', intent_id='00000000-0000-0000-0000-000000000002', group_id='exit'))
    assert load(case(monkeypatch, lineage_transform=changed)).assignment_id == 'assignment'


@pytest.mark.parametrize('field,value', [('source_broker_snapshot_id', '00000000-0000-0000-0000-000000000001'),
                                      ('source_broker_snapshot_hash', 'f'*64)])
def test_changed_broker_root_reference_rejects_before_oms(monkeypatch, field, value):
    data = case(monkeypatch)
    data[0][field] = value
    with pytest.raises(ValueError, match='immutable checkpoint reference'):
        load(data)
    assert len(data[9]) == 3


def test_native_broker_child_tampering_rejects(monkeypatch):
    data = case(monkeypatch)
    data[5].positions[0]['quantity_f64_bits'] += 1
    with pytest.raises(ValueError, match='child hash'):
        load(data)


@pytest.mark.parametrize('field,value', [('mode', 'live'), ('strategy_revision', 34),
    ('session_date', '2026-08-09'), ('account_ids', ('foreign',)), ('evaluation_interval_ms', 1000)])
def test_foreign_runtime_context_rejects_before_broker_read(monkeypatch, field, value):
    data = case(monkeypatch)
    data[6][field] = value
    with pytest.raises(ValueError, match='pinned Backtest context'):
        load(data)
    assert len(data[9]) == 1


@pytest.mark.parametrize('field,value', [('event_sequence', 63), ('boundary_ms', 44_807_300),
    ('session_date', '2026-08-09'), ('batch_id', 'uncommitted')])
def test_wrong_historical_cursor_rejects(monkeypatch, field, value):
    data = case(monkeypatch)
    data[7][field] = value
    with pytest.raises(ValueError, match='exact committed cursor'):
        load(data)
    assert len(data[9]) == 2


@pytest.mark.parametrize('mode', ['missing', 'duplicate', 'oversized', 'foreign_through'])
def test_missing_ambiguous_or_foreign_oms_lineage_rejects(monkeypatch, mode):
    def changed(items, financial, witness):
        if mode == 'missing': return ()
        if mode == 'duplicate': return items*2
        if mode == 'oversized': return items*2001
        return (replace(items[0], through_sequence=63),)
    with pytest.raises(ValueError):
        load(case(monkeypatch, lineage_transform=changed))


def test_installed_strategy35_requires_complete_committed_protection_history(monkeypatch):
    data = case(monkeypatch, mock_oms=False)
    calls = []
    def missing_history(client, prefix, **kwargs):
        calls.append((client, prefix, kwargs))
        raise RuntimeError('Committed protection evidence is missing')
    monkeypatch.setattr(
        'src.trading_runtime.arte_journal_reader.load_complete_typed_protection_history',
        missing_history)
    with pytest.raises(RuntimeError, match='Committed protection evidence is missing'):
        load(data)
    assert calls == [(None, data[3], {'page_size': 1000, 'max_events': 100000})]
