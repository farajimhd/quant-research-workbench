"""Real pending recomputation and OMS ownership; checkpoint transport is mocked."""
from dataclasses import asdict, replace
from datetime import timedelta, timezone
from types import SimpleNamespace

import pytest

from tests.test_backtest_native_channel_qualification import installed, unit  # noqa: F401
from tests.test_backtest_native_channel_manager_recovery import manager_case
from src.backend.backtest_native_channel_financial_checkpoint import confirm_native_channel_held_checkpoint
from src.backend.backtest_native_channel_manager_recovery import reprove_native_channel_manager_snapshot
from src.backend.backtest_typed_publisher import TypedBacktestReceipt
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.trading_runtime.arte_intent_projection import RecoveredIntent
from src.trading_runtime.arte_oms_projection import RecoveredOmsGroupState, RecoveredStrategyOneOmsLineage
from src.trading_runtime.ibkr_schema import OrderRequest
from src.trading_runtime.signals import StrategyIntent
from src.trading_runtime.strategy_one_contract import STRATEGY_ID
from src.trading_runtime.strategy_one_management_snapshot import ManagerSnapshotHead, restore_manager_snapshot
from src.trading_runtime.strategy_one_broker_match_snapshot import BrokerMatchHead, project_broker_match_snapshot
from src.trading_runtime.order_management import OrderManagementState


def checkpoint_case(installed, monkeypatch, defect=None, *, return_scope=False, return_history=False):
    rows, policy, contexts = manager_case(installed)
    root = rows.snapshot
    args = next(iter(contexts.values()))
    native_conid = args['expected_conid']
    requests = reprove_native_channel_manager_snapshot(rows, policy, contexts)
    if defect == 'source_conid': args['expected_conid'] += 1
    if defect == 'original_entry': args['original_entry'] = replace(args['original_entry'], initial_target=args['original_entry'].initial_target+1)
    request, financial = requests[0], requests[0].financial
    state = restore_manager_snapshot(rows.inherited)
    run, sequence, batch = root['run_id'], root['checkpoint_sequence'], rows.pending[0]['batch_id']
    prefix = V4CommittedPrefix(run, sequence, batch, 'cursor', 'running', (batch,))
    receipt = TypedBacktestReceipt(sequence, batch, 'cursor')
    native = dict(schema_version=4, bar_mode=True, initial_time=args['as_of'],
        account_ids=(financial.account_id,), cash={financial.account_id:10000},
        realized_pnl={financial.account_id:0}, positions={financial.account_id:[dict(
            conid=native_conid, ticker=financial.ticker, quantity=financial.position_quantity,
            avg_cost=1.0, realized_pnl=0)]}, orders=(), next_order_id=1, next_execution_id=2,
        performance_extrema=dict(complete=False,as_of=None,unrealized=0,market_value=0,
            peak_unrealized=0,worst_unrealized=0,equity_peak=0,maximum_drawdown=0))
    if defect == 'quantity': native['positions'][financial.account_id][0]['quantity'] += 1
    broker = project_broker_match_snapshot(run_id=run, session_date=args['session_date'],
        checkpoint_sequence=sequence, boundary_ms=state.boundary_ms, state=native)
    intent = StrategyIntent(request.acquisition.source_entry_intent_id, financial.ticker,
        args['as_of'], 'enter_long', financial.position_quantity, 1.0)
    source = RecoveredIntent(1, financial.account_id, 'entry-record', batch, intent)
    order = OrderRequest(acctId=financial.account_id,conid=native_conid,ticker=financial.ticker,
        orderType='LMT',side='BUY',quantity=financial.position_quantity,price=1.0,cOID='entry-client')
    group = dict(run_id=run,batch_id=batch,group_id=request.acquisition.source_group_id,
        account_id=financial.account_id,strategy_id=STRATEGY_ID,strategy_revision=1,
        strategy_intent_id=intent.intent_id,state=OrderManagementState.WORKING.value)
    native_group = args['group']
    group['state'] = native_group.state.value
    group.update(submitted_at=native_group.submitted_at.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f'),
        updated_at=native_group.updated_at.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f'),
        filled_quantity=str(native_group.filled_quantity),remaining_quantity=str(native_group.remaining_quantity))
    bindings = tuple(dict(broker_order_id=identity,request_index=0) for identity in native_group.broker_order_ids)
    if defect == 'oms_fill_quantity': group['filled_quantity'] = str(native_group.filled_quantity+1)
    if defect == 'oms_state': group['state'] = next(s.value for s in OrderManagementState if s != native_group.state)
    if defect == 'binding': bindings += (dict(broker_order_id='foreign-order',request_index=0),)
    if defect == 'group': group['group_id'] = 'foreign'
    recovered = RecoveredOmsGroupState(2, source.record_id, group, (order,), (0,), ('entry',), bindings, (), ())
    lineage = (RecoveredStrategyOneOmsLineage(recovered,source,(order,),sequence,
        replace(intent,metadata={'assignment_id':financial.assignment_id}),
        {'assignment_id':financial.assignment_id}),)
    if defect == 'pending_exit':
        exit_intent = replace(intent,intent_id='exit-intent',action='exit_long')
        exit_source = replace(source,record_id='exit-record',intent=exit_intent)
        exit_group = {**group,'group_id':'exit','strategy_intent_id':'exit-intent'}
        lineage += (replace(lineage[0],state=replace(recovered,intent_record_id='exit-record',group=exit_group),
            source_intent=exit_source,approved_intent=replace(exit_intent,metadata={'assignment_id':financial.assignment_id})),)
    context = dict(run_id=run,mode='backtest',strategy_id=STRATEGY_ID,strategy_revision=1,
        account_ids=(financial.account_id,),session_date=root['session_date'],evaluation_interval_ms=100)
    if defect == 'context': context['strategy_revision'] = 2
    cursor = dict(run_id=run,event_sequence=sequence,batch_id=batch,boundary_ms=state.boundary_ms,session_date=root['session_date'])
    if defect == 'cursor': cursor['boundary_ms'] += 100
    if defect == 'prefix': prefix = replace(prefix,last_sequence=sequence+1)
    prefix_reads = []
    def read_prefix(*_a, **_k):
        prefix_reads.append(True)
        return replace(prefix,last_sequence=sequence+1) if defect=='frontier_changed' and len(prefix_reads)>1 else prefix
    monkeypatch.setattr('src.trading_runtime.arte_journal_commit_v4.load_verified_v4_prefix',read_prefix)
    monkeypatch.setattr('src.trading_runtime.arte_journal_projection.load_latest_backtest_cursor',lambda *_a:cursor)
    monkeypatch.setattr('src.trading_runtime.arte_journal_writer.load_typed_run_context',lambda *_a:context)
    monkeypatch.setattr('src.trading_runtime.native_channel_manager_reader.load_attested_native_channel_manager_snapshot',lambda *_a,**_k:(rows,state,requests))
    monkeypatch.setattr('src.trading_runtime.strategy_one_broker_match_snapshot.load_attested_broker_match_snapshot',lambda *_a,**_k:broker)
    monkeypatch.setattr('src.trading_runtime.arte_oms_projection.load_recovered_strategy_one_oms_lineage',lambda *_a,**_k:lineage)
    execution = args['executions'][0]
    if defect == 'execution_changed': execution = replace(execution,trade_time=execution.trade_time+timedelta(milliseconds=1),trade_time_r=execution.trade_time_r+1)
    if defect == 'execution_future': execution = replace(execution,trade_time=args['as_of']+timedelta(seconds=1))
    payload = asdict(execution)
    payload['trade_time'] = execution.trade_time.isoformat()
    monkeypatch.setattr('src.backend.backtest_v4_execution_restore.load_v4_broker_executions',lambda *_a,**_k:[] if defect=='execution_missing' else [payload])
    monkeypatch.setattr('src.trading_runtime.numbered_fixed_strategy.numbered_fixed_strategy',lambda n:SimpleNamespace(native_channel_management_policy=policy if n==1 else None))
    class Keeper:
        def __init__(self,head): self.head,self.reads=head,0
        def read_head(self,**_scope):
            self.reads += 1
            return replace(self.head,keeper_version=1) if defect=='head_changed' and self.reads>1 else self.head
    manager = Keeper(ManagerSnapshotHead(run,sequence,batch,root['content_hash'],0))
    keeper = Keeper(BrokerMatchHead(run,sequence,batch,broker.snapshot['content_hash'],0))
    client = SimpleNamespace(native_channel_management_policy=policy)
    if return_history:
        from src.backend.backtest_native_channel_exit_projection import NativeChannelExitCheckpointReference
        monkeypatch.setattr('src.trading_runtime.native_channel_manager_reader.load_unattested_native_channel_manager_rows',
            lambda *_a,**_k:rows)
        monkeypatch.setattr('src.trading_runtime.strategy_one_management_snapshot.attach_committed_momentum_sources',
            lambda *_a,**_k:state)
        monkeypatch.setattr('src.trading_runtime.strategy_one_broker_match_snapshot.load_unattested_broker_match_snapshot',
            lambda *_a,**_k:broker)
        return dict(client=client,prefix=prefix,reference=NativeChannelExitCheckpointReference(
            root['snapshot_id'],sequence,root['content_hash'],broker.snapshot['snapshot_id'],
            broker.snapshot['content_hash']),policy=policy,contexts=contexts,
            first_price_source='transport-test-only')
    if return_scope:
        return dict(client=client,manager_keeper=manager,broker_keeper=keeper,receipt=receipt,
            run_id=run,management_policy=policy,contexts=contexts,
            first_price_source='transport-test-only')
    return lambda: confirm_native_channel_held_checkpoint(client,manager,keeper,receipt,
        run_id=run,policy=policy,contexts=contexts,first_price_source='transport-test-only')


def test_pending_channel_binds_actual_held_quantity_and_both_checkpoint_roots(installed,monkeypatch):
    result = checkpoint_case(installed,monkeypatch)()
    assert len(result)==1 and result[0].held_quantity==5
    assert result[0].conid>0 and result[0].checkpoint.source_manager_checkpoint_sequence==12


@pytest.mark.parametrize('defect',['quantity','pending_exit','group','context','cursor','prefix','head_changed','frontier_changed','source_conid','original_entry','execution_changed','execution_future','execution_missing','oms_fill_quantity','oms_state','binding'])
def test_channel_held_confirmation_rejects_foreign_or_changed_native_financial_authority(installed,monkeypatch,defect):
    with pytest.raises(ValueError): checkpoint_case(installed,monkeypatch,defect)()
