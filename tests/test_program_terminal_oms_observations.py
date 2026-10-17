"""Pure terminal OMS observation projection retains native cancelled fingerprints."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import pytest
from src.backend.program_terminal_portfolio_rollforward import rollforward_terminal_oms_observations
from src.backend.program_terminal_financial_rollforward import TerminalClosingTailScope,RecordedClosingCancellation
from src.trading_runtime.arte_oms_actor_restore import reconstruct_typed_oms_actor_image
from src.trading_runtime.order_management import _live_order_state_fingerprint,OrderManagementState
from src.trading_runtime.arte_broker_match_snapshot import project_shared_broker_snapshot
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter,SimulationConfig
from src.trading_runtime.backtest_broker_contract import BacktestBrokerContract,project_broker_contract
from src.trading_runtime.domain import TradingMode
from src.backend.backtest_market_data import market_day_boundary
from tests.test_arte_oms_actor_restore import _source,AT,RUN,STRATEGY_ID,STRATEGY_NUMBER


def cancelled_image():
    lineage,history=_source()
    request=lineage.orders[0]
    broker=SimulatedBrokerAdapter(['DU1'],mode=TradingMode.BACKTEST,initial_time=AT,fixed_bar_mode=True)
    async def produce():
        await broker.initialize();await broker.place_orders('DU1',[request]);before=(await broker.live_orders())[0]
        await broker.cancel_order('DU1',before.orderId);after=(await broker.live_orders())[0]
        return before,after
    before,after=asyncio.run(produce())
    # The fixture's declared logical broker binding is replaced with the exact
    # native numeric broker ID, preserving its recorded group/request identity.
    old=reconstruct_typed_oms_actor_image((lineage,),history,run_id=RUN,strategy_id=STRATEGY_ID,strategy_revision=STRATEGY_NUMBER,through_sequence=7,cutoff_at=AT)
    group=old.groups['group-1'];old_id=group.broker_order_ids[0];identity=before.orderId
    group.broker_order_ids=[identity]
    for name in ('broker_order_request_indexes','broker_order_roles','broker_order_slices','filled_by_broker_order'):
        values=getattr(group,name)
        if old_id in values:values[identity]=values.pop(old_id)
    old.group_by_broker_id.clear();old.group_by_broker_id[identity]='group-1'
    group.broker_order_state_fingerprints={identity:_live_order_state_fingerprint(before)}
    final=deepcopy(old);new=final.groups['group-1'];new.broker_order_state_fingerprints={};new.terminal_broker_order_ids.add(identity);new.state=OrderManagementState.CANCELLED
    stop=AT+timedelta(seconds=1);boundary=int((stop-market_day_boundary(stop.date(),0)).total_seconds()*1000)
    contract=BacktestBrokerContract(project_broker_contract(RUN,broker.config,liquidity_plan_hash='a'*64),broker.config.initial_cash)
    snapshot=project_shared_broker_snapshot(run_id=RUN,session_date=stop.date(),checkpoint_sequence=8,boundary_ms=boundary,state=broker.checkpoint_state(),simulation_config=broker.config,broker_contract=contract)
    scope=TerminalClosingTailScope(RUN,8,8,AT,stop,('AAA',),'a'*64,'b'*64,'c'*64,'d'*64,1,'e'*64)
    cancellation=RecordedClosingCancellation(8,stop,identity,'DU1','f'*64)
    return old,final,dict(scope=scope,closing_orders=(after,),executions=(),cancellations=(cancellation,),broker_snapshot=snapshot)


def test_native_cancelled_observation_matches_owner_and_preserves_original_image():
    old,final,kwargs=cancelled_image();before=deepcopy(old)
    result=rollforward_terminal_oms_observations(old,final,**kwargs)
    identity=kwargs['closing_orders'][0].orderId
    assert result.image.groups['group-1'].broker_order_state_fingerprints[identity]==_live_order_state_fingerprint(kwargs['closing_orders'][0])
    assert result.observations.root['observation_count']==1
    assert old.groups['group-1'].broker_order_state_fingerprints == before.groups['group-1'].broker_order_state_fingerprints
    assert old.groups['group-1'].terminal_broker_order_ids == before.groups['group-1'].terminal_broker_order_ids
    assert not final.groups['group-1'].broker_order_state_fingerprints


@pytest.mark.parametrize('change',['missing_order','wrong_account','warning','unconfirmed','old_fingerprint'])
def test_terminal_oms_rejects_incomplete_confirmed_observation(change):
    old,final,kwargs=cancelled_image()
    if change=='missing_order':kwargs['closing_orders']=()
    elif change=='wrong_account':kwargs['closing_orders']=(replace(kwargs['closing_orders'][0],account='foreign'),)
    elif change=='warning':kwargs['closing_orders']=(replace(kwargs['closing_orders'][0],statusDescription='Request was submitted'),)
    elif change=='unconfirmed':final.groups['group-1'].terminal_broker_order_ids.clear()
    else:old.groups['group-1'].broker_order_state_fingerprints.clear()
    with pytest.raises((ValueError,RuntimeError)):rollforward_terminal_oms_observations(old,final,**kwargs)

