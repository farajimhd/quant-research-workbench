"""Causal scalar candidate checks; native release/recovery are separate gates."""
from dataclasses import fields, replace
import pytest
from src.trading_runtime.strategy_followthrough_failure import FollowThroughFailureInput
from src.trading_runtime.strategy_profit_giveback import (
    ProfitGivebackInput, ProfitGivebackWitness, profit_giveback,
    profit_giveback_policy_payload,
)


def sample():
    return ProfitGivebackInput(FollowThroughFailureInput(
        10000, 1000, 10., 9., 10000, 105000, True,
        .01, .02, 10.5, 10.51, 1000, 100., False), 110000, 9900)


def test_exact_threshold_and_positive_macd_regime():
    assert type(profit_giveback(sample())) is ProfitGivebackWitness


@pytest.mark.parametrize('field,value', [
    ('completed_five_second_close_int', 105001), ('bid', 10.5001),
    ('quote_age_us', 1000001), ('quote_age_us', -1), ('quote_age_us', None),
    ('macd_line', .02), ('macd_signal', None), ('macd_line', float('nan')),
    ('price_valid', False), ('pending_exit', True), ('position_quantity', 0.),
    ('completed_five_second_boundary_ms', 5000), ('first_held_boundary_ms', 6000),
    ('boundary_ms', 10100), ('bid', None), ('ask', 10.4),
])
def test_missing_stale_noncompleted_or_nonfailed_inputs_do_not_exit(field, value):
    original = sample()
    assert profit_giveback(replace(original, completed=replace(original.completed, **{field: value}))) is None


def test_unarmed_high_does_not_exit():
    assert profit_giveback(replace(sample(), prior_high_int=109999)) is None


@pytest.mark.parametrize('boundary', [10000, 10100, 900, -100])
def test_current_future_or_preheld_high_is_rejected(boundary):
    with pytest.raises(ValueError, match='prior causal'):
        profit_giveback(replace(sample(), prior_high_through_boundary_ms=boundary))


def test_decimal_source_price_exact_floor_is_admitted():
    original = sample()
    completed = replace(original.completed, reference_ask=3.23, initial_stop=3.05,
                        completed_five_second_close_int=33200, bid=3.32, ask=3.33)
    assert profit_giveback(replace(original, completed=completed, prior_high_int=34100)) is not None
    assert profit_giveback(replace(original, completed=replace(completed, bid=3.3201), prior_high_int=34100)) is None


def test_witness_is_scalar_and_retains_original_risk_and_prior_high():
    witness = profit_giveback(sample())
    assert all(type(getattr(witness, f.name)) in (int, float) for f in fields(witness))
    assert witness.reference_ask == 10. and witness.initial_stop == 9.
    assert witness.prior_high_int == 110000 and witness.prior_high_through_boundary_ms == 9900


def test_policy_declares_parent_and_prior_high_authority():
    policy = profit_giveback_policy_payload()
    assert policy['parent_failure_policy'] == 'retain_strategy30'
    assert policy['current_bucket_arming'] is False
    assert policy['time_alone'] == 'never_exits'
