"""Actual normalized config/canonical identity/candidate reader component tests.

Own run fencing and market certification are explicit uninstalled fixtures.
No installed or historical financial admission is claimed.
"""
from copy import deepcopy
from dataclasses import replace, FrozenInstanceError
from hashlib import sha256
import json
from types import SimpleNamespace
import pytest

from test_declared_native_fixed_capabilities import prepared
from test_declared_native_fixed_candidate import candidate, APPROVAL
from test_backtest_strategy_one_identity import Client as IdentityReader, ROWS
from test_backtest_strategy_one_candidate_store import Reader as CandidateReader, _market
from src.backend.fixed_bar_signal import first_squeeze_sql
from src.backend.backtest_strategy_one_identity import identity_content_hash
from src.backend.backtest_v4_run_context import historical_simulated_account_ids
from src.trading_runtime.runtime import RunMode
from src.trading_runtime.declared_native_entry_source import DeclaredNativeEntrySourcePolicy
from src.trading_runtime.declared_native_entry_request import inherited_fixed_entry_request_policy
from src.backend import backtest_declared_native_fixed_assignments as module

RUN='11111111-1111-4111-8111-111111111111'


@pytest.fixture
def setup(monkeypatch):
    # Original ancestor fixture omits account keys because its release-only
    # tests do not construct a runtime. Complete this test-owned source fixture
    # before normalized ancestor/parent compilation; never mutate the envelope.
    import test_strategy_two_configuration as ancestor_fixture
    full=deepcopy(ancestor_fixture.ONE)
    full['accounts']['bindings']=[{'account_key':'account-1','enabled':True,'modes':['backtest']},
                                 {'account_key':'account-2','enabled':True,'modes':['backtest']}]
    monkeypatch.setattr(ancestor_fixture,'ONE',full)
    foundation=prepared.__wrapped__(SimpleNamespace(param=59),monkeypatch)
    import src.backend.backtest_fixed_v4_certification as cert
    monkeypatch.setattr(cert,'certify_numbered_fixed_v4_projection',lambda number:'c'*64)
    from src.trading_runtime.declared_native_execution import (DeclaredNativeExecutionSpec,
        prepare_declared_execution_configuration)
    spec=DeclaredNativeExecutionSpec(candidate(foundation),
        DeclaredNativeEntrySourcePolicy('declared-entry-spread-risk-quote-source@2'),module.DeclaredAssignmentPolicy(),
        inherited_fixed_entry_request_policy())
    client=foundation[2]
    envelope=prepare_declared_execution_configuration(client,spec,approval=APPROVAL)
    market=replace(_market(),build_id='a'*64,token='b'*64)
    identities=IdentityReader(rows=[{**row,'ticker':ticker} for row,ticker in zip(ROWS,market.tickers)])
    identities.coverage[0]['content_hash']=identity_content_hash(identities.rows)
    candidates=CandidateReader();execute=client.execute
    def read(sql):
        if 'identity_' in sql: return identities.execute(sql)
        if any(name in sql for name in ('strategy_one_candidate_','FROM system.tables','FROM system.parts')):
            answer=candidates.execute(sql)
            if 'candidate_coverage' in sql:
                rows=[json.loads(row) for row in answer.splitlines()]
                for row in rows: row['scan_query_sha256']=sha256(first_squeeze_sql(market,through_boundary_ms=57_600_000).encode()).hexdigest()
                answer='\n'.join(json.dumps(row) for row in rows)
            return answer
        return execute(sql)
    client.execute=read
    native=dict(run_id=RUN,mode='backtest',strategy_id=spec.candidate.base.identity.strategy_id,
        strategy_revision=spec.candidate.base.identity.revision,configuration_hash=envelope['payload_hash'],
        market_plan_token=market.token,session_date=market.sessions[0],evaluation_interval_ms=100,
        account_ids=historical_simulated_account_ids(mode=RunMode.BACKTEST,configuration=envelope['payload']))
    saved=dict(definition=dict(ticker_population_mode='market_plan',final_session_date=market.sessions[0],end_local_ms=34200000),tickers=(),assignments=())
    import src.trading_runtime.arte_journal_writer as journal
    import src.trading_runtime.arte_backtest_definition as definitions
    monkeypatch.setattr(journal,'load_typed_run_context',lambda c,r:deepcopy(native))
    monkeypatch.setattr(definitions,'load_backtest_definition',lambda c,r,run_context:deepcopy(saved))
    checks=[];monkeypatch.setattr(module,'verify_market_day_plan',lambda m,c:checks.append(m.token))
    args=dict(run_id=RUN,spec=spec,envelope=envelope,approval=APPROVAL,market=market)
    return SimpleNamespace(client=client,args=args,native=native,saved=saved,identities=identities,
        candidates=candidates,checks=checks,monkeypatch=monkeypatch)


def build(x): return module.prepare_declared_assignment_plan(x.client,**x.args)


def test_real_complete_readers_and_exact_scope(setup):
    x=setup;plan=build(x)
    assert plan.candidate_tickers==('ABCD',)  # certified empty EFGH remains identity-only
    assert len(plan.scopes)==len(plan.account_bindings)
    assert len(x.identities.queries)==2 and x.candidates.queries and x.checks
    row=plan.scopes[0]
    assert row.conid==101 and row.permissions==(True,)*6
    assert json.loads(row.parameters_json)==x.args['envelope']['payload']['strategy']['parameters']
    assert row.tick_size==json.loads(row.parameters_json)['execution']['tick_size']
    assert plan.resolve(row.assignment_id,account_id=row.account_id,ticker=row.ticker,
        strategy_id=row.strategy_id,revision=row.revision)==row
    assert module.resolve_declared_assignment(x.client,**x.args,assignment_id=row.assignment_id,
        account_id=row.account_id,ticker=row.ticker,strategy_id=row.strategy_id,revision=row.revision)==row
    assert all(sql.startswith('SELECT') for sql in x.identities.queries+x.candidates.queries)
    with pytest.raises(FrozenInstanceError): plan.token='f'*64


@pytest.mark.parametrize('field,value',[('account_id','valid-but-other-account'),('ticker','EFGH'),
    ('strategy_id','foreign'),('revision',59),('revision',True),('revision',9017.0),('account_id',True),('ticker',1.0),('assignment_id',RUN)])
def test_crossed_and_foreign_identity_cannot_resolve(setup,field,value):
    plan=build(setup);row=plan.scopes[0]
    scope=dict(assignment_id=row.assignment_id,account_id=row.account_id,ticker=row.ticker,
        strategy_id=row.strategy_id,revision=row.revision);scope[field]=value
    with pytest.raises(ValueError,match='binding differs'):plan.resolve(**scope)


@pytest.mark.parametrize('field,value',[('configuration_hash','f'*64),('market_plan_token','f'*64),
    ('strategy_revision',59),('account_ids',('SIM-foreign',)),('mode','live')])
def test_fresh_run_scope_drift_rejected_before_identity_reads(setup,field,value):
    setup.native[field]=value
    with pytest.raises(ValueError):build(setup)
    assert not setup.identities.queries


@pytest.mark.parametrize('mode',['missing','changed','duplicate','future'])
def test_canonical_identity_corruption_rejected(setup,mode):
    rows=setup.identities.rows
    if mode=='missing':setup.identities.rows=rows[:1]
    if mode=='changed':setup.identities.rows=[{**rows[0],'ibkr_conid':999},rows[1]]
    if mode=='duplicate':setup.identities.rows=[rows[0],rows[0]]
    if mode=='future':setup.identities.rows=[{**rows[0],'source_inserted_at':'2026-08-18 08:01:00.000'},rows[1]]
    with pytest.raises(RuntimeError):build(setup)


def test_saved_assignment_membership_has_only_actual_schema_and_exact_order(setup):
    x=setup;plan=build(x)
    x.saved['assignments']=tuple({'assignment_id':row.assignment_id} for row in plan.scopes)
    assert build(x)==plan
    x.saved['assignments']=({'assignment_id':RUN},)
    with pytest.raises(ValueError,match='saved assignment membership'):build(x)


def test_source_and_candidate_population_cannot_be_narrowed(setup):
    setup.saved['definition']['ticker_population_mode']='explicit'
    setup.saved['tickers']=({'ticker':'ABCD'},)
    with pytest.raises(ValueError,match='full population'):build(setup)
    assert not setup.identities.queries


def test_missing_candidate_certificate_not_empty_authority(setup):
    setup.candidates.missing=True
    with pytest.raises(RuntimeError):build(setup)


def test_configuration_mutation_cannot_reseal_complete_config(setup):
    setup.args['envelope']['payload']['strategy']['parameters']['execution']['tick_size']=0.02
    with pytest.raises(ValueError):build(setup)
    assert not setup.identities.queries


def test_plan_resealed_missing_scope_still_rejected(setup):
    plan=build(setup)
    payload=plan.payload();payload['scopes']=[]
    with pytest.raises(ValueError,match='missing, duplicate or narrowed'):
        replace(plan,scopes=(),token=module._hash(payload))


def test_policy_strict_and_detached():
    policy=module.DeclaredAssignmentPolicy();copy=policy.payload();copy['permissions']['enter']=False
    assert policy.payload()['permissions']['enter'] is True
    for value in (True,1,'foreign'):
        with pytest.raises(ValueError):module.DeclaredAssignmentPolicy(value)


def test_crossed_two_valid_accounts_and_tickers_do_not_alias_scope(setup):
    original=build(setup);first=original.scopes[0]
    # Pure complete plan validator fixture: both account/ticker identities are
    # valid simultaneously, and each deterministic own assignment is sealed.
    bindings=((first.account_key,first.account_id),('other-key','SIM-02-OTHER'))
    tickers=('ABCD','EFGH')
    scopes=tuple(replace(first,account_key=key,account_id=account,ticker=ticker,
        assignment_id=module._assignment_id(first.run_id,first.strategy_id,first.revision,key,ticker))
        for key,account in bindings for ticker in tickers)
    payload=original.payload();payload.update(account_bindings=bindings,candidate_tickers=tickers,
        scopes=[row.payload() for row in scopes])
    plan=replace(original,account_bindings=bindings,candidate_tickers=tickers,scopes=scopes,token=module._hash(payload))
    for row in scopes:
        assert plan.resolve(row.assignment_id,account_id=row.account_id,ticker=row.ticker,
            strategy_id=row.strategy_id,revision=row.revision)==row
    for field,value in [('account_id',bindings[1][1]),('ticker','EFGH')]:
        requested=dict(account_id=scopes[0].account_id,ticker=scopes[0].ticker,
            strategy_id=first.strategy_id,revision=first.revision);requested[field]=value
        with pytest.raises(ValueError,match='binding differs'):
            plan.resolve(scopes[0].assignment_id,**requested)


def test_prepared_lookup_does_not_rehash_plan_and_index_is_immutable(setup,monkeypatch):
    plan=build(setup);row=plan.scopes[0]
    monkeypatch.setattr(module,'_hash',lambda value:pytest.fail('hot resolve rehashed complete plan'))
    for _ in range(10):
        assert plan.resolve(row.assignment_id,account_id=row.account_id,ticker=row.ticker,
            strategy_id=row.strategy_id,revision=row.revision)==row
    with pytest.raises(TypeError):plan._index[row.assignment_id]=row


def test_actual_complete_two_account_bindings_reject_crossed_valid_account(setup):
    plan=build(setup)
    assert len(plan.scopes)==2
    first,second=plan.scopes
    assert first.account_id!=second.account_id and first.ticker==second.ticker
    with pytest.raises(ValueError,match='binding differs'):
        plan.resolve(first.assignment_id,account_id=second.account_id,ticker=first.ticker,
            strategy_id=first.strategy_id,revision=first.revision)


def test_account_binding_order_and_slug_collision_fail_closed(setup):
    setup.native['account_ids']=tuple(reversed(setup.native['account_ids']))
    with pytest.raises(ValueError,match='saved order'):build(setup)
    assert not setup.identities.queries


def test_corrupted_candidate_rows_never_create_assignment(setup):
    setup.candidates.changed=True
    with pytest.raises(RuntimeError):build(setup)
    assert not setup.identities.queries
