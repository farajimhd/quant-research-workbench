"""Actual prepared manager and journal normalization; native hooks stay closed."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from test_arte_declared_native_command_v4 import case, ATTEMPT, BATCH
from test_backtest_declared_native_fixed_management import Runtime, Evidence, Lookup, rows, held
from test_declared_native_management_command import exit_command, protection_command
from src.backend.backtest_declared_native_fixed_management import DeclaredNativeFixedManagementRunner
from src.trading_runtime.declared_native_management_submission import declared_management_submission
from src.trading_runtime.arte_declared_native_fixed_sources import DeclaredHistoricalPredecessor
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.trading_runtime import arte_declared_native_management_v4 as module

MANAGEMENT_BATCH='55555555-5555-4555-8555-555555555555'

@pytest.fixture(params=['exit','protection','session'])
def packet(case,request):
    c=case;p=c.submission.proposal;prep=c.submission.binding.preparation
    runtime,evidence,lookup=Runtime(),Evidence(),Lookup()
    manager=DeclaredNativeFixedManagementRunner(runtime=runtime,evidence=evidence,
        preparation=prep,tick_for_ticker=lambda ticker:.01)
    refs={}
    for ticker,_ in prep.source.parent.momentum.keys:
        refs[ticker]=dict(source_build_id=c.x.market.build_id,source_market_plan_token=c.x.market.token)
        for stage,name in (('bars','source_bars_attempt_id'),('technical','source_indicators_attempt_id'),('broker_100ms','source_liquidity_attempt_id')):
            refs[ticker][name]=next(u.attempt_id for u in c.x.market.units if u.ticker==ticker and u.stage==stage)
    manager.bind_liquidity_source(lookup,refs)
    bundle=(manager,runtime,evidence,lookup,p,c.submission.financial)
    async def produce():
        if request.param=='protection': return await protection_command(bundle)
        view=await held(bundle)
        if request.param in ('exit','arm'):
            from src.trading_runtime.strategy_liquidity_fade_failure import LiquidityFadeCandle
            if request.param=='arm':
                from src.backend.backtest_declared_native_fixed_management import DeclaredProfitArmReference
                ev,frame=rows(manager,p,40000,bid=p.reference_ask+.1,high=round((2*p.reference_ask-p.initial_stop)*10000))
                evidence.rows[40000]=ev
                await manager.on_management(view,frame,40000)
                candidates=manager.profit_arming_requests(boundary_ms=40000)
                assert len(candidates)==1
                reference=DeclaredProfitArmReference(p.run_id,prep.source.token,candidates[0][0],ATTEMPT,
                    c.record.sequence,BATCH,manager.capture_state(boundary_ms=40000).content_hash())
                manager.accept_profit_arming_references(candidates,(reference,),boundary_ms=40000)
            boundary=45000 if request.param=='arm' else 40000
            ev,frame=rows(manager,p,boundary,bid=p.initial_stop,line=.01,signal=.02)
            frame[10000]={'boundary_ms':boundary//10000*10000,'price_valid':1,
                'close_int':round(p.initial_stop*10000),'macd_line':.01,'macd_signal':.02}
            lookup.windows[(p.ticker,boundary)]=SimpleNamespace(candles=tuple(
                LiquidityFadeCandle(boundary-(3-i)*5000,i) for i in range(4)))
            evidence.rows[boundary]=ev
            await manager.on_management(view,frame,boundary)
            return runtime.commands[-1]
        from src.trading_runtime.entry_spread_risk import exact_epoch_us
        from src.backend.backtest_market_data import market_day_boundary
        at=exact_epoch_us(market_day_boundary(c.submission.binding.session_date,19740000))
        await manager.on_management(view,{100:{'ticker':p.ticker,'boundary_ms':19740000,'quote_valid':1,
            'quote_timestamp_us':at,'bid_int':round(p.reference_ask*10000),
            'ask_int':round((p.reference_ask+.0001)*10000)}},19740000)
        return runtime.commands[-1]
    command=asyncio.run(produce())
    submission=declared_management_submission(c.submission.binding,c.x.managed_spec,command)
    records=c.journal.append_declared_native_management(submission=submission,account_id=submission.account_id,
        strategy_id=p.strategy_id,strategy_revision=p.revision)
    prefix=V4CommittedPrefix(p.run_id,c.record.sequence,BATCH,'fixture:management','running',(BATCH,))
    pred=DeclaredHistoricalPredecessor(p.run_id,MANAGEMENT_BATCH,BATCH,c.record.sequence,command.context.boundary_ms,
        c.x.managed_envelope['payload_hash'],c.x.market.token,prefix,'a'*64,'b'*64)
    args=dict(entry_packet=c.packet,entry_predecessor=c.predecessor,predecessor=pred,**c.args)
    result=module.project_declared_management(records,submission,**args,attempt_id=ATTEMPT,
        batch_id=MANAGEMENT_BATCH,run_month=submission.event_time.date().replace(day=1),source_cursor=prefix.source_cursor)
    return SimpleNamespace(case=c,packet=result,args=args,submission=submission,records=records,bundle=bundle)

def cold(p,packet=None):
    return module.readback_declared_management_transport(p.packet if packet is None else packet,**p.args)

def mutate(p,family,index,field,value):
    families=[(n,list(r)) for n,r in p.packet.families]
    row=dict(families[family][1][index]);row.pop('content_hash');row[field]=value
    families[family][1][index]=module.entry_rows._seal(module.TABLES[family],row)
    return module.DeclaredManagementRows(p.packet.bases,tuple((n,tuple(r)) for n,r in families))

def test_actual_manager_journal_normalized_replay_preserves_own_requests(packet):
    restored=cold(packet)
    assert type(restored.command) is type(packet.submission.command)
    assert module._equal(restored.intents,packet.submission.intents)
    assert module._equal(restored.command.replay(),packet.submission.command.replay())
    assert restored.command.context.source==packet.submission.command.context.source
    assert restored.binding.configuration_hash==packet.submission.binding.configuration_hash
    assert packet.packet.bases[0].events[0]['entity_type']=='declared_native_management_intent'
    assert any('attempt_id=toUUID' in q for q in packet.case.x.source.queries)
    with pytest.raises(TypeError): packet.packet.families[0][1][0]['revision']=1

@pytest.mark.parametrize('field,value',[('managed_spec_hash','f'*64),('configuration_hash','f'*64),
    ('entry_record_id',ATTEMPT),('entry_intent_id',ATTEMPT),('source_liquidity_attempt_id',ATTEMPT),
    ('result_hash','f'*64),('portfolio_state_hash','f'*64),('boundary_ms',40100)])
def test_resealed_foreign_source_scope_or_claim_rejects(packet,field,value):
    with pytest.raises(ValueError): cold(packet,mutate(packet,0,0,field,value))

@pytest.mark.parametrize('field,value',[('revision',9017.),('boundary_ms',True),('pending_exit',False),
    ('position_quantity',10),('configuration_hash',b'f'*64)])
def test_exact_scalar_alias_rejects_before_readback(packet,field,value):
    with pytest.raises(ValueError): mutate(packet,0,0,field,value)

@pytest.mark.parametrize('change',['missing','duplicate','order','hash'])
def test_complete_normalized_inventory_rejects(packet,change):
    families=[(n,list(r)) for n,r in packet.packet.families]
    if change=='missing': families[0][1].clear()
    elif change=='duplicate': families[0][1].append(families[0][1][0])
    elif change=='order': families.reverse()
    else: families[0][1][0]=dict(families[0][1][0],content_hash='f'*64)
    with pytest.raises(ValueError):
        cold(packet,module.DeclaredManagementRows(packet.packet.bases,tuple((n,tuple(r)) for n,r in families)))

@pytest.mark.parametrize('field,value',[('entity_type','strategy_intent'),('correlation_id',ATTEMPT),
    ('causation_id',ATTEMPT),('sequence',99),('account_id','foreign')])
def test_shared_semantic_envelope_does_not_adopt_stored_lineage(packet,field,value):
    bases=list(packet.packet.bases);event=dict(bases[0].events[0],**{field:value})
    bases[0]=replace(bases[0],events=(event,))
    with pytest.raises(ValueError): cold(packet,replace(packet.packet,bases=tuple(bases)))

def test_financial_and_complete_management_source_admission_remains_closed(packet):
    with pytest.raises(ValueError,match='historical'):
        module.verify_declared_management_source_and_financial_admission(packet.packet,**packet.args)
    for table in module.TABLES:
        assert "storage_policy = 'live_market_ssd'" in table.ddl()
        assert all(not any(word in kind for word in ('Array','JSON','Blob')) for _,kind in table.columns)

def test_missing_product_or_resealed_configuration_fails_before_acceptance(packet):
    packet.case.x.source.quote='missing'
    with pytest.raises(ValueError): cold(packet)

def test_unknown_producer_or_structural_field_is_not_silently_dropped():
    with pytest.raises(ValueError,match='normalized contract'): module._mapping({'future_private_feature':1},module._RESOLUTION)
    with pytest.raises(ValueError,match='normalized contract'): module._mapping({'private_rank':1},module._LEVEL)


@pytest.mark.parametrize('packet',['arm'],indirect=True)
def test_actual_prior_confirmed_arm_and_zero_count_candles_roundtrip(packet):
    restored=cold(packet)
    assert restored.command.inputs.prior_arm==packet.submission.command.inputs.prior_arm
    assert restored.command.inputs.candles==packet.submission.command.inputs.candles
    assert tuple(c.trade_count for c in restored.command.inputs.candles)==(0,1,2,3)
    assert module._equal(restored.command.inputs.ten,packet.submission.command.inputs.ten)
    with pytest.raises(ValueError): cold(packet,mutate(packet,4,0,'source_token','f'*64))


@pytest.mark.parametrize('packet',['protection'],indirect=True)
@pytest.mark.parametrize('family,field,value',[(6,'stop',100.),(7,'unified_level_id','foreign'),
    (8,'completed_boundary_ms',40100),(8,'lower',100.)])
def test_protection_predecessor_result_groups_and_future_geometry_drift_reject(packet,family,field,value):
    with pytest.raises(ValueError): cold(packet,mutate(packet,family,0,field,value))


@pytest.mark.parametrize('packet',['exit'],indirect=True)
def test_full_ten_and_liquidity_row_order_missing_versus_zero_are_preserved(packet):
    restored=cold(packet)
    assert module._equal(restored.command.inputs.ten,packet.submission.command.inputs.ten)
    assert restored.command.inputs.candles[0].trade_count==0
    families=[(n,list(r)) for n,r in packet.packet.families]
    families[3][1].reverse()
    with pytest.raises(ValueError):
        cold(packet,module.DeclaredManagementRows(packet.packet.bases,tuple((n,tuple(r)) for n,r in families)))


def test_noncanonical_integer_quantity_cannot_silently_lose_bits():
    with pytest.raises(ValueError,match='losslessly'): module._strict_number(2**53+1)


@pytest.mark.parametrize('packet',['protection'],indirect=True)
def test_plausible_prior_protection_is_only_a_claim_until_historical_hook(packet):
    changed=mutate(packet,6,0,'stop',1.)
    restored=cold(packet,changed)
    assert restored.command.inputs.previous.stop==1.
    assert module._equal(restored.command.replay(),packet.submission.command.replay())
    with pytest.raises(ValueError,match='historical'):
        module.verify_declared_management_source_and_financial_admission(changed,**packet.args)


@pytest.mark.parametrize('packet',['exit','protection'],indirect=True)
@pytest.mark.parametrize('context_qty,completed_qty',[(10.,10),(10,10.)])
def test_completed_quantity_type_roundtrips_independently_from_context_quantity(packet,context_qty,completed_qty):
    original=packet.submission.command
    name='inputs' if type(original) is module.DeclaredExitCommand else 'exit_inputs'
    completed=replace(getattr(original,name).completed,position_quantity=completed_qty)
    command=replace(original,context=replace(original.context,financial=replace(original.context.financial,
        position_quantity=context_qty)),**{name:replace(getattr(original,name),completed=completed)})
    submission=declared_management_submission(packet.submission.binding,packet.submission.spec,command)
    assert type(command.context.financial.position_quantity) is type(context_qty)
    assert type(getattr(command,name).completed.position_quantity) is type(completed_qty)
    normalized=module.project_declared_management(packet.records,submission,**packet.args,
        attempt_id=ATTEMPT,batch_id=MANAGEMENT_BATCH,run_month=submission.event_time.date().replace(day=1),
        source_cursor=packet.args['predecessor'].prefix.source_cursor)
    restored=cold(packet,normalized)
    assert module._equal(getattr(restored.command,name).completed,getattr(command,name).completed)
    assert module._equal(restored.command.context.financial,command.context.financial)
