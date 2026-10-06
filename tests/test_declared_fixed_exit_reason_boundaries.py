"""Canonical declared reason recognition; no financial/source admission claim."""
import pytest

from src.trading_runtime.numbered_fixed_strategy import declared_fixed_exit_reason


RULES = (
    ('profit_giveback', 'strategy-thirty-one-original-risk-profit-giveback-v1'),
    ('confirmed_ah_failure', 'strategy.confirmed-ah-risk-failure.v1'),
    ('liquidity_fade_failure', 'strategy-thirty-five-completed-liquidity-fade-v1'),
)


@pytest.mark.parametrize('suffix,rule', RULES)
def test_actual_registered_declaration_recognizes_exact_factory_reason(suffix, rule):
    assert declared_fixed_exit_reason(f'strategy_64_{suffix}', rule)


@pytest.mark.parametrize('reason', [
    'strategy_4294967296_profit_giveback',
    'strategy_' + '1' * 4301 + '_profit_giveback',
    'strategy_064_profit_giveback', 'strategy_0_profit_giveback',
    'strategy_+64_profit_giveback', 'strategy_64.0_profit_giveback',
    'strategy_６４_profit_giveback', 'strategy_64_profit_giveback ',
    'strategy_64_unknown', '', None, True, 64,
])
def test_malformed_reason_never_queries_registry(monkeypatch, reason):
    from src.trading_runtime import strategy_registry
    def forbidden(_number):
        raise AssertionError('Malformed input reached registry')
    monkeypatch.setattr(strategy_registry, 'numbered_strategy', forbidden)
    assert not declared_fixed_exit_reason(reason, RULES[0][1])


@pytest.mark.parametrize('rule', ['foreign', None, True, 31])
def test_unknown_rule_never_queries_registry(monkeypatch, rule):
    from src.trading_runtime import strategy_registry
    def forbidden(_number):
        raise AssertionError('Foreign rule reached registry')
    monkeypatch.setattr(strategy_registry, 'numbered_strategy', forbidden)
    assert not declared_fixed_exit_reason('strategy_64_profit_giveback', rule)


@pytest.mark.parametrize('suffix,rule', RULES)
def test_other_declared_rule_cannot_recognize_foreign_factory_reason(suffix, rule):
    other = next(item for item in RULES if item[1] != rule)
    assert not declared_fixed_exit_reason(f'strategy_64_{suffix}', other[1])


def test_corrupt_registered_release_failure_is_not_hidden(monkeypatch):
    from src.trading_runtime import strategy_registry
    def corrupted(_number):
        raise ValueError('Registered release seal differs')
    monkeypatch.setattr(strategy_registry, 'numbered_strategy', corrupted)
    with pytest.raises(ValueError, match='Registered release seal differs'):
        declared_fixed_exit_reason('strategy_64_profit_giveback', RULES[0][1])


def test_unpublished_identity_and_legacy_nonadapter_cannot_gain_authority():
    assert not declared_fixed_exit_reason('strategy_7001_profit_giveback', RULES[0][1])
    assert not declared_fixed_exit_reason('strategy_42_profit_giveback', RULES[0][1])
