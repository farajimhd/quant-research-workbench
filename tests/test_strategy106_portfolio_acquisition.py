"""Real native normalized definition and Portfolio fence; in-memory SQL transport only."""
import asyncio,json,re
from dataclasses import replace,fields
from datetime import datetime,timedelta,time
from hashlib import sha256
from copy import deepcopy
from pathlib import Path
import pytest
from tests.test_fixed_structural_lot_configuration_routing import source_fixture
from tests.test_strategy_fifty_release import APPROVAL
from tests.test_arte_backtest_definition import _definition,RUN_MONTH
from tests.test_portfolio_management import summary,ledger,intent
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_portfolio_acquisition_scope import issue_native_acquisition_authority,NativeSessionAcquisitionAuthority
from src.backend.backtest_v4_run_context import historical_runtime_config,fixed_v4_context_rows
from src.trading_runtime.runtime import RunMode
from src.trading_runtime.arte_backtest_definition import prepare_backtest_definition,TABLES
from src.trading_runtime.arte_journal_writer import typed_row,_canonical_typed_content
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.portfolio import PortfolioManagementEngine,PortfolioAccountProfile,PortfolioPolicy,PortfolioDecisionStatus
from src.trading_runtime.strategy_one_hundred_five_release import derive_strategy_one_hundred_five_configuration
from src.trading_runtime.strategy_one_hundred_six_release import derive_strategy_one_hundred_six_configuration,verify_prepared_strategy_one_hundred_six_configuration
from src.trading_runtime.strategy_one_hundred_six_contract import strategy_one_hundred_six_contract
from src.trading_runtime.strategy_registry import initialize_numbered_fixed_strategies

RUN='00000000-0000-0000-0000-000000000106'

class Transport:
    def __init__(self,tables):self.tables=tables;self.queries=[]
    def execute(self,sql):
        assert sql.startswith('SELECT ') and "WHERE run_id='"+RUN+"'" in sql
        self.queries.append(sql)
        table=sql.split('FROM arte.')[1].split(' ')[0]
        return '\n'.join(json.dumps(r,default=str) for r in self.tables[table])

def issued(start=None,end=None,actual_parent=False):
    initialize_numbered_fixed_strategies()
    parent=source_fixture();account_id='DU1'
    if actual_parent:
        from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
        artifact=Path('D:/TradingML/runtimes/strategy-optimization-20261005/strategy101-exact42-parent-configuration-v1.json')
        parent=CertifiedStrategyOneConfiguration(**json.loads(artifact.read_text(encoding='utf-8'))['certificate'])
        from src.backend.backtest_v4_run_context import historical_simulated_account_ids
        account_id=historical_simulated_account_ids(mode=RunMode.BACKTEST,configuration=parent.payload)[0]
    tree=derive_strategy_one_hundred_six_configuration(parent,**APPROVAL)
    revision={'revision_id':'strategy-one-106:00000000-0000-0000-0000-000000000106',
        'content_hash':tree['payload_hash'],'payload':tree['payload']}
    market={'token':'c'*64,'build_id':'build-1','price_level_plan_token':'d'*64,'price_level_unit_count':2,
        'execution_interval':{'milliseconds':100},'strategy_one_candidate_rule_digest':__import__('src.trading_runtime.strategy_one_candidate_schema',fromlist=['RULE_DIGEST']).RULE_DIGEST}
    for key,value in [('pivot','PRODUCT_DIGEST'),('hod','PRODUCT_DIGEST'),('entry_evidence','PRODUCT_DIGEST'),('v7_interval','PRODUCT_DIGEST')]:
        module=__import__('src.trading_runtime.strategy_one_'+key+'_schema',fromlist=[value])
        market['strategy_one_'+('entry' if key=='entry_evidence' else key)+'_digest']=getattr(module,value)
    for key in ['candidate','identity','scan_query_sha256','pivot','activation','hod','entry','v7_interval']:
        market['strategy_one_'+key+('' if key=='scan_query_sha256' else '_token')]='e'*64
    definition=_definition(configuration_revision=revision,market_data_plan=market,initial_cash=10000.)
    prepared=prepare_backtest_definition(RUN,definition,run_month=RUN_MONTH)
    if start is not None:definition=replace(definition,start_time=start,end_time=end)
    prepared=prepare_backtest_definition(RUN,definition,run_month=RUN_MONTH)
    config=historical_runtime_config(mode=RunMode.BACKTEST,configuration=tree['payload'],account_ids=(account_id,),
        anchor_date=definition.session_date,run_id=RUN)
    run,runtime=fixed_v4_context_rows(config,execution_interval='100ms',configuration_hash=tree['payload_hash'],
        code_hash='b'*64,market_plan_token=market['token'],started_at=datetime.fromisoformat('2026-09-01T12:00:00+00:00'))
    runtime={k:int(v) if type(v) is bool else v for k,v in runtime.items()}
    configrow=typed_row('trading_runtime_config_v1',{'run_id':RUN,'run_month':RUN_MONTH.isoformat(),**runtime})
    account=typed_row('trading_run_account_v1',{'run_id':RUN,'run_month':RUN_MONTH.isoformat(),'ordinal':0,'account_id':account_id})
    from src.trading_runtime.arte_journal_writer import _datetime_wire
    run['started_at']=_datetime_wire(run['started_at'],6)
    fence=dict(run_id=RUN,run_month=RUN_MONTH.isoformat(),run_hash=sha256(canonical_json(_canonical_typed_content('trading_run_v1',run,stored_utc=True)).encode()).hexdigest(),
        config_hash=configrow['content_hash'],account_count=1,account_hash=sha256(canonical_json([(0,account['content_hash'])]).encode()).hexdigest())
    tablekeys=('definition','tickers','assignments','price_plan','commit')
    tables={table.name:(list(prepared[k]) if k in ('tickers','assignments') else [prepared[k]]) for table,k in zip(TABLES,tablekeys)}
    tables.update(trading_run_v1=[run],trading_runtime_config_v1=[configrow],trading_run_account_v1=[account],trading_run_context_commit_v1=[fence])
    client=Transport(tables)
    authority=issue_native_acquisition_authority(client,definition=definition,run_id=RUN,account_ids=(account_id,))
    return authority,definition,client,tree

def test_complete105_tree_preserved_except106_identity_and_quota():
    initialize_numbered_fixed_strategies();parent=source_fixture()
    before=derive_strategy_one_hundred_five_configuration(parent,**APPROVAL);after=derive_strategy_one_hundred_six_configuration(parent,**APPROVAL)
    restored=deepcopy(after['payload']);restored['strategy']['parameters'].pop('session_acquisition_quota')
    for section,keys in {'strategy':('strategy_number','revision','profile_id','profile_revision','name','numbered_release'),
        'strategy_profile':('profile_id','revision','definition_revision','name','description'),'run_plan':('profile_id','name','description')}.items():
        for key in keys:restored[section][key]=before['payload'][section][key]
    assert restored==before['payload']
    assert verify_prepared_strategy_one_hundred_six_configuration(parent,after['payload'])==after
    assert strategy_one_hundred_six_contract().session_acquisition_quota.maximum==1
    after['payload']['strategy']['parameters']['sizing']['unexpected']=1
    with pytest.raises(ValueError,match='whole inherited tree'):verify_prepared_strategy_one_hundred_six_configuration(parent,after['payload'])

def test_original_committed_definition_and_cursor_independence():
    scope,definition,client,_=issued()
    assert scope.scopes['DU1'].begins_at==definition.requested_start
    # Advancing caller playback cursor never participates in issuance.
    resumed_cursor=definition.requested_start+timedelta(hours=1)
    assert resumed_cursor>scope.scopes['DU1'].begins_at
    again=issue_native_acquisition_authority(client,definition=definition,run_id=RUN,account_ids=('DU1',))
    assert again.definition_hash==scope.definition_hash and again.scopes==scope.scopes
    with pytest.raises(ValueError,match='original committed'):
        issue_native_acquisition_authority(client,definition=replace(definition,start_time=time(5,0)),run_id=RUN,account_ids=('DU1',))
    client.tables['trading_backtest_definition_v1'][0]['start_local_ms']+=100
    with pytest.raises(ValueError,match='content hash'):
        issue_native_acquisition_authority(client,definition=definition,run_id=RUN,account_ids=('DU1',))

def engine(authority,clock,journal=None,recovery=None):
    journal=journal or BacktestMemoryJournal(run_id=RUN)
    policy=PortfolioPolicy(maximum_position_fraction=1.,maximum_ticker_fraction=1.,maximum_planned_risk_fraction=.5,maximum_open_risk_fraction=.5,entry_fee_buffer_bps=0.)
    portfolio=PortfolioManagementEngine((PortfolioAccountProfile('cash','DU1','backtest','simulated',policy),),journal=journal,
        run_id=RUN,strategy_id='early-squeeze-strategy',strategy_revision=106,event_clock=lambda:clock[0],
        session_acquisition_authority=authority,typed_recovery=recovery)
    portfolio.synchronize_snapshot('DU1',summary=summary('DU1',equity=10000,available=10000,at=clock[0]),ledger=ledger('DU1',cash=10000,at=clock[0]),positions=[])
    return portfolio,journal

def test_real_portfolio_fence_retry_release_other_ticker_and_rejection():
    authority,definition,_,_=issued();clock=[definition.requested_start+timedelta(seconds=1)]
    portfolio,journal=engine(authority,clock)
    async def exercise():
        first=replace(intent('first',ticker='AAA',quantity=10,price=10,invalidation=9),event_time=clock[0])
        decision,approved=await portfolio.approve(first,account_id='DU1')
        assert decision.status==PortfolioDecisionStatus.APPROVED and approved is not None
        assert len(portfolio.reservations)==1  # Portfolio aggregate reservation; OMS child graph is not exercised here.
        portfolio.release_intent('first',reason='controlled_unfilled_release')
        clock[0]+=timedelta(seconds=1)
        same,duplicate=await portfolio.approve(first,account_id='DU1')
        assert 'entry_request_already_allocated' in same.reasons and duplicate is None
        rejected,none=await portfolio.approve(replace(first,intent_id='second',event_time=clock[0]),account_id='DU1')
        assert rejected.reasons==('session_acquisition_quota_reached',) and none is None
        assert len(portfolio.reservations)==1
        other,ok=await portfolio.approve(replace(first,intent_id='other',ticker='BBB',event_time=clock[0]),account_id='DU1')
        assert other.status==PortfolioDecisionStatus.APPROVED and ok is not None
    try:asyncio.run(exercise())
    finally:journal.close()


def test_forged_or_mutated_scope_rejected():
    authority,definition,_,_=issued();clock=[definition.requested_start+timedelta(seconds=1)]
    fake=NativeSessionAcquisitionAuthority(authority.run_id,authority.configuration_hash,authority.definition_hash,authority.strategy_id,authority.strategy_revision,authority.scopes)
    with pytest.raises(ValueError,match='issued authority'):engine(fake,clock)
    object.__setattr__(authority.scopes['DU1'],'begins_at',clock[0])
    with pytest.raises(ValueError,match='content differs'):engine(authority,clock)


def test_rejected_request_does_not_consume_and_legacy_none_remains_unlimited():
    authority,definition,_,_=issued();clock=[definition.requested_start+timedelta(seconds=1)]
    async def run(selected):
        portfolio,journal=engine(selected,clock)
        try:
            bad=replace(intent('bad',ticker='AAA',quantity=10,price=0,invalidation=0),event_time=clock[0])
            rejected,none=await portfolio.approve(bad,account_id='DU1')
            assert none is None and not portfolio.reservations
            first=replace(bad,intent_id='valid',quantity=10,reference_price=10,invalidation_price=9)
            _,accepted=await portfolio.approve(first,account_id='DU1');assert accepted is not None
            portfolio.release_intent('valid',reason='release')
            _,second=await portfolio.approve(replace(first,intent_id='next'),account_id='DU1')
            assert (second is None)==(selected is not None)
        finally:journal.close()
    asyncio.run(run(authority));asyncio.run(run(None))


@pytest.mark.parametrize('terminal',[False,True])
def test_real_normalized_portfolio_cold_recovery_preserves_released_acquisition(terminal):
    from tests.test_arte_portfolio_snapshot_persistence import SnapshotClient
    from src.trading_runtime.arte_portfolio_snapshot import prepare_captured_portfolio_snapshot,publish_prepared_portfolio_snapshot
    from src.trading_runtime.arte_portfolio_recovery import recover_portfolio_engine_state
    authority,definition,base,_=issued();clock=[definition.requested_start+timedelta(seconds=1)]
    portfolio,journal=engine(authority,clock)
    async def accept():
        first=replace(intent('accepted',ticker='AAA',quantity=10,price=10,invalidation=9),event_time=clock[0])
        _,approved=await portfolio.approve(first,account_id='DU1');assert approved is not None
        portfolio.release_intent('accepted',reason='released_but_acquired')
    try:
        asyncio.run(accept())
        if terminal:clock[0]=definition.session_end
        packet=prepare_captured_portfolio_snapshot(portfolio.capture_recovery_snapshot('DU1',state_revision=7,snapshot_at=clock[0]))
        snapshots=SnapshotClient()
        publish_prepared_portfolio_snapshot(snapshots,packet)
        class ColdTransport(Transport):
            def execute(self,sql):
                table=sql.split('FROM arte.')[1].split(' ')[0]
                if table in self.tables:return super().execute(sql)
                return snapshots.execute(sql)
        cold=ColdTransport(base.tables)
        restored=recover_portfolio_engine_state(cold,run_id=RUN,profiles=tuple(s.profile for s in portfolio.states.values()),
            state_revisions={'DU1':7},cutoff_at=clock[0])
        assert restored.reservations and all(r.status=='released' for r in restored.reservations.values())
        original=issue_native_acquisition_authority(cold,definition=definition,run_id=RUN,account_ids=('DU1',))
        if not terminal:clock[0]+=timedelta(seconds=1)
        recovered,newjournal=engine(original,clock,recovery=restored)
        try:
            blocked=replace(intent('cold-new',ticker='AAA',quantity=10,price=10,invalidation=9),event_time=clock[0])
            if terminal:
                with pytest.raises(ValueError,match='causal session clock'):asyncio.run(recovered.approve(blocked,account_id='DU1'))
                from src.trading_runtime.portfolio_acquisition_limit import require_owned_session_history
                with pytest.raises(ValueError,match='invalid session clock'):
                    require_owned_session_history(original.scopes['DU1'],tuple(restored.reservations.values()),run_id=RUN,at=clock[0]+timedelta(milliseconds=100))
            else:
                decision,none=asyncio.run(recovered.approve(blocked,account_id='DU1'))
                assert none is None and decision.reasons==('session_acquisition_quota_reached',)
        finally:newjournal.close()
        with pytest.raises(ValueError,match='original committed'):
            issue_native_acquisition_authority(cold,definition=replace(definition,start_time=time(5,0)),run_id=RUN,account_ids=('DU1',))
    finally:journal.close()


def test_issued_scope_rejects_foreign_engine_revision():
    authority,definition,client,_=issued()
    journal=BacktestMemoryJournal(run_id=RUN)
    try:
        with pytest.raises(ValueError,match='ownership/content'):
            PortfolioManagementEngine((PortfolioAccountProfile('cash','DU1','backtest','simulated',PortfolioPolicy()),),
                journal=journal,run_id=RUN,strategy_id=authority.strategy_id,strategy_revision=105,
                event_clock=lambda:definition.requested_start,session_acquisition_authority=authority)
    finally:journal.close()


def test_generic_compiler_respects_explicit_declared_maximum_two():
    from src.trading_runtime.fixed_structural_lot_release_v25 import derive_fixed_structural_lot_release,verify_prepared_fixed_structural_lot_release
    from src.trading_runtime.portfolio_acquisition_contract import SessionAcquisitionQuotaPolicy
    from src.trading_runtime.strategy_one_hundred_five_release import release_contract as inherited_release
    from src.trading_runtime.strategy_one_hundred_six_release import release_contract
    parent=source_fixture();policy=SessionAcquisitionQuotaPolicy(maximum=2)
    arguments=dict(inherited_derive=derive_strategy_one_hundred_five_configuration,inherited_release=inherited_release(),release=release_contract(),acquisition_policy=policy)
    result=derive_fixed_structural_lot_release(parent,**arguments,**APPROVAL)
    assert result['payload']['strategy']['parameters']['session_acquisition_quota']['maximum_accepted_acquisitions_per_ticker_session']==2
    assert verify_prepared_fixed_structural_lot_release(parent,result['payload'],**arguments)==result
    assert strategy_one_hundred_six_contract().session_acquisition_quota.maximum==1


@pytest.mark.parametrize('start,end',[(time(4),time(9,30)),(time(16),time(20)),(time(5),time(9,30))])
def test_fresh_controller_exact_portfolio_clock_callsite(tmp_path,start,end):
    import ast,inspect,textwrap
    import src.backend.replay_run_service as service
    authority,definition,client,_=issued(start,end)
    controller=service.ReplayRunController(definition,run_id=RUN,runtime_root=tmp_path)
    # Execute the actual clock expression from the production constructor call;
    # unrelated account/planner fixture authority is outside this bounded test.
    tree=ast.parse(textwrap.dedent(inspect.getsource(service.ReplayRunController._initialize_runtime)))
    calls=[n for n in ast.walk(tree) if type(n) is ast.Call and type(n.func) is ast.Name and n.func.id=='PortfolioManagementEngine']
    assert len(calls)==1
    expression=next(k.value for k in calls[0].keywords if k.arg=='event_clock')
    selected_clock=eval(compile(ast.Expression(expression),'<production-clock>','eval'),{'self':controller,'session_acquisition_authority':authority})
    journal=BacktestMemoryJournal(run_id=RUN)
    try:
        assert controller.current_time is None
        portfolio=PortfolioManagementEngine((PortfolioAccountProfile('cash','DU1','backtest','simulated',PortfolioPolicy()),),
            journal=journal,run_id=RUN,strategy_id=authority.strategy_id,strategy_revision=authority.strategy_revision,
            event_clock=selected_clock,session_acquisition_authority=authority)
        assert portfolio._event_time()==definition.requested_start
        controller.current_time=definition.requested_start+timedelta(minutes=1)
        assert portfolio._event_time()==controller.current_time
        assert portfolio.session_acquisition_authority.scopes['DU1'].begins_at==definition.requested_start
        controller.current_time=None
        legacy_clock=eval(compile(ast.Expression(expression),'<production-clock>','eval'),{'self':controller,'session_acquisition_authority':None})
        assert legacy_clock()==definition.session_start
    finally:journal.close()


def test_exact_retained105_source_restoration_and_unsealed_gate():
    import ast,subprocess
    from pathlib import Path
    from src.backend.backtest_fixed_structural_lot_compatibility_v25 import REVIEWED_EDITS,restore_reviewed_parent_source
    from src.backend.backtest_fixed_structural_lot_certification_v25 import certify_fixed_structural_lot_source
    root=Path(__file__).resolve().parents[1]
    for relative in REVIEWED_EDITS:
        current=(root/'src'/relative).read_text(encoding='utf-8')
        actual=restore_reviewed_parent_source(current,relative)
        old=subprocess.check_output(['git','show','6a154b3a57a949ecd109331d5921934dc2d85fae:src/'+relative],cwd=root).decode()
        assert bool(ast.dump(ast.parse(actual))==ast.dump(ast.parse(old))),relative
        mutated=current+'\nforeign_executable=1\n'
        assert bool(restore_reviewed_parent_source(mutated,relative)==mutated),relative
        assert ast.dump(ast.parse(mutated))!=ast.dump(ast.parse(old))
    import src.backend.backtest_fixed_structural_lot_certification_v25 as seal
    if seal.REVIEWED_SOURCE_AST:
        assert len(certify_fixed_structural_lot_source())==64
    else:
        with pytest.raises(ValueError,match='unapproved'):certify_fixed_structural_lot_source()


@pytest.mark.parametrize('start,end',[(time(4),time(9,30)),(time(16),time(20)),(time(5),time(9,30))])
def test_actual_initialize_runtime_portfolio_review_path(monkeypatch,tmp_path,start,end):
    from types import SimpleNamespace
    import src.backend.replay_run_service as service
    authority,definition,client,_=issued(start,end,actual_parent=True)
    # External watchlist/signal producers are out of this original-window fixture.
    monkeypatch.setattr(service,'_historical_watchlist_plans_for_configuration',lambda *a,**k:[])
    monkeypatch.setattr(service,'_historical_core_signal_plans_for_configuration',lambda *a,**k:[])
    controller=service.ReplayRunController(definition,run_id=RUN,runtime_root=tmp_path)
    controller._journal=BacktestMemoryJournal(run_id=RUN);controller._journal_writer=SimpleNamespace(_client=client)
    captured=[];original=service.PortfolioManagementEngine
    def capture(*args,**kwargs):
        value=original(*args,**kwargs);captured.append(value);return value
    class ReachedBroker(Exception):pass
    def broker_boundary(*args,**kwargs):raise ReachedBroker()
    # Canonical assignment producer is external to this clock test; review has no actions.
    monkeypatch.setattr(controller,'_selected_assignments',lambda:[])
    monkeypatch.setattr(service,'PortfolioManagementEngine',capture)
    monkeypatch.setattr(service,'SimulatedBrokerAdapter',broker_boundary)
    try:
        with pytest.raises(ReachedBroker):asyncio.run(controller._initialize_runtime(record_configuration=False,record_lifecycle=False,review_only=True))
        assert captured[0]._event_time()==definition.requested_start
        assert captured[0].session_acquisition_authority.definition_hash==authority.definition_hash
    finally:controller._journal.close()


def test_real_native_request_three_protected_lots_one_acquisition(monkeypatch):
    from types import SimpleNamespace
    from datetime import date
    from uuid import uuid4
    import test_fixed_structural_lot_native as base_fixture
    from test_fixed_structural_lot_checkpoint_reader_profile import published
    from src.trading_runtime import fixed_structural_lot_release_v19 as inherited_compiler
    from src.trading_runtime.strategy_registry import numbered_strategy
    original=inherited_compiler.derive_fixed_structural_lot_release
    parent=source_fixture()
    monkeypatch.setattr(base_fixture,'declarations',lambda:(parent,None,None,numbered_strategy(42)))
    def compiler(actual_parent,**options):
        monkeypatch.setattr(inherited_compiler,'derive_fixed_structural_lot_release',original)
        return derive_strategy_one_hundred_six_configuration(actual_parent,**{k:options[k] for k in APPROVAL})
    monkeypatch.setattr(inherited_compiler,'derive_fixed_structural_lot_release',compiler)
    actual,request,plans,config,client,flat=published(monkeypatch,actual_loader=False,exclusive_writer=True,number=106,version=19)
    source=actual.operation.source;entry=request.entry.proposal
    # Original normalized definition/run producer authority over the same native context.
    definition=_definition(causal_v7_plan={'token':'controlled-causal-v7','build_id':plans.market.build_id,'catalog_hash':'b'*64},session_date=source.session_date,assignment_ids=(entry.assignment_id,),tickers=(entry.ticker,),
        configuration_revision={'revision_id':'strategy-one-106:'+str(uuid4()),'content_hash':sha256(canonical_json(source.installed_payload).encode()).hexdigest(),'payload':source.installed_payload},
        market_data_plan={'token':plans.market.token,'build_id':plans.market.build_id,'price_level_plan_token':'d'*64,'price_level_unit_count':2,
            'execution_interval':{'milliseconds':100},**{k:v for k,v in issued()[1].market_data_plan.items() if k.startswith('strategy_one_')}})
    packet=prepare_backtest_definition(config.run_id,definition,run_month=source.session_date.replace(day=1))
    class NativeScopeReader:
        def execute(self,query):
            name=query.split('FROM arte.')[1].split(' ')[0]
            if name in {t.name for t in TABLES}:
                key=dict(zip((t.name for t in TABLES),('definition','tickers','assignments','price_plan','commit')))[name]
                rows=packet[key] if key in ('tickers','assignments') else [packet[key]]
                return '\n'.join(json.dumps(r,default=str) for r in rows)
            return client.execute(query)
    from src.trading_runtime.arte_journal_writer import load_typed_run_context
    ctx=load_typed_run_context(NativeScopeReader(),config.run_id)
    assert ctx['session_date']==definition.session_date.isoformat(),(ctx['session_date'],definition.session_date)
    assert ctx['configuration_hash']==definition.configuration_revision['content_hash'],(ctx['configuration_hash'],definition.configuration_revision['content_hash'])
    authority=issue_native_acquisition_authority(NativeScopeReader(),definition=definition,run_id=config.run_id,account_ids=config.account_ids)
    from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter,SimulationConfig
    from src.trading_runtime.domain import TradingMode,InstrumentContract
    from src.trading_runtime.strategy_orders import RuntimeIbkrStrategyOrderPlanner
    from src.trading_runtime.runtime import TradingRuntime
    async def exercise():
        at=request.intent.event_time;journal=BacktestMemoryJournal(run_id=config.run_id)
        broker=SimulatedBrokerAdapter(config.account_ids,SimulationConfig(initial_cash=10000.),mode=TradingMode.BACKTEST,initial_time=at,fixed_bar_mode=True)
        profile=PortfolioAccountProfile('cash',entry.account_id,'backtest','simulated',PortfolioPolicy(allow_outside_rth=True))
        portfolio=PortfolioManagementEngine((profile,),journal=journal,run_id=config.run_id,strategy_id=config.strategy_id,strategy_revision=config.strategy_revision,event_clock=lambda:at,session_acquisition_authority=authority)
        planner=RuntimeIbkrStrategyOrderPlanner({entry.ticker:InstrumentContract(entry.ticker,1,entry.ticker,'STK','USD')},strategy_id=config.strategy_id,strategy_revision=config.strategy_revision,run_id=config.run_id)
        runtime=TradingRuntime(config,broker,SimpleNamespace(strategy_id=config.strategy_id,revision=config.strategy_revision,automatic=True),journal,portfolio=portfolio,intent_planner=planner)
        actual.bind_runtime(runtime)
        try:
            await runtime.initialize()
            us=int((at-timedelta(microseconds=1000)).timestamp()*1000000)
            local=at.astimezone(__import__('zoneinfo').ZoneInfo('America/New_York'))
            bucket=(local.hour*3600000+local.minute*60000+local.second*1000+local.microsecond//1000)//100-1
            row=dict(ticker=entry.ticker,resolution_ms=100,bucket_index=bucket,event_count=1,first_event_us=us,last_event_us=us,quote_timestamp_us=us,quote_valid=1,bid_int=100000,ask_int=100100,bid_size=100000.,ask_size=100000.,price_valid=1,close_int=100100,extremes_valid=1,low_int=100100,high_int=100100,execution_volume=100000.)
            await runtime.process_liquidity_boundary([row],at=at)
            result=await runtime.submit_fixed_structural_lot_request(request)
            assert result[0]['decision']['status'] in ('approved','resized')
            group=next(g for g in runtime.order_manager._groups.values() if g.intent.intent_id==request.intent.intent_id)
            assert len(group.intent.protection_profile.slices)==3
            assert len(set(group.plan.order_slice_ids))==3
            assert len(group.plan.orders)==9
            assert len(portfolio.reservations)==1
            from src.trading_runtime.portfolio_acquisition_limit import accepted_acquisition_ids
            assert len(accepted_acquisition_ids(authority.scopes[entry.account_id],tuple(portfolio.reservations.values()),run_id=config.run_id,ticker=entry.ticker,at=at))==1
        finally:
            await runtime.order_manager.close()
            for task in (runtime._broker_stream_task,runtime._risk_refresh_task):
                if task is not None:
                    task.cancel()
                    try:await task
                    except asyncio.CancelledError:pass
            journal.close()
    asyncio.run(exercise())


@pytest.mark.parametrize('surface',['own_addition','quota_global','policy_function','stale_loaded'])
def test_selected_source_seal_rejects_mutations(monkeypatch,surface):
    import src.backend.backtest_fixed_structural_lot_certification_v25 as seal
    if not seal.REVIEWED_SOURCE_AST:pytest.skip('Mechanically reviewed source seal not yet populated')
    original=Path.read_text
    root=Path(seal.__file__).resolve().parents[2]
    if surface=='stale_loaded':
        monkeypatch.setattr(seal,'REVIEWED_SOURCE_AST',{**seal.REVIEWED_SOURCE_AST,'foreign.py':'a'*64})
    else:
        target=Path(seal.__file__) if surface=='own_addition' else root/('src/trading_runtime/portfolio_acquisition_policy.py' if surface=='quota_global' else 'src/trading_runtime/portfolio_acquisition_contract.py')
        def changed(path,*args,**kwargs):
            text=original(path,*args,**kwargs)
            if path.resolve()==target.resolve():
                if surface=='policy_function':return text.replace('return None','return 2',1)
                return text+'\nforeign_source_authority=1\n'
            return text
        monkeypatch.setattr(Path,'read_text',changed)
    with pytest.raises(ValueError):seal.certify_fixed_structural_lot_source()


def test_source_certificate_has_no_caller_hash_path_admission():
    from src.backend.backtest_fixed_structural_lot_certification_v25 import certify_fixed_structural_lot_source
    with pytest.raises(TypeError):certify_fixed_structural_lot_source(expected={'caller.py':'a'*64})


def test_core_compatibility_retains_original_source_and_exact_legacy_pin():
    import ast,subprocess
    import src.backend.backtest_fixed_v4_certification as core
    root=Path(__file__).resolve().parents[1]
    relative='src/trading_runtime/portfolio.py';current=(root/relative).read_text(encoding='utf-8')
    old=subprocess.check_output(['git','show','6a154b3a57a949ecd109331d5921934dc2d85fae:'+relative],cwd=root).decode()
    expected=core._DRAWDOWN_CORE_REVIEWED_AST[relative]
    assert sha256(ast.unparse(ast.parse(old)).encode()).hexdigest()==expected
    assert core._reviewed_fixed_lot_core_projection(current,relative,'__module__',expected)
    assert not core._reviewed_fixed_lot_core_projection(current+'\nforeign_executable=1\n',relative,'__module__',expected)
    assert not core._reviewed_fixed_lot_core_projection(current,relative,'__module__','f'*64)
