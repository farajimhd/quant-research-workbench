from dataclasses import replace

import pytest

from src.trading_runtime.confirmed_original_risk_failure import (
    CompletedRiskBucket, ConfirmedOriginalRiskPolicy, confirmed_original_risk_failure,
)
from src.trading_runtime.strategy_followthrough_failure import FollowThroughFailureInput


def inputs(boundary=100_000, held=80_000):
    prior = CompletedRiskBucket(boundary-5000, 97500, True, .1, .2,
        'build', 'a'*64, '00000000-0000-0000-0000-000000000001',
        '00000000-0000-0000-0000-000000000002', '2026-01-01', 'TEST',
        '00000000-0000-0000-0000-000000000003')
    newest = replace(prior, boundary_ms=boundary)
    value = FollowThroughFailureInput(boundary, held, 10., 9., boundary,
        97500, True, .1, .2, 9.75, 9.76, 1_000_000, 100., False)
    return value, prior, newest


def run(value, prior, newest):
    return confirmed_original_risk_failure(value, prior=prior, newest=newest,
                                            policy=ConfirmedOriginalRiskPolicy())


@pytest.mark.parametrize('boundary,held', [(100_000,80_000), (140_000,60_000),
    (19_800_000,19_770_000), (43_225_000,43_210_000), (57_600_000,57_570_000)])
def test_exact_two_buckets_and_late_extended_session(boundary, held):
    value, prior, newest = inputs(boundary, held)
    witness = run(value, prior, newest)
    assert witness is not None
    assert witness.prior == prior and witness.newest == newest
    assert witness.current.reference_ask == 10 and witness.current.initial_stop == 9


@pytest.mark.parametrize('field,bad', [('boundary_ms',90000), ('close_int',97501),
    ('price_valid',False), ('macd_line',.2), ('macd_signal',float('nan')),
    ('source_build_id','other'), ('source_bars_attempt_id','00000000-0000-0000-0000-000000000003'),
    ('source_indicators_attempt_id','00000000-0000-0000-0000-000000000003'), ('source_market_plan_token','b'*64),
    ('session_date','2026-01-02'), ('ticker','OTHER')])
def test_prior_not_adverse_or_foreign_never_extends(field,bad):
    value,prior,newest=inputs()
    assert run(value,replace(prior,**{field:bad}),newest) is None


@pytest.mark.parametrize('field,bad', [('quote_age_us',1_000_001),('bid',9.75001),
    ('pending_exit',True),('position_quantity',0.),('first_held_boundary_ms',90001),
    ('completed_five_second_boundary_ms',95000),('macd_line',.2),
    ('boundary_ms',43_200_000)])
def test_position_current_quote_and_original_session_fences(field,bad):
    value,prior,newest=inputs()
    if field=='first_held_boundary_ms':bad=90100
    assert run(replace(value,**{field:bad}),prior,newest) is None


def test_earliest_both_wholly_held_and_reset_missing():
    value,prior,newest=inputs(100_000,90_000)
    assert run(value,prior,newest) is not None
    assert run(replace(value,first_held_boundary_ms=90100),prior,newest) is None
    assert run(value,None,newest) is None
    assert run(value,prior,None) is None
    assert run(value,replace(prior,boundary_ms=85000),newest) is None


@pytest.mark.parametrize('changes',[{'consecutive_buckets':1},
    {'original_risk_fraction':(1,2)}, {'completed_bucket_ms':1000},
    {'quote_max_age_us':True}, {'policy_id':'foreign'}])
def test_policy_is_closed(changes):
    with pytest.raises(ValueError):ConfirmedOriginalRiskPolicy(**changes)


@pytest.mark.parametrize('bad',[None,'100000',True,100000.0,-5000,2**32])
def test_malformed_bucket_clock_never_arithmetic_exception(bad):
    value,prior,newest=inputs()
    assert run(value,replace(prior,boundary_ms=bad),newest) is None
    assert run(value,prior,replace(newest,boundary_ms=bad)) is None


@pytest.mark.parametrize('field,bad',[('source_market_plan_token','bad'),
    ('source_bars_attempt_id','bad'),('session_date','2026-1-1')])
def test_malformed_source_rejected_with_controlled_error(field,bad):
    value,prior,newest=inputs()
    with pytest.raises(ValueError,match='source|date|attempt'):
        run(value,replace(prior,**{field:bad}),newest)
