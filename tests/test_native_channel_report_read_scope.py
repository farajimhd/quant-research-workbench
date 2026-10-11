"""Saved report selection preserves exact native policy and SELECT admission."""
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID

import pytest


def setup_profile(monkeypatch):
    from src.trading_runtime.strategy_registry import initialize_numbered_fixed_strategies
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    from src.trading_runtime import original_risk_diagnostic_profile as profile
    initialize_numbered_fixed_strategies()
    contract = numbered_fixed_strategy(125)
    options = profile.original_risk_runner_options(contract)
    context = dict(strategy_id=contract.strategy_id, strategy_revision=125,
                   configuration_hash='a' * 64)
    sealed = SimpleNamespace(payload_hash=context['configuration_hash'],
        strategy_number=125, payload={'strategy': {'strategy_id': contract.strategy_id}})
    monkeypatch.setattr(profile, 'declared_fixed_runner_options', lambda payload: options)
    return options, context, sealed


def transport(options):
    def execute(query):
        raise AssertionError('Rejected write reached the transport')
    return SimpleNamespace(base_url='synthetic://native-reader', user='synthetic', password='',
        execute=execute,
        entry_spread_risk_profile=options['entry_spread_risk'], automatic_ladder_profile=False,
        confirmed_original_risk_policy=options['confirmed_original_risk_policy'],
        native_channel_management_policy=options['native_channel_management_policy'])


def test_report_reopens_reader_when_original_risk_matches_but_native_policy_missing(monkeypatch):
    from scripts.clickhouse import report_strategy_one_trades as report
    from src.backend import backtest_v4_saved_review as review
    from src.trading_runtime import arte_journal_writer as writer
    options, context, sealed = setup_profile(monkeypatch)
    initial = transport(options)
    initial.native_channel_management_policy = None
    selected = transport(options)
    calls = []
    selected.close = lambda: calls.append('closed')
    def open_reader(**actual):
        assert actual == options
        calls.append('opened')
        return selected
    monkeypatch.setattr(writer, 'backtest_v4_operator_client_from_env', open_reader)
    monkeypatch.setattr(writer, '_v4_preflight', lambda client: calls.append('preflight'))
    def terminal(client, run_id, **kwargs):
        review._require_declared_read_profile(client, context, sealed_configuration=sealed)
        assert type(client) is report.SelectOnly
        assert client.native_channel_management_policy is options['native_channel_management_policy']
        with pytest.raises(ValueError):
            client.execute('INSERT INTO arte.example VALUES (1)')
        calls.append('selected-detail')
        raise RuntimeError('controlled producer boundary')
    monkeypatch.setattr(report, 'load_v4_terminal_review_page', terminal)
    with pytest.raises(RuntimeError, match='controlled producer boundary'):
        report.build_report(report.SelectOnly(initial), object(), str(UUID(int=125)))
    assert calls == ['opened', 'preflight', 'selected-detail', 'closed']
    assert review._DECLARED_READ_SCOPE.get() is None


def test_report_exact_native_policy_reused_and_foreign_policy_rejected(monkeypatch):
    from scripts.clickhouse.report_strategy_one_trades import SelectOnly
    from src.backend import backtest_v4_saved_review as review
    options, context, sealed = setup_profile(monkeypatch)
    client = transport(options)
    wrapped = SelectOnly(client)
    assert review._require_declared_read_profile(wrapped, context, sealed_configuration=sealed) == {}
    rewrapped = wrapped.declared_read_wrapper(client)
    assert rewrapped.native_channel_management_policy is client.native_channel_management_policy
    client.native_channel_management_policy = replace(client.native_channel_management_policy,
        max_rows_per_family=client.native_channel_management_policy.max_rows_per_family + 1)
    with pytest.raises(ValueError, match='foreign typed policy'):
        review._require_declared_read_profile(SelectOnly(client), context, sealed_configuration=sealed)
