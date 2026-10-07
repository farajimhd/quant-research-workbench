"""Real report decorator selection; saved authority producers are controlled."""
from types import SimpleNamespace
from uuid import UUID

import pytest


@pytest.mark.parametrize('foreign', [False, True])
def test_actual_build_report_reopens_declared_reader_before_details(monkeypatch, foreign):
    from scripts.clickhouse import report_strategy_one_trades as command
    from src.backend import backtest_v4_saved_review as review
    from src.trading_runtime import arte_journal_writer as writer
    from src.trading_runtime import strategy_sixty_eight_release as release
    from test_strategy_sixty_eight_release import source_fixture, APPROVAL

    prepared = release.derive_strategy_sixty_eight_configuration(source_fixture(), **APPROVAL)
    payload = prepared['payload']
    context = dict(strategy_id=payload['strategy']['strategy_id'], strategy_revision=68,
                   configuration_hash=('f'*64 if foreign else prepared['payload_hash']))
    sealed = SimpleNamespace(payload=payload, payload_hash=prepared['payload_hash'], strategy_number=68)
    selected = SimpleNamespace(base_url='synthetic://reader', user='synthetic', password='',
                              confirmed_original_risk_policy=release.CONFIRMED_ORIGINAL_RISK_POLICY,
                              closed=False, automatic_ladder_profile=False,
                              entry_spread_risk_profile=False)
    selected.close = lambda: setattr(selected, 'closed', True)
    calls = []
    def open_reader(**options):
        assert options == {'confirmed_original_risk_policy': release.CONFIRMED_ORIGINAL_RISK_POLICY}
        calls.append('open')
        return selected
    monkeypatch.setattr(writer, 'backtest_v4_operator_client_from_env', open_reader)
    monkeypatch.setattr(writer, '_v4_preflight', lambda client: calls.append(('preflight', client)))
    def terminal(client, run_id, **kwargs):
        review._require_declared_read_profile(client, context, sealed_configuration=sealed)
        assert isinstance(client, command.SelectOnly)
        assert client.confirmed_original_risk_policy == selected.confirmed_original_risk_policy
        assert not selected.closed
        calls.append('selected-detail')
        raise RuntimeError('controlled terminal producer boundary')
    monkeypatch.setattr(command, 'load_v4_terminal_review_page', terminal)
    initial = command.SelectOnly(SimpleNamespace(base_url='synthetic://bootstrap', user='synthetic', password=''))
    with pytest.raises((ValueError, RuntimeError), match=('sealed run configuration' if foreign else 'controlled terminal')):
        command.build_report(initial, object(), str(UUID(int=68)))
    assert calls == ([] if foreign else ['open', ('preflight', selected), 'selected-detail'])
    assert selected.closed is (not foreign)
    assert review._DECLARED_READ_SCOPE.get() is None
