"""Independent price-action oracle and sequencing/provenance contracts."""
from functools import lru_cache

import numpy as np
import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from research.rl_trading.v6 import price_action_labels as pa
from research.rl_trading.v6 import label_audit as audit
from src.backend.research_model_service import router


def bars(prices, seconds=None, signs=None):
    n = len(prices)
    return pl.DataFrame(dict(time_us=[int(s*1e6) for s in (seconds or range(1,n+1))],
        open=prices, close=prices, high=[p+.1 for p in prices], low=[p-.1 for p in prices],
        macd_line=signs or [1.]*n, macd_signal=[0.]*n))


def oracle(prices, times, half_life):
    """Enumerate state transitions, independently of the producer's exit scan."""
    @lru_cache(None)
    def value(i, entry):
        if i == len(prices)-1:
            return 0. if entry is None else prices[i]-prices[entry]
        d = 2**(-(times[i+1]-times[i])/half_life)
        if entry is None:
            return max(d*value(i+1,None), d*value(i+1,i))
        return max(prices[i]-prices[entry]+d*value(i+1,None), d*value(i+1,entry))
    return value


@pytest.mark.parametrize('prices,times', [
    ([10.,11.,9.,12.,11.], [1,2,3,4,5]),
    ([10.,11.,9.,12.,11.], [1,2,30,90,100]),
    ([10.,10.,10.,10.], [1,2,3,4]),
    ([14.,13.,12.,11.], [1,2,3,4]),
])
def test_values_match_exhaustive_state_oracle_and_realized_sequence(prices,times):
    labels, episodes, pairs, trades = pa.calculate(bars(prices,times),pa.Config(half_life_seconds=10.))
    expected = oracle(prices,times,10.)
    for i,r in enumerate(labels.iter_rows(named=True)):
        d = 2**(-(times[i+1]-times[i])/10.) if i < len(prices)-1 else 0.
        assert r['wait_value'] == pytest.approx(d*expected(i+1,None) if d else 0.)
        if r['entry_value'] is not None:
            assert r['entry_value'] == pytest.approx(d*expected(i+1,i))
        if r['entry_basis'] is not None:
            entry = prices.index(r['entry_basis'])
            assert r['exit_value'] == pytest.approx(prices[i]-prices[entry]+(d*expected(i+1,None) if d else 0.))
            if r['hold_value'] is not None:
                assert r['hold_value'] == pytest.approx(d*expected(i+1,entry))
    # No double counting of the current trade or future continuation.
    discounted_pnl = sum(2**(-(r['exit_us']/1e6-times[0])/10.)*r['price_pnl'] for r in trades.iter_rows(named=True))
    assert labels['label_value'][0] == pytest.approx(discounted_pnl)
    actions = labels['action'].to_list()
    held = False
    for action in actions:
        assert action in (('HOLD','EXIT') if held else ('WAIT','ENTRY'))
        if action == 'ENTRY': held = True
        if action == 'EXIT': held = False
    assert not held


def test_pair_extrema_include_open_close_and_first_long_has_no_previous_short():
    frame = bars([10.,9.,11.,12.],signs=[0.,-1.,1.,1.]).with_columns(pl.Series('open',[9.9,8.9,10.9,11.9]))
    labels, episodes, pairs, _ = pa.calculate(frame)
    assert labels['direction'].to_list() == [1,-1,1,1]  # Equality belongs to long.
    first, second = pairs.to_dicts()
    assert first['short_episode'] is None
    assert first['best_start_price'] == 9.9
    assert second['short_episode'] == 2 and second['long_episode'] == 3
    assert second['best_start_price'] == 8.9
    assert second['best_end_price'] == 12.
    assert second['best_stop'] == pytest.approx(8.89)
    assert second['best_target'] == 12.1


def test_ties_wait_and_last_candle_cannot_enter():
    labels, _, _, trades = pa.calculate(bars([10.,10.,10.]))
    assert labels['action'].to_list() == ['WAIT']*3
    assert labels['entry_value'][-1] is None
    assert trades.is_empty()


def test_invalid_order_geometry_and_parameters_fail_closed():
    with pytest.raises(ValueError,match='time ordered'):
        pa.calculate(bars([10.,11.],[2,1]))
    with pytest.raises(ValueError,match='OHLC'):
        pa.calculate(bars([10.,11.]).with_columns(pl.lit(1.).alias('high')))
    with pytest.raises(ValueError,match='half-life'):
        pa.calculate(bars([10.,11.]),pa.Config(half_life_seconds=0))
    with pytest.raises(ValueError,match='1s MACD'):
        pa.calculate(bars([10.,11.]),pa.Config(timeframe_seconds=5))


def test_macd_regions_use_sign_equality_and_not_hindsight_hints():
    scalar = np.zeros((4,len(audit.SCALAR_NAMES)),dtype=np.float32)
    for name in ['bar_price_valid','bar_extremes_valid','indicator_available']:
        scalar[:,audit.SCALAR_NAMES.index(name)] = 1
    scalar[:,audit.SCALAR_NAMES.index('macd_line_rel')] = [-1,0,1,-1]
    regions = audit.macd_regions(np.arange(1,5)*1_000_000,scalar,1_000_000,5_000_000)
    assert [(r['start'],r['end'],r['color']) for r in regions] == [
        (0,1,'var(--danger)'),(1,3,'var(--success)'),(3,4,'var(--danger)')]


def test_price_action_routes_are_read_only_and_bound_windows(monkeypatch):
    monkeypatch.setattr(pa,'metadata',lambda:dict(ticker='NVDA',status='experimental_not_training_labels'))
    app = FastAPI(); app.include_router(router); client = TestClient(app)
    assert client.get('/api/research/models/v6/price-action').json()['ticker'] == 'NVDA'
    assert client.post('/api/research/models/v6/price-action').status_code == 405
    assert client.get('/api/research/models/v6/price-action/chart?seconds=3601').status_code == 422
