"""Real prepared source consumers; external producer reads use native fixtures."""
from dataclasses import asdict, replace
from hashlib import sha256
from uuid import UUID

import numpy as np
import pyarrow as pa
import pytest

from src.backend.backtest_squeeze_ladder_loader import load_ladder_observations
from src.backend.backtest_squeeze_ladder_setup import bind_ladder_setups
from src.backend.backtest_squeeze_ladder_entry import propose_ladder_breakout
from src.backend.backtest_squeeze_ladder_evidence import project_ladder_evidence, verify_ladder_evidence_projection
from src.backend.backtest_squeeze_ladder_readback import reconstruct_ladder_market_decision, verify_ladder_market_evidence
from src.backend.backtest_squeeze_ladder_admission import admit_ladder_proposal
from src.trading_runtime.squeeze_ladder_geometry import LadderGeometryBindingPolicy, declared_geometry_binding_policy
from src.trading_runtime.squeeze_ladder_columnar import compile_ladder_gate
from src.trading_runtime.strategy_one_pivot_product import interval_content_hash
from src.trading_runtime.journal_contract import canonical_json
from tests.test_backtest_squeeze_ladder_loader import fixture
from tests.test_backtest_squeeze_ladder_setup import plans
from tests.test_backtest_squeeze_ladder_entry import prepared, decide
from tests.test_backtest_squeeze_ladder_admission import financial, DAY
from tests.test_backtest_strategy_one_loader import TICKER


WAIT = LadderGeometryBindingPolicy()


def source(*, confirmed=66000, changes=None, policy_changes=None):
    _, market, v7, pivots = plans()
    _, _, v7 = prepared()  # complete genuine structural target family
    _, scan, policy, table, Reader = fixture()
    policy = replace(policy, **(policy_changes or {}))
    modifications = {'close_int': {660:104200}, 'bid_int':{660:104200}, 'ask_int': {660:104300}}
    for name, rows in (changes or {}).items():
        modifications.setdefault(name, {}).update(rows)
    for name, rows in modifications.items():
        values = table[name].to_pylist()
        for index, value in rows.items():
            values[index] = value
        table = table.set_column(table.schema.get_field_index(name), name,
                                 pa.array(values, type=table[name].type))
    observed, = load_ladder_observations(market, session_date=market.sessions[0], tickers=(TICKER,),
        through_boundary_ms=70000, certified_scan=scan, policy=policy, client=Reader(table))
    original = pivots.intervals[0][1][0]
    epoch_origin = original.confirmed_at_us - original.valid_from_boundary_ms * 1000
    interval = replace(original, confirmed_at_us=epoch_origin + confirmed * 1000,
                       valid_from_boundary_ms=confirmed)
    pivots = replace(pivots, intervals=((TICKER, (interval,)),), coverage=(
        replace(pivots.coverage[0], content_hash=interval_content_hash((interval,))),))
    return observed, market, v7, pivots, policy


def bind(parts, waiting=True):
    observed, market, v7, pivots, policy = parts
    return bind_ladder_setups(observed, market=market, v7=v7, pivots=pivots,
        tick_int=100, stop_buffer_ticks=1,
        **({'geometry_policy':WAIT, 'gate_policy':policy} if waiting else {}))


def evidence(parts):
    observed, market, v7, pivots, policy = parts
    setup, = bind(parts)
    decision = propose_ladder_breakout(observed, setup, v7=v7, boundary_ms=66100,
        tick_int=100, break_buffer_ticks=1, target_count=3, allocation='equal')
    assert decision.reason == 'entry_proposed'
    admission = admit_ladder_proposal(decision, financial(), session_date=DAY, groups=())
    identity = dict(run_id='waiting-source', batch_id=str(UUID(int=1)), parent_record_id=str(UUID(int=2)))
    rows = project_ladder_evidence(admission, assignment_id='assignment-1', session_date=DAY, **identity)
    context = dict(observations=observed, market=market, v7=v7, pivots=pivots,
        tick_int=100, stop_buffer_ticks=1, break_buffer_ticks=1, target_count=3,
        allocation='equal', geometry_policy=WAIT, gate_policy=policy)
    return rows, context, decision, admission, identity


def test_original_real_cross_waits_for_first_complete_pair_freezes_once():
    parts = source()
    legacy, = bind(parts, waiting=False)
    assert legacy.qualification_boundary_ms == 65000
    assert legacy.reason == 'confirmed_swing_stop_unavailable'
    setup, = bind(parts)
    assert setup.reason == 'setup_qualified'
    assert setup.qualification_boundary_ms == 66000
    assert setup.resistance.upper == 10.4
    assert asdict(setup.geometry_binding) == dict(policy_version=WAIT.version,
        trigger_source_row_index=649, trigger_boundary_ms=65000,
        freeze_boundary_ms=66000, admission_expiry_boundary_ms=364000)
    assert setup.stop.stop_int == 97900
    with pytest.raises(ValueError, match='identity or policy'):
        decide(parts[0], setup, parts[2], boundary=66000)


@pytest.mark.parametrize('changes', [
    {'quote_valid':{659:0}}, {'volume_trade_count':{659:0}},
    {'close_int':{659:99900}}, {'price_valid':{659:0}},
])
def test_complete_geometry_waits_for_current_quote_liquidity_vwap_and_price(changes):
    parts = source(confirmed=66000, changes=changes, policy_changes={'minimum_trade_rate_10s':10.})
    # Confirmed at 66000, but ineligible then; no borrowed earlier operands.
    setup, = bind(parts)
    assert setup.qualification_boundary_ms > 66000 or setup.reason != 'setup_qualified'


@pytest.mark.parametrize('policy_changes', [
    {'admission_ttl_ms':2000}, {'acquisition_windows':((0,66000),)},
])
def test_original_strict_expiry_and_acquisition_cutoff_are_not_reset(policy_changes):
    setup, = bind(source(policy_changes=policy_changes))
    assert setup.reason != 'setup_qualified'
    assert setup.geometry_binding is None


def test_first_complete_pair_does_not_rebind_to_later_native_geometry():
    parts = source(confirmed=63000)
    setup, = bind(parts)
    assert setup.qualification_boundary_ms == 65000
    assert setup.resistance.upper == 10.2
    assert decide(parts[0], setup, parts[2], boundary=66100).reason == 'entry_proposed'
    assert setup.resistance.upper == 10.2


def test_wait_requires_certified_prefix_and_exact_declared_policy():
    parts = source()
    with pytest.raises(ValueError, match='certified source prefix'):
        bind((replace(parts[0], gate=replace(parts[0].gate, certified_history_through_ms=None)), *parts[1:]))
    assert declared_geometry_binding_policy({}) is None
    assert declared_geometry_binding_policy({'geometry_binding_policy':WAIT.payload()}) == WAIT
    for value in ({}, {'version':'future'}, {'version':WAIT.version,'reset_ttl':True}, None):
        with pytest.raises(ValueError):
            declared_geometry_binding_policy({'geometry_binding_policy':value})


def test_cold_prefix_recomputes_earliest_pair_and_requires_exact_witness():
    rows, context, decision, admission, identity = evidence(source())
    assert reconstruct_ladder_market_decision(rows, **context) == decision
    assert verify_ladder_market_evidence(rows, account_id='DU1', assignment_id='assignment-1',
        **identity, **context) == admission.intent
    for altered in (replace(rows, geometry_bindings=()),
                    replace(rows, geometry_bindings=rows.geometry_bindings * 2)):
        with pytest.raises(ValueError, match='earliest certified pair'):
            reconstruct_ladder_market_decision(altered, **context)
    for field, value in [('trigger_boundary_ms',65100), ('freeze_boundary_ms',65100),
                         ('admission_expiry_boundary_ms',365000), ('trigger_source_row_index',650)]:
        changed = dict(rows.geometry_bindings[0], **{field:value})
        changed['content_hash'] = sha256(canonical_json({k:v for k,v in changed.items() if k != 'content_hash'}).encode()).hexdigest()
        with pytest.raises(ValueError, match='earliest certified pair'):
            reconstruct_ladder_market_decision(replace(rows, geometry_bindings=(changed,)), **context)
    legacy = {**context, 'geometry_policy':None}
    with pytest.raises(ValueError):
        reconstruct_ladder_market_decision(rows, **legacy)


def test_cold_future_tail_geometry_cannot_enter_prefix():
    rows, context, decision, _, _ = evidence(source())
    # The active future interval is malformed; only later qualification could read it.
    v7 = context['v7']
    intervals = v7.intervals[0][1]
    changed = replace(intervals[1], valid_from_ms=67000, confirmed_at_ms=2**62)
    future = replace(v7, intervals=((TICKER, (intervals[0],replace(intervals[1],valid_to_ms=67000),changed,*intervals[2:])),))
    assert reconstruct_ladder_market_decision(rows, **{**context,'v7':future}) == decision


def test_earlier_malformed_geometry_raises_instead_of_waiting_past_it():
    parts = source()
    v7 = parts[2]
    intervals = v7.intervals[0][1]
    malformed = replace(intervals[0], confirmed_at_ms=2**62)
    v7 = replace(v7, intervals=((TICKER,(malformed,*intervals[1:])),))
    with pytest.raises(ValueError, match='malformed or future'):
        bind((parts[0],parts[1],v7,*parts[3:]))


def test_legacy_projection_has_no_binding_and_rejects_foreign_companion():
    observed, setup, v7 = prepared()
    admission = admit_ladder_proposal(decide(observed,setup,v7),financial(),session_date=DAY,groups=())
    rows = project_ladder_evidence(admission,run_id='legacy',batch_id=str(UUID(int=1)),
        parent_record_id=str(UUID(int=2)),assignment_id='assignment-1',session_date=DAY)
    assert rows.geometry_bindings == ()
    # Captured by running the dc059 HEAD projector on this exact fixed fixture.
    assert sha256(canonical_json({'setup':rows.setup,'targets':rows.targets}).encode()).hexdigest() == '9966bd36e80c2188977f8f500e428f7ea5a1e826503fa64bf4f48b664cf10fbe'
    assert 'geometry_binding' not in rows.setup
    assert 'trigger_boundary_ms' not in rows.setup
    foreign = evidence(source())[0].geometry_bindings
    with pytest.raises(ValueError, match='missing or foreign'):
        verify_ladder_evidence_projection(replace(rows,geometry_bindings=foreign),expected=rows)


def test_partial_geometry_is_not_frozen_or_carried_to_later_stop():
    parts = source()
    v7 = parts[2]
    original = v7.intervals[0][1]
    later = replace(original[1], level_id='later-R', ordinal=1, valid_from_ms=67000)
    support = replace(original[1], role='support', valid_to_ms=67000)
    changed = replace(v7, intervals=((TICKER,(original[0],support,later)),))
    setup, = bind((parts[0],parts[1],changed,*parts[3:]))
    assert setup.qualification_boundary_ms == 67000
    assert setup.resistance.level_id == 'later-R'
    assert setup.resistance.upper == 10.4
    assert setup.stop.qualification_boundary_ms == 67000


def test_new_admission_terminates_original_wait_without_expiry_reset():
    from tests.test_squeeze_ladder_columnar import columns
    parts = source()
    args = columns()
    args['admission_boundaries_ms'] = np.array([64000,65500],dtype=np.int64)
    args['certified_history_through_ms'] = 70000
    gate = compile_ladder_gate(**args)
    setup, = bind((replace(parts[0],gate=gate),*parts[1:]))
    assert setup.admission_boundary_ms == 64000
    assert setup.reason != 'setup_qualified'
    assert setup.geometry_binding is None
    # A separate first-above qualification may start the new admission, never renew old one.
    args['policy'] = replace(args['policy'],qualification_mode='first_eligible_above_vwap')
    gate = compile_ladder_gate(**args)
    results = bind((replace(parts[0],gate=gate),*parts[1:4],args['policy']))
    assert [row.admission_boundary_ms for row in results] == [64000,65500]
    assert results[0].reason != 'setup_qualified'
    assert results[1].geometry_binding.trigger_boundary_ms == 65500
    assert results[1].geometry_binding.admission_expiry_boundary_ms == 365500


def test_binding_native_schema_hash_and_foreign_parent_are_checked():
    from src.trading_runtime.arte_journal_writer import typed_row
    from src.trading_runtime.arte_squeeze_ladder_schema import BINDING
    rows, _, _, _, _ = evidence(source())
    native = typed_row(BINDING.name,{k:v for k,v in rows.geometry_bindings[0].items() if k != 'content_hash'})
    assert native == rows.geometry_bindings[0]
    for changes in ({'parent_record_id':str(UUID(int=9))}, {'run_id':'foreign'},
                    {'policy_version':'foreign'}, {'content_hash':'0'*64}):
        altered = replace(rows,geometry_bindings=(dict(rows.geometry_bindings[0],**changes),))
        with pytest.raises(ValueError,match='reconstructed'):
            verify_ladder_evidence_projection(altered,expected=rows)


def waiting_native_unit():
    from src.trading_runtime.squeeze_ladder_automatic import AutomaticLadderRequest
    from src.trading_runtime.automatic_ladder_transport import V4AutomaticLadderBatch
    from src.trading_runtime.arte_intent_projection import strategy_intent_batch
    from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
    from tests.test_squeeze_ladder_automatic import source as native_source
    context, policy, assignment, _ = native_source()
    parts = source()
    _, _, decision, _, _ = evidence(parts)
    payload = {**context.configuration.payload}
    strategy = {**payload['strategy']}
    release = {**strategy['numbered_release']}
    release['automatic_market_policy'] = {**release['automatic_market_policy'],
                                        'geometry_binding_policy':WAIT.payload()}
    strategy['numbered_release'] = release
    payload['strategy'] = strategy
    config = replace(context.configuration,payload=payload,
                     payload_hash=sha256(canonical_json(payload).encode()).hexdigest())
    context = replace(context,configuration=config,observations=parts[0],market=parts[1],
                      v7=parts[2],pivots=parts[3],geometry_policy=WAIT)
    account = replace(financial(),assignment_id=assignment.assignment_id,permissions=policy.permissions)
    admission = admit_ladder_proposal(decision,account,session_date=context.session_date,groups=())
    request = AutomaticLadderRequest(admission,context,assignment.assignment_id,policy)
    previous = str(UUID(int=8))
    batch = strategy_intent_batch(request.intent,run_id=context.run_id,
        run_month=context.session_date.replace(day=1),account_id='DU1',attempt_id=str(UUID(int=9)),
        batch_id=str(UUID(int=10)),prior_batch_id=previous,sequence=2,
        source_cursor='boundary',run_status='running',recorded_at=request.intent.event_time)
    unit = V4AutomaticLadderBatch.from_request(batch,request)
    prefix = V4CommittedPrefix(context.run_id,1,previous,'boundary','running',(previous,))
    return unit,prefix,account


def test_actual_native_request_envelope_and_journal_admission_keep_companion():
    from src.trading_runtime.arte_squeeze_ladder_schema import BINDING
    unit,prefix,account = waiting_native_unit()
    families = unit.prepare_families(verified_prior_prefix=prefix,native_financial=account)
    assert [len(rows) for _,rows in families] == [1,3,1]
    assert families[-1][0] == BINDING.name
    assert unit.evidence.geometry_bindings[0]['freeze_boundary_ms'] == 66000
    for bindings in ((),unit.evidence.geometry_bindings * 2,
                     (dict(unit.evidence.geometry_bindings[0],trigger_boundary_ms=65100),)):
        with pytest.raises(ValueError):
            replace(unit,evidence=replace(unit.evidence,geometry_bindings=bindings))


def test_actual_cold_native_consumer_rejects_missing_duplicate_or_tampered_binding_before_financial_reads():
    from src.trading_runtime.automatic_ladder_transport import verify_cold_automatic_ladder_families
    from src.trading_runtime.arte_squeeze_ladder_schema import SETUP,TARGET,BINDING
    unit,prefix,_ = waiting_native_unit()
    base = dict(unit.base.families())
    base.update({SETUP.name:(unit.evidence.setup,),TARGET.name:unit.evidence.targets})
    metadata = dict(first_sequence=2,last_sequence=2,status='running',run_month=unit.base.run_month.isoformat(),
                    attempt_id=unit.base.attempt_id,source_cursor=unit.base.source_cursor)
    for bindings in ((),unit.evidence.geometry_bindings * 2,
                     (dict(unit.evidence.geometry_bindings[0],admission_expiry_boundary_ms=365000),)):
        with pytest.raises(ValueError,match='earliest certified pair'):
            verify_cold_automatic_ladder_families(object(),related_rows={**base,BINDING.name:bindings},
                run_id=unit.base.run_id,batch_id=unit.base.batch_id,prior_batch_id=unit.base.prior_batch_id,
                verified_prior_prefix=prefix,sources=(unit.request.market_context,),batch_metadata=metadata)


def test_new_binding_family_cannot_bypass_native_publication_fence():
    from types import SimpleNamespace
    from src.trading_runtime.arte_journal_commit_v4 import _publish_sealed_batch_v4
    from src.trading_runtime.arte_squeeze_ladder_schema import BINDING
    with pytest.raises(ValueError,match='cannot bypass'):
        _publish_sealed_batch_v4(SimpleNamespace(typed_insert_dispatch=object()),
            object(),(),((BINDING.name,({},)),))


@pytest.mark.parametrize('field,value', [('trigger_source_row_index',2**32),
    ('trigger_source_row_index',True),('trigger_source_row_index',-1),
    ('admission_expiry_boundary_ms',2**32 + 4),('freeze_boundary_ms',True),
    ('trigger_boundary_ms',2**32 + 4)])
def test_witness_rejects_non_scalar_or_uint32_overflow_before_projection(field,value):
    from src.trading_runtime.squeeze_ladder_geometry import LadderGeometryBindingWitness
    content = dict(policy_version=WAIT.version,trigger_source_row_index=649,
        trigger_boundary_ms=65000,freeze_boundary_ms=66000,admission_expiry_boundary_ms=364000)
    with pytest.raises(ValueError,match='binding witness'):
        LadderGeometryBindingWitness(**{**content,field:value})


def test_declared_source_end_and_native_context_bind_optional_policy_exactly():
    from src.backend.backtest_ladder_source_authority import declared_source_end
    from tests.test_ladder_cold_source_authority import declared
    unit,_,_ = waiting_native_unit()
    context = unit.request.market_context
    config = declared(context)
    market_policy = config.payload['strategy']['numbered_release']['automatic_market_policy']
    declared_policy = {**market_policy,'geometry_binding_policy':WAIT.payload()}
    assert declared_source_end(declared_policy,{'start_local_ms':14400000,'end_local_ms':34200000}) == 19800000
    with pytest.raises(ValueError,match='geometry binding policy'):
        declared_source_end({**declared_policy,'geometry_binding_policy':{'version':'future'}},
                           {'start_local_ms':14400000,'end_local_ms':34200000})
    context.verify_policy(unit.request.policy)
    with pytest.raises(ValueError,match='sealed declared automatic policy'):
        replace(context,geometry_policy=None).verify_policy(unit.request.policy)


def test_saved_binding_hash_and_parent_tampering_fail_market_cold_before_financial_authority():
    rows,context,_,_,_ = evidence(source())
    for changed in (dict(rows.geometry_bindings[0],content_hash='0'*64),
                    dict(rows.geometry_bindings[0],run_id='foreign'),
                    {**rows.geometry_bindings[0],'extra':1}):
        with pytest.raises(ValueError,match='earliest certified pair/source parent'):
            reconstruct_ladder_market_decision(replace(rows,geometry_bindings=(changed,)),**context)
