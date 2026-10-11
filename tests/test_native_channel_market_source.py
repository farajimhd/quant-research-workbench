from dataclasses import replace

import pytest

from src.backend.backtest_market_data import ExecutionInterval, project_market_day_plan
from src.backend.backtest_native_channel_market_source import (
    native_channel_source_configuration, certify_native_channel_market_source,
)
from src.trading_runtime.strategy_one_hundred_eighteen_release import CHANNEL_POLICY
from tests.test_native_causal_channel_producer import request

POLICY = CHANNEL_POLICY.exit_policy.sequence


def market():
    m = request().market
    units = tuple(replace(m.units[0], stage=stage)
        for stage in ('bars', 'technical', 'broker_100ms'))
    return replace(m, units=units, required_resolutions_ms=(100, 1000, 5000, 10000, 30000))


def feature(m):
    return replace(m, required_resolutions_ms=(100, 1000, 30000, 60000, 300000), token='f' * 64)


def test_declared_source_dependencies_and_source_only_plan_identity():
    m = market()
    calls = []
    def factory(**kwargs):
        calls.append(kwargs)
        return feature(m)
    actual = certify_native_channel_market_source(m, POLICY, plan_factory=factory)
    assert actual == feature(m) and m.required_resolutions_ms == (100, 1000, 5000, 10000, 30000)
    assert calls == [dict(sessions=m.sessions, tickers=m.tickers,
        configuration=native_channel_source_configuration(POLICY))]


@pytest.mark.parametrize('field,value', [
    ('build_id', '1' * 64), ('definition_hash', '2' * 64),
    ('sessions', ('2026-08-05',)), ('tickers', ('FOREIGN',)), ('units', ()),
    ('required_resolutions_ms', (100, 1000, 30000)),
    ('execution_interval', ExecutionInterval.fixed(1000)),
])
def test_source_bridge_rejects_changed_source_or_missing_dependency(field, value):
    m = market()
    with pytest.raises(ValueError, match='differs'):
        certify_native_channel_market_source(m, POLICY,
            plan_factory=lambda **kwargs: replace(feature(m), **{field: value}))


def test_external_exclusions_use_exact_producer_projection():
    m = market()
    other = tuple(replace(unit, ticker='Y') for unit in m.units)
    m = replace(m, tickers=('X', 'Y'), units=m.units + other)
    actual = certify_native_channel_market_source(m, POLICY, exclusions=('Y',),
        plan_factory=lambda **kwargs: feature(m))
    assert actual == project_market_day_plan(feature(m), ('X',))
    assert m.tickers == ('X', 'Y')
    with pytest.raises(ValueError):
        certify_native_channel_market_source(m, POLICY, exclusions=('X', 'Y'),
            plan_factory=lambda **kwargs: feature(m))


@pytest.mark.parametrize('exclusions', [('Y', 'X'), ('X', 'X'), ['X']])
def test_malformed_external_exclusions_fail_before_source_read(exclusions):
    with pytest.raises(ValueError):
        certify_native_channel_market_source(market(), POLICY, exclusions=exclusions,
            plan_factory=lambda **kwargs: pytest.fail('Malformed scope performed I/O'))
