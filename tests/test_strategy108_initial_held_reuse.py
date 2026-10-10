"""Bounded source preparation; fixture source/producer/SQL seams are explicit."""
from dataclasses import fields
from decimal import Decimal
from types import MappingProxyType
import pytest

from src.trading_runtime.initial_held_recovery_reuse_policy import InitialHeldRecoveryReusePolicy

@pytest.mark.parametrize('bounds',((1,1,1,1),(32,8,100000,67108864)))
def test_explicit_policy(bounds):
    assert InitialHeldRecoveryReusePolicy(*bounds).payload()['max_contexts']==bounds[0]

@pytest.mark.parametrize('bounds',((True,1,1,1),(0,1,1,1),(1,33,1,1),(1,1,1000001,1),(1,1,1,67108865)))
def test_policy_rejects_foreign_bounds(bounds):
    with pytest.raises(ValueError):InitialHeldRecoveryReusePolicy(*bounds)

def test_factory_preserves_every107_parameter_and_cadence():
    from src.trading_runtime.strategy_one_hundred_seven_contract import strategy_one_hundred_seven_contract
    from src.trading_runtime.strategy_one_hundred_eight_contract import strategy_one_hundred_eight_contract
    old,new=strategy_one_hundred_seven_contract(),strategy_one_hundred_eight_contract()
    for field in fields(old):
        if field.name not in ('strategy_number','release','initial_held_recovery_reuse_policy','proposal_decision_inventory_reuse_policy'):
            assert getattr(old,field.name)==getattr(new,field.name),field.name
    assert new.management_cadence_policy.interval_ms==5000

def test_paired_declaration_rejects_orphan_and_missing_policy():
    from src.trading_runtime.strategy_one_hundred_seven_release import release_contract as old
    from src.trading_runtime.strategy_one_hundred_eight_release import release_contract as new
    from src.trading_runtime.initial_held_recovery_reuse_policy import require_declared_initial_held_reuse
    with pytest.raises(ValueError):require_declared_initial_held_reuse(old(),InitialHeldRecoveryReusePolicy(1,1,1,1))
    with pytest.raises(ValueError):require_declared_initial_held_reuse(new(),None)

def test_private_typed_copy_has_no_mapping_alias_and_preserves_decimal():
    from src.backend.backtest_fixed_lot_initial_recovery_reuse import _copy,_image
    original=({'state':{'remaining':Decimal('166.0')},'rows':(MappingProxyType({'value':1.0}),)},)
    copied=_copy(original)
    assert _image(copied)==_image(original)
    assert copied[0] is not original[0] and copied[0]['state'] is not original[0]['state']
    original[0]['state']['remaining']=Decimal('0')
    assert copied[0]['state']['remaining']==Decimal('166.0')

def test_unselected_context_and_cold_inventory_execute_original():
    from src.backend.backtest_fixed_lot_initial_recovery_reuse import context_request,inventory_read
    assert context_request(object()) is None
    result=object()
    assert inventory_read(object(),object(),{},lambda:result) is result

def test_absent_installed_payload_is_unselected():
    from types import SimpleNamespace
    from src.backend.backtest_fixed_lot_initial_recovery_reuse import _selected
    assert _selected(SimpleNamespace(installed_payload=None)) is None

def test_set_shape_is_order_independent_and_private():
    from src.backend.backtest_fixed_lot_initial_recovery_reuse import _copy,_shape
    left={'a','b','c'};right=set(reversed(tuple(left)))
    assert _shape(left)==_shape(right) and _copy(left) is not left

def test_sealed_own_source_and_loaded_metadata_mutation(monkeypatch):
    from src.backend import backtest_fixed_structural_lot_certification_v27 as source
    assert len(source.REQUIRED_SOURCE_FILES)==557
    proof=source.certify_fixed_structural_lot_source()
    assert len(proof)==64
    monkeypatch.setattr(source,'APPROVED_METADATA_ANCHOR','0'*64)
    with pytest.raises(ValueError,match='loaded and fresh'):
        source.certify_fixed_structural_lot_source()

def _actual108(monkeypatch,mutation=None, *, cold_probe=False,paired=False,cold_graph=False,two_tickers=False):
    """Actual107 source-bound processor fixture, exact108 declaration substitution.

    Producer/source certificate/storage SQL seams stay explicit in the emitted
    fixture; entry/context/owner issuers and Portfolio/OMS guards are unchanged.
    """
    import inspect,textwrap
    from pathlib import Path
    import tests.test_strategy107_management_cadence as prior
    from src.backend import backtest_fixed_lot_initial_recovery_reuse as reuse
    original=reuse.context_request
    counts={'contexts':0,'proposal_contexts':0,'first_open_seconds':[],'cold_complete':0,'initial_pairs':[],'proposal_pairs':[],'output':[]}
    from src.trading_runtime.fixed_structural_lot_entry_v4 import FixedStructuralLotPublicationContext
    complete=FixedStructuralLotPublicationContext._verify_source_complete
    def full(context):
        counts['cold_complete']+=1;return complete(context)
    monkeypatch.setattr(FixedStructuralLotPublicationContext,'_verify_source_complete',full)
    def observed(context):
        result=original(context)
        if result is not None:
            counts['contexts']+=1
            from src.trading_runtime.proposal_decision_inventory_reuse_policy import ProposalDecisionInventoryReusePolicy
            if type(reuse._READ.get().policy) is ProposalDecisionInventoryReusePolicy:counts['proposal_contexts']+=1
            if cold_probe and not counts.get('foreign_probed'):
                from copy import copy
                counts['foreign_probed']=True
                operation=reuse._READ.get();client=copy(operation.proof.owner.client)
                if hasattr(client,'backtest_v4_lease'):delattr(client,'backtest_v4_lease')
                before=counts['cold_complete']
                def full_cold():
                    assert reuse._READ.get() is None
                    assert original(context) is None
                    return context.verify_source()
                cold=reuse.inventory_read(client,operation.proof.prefix,{},full_cold)
                assert cold is not None and counts['cold_complete']>before
                from dataclasses import replace
                before=counts['cold_complete'];foreign_context=replace(context)
                assert foreign_context.verify_source() is not None
                assert counts['cold_complete']>before
            if mutation is not None:mutation(context,reuse)
        return result
    monkeypatch.setattr(reuse,'context_request',observed)
    from src.backend.backtest_fixed_structural_lot_management import NativeFixedStructuralLotManagement
    from time import perf_counter
    original_open=NativeFixedStructuralLotManagement.open
    def timed(owner,*args,**kwargs):
        started=perf_counter()
        try:
            if not paired:return original_open(owner,*args,**kwargs)
            control_owner=NativeFixedStructuralLotManagement(operation=owner.operation,publisher=owner.publisher,client=owner.client)
            from src.backend.backtest_fixed_lot_management_reuse import _content
            with monkeypatch.context() as control:
                control.setattr(reuse,'_selected',lambda *a,**kw:None)
                before=perf_counter();cold=original_open(control_owner,*args,**kwargs);cold_s=perf_counter()-before
            before=perf_counter();selected=original_open(owner,*args,**kwargs);selected_s=perf_counter()-before
            assert _content(cold)==_content(selected)
            counts['initial_pairs'].append((cold_s,selected_s))
            return selected
        finally:counts['first_open_seconds'].append(perf_counter()-started)
    timed.__wrapped__=original_open
    monkeypatch.setattr(NativeFixedStructuralLotManagement,'open',timed)
    if paired:
        original_propose=NativeFixedStructuralLotManagement.propose
        def timed_propose(owner,*args,**kwargs):
            from src.backend.backtest_fixed_lot_management_reuse import _content
            with monkeypatch.context() as control:
                control.setattr(reuse,'_selected',lambda *a,**kw:None)
                before=perf_counter();cold=original_propose(owner,*args,**kwargs);cold_s=perf_counter()-before
            owner._requests.pop(cold,None);owner._management_reads.pop(cold,None)
            before=perf_counter();selected=original_propose(owner,*args,**kwargs);selected_s=perf_counter()-before
            def image(request):return _content((request.proposal,request.financial,request.intents,request.requested_legs))
            assert image(cold)==image(selected)
            counts['proposal_pairs'].append((cold_s,selected_s))
            return selected
        timed_propose.__wrapped__=original_propose
        monkeypatch.setattr(NativeFixedStructuralLotManagement,'propose',timed_propose)
    text=textwrap.dedent(inspect.getsource(prior.test_real107_processor_runtime_partial_target_and_off_cadence_protection))
    text=text.replace('test_real107_processor_runtime_partial_target_and_off_cadence_protection','controlled108')
    text=text.replace('strategy_one_hundred_seven','strategy_one_hundred_eight').replace('number=107','number=108').replace('strategy-one-107:','strategy-one-108:').replace('native107','native108').replace('strategy107-controlled','strategy108-controlled')
    Path('D:/TradingML/runtimes/strategy-optimization-20261005/strategy108-controlled-native-fixture-v1.py').write_text(text,encoding='utf-8')
    if paired:
        text=text.replace("namespace=dict(vars(prior_fixture));namespace.update(","namespace=dict(vars(prior_fixture));namespace['capture_output']=capture_output;namespace.update(")
        marker="print('native108_source_bound_manager_cold_restore_boundary='+str(later))"
        addition='\n            capture_output(dict(cash=(await broker.account_summary(entry.account_id)).totalcashvalue,position=residual,stop=owner.states[key].protection.stop,boundary=later,reservations=len(portfolio.reservations),orders=tuple((o.side,o.orderType,o.totalSize,o.filledQuantity,o.remainingQuantity,o.avgPrice,o.price,o.auxPrice,o.order_status.value) for o in await broker.live_orders()),oca=tuple(tuple(i for i,s in enumerate(broker._orders.values()) if s.oca_group==state.oca_group) for state in broker._orders.values())))'
        assert marker in text;text=text.replace(marker,marker+addition)
    if cold_graph:
        marker="print('native108_source_bound_manager_cold_restore_boundary='+str(later))"
        code="""
            await publisher.await_fence()
            from test_fixed_structural_lot_checkpoint_reader_profile import controlled_price_rows
            from src.backend.backtest_fixed_structural_lot_execution_v19 import prepare_fixed_structural_lot_session
            class SourceRows:
                def execute(self,sql,*args,**kwargs):return controlled_price_rows(plans,sql)
                def close(self):pass
            fresh=prepare_fixed_structural_lot_session(plans=plans,number=108,run_id=config.run_id,
                session_date=source.session_date,market=plans.market,candidates=plans.candidates,
                entry=plans.entry,seeds=plans.seeds,through_boundary_ms=57600000,client_factory=SourceRows)
            fresh_source=fresh.operation.source
            assert fresh_source is not source
            from src.trading_runtime.fixed_structural_lot_profile import issue_fixed_structural_lot_profile
            class ColdRows:
                fixed_structural_lot_profile=issue_fixed_structural_lot_profile(fresh.operation)
                backtest_v4_lease=None
                v4_batched_detail_readback=True
                fixed_structural_lot_contexts=()
                fixed_lot_recovery_contexts=()
                def execute(self,sql,*args,**kwargs):return client.execute(sql,*args,**kwargs)
            cold_client=ColdRows()
            from src.backend.backtest_fixed_structural_lot_resume import load_persisted_fixed_lot_contexts
            from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix
            from src.trading_runtime.arte_oms_projection import _load_latest_committed_oms_groups
            assert reuse._READ.get() is None and reuse._OPERATION.get() is None
            contexts=load_persisted_fixed_lot_contexts(cold_client,source=fresh_source)
            assert contexts and all(c.source is fresh_source for c in contexts)
            assert not cold_client.fixed_structural_lot_contexts
            cold_client.fixed_structural_lot_contexts=contexts
            prefix=load_verified_v4_prefix(cold_client,config.run_id,first_price_source=fresh_source.price_authority,
                fixed_lot_contexts=contexts,_fixed_lot_cold_source=fresh_source,_cold_recovery_context_sink=[])
            groups=_load_latest_committed_oms_groups(cold_client,prefix,fixed_lot_contexts=contexts,
                allowed_accounts=frozenset(config.account_ids),strategy_identity=(config.strategy_id,config.strategy_revision))
            assert groups and prefix.last_sequence==journal._fenced_sequence
            from src.trading_runtime.fixed_structural_lot_entry_schema import LOT
            companion=client.tables[LOT.name][0];original_quantity=companion['fixed_target']
            try:
                companion['fixed_target']=original_quantity+1
                with pytest.raises(ValueError):load_persisted_fixed_lot_contexts(cold_client,source=fresh_source)
            finally:companion['fixed_target']=original_quantity
            capture_cold(dict(contexts=len(contexts),groups=len(groups),sequence=prefix.last_sequence,
                distinct_source=True,no_writer_lease=True,companion_mutation_rejected=True))
"""
        assert marker in text
        text=text.replace(marker,marker+code)
        text=text.replace("namespace=dict(vars(prior_fixture));namespace.update(","namespace=dict(vars(prior_fixture));namespace['reuse']=reuse;namespace['capture_cold']=capture_cold;namespace.update(")
    if two_tickers:
        edits=[("tickers=(entry.ticker,)","tickers=('AAA','ZZZ')"),
            ("{entry.ticker:InstrumentContract(entry.ticker,1,entry.ticker,'STK','USD')}","{t:InstrumentContract(t,1,t,'STK','USD') for t in ('AAA','ZZZ')}"),
            ("await runtime.process_liquidity_boundary([row],at=at)","await runtime.process_liquidity_boundary([row,dict(row,ticker='ZZZ')],at=at)"),
            ('assert len(portfolio.reservations)==1','assert len(portfolio.reservations)==2'),
            ('            await manager.on_entry_proposal(entry)',"\n            from src.backend.backtest_strategy_certified_price_break import bind_certified_price_break_proposal\n            from src.backend.backtest_strategy_episode_activity_source import bind_episode_activity_proposal\n            second=replace(entry,ticker='ZZZ',momentum=source.price_authority.plan.momentum.lookup('ZZZ',41000),\n                initial_momentum=source.price_authority.plan.source.parent.selection_witness('ZZZ',41000),\n                strategy_number=18,first_price=None,price_source_token=None)\n            second=bind_episode_activity_proposal(source.price_authority,\n                bind_certified_price_break_proposal(source.price_authority.plan,second,strategy_number=36),session_date=source.session_date)\n            second_request=actual.operation.request(second)\n            assert second_request.intent.intent_id!=request.intent.intent_id\n            second_result=await runtime.submit_fixed_structural_lot_request(second_request)\n            assert second_result[0]['decision']['status'] in ('approved','resized')\n"+'            await manager.on_entry_proposal(entry)')]
        injected=''.join('    source=source.replace('+repr(before)+','+repr(after)+')\n' for before,after in edits)
        marker="    exec(compile(source,'<controlled-native108>','exec'),namespace)"
        assert marker in text;text=text.replace(marker,injected+marker)
    namespace=dict(vars(prior));namespace['capture_output']=counts['output'].append
    namespace['reuse']=reuse;namespace['capture_cold']=lambda v:counts.update(cold_graph=v)
    exec(compile(text,'<controlled-native108>','exec'),namespace)
    namespace['controlled108'](monkeypatch)
    assert counts['contexts']>0
    assert counts['proposal_contexts']>0
    print('actual108_initial_context_reuses='+str(counts['contexts']))
    print('actual108_proposal_context_reuses='+str(counts['proposal_contexts']))
    if cold_probe:assert counts.get('foreign_probed')
    print('actual108_complete_context_replays='+str(counts['cold_complete']))
    print('actual108_initial_open_seconds='+repr(counts['first_open_seconds']))
    return counts

def test_actual108_initial_held_runtime_partial_fill_and_cold_manager(monkeypatch):
    _actual108(monkeypatch)

@pytest.mark.parametrize('kind',('packet','source','frontier','wrapper_code'))
def test_actual108_initial_issued_scope_rejects_mutation(monkeypatch,kind):
    changed=False
    restore=[]
    def mutation(context,reuse):
        nonlocal changed
        if changed:return
        changed=True
        if kind=='packet':
            packet=context.unit.packet
            object.__setattr__(packet,'root',MappingProxyType({**packet.root,'configuration_nodes_hash':'0'*64}))
        elif kind=='source':
            request=reuse._READ.get().proof.normalized_snapshot[4][0][1]
            proposal=request.entry.proposal
            quote=context.source._quotes[(proposal.ticker,proposal.boundary_ms)]
            restore.append((quote,quote.bid_int))
            object.__setattr__(quote,'bid_int',quote.bid_int+1)
        elif kind=='frontier':
            journal=reuse._READ.get().proof.owner.publisher.journal
            monkeypatch.setattr(journal,'_fenced_sequence',journal._fenced_sequence+1)
        else:
            from src.backend.backtest_fixed_structural_lot_management import NativeFixedStructuralLotManagement
            opened=NativeFixedStructuralLotManagement.open.__wrapped__
            monkeypatch.setattr(opened,'__code__',opened.__code__.replace(co_name='foreign_open'))
    try:
        with pytest.raises(ValueError,match='changed|frontier|structural'):
            _actual108(monkeypatch,mutation)
    finally:
        for quote,bid in restore:object.__setattr__(quote,'bid_int',bid)


@pytest.mark.parametrize('module',('initial_held_recovery_reuse_policy','proposal_decision_inventory_reuse_policy'))
@pytest.mark.parametrize('field',('input_contracts','rule_set_contracts'))
def test_paired_markers_require_exactly_one(module,field):
    from importlib import import_module
    from types import SimpleNamespace
    policy=import_module('src.trading_runtime.'+module)
    cls=policy.InitialHeldRecoveryReusePolicy if module.startswith('initial') else policy.ProposalDecisionInventoryReusePolicy
    require=policy.require_declared_initial_held_reuse if module.startswith('initial') else policy.require_declared_proposal_decision_reuse
    values=dict(input_contracts=(policy.INPUT,),rule_set_contracts=(policy.RULE,))
    require(SimpleNamespace(**values),cls(1,1,1,1))
    values[field]=values[field]*2
    with pytest.raises(ValueError,match='Paired'):require(SimpleNamespace(**values),cls(1,1,1,1))

def test_cold_fallback_suspends_all_owned_read_contexts(monkeypatch):
    from src.backend import backtest_fixed_lot_initial_recovery_reuse as initial
    from src.backend import backtest_fixed_lot_management_reuse as base
    tokens=(initial._READ.set(object()),base._ACTIVE.set(object()),base._CONTEXT_OWNER.set(object()))
    try:
        def original():
            assert initial.context_request(object()) is None
            assert base._ACTIVE.get() is None and base._CONTEXT_OWNER.get() is None
            return 'full original'
        assert initial._cold_loader(original)=='full original'
        assert initial._READ.get() is not None
    finally:
        initial._READ.reset(tokens[0]);base._ACTIVE.reset(tokens[1]);base._CONTEXT_OWNER.reset(tokens[2])


def test_actual108_foreign_no_lease_reader_replays_original_context(monkeypatch):
    _actual108(monkeypatch,cold_probe=True)

@pytest.mark.parametrize('kind',('factory','payload','proposal_code','getter','decoder','registration'))
def test_actual108_issued_selected_factory_payload_and_code_reject(monkeypatch,kind):
    changed=False;restore=[]
    def mutate(context,reuse):
        nonlocal changed
        if changed:return
        from src.trading_runtime.proposal_decision_inventory_reuse_policy import ProposalDecisionInventoryReusePolicy
        if type(reuse._READ.get().policy) is not ProposalDecisionInventoryReusePolicy:return
        changed=True
        if kind in ('factory','registration'):
            from src.trading_runtime import strategy_registry as registry
            source=context.source;number=source.installed_payload['strategy']['strategy_number']
            release=registry._NUMBERED_RELEASES[number]
            registration=registry._FIXED_REGISTRY[(release.executor_strategy_id,release.executor_revision)]
            if kind=='registration':
                from dataclasses import replace
                monkeypatch.setitem(registry._FIXED_REGISTRY,(release.executor_strategy_id,release.executor_revision),replace(registration))
            else:
                restore.append((registration,'contract_factory',registration.contract_factory))
                object.__setattr__(registration,'contract_factory',lambda:None)
        elif kind=='payload':
            import json
            source=context.source;payload=source.installed_payload
            payload['strategy']['parameters']['proposal_decision_inventory_reuse_policy']['max_inventory_entries']=1
            restore.append((source,'installed_json',source.installed_json))
            object.__setattr__(source,'installed_json',json.dumps(payload))
        elif kind=='getter':
            method=type(context.source).installed_payload.fget
            monkeypatch.setattr(method,'__code__',method.__code__.replace(co_name='foreign_payload_getter'))
        elif kind=='decoder':
            from src.backend import backtest_fixed_structural_lot_source as module
            monkeypatch.setattr(module.json._default_decoder,'strict',False)
        else:
            from src.backend.backtest_fixed_structural_lot_management import NativeFixedStructuralLotManagement
            method=NativeFixedStructuralLotManagement.propose
            monkeypatch.setattr(method,'__code__',method.__code__.replace(co_name='foreign_proposal'))
    try:
        with pytest.raises(ValueError,match='changed|differs|source|fingerprint|factory'):_actual108(monkeypatch,mutate)
    finally:
        for obj,name,value in restore:object.__setattr__(obj,name,value)


def test_actual108_paired_initial_and_proposal_phases_same_run(monkeypatch):
    import json,hashlib
    from pathlib import Path
    counts=_actual108(monkeypatch,paired=True)
    assert len(counts['initial_pairs'])==1 and len(counts['proposal_pairs'])>=3
    assert len(counts['output'])==1
    encoded=json.dumps(counts['output'][0],sort_keys=True,separators=(',',':')).encode()
    receipt=dict(status='passed',initial_pairs=counts['initial_pairs'],proposal_pairs=counts['proposal_pairs'],output=counts['output'][0],output_sha256=hashlib.sha256(encoded).hexdigest(),control='external new memo disabled;existing policies/source guards retained',same_run_source_config_prefix=True,action_state_equality=True,control_economic_submitted=False,actual_selected_runtime_submitted=True,financial_acceptance=False,full_session_acceptance=False)
    path=Path('D:/TradingML/runtimes/strategy-optimization-20261005/strategy108-paired-native-phase-measurement-v1.json')
    path.write_text(json.dumps(receipt,indent=2))
    print(json.dumps({'initial_pairs':receipt['initial_pairs'],'proposal_pairs':receipt['proposal_pairs'],'output_sha256':receipt['output_sha256']}))

@pytest.mark.parametrize('kind',('private_tamper','row_budget'))
def test_actual108_private_inventory_tamper_and_budget(monkeypatch,kind):
    """Genuine issued scope, full context replay; controlled inventory transport.

    This qualifies optional cache semantics, not a second cold OMS session.
    """
    checked=[];operations=[]
    def probe(context,reuse):
        if checked:return
        checked.append(True)
        from src.backend import backtest_fixed_lot_management_reuse as base
        operation=reuse._READ.get();operations.append(operation)
        kwargs={'fixed_lot_contexts':operation.proof.frontier[-2],
                'qualification_inventory':kind}
        rows=[{'quantity':166,'price':Decimal('11.92')}] if kind=='private_tamper' else [{} for _ in range(operation.policy.max_inventory_rows+1)]
        calls=[]
        def original():
            assert reuse._READ.get() is None and base._ACTIVE.get() is None
            assert base._CONTEXT_OWNER.get() is None
            assert context.verify_source() is not None
            calls.append(True)
            return rows
        before=set(operation.cache)
        first=reuse.inventory_read(operation.proof.owner.client,operation.proof.prefix,kwargs,original)
        assert first==rows and len(first)==len(rows) and len(calls)==1
        if kind=='private_tamper':
            key=(set(operation.cache)-before).pop()
            value,_,_=operation.cache[key]
            assert value is not rows and value[0] is not rows[0]
            first[0]['quantity']=0
            hit=reuse.inventory_read(operation.proof.owner.client,operation.proof.prefix,kwargs,original)
            assert hit[0]['quantity']==166 and len(calls)==1
            value[0]['quantity']=0
            with pytest.raises(ValueError,match='Private initial-held OMS inventory changed'):
                reuse.inventory_read(operation.proof.owner.client,operation.proof.prefix,kwargs,original)
            assert len(calls)==1
            operation.cache.pop(key)
        else:
            assert set(operation.cache)==before
            second=reuse.inventory_read(operation.proof.owner.client,operation.proof.prefix,kwargs,original)
            assert second==rows and len(second)==operation.policy.max_inventory_rows+1
            assert len(calls)==2 and set(operation.cache)==before
            assert operation.proof.owner._verification_reuse_bypass_counts['inventory_budget']>=2
    _actual108(monkeypatch,probe)
    assert checked and all(not op.cache and op.bytes==0 for op in operations)


def test_actual108_independently_prepared_cold_oms_and_companion_mutation(monkeypatch):
    counts=_actual108(monkeypatch,cold_graph=True)
    assert counts['cold_graph']['distinct_source']
    print('independent108_cold_oms='+repr(counts['cold_graph']))



def _two_ticker_producer_inputs(monkeypatch):
    """Explicit synthetic producer products; compile actual causal authorities."""
    from dataclasses import replace
    from uuid import uuid4
    import test_fixed_structural_lot_source_v2 as fixture
    from src.backend import backtest_fixed_structural_lot_source_v2 as scope
    from src.backend.backtest_strategy_one_candidate_store import _token
    from src.backend.backtest_market_data import project_market_day_plan
    from src.backend.backtest_strategy_rising_momentum import load_rising_momentum_plan
    from test_backtest_strategy_initial_momentum_growth import StrongFirst
    from src.backend.backtest_strategy_initial_ten_percent import compile_initial_ten_percent_plan
    from src.backend.backtest_strategy_first_price_source import load_first_price_source
    from src.backend.backtest_strategy_certified_price_break import compile_certified_price_break_plan,CertifiedPriceReadbackAuthority
    from test_backtest_strategy_first_price_source import Bars
    from src.backend.backtest_strategy_entry_activity_source import load_entry_activity_plan
    from test_backtest_strategy_entry_activity_source import ActivityBars
    from src.backend.backtest_strategy_episode_activity_gate import compile_episode_activity_static_gate
    from src.backend.backtest_strategy_episode_activity_source import EpisodeActivityReadbackAuthority
    plans,authority,old,proposal,calls=_ORIGINAL_TWO_INPUTS(monkeypatch)
    candidates=plans.candidates;row=candidates.prepared[0]
    coverage=(candidates.coverage[0],replace(candidates.coverage[1],candidate_count=len(row.boundary_ms),derivation_attempt_id=str(uuid4())))
    candidates=replace(candidates,coverage=coverage,prepared=(row,replace(row,ticker='ZZZ')),
        token=_token(candidates.source_build_id,candidates.candidate_rule_digest,candidates.scan_query_sha256,coverage))
    parent=authority.plan.source.parent
    entry=replace(parent.entry,coverage=(*parent.entry.coverage,*(replace(c,ticker='ZZZ') for c in parent.entry.coverage)),
        activations=(*parent.entry.activations,*(replace(c,ticker='ZZZ') for c in parent.entry.activations)),
        candidates=(*parent.entry.candidates,*(replace(c,ticker='ZZZ') for c in parent.entry.candidates)))
    momentum=load_rising_momentum_plan(plans.market,candidates,client=StrongFirst())
    initial=compile_initial_ten_percent_plan(candidates,entry,momentum)
    price=compile_certified_price_break_plan(load_first_price_source(plans.market,initial,client=Bars()))
    activity=load_entry_activity_plan(plans.market,price,client=ActivityBars())
    episode=EpisodeActivityReadbackAuthority(old.run_id,compile_episode_activity_static_gate(activity),80)
    authority=CertifiedPriceReadbackAuthority(old.run_id,price,episode)
    seed=replace(plans.seeds,units=(*plans.seeds.units,dict(plans.seeds.units[0],ticker='ZZZ')))
    intervals=plans.v7_intervals
    intervals=replace(intervals,coverage=(*intervals.coverage,replace(intervals.coverage[0],ticker='ZZZ',attempt_id=str(uuid4()))),
        valid_seconds=(*intervals.valid_seconds,('ZZZ',intervals.valid_seconds[0][1])),
        intervals=(*intervals.intervals,('ZZZ',intervals.intervals[0][1])))
    plans=replace(plans,candidates=candidates,execution_market=project_market_day_plan(plans.market,('AAA','ZZZ')),
        entry=entry,seeds=seed,v7_intervals=intervals)
    monkeypatch.setattr(scope,'certify_candidate_plan',lambda *a,**k:candidates)
    monkeypatch.setattr(scope,'certified_seed_plan',lambda *a,**k:seed)
    monkeypatch.setattr(scope,'certify_v7_interval_plan',lambda *a,**k:intervals)
    monkeypatch.setattr(scope,'_load_quotes',lambda *a,**k:(*old.quotes,*(replace(q,ticker='ZZZ') for q in old.quotes)))
    return plans,authority,old,proposal,calls

_ORIGINAL_TWO_INPUTS=None

def test_actual108_two_ticker_historical_source_mutation(monkeypatch):
    import test_fixed_structural_lot_source_v2 as fixture
    import test_fixed_structural_lot_checkpoint_reader_profile as transport
    global _ORIGINAL_TWO_INPUTS
    _ORIGINAL_TWO_INPUTS=fixture.inputs
    monkeypatch.setattr(fixture,'inputs',_two_ticker_producer_inputs)
    def two_price_rows(plans,query):
        import json,re
        from tests.test_backtest_liquidity_price import Reader
        response=Reader().execute(query)
        if 'FROM arte.liquidity_execution_price_' not in query:return response
        rows=[]
        selected=set(re.findall(r"toDate\('[^']+'\),'([^']+)'",query))
        for unit in plans.execution_market.units:
            if unit.stage!='broker_100ms' or unit.ticker not in selected:continue
            for line in response.splitlines():
                row=json.loads(line);row.update(session_date=unit.session_date,ticker=unit.ticker,source_attempt_text=unit.attempt_id)
                rows.append(row)
        return '\n'.join(json.dumps(row) for row in rows)
    monkeypatch.setattr(transport,'controlled_price_rows',two_price_rows)
    original_ordinal=transport.ordinal_transport_plan
    def ordinal(plan):
        from dataclasses import replace
        pieces=[original_ordinal(replace(plan,coverage=(unit,),valid_seconds=((unit.ticker,dict(plan.valid_seconds)[unit.ticker]),),intervals=((unit.ticker,dict(plan.intervals)[unit.ticker]),))) for unit in plan.coverage]
        return replace(plan,coverage=tuple(p.coverage[0] for p in pieces),intervals=tuple(p.intervals[0] for p in pieces))
    monkeypatch.setattr(transport,'ordinal_transport_plan',ordinal)
    checked=set()
    def mutate(context,reuse):
        operation=reuse._READ.get()
        kind=type(operation.policy).__name__
        contexts=operation.proof.owner.client.fixed_structural_lot_contexts
        if kind in checked or len(contexts)<2:return
        assert {c.record.payload['ticker'] for c in contexts}=={'AAA','ZZZ'}
        checked.add(kind)
        source=context.source;quote=source._quotes[('ZZZ',41000)];before=quote.bid_int
        current_bid=source._quotes[('AAA',41000)].bid_int
        try:
            object.__setattr__(quote,'bid_int',before+1)
            assert source._quotes[('AAA',41000)].bid_int==current_bid
            with pytest.raises(ValueError):operation.require()
        finally:object.__setattr__(quote,'bid_int',before)
    _actual108(monkeypatch,mutate,two_tickers=True)
    assert checked=={'InitialHeldRecoveryReusePolicy','ProposalDecisionInventoryReusePolicy'}
    print('two_ticker_historical_mutation_rejected_scopes='+repr(sorted(checked)))
