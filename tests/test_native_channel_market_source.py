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


def test_feature_pin_preserves_declared_exclusion_when_candidate_exclusions_are_empty(monkeypatch):
    from src.backend import backtest_native_channel_market_source as subject
    m = market()
    m = replace(m, tickers=('X', 'Y'), units=m.units + tuple(
        replace(unit, ticker='Y') for unit in m.units))
    original = subject.certify_native_channel_market_source
    monkeypatch.setattr(subject, 'certify_native_channel_market_source',
        lambda market, policy, **kwargs: original(market, policy, **kwargs,
            plan_factory=lambda **unused: feature(m)))
    pin = subject.native_channel_feature_source_pin(m, POLICY, exclusions=('Y',))
    metadata = {'strategy_one_input_exclusions': [], 'native_channel_feature_source': pin}
    selected = subject.certify_pinned_native_channel_market_source(m, POLICY, metadata)
    assert selected == project_market_day_plan(feature(m), ('X',))
    assert pin['declared_input_exclusions'] == ['Y']
    assert metadata['strategy_one_input_exclusions'] == []
    with pytest.raises(ValueError, match='parent changed'):
        subject.certify_pinned_native_channel_market_source(m, POLICY, {
            **metadata, 'native_channel_feature_source': {**pin, 'parent_market_plan_token': '0' * 64}})


@pytest.mark.parametrize('pin', [None, {}, {'schema': 'foreign'}])
def test_missing_source_pin_never_falls_back_to_effective_candidate_exclusions(monkeypatch, pin):
    from src.backend import backtest_native_channel_market_source as subject
    monkeypatch.setattr(subject, 'certify_native_channel_market_source',
        lambda *a, **k: pytest.fail('Unpinned source performed I/O'))
    with pytest.raises(ValueError, match='launch-pinned'):
        subject.certify_pinned_native_channel_market_source(market(), POLICY, {
            'strategy_one_input_exclusions': [], 'native_channel_feature_source': pin})
