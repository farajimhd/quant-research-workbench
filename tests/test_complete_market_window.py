"""Window release integrity; source certificates are not proven by fixtures."""
from http.client import IncompleteRead

import pytest

from src.backend import backtest_complete_market_window as window
from src.backend.backtest_complete_market_response import CompleteMarketResponseBounds
from src.backend.backtest_market_data import CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit
from src.backend.backtest_liquidity_price import PriceLevelPlan, PriceLevelUnit


DAY = '2026-08-04'
ATTEMPT = '00000000-0000-0000-0000-000000000001'
BOUNDS = CompleteMarketResponseBounds(1024 * 1024, 1000, 16 * 1024 * 1024)


def plans():
    units = tuple(MarketDayUnit('a' * 64, DAY, 'AAA', stage, ATTEMPT,
                               'b' * 64, 1, 'c' * 64)
                  for stage in ('bars', 'technical', 'broker_100ms'))
    market = CertifiedMarketDayPlan(ExecutionInterval.fixed(100), 'a' * 64,
                                   'd' * 64, (DAY,), ('AAA',), units,
                                   (1000,), 'e' * 64)
    prices = PriceLevelPlan(market.build_id, (PriceLevelUnit(DAY, 'AAA', ATTEMPT,
                            ATTEMPT, 1, 1, 1., 'f' * 64),), 'f' * 64)
    return market, prices


def row(clock, resolution=100, **changes):
    return dict(session_date=DAY, ticker='AAA', boundary_ms=clock,
                resolution_ms=resolution, **changes)


def read(*, market=None, prices=None, after=0, through=1000, budget=1000):
    original, original_prices = plans()
    return window.read_complete_market_window(market or original,
        prices=prices or original_prices, after_boundary_ms=after,
        through_boundary_ms=through, client=object(), response_bounds=BOUNDS,
        max_window_rows=budget)


def install(monkeypatch, packets):
    packets = iter(packets)
    # Use original query compilation; replace only transport in unit tests.
    monkeypatch.setattr(window, 'read_complete_market_response', lambda *a, **k: next(packets))


def test_original_native_sql_merge_preserves_prices_and_clocks(monkeypatch):
    market, prices = plans()
    from src.backend.backtest_market_data import market_day_source_sqls
    sqls = market_day_source_sqls(market, price_plan=prices,
        after_boundary_ms=0, through_boundary_ms=1000)
    packets = [(row(100, execution_price_levels=[[12300, 2.5]]), row(1000))]
    packets += [(row(1000, 1000),)] if len(sqls) == 2 else []
    install(monkeypatch, packets)
    result = read()
    assert [r['boundary_ms'] for r in result] == sorted(r['boundary_ms'] for r in result)
    assert result[0]['execution_price_levels'] == ({'price_int': 12300, 'volume': 2.5},)


def test_uint64_clock_wire_spelling_is_preserved_with_numeric_order(monkeypatch):
    monkeypatch.setattr(window, 'market_day_source_sqls', lambda *a, **k: ('one',))
    install(monkeypatch, [(row('100'), row('1000'))])
    assert [r['boundary_ms'] for r in read()] == ['100', '1000']


@pytest.mark.parametrize('bad', [
        row(1100), row(0), {**row(100), 'ticker': 'BBB'},
    {**row(100), 'session_date': '2026-08-05'}, row(100, 1000),
    row(100, execution_price_levels=[[True, 1]]),
    row(100, execution_price_levels=[[12300, float('inf')]]),
        row(True), row('0100'), row('1e2'),
])
def test_foreign_future_or_invalid_rows_do_not_escape(monkeypatch, bad):
    install(monkeypatch, [(row(100), bad)])
    with pytest.raises(ValueError):
        read()


def test_partial_second_response_cannot_release_first_response(monkeypatch):
    monkeypatch.setattr(window, 'market_day_source_sqls', lambda *a, **k: ('one', 'two'))
    calls = []
    def transport(*a, **k):
        calls.append(a[1])
        if len(calls) == 2:
            raise IncompleteRead(b'partial')
        return (row(100),)
    monkeypatch.setattr(window, 'read_complete_market_response', transport)
    with pytest.raises(IncompleteRead):
        read()
    assert calls == ['one', 'two']


def test_cross_response_duplicate_and_aggregate_budget_rejected(monkeypatch):
    monkeypatch.setattr(window, 'market_day_source_sqls', lambda *a, **k: ('one', 'two'))
    install(monkeypatch, [(row(100),), (row(100),)])
    with pytest.raises(ValueError, match='duplicated'):
        read()
    install(monkeypatch, [(row(100),), (row(200),)])
    with pytest.raises(ValueError, match='aggregate'):
        read(budget=1)


def test_window_edges_release_in_order_and_do_not_retry_failed_next_window(monkeypatch):
    market, prices = plans()
    calls = []
    def prepare(*a, **k):
        calls.append((k['after_boundary_ms'], k['through_boundary_ms']))
        if len(calls) == 2:
            raise IncompleteRead(b'partial')
        return (row(100), row(200))
    monkeypatch.setattr(window, 'read_complete_market_window', prepare)
    source = window.iter_complete_market_windows(market, prices=prices,
        after_boundary_ms=0, through_boundary_ms=500, window_span_ms=200,
        client=object(), response_bounds=BOUNDS, max_window_rows=1000)
    assert next(source)[0] == 100
    assert next(source)[0] == 200
    with pytest.raises(IncompleteRead):
        next(source)
    assert calls == [(0, 200), (200, 400)]


def test_resume_window_is_exact_exclusive_and_no_empty_clock_is_fabricated(monkeypatch):
    market, prices = plans()
    calls = []
    def prepare(*a, **k):
        calls.append((k['after_boundary_ms'], k['through_boundary_ms']))
        return ()
    monkeypatch.setattr(window, 'read_complete_market_window', prepare)
    assert tuple(window.iter_complete_market_windows(market, prices=prices,
        after_boundary_ms=200, through_boundary_ms=500, window_span_ms=200,
        client=object(), response_bounds=BOUNDS, max_window_rows=1000)) == ()
    assert calls == [(200, 400), (400, 500)]
