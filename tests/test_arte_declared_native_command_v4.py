"""Real prepared journal/scalar/producer readback; product/run fences are fixtures.

No installed strategy, database table, historical finance or writer acceptance.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import pytest

from test_arte_declared_native_fixed_sources import setup as source_setup
from test_arte_declared_native_managed_sources import managed as managed_setup
from test_declared_native_fixed_candidate import APPROVAL
from test_backtest_declared_native_fixed_entry import financial
from test_backtest_strategy_one_identity import Client as IdentityReader, ROWS
from src.backend.backtest_strategy_one_identity import identity_content_hash
from src.backend.backtest_v4_run_context import historical_simulated_account_ids
from src.trading_runtime.runtime import RunMode
from src.backend.backtest_declared_native_fixed_entry import DeclaredEntryPreparation
from src.backend.backtest_declared_native_fixed_assignments import prepare_declared_managed_assignment_plan
from src.backend.backtest_declared_native_journal import DeclaredNativeJournal
from src.trading_runtime.declared_native_submission import DeclaredNativeSubmission, DeclaredSubmissionBinding, declared_entry_intent
from src.trading_runtime.arte_declared_native_fixed_sources import DeclaredHistoricalPredecessor
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from src.trading_runtime import arte_declared_native_command_v4 as module

BATCH = '22222222-2222-4222-8222-222222222222'
PRIOR = '33333333-3333-4333-8333-333333333333'
ATTEMPT = '44444444-4444-4444-8444-444444444444'


@pytest.fixture
def case(monkeypatch, request):
    import test_strategy_two_configuration as ancestor
    full = deepcopy(ancestor.ONE)
    full['accounts']['bindings'] = [{'account_key':'primary','modes':['backtest'],'enabled':True}]
    monkeypatch.setattr(ancestor,'ONE',full)
    if getattr(request, 'param', False):
        import test_arte_declared_native_fixed_sources as source_fixture
        original_candidate = source_fixture.candidate
        monkeypatch.setattr(source_fixture, 'candidate', lambda parent: original_candidate(parent, spread=True))
    x = managed_setup.__wrapped__(source_setup.__wrapped__(monkeypatch))
    accounts = historical_simulated_account_ids(mode=RunMode.BACKTEST,configuration=x.managed_envelope['payload'])
    x.context['account_ids'] = accounts
    x.saved['assignments'] = ()
    rows = [{**ROWS[i % len(ROWS)],'ticker':ticker} for i,ticker in enumerate(x.market.tickers)]
    identities = IdentityReader(rows=rows)
    identities.coverage[0].update(ticker_count=len(rows),universe_date=x.market.sessions[0],content_hash=identity_content_hash(rows))
    execute = x.client.execute
    x.client.execute = lambda sql: identities.execute(sql) if 'identity_' in sql else execute(sql)
    import src.backend.backtest_declared_native_fixed_assignments as assignments
    monkeypatch.setattr(assignments,'verify_market_day_plan',lambda m,c:None)  # product certificate fixture only
    args = dict(resolver=x.managed_resolver,spec=x.managed_spec,envelope=x.managed_envelope,
                approval=APPROVAL,market=x.market)
    plan = prepare_declared_managed_assignment_plan(x.client,run_id=x.context['run_id'],
        **{k:v for k,v in args.items() if k != 'resolver'})
    scope = next(s for s in plan.scopes if s.ticker == x.proposal.ticker)
    x.saved['assignments'] = tuple({'assignment_id':s.assignment_id} for s in plan.scopes)
    source, _, _, _ = x.managed_resolver.reload_entry_plan()
    prep = DeclaredEntryPreparation(x.context['run_id'],scope.assignment_id,scope.account_id,source)
    view = replace(financial(source.parent),account_id=scope.account_id,assignment_id=scope.assignment_id)
    proposal = prep.propose(x.proposal.ticker,x.proposal.boundary_ms,view).proposal
    binding = DeclaredSubmissionBinding(prep,date.fromisoformat(x.market.sessions[0]),
        x.managed_spec.execution.entry_request,x.managed_envelope['payload_hash'],module._hash(x.managed_spec.execution.payload()))
    submission = DeclaredNativeSubmission(binding,proposal,view,declared_entry_intent(binding,proposal))
    journal = DeclaredNativeJournal(binding=binding,run_id=proposal.run_id,initial_sequence=7)
    record = journal.append_declared_native_intent(submission=submission,intent=submission.intent,
        account_id=proposal.account_id,strategy_id=proposal.strategy_id,strategy_revision=proposal.revision)
    prefix = V4CommittedPrefix(proposal.run_id,7,PRIOR,'fixture:31000','running',(PRIOR,))
    predecessor = DeclaredHistoricalPredecessor(proposal.run_id,BATCH,PRIOR,7,proposal.boundary_ms,
        x.managed_envelope['payload_hash'],x.market.token,prefix,'a'*64,'b'*64)
    packet = module.project_declared_entry(record,submission,**args,predecessor=predecessor,
        attempt_id=ATTEMPT,batch_id=BATCH,run_month=submission.intent.event_time.date().replace(day=1),source_cursor='fixture:31000')
    return SimpleNamespace(x=x,args=args,packet=packet,predecessor=predecessor,record=record,submission=submission,journal=journal)


def read(c, packet=None):
    return module.readback_declared_entry_source_equivalence(c.packet if packet is None else packet,
        **c.args,predecessor=c.predecessor)


def reseal(c, family, index, field, value):
    families = [(name,list(rows)) for name,rows in c.packet.families]
    row = dict(families[family][1][index]);row.pop('content_hash');row[field] = value
    families[family][1][index] = module._seal(module.TABLES[family],row)
    return module.DeclaredEntryRows(c.packet.base,tuple((name,tuple(rows)) for name,rows in families))


def test_real_own_journal_to_full_normalized_cold_source_equivalence(case):
    c = case; value = read(c)
    assert value.proposal == c.submission.proposal
    assert value.configuration_hash == c.x.managed_envelope['payload_hash']
    assert c.packet.base.events[0]['entity_type'] == c.record.entity_type == 'declared_native_intent'
    assert c.packet.base.events[0]['record_id'] == c.record.record_id
    assert c.packet.base.events[0]['entity_id'] == c.record.entity_id
    assert c.packet.base.events[0]['sequence'] == c.record.sequence
    assert tuple(len(rows) for _,rows in c.packet.families) == (1,4,1,1,4)
    assert any('attempt_id=toUUID' in q for q in c.x.source.queries)
    assert c.packet.families[0][1][0]['assignment_id'] == c.submission.assignment_id
    with pytest.raises(TypeError): c.packet.families[0][1][0]['revision'] = 1
    assert c.journal.declared_submission_for_record(c.record.record_id) == c.submission


@pytest.mark.parametrize('case', [True], indirect=True)
def test_optional_quarter_spread_full_managed_journal_and_cold_source(case):
    value = read(case)
    assert value.proposal == case.submission.proposal
    row = case.packet.families[0][1][0]
    policy = case.x.managed_spec.execution.candidate.delta.spread
    assert row['spread_policy_id'] == policy.policy_id
    assert (row['spread_numerator'], row['spread_denominator']) == (1, 4)
    assert row['configuration_hash'] == case.x.managed_envelope['payload_hash']
    assert case.packet.base.events[0]['entity_type'] == 'declared_native_intent'


@pytest.mark.parametrize('family,field,value', [
    (0,'configuration_hash','f'*64),(0,'managed_spec_hash','f'*64),(0,'assignment_plan_token','f'*64),
    (0,'technical_attempt_id',ATTEMPT),(0,'broker_attempt_id',ATTEMPT),(0,'spread_numerator',1),
    (0,'predecessor_sequence',6),(0,'entry_request_hash','f'*64),(0,'reference_ask','1.000000000000000000'),
    (1,'current_line',.5),(1,'boundary_ms',41000),(2,'prior_high_int',1),(3,'entry_plan_token','f'*64),
    (4,'trade_count',999),
])
def test_resealed_source_lineage_or_witness_drift_fails(case,family,field,value):
    with pytest.raises(ValueError): read(case,reseal(case,family,0,field,value))


@pytest.mark.parametrize('family,field,value',[(0,'revision',9017.),(0,'boundary_ms',True),
    (0,'quote_valid',True),(0,'reference_ask',1.),(1,'resolution_ms',1000.),(4,'observed',False)])
def test_exact_normalized_scalar_aliases_reject(case,family,field,value):
    with pytest.raises(ValueError): reseal(case,family,0,field,value)


@pytest.mark.parametrize('mode',['family_missing','family_order','row_missing','row_duplicate','row_order','row_hash'])
def test_complete_family_row_order_omission_duplicate_and_hash_fail(case,mode):
    families = [(name,list(rows)) for name,rows in case.packet.families]
    if mode == 'family_missing': families.pop()
    elif mode == 'family_order': families.reverse()
    elif mode == 'row_missing': families[1][1].pop()
    elif mode == 'row_duplicate': families[1][1].append(families[1][1][0])
    elif mode == 'row_order': families[1][1].reverse()
    else:
        families[1][1][0] = dict(families[1][1][0],content_hash='f'*64)
    with pytest.raises(ValueError):
        read(case,module.DeclaredEntryRows(case.packet.base,tuple((name,tuple(rows)) for name,rows in families)))


@pytest.mark.parametrize('field,value',[('entity_type','strategy_intent'),('entity_id',ATTEMPT),
    ('account_id','foreign'),('sequence',9),('sequence',8.)])
def test_original_own_event_envelope_drift_rejects(case,field,value):
    with pytest.raises(ValueError):
        module.project_declared_entry(replace(case.record,**{field:value}),case.submission,**case.args,
            predecessor=case.predecessor,attempt_id=ATTEMPT,batch_id=BATCH,
            run_month=case.packet.base.run_month,source_cursor=case.packet.base.source_cursor)


def test_reloaded_product_failure_and_full_child_disagreement_reject(case):
    case.x.source.quote = 'missing'
    with pytest.raises(ValueError): read(case)
    case.x.source.quote = 'valid'
    args = dict(case.args,envelope=deepcopy(case.args['envelope']))
    args['envelope']['payload']['private_cash'] = 100000
    from test_declared_native_execution import seal
    seal(args['envelope'])
    with pytest.raises(ValueError):
        module.readback_declared_entry_source_equivalence(case.packet,**args,predecessor=case.predecessor)


def test_historical_financial_admission_stays_closed_and_tables_are_normalized(case):
    with pytest.raises(ValueError,match='historical'):
        module.verify_declared_entry_financial_admission(case.packet,**case.args,predecessor=case.predecessor)
    assert len(module.TABLES) == 5
    for contract in module.TABLES:
        assert all(not any(word in kind for word in ('Array','JSON','Blob')) for _,kind in contract.columns)
        assert "storage_policy = 'live_market_ssd'" in contract.ddl()
    assert case.packet.families[0][1][0]['portfolio_state_hash'] == 'a'*64  # requested identity, not approval


@pytest.mark.parametrize('field,value',[('batch_id',ATTEMPT),('first_sequence',8.),('source_cursor','foreign'),('run_month',date(2026,9,1))])
def test_cold_base_envelope_cannot_choose_its_own_predecessor_or_month(case,field,value):
    with pytest.raises(ValueError): read(case,replace(case.packet,base=replace(case.packet.base,**{field:value})))


def test_cold_original_intent_scalar_alias_and_price_mutation_reject(case):
    for field,value in (('outside_rth',True),('reference_price','1.000000000000000000')):
        intents=(dict(case.packet.base.intents[0],**{field:value}),)
        with pytest.raises(ValueError): read(case,replace(case.packet,base=replace(case.packet.base,intents=intents)))


@pytest.mark.parametrize('field', ['correlation_id', 'causation_id'])
def test_resealed_cold_event_cannot_choose_foreign_request_lineage(case, field):
    from src.trading_runtime.arte_journal_writer import typed_row
    original = case.packet.base.events[0]
    assert original[field] == ''
    event = typed_row('trading_event_v1', {**{key:value for key,value in original.items()
        if key != 'content_hash'}, field:ATTEMPT})
    with pytest.raises(ValueError):
        read(case, replace(case.packet, base=replace(case.packet.base, events=(event,))))


@pytest.mark.parametrize('field,value',[('portfolio_state_hash','f'*64),('market_plan_token','f'*64),('configuration_hash','f'*64),('boundary_ms',41000)])
def test_foreign_typed_predecessor_request_does_not_rebind_rows(case,field,value):
    with pytest.raises(ValueError):
        module.readback_declared_entry_source_equivalence(case.packet,**case.args,
            predecessor=replace(case.predecessor,**{field:value}))


def test_dated_scope_mismatch_and_changed_producer_coverage_fail_fresh(case):
    case.x.context['account_ids'] = ('foreign',)
    with pytest.raises(ValueError): read(case)


@pytest.mark.parametrize('value',['0.0000000000000000001','100000000000000000000.000000000000000000','NaN','1.0'])
def test_original_amount_cannot_be_rounded_overflowed_or_noncanonical(value):
    with pytest.raises(ValueError): module._scalar(value,'Decimal(38,18)')


@pytest.mark.parametrize('value',[7.,True])
def test_predecessor_nested_prefix_scalar_alias_fails(case,value):
    with pytest.raises(ValueError):
        readback = replace(case.predecessor,prefix=replace(case.predecessor.prefix,last_sequence=value))
        module.readback_declared_entry_source_equivalence(case.packet,**case.args,predecessor=readback)


def test_cold_attempt_uuid_spelling_cannot_be_coerced(case):
    with pytest.raises(ValueError):
        read(case,replace(case.packet,base=replace(case.packet.base,attempt_id='{'+ATTEMPT+'}')))
