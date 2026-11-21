from .compare import compare


def test_aggregate_improvement_cannot_hide_regression():
    reference={'all_visible/raw_mae/1s/trade_close':1.,'masked/raw_mae/100ms/bid_size':100.,
               'all_visible/candle/1s/trade/moving_direction_pct':88.}
    candidate={**reference,'masked/raw_mae/100ms/bid_size':1.,'all_visible/raw_mae/1s/trade_close':1.01}
    result=compare(candidate,reference)
    assert not result['strict_no_regression'] and result['error_regressions']==1
    candidate['all_visible/raw_mae/1s/trade_close']=.99
    assert compare(candidate,reference)['strict_no_regression']
