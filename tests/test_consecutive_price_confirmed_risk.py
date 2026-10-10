from dataclasses import replace

import pytest

from src.trading_runtime.consecutive_price_confirmed_risk import (
    ConsecutivePriceRiskPolicy, consecutive_price_risk_failure,
)
from src.trading_runtime.confirmed_original_risk_failure import CompletedRiskBucket
from src.trading_runtime.price_confirmed_original_risk import PriceConfirmedOriginalRiskPolicy
from src.trading_runtime.strategy_followthrough_failure import FollowThroughFailureInput


def case():
    policy = ConsecutivePriceRiskPolicy(PriceConfirmedOriginalRiskPolicy((1, 2), (1, 2), 120000, 1000000))
    value = FollowThroughFailureInput(15000, 100, 6.99, 6.61, 15000,
        68000, True, .15, .14, 6.8, 6.81, 1000000, 1., False)
    prior = CompletedRiskBucket(10000, 68000, True, .16, .14,
        'build', 'a' * 64, '11111111-1111-1111-1111-111111111111',
        '22222222-2222-2222-2222-222222222222', '2026-08-10', 'TEST',
        '33333333-3333-3333-3333-333333333333')
    newest = replace(prior, boundary_ms=15000, macd_line=.15)
    return value, prior, newest, policy


def test_exact_half_risk_boundary_and_positive_momentum_are_preserved():
    value, prior, newest, policy = case()
    witness = consecutive_price_risk_failure(value, prior=prior, newest=newest, policy=policy)
    assert witness.current.reference_ask == 6.99
    assert witness.prior == prior and witness.newest == newest
    assert witness.current.macd_line > witness.current.macd_signal


@pytest.mark.parametrize('changes', [dict(close_int=68001), dict(boundary_ms=5000),
    dict(boundary_ms=20000), dict(boundary_ms=9900), dict(price_valid=False),
    dict(close_int=None), dict(macd_line=None), dict(macd_signal=float('nan'))])
def test_partial_missing_future_gap_and_prior_recovery_do_not_confirm(changes):
    value, prior, newest, policy = case()
    assert consecutive_price_risk_failure(value, prior=replace(prior, **changes),
        newest=newest, policy=policy) is None


@pytest.mark.parametrize('field,value', [('source_build_id', 'other'),
    ('source_market_plan_token', 'b' * 64), ('session_date', '2026-08-11'),
    ('ticker', 'OTHER'), ('source_bars_attempt_id', '44444444-4444-4444-4444-444444444444'),
    ('source_indicators_attempt_id', '44444444-4444-4444-4444-444444444444'),
    ('source_liquidity_attempt_id', '44444444-4444-4444-4444-444444444444')])
def test_foreign_source_is_rejected(field, value):
    current, prior, newest, policy = case()
    with pytest.raises(ValueError, match='ownership'):
        consecutive_price_risk_failure(current, prior=replace(prior, **{field: value}),
            newest=newest, policy=policy)


@pytest.mark.parametrize('changes', [dict(quote_age_us=1000001), dict(pending_exit=True),
    dict(position_quantity=0.), dict(bid=6.8001), dict(first_held_boundary_ms=5100)])
def test_current_freshness_pending_exit_and_held_fence(changes):
    value, prior, newest, policy = case()
    assert consecutive_price_risk_failure(replace(value, **changes), prior=prior,
        newest=newest, policy=policy) is None


def test_newest_cannot_substitute_earlier_or_changed_completed_facts():
    value, prior, newest, policy = case()
    with pytest.raises(ValueError, match='current witness'):
        consecutive_price_risk_failure(value, prior=prior,
            newest=replace(newest, close_int=67999), policy=policy)
    assert consecutive_price_risk_failure(value, prior=None, newest=newest, policy=policy) is None


@pytest.mark.parametrize('changes', [dict(close_int=68000.), dict(price_valid=1)])
def test_newest_equivalent_numbers_cannot_substitute_typed_facts(changes):
    value, prior, newest, policy = case()
    with pytest.raises(ValueError, match='current witness'):
        consecutive_price_risk_failure(value, prior=prior,
            newest=replace(newest, **changes), policy=policy)


def test_supported_policy_shape_is_explicit_and_frozen():
    _, _, _, policy = case()
    with pytest.raises(ValueError):
        replace(policy, consecutive_buckets=3)
    assert policy.payload()['price_policy']['reference'] == 'original_proposal_ask'
