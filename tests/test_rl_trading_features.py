from datetime import date

import numpy as np
import polars as pl
import pytest

from research.rl_trading.v1.features import FEATURE_NAMES, SECONDS, encode
from research.rl_trading.v1.reference_features import fundamentals


class V7:
    def __init__(self, seed, **kwargs):
        self.engine = type('Engine',(),{'_projection_revision':0})()
        self.levels = []
    def update_second(self, row, *, at):
        self.engine._projection_revision += 1
        self.levels = [{'side':1,'price':9.},{'side':-1,'price':12.}]
    def context(self, *, as_of, price):
        return {'v7_max_input_timestamp':as_of.timestamp(),
                'qmd_structure_unified_levels':self.levels}


def test_features_use_only_completed_bars_indicators_and_v7(monkeypatch):
    monkeypatch.setattr('research.rl_trading.v1.features.FixedV7Stream',V7)
    day = date(2026,8,21)
    bars = pl.DataFrame(dict(resolution_ms=[1000,1000],bucket_index=[14_400,14_402],open_int=[100000,110000],
        high_int=[110000,120000],low_int=[90000,100000],close_int=[100000,110000],
        price_valid=[1,1],extremes_valid=[1,1],volume=[100.,200.],trade_count=[2,3]))
    indicators = pl.DataFrame(dict(bucket_index=[14_400,14_402],macd_line=[1.,2.],
        macd_signal=[0.5,1.],rsi_14=[50.,60.],atr_14=[1.,2.],
        ema_7=[10.,11.],ema_26=[9.,10.]))
    reference = fundamentals(day,[],[])
    features,volume60 = encode(day,bars,indicators,{'ticker':'AAPL'},[],reference)
    columns = {name:features[:,i] for i,name in enumerate(FEATURE_NAMES)}
    assert features.shape == (SECONDS,len(FEATURE_NAMES))
    assert columns['price_present'][0] == 0
    assert columns['price_present'][1] == 1
    assert columns['price_age'][2] > 0
    assert columns['log_price'][2] == pytest.approx(np.log(10.))
    assert columns['log_price'][3] == pytest.approx(np.log(11.))
    assert columns['v7_level_count'][0] == 0
    assert columns['v7_level_count'][1] == pytest.approx(np.log1p(2))
    assert volume60[:4].tolist() == pytest.approx([0.,100.,100.,300.])


def test_future_v7_state_is_rejected(monkeypatch):
    day = date(2026,8,21)
    class Future(V7):
        def context(self, *, as_of, price):
            result = super().context(as_of=as_of,price=price)
            result['v7_max_input_timestamp'] += 1
            return result
    monkeypatch.setattr('research.rl_trading.v1.features.FixedV7Stream',Future)
    empty = pl.DataFrame(schema=dict(resolution_ms=pl.Int64,bucket_index=pl.Int64,open_int=pl.Int64,
        high_int=pl.Int64,low_int=pl.Int64,close_int=pl.Int64,price_valid=pl.Int64,
        extremes_valid=pl.Int64,volume=pl.Float64,trade_count=pl.Int64))
    indicators = pl.DataFrame(schema=dict(bucket_index=pl.Int64,macd_line=pl.Float64,
        macd_signal=pl.Float64,rsi_14=pl.Float64,atr_14=pl.Float64,
        ema_7=pl.Float64,ema_26=pl.Float64))
    with pytest.raises(ValueError,match='future bar'):
        encode(day,empty,indicators,{'ticker':'AAPL'},[],fundamentals(day,[],[]))
