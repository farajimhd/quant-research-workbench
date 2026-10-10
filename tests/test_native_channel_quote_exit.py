from dataclasses import replace
import polars as pl
import pytest

from src.trading_runtime.native_channel_quote_exit import (
    NativeChannelQuoteExitPolicy, qualify_native_channel_quote_exits,
)


def rows(**changes):
    values=dict(qualified=[True],held=[True],pending_exit=[False],decision_day_ms=[120000],
        quote_day_us=[120000000],bid_int=[9900],ask_int=[10000],reference_close_int=[10000],
        original_ask=[1.0],initial_stop=[0.8])
    values.update(changes)
    return pl.DataFrame(values,schema_overrides=dict(qualified=pl.Boolean,held=pl.Boolean,
        pending_exit=pl.Boolean,decision_day_ms=pl.Int64,quote_day_us=pl.Int64,
        bid_int=pl.UInt64,ask_int=pl.UInt64,reference_close_int=pl.UInt64,
        original_ask=pl.Float64,initial_stop=pl.Float64))


POLICY=NativeChannelQuoteExitPolicy(1000000,'below_reference_close',None,100)


@pytest.mark.parametrize('changes',[
    {'quote_day_us':[120000001]}, {'quote_day_us':[118999999]},
    {'bid_int':[10000]}, {'ask_int':[9800]}, {'pending_exit':[True]},
    {'held':[False]}, {'qualified':[False]}, {'reference_close_int':[None]},
])
def test_missing_future_stale_recovered_or_unowned_quote_cannot_trigger(changes):
    assert not qualify_native_channel_quote_exits(rows(**changes),policy=POLICY)[0]


def test_valid_quote_mask_is_immutable_and_reference_comparison_is_exact():
    result=qualify_native_channel_quote_exits(rows(),policy=POLICY)
    assert result.tolist()==[True] and not result.flags.writeable
    assert qualify_native_channel_quote_exits(rows(bid_int=[2**53],ask_int=[2**53+1],
        reference_close_int=[2**53+1]),policy=POLICY)[0]


def test_original_risk_fraction_is_declared_and_changes_threshold():
    policy=NativeChannelQuoteExitPolicy(1000000,'below_original_risk_threshold',0.25,100)
    data=rows(bid_int=[9400],ask_int=[9500])
    assert qualify_native_channel_quote_exits(data,policy=policy)[0]
    assert not qualify_native_channel_quote_exits(data,policy=replace(policy,original_risk_fraction=0.5))[0]
    with pytest.raises(ValueError):replace(policy,original_risk_fraction=True)
    with pytest.raises(ValueError):replace(POLICY,original_risk_fraction=0.25)
