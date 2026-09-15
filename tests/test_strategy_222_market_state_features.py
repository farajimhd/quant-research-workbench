import sys
from pathlib import Path

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from strategy_222_market_state_features import MarketStates, candle_features


def test_engulfing_and_wicks_use_only_completed_pair():
    previous=dict(open=10.,high=10.1,low=9.4,close=9.5)
    current=dict(open=9.4,high=10.3,low=9.3,close=10.2)
    result=candle_features(current,previous)
    assert result['bullish_body_engulfing']==1
    assert result['outside_bar']==1
    assert result['higher_high']==1
    assert result['lower_low']==1
    assert result['body_fraction']==pytest.approx(.8)


def test_flat_candle_does_not_invent_shape_ratios():
    result=candle_features(dict(open=10.,high=10.,low=10.,close=10.))
    assert result==dict(green=0.,red=0.)


def test_delivered_five_second_watermark_does_not_use_newer_frame():
    state=MarketStates.__new__(MarketStates)
    def snap(at,positive):return dict(as_of=at,positive=positive,histogram_bps=at,geometry={'green':1,'atr_pct':.1})
    state.streams={('X','1s'):([snap(6,True)],[6]),('X','5s'):([snap(5,False),snap(10,True)],[5,10])}
    decision=dict(source_signal_ids=['qmd-derived:X:1s:1'],metadata=dict(macd=dict(observed_at=6,completed_base_at=5)))
    result,evidence=state.at('X',decision,6)
    assert result['completed_5s.histogram_bps']==5
    assert result['completed_5s.histogram_bps_per_atr_bps']==.5
    assert result['macd_completed_timeframes_agree']==0
    assert evidence['5s']['delivered_cutoff']==5
    decision['metadata']['macd']['completed_base_at']=10
    with pytest.raises(ValueError,match='future'):state.at('X',decision,6)


def test_missing_one_second_delivery_is_not_imputed_from_clock():
    state=MarketStates.__new__(MarketStates)
    state.streams={('X',tf):([],[]) for tf in ('1s','5s')}
    result,evidence=state.at('X',dict(metadata={},source_signal_ids=[]),6)
    assert not result
    assert evidence['1s']['status']=='no_delivered_frame_watermark'
