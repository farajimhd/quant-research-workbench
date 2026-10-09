"""Actual processor ordering with explicit external fixed owner/publisher seams."""
import asyncio
from dataclasses import replace,fields
from types import SimpleNamespace
from contextlib import nullcontext
import pytest
from tests.test_backtest_strategy_one_management import _Runtime,_Evidence,_proposal,_financial,_evidence,_level
from src.backend.backtest_strategy_one_management import StrategyOneManagementRunner
from src.trading_runtime.strategy_one_position import ProtectionState,ResistanceBreak
from src.trading_runtime.fixed_lot_management_cadence_policy import *
from src.trading_runtime.strategy_one_hundred_seven_contract import strategy_one_hundred_seven_contract
from src.trading_runtime.strategy_one_hundred_six_contract import strategy_one_hundred_six_contract
from src.trading_runtime.strategy_registry import initialize_numbered_fixed_strategies

@pytest.mark.parametrize('value',(100,5000,30000))
def test_declared_range(value): assert FixedLotManagementCadencePolicy(value).payload()==value
@pytest.mark.parametrize('value',(True,0,-100,99,101,30100,5000.0,'5000'))
def test_foreign_range(value):
    with pytest.raises(ValueError): FixedLotManagementCadencePolicy(value)
def test_exact_factory_preserves_every_inherited_field():
    a=strategy_one_hundred_six_contract();b=strategy_one_hundred_seven_contract()
    for f in fields(a):
        if f.name not in ('strategy_number','release','management_cadence_policy'):
            assert getattr(a,f.name)==getattr(b,f.name),f.name
    assert b.management_cadence_policy==FixedLotManagementCadencePolicy(5000)
    with pytest.raises(ValueError):require_declared_management_cadence(a.release,b.management_cadence_policy)
    with pytest.raises(ValueError):parse_declared_management_cadence(b.release,None)

def fixture(monkeypatch,interval=5000):
    initialize_numbered_fixed_strategies()
    runtime=_Runtime();runtime.config=SimpleNamespace(strategy_revision=107,anchor_date=__import__('datetime').date(2026,8,4))
    evidence=_Evidence();manager=StrategyOneManagementRunner(runtime=runtime,evidence=evidence,tick_for_ticker=lambda _:0.01)
    manager._management_cadence=FixedLotManagementCadencePolicy(interval)
    key=('DU1','A1','AAA');proposal=replace(_proposal(),strategy_number=107,boundary_ms=30000)
    manager._submitted[key]=proposal
    calls=[]
    class Publisher:
        def enqueue_pending(self):calls.append(('enqueue',))
        async def await_fence(self):calls.append(('fence',))
    class Owner:
        publisher=Publisher();entries={key:object()};client=SimpleNamespace(fixed_lot_recovery_contexts=())
        def observe_checkpoint_financial(self,financial):calls.append(('financial',financial.position_quantity))
        async def first_held(self,key,*,boundary_ms):
            calls.append(('first',boundary_ms));return SimpleNamespace(protection=ProtectionState(boundary_ms,proposal.initial_stop,proposal.initial_target))
        def propose(self,entry,financial,**kwargs):
            calls.append(('propose',kwargs));return kwargs
        async def retire(self,key):calls.append(('retire',))
    owner=Owner();manager._fixed_lot_owner=owner
    owner.semantic="stable"
    monkeypatch.setattr("src.trading_runtime.fixed_lot_management_cadence_policy.execution_cadence_binding",lambda runtime,owner,key:owner.semantic)
    manager._liquidity_lookup=SimpleNamespace(window_at=lambda ticker,boundary:None)
    async def submit(request):
        calls.append(('submit',request['now_ms']))
        return SimpleNamespace(state=SimpleNamespace(protection=ProtectionState(request['now_ms'],proposal.initial_stop,proposal.initial_target),roster=SimpleNamespace(ceiling=11)))
    runtime.submit_fixed_structural_lot_protection=submit
    monkeypatch.setattr('src.trading_runtime.selected_checkpoint_products.observe_runtime_submission',lambda owner:nullcontext())
    return manager,evidence,runtime,owner,key,calls

@pytest.mark.parametrize('interval,next_boundary',( (5000,35000),(30000,60000),(100,30300)))
def test_processor_retains_events_then_processes_completed_cadence(monkeypatch,interval,next_boundary):
    async def run():
        m,e,r,o,key,calls=fixture(monkeypatch,interval)
        await m.on_management(_financial(),{},30100)
        assert ('first',30100) in calls
        e.rows[30200]=_evidence(30200);await m.on_management(_financial(),{},30200) # first received forces full decision
        if interval>100:
            event=ResistanceBreak(31000,_level('R-new',10.1))
            e.rows[31000]=_evidence(31000,breaks=(event,));await m.on_management(_financial(),{},31000)
            assert m._pending_breaks[key]==[event]
            assert len([c for c in calls if c[0]=='propose'])==1
            captured=m.capture_state(boundary_ms=31000)
            assert captured.pending_breaks[0][1]==(event,)
        e.rows[next_boundary]=_evidence(next_boundary);await m.on_management(_financial(),{},next_boundary)
        decisions=[c[1] for c in calls if c[0]=='propose']
        assert decisions[-1]['now_ms']==next_boundary
        if interval>100: assert decisions[-1]['breaks']==(event,)
        assert m._pending_breaks[key]==[]
    asyncio.run(run())

def test_partial_fill_and_cold_restart_force_immediate_then_retirement(monkeypatch):
    async def run():
        m,e,r,o,key,calls=fixture(monkeypatch)
        await m.on_management(_financial(),{},30100)
        for t,q in ((30200,1),(30300,0.5),(30400,0.5)):
            e.rows[t]=_evidence(t);await m.on_management(_financial(held=q),{},t)
        assert [c[1]['now_ms'] for c in calls if c[0]=='propose']==[30200,30300]
        # Actual inherited capture/restore; source-price cert producer seam is explicit.
        capture=m.capture_state(boundary_ms=30400)
        restored,ev,rr,oo,kk,cc=fixture(monkeypatch)
        restored._submitted.clear()
        monkeypatch.setattr('src.backend.backtest_strategy_one_management.declared_fixed_rule',lambda *args:False)
        # Legacy-shaped prepared proposal permits inherited normalized state fixture;
        # installed selected source recovery remains a later qualification gate.
        from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
        restored.contract=numbered_fixed_strategy(1)
        capture=replace(capture,submitted=tuple((k,replace(p,strategy_number=1)) for k,p in capture.submitted),first_held_boundaries=())
        restored.restore_state(capture)
        ev.rows[30500]=_evidence(30500);await restored.on_management(_financial(held=.5),{},30500)
        assert [c[1]['now_ms'] for c in cc if c[0]=='propose']==[30500]
        await m.on_management(_financial(held=0),{},30600)
        assert ('retire',) in calls and key not in m._cadence_financials
    asyncio.run(run())

def test_default_manager_has_no_cadence_selection():
    manager=StrategyOneManagementRunner(runtime=_Runtime(),evidence=_Evidence(),tick_for_ticker=lambda _:0.01)
    assert manager._management_cadence is None

def test_sealed_native_source_gate_positive():
    from src.backend.backtest_fixed_structural_lot_certification_v26 import certify_fixed_structural_lot_source
    assert len(certify_fixed_structural_lot_source())==64

def test_ack_failure_retries_next_row_and_external_state_change_forces(monkeypatch):
    async def run():
        m,e,r,o,key,calls=fixture(monkeypatch)
        await m.on_management(_financial(),{},30100)
        e.rows[30200]=_evidence(30200);await m.on_management(_financial(),{},30200)
        original=r.submit_fixed_structural_lot_protection
        async def failure(request):raise RuntimeError('controlled unresolved ACK')
        r.submit_fixed_structural_lot_protection=failure
        e.rows[35000]=_evidence(35000)
        with pytest.raises(RuntimeError,match='ACK'):await m.on_management(_financial(),{},35000)
        assert key in m._cadence_retry
        r.submit_fixed_structural_lot_protection=original
        e.rows[35100]=_evidence(35100);await m.on_management(_financial(),{},35100)
        assert key not in m._cadence_retry
        o.semantic='same quantity resolved protection changed'
        e.rows[35200]=_evidence(35200);await m.on_management(_financial(),{},35200)
        assert [c[1]['now_ms'] for c in calls if c[0]=='propose']==[30200,35000,35100,35200]
    asyncio.run(run())

def test_pending_bound_checked_before_cadence_and_recovery_change_forces(monkeypatch):
    async def run():
        m,e,r,o,key,calls=fixture(monkeypatch)
        await m.on_management(_financial(),{},30100)
        e.rows[30200]=_evidence(30200);await m.on_management(_financial(),{},30200)
        m.max_pending_breaks=1
        e.rows[31000]=_evidence(31000,breaks=(ResistanceBreak(31000,_level('one',10.1)),ResistanceBreak(31000,_level('two',10.2))))
        with pytest.raises(RuntimeError,match='memory bound'):await m.on_management(_financial(),{},31000)
        assert len([c for c in calls if c[0]=='propose'])==1
        from src.trading_runtime.fixed_structural_lot_cold_recovery import FixedStructuralLotColdRecoveryContext
        # Trigger only; actual recovery admission remains in the native owner.
        context=FixedStructuralLotColdRecoveryContext(object(),'batch',42,'[]')
        o.client.fixed_lot_recovery_contexts=(('batch',context),)
        e.rows[31100]=_evidence(31100);await m.on_management(_financial(),{},31100)
        assert [c[1]['now_ms'] for c in calls if c[0]=='propose']==[30200,31100]
    asyncio.run(run())

def test_whole_compiler_preserves_all106_parameters_and_explicit_alternative():
    from tests.test_fixed_structural_lot_configuration_routing import source_fixture
    from tests.test_strategy_fifty_release import APPROVAL
    from src.trading_runtime.strategy_one_hundred_six_release import derive_strategy_one_hundred_six_configuration,release_contract as inherited_release
    from src.trading_runtime.strategy_one_hundred_seven_release import derive_strategy_one_hundred_seven_configuration,release_contract,verify_prepared_strategy_one_hundred_seven_configuration
    from src.trading_runtime.fixed_structural_lot_release_v26 import derive_fixed_structural_lot_release
    parent=source_fixture();prior=derive_strategy_one_hundred_six_configuration(parent,**APPROVAL)
    current=derive_strategy_one_hundred_seven_configuration(parent,**APPROVAL)
    expected=dict(prior['payload']['strategy']['parameters']);expected[PARAMETER]=5000
    assert current['payload']['strategy']['parameters']==expected
    from copy import deepcopy
    from src.trading_runtime.journal_contract import canonical_json
    a=deepcopy(prior['payload']);b=deepcopy(current['payload'])
    before_manifest=a['strategy']['numbered_release'];after_manifest=b['strategy']['numbered_release']
    excluded={'contract','approved_digest','manifest_hash'}
    assert {k:v for k,v in before_manifest.items() if k not in excluded}=={k:v for k,v in after_manifest.items() if k not in excluded}
    for key in excluded:after_manifest[key]=before_manifest[key]
    b['strategy']['parameters'].pop(PARAMETER)
    for section,keys in (('strategy',('strategy_number','revision','profile_id','profile_revision','name')),
        ('strategy_profile',('profile_id','revision','definition_revision','name','description')),
        ('run_plan',('profile_id','name','description'))):
        for key in keys:b[section][key]=a[section][key]
    assert canonical_json(a)==canonical_json(b),'Entire inherited106 payload differs outside explicit cadence/identity declaration'
    assert verify_prepared_strategy_one_hundred_seven_configuration(parent,current['payload'])==current
    other=derive_fixed_structural_lot_release(parent,inherited_derive=derive_strategy_one_hundred_six_configuration,
        inherited_release=inherited_release(),release=release_contract(),management_cadence_policy=FixedLotManagementCadencePolicy(30000),**APPROVAL)
    assert other['payload']['strategy']['parameters'][PARAMETER]==30000

def test_exact_native_oms_semantic_trigger_ignores_timestamp_and_prices_outside_orders():
    from datetime import datetime,timezone,timedelta
    from src.trading_runtime.order_management import OrderManagementEngine,_ManagedOrderGroup,OrderManagementState
    from src.trading_runtime.ibkr_schema import OrderRequest
    from tests.test_portfolio_management import intent
    at=datetime.now(timezone.utc);journal=object()
    engine=OrderManagementEngine(broker=object(),planner=lambda *a:None,risk=object(),journal=journal,
        run_id='run',strategy_id='strategy',strategy_revision=107)
    key=('DU1','assignment-AAA','AAA');order=OrderRequest('DU1',1,'STP','SELL',quantity=1,cOID='stop',auxPrice=9)
    entry=intent('entry',ticker='AAA')
    group=_ManagedOrderGroup('group',entry,'DU1',object(),OrderManagementState.WORKING,at,at,[order])
    engine._groups['group']=group
    runtime=SimpleNamespace(order_manager=engine,run_id='run',journal=journal,
        config=SimpleNamespace(strategy_id='strategy',strategy_revision=107))
    owner=SimpleNamespace(operation=SimpleNamespace(source=SimpleNamespace(run_id='run')),
        groups={key:'group'},entries={key:SimpleNamespace(intent=entry)})
    from src.trading_runtime.fixed_lot_management_cadence_policy import execution_cadence_binding
    before=execution_cadence_binding(runtime,owner,key)
    group.updated_at=at+timedelta(seconds=1);group.high_water_price=99
    assert execution_cadence_binding(runtime,owner,key)==before
    group.orders[0]=replace(order,auxPrice=9.5)
    assert execution_cadence_binding(runtime,owner,key)!=before
    before=execution_cadence_binding(runtime,owner,key)
    group.state=OrderManagementState.OUTCOME_UNKNOWN
    assert execution_cadence_binding(runtime,owner,key)!=before
    engine.strategy_revision=106
    with pytest.raises(ValueError,match='ownership'):execution_cadence_binding(runtime,owner,key)

def test_completed30s_low_and_session_exit_precede_cadence(monkeypatch):
    async def run():
        m,e,r,o,key,calls=fixture(monkeypatch,30000)
        await m.on_management(_financial(),{},30100)
        e.rows[30200]=_evidence(30200);await m.on_management(_financial(),{},30200)
        e.rows[60000]=replace(_evidence(60000),low_boundary_ms=60000,low_int=99000)
        await m.on_management(_financial(),{},60000)
        latest=[c[1] for c in calls if c[0]=='propose'][-1]
        assert latest['low_boundary_ms']==60000 and latest['low_int']==99000
        exits=[]
        async def session_exit(financial,resolutions,boundary):exits.append(boundary)
        r.submit_numbered_session_exit=session_exit
        await m.on_management(_financial(),{},57600000)
        assert exits==[57600000]
        assert len([c for c in calls if c[0]=='propose'])==2
    asyncio.run(run())

def test_real107_processor_runtime_partial_target_and_off_cadence_protection(monkeypatch):
    """Reuse the real106 Runtime/Portfolio/OMS fixture, with explicit source/SQL seams.

    Exact source transformations add the actual processor/owner and actual broker
    fill rows; no context/entry/Portfolio/OMS/recovery capability issuer is patched.
    The emitted fixture is retained outside source for independent review.
    """
    import inspect,textwrap
    import tests.test_strategy106_portfolio_acquisition as prior_fixture
    source=textwrap.dedent(inspect.getsource(prior_fixture.test_real_native_request_three_protected_lots_one_acquisition))
    def change(before,after):
        nonlocal source
        assert source.count(before)==1,before
        source=source.replace(before,after)
    change('def test_real_native_request_three_protected_lots_one_acquisition(monkeypatch):','def controlled_native107(monkeypatch):')
    change('return derive_strategy_one_hundred_six_configuration(actual_parent,**{k:options[k] for k in APPROVAL})','return derive_strategy_one_hundred_seven_configuration(actual_parent,**{k:options[k] for k in APPROVAL})')
    change('number=106,version=19','number=107,version=19')
    change("'strategy-one-106:'+str(uuid4())","'strategy-one-107:'+str(uuid4())")
    change('        actual.bind_runtime(runtime)',"""        actual.bind_runtime(runtime)
        from src.trading_runtime import arte_journal_writer as writer_module
        from src.backend.backtest_typed_publisher import BacktestTypedJournalPublisher
        from src.backend.backtest_fixed_structural_lot_management import NativeFixedStructuralLotManagement
        from src.backend.backtest_strategy_one_management import StrategyOneManagementRunner
        from src.trading_runtime.strategy_one_management_evidence import StrategyOneManagementEvidence
        from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView
        from src.trading_runtime.strategy_engine import AssignmentStatus,StrategyPermissions
        from src.backend.backtest_market_data import market_day_boundary
        monkeypatch.setattr(writer_module,'storage_preflight',lambda *a,**kw:None)
        monkeypatch.setattr(writer_module,'journal_permission_preflight',lambda *a,**kw:None)
        client.fixed_structural_lot_profile=actual.profile
        writer=writer_module.ArteJournalWriter(client,run_id=config.run_id,journal_profile='backtest_v4',coalesce_batches=False)
        publisher=BacktestTypedJournalPublisher(journal,writer,attempt_id=str(uuid4()),run_month=source.session_date.replace(day=1),expected_config=flat,fixed_market_parent_plan=plans.market)
        actual.operation.bind_publisher(publisher);publisher.bind_first_price_source(source.price_authority)
        owner=NativeFixedStructuralLotManagement(operation=actual.operation,publisher=publisher,client=client)
        class Evidence:
            values={}
            async def management_evidence(self,ticker,resolutions,*,boundary_ms):return self.values[boundary_ms]
        evidence=Evidence()
        manager=StrategyOneManagementRunner(runtime=runtime,evidence=evidence,tick_for_ticker=lambda _:source.tick)
        manager.bind_fixed_structural_lot_management(owner)
        from src.backend.backtest_strategy_liquidity_fade_loader import load_compiled_liquidity_fade_lookup
        class EmptyActivityTransport:
            def iter_arrow_record_batches(self,query):
                assert query.startswith('SELECT ') and 'FROM arte.bars_v1' in query
                return iter(()) # explicit empty synthetic 5s activity transport
        liquidity_market=source.price_authority.plan.source.market
        activity=load_compiled_liquidity_fade_lookup(EmptyActivityTransport(),plan=liquidity_market,
            session_date=source.session_date,strategy_number=config.strategy_revision)
        manager.bind_liquidity_fade_lookup(activity,liquidity_market)
        def fact(boundary,bid=10.,breaks=()):
            return StrategyOneManagementEvidence(entry.ticker,boundary,bid,bid+.01,True,None,None,tuple(breaks),())
        async def boundary_row(boundary,price,volume=0):
            nonlocal at
            at=market_day_boundary(source.session_date,boundary)
            us=int((at-timedelta(microseconds=1000)).timestamp()*1000000)
            local=at.astimezone(__import__('zoneinfo').ZoneInfo('America/New_York'))
            bucket=(local.hour*3600000+local.minute*60000+local.second*1000+local.microsecond//1000)//100-1
            value=int(round(price*10000))
            await runtime.process_liquidity_boundary([{**row,'bucket_index':bucket,'first_event_us':us,'last_event_us':us,'quote_timestamp_us':us,'bid_int':value,'ask_int':value+100,'close_int':value,'low_int':value,'high_int':value,'execution_volume':volume,'execution_price_levels':({'price_int':value,'volume':volume},) if volume else ()}],at=at)
""")
    change("            result=await runtime.submit_fixed_structural_lot_request(request)\n            assert result[0]['decision']['status'] in ('approved','resized')","            await manager.on_entry_proposal(entry)")
    change('            assert len(portfolio.reservations)==1',"""            assert len(portfolio.reservations)==1
            await boundary_row(entry.boundary_ms+100,10.01,100000.)
            quantity=sum(float(p.position) for p in await broker.positions(entry.account_id))
            assert quantity>0
            financial=StrategyOneFinancialView(entry.assignment_id,entry.account_id,entry.ticker,AssignmentStatus.MANAGING,StrategyPermissions(),quantity,False,False,False,1)
            await manager.on_management(financial,{},entry.boundary_ms+100)
            key=(entry.account_id,entry.assignment_id,entry.ticker)
            assert owner.states[key].protection.boundary_ms==entry.boundary_ms+100
            for boundary in (entry.boundary_ms+200,entry.boundary_ms+300):
                await boundary_row(boundary,10.01)
                evidence.values[boundary]=fact(boundary)
                await manager.on_management(financial,{},boundary)
            assert owner.states[key].protection.boundary_ms==entry.boundary_ms+200
            from src.trading_runtime.ibkr_schema import OPEN_ORDER_STATUSES
            targets=sorted((o for o in await broker.live_orders() if o.orderType=='LMT' and o.side=='SELL' and o.remainingQuantity>0 and o.order_status in OPEN_ORDER_STATUSES),key=lambda o:o.price)
            target=targets[0];boundary=(entry.boundary_ms+1000)//1000*1000
            assert boundary%5000
            await boundary_row(boundary,float(target.price),float(target.remainingQuantity))
            residual=sum(float(p.position) for p in await broker.positions(entry.account_id))
            assert 0<residual<quantity
            from test_strategy_one_position import level
            evidence.values[boundary]=fact(boundary,float(target.price),tuple(ResistanceBreak(boundary,level(i,float(target.price)-.06+i*.01)) for i in range(3)))
            await manager.on_management(replace(financial,position_quantity=residual),{},boundary)
            assert owner.states[key].protection.boundary_ms==boundary
            assert float(sum(q for _,q in owner.states[key].roster.remaining))==residual
            assert owner.states[key].protection.stop>entry.initial_stop
            assert manager._pending_breaks[key]==[]
            print('native107_partial_target_residual='+str(residual))
            capture=manager.capture_state(boundary_ms=boundary)
            restored=StrategyOneManagementRunner(runtime=runtime,evidence=evidence,tick_for_ticker=lambda _:source.tick)
            restored.bind_fixed_structural_lot_management(owner)
            restored.bind_liquidity_fade_lookup(manager._liquidity_lookup,liquidity_market)
            restored.restore_state(capture,first_price_source=source.price_authority)
            assert not restored._cadence_financials and not restored._cadence_retry
            later=boundary+100
            await boundary_row(later,float(target.price))
            evidence.values[later]=fact(later,float(target.price))
            await restored.on_management(replace(financial,position_quantity=residual),{},later)
            assert owner.states[key].protection.boundary_ms==later
            assert restored._cadence_financials and not restored._cadence_retry
            print('native107_source_bound_manager_cold_restore_boundary='+str(later))
""")
    change('            await runtime.order_manager.close()',"            await runtime.order_manager.close()\n            writer.close()")
    from src.trading_runtime.strategy_one_hundred_seven_release import derive_strategy_one_hundred_seven_configuration
    namespace=dict(vars(prior_fixture));namespace.update(derive_strategy_one_hundred_seven_configuration=derive_strategy_one_hundred_seven_configuration,ResistanceBreak=ResistanceBreak)
    from pathlib import Path
    Path('D:/TradingML/runtimes/strategy-optimization-20261005/strategy107-controlled-native-fixture-v1.py').write_text(source,encoding='utf-8')
    exec(compile(source,'<controlled-native107>','exec'),namespace)
    namespace['controlled_native107'](monkeypatch)

def test_v26_sealed_inventory_and_exact_parent_restorations():
    import ast,json,subprocess
    from hashlib import sha256
    from pathlib import Path
    from src.backend import backtest_fixed_structural_lot_certification_v25 as prior
    from src.backend import backtest_fixed_structural_lot_certification_v26 as current
    from src.backend import backtest_fixed_structural_lot_compatibility_v26 as helper
    root=Path(__file__).resolve().parents[1]
    packet=json.loads(Path('D:/TradingML/runtimes/strategy-optimization-20261005/strategy107-v26-source-seal-proposal-v2.json').read_text())
    assert current.REQUIRED_SOURCE_FILES[:543]==prior.REQUIRED_SOURCE_FILES
    assert len(current.REQUIRED_SOURCE_FILES)==len(set(current.REQUIRED_SOURCE_FILES))==549
    assert all((root/p).is_file() for p in current.REQUIRED_SOURCE_FILES)
    assert packet['old_pins']==prior.REVIEWED_SOURCE_AST
    assert helper.REVIEWED_EDITS==packet['restorations']
    digest=lambda text:sha256(ast.unparse(ast.parse(text)).encode()).hexdigest()
    for relative,recipe in helper.REVIEWED_EDITS.items():
        path='src/'+relative
        actual=(root/path).read_text(encoding='utf-8')
        assert digest(actual)==recipe['current_ast'],relative
        restored=actual
        for now,old in recipe['edits']:
            assert now and restored.count(now)==1,relative
            restored=restored.replace(now,old,1)
        parent=subprocess.check_output(['git','show',packet['base_commit']+':'+path],cwd=root).decode()
        assert digest(restored)==digest(parent)==recipe['parent_ast'],relative
        for addition in ('\nimport fractions\n','\nUNREVIEWED_GLOBAL=1\n','\ndef unreviewed_function(): return 1\n'):
            assert digest(actual+addition)!=recipe['current_ast'],relative
    assert helper.restore_reviewed_parent_source('x=1','foreign.py')=='x=1'
    with pytest.raises(TypeError):current.certify_fixed_structural_lot_source(source_override={})


def test_v26_sealed_loaded_metadata_and_executable_envelope_reject(monkeypatch):
    from pathlib import Path
    from src.backend import backtest_fixed_structural_lot_certification_v26 as gate
    monkeypatch.setattr(gate,'REVIEWED_SOURCE_AST',{'foreign.py':'0'*64})
    with pytest.raises(ValueError,match='loaded and fresh'):gate.certify_fixed_structural_lot_source()
    monkeypatch.undo()
    original=Path.read_text
    def changed(path,*args,**kwargs):
        text=original(path,*args,**kwargs)
        return text+'\nUNREVIEWED=1\n' if path.name=='backtest_fixed_structural_lot_certification_v26.py' else text
    monkeypatch.setattr(Path,'read_text',changed)
    with pytest.raises(ValueError,match='envelope'):gate.certify_fixed_structural_lot_source()


def test_v26_management_symbol_bridge_exact_parent_and_mutations(monkeypatch):
    import ast
    from pathlib import Path
    from hashlib import sha256
    from src.backend import backtest_fixed_v4_certification as core
    from src.backend import backtest_fixed_structural_lot_compatibility_v26 as helper
    root=Path(__file__).resolve().parents[1]
    relative='backend/backtest_strategy_one_management.py'
    source=(root/'src'/relative).read_text(encoding='utf-8')
    for name in ('__init__','on_management'):
        expected=core._RISING_MOMENTUM_REVIEWED_AST[relative][name]
        assert core._reviewed_fixed_lot_management_projection(source,name,expected)
        assert not core._reviewed_fixed_lot_management_projection(source+'\nUNREVIEWED=1\n',name,expected)
    altered=source.replace('self._cadence_retry = set()','self._cadence_retry = {1}')
    assert altered!=source
    assert not core._reviewed_fixed_lot_management_projection(altered,'__init__',core._RISING_MOMENTUM_REVIEWED_AST[relative]['__init__'])

@pytest.mark.parametrize('filename',('backtest_strategy_one_management.py','fixed_lot_management_cadence_policy.py','fixed_structural_lot_release_v26.py','strategy_one_hundred_seven_contract.py','strategy_one_hundred_seven_release.py','backtest_fixed_structural_lot_compatibility_v26.py'))
def test_sealed_source_rejects_executable_leaf_mutation(monkeypatch,filename):
    from pathlib import Path
    from src.backend.backtest_fixed_structural_lot_certification_v26 import certify_fixed_structural_lot_source
    original=Path.read_text
    def changed(path,*args,**kwargs):
        text=original(path,*args,**kwargs)
        return text+'\nUNREVIEWED_EXECUTABLE=1\n' if path.name==filename else text
    monkeypatch.setattr(Path,'read_text',changed)
    with pytest.raises(ValueError,match='reviewed source authority changed'):certify_fixed_structural_lot_source()
