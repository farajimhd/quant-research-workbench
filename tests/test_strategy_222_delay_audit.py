import pytest

from scripts.audit_strategy_222_delays import select_comparison


def test_delay_audit_keeps_portfolio_scope_and_longest_coverage_independent_of_order():
    trials={
        'long':dict(symbol='JUNS',end='08:00:00'),
        'short':dict(symbol='JUNS',end='07:20:00'),
        'shared':dict(symbol='PORTFOLIO',end='09:40:00'),
    }
    values=[dict(name='candidate',run_id=key) for key in trials]
    for order in (values,list(reversed(values))):
        case=dict(variants=order)
        assert select_comparison(case,trials,'candidate')['run_id']=='long'
        assert select_comparison(case,trials,'candidate',portfolio=True)['run_id']=='shared'
    assert select_comparison(dict(variants=values[:2]),trials,'candidate',portfolio=True) is None
    assert select_comparison(dict(variants=values),trials,'missing') is None


def test_delay_audit_fails_on_missing_trial_instead_of_guessing_its_scope():
    with pytest.raises(KeyError):
        select_comparison(dict(variants=[dict(name='candidate',run_id='unknown')]),{},'candidate')
