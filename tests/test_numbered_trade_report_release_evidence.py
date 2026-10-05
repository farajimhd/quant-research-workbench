"""Saved trade reports must retain installed numbered release authority."""
from datetime import date
from types import SimpleNamespace

import pytest

from scripts.clickhouse import report_strategy_one_trades as command


@pytest.mark.parametrize('number', [35, 36, 37, 38, 42, 46])
@pytest.mark.parametrize('changed_release', [False, True])
def test_report_requires_exact_numbered_release_and_preserves_identity(monkeypatch, number, changed_release):
    from src.backend import backtest_strategy_one_configuration as configurations
    context = {'strategy_revision': number, 'configuration_hash': 'a' * 64, 'initial_cash': 10000}
    plan = SimpleNamespace(build_id='build', token='market-token', units=())
    manifest = {'contract': {'number': number}, 'approved_digest': 'sealed-policy'}
    release = SimpleNamespace(payload_hash=('b' if changed_release else 'a') * 64,
        payload={'strategy': {'numbered_release': manifest}}, token='release-token')
    market = object()
    calls = []
    monkeypatch.setattr(command, 'load_v4_terminal_review_page',
        lambda *_a, **_k: {'status': 'completed'})
    monkeypatch.setattr(command, 'certified_saved_run_plan',
        lambda *_a, **_k: (date(2026, 8, 10), context, None, plan))
    def certify(actual, selected):
        assert actual is market and selected == number
        calls.append(selected)
        return release
    monkeypatch.setattr(configurations, 'certify_numbered_configuration', certify)
    monkeypatch.setattr(command, 'load_v4_performance_report', lambda *_: {
        'verified_sequence': 7, 'position_lifecycles': [], 'report': {'episodes': []}})
    monkeypatch.setattr(command, 'load_broker_observed_drawdown',
        lambda *_: {'verified_terminal_sequence': 7})
    if changed_release:
        with pytest.raises(RuntimeError, match='sealed run configuration'):
            command.build_report(object(), market, 'run')
    else:
        report = command.build_report(object(), market, 'run')
        assert report['strategy_number'] == number
        assert report['numbered_release'] == manifest
        assert report['configuration_release_token'] == release.token
        assert report['initial_cash'] == 10000 and report['open_lifecycle_count'] == 0
    assert calls == [number]
