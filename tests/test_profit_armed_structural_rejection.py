"""Standalone causal reducer controls; no source certification or P&L claim."""
from dataclasses import replace
from math import nan, inf
import pytest
from src.trading_runtime.profit_armed_structural_rejection import (
    StructuralRejectionPolicy as Policy, RejectionSource as Source,
    HeldBar as Bar, FrozenResistance as Level, CurrentQuote as Quote,
    StructuralRejectionState as State, StructuralRejectionInput as Input,
    reduce_structural_rejection as reduce,
)

SOURCE = Source('00000000-0000-0000-0000-000000000001','TEST','2026-08-04',
    'producer-owned-build-identity','a'*64,'00000000-0000-0000-0000-000000000002',
    '00000000-0000-0000-0000-000000000003','00000000-0000-0000-0000-000000000004','b'*64)
ORIGIN = 1000000000000
POLICY = Policy()


def state(policy=POLICY, first_held=0):
    return State(SOURCE,'00000000-0000-0000-0000-000000000005','ACCOUNT',
                 ORIGIN,first_held,10000,9000,policy=policy)


def bar(t, close=11000, high=11200, trades=10, **kwargs):
    return Bar(SOURCE,t,close,high,trades,macd_line=-0.1,macd_signal=0.1,**kwargs)


def level(available=9000):
    return Level(SOURCE,'level-identity','c'*64,11050,11150,11100,available,available)


def quote(t=20000, age=0, bid=11000):
    return Quote(SOURCE,ORIGIN+t*1000-age,bid,max(bid,11001))


def ready(policy=POLICY):
    s=state(policy)
    bars=(bar(5000,trades=20),bar(10000,close=11200,trades=20),bar(15000),bar(20000))
    for b in bars[:3]:
        s,w=reduce(s,Input(SOURCE,b.boundary_ms,b,level() if b.boundary_ms>=9000 else None),policy=policy)
        assert w is None
    return s,bars


def firing(**kwargs):
    s,bars=ready()
    return reduce(s,replace(Input(SOURCE,20000,bars[-1],activity=bars,quote=quote()),**kwargs),policy=POLICY)


def test_exact_v2_defaults_and_full_causal_witness_replay():
    s,bars=ready(); x=Input(SOURCE,20000,bars[-1],activity=bars,quote=quote())
    result=reduce(s,x,policy=POLICY)
    assert result==reduce(s,x,policy=POLICY)
    next_s,w=result
    assert w.predecessor==s and next_s.fired
    assert w.arm.boundary_ms < w.cross.boundary_ms < w.rejections[0].boundary_ms
    assert w.resistance.available_boundary_ms < w.cross.boundary_ms
    assert w.rejections==bars[2:] and w.activity==bars
    assert POLICY.payload()['decision_resolution_ms']==5000
    assert POLICY.arm_original_risk==(1,1)
    assert sum(b.trade_count for b in w.activity[:2])==2*sum(b.trade_count for b in w.activity[2:])
    _,again=reduce(next_s,Input(SOURCE,25000,bar(25000)),policy=POLICY)
    assert again is None


@pytest.mark.parametrize('guard',['higher_priority_exit','pending_exit'])
def test_inherited_or_pending_exit_takes_priority(guard):
    s,w=firing(**{guard:True});assert w is None and not s.fired
    assert len(s.rejections)==2


def test_unheld_position_has_no_new_exit():
    _,w=firing(position_held=False);assert w is None


@pytest.mark.parametrize('change',[dict(quote=None),dict(activity=()),
    dict(quote=quote(age=1000001)),dict(quote=quote(bid=11100)),
    dict(quote=replace(quote(),ask_int=10999))])
def test_missing_stale_or_nonconfirming_quote_activity_no_exit(change):
    s,w=firing(**change);assert w is None
    assert s.arm is not None and s.cross is not None


@pytest.mark.parametrize('momentum',[(None,0.1),(nan,0.1),(inf,0.1),(0.1,0.1),(0.2,0.1)])
def test_missing_nonfinite_or_nonbearish_momentum_no_exit(momentum):
    s,bars=ready();new=replace(bars[-1],macd_line=momentum[0],macd_signal=momentum[1])
    _,w=reduce(s,Input(SOURCE,20000,new,activity=(*bars[:3],new),quote=quote()),policy=POLICY)
    assert w is None


def test_quote_age_equality_is_allowed():
    _,w=firing(quote=quote(age=1000000));assert w is not None


def test_current_crossing_bar_cannot_arm_and_cross():
    s,w=reduce(state(),Input(SOURCE,5000,bar(5000,close=11200),level(0)),policy=POLICY)
    assert w is None and s.arm is not None and s.cross is None


def test_unavailable_high_cannot_arm_even_when_stored_value_reaches_profit():
    s,w=reduce(state(),Input(SOURCE,5000,bar(5000,high=20000,extremes_valid=False)),policy=POLICY)
    assert w is None and s.arm is None and s.last_bar.price_valid


def test_valid_rejection_close_remains_usable_when_high_is_unavailable():
    s,bars=ready()
    last=replace(bars[-1],high_int=0,extremes_valid=False)
    result,w=reduce(s,Input(SOURCE,20000,last,activity=(*bars[:3],last),quote=quote()),policy=POLICY)
    assert result.fired and w.rejections[-1]==last


def test_restored_profit_arm_requires_valid_high():
    s,_=reduce(state(),Input(SOURCE,5000,bar(5000)),policy=POLICY)
    invalid=replace(s.arm,extremes_valid=False)
    with pytest.raises(ValueError,match='Persisted arm'):
        replace(s,arm=invalid,last_bar=invalid)


def test_extreme_validity_requires_exact_boolean():
    with pytest.raises(ValueError,match='Malformed completed'):
        bar(5000,extremes_valid=1)


def test_arm_and_geometry_must_be_strictly_prior_to_cross():
    s,_=reduce(state(),Input(SOURCE,5000,bar(5000)),policy=POLICY)
    s,_=reduce(s,Input(SOURCE,10000,bar(10000,close=11200),level(10000)),policy=POLICY)
    assert s.cross is None


def test_missing_level_then_rejecting_prices_do_not_invent_cross():
    s=state()
    for t,c in [(5000,11200),(10000,11200),(15000,11000),(20000,10900)]:
        s,w=reduce(s,Input(SOURCE,t,bar(t,close=c),quote=quote(t)),policy=POLICY)
        assert w is None and s.cross is None


@pytest.mark.parametrize('held',[1,2500,4999,5000])
def test_overlap_first_held_bar_cannot_arm(held):
    s,_=reduce(state(first_held=held),Input(SOURCE,5000,bar(5000)),policy=POLICY)
    assert s.arm is None
    s,_=reduce(s,Input(SOURCE,10000,bar(10000)),policy=POLICY)
    assert s.arm is not None


def test_missing_decision_retains_cross_but_gap_prevents_rejection_pair():
    s,bars=ready()
    missing,w=reduce(s,Input(SOURCE,20000),policy=POLICY)
    assert w is None and missing.arm==s.arm and missing.cross==s.cross
    b=bar(25000)
    s,w=reduce(missing,Input(SOURCE,25000,b,activity=(*bars[:3],b),quote=quote(25000)),policy=POLICY)
    assert w is None and s.rejections==(b,)


def test_invalid_intermediate_bar_breaks_rejection_preserves_earned_arm_cross():
    s,_=ready();bad=bar(20000,close=0,high=0,price_valid=False)
    s,w=reduce(s,Input(SOURCE,20000,bad),policy=POLICY)
    assert w is None and not s.rejections and s.arm is not None and s.cross is not None
    s,w=reduce(s,Input(SOURCE,25000,bar(25000)),policy=POLICY)
    assert w is None and len(s.rejections)==1


def test_noncontiguous_activity_and_zero_prior_do_not_impute_liquidity():
    s,bars=ready()
    gap=(bars[0],bars[2],bars[3])
    _,w=reduce(s,Input(SOURCE,20000,bars[-1],activity=gap,quote=quote()),policy=POLICY)
    assert w is None
    s,bars=ready(); altered=tuple(replace(b,trade_count=0) for b in bars)
    # Build a consistent alternate original path, not rewrite prior source bars.
    s=state()
    for b in altered[:3]:s,_=reduce(s,Input(SOURCE,b.boundary_ms,b,level() if b.boundary_ms>=9000 else None),policy=POLICY)
    _,w=reduce(s,Input(SOURCE,20000,altered[-1],activity=altered,quote=quote()),policy=POLICY)
    assert w is None


@pytest.mark.parametrize('kind',['bar','quote','level','input'])
def test_foreign_source_is_fatal(kind):
    s,bars=ready();foreign=replace(SOURCE,market_plan_token='d'*64)
    x=Input(SOURCE,20000,bars[-1],activity=bars,quote=quote())
    if kind=='bar':x=replace(x,completed_bar=replace(bars[-1],source=foreign))
    if kind=='quote':x=replace(x,quote=replace(quote(),source=foreign))
    if kind=='level':x=replace(x,resistance=replace(level(),source=foreign))
    if kind=='input':x=replace(x,source=foreign)
    with pytest.raises(ValueError):reduce(s,x,policy=POLICY)


@pytest.mark.parametrize('kind',['bar','quote','level','decision'])
def test_future_or_unordered_clock_fails_closed(kind):
    s,bars=ready();x=Input(SOURCE,20000,bars[-1],activity=bars,quote=quote())
    if kind=='bar':x=replace(x,completed_bar=bar(25000))
    if kind=='quote':x=replace(x,quote=quote(age=-1))
    if kind=='level':x=replace(x,resistance=level(21000))
    if kind=='decision':x=replace(x,boundary_ms=15000)
    with pytest.raises(ValueError):reduce(s,x,policy=POLICY)


def test_geometry_rebinding_duplicate_and_conflicting_source_fail():
    s,bars=ready()
    for x in (Input(SOURCE,20000,bars[-1],replace(level(),level_id='another')),
              Input(SOURCE,20000,bars[-1],activity=(bars[0],bars[0])),
              Input(SOURCE,20000,bars[-1],activity=(replace(bars[1],trade_count=21),*bars[2:]))):
        with pytest.raises(ValueError):reduce(s,x,policy=POLICY)


@pytest.mark.parametrize('kwargs',[{'decision_resolution_ms':True},{'activity_count':3},
    {'rejection_count':0},{'recent_activity_multiplier':0},{'maximum_quote_age_us':0},
    {'arm_original_risk':(0,1)},{'arm_original_risk':(True,1)}, {'bar_resolution_ms':4999},
    {'decision_resolution_ms':3000}])
def test_policy_malformed_or_unsupported_parameters_fail(kwargs):
    with pytest.raises(ValueError):Policy(**kwargs)


def test_generic_declared_thresholds_counts_resolution_actually_change_reduction():
    p=Policy(decision_resolution_ms=1000,bar_resolution_ms=1000,
             arm_original_risk=(2,1),rejection_count=3,activity_count=6,
             recent_activity_multiplier=3,maximum_quote_age_us=500000)
    s=state(p); rows=[]
    # A 1R high cannot arm the declared 2R variant.
    b=bar(1000,high=11500,resolution_ms=1000)
    s,_=reduce(s,Input(SOURCE,1000,b),policy=p);assert s.arm is None
    for t,c,hi,tr in [(2000,12000,12000,30),(3000,12200,12200,30),
                      (4000,11000,11200,10),(5000,11000,11200,10),(6000,10900,11200,10)]:
        b=bar(t,close=c,high=hi,trades=tr,resolution_ms=1000);rows.append(b)
        activity=(bar(1000,high=11500,trades=30,resolution_ms=1000),*rows) if t==6000 else ()
        # Use the exact already-observed first bar, including trade count.
        if t==6000:activity=(replace(activity[0],trade_count=10),*rows)
        q=quote(t,age=500000)
        s,w=reduce(s,Input(SOURCE,t,b,replace(level(2500),lower_int=12050,upper_int=12150,price_int=12100) if t>=2500 else None,activity,q),policy=p)
    assert w is None  # prior activity70, recent30 =>3*30>70.
    assert len(s.rejections)==3 and s.arm.boundary_ms==2000
    assert p.payload()!=POLICY.payload()


def test_faster_decision_retained_bar_cannot_advance_streak_and_fresh_quote_confirms_once():
    p=Policy(decision_resolution_ms=100);s,bars=ready(p)
    s,w=reduce(s,Input(SOURCE,20000,bars[-1],activity=bars),policy=p)
    assert w is None and len(s.rejections)==2
    # Decision contract explicitly permits a fresh current quote while completed
    # observation is still younger than its resolution; no new streak bar.
    s,w=reduce(s,Input(SOURCE,20100,bars[-1],activity=bars,quote=quote(20100)),policy=p)
    assert w is not None and len(s.rejections)==2
    _,w=reduce(s,Input(SOURCE,20200,bars[-1],activity=bars,quote=quote(20200)),policy=p)
    assert w is None


def test_transient_pullback_without_profit_arm_or_cross_cannot_cut_winner():
    s=state()
    for t in (5000,10000,15000,20000):
        s,w=reduce(s,Input(SOURCE,t,bar(t,close=9900,high=10500),level() if t>=9000 else None,quote=quote(t,bid=9800)),policy=POLICY)
        assert w is None and s.arm is None and s.cross is None


def test_recovery_price_at_resistance_resets_rejection_without_erasing_arm_cross():
    s,_=ready();s,w=reduce(s,Input(SOURCE,20000,bar(20000,close=11100)),policy=POLICY)
    assert w is None and not s.rejections and s.arm is not None and s.cross is not None


def test_tampered_cold_state_rejects_future_arm_cross_or_rejection():
    s,bars=ready()
    for kwargs in ({'last_decision_boundary_ms':10000},{'arm':bars[2]},
                   {'resistance':replace(s.resistance,available_boundary_ms=10000)},
                   {'rejections':(bars[0],)}):
        with pytest.raises(ValueError):replace(s,**kwargs)


@pytest.mark.parametrize('scenario',['already_above','gap','invalid_prior'])
def test_above_price_is_not_a_causal_crossing(scenario):
    prior=bar(5000,close=11200 if scenario=='already_above' else 11000)
    s,_=reduce(state(),Input(SOURCE,5000,prior),policy=POLICY)
    if scenario=='invalid_prior':
        s,_=reduce(s,Input(SOURCE,10000,bar(10000,close=0,high=0,price_valid=False)),policy=POLICY)
    t=15000 if scenario in ('gap','invalid_prior') else 10000
    s,w=reduce(s,Input(SOURCE,t,bar(t,close=11200),level()),policy=POLICY)
    assert w is None and s.cross is None and s.arm is not None


def test_arm_bar_can_be_exact_contiguous_cross_predecessor():
    s,bars=ready()
    assert s.cross_prior==bars[0] and s.cross_prior==s.arm
    assert s.cross_prior.close_int <= s.resistance.price_int < s.cross.close_int


def test_generic_risk_ratio_rejection_count_and_liquidity_bounds_positive():
    p=Policy(bar_resolution_ms=1000,decision_resolution_ms=1000,
             arm_original_risk=(2,1),rejection_count=3,activity_count=6,
             recent_activity_multiplier=3,maximum_quote_age_us=500000)
    s=state(p);rows=[];l=Level(SOURCE,'higher','c'*64,12050,12150,12100,0,0)
    for t,c,h,tr in [(1000,11000,11500,30),(2000,12000,12000,30),
                     (3000,12200,12200,30),(4000,11000,11200,10),
                     (5000,11000,11200,10),(6000,10900,11200,10)]:
        b=bar(t,close=c,high=h,trades=tr,resolution_ms=1000);rows.append(b)
        s,w=reduce(s,Input(SOURCE,t,b,l,tuple(rows) if t==6000 else (),quote(t,age=500000)),policy=p)
    assert w is not None and len(w.rejections)==3 and len(w.activity)==6
    assert w.arm.boundary_ms==2000 and w.cross.boundary_ms==3000


def test_late_retained_observation_does_not_confirm_after_stale_boundary():
    p=Policy(decision_resolution_ms=100);s,bars=ready(p)
    s,_=reduce(s,Input(SOURCE,20000,bars[-1],activity=bars),policy=p)
    keep,w=reduce(s,Input(SOURCE,25000,bars[-1],activity=bars,quote=quote(25000)),policy=p)
    assert w is None and keep.arm==s.arm and keep.cross==s.cross and keep.rejections==s.rejections


@pytest.mark.parametrize('field,value', [('original_ask_int',True),('original_stop_int',10000),
    ('account_id',''),('last_decision_boundary_ms',-1),('rejections',[]),('fired',True)])
def test_malformed_initial_state_is_not_authority(field,value):
    with pytest.raises(ValueError):replace(state(),**{field:value})


def test_exact_fresh_typed_cold_state_replay_matches_live_output():
    from dataclasses import fields
    s,bars=ready()
    cold=State(**{f.name:getattr(s,f.name) for f in fields(State)})
    x=Input(SOURCE,20000,bars[-1],activity=bars,quote=quote())
    assert reduce(cold,x,policy=POLICY)==reduce(s,x,policy=POLICY)
    with pytest.raises(ValueError):replace(s,last_bar=replace(s.last_bar,trade_count=11))
    with pytest.raises(ValueError):replace(s,last_bar=s.arm)


def test_exact_same_clock_content_cannot_be_revised_after_invalid_observation():
    p=Policy(decision_resolution_ms=100);s,bars=ready(p)
    bad=replace(bars[-1],price_valid=False)
    s,_=reduce(s,Input(SOURCE,20000,bad),policy=p)
    with pytest.raises(ValueError):reduce(s,Input(SOURCE,20100,bars[-1]),policy=p)


def test_liquidity_just_above_declared_ratio_prevents_exit():
    s,bars=ready();new=replace(bars[-1],trade_count=11)
    _,w=reduce(s,Input(SOURCE,20000,new,activity=(*bars[:3],new),quote=quote()),policy=POLICY)
    assert w is None


@pytest.mark.parametrize('kwargs',[{'higher_priority_exit':1},{'pending_exit':None},
                                   {'position_held':1},{'activity':[]}])
def test_input_shape_cannot_coerce_caller_guards(kwargs):
    s,bars=ready()
    with pytest.raises(ValueError):reduce(s,replace(Input(SOURCE,20000,bars[-1]),**kwargs),policy=POLICY)


def test_policy_drift_from_persisted_state_rejected():
    with pytest.raises(ValueError):reduce(state(),Input(SOURCE,5000,bar(5000)),
                                        policy=Policy(recent_activity_multiplier=3))


def test_native_content_build_string_is_not_converted_to_uuid_or_hash():
    assert replace(SOURCE,market_build_id='native-content-build-string').market_build_id
    with pytest.raises(ValueError):replace(SOURCE,market_build_id='')


def test_source_attempts_and_tokens_cannot_be_coerced():
    with pytest.raises(ValueError):replace(SOURCE,bars_attempt_id='not-an-attempt')
    with pytest.raises(ValueError):replace(SOURCE,market_plan_token='not-a-token')
