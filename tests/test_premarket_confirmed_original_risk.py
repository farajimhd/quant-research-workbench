"""Declared replacement differential proof; synthetic certified producer facts only."""
from dataclasses import replace
import pytest
from test_confirmed_original_risk_failure import inputs
from src.trading_runtime.premarket_confirmed_original_risk import (
    PremarketConfirmedOriginalRiskPolicy, PREMARKET_CONFIRMED_RISK_RULE,
    replacement_stage, premarket_confirmed_original_risk_failure as run_rule)
from src.trading_runtime.confirmed_original_risk_failure import (
    ConfirmedOriginalRiskPolicy, OriginalRiskDecisionDiagnostic,
    CONFIRMED_ORIGINAL_RISK_RULE, INHERITED_ORIGINAL_RISK_RULE,
    confirmed_original_risk_failure, validate_decision_diagnostic)
from src.trading_runtime.strategy_zero_regime_risk_failure import zero_regime_risk_failure

POLICY=PremarketConfirmedOriginalRiskPolicy()
def run(value,prior,newest):return run_rule(value,prior=prior,newest=newest,policy=POLICY)

def test_single_bucket_replaced_at_same_priority_only():
    value,prior,newest=inputs()
    assert zero_regime_risk_failure(value) is not None
    assert run(value,replace(prior,macd_line=.3),newest) is None
    result=run(value,prior,newest)
    assert result.current==zero_regime_risk_failure(value)
    assert result.semantic_rule==PREMARKET_CONFIRMED_RISK_RULE

@pytest.mark.parametrize('boundary,held,selected',[(100000,40000,True),(100000,39900,False),
    (19800000,19740000,True),(19800100,19750000,False),(19805000,19750000,False),
    (43225000,43210000,False),(100000,90100,True)])
def test_scope_age_session_and_nonaligned_held(boundary,held,selected):
    value,prior,newest=inputs(boundary,held)
    assert replacement_stage(value,policy=POLICY) is selected
    result=run(value,prior,newest)
    if not selected or held>boundary-10000:assert result is None
    else:assert result is not None

@pytest.mark.parametrize('field,bad',[('first_held_boundary_ms',0),('first_held_boundary_ms',True),
    ('first_held_boundary_ms',40001),('first_held_boundary_ms',40000.0),
    ('boundary_ms',100001),('boundary_ms',True),('pending_exit',1),('price_valid',1),
    ('reference_ask',float('nan')),('position_quantity',-1)])
def test_exact_inherited_malformed_position_rejection(field,bad):
    value,prior,newest=inputs()
    with pytest.raises(ValueError):run(replace(value,**{field:bad}),prior,newest)

def test_held_age_60001_is_malformed_not_silently_admitted():
    value,prior,newest=inputs(100000,39999)
    with pytest.raises(ValueError):run(value,prior,newest)

@pytest.mark.parametrize('defect',['prior_missing','newest_missing','gap','before_held','foreign_source',
    'foreign_ticker','foreign_date','prior_winner','prior_above_threshold','current_above_threshold',
    'stale_quote','pending','empty','quote_above','current_mismatch'])
def test_pair_causality_and_failure_fences(defect):
    value,prior,newest=inputs()
    if defect=='prior_missing':prior=None
    elif defect=='newest_missing':newest=None
    elif defect=='gap':prior=replace(prior,boundary_ms=90000)
    elif defect=='before_held':value=replace(value,first_held_boundary_ms=90100)
    elif defect=='foreign_source':prior=replace(prior,source_market_plan_token='b'*64)
    elif defect=='foreign_ticker':prior=replace(prior,ticker='OTHER')
    elif defect=='foreign_date':prior=replace(prior,session_date='2026-01-02')
    elif defect=='prior_winner':prior=replace(prior,macd_line=.3)
    elif defect=='prior_above_threshold':prior=replace(prior,close_int=97501)
    elif defect=='current_above_threshold':newest=replace(newest,close_int=97501);value=replace(value,completed_five_second_close_int=97501)
    elif defect=='stale_quote':value=replace(value,quote_age_us=1000001)
    elif defect=='pending':value=replace(value,pending_exit=True)
    elif defect=='empty':value=replace(value,position_quantity=0.)
    elif defect=='quote_above':value=replace(value,bid=9.7501)
    else:value=replace(value,completed_five_second_boundary_ms=95000)
    assert run(value,prior,newest) is None

@pytest.mark.parametrize('boundary,held',[(140000,52000),(43225000,43210000)])
def test_late_88_second_and_ah_extension_unchanged(boundary,held):
    value,prior,newest=inputs(boundary,held)
    assert not replacement_stage(value,policy=POLICY)
    result=confirmed_original_risk_failure(value,prior=prior,newest=newest,policy=ConfirmedOriginalRiskPolicy())
    assert result is not None and result.semantic_rule==CONFIRMED_ORIGINAL_RISK_RULE
    assert run(value,prior,newest) is None

@pytest.mark.parametrize('claimed',[INHERITED_ORIGINAL_RISK_RULE,CONFIRMED_ORIGINAL_RISK_RULE,'foreign'])
def test_suppressed_stage_cannot_be_relabelled_as_another_priority(claimed):
    value,prior,newest=inputs();result=run(value,prior,newest)
    diagnostic=OriginalRiskDecisionDiagnostic(result.current,newest,prior,claimed)
    with pytest.raises(ValueError):validate_decision_diagnostic(diagnostic,policy=ConfirmedOriginalRiskPolicy(),premarket_policy=POLICY)

def test_exact_diagnostic_requires_selected_policy_and_both_original_buckets():
    value,prior,newest=inputs();result=run(value,prior,newest)
    diagnostic=OriginalRiskDecisionDiagnostic(result.current,newest,prior,result.semantic_rule)
    assert validate_decision_diagnostic(diagnostic,policy=ConfirmedOriginalRiskPolicy(),premarket_policy=POLICY)==diagnostic
    with pytest.raises(ValueError):validate_decision_diagnostic(diagnostic,policy=ConfirmedOriginalRiskPolicy())
    with pytest.raises(ValueError):validate_decision_diagnostic(replace(diagnostic,prior=None),policy=ConfirmedOriginalRiskPolicy(),premarket_policy=POLICY)

@pytest.mark.parametrize('changes',[{'consecutive_buckets':1},{'original_risk_fraction':(1,2)},
    {'session_end_ms':19800100},{'maximum_held_age_ms':60001},{'quote_max_age_us':True},
    {'policy_id':'foreign'},{'completed_bucket_ms':5000.0}])
def test_closed_typed_policy(changes):
    with pytest.raises(ValueError):PremarketConfirmedOriginalRiskPolicy(**changes)

def test_actual_normalized_diagnostic_roundtrip_with_selected_policy_and_checkpoint():
    from src.trading_runtime.original_risk_checkpoint import OriginalRiskCheckpointReference
    from src.trading_runtime.strategy_followthrough_exit import followthrough_exit_intent
    from src.trading_runtime.arte_followthrough_failure_v4 import project_followthrough_failure
    from src.trading_runtime.arte_original_risk_diagnostic_v4 import project_original_risk_diagnostic,restore_original_risk_diagnostic
    from src.trading_runtime.arte_intent_projection import strategy_intent_batch
    from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView
    from src.trading_runtime.strategy_engine import AssignmentStatus,StrategyPermissions
    from datetime import date
    from uuid import UUID
    value,prior,newest=inputs();result=run(value,prior,newest)
    # Typed reference is a structural fixture, not independent financial approval.
    reference=OriginalRiskCheckpointReference(str(UUID(int=5)),7,'a'*64,str(UUID(int=6)),'b'*64)
    diagnostic=OriginalRiskDecisionDiagnostic(result.current,newest,prior,result.semantic_rule,reference)
    financial=StrategyOneFinancialView('assignment','account','TEST',AssignmentStatus.MANAGING,StrategyPermissions(),10.,False,False,False,1)
    source_id=str(UUID(int=77));day=date(2026,1,1)
    intent=followthrough_exit_intent(result.current,financial,session_date=day,source_entry_intent_id=source_id,strategy_number=74,diagnostic=diagnostic)
    base=strategy_intent_batch(intent,run_id=str(UUID(int=1)),run_month=day,account_id=financial.account_id,attempt_id=str(UUID(int=2)),batch_id=str(UUID(int=3)),prior_batch_id=str(UUID(int=0)),sequence=2,source_cursor='cursor',run_status='running',recorded_at=intent.event_time)
    failure=project_followthrough_failure(result.current,intent,source_id,run_id=base.run_id,batch_id=base.batch_id,parent_record_id=base.events[0]['record_id'],assignment_id=financial.assignment_id,strategy_number=74,diagnostic=diagnostic)
    row=project_original_risk_diagnostic(diagnostic,failure)
    assert restore_original_risk_diagnostic(row,result.current)==diagnostic
    for changes in ({'semantic_rule':INHERITED_ORIGINAL_RISK_RULE},{'semantic_rule':CONFIRMED_ORIGINAL_RISK_RULE},{'has_prior':0,'prior_boundary_ms':0,'prior_close_int':0,'prior_macd_line':0.,'prior_macd_signal':0.},{'prior_close_int':97501}):
        with pytest.raises(ValueError):restore_original_risk_diagnostic(dict(row,**changes),result.current)

@pytest.mark.parametrize('ask,stop,bid,close,accepted',[(1.0003,.9983,.9998,9998,False),
    (1000000000000.,999999999999.9999,1000000000000.,10000000000000000,True),
    (10.,9.,9.75,97500,True)])
def test_inherited_float64_source_lattice_both_fraction_directions(ask,stop,bid,close,accepted):
    value,prior,newest=inputs()
    value=replace(value,reference_ask=ask,initial_stop=stop,bid=bid,ask=ask,completed_five_second_close_int=close)
    prior=replace(prior,close_int=close);newest=replace(newest,close_int=close)
    old=zero_regime_risk_failure(value)
    assert (old is not None)==accepted
    result=run(value,prior,newest)
    assert (result is not None)==accepted
    if accepted:assert result.current==old

def test_finite_source_anchor_overflow_fails_closed_without_rounding():
    value,prior,newest=inputs()
    value=replace(value,reference_ask=1e308,initial_stop=9e307,bid=1.,ask=1.1)
    with pytest.raises(ValueError,match='overflow'):run(value,prior,newest)
