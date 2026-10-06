"""Component readback, not installed native approval.

Normalized parent/config reconstruction and physical Arrow/quote reloads are
real. Fenced own run, market/product certification fixtures replace unavailable
unregistered own native storage; they never attest historical financial state.
"""
from copy import deepcopy
from dataclasses import replace, asdict
from hashlib import sha256
import json
from types import SimpleNamespace

import pyarrow as pa
import pytest

from test_declared_native_fixed_capabilities import prepared
from test_declared_native_fixed_candidate import candidate, APPROVAL
from test_backtest_strategy_entry_activity_source import source_authority
from test_backtest_strategy_initial_momentum_growth import StrongFirst
from test_backtest_declared_native_fixed_entry import Source, financial, RUN
from src.backend.backtest_declared_native_fixed_plan import compile_declared_momentum_plan, load_declared_entry_source_plan
from src.backend.backtest_declared_native_fixed_entry import DeclaredEntryPreparation
from src.trading_runtime.declared_native_fixed_candidate import prepare_candidate_configuration
from src.trading_runtime.declared_native_entry_source import DeclaredNativeEntrySourcePolicy
from src.trading_runtime.arte_declared_native_fixed_sources import (
    PreparedDeclaredSourceResolver, DeclaredHistoricalPredecessor, _digest, QUOTE_CONTRACT,
)
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix


@pytest.fixture
def setup(monkeypatch):
    foundation=prepared.__wrapped__(SimpleNamespace(param=59),monkeypatch)
    # Existing source-guard suites cover this parent's full proof. This explicit
    # component seam avoids repeatedly rerunning that overlapping proof; it is
    # not an own installed certificate or historical financial approval.
    import src.backend.backtest_fixed_v4_certification as source_certification
    certificate_calls=[]
    def certificate(number):
        assert number==foundation[3].strategy_number
        certificate_calls.append(number)
        return 'c'*64
    monkeypatch.setattr(source_certification,'certify_numbered_fixed_v4_projection',certificate)
    spec=candidate(foundation)
    client=foundation[2]
    envelope=prepare_candidate_configuration(client,spec,approval=APPROVAL)
    market,price=source_authority()
    original=price.source.parent
    broker=replace(market.units[0],stage='broker_100ms',attempt_id=original.candidates.coverage[0].source_attempts[2])
    market=replace(market,units=(*market.units,broker))
    parent=compile_declared_momentum_plan(spec.base,market,original.candidates,original.entry,original.momentum)
    source=Source(parent)
    execute=client.execute
    client.execute=lambda sql: '1' if sql=="SELECT getSetting('readonly')" else source.execute(sql) if 'FROM arte.liquidity_100ms_v1' in sql else execute(sql)
    def arrow(sql):
        if 'FROM arte.indicators_v1' in sql:
            return StrongFirst().iter_arrow_record_batches(sql)
        return source.iter_arrow_record_batches(sql)
    client.iter_arrow_record_batches=arrow
    ownplan=load_declared_entry_source_plan(parent,client=client,candidate=spec)
    proposal=DeclaredEntryPreparation(RUN,'assignment','account',ownplan).propose(*parent.momentum.keys[0],financial(parent)).proposal
    context=dict(run_id=RUN,mode='backtest',strategy_id=spec.base.identity.strategy_id,
        strategy_revision=spec.base.identity.revision,configuration_hash=envelope['payload_hash'],
        market_plan_token=market.token,session_date=market.sessions[0],evaluation_interval_ms=100,
        account_ids=('account',))
    saved=dict(definition=dict(ticker_population_mode='market_plan',final_session_date=market.sessions[0],
        end_local_ms=34200000),tickers=(),assignments=({'assignment_id':'assignment'},))
    import src.trading_runtime.arte_journal_writer as journal
    import src.trading_runtime.arte_backtest_definition as definitions
    import src.trading_runtime.arte_declared_native_fixed_sources as module
    monkeypatch.setattr(journal,'load_typed_run_context',lambda c,r:deepcopy(context))
    monkeypatch.setattr(definitions,'load_backtest_definition',lambda c,r,run_context:deepcopy(saved))
    calls=[]
    monkeypatch.setattr(module,'verify_market_day_plan',lambda m,c:calls.append('market'))
    import src.backend.backtest_strategy_one_candidate_store as candidates
    import src.backend.backtest_strategy_one_activation as activations
    import src.backend.structural_v7_seed as seeds
    import src.backend.backtest_strategy_one_pivot_store as pivots
    import src.backend.backtest_strategy_one_hod_store as hod
    import src.backend.backtest_strategy_one_entry_store as entry
    def certify(m,*,candidate_rule_digest,through_boundary_ms,client,batch_size=512):
        assert m.build_id==market.build_id and m.token==market.token and through_boundary_ms==19800000
        calls.append('candidates');return original.candidates
    monkeypatch.setattr(candidates,'certify_candidate_plan',certify)
    monkeypatch.setattr(activations,'load_strategy_one_activations',lambda *a,**k:calls.append('activations'))
    monkeypatch.setattr(seeds,'certified_seed_plan',lambda *a,**k:calls.append('seeds'))
    monkeypatch.setattr(pivots,'certify_pivot_plan',lambda *a,**k:calls.append('pivots'))
    monkeypatch.setattr(hod,'certify_hod_plan',lambda *a,**k:calls.append('hod'))
    monkeypatch.setattr(entry,'certify_entry_evidence_plan',lambda *a,**k:(calls.append('entry'),original.entry)[1])
    resolver=PreparedDeclaredSourceResolver(client,run_id=RUN,candidate=spec,envelope=envelope,
        approval=APPROVAL,market=market,source_policy=DeclaredNativeEntrySourcePolicy(QUOTE_CONTRACT))
    return SimpleNamespace(resolver=resolver,proposal=proposal,context=context,saved=saved,client=client,
        source=source,market=market,spec=spec,envelope=envelope,calls=calls,monkeypatch=monkeypatch,
        certificate_calls=certificate_calls,candidates=original.candidates)


def test_independent_full_plan_reload_reconstructs_complete_entry_and_witnesses(setup):
    x=setup;value=x.resolver.reconstruct_entry_facts(x.proposal)
    assert value.proposal==x.proposal
    assert value.initial.first_setup.boundary_ms==31000
    assert value.momentum.boundary_ms==31000
    assert value.first_price.first_setup_boundary_ms==31000
    assert value.activity.candles[0].boundary_ms==15000
    assert value.content_hash==_digest(value.payload())
    assert x.resolver.compare_entry_witnesses(x.proposal,value)==value
    assert set(x.calls)=={'market','candidates','activations','seeds','pivots','hod','entry'}
    assert len(x.certificate_calls)>=4
    assert any('attempt_id=toUUID' in q for q in x.source.queries)


@pytest.mark.parametrize('field,value',[('run_id','22222222-2222-4222-8222-222222222222'),
    ('revision',9018),('strategy_number',59),('source_token','f'*64),('reference_ask',1.05),
    ('account_id','foreign'),('assignment_id','foreign'),('intent_id',RUN)])
def test_own_identity_and_complete_scalar_corruption_rejected(setup,field,value):
    with pytest.raises(ValueError):setup.resolver.reconstruct_entry_facts(replace(setup.proposal,**{field:value}))


@pytest.mark.parametrize('mode',['missing','duplicate','foreign','future','bool'])
def test_independent_quote_readback_not_caller_token(setup,mode):
    setup.source.quote=mode
    with pytest.raises(ValueError):setup.resolver.reconstruct_entry_facts(setup.proposal)


@pytest.mark.parametrize('stage,mode',[('price','missing'),('activity','missing'),('activity','fade')])
def test_actual_original_price_or_activity_loss_cannot_reuse_old_token(setup,stage,mode):
    getattr(setup.source,stage).mode=mode
    with pytest.raises(ValueError,match='reconstructed causal selection|complete source'):
        setup.resolver.reconstruct_entry_facts(setup.proposal)


def test_first_anchor_and_contenthash_cannot_be_resealed_by_caller(setup):
    expected=setup.resolver.reconstruct_entry_facts(setup.proposal)
    wrong=replace(expected,initial=replace(expected.initial,first_setup=replace(expected.initial.first_setup,boundary_ms=41000)))
    wrong=replace(wrong,content_hash=_digest(wrong.payload()))
    with pytest.raises(ValueError,match='witness fields'):
        setup.resolver.compare_entry_witnesses(setup.proposal,wrong)
    with pytest.raises(ValueError,match='content hash'):
        setup.resolver.compare_entry_witnesses(setup.proposal,replace(expected,content_hash='f'*64))


@pytest.mark.parametrize('field,value',[('configuration_hash','f'*64),('market_plan_token','f'*64),
    ('strategy_revision',59),('run_id','22222222-2222-4222-8222-222222222222'),('mode','live')])
def test_fresh_run_binding_rejects_drift_before_producer_reads(setup,field,value):
    setup.source.queries.clear();setup.context[field]=value
    with pytest.raises(ValueError,match='fenced own run'):
        setup.resolver.reconstruct_entry_facts(setup.proposal)
    assert setup.source.queries==[]


def test_exact_three_source_attempts_rechecked_during_cold_reload(setup):
    setup.resolver.market=replace(setup.market,units=tuple(replace(u,attempt_id='22222222-2222-4222-8222-222222222222')
        if u.stage=='broker_100ms' else u for u in setup.market.units))
    with pytest.raises(ValueError,match='attempt differs'):
        setup.resolver.reconstruct_entry_facts(setup.proposal)


def test_complete_config_not_only_hash_or_caller_flags(setup):
    value=deepcopy(setup.envelope)
    value['payload']['run_plan']['name']='caller edited full configuration'
    setup.resolver._envelope=json.dumps(value)
    with pytest.raises(ValueError):setup.resolver.reconstruct_entry_facts(setup.proposal)


def test_population_narrowing_is_not_derived_from_proposal_or_token(setup):
    setup.saved['definition']['ticker_population_mode']='explicit'
    with pytest.raises(ValueError,match='population'):
        setup.resolver.reconstruct_entry_facts(setup.proposal)


def test_unfenced_research_exclusion_product_is_explicitly_unsupported(setup):
    import src.backend.backtest_strategy_one_candidate_store as candidates
    setup.monkeypatch.setattr(candidates,'certify_candidate_plan',lambda *a,**k:replace(setup.candidates,excluded_tickers=('ZZZ',)))
    setup.source.queries.clear()
    with pytest.raises(ValueError,match='generic cohort metadata; unsupported'):
        setup.resolver.reconstruct_entry_facts(setup.proposal)
    assert setup.source.queries==[]


def test_historical_interface_refuses_current_or_self_attested_financial_authority(setup):
    x=setup
    request=DeclaredHistoricalPredecessor(RUN,'33333333-3333-4333-8333-333333333333',
        '44444444-4444-4444-8444-444444444444',19,31000,x.envelope['payload_hash'],x.market.token,
        V4CommittedPrefix(RUN,19,'44444444-4444-4444-8444-444444444444','fixture:19','running',
            ('44444444-4444-4444-8444-444444444444',)),'a'*64,'b'*64)
    with pytest.raises(ValueError,match='installed V4 predecessor/source hook is missing'):
        x.resolver.verify_historical_predecessor(request)
    for changed in (replace(request,configuration_hash='f'*64),):
        with pytest.raises(ValueError,match='foreign'):
            x.resolver.verify_historical_predecessor(changed)
    with pytest.raises(ValueError,match='exact typed'):
        x.resolver.verify_historical_predecessor(SimpleNamespace(**asdict(request),financial_verified=True))
    with pytest.raises(ValueError,match='invalid scope'):
        replace(request,prefix=replace(request.prefix,last_sequence=20)) # Current head cannot substitute predecessor.


def test_manager_source_equivalence_does_not_approve_positions_or_restore(setup):
    from src.backend.backtest_declared_native_fixed_management import DeclaredManagementState
    p=setup.proposal
    state=DeclaredManagementState(RUN,p.source_token,41000,(((p.account_id,p.assignment_id,p.ticker),p),),(),(),(),(),())
    assert setup.resolver.reconstruct_manager_sources(state)[0].proposal==p
    with pytest.raises(ValueError,match='restore remains closed'):setup.resolver.verify_manager_state(state)
    with pytest.raises(ValueError,match='future clock'):setup.resolver.reconstruct_manager_sources(replace(state,boundary_ms=30000))
    with pytest.raises(ValueError,match='source image'):setup.resolver.reconstruct_manager_sources(replace(state,source_token='f'*64))


def install_completed_reader(x,*,mode='normal'):
    import re
    def arrow(sql):
        bucket=int(re.search(r'bucket_index=(\d+)',sql).group(1))
        if 'FROM arte.bars_v1' in sql:
            return iter([pa.RecordBatch.from_arrays([pa.array([bucket],pa.uint32()),pa.array([5000],pa.uint32()),
                pa.array([9900],pa.uint64()),pa.array([1],pa.uint8())],names=['bucket_index','resolution_ms','close_int','price_valid'])])
        return iter([pa.RecordBatch.from_arrays([pa.array([bucket],pa.uint32()),pa.array([5000],pa.uint32()),
            pa.array([float('inf') if mode=='infinite' else -.03],pa.float64()),pa.array([.02],pa.float64())],names=['bucket_index','resolution_ms','macd_line','macd_signal'])])
    x.client.iter_arrow_record_batches=arrow


def test_management_producer_fields_reloaded_without_inventing_held_state(setup):
    install_completed_reader(setup)
    value=setup.resolver.completed_producer_facts(setup.proposal.ticker,31000)
    assert value.completed_boundary_ms==30000 and value.macd_line==-.03
    assert value.source_attempts==setup.source.parent.candidates.coverage[0].source_attempts
    assert value.content_hash==_digest(value.payload())
    assert setup.resolver.compare_completed_facts(value)==value
    changed=replace(value,completed_boundary_ms=35000)
    changed=replace(changed,content_hash=_digest(changed.payload()))
    with pytest.raises(ValueError,match='producer fields'):
        setup.resolver.compare_completed_facts(changed)


def test_management_infinite_source_is_corrupt_not_unknown(setup):
    install_completed_reader(setup,mode='infinite')
    with pytest.raises(ValueError,match='nonfinite'):
        setup.resolver.completed_producer_facts(setup.proposal.ticker,31000)


def test_readonly_is_required_but_not_an_installed_principal_grant(setup):
    prior=setup.client.execute
    setup.client.execute=lambda sql:'0' if sql=="SELECT getSetting('readonly')" else prior(sql)
    with pytest.raises(ValueError,match='SELECT-only'):
        setup.resolver.reconstruct_entry_facts(setup.proposal)


def test_membership_cannot_authorize_crossed_assignment_scope(setup):
    x=setup
    x.context['account_ids']=('account','other-account')
    # These are the actual saved definition columns, not invented scope facts.
    x.saved['assignments']=({'assignment_id':'assignment'}, {'assignment_id':'other-assignment'})
    loaded=x.resolver.reload_entry_plan()
    # Build a complete source-equivalent proposal for the other valid account.
    from uuid import uuid5, NAMESPACE_URL
    from src.backend.backtest_declared_native_fixed_entry import ENTRY_FAMILY
    p=x.proposal
    intent=str(uuid5(NAMESPACE_URL,json.dumps((ENTRY_FAMILY,p.run_id,str(p.strategy_number),
        p.strategy_id,str(p.revision),p.source_token,p.assignment_id,'other-account',
        p.ticker,str(p.boundary_ms),str(p.episode_start_ms)),separators=(',',':'))))
    crossed=replace(p,account_id='other-account',intent_id=intent)
    assert x.resolver.reconstruct_entry_facts(crossed).proposal==crossed
    for proposal in (p,crossed):
        with pytest.raises(ValueError,match='assignment scope hook is missing'):
            x.resolver.reconstruct_entry(proposal)
    assert all(set(row)=={'assignment_id'} for row in x.saved['assignments'])
