"""Standalone codec/replay controls; no Keeper/native source attestation seam."""
from dataclasses import replace
import json
import math

import pytest

from src.trading_runtime import profit_armed_structural_rejection_checkpoint as cp
from src.trading_runtime.profit_armed_structural_rejection import (
    StructuralRejectionState, StructuralRejectionInput, StructuralRejectionPolicy,
    HeldBar, FrozenResistance, CurrentQuote, RejectionSource, reduce_structural_rejection,
)

POLICY = StructuralRejectionPolicy()
SOURCE = RejectionSource('00000000-0000-0000-0000-000000000001', 'TEST', '2026-08-04',
    'producer-build-string', 'a'*64, '00000000-0000-0000-0000-000000000002',
    '00000000-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000004', 'b'*64)
ORIGIN = 1000000000000


def initial(policy=POLICY):
    return StructuralRejectionState(SOURCE,'00000000-0000-0000-0000-000000000005',
        'ACCOUNT',ORIGIN,0,10000,9000,policy=policy)


def inputs():
    bars = tuple(HeldBar(SOURCE,t,11200 if t==10000 else 11000,11300,
        20 if t<=10000 else 10,macd_line=-.1,macd_signal=.1)
        for t in (5000,10000,15000,20000))
    level = FrozenResistance(SOURCE,'level-identity','c'*64,11050,11150,11100,9000,9000)
    quote = CurrentQuote(SOURCE,ORIGIN+20000*1000,11000,11001)
    return tuple(StructuralRejectionInput(SOURCE,b.boundary_ms,b,
        level if b.boundary_ms>=10000 else None,
        bars if b.boundary_ms==20000 else (),quote if b.boundary_ms==20000 else None)
        for b in bars)


def binding(state, seq=1):
    return cp.StructuralRejectionCheckpointBinding(**{name:getattr(state,name)
        for name in ('source','policy','position_intent_id','account_id','session_origin_us',
                     'first_held_boundary_ms','original_ask_int','original_stop_int')},
        checkpoint_sequence=seq,boundary_ms=state.last_decision_boundary_ms)


def ready(count=3):
    state=initial()
    witness=None
    for value in inputs()[:count]:
        state,witness=reduce_structural_rejection(state,value,policy=POLICY)
    return state,witness


@pytest.mark.parametrize('count',[0,1,2,3,4])
def test_every_causal_stage_roundtrip_exact_and_deterministic(count):
    state,witness=ready(count)
    checkpoint=cp.checkpoint_structural_rejection(state,checkpoint_sequence=10,
                                                firing_witness=witness)
    pins=binding(state,10)
    data=cp.encode_structural_rejection_checkpoint(checkpoint,binding=pins)
    restored=cp.decode_structural_rejection_checkpoint(data,binding=pins)
    assert restored==checkpoint
    assert cp.restore_structural_rejection_checkpoint(restored,binding=pins)==state
    assert cp.encode_structural_rejection_checkpoint(restored,binding=pins)==data
    assert checkpoint.state_hash==cp._hash(state)
    assert data.decode('utf-8').count('cross_prior')>=1


def test_cold_continuation_matches_uninterrupted_actual_reducer_and_witness():
    state,_=ready(3)
    checkpoint=cp.checkpoint_structural_rejection(state,checkpoint_sequence=10)
    data=cp.encode_structural_rejection_checkpoint(checkpoint,binding=binding(state,10))
    cold=cp.decode_structural_rejection_checkpoint(data,binding=binding(state,10))
    replay=cp.replay_structural_rejection_checkpoint(cold,(inputs()[-1],),
        binding=binding(state,10),next_checkpoint_sequence=11)
    expected,witness=reduce_structural_rejection(state,inputs()[-1],policy=POLICY)
    assert replay.checkpoint.state==expected and replay.emitted_witnesses==(witness,)
    assert replay.checkpoint.firing_witness==witness and replay.input_count==1
    assert replay.checkpoint.state.fired


def test_fired_checkpoint_preserves_witness_on_later_missing_decision_no_duplicate():
    state,witness=ready(4)
    checkpoint=cp.checkpoint_structural_rejection(state,checkpoint_sequence=10,firing_witness=witness)
    result=cp.replay_structural_rejection_checkpoint(checkpoint,
        (StructuralRejectionInput(SOURCE,25000),),binding=binding(state,10),next_checkpoint_sequence=11)
    assert result.emitted_witnesses==()
    assert result.checkpoint.firing_witness==witness
    assert result.checkpoint.state.last_decision_boundary_ms==25000
    assert cp.decode_structural_rejection_checkpoint(cp.encode_structural_rejection_checkpoint(
        result.checkpoint,binding=binding(result.checkpoint.state,11)),
        binding=binding(result.checkpoint.state,11))==result.checkpoint


def test_midchain_cold_then_delayed_fresh_quote_confirms_same_completed_bar_once():
    policy=replace(POLICY,decision_resolution_ms=100)
    state=initial(policy)
    for value in inputs():
        state,witness=reduce_structural_rejection(state,replace(value,quote=None),policy=policy)
        assert witness is None
    assert len(state.rejections)==2 and not state.fired
    checkpoint=cp.checkpoint_structural_rejection(state,checkpoint_sequence=10)
    data=cp.encode_structural_rejection_checkpoint(checkpoint,binding=binding(state,10))
    cold=cp.decode_structural_rejection_checkpoint(data,binding=binding(state,10))
    value=replace(inputs()[-1],boundary_ms=20100,
        quote=CurrentQuote(SOURCE,ORIGIN+20100*1000,11000,11001))
    result=cp.replay_structural_rejection_checkpoint(cold,(value,),
        binding=binding(state,10),next_checkpoint_sequence=11)
    assert result.checkpoint.state.fired and len(result.emitted_witnesses)==1
    assert result.checkpoint.state.rejections==state.rejections
    assert result.emitted_witnesses[0].decision_boundary_ms==20100
    pins=binding(result.checkpoint.state,11)
    assert cp.decode_structural_rejection_checkpoint(cp.encode_structural_rejection_checkpoint(
        result.checkpoint,binding=pins),binding=pins).state==result.checkpoint.state


@pytest.mark.parametrize('invalid',[False,True])
def test_sparse_invalid_continuation_preserves_arm_cross_without_claiming_contiguous_rejections(invalid):
    state,_=ready(3)
    bar=replace(inputs()[-1].completed_bar,price_valid=False) if invalid else None
    state,_=reduce_structural_rejection(state,StructuralRejectionInput(SOURCE,20000,bar),policy=POLICY)
    checkpoint=cp.checkpoint_structural_rejection(state,checkpoint_sequence=10)
    data=cp.encode_structural_rejection_checkpoint(checkpoint,binding=binding(state,10))
    cold=cp.decode_structural_rejection_checkpoint(data,binding=binding(state,10))
    new=replace(inputs()[-1].completed_bar,boundary_ms=25000)
    result=cp.replay_structural_rejection_checkpoint(cold,(StructuralRejectionInput(SOURCE,25000,new),),
        binding=binding(state,10),next_checkpoint_sequence=11)
    assert not result.emitted_witnesses and not result.checkpoint.state.fired
    assert result.checkpoint.state.arm==state.arm and result.checkpoint.state.cross==state.cross
    assert result.checkpoint.state.rejections==(new,)


@pytest.mark.parametrize('value',[None,math.nan,math.inf,-math.inf,-0.0,0.0,1.25,2])
def test_momentum_scalar_semantics_preserved_without_json_nan(value):
    bar=HeldBar(SOURCE,5000,11000,11300,12,macd_line=value,macd_signal=value)
    state,_=reduce_structural_rejection(initial(),StructuralRejectionInput(SOURCE,5000,bar),policy=POLICY)
    checkpoint=cp.checkpoint_structural_rejection(state,checkpoint_sequence=1)
    data=cp.encode_structural_rejection_checkpoint(checkpoint,binding=binding(state))
    assert b'NaN' not in data and b'Infinity' not in data
    restored=cp.decode_structural_rejection_checkpoint(data,binding=binding(state))
    observed=restored.state.last_bar.macd_line
    assert type(observed) is type(value)
    if type(value) is float:
        assert observed.hex()==value.hex()
    else:
        assert observed==value
    assert cp.encode_structural_rejection_checkpoint(restored,binding=binding(state))==data


def test_missing_extremes_rejection_state_and_parameterized_policy_roundtrip():
    policy=replace(POLICY,decision_resolution_ms=100,bar_resolution_ms=10000,
                   activity_count=6,rejection_count=3,arm_original_risk=(3,2))
    bar=HeldBar(SOURCE,10000,10000,0,10,True,None,None,10000,False)
    state,_=reduce_structural_rejection(initial(policy),StructuralRejectionInput(SOURCE,10100,bar),policy=policy)
    checkpoint=cp.checkpoint_structural_rejection(state,checkpoint_sequence=1)
    decoded=cp.decode_structural_rejection_checkpoint(cp.encode_structural_rejection_checkpoint(
        checkpoint,binding=binding(state)),binding=binding(state))
    assert decoded.state.policy==policy and not decoded.state.last_bar.extremes_valid
    assert decoded.state.last_bar.price_valid and decoded.state.arm is None


def test_cold_nan_arming_observation_repeats_sameclock_under_unchanged_reducer_guard():
    policy=replace(POLICY,decision_resolution_ms=100)
    bar=HeldBar(SOURCE,5000,11000,11300,12,macd_line=math.nan,macd_signal=.1)
    state,_=reduce_structural_rejection(initial(policy),StructuralRejectionInput(SOURCE,5000,bar),policy=policy)
    checkpoint=cp.checkpoint_structural_rejection(state,checkpoint_sequence=1)
    cold=cp.decode_structural_rejection_checkpoint(cp.encode_structural_rejection_checkpoint(
        checkpoint,binding=binding(state)),binding=binding(state))
    assert cold.state.arm is cold.state.last_bar
    # Reuse the decoded immutable completed observation at the faster decision
    # clock. This is not a fresh external source lookup/certification claim.
    result=cp.replay_structural_rejection_checkpoint(cold,
        (StructuralRejectionInput(SOURCE,5100,cold.state.last_bar),),
        binding=binding(state),next_checkpoint_sequence=2)
    assert result.emitted_witnesses==() and result.checkpoint.state.arm==cold.state.arm
    assert math.isnan(result.checkpoint.state.last_bar.macd_line)
    for changed in (replace(cold.state.last_bar,close_int=10999),
                    replace(cold.state.last_bar,extremes_valid=False),
                    replace(cold.state.last_bar,source=replace(SOURCE,ticker='FOREIGN'))):
        with pytest.raises(ValueError): cp.replay_structural_rejection_checkpoint(cold,
            (StructuralRejectionInput(SOURCE,5100,changed),),
            binding=binding(state),next_checkpoint_sequence=2)


@pytest.mark.parametrize('change',[dict(close_int=10999),dict(extremes_valid=False),
    dict(trade_count=99),dict(source=replace(SOURCE,ticker='FOREIGN'))])
def test_decode_interns_only_identical_complete_observations(change):
    bar=HeldBar(SOURCE,5000,11000,11300,12,macd_line=math.nan,macd_signal=.1)
    pool={};first=cp._untree(cp._tree(bar),pool)
    assert cp._untree(cp._tree(bar),pool) is first
    changed=cp._untree(cp._tree(replace(bar,**change)),pool)
    assert changed is not first


@pytest.mark.parametrize('name,value',[
    ('account_id','FOREIGN'),('position_intent_id','00000000-0000-0000-0000-000000000009'),
    ('session_origin_us',ORIGIN+1),('first_held_boundary_ms',1),
    ('original_ask_int',10001),('original_stop_int',8999),
    ('checkpoint_sequence',2),('boundary_ms',20000)])
def test_independent_entry_cursor_pins_reject_resealed_or_foreign(name,value):
    state,_=ready(3);checkpoint=cp.checkpoint_structural_rejection(state,checkpoint_sequence=1)
    with pytest.raises(ValueError):
        cp.restore_structural_rejection_checkpoint(checkpoint,binding=replace(binding(state),**{name:value}))


@pytest.mark.parametrize('name,value',[('market_build_id','foreign'),('market_plan_token','d'*64),
    ('interval_plan_token','d'*64),('bars_attempt_id','00000000-0000-0000-0000-000000000009'),
    ('indicators_attempt_id','00000000-0000-0000-0000-000000000009'),
    ('liquidity_attempt_id','00000000-0000-0000-0000-000000000009'),
    ('run_id','00000000-0000-0000-0000-000000000009'),('ticker','FOREIGN'),('session_date','2026-08-05')])
def test_all_source_pins_external_not_self_hash(name,value):
    state,_=ready(3);checkpoint=cp.checkpoint_structural_rejection(state,checkpoint_sequence=1)
    with pytest.raises(ValueError,match='binding'):
        cp.restore_structural_rejection_checkpoint(checkpoint,binding=replace(binding(state),
            source=replace(SOURCE,**{name:value})))


def test_policy_drift_rejected_despite_valid_alternative():
    state,_=ready(3);checkpoint=cp.checkpoint_structural_rejection(state,checkpoint_sequence=1)
    with pytest.raises(ValueError,match='binding'):
        cp.restore_structural_rejection_checkpoint(checkpoint,binding=replace(binding(state),
            policy=replace(POLICY,recent_activity_multiplier=3)))


@pytest.mark.parametrize('field,value',[('version','future@2'),('state_hash','a'*64),
    ('witness_hash','b'*64),('content_hash','c'*64),('checkpoint_sequence',True)])
def test_root_tamper(field,value):
    state,_=ready(3);checkpoint=cp.checkpoint_structural_rejection(state,checkpoint_sequence=1)
    with pytest.raises(ValueError): cp.restore_structural_rejection_checkpoint(
        replace(checkpoint,**{field:value}),binding=binding(state))


@pytest.mark.parametrize('change',[dict(last_bar=None),dict(last_decision_boundary_ms=5000),
    dict(rejections=()),dict(resistance=None),dict(cross_prior=None)])
def test_resealed_invalid_causal_state_still_rejects(change):
    state,witness=ready(4)
    with pytest.raises(ValueError):
        modified=replace(state,**change)
        cp.checkpoint_structural_rejection(modified,checkpoint_sequence=1,firing_witness=witness)


@pytest.mark.parametrize('change',[dict(quote=None),dict(activity=()),
    dict(decision_boundary_ms=25000),dict(rejections=()),dict(predecessor=initial())])
def test_fired_witness_must_reproduce_full_actual_transition(change):
    state,witness=ready(4)
    with pytest.raises(ValueError): cp.checkpoint_structural_rejection(state,
        checkpoint_sequence=1,firing_witness=replace(witness,**change))


def test_unwitnessed_fired_and_unfired_witness_reject():
    state,witness=ready(4)
    with pytest.raises(ValueError): cp.checkpoint_structural_rejection(state,checkpoint_sequence=1)
    predecessor,_=ready(3)
    with pytest.raises(ValueError): cp.checkpoint_structural_rejection(predecessor,
        checkpoint_sequence=1,firing_witness=witness)


@pytest.mark.parametrize('mutation',['unknown','missing','class','duplicate','float','noncanonical','nan'])
def test_byte_parser_closed_shapes_and_canonical_representation(mutation):
    state,_=ready(3);checkpoint=cp.checkpoint_structural_rejection(state,checkpoint_sequence=1)
    data=cp.encode_structural_rejection_checkpoint(checkpoint,binding=binding(state));tree=json.loads(data)
    if mutation=='unknown': tree['extra']=1
    elif mutation=='missing': del tree['state']['fields']['cross_prior']
    elif mutation=='class': tree['state']['type']='arbitrary.Class'
    elif mutation=='float': tree['state']['fields']['last_bar']['fields']['macd_line']={'float64':'0.1'}
    elif mutation=='duplicate': data=b'{"version":"x",'+data[1:]
    elif mutation=='noncanonical': data=b' '+data
    elif mutation=='nan': data=b'{"x":NaN}'
    if mutation in ('unknown','missing','class','float'):
        data=json.dumps(tree,sort_keys=True,separators=(',',':')).encode()
    with pytest.raises(ValueError): cp.decode_structural_rejection_checkpoint(data,binding=binding(state))


@pytest.mark.parametrize('data',[b'',bytearray(b'{}'),b'\xff',b'[]',b'x'*1_048_577],
                         ids=['empty','bytearray','invalid-utf8','array','oversized'])
def test_bounded_exact_bytes(data):
    with pytest.raises(ValueError): cp.decode_structural_rejection_checkpoint(data,binding=binding(initial()))


@pytest.mark.parametrize('values,next_seq',[((),2),(list(inputs()),2),((inputs()[0],),1),
    ((inputs()[2],inputs()[1]),2),((replace(inputs()[0],source=replace(SOURCE,ticker='FOREIGN')),),2)])
def test_replay_rejects_unordered_foreign_or_nonforward_inputs(values,next_seq):
    checkpoint=cp.checkpoint_structural_rejection(initial(),checkpoint_sequence=1)
    with pytest.raises(ValueError): cp.replay_structural_rejection_checkpoint(checkpoint,
        values,binding=binding(initial()),next_checkpoint_sequence=next_seq)


def test_priority_and_missing_data_replay_retains_predecessor_without_firing():
    state,_=ready(3);checkpoint=cp.checkpoint_structural_rejection(state,checkpoint_sequence=1)
    result=cp.replay_structural_rejection_checkpoint(checkpoint,
        (replace(inputs()[-1],higher_priority_exit=True),StructuralRejectionInput(SOURCE,25000)),
        binding=binding(state),next_checkpoint_sequence=2)
    assert result.emitted_witnesses==() and not result.checkpoint.state.fired
    assert result.checkpoint.state.arm==state.arm and result.checkpoint.state.cross==state.cross


def test_no_live_or_native_capability_claim_and_subclasses_reject():
    class Derived(cp.StructuralRejectionCheckpointBinding): pass
    state,_=ready(3);checkpoint=cp.checkpoint_structural_rejection(state,checkpoint_sequence=1)
    with pytest.raises(ValueError): cp.restore_structural_rejection_checkpoint(checkpoint,
        binding=Derived(**{f.name:getattr(binding(state),f.name) for f in cp.fields(binding(state))}))
    assert not hasattr(checkpoint,'require_installed_admission')
