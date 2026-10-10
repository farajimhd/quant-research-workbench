from dataclasses import replace

import pytest

from src.trading_runtime.consecutive_price_confirmed_risk import (
    INPUT, RULE, POLICY_KEY, ConsecutivePriceRiskPolicy, consecutive_price_risk_failure,
    parse_consecutive_price_risk_policy, validate_consecutive_price_risk_diagnostic,
)
from src.trading_runtime.confirmed_original_risk_failure import (
    CompletedRiskBucket, OriginalRiskDecisionDiagnostic, INHERITED_ORIGINAL_RISK_RULE,
)
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


def declared():
    import json
    from src.trading_runtime.journal_contract import canonical_json
    from src.trading_runtime.strategy_fifty_seven_release import release_contract
    _, _, _, policy = case()
    parent = release_contract()
    release = replace(parent, input_contracts=(*parent.input_contracts, INPUT),
        rule_set_contracts=(*parent.rule_set_contracts, RULE), approved_digest='')
    release = replace(release, approved_digest=release.digest())
    return release, {POLICY_KEY: json.loads(canonical_json(policy.payload()))}


def test_normalized_declaration_round_trip_and_unselected_contract():
    release, policies = declared()
    assert parse_consecutive_price_risk_policy(release, policies) == case()[3]
    from src.trading_runtime.strategy_fifty_seven_release import release_contract
    assert parse_consecutive_price_risk_policy(release_contract(), {}) is None
    with pytest.raises(ValueError, match='incomplete'):
        parse_consecutive_price_risk_policy(release, {})


@pytest.mark.parametrize('field,value', [('priority', 'extension_first'),
    ('missing', 'synthetic_confirmation'), ('consecutive_buckets', 3),
    ('confirmation', 'current_close_only')])
def test_changed_normalized_policy_is_rejected(field, value):
    release, policies = declared()
    policies[POLICY_KEY][field] = value
    with pytest.raises(ValueError):
        parse_consecutive_price_risk_policy(release, policies)


def diagnostic():
    value, prior, newest, policy = case()
    selected = consecutive_price_risk_failure(value, prior=prior, newest=newest, policy=policy)
    return OriginalRiskDecisionDiagnostic(selected.current, newest, prior, RULE), policy


def test_diagnostic_retains_both_completed_sources():
    selected, policy = diagnostic()
    assert validate_consecutive_price_risk_diagnostic(selected, policy=policy) is selected


@pytest.mark.parametrize('changes', [dict(prior=None), dict(semantic_rule='foreign'),
    dict(semantic_rule=INHERITED_ORIGINAL_RISK_RULE)])
def test_diagnostic_cannot_drop_prior_or_relabel_firing_rule(changes):
    selected, policy = diagnostic()
    with pytest.raises(ValueError):
        validate_consecutive_price_risk_diagnostic(replace(selected, **changes), policy=policy)


def test_inherited_firing_keeps_priority_over_the_new_rule():
    from src.trading_runtime.strategy_zero_regime_risk_failure import zero_regime_risk_failure
    value, prior, newest, policy = case()
    value = replace(value, macd_line=-.2, macd_signal=-.1)
    newest = replace(newest, macd_line=-.2, macd_signal=-.1)
    inherited = zero_regime_risk_failure(value)
    assert inherited is not None
    with pytest.raises(ValueError, match='priority'):
        validate_consecutive_price_risk_diagnostic(
            OriginalRiskDecisionDiagnostic(inherited, newest, prior, RULE), policy=policy)
    selected = OriginalRiskDecisionDiagnostic(inherited, newest, None, INHERITED_ORIGINAL_RISK_RULE)
    assert validate_consecutive_price_risk_diagnostic(selected, policy=policy) is selected


def test_inherited_ah_weak_positive_macd_keeps_exact_semantic_authority():
    from src.trading_runtime.strategy_fifty_release import EARLY_FAILURE_POLICY
    from src.trading_runtime.early_original_risk_failure import early_original_risk_failure
    from src.trading_runtime.confirmed_original_risk_failure import validate_decision_diagnostic
    value, prior, newest, policy = case()
    value = replace(value, boundary_ms=43215000, first_held_boundary_ms=43200100,
        completed_five_second_boundary_ms=43215000, completed_five_second_close_int=68900,
        bid=6.89, ask=6.90, macd_line=.1, macd_signal=.12)
    newest = replace(newest, boundary_ms=value.boundary_ms, close_int=68900,
        macd_line=.1, macd_signal=.12)
    inherited = early_original_risk_failure(value, policy=EARLY_FAILURE_POLICY)
    assert inherited is not None and inherited.macd_line > 0
    assert inherited.macd_line < inherited.macd_signal
    selected = OriginalRiskDecisionDiagnostic(inherited, newest, None, EARLY_FAILURE_POLICY.policy_id)
    assert validate_decision_diagnostic(selected, policy=policy,
        inherited_early_policy=EARLY_FAILURE_POLICY) is selected
    with pytest.raises(ValueError, match='Foreign'):
        validate_decision_diagnostic(selected, policy=policy)
    with pytest.raises(ValueError):
        validate_decision_diagnostic(replace(selected, prior=prior), policy=policy,
            inherited_early_policy=EARLY_FAILURE_POLICY)


def test_extension_cannot_supersede_an_eligible_declared_early_rule():
    from src.trading_runtime.strategy_fifty_release import EARLY_FAILURE_POLICY
    from src.trading_runtime.early_original_risk_failure import early_original_risk_failure
    value, prior, newest, policy = case()
    policy = replace(policy, price_policy=replace(policy.price_policy, afterhours_fraction=(1, 4)))
    value = replace(value, boundary_ms=43215000, first_held_boundary_ms=43200100,
        completed_five_second_boundary_ms=43215000, completed_five_second_close_int=68900,
        bid=6.89, ask=6.90, macd_line=.1, macd_signal=.12)
    newest = replace(newest, boundary_ms=value.boundary_ms, close_int=68900,
        macd_line=.1, macd_signal=.12)
    prior = replace(newest, boundary_ms=value.boundary_ms-5000)
    witness = early_original_risk_failure(value, policy=EARLY_FAILURE_POLICY)
    assert consecutive_price_risk_failure(value, prior=prior, newest=newest, policy=policy) is not None
    with pytest.raises(ValueError, match='priority'):
        validate_consecutive_price_risk_diagnostic(
            OriginalRiskDecisionDiagnostic(witness, newest, prior, RULE), policy=policy,
            inherited_early_policy=EARLY_FAILURE_POLICY)
