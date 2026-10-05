"""A declared exclusion projects certificates; it never truncates a universe."""
from dataclasses import replace

import pytest

from src.backend import backtest_market_data as market_data
from src.trading_runtime.squeeze_ladder_automatic import AutomaticLadderPolicy
from test_backtest_market_data import BacktestMarketDataTests


def configuration(exclusions):
    return {'strategy': {'numbered_release': {
        'automatic_entry_policy': AutomaticLadderPolicy().payload(),
        'automatic_market_policy': {'population_exclusions': exclusions},
    }}}


def test_full_declared_population_is_complete_and_keeps_source_identity(monkeypatch):
    original = BacktestMarketDataTests()._plan()
    symbols = tuple(sorted(('LGHL', 'SUGP') + tuple(f'T{i:05}' for i in range(6085))))
    units = tuple(replace(unit, ticker=ticker) for ticker in symbols for unit in original.units)
    full = replace(original, tickers=symbols, units=units)
    calls = []
    def certificate(**kwargs):
        calls.append(kwargs)
        return full
    monkeypatch.setattr(market_data, '_certified_market_plan_from_arte', certificate)
    kwargs = dict(sessions=full.sessions, tickers=(), configuration=configuration(['LGHL']))
    observed = market_data.certified_market_plan_from_arte(**kwargs)
    assert len(observed.tickers) == 6086
    assert set(observed.tickers) == set(full.tickers) - {'LGHL'}
    assert observed.units == tuple(unit for unit in full.units if unit.ticker != 'LGHL')
    assert observed.build_id == full.build_id and observed.definition_hash == full.definition_hash
    assert observed.token != full.token
    assert calls == [kwargs]
    assert market_data.certified_market_plan_from_arte(**kwargs) == observed


def test_old_configurations_keep_exact_existing_certificate(monkeypatch):
    full = BacktestMarketDataTests()._plan()
    monkeypatch.setattr(market_data, '_certified_market_plan_from_arte', lambda **kwargs: full)
    assert market_data.certified_market_plan_from_arte(
        sessions=full.sessions, tickers=(), configuration={}) is full


def test_requested_excluded_ticker_is_rejected_before_read(monkeypatch):
    monkeypatch.setattr(market_data, '_certified_market_plan_from_arte',
                        lambda **kwargs: pytest.fail('excluded request reached certificate reader'))
    with pytest.raises(ValueError, match='declared excluded'):
        market_data.certified_market_plan_from_arte(
            sessions=('2026-08-26',), tickers=('LGHL',), configuration=configuration(['LGHL']))


def test_exclusion_without_declared_automatic_policy_is_rejected(monkeypatch):
    value = configuration(['LGHL'])
    value['strategy']['numbered_release'].pop('automatic_entry_policy')
    with pytest.raises(ValueError, match='declared automatic'):
        market_data.certified_market_plan_from_arte(
            sessions=('2026-08-26',), tickers=(), configuration=value)
