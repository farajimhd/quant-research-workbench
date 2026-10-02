"""Version-bound scalar persistence checks, not native publication acceptance."""
from dataclasses import replace
from datetime import date

import pytest

from src.trading_runtime.strategy_half_risk_liquidity_fade import (
    HalfRiskLiquidityFadeFailure, half_risk_liquidity_fade_failure,
    numbered_liquidity_fade_failure,
)
from src.trading_runtime.strategy_liquidity_fade_failure import LiquidityFadeFailure
from src.trading_runtime.strategy_liquidity_fade_exit import (
    liquidity_fade_exit_intent, validate_liquidity_fade_witness,
)
from src.trading_runtime.arte_liquidity_fade_failure_v4 import (
    LIQUIDITY_FADE_FAILURE, project_liquidity_fade_failure, restore_liquidity_fade_failure,
)
from test_strategy_half_risk_liquidity_fade import supplied
from test_arte_liquidity_fade_failure_v4 import prepared_case, IDENTITY


def projected(value):
    witness=numbered_liquidity_fade_failure(value,strategy_number=39)
    _,financial,_,template=prepared_case()
    args=dict(session_date=date(2026,8,18),source_entry_intent_id=IDENTITY,strategy_number=39)
    intent=liquidity_fade_exit_intent(witness,financial,**args)
    fields=('run_id','batch_id','parent_record_id','source_build_id','source_bars_attempt_id',
        'source_indicators_attempt_id','source_liquidity_attempt_id','source_market_plan_token',
        'source_manager_snapshot_id','source_manager_checkpoint_sequence','source_manager_snapshot_hash',
        'source_broker_snapshot_id','source_broker_snapshot_hash')
    row=project_liquidity_fade_failure(witness,intent,financial,**args,**{k:template[k] for k in fields})
    return witness,financial,intent,row


@pytest.mark.parametrize('counts,kind',[
    ((100,100,60,40),HalfRiskLiquidityFadeFailure),
    ((100,100,30,20),LiquidityFadeFailure),
])
def test_normalized_scalar_roundtrip_preserves_version_and_inherited_priority(counts,kind):
    witness,_,intent,row=projected(supplied(counts=counts))
    assert type(witness) is kind
    assert intent.reason=='strategy_thirty_nine_liquidity_fade_failure'
    assert row['strategy_number']==39
    assert set(row)=={name for name,_ in LIQUIDITY_FADE_FAILURE.columns}-{'content_hash'}
    assert restore_liquidity_fade_failure(row)==witness
    assert type(restore_liquidity_fade_failure(row)) is kind


@pytest.mark.parametrize('number',[35,36,37,38])
def test_additional_witness_cannot_be_relabelled_as_an_older_release(number):
    witness,financial,_,row=projected(supplied())
    with pytest.raises(ValueError,match='typed witness'):
        liquidity_fade_exit_intent(witness,financial,session_date=date(2026,8,18),
            source_entry_intent_id=IDENTITY,strategy_number=number)
    with pytest.raises(ValueError):restore_liquidity_fade_failure(dict(row,strategy_number=number))
    assert numbered_liquidity_fade_failure(supplied(),strategy_number=number) is None


def test_additional_witness_cannot_displace_an_eligible_inherited_exit():
    value=supplied(counts=(100,100,30,20))
    additional=half_risk_liquidity_fade_failure(value)
    assert type(additional) is HalfRiskLiquidityFadeFailure
    assert type(numbered_liquidity_fade_failure(value,strategy_number=39)) is LiquidityFadeFailure
    with pytest.raises(ValueError,match='producer observations'):
        validate_liquidity_fade_witness(additional,strategy_number=39)


@pytest.mark.parametrize('field,value',[
    ('strategy_number',True),('strategy_number',40),('trade_count_0',True),
    ('trade_count_3',41),('completed_close_int',38001),('bid',3.8001),
    ('quote_age_us',1000001),('first_held_boundary_ms',20100),
    ('source_broker_snapshot_hash','invalid'),('source_bars_attempt_id','invalid'),
])
def test_changed_scalar_or_source_metadata_rejects(field,value):
    *_,row=projected(supplied())
    with pytest.raises(ValueError):restore_liquidity_fade_failure(dict(row,**{field:value}))


def test_additional_namespace_does_not_reuse_parent_intent_identity():
    witness,financial,new,row=projected(supplied(counts=(100,100,30,20)))
    old=liquidity_fade_exit_intent(witness,financial,session_date=date(2026,8,18),
        source_entry_intent_id=IDENTITY,strategy_number=38)
    assert new.intent_id!=old.intent_id and new.reason!=old.reason
    for field in ('quantity','reference_price','execution_policy','event_time'):
        assert getattr(new,field)==getattr(old,field)


@pytest.mark.parametrize('number',[True,34,40,'39'])
def test_policy_selector_rejects_unknown_or_coerced_number(number):
    with pytest.raises(ValueError):numbered_liquidity_fade_failure(supplied(),strategy_number=number)
