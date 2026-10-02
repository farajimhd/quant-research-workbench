"""Producer and original-held binding checks; no connected actor acceptance."""
from dataclasses import replace

import pytest

from src.trading_runtime.strategy_half_risk_liquidity_fade import half_risk_liquidity_fade_failure
from src.trading_runtime.strategy_liquidity_fade_source import validate_liquidity_fade_state
from src.trading_runtime.strategy_liquidity_fade_market_source import (
    load_liquidity_fade_market_observations, validate_liquidity_fade_market_observations,
)
from test_strategy_liquidity_fade_failure import observed_case
from test_strategy_liquidity_fade_market_source import native_case, Reader
from test_arte_liquidity_fade_failure_v4 import prepared_case


def source_case():
    value=observed_case()
    counts=(57,18,20,10)
    value=replace(value,five_second=replace(value.five_second,completed_five_second_close_int=23100),
        candles=tuple(replace(c,trade_count=n) for c,n in zip(value.candles,counts)))
    witness=half_risk_liquidity_fade_failure(value)
    _,source,bars,indicator,quote,args=native_case()
    bars=tuple(dict(b,trade_count=c.trade_count,close_int=witness.completed_close_int)
        for b,c in zip(bars,witness.candles))
    return witness,source,bars,indicator,quote,dict(args,strategy_number=39)


def held_case():
    witness,*_=source_case()
    _,financial,state,_=prepared_case();key,proposal=state.submitted[0]
    return witness,financial,replace(state,submitted=((key,replace(proposal,strategy_number=39)),))


def test_original_entry_and_native_first_held_bind_additional_policy():
    witness,financial,state=held_case()
    assert validate_liquidity_fade_state(witness,state,financial).strategy_number==39
    key,proposal=state.submitted[0]
    for older in (35,36,37,38):
        with pytest.raises(ValueError):validate_liquidity_fade_state(witness,
            replace(state,submitted=((key,replace(proposal,strategy_number=older)),)),financial)
    for changes in (dict(initial_stop=2.29),dict(reference_ask=2.34)):
        with pytest.raises(ValueError):validate_liquidity_fade_state(witness,
            replace(state,submitted=((key,replace(proposal,**changes)),)),financial)
    with pytest.raises(ValueError,match='first-held authority'):
        validate_liquidity_fade_state(witness,
            replace(state,first_held_boundaries=((key,witness.first_held_boundary_ms+100),)),financial)


def test_four_native_bars_indicator_and_current_quote_bind_exactly():
    witness,source,bars,indicator,quote,args=source_case()
    assert validate_liquidity_fade_market_observations(witness,source,bars,indicator,quote,**args)==witness
    reader=Reader((bars,(indicator,),(quote,)))
    assert load_liquidity_fade_market_observations(reader,witness,source,**args)==witness
    assert len(reader.queries)==3 and all('file(' not in q for q in reader.queries)


@pytest.mark.parametrize('family,changes',[
    ('bar',{'trade_count':19}),('bar',{'attempt_id':'foreign'}),
    ('bar',{'close_int':23101}),('indicator',{'macd_line':0.01}),
    ('indicator',{'bucket_index':0}),('quote',{'quote_timestamp_us':1786393607400000}),
    ('quote',{'quote_valid':0}),('quote',{'bid_int':23101}),
    ('quote',{'attempt_id':'foreign'}),
])
def test_altered_native_evidence_never_attests_additional_witness(family,changes):
    witness,source,bars,indicator,quote,args=source_case()
    if family=='bar':bars=(*bars[:-1],dict(bars[-1],**changes))
    elif family=='indicator':indicator=dict(indicator,**changes)
    else:quote=dict(quote,**changes)
    with pytest.raises(ValueError):validate_liquidity_fade_market_observations(
        witness,source,bars,indicator,quote,**args)


def test_source_validation_cannot_treat_new_witness_as_parent_policy():
    witness,source,bars,indicator,quote,args=source_case()
    for number in (35,36,37,38):
        reader=Reader((bars,(indicator,),(quote,)))
        with pytest.raises(ValueError):load_liquidity_fade_market_observations(
            reader,witness,source,**dict(args,strategy_number=number))
        assert not reader.queries


def test_prepared_checkpoint_request_retains_exact_witness_and_source_identity():
    from src.backend.backtest_strategy_one_management import LiquidityFadeCheckpointRequest
    from test_arte_liquidity_fade_failure_v4 import IDENTITY
    witness,financial,_=held_case()
    _,source,*_=source_case()
    request=LiquidityFadeCheckpointRequest(witness,financial,IDENTITY,source)
    assert request.witness is witness and request.financial is financial
    assert request.observation_source==source
    with pytest.raises(TypeError):request.observation_source['source_build_id']='f'*64
    with pytest.raises(ValueError):LiquidityFadeCheckpointRequest(witness,
        replace(financial,pending_exit=True),IDENTITY,source)
